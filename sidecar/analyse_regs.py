"""
How large is the actually-adjudicatable population in AGP 300?

Only SPLSY and SPLUS are Sanctioned Party List Screening regulations. Everything
else in the catalogue is customs, transit, export control or embargo — those
block a partner on jurisdiction or licence grounds, not on a name match, so they
carry no SPL entry and nothing for the agent to adjudicate.

Read-only. GETs only.
"""

import collections
import sys

sys.path.insert(0, ".")

from core.sap.odata import ENTITY_SET, GtsODataClient

BASE = "https://agpapp.sap.pwc.com:8001"
SPL_REGS = ["SPLSY", "SPLUS"]
OTHER_REGS = ["ZHORG", "ZPLCN", "ZPLEM", "ZPLKW", "ZHIND"]


def main() -> int:
    pw = sys.stdin.readline().rstrip("\r\n")
    if not pw:
        print("no password on stdin")
        return 1

    with GtsODataClient(BASE, "jtyagi002", pw, sap_client="300") as c:
        print("=== Blocked counts by legal regulation ===")
        total_spl = 0
        for reg in SPL_REGS + OTHER_REGS:
            try:
                blocked = c.count(ENTITY_SET,
                                  f"SPLScreenedAddressIsBlocked eq true and LegalRegulation eq '{reg}'")
                allrec = c.count(ENTITY_SET, f"LegalRegulation eq '{reg}'")
            except Exception as exc:
                print(f"  {reg:8s} error: {exc}")
                continue
            tag = "SPL screening" if reg in SPL_REGS else "not SPL"
            print(f"  {reg:8s} blocked={blocked:6d}  of {allrec:6d} screened   ({tag})")
            if reg in SPL_REGS:
                total_spl += blocked

        print(f"\n  -> adjudicatable population (SPL regs, blocked): {total_spl}")

        print("\n=== Do SPL-reg blocked records carry hits? ===")
        for reg in SPL_REGS:
            recs = []
            for i, rec in enumerate(c.iter_blocked_addresses(legal_regulation=reg,
                                                             page_size=100, enrich_bp=False)):
                recs.append(rec)
                if i + 1 >= 100:
                    break
            if not recs:
                print(f"  {reg}: no blocked records")
                continue
            with_hits = [r for r in recs if r.hits]
            lists = collections.Counter(h.spl_list_type for r in recs for h in r.hits)
            provs = collections.Counter(h.gts_data_provider for r in recs for h in r.hits)
            bases = collections.Counter(h.match_basis for r in recs for h in r.hits)
            hitdist = collections.Counter(len(r.hits) for r in recs)
            print(f"  {reg}: sampled {len(recs)}, with_hits={len(with_hits)}")
            print(f"       hits/record : {dict(sorted(hitdist.items()))}")
            print(f"       list types  : {dict(lists) or '-'}")
            print(f"       providers   : {dict(provs) or '-'}")
            print(f"       match basis : {dict(bases) or '-'}")

            for rec in with_hits[:5]:
                print(f"\n       BP {rec.business_partner} addr {rec.address_id} "
                      f"{rec.partner_name!r} [{rec.country}/{rec.city}]")
                print(f"         status={rec.screening_status_text!r} "
                      f"sysdec={rec.system_decision!r} proc={rec.processing_status!r}")
                for h in rec.hits:
                    print(f"         HIT entry={h.spl_entity!r} list={h.spl_list_type!r} "
                          f"provider={h.gts_data_provider!r} basis={h.match_basis!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
