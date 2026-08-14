#!/usr/bin/env python
"""
One-pass read-only exploration of AGP 300's GTS OData surface.

Answers, in a single run:
  1. Which LLS_* services are reachable with your authorization
  2. What each one's entity model looks like
  3. How many blocked screening results actually exist
  4. What real records contain — the open question metadata cannot answer
  5. Whether the sidecar's read client works against live data

READ-ONLY BY CONSTRUCTION
  - Only GET is ever issued; there is no code path here that can POST, PUT,
    PATCH, MERGE or DELETE.
  - No $batch (which can smuggle writes).
  - No function imports are invoked, since those can have side effects even
    over GET.
  - Nothing is written back to SAP. Output goes to local files only.

Usage:
    python explore_agp.py                       # prompts for user + password
    python explore_agp.py --user MYUSER         # prompts for password only
    python explore_agp.py --no-data             # metadata and counts only

The password is read from a hidden prompt and held in memory for the run.
It is never echoed, never written to disk, and never placed in argv.
"""

from __future__ import annotations

import argparse
import getpass
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import httpx

BASE = "https://agpapp.sap.pwc.com:8001"
SAP_CLIENT = "300"
OUT_DIR = Path(__file__).parent / "agp_exploration"

# Every GTS service seen in /IWFND/MAINT_SERVICE, by external name.
SERVICES = [
    "LLS_BPADDR_MNG_SRV",
    "LLS_BPADDRSCRNGRSLT_MNG",
    "LLS_BLKDBPADDR_MNG",
    "LLS_BLKDCCD_MNG",
    "LLS_BLKDCCD_RSV",
    "LLS_CCD_DSP",
    "LLS_CUSTLTSD_MNG",
    "LLS_CUSTPREF_MNG",
    "LLS_EXPORTCONF_MNG",
    "LLS_EXPORTDECLN_DSP",
    "LLS_EXPORTDECLN_MNG",
    "LLS_LTSDREQUEST_MNG",
    "LLS_PPF",
    "LLS_SUPLRLTSD_MNG",
    "LLS_SUPLRPREF_MNG",
    "LLS_TRANSITCONF_MNG",
    "LLS_TRANSITDECLN_DSP",
    "LLS_TRANSITDECLN_MNG",
]

# Entity sets worth sampling, per service. Kept small and specific so we pull a
# handful of rows rather than trawling the system.
SAMPLE_TARGETS = {
    "LLS_BPADDR_MNG_SRV": ["C_SPLScrngScreenedPrtnAddress"],
    "LLS_BPADDRSCRNGRSLT_MNG": ["SPLBlkdAddrHdrSet", "SPLHitsDetailSet", "SPLAssocPartnerAddrSet"],
    "LLS_BLKDBPADDR_MNG": ["SPLScrngBlkdPartnerAddressSet"],
}

# Fields that would carry sanctioned-party content (§4.1). We already know the
# three main services lack them; this checks every remaining service too.
SPL_CONTENT_NEEDLES = [
    "ALIAS", "BIRTH", "DOB", "NATIONAL", "CITIZEN", "PASSPORT", "PROGRAMME",
    "REMARK", "NARRATIV", "SANCTION", "DENIED", "VESSEL", "AIRCRAFT",
]

# Values that look like personal data, redacted in the saved samples so real
# individuals' details do not end up in a file or a transcript (§12 / DPIA).
REDACT_FIELD_HINTS = [
    "BIRTHDATE", "BIRTHPLACE", "BIRTHNAME", "NATIONALITY", "PASSPORT",
    "IDNUMBER", "IDENTIFICATIONNUMBER", "TAXNUMBER", "BANKACCOUNT", "IBAN",
]


def ln(elem) -> str:
    return elem.tag.split("}")[-1]


