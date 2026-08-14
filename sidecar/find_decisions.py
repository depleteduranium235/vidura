"""
Does AGP hold ANY human adjudication decisions?

A backtest needs (hit, evidence, decision-a-human-made). The decision fields
exist on records we can already read; this asks whether any record has them
populated, and dumps the Manage Partner Screening Result header, which is the
other candidate carrier of decision history.

READ ONLY — GETs only.
"""

import sys

sys.path.insert(0, ".")

import httpx

from core.sap.odata import ENTITY_SET, SERVICE_PATH, GtsODataClient, default_verify

BASE = "https://agpapp.sap.pwc.com:8001"
RSLT = "/sap/opu/odata/sap/LLS_BPADDRSCRNGRSLT_MNG"


def main() -> int:
    pw = sys.stdin.readline().rstrip("\r\n")
    if not pw:
        print("no password on stdin")
        return 1

    with GtsODataClient(BASE, "jtyagi002", pw, sap_client="300") as c:
        print("=== Has anyone ever adjudicated anything in AGP? ===")
        checks = [
            ("SPLScreeningResultUserDecision ne ''", "records with a USER decision"),
            ("SPLScrngResultSystemDecision ne ''", "records with a system decision"),
            ("CstmsCmplncBlockReleaseReason ne ''", "records with a release reason"),
            ("ProcessComment ne ''", "records with a process comment"),
            ("GTSCommentIsExisting eq true", "records flagged as having a comment"),
            ("SPLScreeningBlockProcessor ne ''", "records with an assigned processor"),
            ("LastChangedByUser ne ''", "records ever changed by a user"),
        ]
        for filt, label in checks:
            try:
                print(f"  {label:44s} {c.count(ENTITY_SET, filt):>8}")
            except Exception as exc:
                print(f"  {label:44s} error: {str(exc)[:50]}")

        print("\n=== Who created / changed these records? ===")
        try:
            r = c._client.get(
                f"{BASE}{SERVICE_PATH}/{ENTITY_SET}",
                params={"sap-client": "300", "$format": "json", "$top": "200",
                        "$select": "CreatedByUser,LastChangedByUser,SPLScreeningCheckStatus_Text"},
            )
            rows = r.json().get("d", {}).get("results", []) if r.status_code == 200 else []
            import collections
            print("  created by :", dict(collections.Counter(
                x.get("CreatedByUser") or "(blank)" for x in rows)))
            print("  changed by :", dict(collections.Counter(
                x.get("LastChangedByUser") or "(blank)" for x in rows)))
            print("  statuses   :", dict(collections.Counter(
                x.get("SPLScreeningCheckStatus_Text") or "(blank)" for x in rows)))
        except Exception as exc:
            print(f"  error: {exc}")

    # The other candidate: Manage Partner Screening Result header
    with httpx.Client(auth=("jtyagi002", pw), timeout=120.0, verify=default_verify()) as h:
        print("\n=== SPLBlkdAddrHdr (Manage Partner Screening Result) — a real row ===")
        r = h.get(f"{BASE}{RSLT}/SPLBlkdAddrHdrSet",
                  params={"sap-client": "300", "$format": "json", "$top": "1",
                          "$expand": "to_SPLBlkdAddrCmt,to_SPLHitsDetailSet"},
                  headers={"Accept": "application/json"})
        if r.status_code != 200:
            print(f"  HTTP {r.status_code}: {r.text[:200]}")
            return 0
        rows = r.json().get("d", {}).get("results", [])
        if not rows:
            print("  no rows")
            return 0
        row = rows[0]
        print("  POPULATED:")
        for k, v in sorted(row.items()):
            if k == "__metadata" or isinstance(v, (dict, list)):
                continue
            if v not in (None, "", False):
                print(f"    {k:38s} {v!r}")
        print("  EMPTY:", ", ".join(
            k for k, v in sorted(row.items())
            if k != "__metadata" and not isinstance(v, (dict, list)) and v in (None, "", False)))
        for nav in ("to_SPLBlkdAddrCmt", "to_SPLHitsDetailSet"):
            node = row.get(nav)
            inner = node.get("results", []) if isinstance(node, dict) else []
            print(f"  {nav}: {len(inner)} row(s)")
            for x in inner[:3]:
                print(f"    {{k: v for k, v in x.items() if v}}".replace("{k: v for k, v in x.items() if v}",
                      str({k: v for k, v in x.items() if k != '__metadata' and v not in (None, '')})))
    return 0


if __name__ == "__main__":
    sys.exit(main())
