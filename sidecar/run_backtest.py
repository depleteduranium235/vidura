"""
§11.2 Phase 1 backtest — CLI.

Reads screening records a human already decided out of AGP 300, runs each matched
SPL entry through the real pipeline, compares the band against the reviewer's
release reason, and writes a spreadsheet plus a credibility argument.

READ ONLY — GETs only. Nothing is written back to AGP, and the release entities
§5.4 reserves for a human are never addressed.

Usage
-----
    export PYTHONIOENCODING=utf-8

    # Live read from AGP + real LLM. Password on stdin (getpass ignores a pipe
    # on Windows, so every script here reads stdin instead).
    printf '%s\\n' 'PASSWORD' | python run_backtest.py

    # Live read, no LLM spend — proves the read/map/band path only.
    printf '%s\\n' 'PASSWORD' | python run_backtest.py --mock

    # Fetch once, then iterate on the taxonomy offline against the same records.
    printf '%s\\n' 'PASSWORD' | python run_backtest.py --dump-records records.json
    python run_backtest.py --from-records records.json

Exits non-zero if the §8 safety gate fails, so it can gate a build.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.backtest import run_backtest, write_all
from core.config import (
    BAND_LOGIC_VERSION,
    MODEL_ID,
    PROMPT_VERSION,
    TAXONOMY_VERSION,
)
from core.evidence.extractor import extract_evidence, extract_evidence_mock
from core.models.schemas import EvidenceLedger, HitInput
from core.sap.odata import SPL_LEGAL_REGULATIONS, GtsODataClient, ODataError
from core.sap.schemas import ScreenedPartnerAddress

BASE = "https://agpapp.sap.pwc.com:8001"
USER = "jtyagi002"
DEFAULT_OUT = Path(__file__).resolve().parent / "backtest_out"


def build_adjudicator(use_mock: bool):
    """
    The evidence extractor, wired for the BP path.

    Passes the SPL entry's address through explicitly: `extract_evidence` takes
    it as a keyword argument and does not read `HitInput.spl_entry_address`, so
    without this the one piece of genuine SPL entry content AGP gives us (§3a)
    would never reach the model.
    """

    async def adjudicate(
        hit: HitInput, record: ScreenedPartnerAddress
    ) -> tuple[EvidenceLedger, int]:
        if use_mock:
            return extract_evidence_mock(hit), 0
        return await extract_evidence(
            hit,
            spl_address=hit.spl_entry_address,
            bp_address=record.full_address,
        )

    return adjudicate


def read_password() -> str:
    pw = sys.stdin.readline().rstrip("\r\n")
    if not pw:
        raise SystemExit(
            "No password on stdin. AGP 300 needs one and it is not stored in this "
            "repo:\n  printf '%s\\n' 'PASSWORD' | python run_backtest.py"
        )
    return pw


def load_records(path: Path) -> list[ScreenedPartnerAddress]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [ScreenedPartnerAddress.model_validate(row) for row in payload]


def dump_records(records: list[ScreenedPartnerAddress], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([r.model_dump(mode="json") for r in records],
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def fetch_records(
    password: str, regs: tuple[str, ...] | None, limit: int | None
) -> list[ScreenedPartnerAddress]:
    """
    Pull the decided population.

    Unfiltered on the blocked flag on purpose — releasing a record clears
    SPLScreenedAddressIsBlocked, so every record a reviewer *cleared* has already
    left the blocked-only feed. Filtering on it would return only confirmed
    matches and bias the whole backtest.

    refetch_empty_hits=True: GTS's collection $expand does not always populate
    hits; a keyed re-read reliably does. Without this, records that have hits
    show up as "no hit detail" and get skipped.
    """
    records: list[ScreenedPartnerAddress] = []
    with GtsODataClient(BASE, USER, password, sap_client="300") as client:
        for record in client.iter_decided_addresses(
            legal_regulations=regs, refetch_empty_hits=True
        ):
            records.append(record)
            if limit and len(records) >= limit:
                break
    return records


async def main_async(args: argparse.Namespace) -> int:
    regs = None if args.all_regs else tuple(
        r.strip().upper() for r in args.regs.split(",") if r.strip()
    )

    if args.from_records:
        source = f"records file {args.from_records}"
        print(f"Loading decided records from {args.from_records} ...")
        records = load_records(Path(args.from_records))
        if regs:
            records = [r for r in records if r.legal_regulation in regs]
        if args.limit:
            records = records[: args.limit]
    else:
        password = read_password()
        source = f"AGP 300 live ({BASE})"
        print(f"Reading decided records from AGP 300 "
              f"(regs: {', '.join(regs) if regs else 'all'}) ...")
        try:
            records = fetch_records(password, regs, args.limit)
        except ODataError as exc:
            print(f"\nRead failed: {exc}")
            return 1

    print(f"  {len(records)} decided record(s)\n")

    if args.dump_records:
        dump_records(records, Path(args.dump_records))
        print(f"  raw records saved to {args.dump_records}\n")

    print(f"Adjudicating ({'mock extractor' if args.mock else MODEL_ID}) ...")
    report = await run_backtest(
        records,
        build_adjudicator(args.mock),
        source=source + (" [mock extractor]" if args.mock else ""),
        regulations=list(regs) if regs else [],
        model_version="mock" if args.mock else MODEL_ID,
        prompt_version=PROMPT_VERSION,
        band_logic_version=BAND_LOGIC_VERSION,
        taxonomy_version=TAXONOMY_VERSION,
        live_read=not args.from_records,
        mock_extractor=args.mock,
        progress=print,
    )

    out_dir = Path(args.out)
    written = write_all(report, out_dir)

    rate = report.agreement_rate
    print("\n" + "=" * 72)
    print(f"  Records read      : {report.records_read}")
    print(f"  Records skipped   : {len(report.skipped)}")
    print(f"  Pairs adjudicated : {report.pairs_adjudicated}")
    print(f"  Agreement rate    : {'n/a' if rate is None else f'{rate:.1%}'}")
    for name, n in report.outcome_counts.items():
        if n:
            print(f"    {name:<20} {n}")
    print("-" * 72)
    if report.safety_gate_passed:
        print(f"  SAFETY GATE (§8)  : PASS "
              f"({report.true_positive_pairs} confirmed-match pair(s) checked)")
        if not report.true_positive_pairs:
            print("                      not violated, but never exercised — no "
                  "confirmed-match\n                      record reached the pipeline")
    else:
        print(f"  SAFETY GATE (§8)  : *** FAIL *** "
              f"{len(report.safety_violations)} confirmed match(es) proposed for clearing")
    print("=" * 72)
    print("\nWritten:")
    for path in written:
        print(f"  {path}")
    print("\nRead backtest.md first — the caveats bound what this run can show.")

    return 0 if report.safety_gate_passed else 1


def main() -> int:
    # GTS returns CJK and German text; without this a plain print dies with a
    # cp1252 UnicodeEncodeError on Windows.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="§11.2 Phase 1 backtest against human decisions in GTS (read-only)."
    )
    parser.add_argument("--mock", action="store_true",
                        help="use the mock extractor — no LLM calls, no API key")
    parser.add_argument("--regs", default=",".join(SPL_LEGAL_REGULATIONS),
                        help="legal regulations to read (default: %(default)s)")
    parser.add_argument("--all-regs", action="store_true",
                        help="read every regulation, not just SPL screening ones")
    parser.add_argument("--limit", type=int, default=None,
                        help="stop after N decided records")
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help="output directory (default: %(default)s)")
    parser.add_argument("--dump-records", default=None,
                        help="save the fetched records to JSON for offline reruns")
    parser.add_argument("--from-records", default=None,
                        help="replay records from JSON instead of reading AGP "
                             "(no password needed)")
    args = parser.parse_args()

    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