class Explorer:
    def __init__(self, client: httpx.Client, redact: bool):
        self.client = client
        self.redact = redact
        self.report: dict = {
            "system": "AGP",
            "sap_client": SAP_CLIENT,
            "run_at": datetime.now(timezone.utc).isoformat(),
            "services": {},
            "findings": [],
        }

    # -- GET is the only verb used anywhere in this script ------------------

    def get(self, url: str, params: dict | None = None, accept: str = "application/json"):
        return self.client.get(url, params=params, headers={"Accept": accept})

    def note(self, text: str) -> None:
        self.report["findings"].append(text)
        print(f"    ! {text}")

    # -- metadata -----------------------------------------------------------

    def probe_service(self, svc: str) -> dict | None:
        url = f"{BASE}/sap/opu/odata/sap/{svc}/$metadata"
        try:
            resp = self.get(url, {"sap-client": SAP_CLIENT, "sap-language": "EN"}, "application/xml")
        except httpx.HTTPError as exc:
            print(f"  {svc:26s} transport error: {exc}")
            return None

        if resp.status_code != 200:
            reason = {401: "not authorized", 403: "forbidden", 404: "not found"}.get(
                resp.status_code, f"HTTP {resp.status_code}"
            )
            print(f"  {svc:26s} {reason}")
            return {"reachable": False, "status": resp.status_code, "reason": reason}

        (OUT_DIR / "metadata").mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "metadata" / f"{svc}.xml").write_text(resp.text, encoding="utf-8")

        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as exc:
            return {"reachable": True, "status": 200, "parse_error": str(exc)}

        entities, sets, spl_content = {}, [], []
        for e in root.iter():
            if ln(e) == "EntityType":
                props = [p.get("Name") for p in e if ln(p) == "Property"]
                entities[e.get("Name")] = {
                    "keys": [k.get("Name") for k in e.iter() if ln(k) == "PropertyRef"],
                    "property_count": len(props),
                    "navigations": [n.get("Name") for n in e if ln(n) == "NavigationProperty"],
                }
                for p in props:
                    flat = (p or "").upper().replace("_", "")
                    if any(x in flat for x in SPL_CONTENT_NEEDLES):
                        spl_content.append(f"{e.get('Name')}.{p}")
            elif ln(e) == "EntitySet":
                sets.append(e.get("Name"))

        print(f"  {svc:26s} OK  {len(entities):2d} entities, {len(sets):2d} sets")
        return {
            "reachable": True,
            "status": 200,
            "entity_count": len(entities),
            "entities": entities,
            "entity_sets": sets,
            "possible_spl_content_fields": spl_content,
        }

    # -- counts and samples -------------------------------------------------

    def count(self, svc: str, entity_set: str, filt: str | None = None) -> int | str:
        params = {"sap-client": SAP_CLIENT}
        if filt:
            params["$filter"] = filt
        resp = self.get(f"{BASE}/sap/opu/odata/sap/{svc}/{entity_set}/$count", params, "text/plain")
        if resp.status_code != 200:
            return f"HTTP {resp.status_code}"
        body = resp.text.strip()
        return int(body) if body.isdigit() else body

    def sample(self, svc: str, entity_set: str, top: int = 3, expand: str | None = None) -> dict:
        params = {"sap-client": SAP_CLIENT, "$format": "json", "$top": str(top)}
        if expand:
            params["$expand"] = expand
        resp = self.get(f"{BASE}/sap/opu/odata/sap/{svc}/{entity_set}", params)
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}", "body": resp.text[:300]}
        try:
            rows = resp.json().get("d", {}).get("results", [])
        except json.JSONDecodeError:
            return {"error": "non-JSON response", "body": resp.text[:300]}
        return {"row_count": len(rows), "rows": [self._clean(r) for r in rows]}

    def _clean(self, row):
        """Strip V2 __metadata noise and redact anything that looks personal."""
        if isinstance(row, list):
            return [self._clean(r) for r in row]
        if not isinstance(row, dict):
            return row
        out = {}
        for k, v in row.items():
            if k == "__metadata":
                continue
            if isinstance(v, dict) and "__deferred" in v:
                continue
            if isinstance(v, dict) and "results" in v:
                out[k] = self._clean(v["results"])
                continue
            if isinstance(v, (dict, list)):
                out[k] = self._clean(v)
                continue
            if self.redact and v not in (None, "", False) and self._is_personal(k):
                out[k] = f"<redacted {type(v).__name__}, len={len(str(v))}>"
            else:
                out[k] = v
        return out

    @staticmethod
    def _is_personal(field: str) -> bool:
        flat = field.upper().replace("_", "")
        return any(h in flat for h in REDACT_FIELD_HINTS)


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only exploration of AGP 300 GTS OData")
    ap.add_argument("--user", help="SAP user (prompted if omitted)")
    ap.add_argument("--no-data", action="store_true", help="metadata and counts only, no rows")
    ap.add_argument("--no-redact", action="store_true",
                    help="do not redact personal-looking values in saved samples")
    args = ap.parse_args()

    # core.sap must be importable before we build the HTTP client
    sys.path.insert(0, str(Path(__file__).parent))

    user = args.user or input("SAP user: ").strip()
    password = getpass.getpass(f"Password for {user} on AGP/{SAP_CLIENT}: ")
    if not password:
        print("No password given, aborting.")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\nAGP 300 exploration  ({BASE})")
    print(f"user={user}  redact_personal_data={not args.no_redact}\n")

    # Verify against the OS trust store: AGP's chain goes through corporate PKI
    # that Windows trusts but certifi doesn't ship, which is why curl (Schannel)
    # works where plain Python fails.
    from core.sap.odata import default_verify

    with httpx.Client(
        auth=(user, password),
        timeout=90.0,
        follow_redirects=False,
        verify=default_verify(),
    ) as http:
        ex = Explorer(http, redact=not args.no_redact)

        # 1 -- reachability and models
        print("[1/5] Service metadata")
        for svc in SERVICES:
            result = ex.probe_service(svc)
            if result:
                ex.report["services"][svc] = result

        reachable = [s for s, r in ex.report["services"].items() if r.get("reachable")]
        print(f"\n      {len(reachable)}/{len(SERVICES)} services reachable")

        # 2 -- does ANY service carry sanctioned-party content?
        print("\n[2/5] Sanctioned-party content scan (§4.1)")
        found = {s: r["possible_spl_content_fields"]
                 for s, r in ex.report["services"].items()
                 if r.get("possible_spl_content_fields")}
        if not reachable:
            # Absence of evidence is not evidence of absence (§3.1 #3) - with no
            # services reachable this scan proves nothing.
            ex.note("Scan inconclusive: no services were reachable, so nothing "
                    "can be concluded about SPL content availability")
        elif found:
            for svc, fields in found.items():
                print(f"  {svc}: {fields}")
            ex.note(f"Candidate SPL-content fields in {len(found)} service(s); needs "
                    "manual review (BP-side DOB/nationality match these needles too)")
        else:
            ex.note(f"No SPL entry content across {len(reachable)} reachable service(s) - "
                    "confirms a Z CDS view over /SAPSLL/TSPL* is required (needs client 200)")

        # 3 -- how much data is actually in there?
        print("\n[3/5] Record counts")
        if "LLS_BPADDR_MNG_SRV" in reachable:
            total = ex.count("LLS_BPADDR_MNG_SRV", "C_SPLScrngScreenedPrtnAddress")
            blocked = ex.count("LLS_BPADDR_MNG_SRV", "C_SPLScrngScreenedPrtnAddress",
                               "SPLScreenedAddressIsBlocked eq true")
            ex.report["counts"] = {"screened_addresses": total, "blocked_addresses": blocked}
            print(f"  screened addresses : {total}")
            print(f"  blocked addresses  : {blocked}")
            if blocked == 0:
                ex.note("Zero blocked addresses - AGP has no hits to adjudicate. "
                        "MVP needs test data seeded or synthetic hits.")
            elif isinstance(blocked, int) and blocked < 5:
                ex.note(f"Only {blocked} blocked address(es) - thin for a demo, "
                        "workable for an integration test.")

        # 4 -- what do real records look like?
        if not args.no_data:
            print("\n[4/5] Sample records")
            for svc, sets in SAMPLE_TARGETS.items():
                if svc not in reachable:
                    continue
                for es in sets:
                    expand = None
                    if es == "C_SPLScrngScreenedPrtnAddress":
                        expand = ("to_SPLHitsDetailSet,to_SPLScrngBPIdentification,"
                                  "to_SPLScrngBPAssocdBlkdObj,to_SPLScrngBPAssociatedBank")
                    elif es == "SPLBlkdAddrHdrSet":
                        expand = "to_SPLHitsDetailSet,to_SPLIdsDetailSet,to_SPLAssocPartnerAddrSet"
                    out = ex.sample(svc, es, top=3, expand=expand)
                    ex.report["services"][svc].setdefault("samples", {})[es] = out
                    status = out.get("error") or f"{out.get('row_count', 0)} row(s)"
                    print(f"  {svc}/{es}: {status}")
        else:
            print("\n[4/5] Sample records skipped (--no-data)")

        # 5 -- does the sidecar's own client work against live data?
        print("\n[5/5] Live check of the sidecar read client")
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from core.sap import GtsODataClient
            from core.sap.mapper import to_hit_inputs, network_evidence_notes

            with GtsODataClient(BASE, user, password, sap_client=SAP_CLIENT,
                                http_client=http) as client:
                records = list(islice_iter(client.iter_blocked_addresses(), 3))
                print(f"  client returned {len(records)} record(s)")
                summary = []
                for rec in records:
                    hits = to_hit_inputs(rec)
                    summary.append({
                        "case_key": rec.case_key,
                        "entity_type": rec.entity_type,
                        "bp_master_fetched": rec.master is not None,
                        "hit_count": len(rec.hits),
                        "spl_entities": [h.spl_entity for h in rec.hits],
                        "match_bases": [h.match_basis for h in rec.hits],
                        "identification_count": len(rec.identifications),
                        "network_evidence": network_evidence_notes(rec),
                        "hit_input_count": len(hits),
                    })
                    print(f"    {rec.case_key[:20]}... "
                          f"{rec.entity_type or '(no entity type)'} "
                          f"hits={len(rec.hits)} ids={len(rec.identifications)} "
                          f"master={'yes' if rec.master else 'no'}")
                ex.report["client_check"] = {"ok": True, "records": summary}
                if records and not any(r.hits for r in records):
                    ex.note("Blocked records exist but carry no hit detail - "
                            "SPLHitsDetail is empty, so there is no BP<->SPL pair to adjudicate")
        except Exception as exc:  # noqa: BLE001 - report rather than crash the run
            print(f"  client check FAILED: {type(exc).__name__}: {exc}")
            ex.report["client_check"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    report_path = OUT_DIR / "report.json"
    report_path.write_text(json.dumps(ex.report, indent=2, default=str), encoding="utf-8")

    print(f"\nWrote {report_path}")
    print(f"      {OUT_DIR / 'metadata'}/*.xml")
    if ex.report["findings"]:
        print("\nFindings:")
        for f in ex.report["findings"]:
            print(f"  - {f}")
    return 0


def islice_iter(iterator, n):
    """Take at most n items; avoids pulling the whole result set."""
    for i, item in enumerate(iterator):
        if i >= n:
            return
        yield item


if __name__ == "__main__":
    sys.exit(main())
