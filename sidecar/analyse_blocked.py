"""
Ad-hoc: how much evidence do AGP's real blocked records actually carry?

Reads the password from stdin so it never lands in argv or a file.
Read-only — iter_blocked_addresses issues GETs only.
"""

import collections
import sys

sys.path.insert(0, ".")

from core.sap import GtsODataClient
from core.sap.mapper import network_evidence_notes, to_hit_inputs

LIMIT = 60


def main() -> int:
    pw = sys.stdin.readline().rstrip("\r\n")
    if not pw:
        print("no password on stdin")
        return 1

    with GtsODataClient("https://agpapp.sap.pwc.com:8001", "jtyagi002", pw,
                        sap_client="300") as c:
        recs = []
        for i, rec in enumerate(c.iter_blocked_addresses(page_size=100)):
            recs.append(rec)
            if i + 1 >= LIMIT:
                break

    n = len(recs)
    print(f"Pulled {n} BLOCKED records\n")
    if not n:
        print("No blocked records returned — check the filter.")
        return 0

    def pct(k):
        return f"{k}/{n}"

    print("=== Evidence availability ===")
    print(f"  >=1 hit                 : {pct(sum(1 for r in recs if r.hits))}")
    print(f"  identifications         : {pct(sum(1 for r in recs if r.identifications))}")
    print(f"  assoc blocked objects   : {pct(sum(1 for r in recs if r.associated_blocked_objects))}")
    print(f"  assoc banks             : {pct(sum(1 for r in recs if r.associated_banks))}")
    print(f"  indirect block flagged  : {pct(sum(1 for r in recs if r.block_is_indirect))}")
    print(f"  blocked-bank flagged    : {pct(sum(1 for r in recs if r.has_blocked_associated_bank))}")
    print(f"  BP master fetched       : {pct(sum(1 for r in recs if r.master))}")

    masters = [r.master for r in recs if r.master]
    if masters:
        m = len(masters)
        print(f"\n=== BP master richness (§4.1 discriminators), n={m} ===")
        for label, fn in (
            ("entity category", lambda x: x.category),
            ("DOB", lambda x: x.birth_date),
            ("nationality", lambda x: x.nationality),
            ("birthplace", lambda x: x.birthplace),
            ("name at birth", lambda x: x.birth_name),
            ("foundation date", lambda x: x.foundation_date),
            ("industry", lambda x: x.industry),
        ):
            print(f"  {label:18s}: {sum(1 for x in masters if fn(x))}/{m}")

    print("\n=== Distributions ===")
    print("  legal regs  :", dict(collections.Counter(r.legal_regulation for r in recs)))
    print("  entity types:", dict(collections.Counter(r.entity_type or "(unset)" for r in recs)))
    print("  statuses    :", dict(collections.Counter(r.screening_status_text for r in recs)))
    print("  list types  :", dict(collections.Counter(h.spl_list_type for r in recs for h in r.hits)))
    print("  providers   :", dict(collections.Counter(h.gts_data_provider for r in recs for h in r.hits)))
    print("  match basis :", dict(collections.Counter(h.match_basis for r in recs for h in r.hits)))
    print("  hits/record :", dict(collections.Counter(len(r.hits) for r in recs)))

    with_hits = [r for r in recs if r.hits]
    if with_hits:
        ex = with_hits[0]
        print("\n=== One real blocked record ===")
        print(f"  BP {ex.business_partner} addr {ex.address_id}: {ex.partner_name!r}")
        print(f"  entity_type={ex.entity_type!r} country={ex.country!r} city={ex.city!r}")
        print(f"  status={ex.screening_status_text!r} system_decision={ex.system_decision!r}")
        print(f"  case_key={ex.case_key}")
        for h in ex.hits:
            print(f"   HIT entry={h.spl_entity!r} list={h.spl_list_type!r} "
                  f"group={h.spl_group_desc or h.spl_group!r} basis={h.match_basis!r}")
        print(f"  network evidence: {network_evidence_notes(ex) or 'none'}")
        hi = to_hit_inputs(ex)[0]
        print(f"\n  -> HitInput  entity={hi.bp_entity_type!r} dob={hi.bp_date_of_birth!r} "
              f"nat={hi.bp_nationality!r}")
        print(f"     identifiers={hi.bp_all_identifiers} reg={hi.bp_registration_no!r}")
        print(f"     spl_entry_name={hi.spl_entry_name!r}   <-- the gap")
    return 0


if __name__ == "__main__":
    sys.exit(main())
