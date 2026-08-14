"""
Is there a usable backtest set in AGP?

124 records carry a human decision and 91 a release reason. A backtest needs
those decisions to sit on SPL-screening records that actually have hits — a
decision on an embargo block tells us nothing about identity adjudication.

READ ONLY — GETs only.
"""

import collections
import sys

sys.path.insert(0, ".")

from core.sap.odata import ENTITY_SET, SERVICE_PATH, GtsODataClient

BASE = "https://agpapp.sap.pwc.com:8001"
SPL_REGS = {"SPLSY", "SPLUS"}


def main() -> int:
    pw = sys.stdin.readline().rstrip("\r\n")
    if not pw:
        print("no password on stdin")
        return 1

    with GtsODataClient(BASE, "jtyagi002", pw, sap_client="300") as c:
        print("=== The 124 records carrying a human decision ===")
        r = c._client.get(
            f"{BASE}{SERVICE_PATH}/{ENTITY_SET}",
            params={
                "sap-client": "300", "$format": "json", "$top": "200",
                "$filter": "SPLScreeningResultUserDecision ne ''",
                "$select": "BusinessPartner,AddressID,LegalRegulation,BusinessPartnerName,"
                           "Country,SPLScreeningResultUserDecision,SPLScrngResultSystemDecision,"
                           "CstmsCmplncBlockReleaseReason,CstmsCmplncBlockReleaseReason_Text,"
                           "SPLScreeningCheckStatus_Text,SPLScreenedAddressIsBlocked,"
                           "LastChangedByUser,SPLScreeningBlockProcessor",
            },
            headers={"Accept": "application/json"},
        )
        if r.status_code != 200:
            print(f"  HTTP {r.status_code}: {r.text[:300]}")
            return 1
        rows = r.json().get("d", {}).get("results", [])
        print(f"  retrieved {len(rows)}\n")

        print("  by legal regulation :", dict(collections.Counter(
            x.get("LegalRegulation") for x in rows)))
        print("  user decisions      :", dict(collections.Counter(
            x.get("SPLScreeningResultUserDecision") for x in rows)))
        print("  system decisions    :", dict(collections.Counter(
            x.get("SPLScrngResultSystemDecision") for x in rows)))
        print("  release reasons     :", dict(collections.Counter(
            (x.get("CstmsCmplncBlockReleaseReason_Text")
             or x.get("CstmsCmplncBlockReleaseReason") or "(none)") for x in rows)))
        print("  decided by          :", dict(collections.Counter(
            x.get("LastChangedByUser") or "(blank)" for x in rows)))
        print("  still blocked       :", dict(collections.Counter(
            bool(x.get("SPLScreenedAddressIsBlocked")) for x in rows)))

        spl = [x for x in rows if x.get("LegalRegulation") in SPL_REGS]
        print(f"\n  >>> on an SPL regulation: {len(spl)} of {len(rows)}")
        for x in spl[:15]:
            print(f"      BP {x.get('BusinessPartner'):>6} {str(x.get('BusinessPartnerName'))[:30]:32s}"
                  f" reg={x.get('LegalRegulation')} user={x.get('SPLScreeningResultUserDecision')!r}"
                  f" sys={x.get('SPLScrngResultSystemDecision')!r}"
                  f" reason={x.get('CstmsCmplncBlockReleaseReason_Text')!r}")

        print("\n=== Do the SPL-reg decided records carry hits? ===")
        if not spl:
            print("  no SPL-regulation records among the decided set")
        else:
            keys = {(x["BusinessPartner"], x["AddressID"], x["LegalRegulation"]) for x in spl}
            found = 0
            for reg in sorted({k[2] for k in keys}):
                for rec in c.iter_blocked_addresses(legal_regulation=reg, page_size=100,
                                                    enrich_bp=False):
                    if (rec.business_partner, rec.address_id, rec.legal_regulation) in keys:
                        found += 1
                        print(f"  BP {rec.business_partner} {rec.partner_name!r} "
                              f"hits={len(rec.hits)} decision={rec.user_decision!r}")
                        for h in rec.hits:
                            print(f"     entry={h.spl_entity!r} list={h.spl_list_type!r} "
                                  f"basis={h.match_basis!r}")
            print(f"  -> {found} decided SPL record(s) still in the blocked feed")
            print("     (decided records that were released are no longer 'blocked',")
            print("      so a backtest must read them unfiltered, not via the blocked filter)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
