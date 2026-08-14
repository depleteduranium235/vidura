"""
Characterise AGP's business partner population — READ ONLY, GETs only.

The earlier "BP data is all dummy" conclusion came from 60 records that were all
ZHORG export-control blocks, which is a biased sample. This asks the population
directly: are there natural persons? BPs with a date of birth, a nationality, a
registration number? Those are the records worth adjudicating.
"""

import sys

sys.path.insert(0, ".")

from core.sap.odata import BP_ENTITY_SET, ENTITY_SET, SERVICE_PATH, GtsODataClient

BASE = "https://agpapp.sap.pwc.com:8001"


def main() -> int:
    pw = sys.stdin.readline().rstrip("\r\n")
    if not pw:
        print("no password on stdin")
        return 1

    with GtsODataClient(BASE, "jtyagi002", pw, sap_client="300") as c:

        def count(entity, filt=None, label=""):
            try:
                n = c.count(entity, filt)
                print(f"  {label or filt or 'total':52s} {n:>8}")
                return n
            except Exception as exc:
                print(f"  {label or filt:52s} error: {str(exc)[:60]}")
                return None

        print("=== Business partner population ===")
        count(BP_ENTITY_SET, None, "total BPs")
        print()
        print("  by category (1=Person 2=Organization 3=Group):")
        for cat, name in (("1", "Person"), ("2", "Organization"), ("3", "Group")):
            count(BP_ENTITY_SET, f"BusinessPartnerCategory eq '{cat}'", f"    category {cat} ({name})")

        print("\n  with §4.1 discriminators populated:")
        for filt, label in (
            ("BirthDate ne null", "    date of birth"),
            ("BusPartNationality ne ''", "    nationality"),
            ("BusinessPartnerBirthplaceName ne ''", "    birthplace"),
            ("BusinessPartnerBirthName ne ''", "    name at birth"),
            ("OrganizationFoundationDate ne null", "    foundation date"),
            ("Industry ne ''", "    industry"),
            ("BusinessPartnerIsBlocked eq true", "    centrally blocked"),
        ):
            count(BP_ENTITY_SET, filt, label)

        print("\n=== Natural persons, if any ===")
        try:
            r = c._client.get(
                f"{BASE}{SERVICE_PATH}/{BP_ENTITY_SET}",
                params={"sap-client": "300", "$format": "json", "$top": "10",
                        "$filter": "BusinessPartnerCategory eq '1'",
                        "$select": "BusinessPartner,FirstName,LastName,BirthDate,"
                                   "BusPartNationality,BusinessPartnerBirthplaceName,Industry"},
            )
            rows = r.json().get("d", {}).get("results", []) if r.status_code == 200 else []
            if not rows:
                print(f"  none returned (HTTP {r.status_code})")
            for row in rows:
                vals = {k: v for k, v in row.items()
                        if k != "__metadata" and v not in (None, "")}
                print(f"  {vals}")
        except Exception as exc:
            print(f"  error: {exc}")

        print("\n=== BPs that carry a date of birth ===")
        try:
            r = c._client.get(
                f"{BASE}{SERVICE_PATH}/{BP_ENTITY_SET}",
                params={"sap-client": "300", "$format": "json", "$top": "10",
                        "$filter": "BirthDate ne null",
                        "$select": "BusinessPartner,FirstName,LastName,BirthDate,"
                                   "BusPartNationality,BusinessPartnerCategory"},
            )
            rows = r.json().get("d", {}).get("results", []) if r.status_code == 200 else []
            if not rows:
                print(f"  none returned (HTTP {r.status_code})")
            for row in rows:
                vals = {k: v for k, v in row.items()
                        if k != "__metadata" and v not in (None, "")}
                print(f"  {vals}")
        except Exception as exc:
            print(f"  error: {exc}")

        print("\n=== The one SPL-blocked BP (229, 'Basam Hasan') in full ===")
        m = c.fetch_business_partner("229")
        if m:
            print(f"  category={m.category!r} ({m.entity_type})")
            print(f"  first={m.first_name!r} last={m.last_name!r}")
            print(f"  dob={m.birth_date} nationality={m.nationality!r} "
                  f"birthplace={m.birthplace!r} birth_name={m.birth_name!r}")
            print(f"  founded={m.foundation_date} industry={m.industry!r} "
                  f"centrally_blocked={m.is_centrally_blocked}")
        else:
            print("  BP master not readable")

        print("\n=== SPLSY population (30 screened, 0 blocked) ===")
        try:
            r = c._client.get(
                f"{BASE}{SERVICE_PATH}/{ENTITY_SET}",
                params={"sap-client": "300", "$format": "json", "$top": "10",
                        "$filter": "LegalRegulation eq 'SPLSY'",
                        "$select": "BusinessPartner,BusinessPartnerName,Country,CityName,"
                                   "SPLScreeningCheckStatus_Text,SPLScreenedAddressIsBlocked"},
            )
            rows = r.json().get("d", {}).get("results", []) if r.status_code == 200 else []
            for row in rows:
                print(f"  BP {row.get('BusinessPartner'):>6} "
                      f"{str(row.get('BusinessPartnerName'))[:36]:38s} "
                      f"{row.get('Country'):3s} blocked={row.get('SPLScreenedAddressIsBlocked')} "
                      f"{row.get('SPLScreeningCheckStatus_Text')!r}")
        except Exception as exc:
            print(f"  error: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
