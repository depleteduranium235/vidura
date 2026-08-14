"""
Batch-run all mock cases through the real LLM pipeline and output
results as Fiori-compatible mock data JSON files.
"""

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.models import HitInput, IntakePath, ListSeverity
from core.bands import determine_band
from core.evidence.extractor import extract_evidence
from core.config import BAND_LOGIC_VERSION, TAXONOMY_VERSION, PROMPT_VERSION, MODEL_ID

CASES = [
    {
        "case_id": "ADJ-2026-000001",
        "bp_id": "0010000042",
        "bp_name": "Al-Rahman Trading LLC",
        "bp_country": "AE",
        "bp_entity_type": "Legal Entity",
        "bp_registration_no": "CR-12345-DXB",
        "spl_entry_id": "SDN-28451",
        "spl_entry_name": "AL-RAHMAN INTERNATIONAL",
        "spl_list_type": "SDN",
        "spl_programme": "IRAN",
        "spl_entity_type": "Individual",
        "match_percentage": 82.0,
        "match_basis": "Name token match (surname + partial given)",
        "intake_path": "BP Block",
        "list_severity": "BLOCKING",
        "extra": {"spl_remarks": "Individual associated with procurement network for Iranian entities. Known aliases include Al-Rahman, Al Rahman Int'l."},
    },
    {
        "case_id": "ADJ-2026-000002",
        "bp_id": "0010000108",
        "bp_name": "Schmidt & Weber GmbH",
        "bp_country": "DE",
        "bp_entity_type": "Legal Entity",
        "bp_registration_no": "HRB 54321",
        "spl_entry_id": "EU-9921",
        "spl_entry_name": "SCHMIDT, Hans-Peter",
        "spl_list_type": "EU Sanctions",
        "spl_programme": "RUSSIA",
        "spl_entity_type": "Individual",
        "match_percentage": 71.5,
        "match_basis": "Surname token match only",
        "intake_path": "BP Block",
        "list_severity": "BLOCKING",
        "extra": {"spl_dob": "1958-03-14", "spl_nationality": "Russian"},
    },
    {
        "case_id": "ADJ-2026-000003",
        "bp_id": "0010000215",
        "bp_name": "Petrochemical Supplies FZE",
        "bp_country": "AE",
        "bp_entity_type": "Legal Entity",
        "bp_registration_no": "SHJ-FZ-2019-4421",
        "spl_entry_id": "SDN-31002",
        "spl_entry_name": "PETRO SUPPLIES TRADING FZE",
        "spl_list_type": "SDN",
        "spl_programme": "IRAN",
        "spl_entity_type": "Legal Entity",
        "match_percentage": 91.3,
        "match_basis": "Near-exact name match with variant",
        "intake_path": "Doc Block",
        "list_severity": "BLOCKING",
        "extra": {
            "bp_address": "Al Wahda Building, Floor 3, Sharjah FZ",
            "spl_address": "Al Wahda Building, Floor 7, Sharjah FZ",
            "spl_remarks": "Front company for procurement of petrochemical products. Linked to IRGC-affiliated network.",
            "doc_ship_to": "IR (Bandar Abbas)",
            "doc_goods": "Petrochemical precursors",
            "doc_value": "USD 245,000",
        },
    },
    {
        "case_id": "ADJ-2026-000004",
        "bp_id": "0010000330",
        "bp_name": "Park Industries Co Ltd",
        "bp_country": "KR",
        "bp_entity_type": "Legal Entity",
        "bp_registration_no": "KR-2001-885412",
        "spl_entry_id": "DPRK-1104",
        "spl_entry_name": "PARK, Chol",
        "spl_list_type": "SDN",
        "spl_programme": "DPRK",
        "spl_entity_type": "Individual",
        "match_percentage": 65.0,
        "match_basis": "Surname token match (common Korean surname)",
        "intake_path": "BP Block",
        "list_severity": "BLOCKING",
        "extra": {
            "spl_dob": "1965-08-22",
            "spl_nationality": "DPRK (North Korean)",
            "spl_identifiers": "Passport: 563420187 (DPRK)",
        },
    },
    {
        "case_id": "ADJ-2026-000005",
        "bp_id": "0010000412",
        "bp_name": "Volga Shipping & Logistics",
        "bp_country": "TR",
        "bp_entity_type": "Legal Entity",
        "bp_registration_no": "TR-2023-IST-9912",
        "spl_entry_id": "EU-10544",
        "spl_entry_name": "VOLGA MARITIME SERVICES LLC",
        "spl_list_type": "EU Sanctions",
        "spl_programme": "RUSSIA",
        "spl_entity_type": "Legal Entity",
        "match_percentage": 78.2,
        "match_basis": "Distinctive name token match (Volga + shipping/maritime)",
        "intake_path": "Doc Block",
        "list_severity": "BLOCKING",
        "extra": {
            "bp_address": "Karakoy District, Istanbul",
            "spl_address": "Novorossiysk, Russia",
            "spl_remarks": "Maritime services company. Directors: Ivanov A.V., Petrov D.N. Vessels: MV Volga Star, MV Volga Express.",
            "doc_ship_to": "TR (Istanbul)",
            "doc_goods": "Ship spares and marine equipment",
            "doc_value": "EUR 89,000",
        },
    },
]


