"""
Find the audit-trail service by asking the Gateway catalogue for every
registered service, rather than guessing at naming conventions.

The LLS_* filter used earlier came from a screenshot of /IWFND/MAINT_SERVICE and
may have missed services under other prefixes. READ ONLY — GETs only.
"""

import sys

sys.path.insert(0, ".")

from core.sap.odata import default_verify

import httpx

BASE = "https://agpapp.sap.pwc.com:8001"
CATALOG = f"{BASE}/sap/opu/odata/IWFND/CATALOGSERVICE;v=2/ServiceCollection"

# What an SPL audit-trail service could plausibly be called
NEEDLES = ["AUDIT", "SPLAT", "TRAIL", "HIST", "SCRNG", "SCREEN", "SPL", "BPADDR", "BLKD"]


def main() -> int:
    pw = sys.stdin.readline().rstrip("\r\n")
    if not pw:
        print("no password on stdin")
        return 1

    with httpx.Client(auth=("jtyagi002", pw), timeout=120.0, verify=default_verify()) as c:
        r = c.get(CATALOG, params={"sap-client": "300", "$format": "json", "$top": "5000"},
                  headers={"Accept": "application/json"})
        if r.status_code != 200:
            print(f"catalogue read failed: HTTP {r.status_code}\n{r.text[:400]}")
            return 1

        rows = r.json().get("d", {}).get("results", [])
        print(f"{len(rows)} registered services in client 300\n")

        def field(row, *names):
            for n in names:
                v = row.get(n)
                if v:
                    return str(v)
            return ""

        # Anything that smells like SPL screening or an audit trail
        print("=== Services matching SPL / screening / audit / history ===")
        hits = []
        for row in rows:
            tech = field(row, "TechnicalServiceName", "ID")
            title = field(row, "Title", "Description")
            blob = f"{tech} {title}".upper()
            if any(n in blob for n in NEEDLES):
                hits.append((tech, title))
        for tech, title in sorted(set(hits)):
            print(f"  {tech:38s} {title}")
        if not hits:
            print("  none")

        # Explicitly hunt the word "audit" anywhere
        print("\n=== Anything with 'audit' in name or title ===")
        aud = [(field(r_, "TechnicalServiceName", "ID"), field(r_, "Title", "Description"))
               for r_ in rows
               if "AUDIT" in f"{field(r_, 'TechnicalServiceName', 'ID')} "
                            f"{field(r_, 'Title', 'Description')}".upper()]
        for tech, title in sorted(set(aud)):
            print(f"  {tech:38s} {title}")
        if not aud:
            print("  none — no audit-trail service is registered")

        # Show what fields the catalogue actually gave us, for orientation
        if rows:
            print("\n=== Catalogue row shape (first row keys) ===")
            print("  " + ", ".join(k for k in rows[0] if k != "__metadata"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
