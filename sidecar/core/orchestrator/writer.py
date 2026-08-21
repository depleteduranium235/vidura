"""
Result writer protocol and implementations.

The writer is the seam between the orchestrator and the result store.
Swapping from stub to production is a one-line change in run_orchestrator.py.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from ..models.schemas import AdjudicationResult

log = logging.getLogger(__name__)

DEFAULT_JSONL_PATH = Path(__file__).resolve().parent.parent.parent / "adjudication_results.jsonl"


@runtime_checkable
class ResultWriter(Protocol):
    def write(self, result: AdjudicationResult) -> None: ...
    def close(self) -> None: ...


class JsonlWriter:
    """
    Development stub: one JSON line per result, idempotent on case_id.

    Production will POST to the Z result table via OData V4, with the same
    write() signature — the orchestrator doesn't know or care which one it's
    talking to.
    """

    def __init__(self, path: Optional[Path] = None):
        self._path = path or DEFAULT_JSONL_PATH
        self._seen: set[str] = set()
        if self._path.exists():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        self._seen.add(json.loads(line)["case_id"])
                    except (json.JSONDecodeError, KeyError):
                        pass

    def write(self, result: AdjudicationResult) -> None:
        if result.case_id in self._seen:
            log.debug("Duplicate write for %s — idempotent skip", result.case_id)
            return
        self._seen.add(result.case_id)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(result.model_dump_json() + "\n")
        log.info("WROTE %s -> %s", result.case_id, result.disposition_band.value)

    def close(self) -> None:
        pass


class ZTableWriter:
    """
    Production writer: POST to the Z result table via the RAP OData V4 service.

    Service URL (AGP client 300):
      /sap/opu/odata4/sap/zspl_adj_v4/srvd_a2x/sap/zspl_adjudication/0001/

    Idempotent on case_id: checks if the case already exists before creating.
    Uses the same interface as JsonlWriter so the orchestrator doesn't know or
    care which one it's talking to.
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        sap_client: str = "300",
    ):
        import ssl
        try:
            import truststore
            verify = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        except ImportError:
            verify = True

        import httpx
        self._base = base_url.rstrip("/")
        self._sap_client = sap_client
        self._service_path = "/sap/opu/odata4/sap/zspl_adj_v4/srvd_a2x/sap/zspl_adjudication/0001"
        self._client = httpx.Client(
            auth=(username, password),
            verify=verify,
            timeout=60,
            follow_redirects=True,
        )
        self._csrf: Optional[str] = None
        self._seen: set[str] = set()

    def _fetch_csrf(self) -> str:
        r = self._client.get(
            f"{self._base}{self._service_path}/",
            params={"sap-client": self._sap_client},
            headers={"X-CSRF-Token": "Fetch", "Accept": "application/json"},
        )
        return r.headers.get("x-csrf-token", "")

    def _to_odata_payload(self, result: AdjudicationResult) -> dict:
        return {
            "CaseID": result.case_id[:20],
            "BusinessPartnerID": (result.case_id.split("::")[0] if "::" in result.case_id else "")[:10],
            "BusinessPartnerName": "",
            "BPCountry": "",
            "SPLEntryID": (result.case_id.split("::")[-1] if "::" in result.case_id else "")[:30],
            "SPLEntryName": "",
            "SPLListType": "",
            "SPLProgramme": "",
            "MatchBasis": "",
            "IntakePath": "BP Block",
            "DispositionBand": result.disposition_band.value,
            "Status": "Agent Complete",
            "Priority": "High" if result.disposition_band.value == "Escalate" else "Medium",
            "AgentRationale": result.rationale[:2000] if result.rationale else "",
            "WhatWouldChangeMyMind": result.what_would_change[:500] if result.what_would_change else "",
            "EvidenceSummary": result.evidence_summary[:500] if result.evidence_summary else "",
            "ModelVersion": result.model_version[:40] if result.model_version else "",
            "PromptVersion": result.prompt_version[:40] if result.prompt_version else "",
            "BandLogicVersion": result.band_logic_version[:20] if result.band_logic_version else "",
            "TaxonomyVersion": result.taxonomy_version[:20] if result.taxonomy_version else "",
        }

    def write(self, result: AdjudicationResult) -> None:
        if result.case_id in self._seen:
            log.debug("Duplicate write for %s — idempotent skip", result.case_id)
            return

        if not self._csrf:
            self._csrf = self._fetch_csrf()

        payload = self._to_odata_payload(result)

        r = self._client.post(
            f"{self._base}{self._service_path}/AdjudicationCase",
            params={"sap-client": self._sap_client},
            json=payload,
            headers={
                "X-CSRF-Token": self._csrf,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )

        if r.status_code == 403 and "csrf" in r.text.lower():
            self._csrf = self._fetch_csrf()
            r = self._client.post(
                f"{self._base}{self._service_path}/AdjudicationCase",
                params={"sap-client": self._sap_client},
                json=payload,
                headers={
                    "X-CSRF-Token": self._csrf,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )

        if r.status_code in (200, 201):
            self._seen.add(result.case_id)
            created = r.json()
            log.info(
                "WROTE %s -> %s (UUID: %s)",
                result.case_id, result.disposition_band.value,
                created.get("CaseUUID", "?"),
            )
        else:
            raise RuntimeError(
                f"OData POST failed for {result.case_id}: "
                f"HTTP {r.status_code} — {r.text[:200]}"
            )

    def close(self) -> None:
        self._client.close()