CRITICALITY_MAP = {
    "Escalate": 1,
    "Review": 2,
    "Propose Clear": 3,
    "Auto-clear": 5,
}

STATUS_MAP = {
    "Escalate": ("In Progress", 2),
    "Review": ("New", 0),
    "Propose Clear": ("Agent Complete", 0),
    "Auto-clear": ("Auto-cleared", 3),
}

PRIORITY_MAP = {
    "Escalate": ("Critical", 1),
    "Review": ("High", 1),
    "Propose Clear": ("Medium", 0),
    "Auto-clear": ("Low", 0),
}

CATEGORY_CRIT = {
    "DISP_EXCL": 5,
    "STRONG_DISC": 3,
    "WEAK_DISC": 0,
    "NEUTRAL": 0,
    "WEAK_CORR": 2,
    "STRONG_CORR": 1,
    "DISP_CONF": 1,
}

CATEGORY_DISPLAY = {
    "DISP_EXCL": "Dispositive Exclusion",
    "STRONG_DISC": "Strong Discriminator",
    "WEAK_DISC": "Weak Discriminator",
    "NEUTRAL": "Neutral",
    "WEAK_CORR": "Weak Corroborator",
    "STRONG_CORR": "Strong Corroborator",
    "DISP_CONF": "Dispositive Confirmation",
}


