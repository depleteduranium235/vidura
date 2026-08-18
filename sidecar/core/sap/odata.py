"""
OData V2 client for the GTS read path.

Talks to LLS_BPADDR_MNG_SRV on AGP. Verified against the live endpoint:
  - Basic auth (www-authenticate: Basic realm="SAP NetWeaver AS [AGP/300]")
  - TLS validates against the system trust store; no CA bundle needed
    (the corporate bundle is additive and fails on its own)
  - OData V2, so responses are wrapped in {"d": {"results": [...]}}

Read-only by design. Writes go to the Z result table via our own V4 service,
and the release itself is always executed by a human in their own session
(§5.4) — this client must never be able to perform one.
"""

from __future__ import annotations

import logging
import ssl
import time
from datetime import datetime, timezone
from typing import Any, Iterator, Optional, Union
from urllib.parse import quote

import httpx

from .schemas import (
    BP_MASTER_SELECT,
    AssociatedBank,
    AssociatedBlockedObject,
    BpIdentification,
    BusinessPartnerMaster,
    ScreenedPartnerAddress,
    SplHitDetail,
)

log = logging.getLogger(__name__)

SERVICE_PATH = "/sap/opu/odata/sap/LLS_BPADDR_MNG_SRV"
ENTITY_SET = "C_SPLScrngScreenedPrtnAddress"
BP_ENTITY_SET = "I_BusinessPartner"

# Navigation properties on the root entity, mapped to the child model and the
# attribute they populate.
EXPANDS: dict[str, tuple[type, str]] = {
    "to_SPLHitsDetailSet": (SplHitDetail, "hits"),
    "to_SPLScrngBPIdentification": (BpIdentification, "identifications"),
    "to_SPLScrngBPAssocdBlkdObj": (AssociatedBlockedObject, "associated_blocked_objects"),
    "to_SPLScrngBPAssociatedBank": (AssociatedBank, "associated_banks"),
}

DEFAULT_PAGE_SIZE = 100
RETRY_STATUS = {429, 500, 502, 503, 504}

# Legal regulations that are Sanctioned Party List screening.
# Validated from I_GTS_LegalRegulationText on AGP 300 — every regulation whose
# description contains "Sanctioned Party List Screening". ZHORG/ZHIND are the
# demo variants for organisations/individuals used in AGP testing.
# Everything else in the catalogue is customs, transit, export control, embargo,
# excise or preference — a block there has no sanctioned party to compare
# against, so there is no identity question to adjudicate.
SPL_LEGAL_REGULATIONS: tuple[str, ...] = (
    "SPLUS",   # Sanctioned Party List Screening
    "SPLSY",   # Sanctioned Party List Screening - Sayari
    "ZHORG",   # SPL Screening (Demo HANA Organizations)
    "ZHIND",   # SPL Screening (Demo HANA Individuals)
    "ZPLCN",   # Foreign Language SPL Screening (Chinese)
    "ZSPLH",   # SPL Screening - High Risk
    "ZSPLL",   # SPL Screening - Low Risk
)


class ODataError(RuntimeError):
    """Non-retryable failure from the SAP Gateway."""


