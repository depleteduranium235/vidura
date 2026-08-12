"""
CLI entry point for running an adjudication.

Usage:
  python run_adjudication.py '{"case_id": "...", "bp_name": "...", ...}'
  python run_adjudication.py --mock  (uses built-in test case, no API key needed)

When called from the Node.js API bridge, receives JSON on argv[1]
and prints the result as JSON to stdout.
"""

import asyncio
import json
import sys
import time
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.models import (
    HitInput,
    DispositionBand,
    ListSeverity,
    IntakePath,
    AdjudicationResult,
)
from core.bands import determine_band
from core.evidence.extractor import extract_evidence, extract_evidence_mock
from core.config import BAND_LOGIC_VERSION, TAXONOMY_VERSION, PROMPT_VERSION, MODEL_ID


MOCK_HIT = HitInput(
    case_id="ADJ-TEST-001",
    bp_id="0010000108",
    bp_name="Schmidt & Weber GmbH",
    bp_country="DE",
    bp_entity_type="Legal Entity",
    bp_registration_no="HRB 54321",
    spl_entry_id="EU-9921",
    spl_entry_name="SCHMIDT, Hans-Peter",
    spl_list_type="EU Sanctions",
    spl_programme="RUSSIA",
    spl_entity_type="Individual",
    match_percentage=71.5,
    match_basis="Surname token match only",
    intake_path=IntakePath.BP_BLOCK,
    list_severity=ListSeverity.BLOCKING,
)


async def run(hit: HitInput, use_mock: bool = False) -> AdjudicationResult:
    start = time.perf_counter_ns()

    if use_mock:
        ledger = extract_evidence_mock(hit)
        elapsed_ms = 0
    else:
        ledger, elapsed_ms = await extract_evidence(
            hit,
            spl_dob="1958-03-14",
            spl_nationality="Russian",
        )

    band = determine_band(
        ledger=ledger,
        list_severity=hit.list_severity,
        below_materiality=hit.order_value < 100_000,
        precedent_exists=False,
    )

    total_ms = (time.perf_counter_ns() - start) // 1_000_000

    exclusions = [i for i in ledger.items if "EXCL" in i.category.value]
    corroborators = [i for i in ledger.items if "CORR" in i.category.value]
    summary_parts = []
    if exclusions:
        summary_parts.append(f"{len(exclusions)} exclusion(s)")
    if corroborators:
        summary_parts.append(f"{len(corroborators)} corroborator(s)")
    summary_parts.append(f"{len(ledger.items)} total items")

    return AdjudicationResult(
        case_id=hit.case_id,
        disposition_band=band,
        rationale="; ".join(i.assessment for i in ledger.items if i.data_available),
        what_would_change="Requires manual review" if band == DispositionBand.REVIEW else "",
        evidence_summary=", ".join(summary_parts),
        evidence_ledger=ledger,
        model_version=MODEL_ID if not use_mock else "mock",
        prompt_version=PROMPT_VERSION,
        band_logic_version=BAND_LOGIC_VERSION,
        taxonomy_version=TAXONOMY_VERSION,
        elapsed_ms=total_ms,
    )


def main():
    use_mock = "--mock" in sys.argv

    if use_mock:
        hit = MOCK_HIT
    elif len(sys.argv) > 1 and sys.argv[1] != "--mock":
        hit = HitInput(**json.loads(sys.argv[1]))
    else:
        hit = MOCK_HIT
        use_mock = True

    result = asyncio.run(run(hit, use_mock=use_mock))
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