async def run_case(case_data: dict) -> dict:
    extra = case_data.pop("extra", {})
    hit = HitInput(**case_data)

    print(f"  Processing {hit.case_id} ({hit.bp_name})...", flush=True)
    if hit.spl_entry_address and "spl_address" not in extra:
        extra["spl_address"] = hit.spl_entry_address
    ledger, elapsed_ms = await extract_evidence(hit, **extra)

    band = determine_band(
        ledger=ledger,
        list_severity=hit.list_severity,
        below_materiality=hit.order_value < 100_000 if hasattr(hit, "order_value") else True,
        precedent_exists=False,
    )

    status_text, status_crit = STATUS_MAP.get(band.value, ("New", 0))
    priority_text, priority_crit = PRIORITY_MAP.get(band.value, ("Medium", 0))

    what_would_change = ""
    for item in ledger.items:
        if not item.data_available and "ownership" in item.assessment.lower():
            what_would_change = item.assessment
            break
    if not what_would_change:
        neutrals = [i for i in ledger.items if not i.data_available]
        if neutrals:
            what_would_change = f"Obtain: {', '.join(i.data_element for i in neutrals[:3])}"

    exclusions = [i for i in ledger.items if "EXCL" in i.category.value]
    corroborators = [i for i in ledger.items if "CORR" in i.category.value]
    discriminators = [i for i in ledger.items if "DISC" in i.category.value]
    summary_parts = []
    if exclusions:
        summary_parts.append(f"{len(exclusions)} exclusion(s)")
    if discriminators:
        summary_parts.append(f"{len(discriminators)} discriminator(s)")
    if corroborators:
        summary_parts.append(f"{len(corroborators)} corroborator(s)")
    neutrals_count = sum(1 for i in ledger.items if i.category.value == "NEUTRAL")
    if neutrals_count:
        summary_parts.append(f"{neutrals_count} neutral")

    rationale = " ".join(i.assessment for i in ledger.items if i.data_available)

    case_uuid = f"a1b2c3d4-e5f6-7890-abcd-ef123456780{case_data['case_id'][-1]}"

    fiori_case = {
        "CaseUUID": case_uuid,
        "CaseID": hit.case_id,
        "BusinessPartnerID": hit.bp_id,
        "BusinessPartnerName": hit.bp_name,
        "BPCountry": hit.bp_country,
        "BPCity": "",
        "BPEntityType": hit.bp_entity_type,
        "BPRegistrationNo": hit.bp_registration_no,
        "SPLEntryID": hit.spl_entry_id,
        "SPLEntryName": hit.spl_entry_name,
        "SPLListType": hit.spl_list_type,
        "SPLProgramme": hit.spl_programme,
        "SPLEntityType": hit.spl_entity_type,
        "MatchPercentage": hit.match_percentage,
        "MatchBasis": hit.match_basis,
        "ComparisonRule": "ZNAME_FUZZY_03",
        "IntakePath": hit.intake_path.value,
        "DispositionBand": band.value,
        "DispositionBandCriticality": CRITICALITY_MAP.get(band.value, 0),
        "Status": status_text,
        "StatusCriticality": status_crit,
        "Priority": priority_text,
        "PriorityCriticality": priority_crit,
        "AssignedTo": "senior.reviewer@example.com" if band.value == "Escalate" else "",
        "AgentRationale": rationale[:2000],
        "WhatWouldChangeMyMind": what_would_change[:500],
        "EvidenceSummary": ", ".join(summary_parts),
        "PrecedentExists": False,
        "PrecedentCaseID": "",
        "DocumentType": "Sales Order" if hit.intake_path == IntakePath.DOC_BLOCK else "",
        "DocumentNumber": "4500012847" if hit.intake_path == IntakePath.DOC_BLOCK else "",
        "OrderValue": float(extra.get("doc_value", "0").replace("USD ", "").replace("EUR ", "").replace(",", "")) if "doc_value" in extra else 0,
        "OrderCurrency": "USD" if "USD" in extra.get("doc_value", "") else ("EUR" if "EUR" in extra.get("doc_value", "") else ""),
        "ShipToCountry": extra.get("doc_ship_to", "").split("(")[0].strip() if "doc_ship_to" in extra else "",
        "EndUseCode": "",
        "ModelVersion": MODEL_ID,
        "PromptVersion": PROMPT_VERSION,
        "BandLogicVersion": BAND_LOGIC_VERSION,
        "TaxonomyVersion": TAXONOMY_VERSION,
        "ProcessingTimestamp": "2026-08-12T23:15:00Z",
        "ElapsedMilliseconds": elapsed_ms,
        "HumanDecision": "",
        "HumanUser": "",
        "HumanComment": "",
        "DecisionTimestamp": None,
        "CreatedAt": "2026-08-12T09:00:00Z",
        "ChangedAt": "2026-08-12T23:15:00Z",
        "AgedDays": 2,
    }

    evidence_items = []
    for idx, item in enumerate(ledger.items, 1):
        evidence_items.append({
            "EvidenceUUID": str(uuid.uuid4()),
            "CaseUUID": case_uuid,
            "Category": CATEGORY_DISPLAY.get(item.category.value, item.category.value),
            "CategoryCriticality": CATEGORY_CRIT.get(item.category.value, 0),
            "DataElement": item.data_element,
            "BPValue": item.bp_value,
            "SPLValue": item.spl_value,
            "Assessment": item.assessment,
            "DataAvailable": item.data_available,
            "SortOrder": idx,
        })

    return {"case": fiori_case, "evidence": evidence_items, "band": band.value, "elapsed_ms": elapsed_ms}


async def main():
    print("Vidura batch adjudication - running 5 cases through LLM...\n")

    all_cases = []
    all_evidence = []

    for case_data in CASES:
        result = await run_case(dict(case_data))
        all_cases.append(result["case"])
        all_evidence.extend(result["evidence"])
        print(f"    -> {result['band']} ({result['elapsed_ms']}ms, {len(result['evidence'])} evidence items)\n")

    output_dir = Path(__file__).parent.parent.parent / "webapp" / "localService" / "mockdata"

    with open(output_dir / "AdjudicationCase.json", "w") as f:
        json.dump(all_cases, f, indent=2)

    with open(output_dir / "EvidenceItem.json", "w") as f:
        json.dump(all_evidence, f, indent=2)

    print(f"\nDone! Wrote {len(all_cases)} cases and {len(all_evidence)} evidence items to webapp/localService/mockdata/")
    print("Restart the Fiori server to see the real agent output in the UI.")


if __name__ == "__main__":
    asyncio.run(main())