def default_verify() -> Union[bool, ssl.SSLContext]:
    """
    Verify TLS against the operating system's trust store.

    SAP systems behind corporate PKI present chains that Windows and macOS
    trust but certifi does not ship, so Python fails where curl succeeds —
    curl on Windows uses Schannel and reads the OS store. Verified against
    AGP: certifi gives CERTIFICATE_VERIFY_FAILED, the OS store gives 401.

    Falls back to httpx's certifi default if truststore isn't installed, so
    this stays a soft dependency rather than an import-time failure.
    """
    try:
        import truststore
    except ImportError:  # pragma: no cover - depends on the environment
        log.debug("truststore not installed; falling back to certifi")
        return True
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def _odata_datetime(dt: datetime) -> str:
    """
    Format for an OData V2 datetime literal.

    V2 wants datetime'yyyy-MM-ddTHH:mm:ss' with no offset and no trailing Z,
    so anything tz-aware is normalised to UTC first.
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return f"datetime'{dt.strftime('%Y-%m-%dT%H:%M:%S')}'"


def _unwrap(node: Any) -> list[dict]:
    """
    Pull rows out of a V2 payload.

    V2 nests inconsistently: a collection nav property is
    {"results": [...]}, a to-one is the object itself, and an unexpanded
    one is {"__deferred": {...}} which yields nothing.
    """
    if node is None:
        return []
    if isinstance(node, list):
        return [r for r in node if isinstance(r, dict)]
    if isinstance(node, dict):
        if "__deferred" in node:
            return []
        if "results" in node:
            return _unwrap(node["results"])
        return [node]
    return []


class GtsODataClient:
    """
    Synchronous OData V2 reader for GTS SPL screening results.

    Usage:
        with GtsODataClient(base_url, user, password) as client:
            for rec in client.iter_blocked_addresses(since=watermark):
                ...
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        sap_client: str = "300",
        *,
        timeout: float = 60.0,
        verify: Union[bool, str, ssl.SSLContext, None] = None,
        max_retries: int = 3,
        http_client: Optional[httpx.Client] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.sap_client = sap_client
        self.max_retries = max_retries
        # One BP commonly has several blocked addresses, so cache master data
        # for the lifetime of the client rather than refetching per address.
        self._bp_cache: dict[str, Optional[BusinessPartnerMaster]] = {}
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            auth=(username, password),
            timeout=timeout,
            verify=default_verify() if verify is None else verify,
            headers={"Accept": "application/json"},
        )

    def __enter__(self) -> "GtsODataClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # ---------------------------------------------------------------- requests

    def _get(self, path: str, params: dict[str, str]) -> dict:
        url = f"{self.base_url}{path}"
        params = {"sap-client": self.sap_client, "$format": "json", **params}

        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                resp = self._client.get(url, params=params)
            except httpx.HTTPError as exc:
                # Transient transport failure — a dead sidecar must not affect
                # GTS (§5.6 #1), so we back off and let the watermark cover us.
                last_exc = exc
                self._sleep(attempt)
                continue

            if resp.status_code == 200:
                return resp.json()

            if resp.status_code in RETRY_STATUS:
                last_exc = ODataError(f"{resp.status_code} from {url}")
                self._sleep(attempt)
                continue

            if resp.status_code in (401, 403):
                raise ODataError(
                    f"Authentication/authorization failed ({resp.status_code}). "
                    "Check the technical user's credentials and its S_SERVICE "
                    "authorization for LLS_BPADDR_MNG_SRV."
                )

            raise ODataError(f"{resp.status_code} from {url}: {resp.text[:500]}")

        raise ODataError(f"Giving up on {url} after {self.max_retries} attempts") from last_exc

    @staticmethod
    def _sleep(attempt: int) -> None:
        time.sleep(min(2**attempt, 8))

    # ------------------------------------------------------------------ public

    def ping(self) -> bool:
        """Cheap liveness check — fetches one key only."""
        self._get(f"{SERVICE_PATH}/{ENTITY_SET}", {"$top": "1", "$select": "BusinessPartner"})
        return True

    def fetch_business_partner(self, business_partner: str) -> Optional[BusinessPartnerMaster]:
        """
        Fetch BP master for §4.1's discriminators — entity category, DOB,
        birthplace, nationality, name at birth, foundation date, industry.

        Cached per client. Returns None if the BP can't be read, rather than
        raising: enrichment is additive, and a missing BP must degrade to
        neutral evidence (§3.1 #3) instead of failing the adjudication.
        """
        if business_partner in self._bp_cache:
            return self._bp_cache[business_partner]

        # V2 keyed access: I_BusinessPartner('0010000108')
        path = f"{SERVICE_PATH}/{BP_ENTITY_SET}('{quote(business_partner, safe='')}')"
        try:
            payload = self._get(path, {"$select": ",".join(BP_MASTER_SELECT)})
        except ODataError as exc:
            log.warning("BP master unavailable for %s: %s", business_partner, exc)
            self._bp_cache[business_partner] = None
            return None

        rows = _unwrap(payload.get("d"))
        master = BusinessPartnerMaster.model_validate(rows[0]) if rows else None
        self._bp_cache[business_partner] = master
        return master

    def fetch_business_partners(self, ids: list[str]) -> dict[str, BusinessPartnerMaster]:
        """
        Batch-fetch BP master for many partners.

        At AGP's volume (hundreds of blocked addresses) one keyed GET per BP is
        a couple of minutes of pure round-trip latency. This collapses it into a
        handful of filtered reads instead.

        Populates the same cache fetch_business_partner() uses, so mixing the
        two is safe. Anything the server omits is cached as None so a missing BP
        isn't retried on every sweep.
        """
        wanted = [i for i in dict.fromkeys(ids) if i and i not in self._bp_cache]
        if not wanted:
            return {i: m for i in ids if (m := self._bp_cache.get(i)) is not None}

        # Keep each $filter well under typical gateway URL limits.
        CHUNK = 40
        for start in range(0, len(wanted), CHUNK):
            chunk = wanted[start:start + CHUNK]
            clause = " or ".join(f"BusinessPartner eq '{bp}'" for bp in chunk)
            try:
                payload = self._get(
                    f"{SERVICE_PATH}/{BP_ENTITY_SET}",
                    {"$filter": clause, "$select": ",".join(BP_MASTER_SELECT),
                     "$top": str(len(chunk))},
                )
            except ODataError as exc:
                log.warning("Batch BP fetch failed for %d partner(s): %s", len(chunk), exc)
                # Fall back to individual reads so one bad ID can't blind the whole chunk
                for bp in chunk:
                    self.fetch_business_partner(bp)
                continue

            returned = set()
            for row in _unwrap(payload.get("d")):
                master = BusinessPartnerMaster.model_validate(row)
                self._bp_cache[master.business_partner] = master
                returned.add(master.business_partner)

            for bp in chunk:
                if bp not in returned:
                    self._bp_cache[bp] = None

        return {i: m for i in ids if (m := self._bp_cache.get(i)) is not None}

    def iter_blocked_addresses(
        self,
        since: Optional[datetime] = None,
        *,
        legal_regulation: Optional[str] = None,
        unprocessed_only: bool = False,
        page_size: int = DEFAULT_PAGE_SIZE,
        expand: bool = True,
        enrich_bp: bool = True,
        refetch_empty_hits: bool = False,
    ) -> Iterator[ScreenedPartnerAddress]:
        """
        Yield blocked screening results, newest activity first.

        `since` filters on SPLCheckDateTime — the watermark that makes replay
        safe after downtime (§5.6 #5). Pass None for a full sweep.

        `refetch_empty_hits`: GTS's collection $expand does not always populate
        to_SPLHitsDetailSet; a keyed single-entity read with the same $expand
        reliably does. When True, any record that comes back with empty hits on
        an SPL regulation is re-read by key. Costs one extra round-trip per such
        record but recovers hits that would otherwise be invisible.
        """
        filters = ["SPLScreenedAddressIsBlocked eq true"]
        if since is not None:
            filters.append(f"SPLCheckDateTime gt {_odata_datetime(since)}")
        if legal_regulation:
            filters.append(f"LegalRegulation eq '{quote(legal_regulation)}'")
        if unprocessed_only:
            # '0' means "not yet processed by a human" — same semantics as empty.
            # Both must be included because GTS uses either depending on how the
            # block was created (system decision vs manual block).
            filters.append(
                "(SPLScrngBlockProcessingStatus eq '' or "
                "SPLScrngBlockProcessingStatus eq '0')"
            )

        yield from self._iter_addresses(
            filters, page_size=page_size, expand=expand, enrich_bp=enrich_bp,
            refetch_empty_hits=refetch_empty_hits,
        )

    def iter_decided_addresses(
        self,
        *,
        legal_regulations: Optional[tuple[str, ...]] = SPL_LEGAL_REGULATIONS,
        page_size: int = DEFAULT_PAGE_SIZE,
        expand: bool = True,
        enrich_bp: bool = True,
        refetch_empty_hits: bool = False,
    ) -> Iterator[ScreenedPartnerAddress]:
        """
        Yield screening results a human has already decided — the §11.2 Phase 1
        backtest population.

        Deliberately does NOT filter on SPLScreenedAddressIsBlocked. Releasing a
        record clears that flag, so every record a reviewer cleared has already
        left the blocked-only feed; filtering on it would return only the
        confirmed-match subset and silently bias the backtest toward the one
        verdict the agent must never get wrong.

        Filters on SPLScreeningResultUserDecision instead, restricted to the
        legal regulations that are genuinely SPL screening.
        """
        filters = ["SPLScreeningResultUserDecision ne ''"]
        if legal_regulations:
            clause = " or ".join(f"LegalRegulation eq '{reg}'" for reg in legal_regulations)
            filters.append(f"({clause})")

        yield from self._iter_addresses(
            filters, page_size=page_size, expand=expand, enrich_bp=enrich_bp,
            refetch_empty_hits=refetch_empty_hits,
        )

    def fetch_record_by_key(self, record: ScreenedPartnerAddress) -> ScreenedPartnerAddress:
        """
        Re-read a single record by its composite key with full $expand.

        GTS reliably populates to_SPLHitsDetailSet on keyed single-entity
        reads but not always in collection $expand. This is the fallback for
        records that come back with empty hits in a list request.
        """
        key_parts = [
            f"LegalRegulation='{quote(record.legal_regulation, safe='')}'",
            f"AddressID='{quote(record.address_id, safe='')}'",
            f"BusinessPartner='{quote(record.business_partner, safe='')}'",
            f"LogicalSystemGroup='{quote(record.logical_system_group, safe='')}'",
            f"GTSBusinessPartnerType='{quote(record.gts_bp_type, safe='')}'",
            f"GTSBusinessPartnerExternalID='{quote(record.gts_bp_external_id, safe='')}'",
            f"ForeignTradeOrganization='{quote(record.foreign_trade_org, safe='')}'",
        ]
        key_str = ",".join(key_parts)
        path = f"{SERVICE_PATH}/{ENTITY_SET}({key_str})"
        payload = self._get(path, {"$expand": ",".join(EXPANDS)})
        row = payload.get("d", {})
        if not row or "__deferred" in row:
            return record
        result = self._parse(row)
        result.master = record.master
        return result

    def _iter_addresses(
        self,
        filters: list[str],
        *,
        page_size: int,
        expand: bool,
        enrich_bp: bool,
        refetch_empty_hits: bool = False,
    ) -> Iterator[ScreenedPartnerAddress]:
        """
        Page through the screening root, parse children, enrich BP master.

        Ordered by SPLCheckDateTime because $skip paging without a stable sort
        can duplicate or drop rows between pages.
        """
        params: dict[str, str] = {
            "$filter": " and ".join(filters),
            "$orderby": "SPLCheckDateTime asc",
            "$top": str(page_size),
        }
        if expand:
            params["$expand"] = ",".join(EXPANDS)

        skip = 0
        while True:
            page = dict(params, **{"$skip": str(skip)})
            payload = self._get(f"{SERVICE_PATH}/{ENTITY_SET}", page)
            rows = _unwrap(payload.get("d"))

            if not rows:
                return

            records = [self._parse(row) for row in rows]

            if enrich_bp:
                self.fetch_business_partners([r.business_partner for r in records])
                for record in records:
                    if record.business_partner:
                        record.master = self._bp_cache.get(record.business_partner)

            if refetch_empty_hits:
                for i, record in enumerate(records):
                    if (not record.hits
                            and record.legal_regulation in SPL_LEGAL_REGULATIONS):
                        try:
                            records[i] = self.fetch_record_by_key(record)
                        except ODataError as exc:
                            log.debug("Keyed re-read failed for %s: %s",
                                      record.business_partner, exc)

            yield from records

            if len(rows) < page_size:
                return
            skip += len(rows)

    def count(self, entity_set: str, filt: Optional[str] = None) -> int:
        """
        Row count for any entity set, without pulling bodies.

        Underpins §8's coverage reconciliation — hits in must equal decisions out.
        Note child-only entities (SPLHitsDetailSet) return 0 here: they are only
        addressable through a parent's $expand, so a zero is not evidence of
        absence.
        """
        params = {"sap-client": self.sap_client}
        if filt:
            params["$filter"] = filt
        resp = self._client.get(
            f"{self.base_url}{SERVICE_PATH}/{entity_set}/$count",
            params=params,
            headers={"Accept": "text/plain"},
        )
        if resp.status_code != 200:
            raise ODataError(f"$count on {entity_set} failed: {resp.status_code}")
        body = resp.text.strip()
        if not body.isdigit():
            raise ODataError(f"$count on {entity_set} returned non-numeric: {body[:100]!r}")
        return int(body)

    def count_blocked(self, since: Optional[datetime] = None) -> int:
        """
        Row count without pulling bodies — useful for the coverage
        reconciliation control in §8 (hits in equals decisions out).
        """
        filters = ["SPLScreenedAddressIsBlocked eq true"]
        if since is not None:
            filters.append(f"SPLCheckDateTime gt {_odata_datetime(since)}")
        url = f"{self.base_url}{SERVICE_PATH}/{ENTITY_SET}/$count"
        resp = self._client.get(
            url,
            params={"sap-client": self.sap_client, "$filter": " and ".join(filters)},
            headers={"Accept": "text/plain"},
        )
        if resp.status_code != 200:
            raise ODataError(f"$count failed: {resp.status_code} {resp.text[:200]}")
        return int(resp.text.strip())

    # ----------------------------------------------------------------- parsing

    @staticmethod
    def _parse(row: dict) -> ScreenedPartnerAddress:
        record = ScreenedPartnerAddress.model_validate(row)
        for nav, (model, attr) in EXPANDS.items():
            children = [model.model_validate(c) for c in _unwrap(row.get(nav))]
            setattr(record, attr, children)
        return record
