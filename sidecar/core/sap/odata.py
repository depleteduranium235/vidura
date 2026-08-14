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
import time
from datetime import datetime, timezone
from typing import Any, Iterator, Optional
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


class ODataError(RuntimeError):
    """Non-retryable failure from the SAP Gateway."""


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
        verify: bool | str = True,
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
            verify=verify,
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

    def iter_blocked_addresses(
        self,
        since: Optional[datetime] = None,
        *,
        legal_regulation: Optional[str] = None,
        unprocessed_only: bool = False,
        page_size: int = DEFAULT_PAGE_SIZE,
        expand: bool = True,
        enrich_bp: bool = True,
    ) -> Iterator[ScreenedPartnerAddress]:
        """
        Yield blocked screening results, newest activity first.

        `since` filters on SPLCheckDateTime — the watermark that makes replay
        safe after downtime (§5.6 #5). Pass None for a full sweep.
        """
        filters = ["SPLScreenedAddressIsBlocked eq true"]
        if since is not None:
            filters.append(f"SPLCheckDateTime gt {_odata_datetime(since)}")
        if legal_regulation:
            filters.append(f"LegalRegulation eq '{quote(legal_regulation)}'")
        if unprocessed_only:
            filters.append("SPLScrngBlockProcessingStatus eq ''")

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

            for row in rows:
                record = self._parse(row)
                if enrich_bp and record.business_partner:
                    record.master = self.fetch_business_partner(record.business_partner)
                yield record

            if len(rows) < page_size:
                return
            skip += len(rows)

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
