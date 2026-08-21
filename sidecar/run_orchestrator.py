"""
§5.6 Production orchestrator — CLI entry point.

Long-running process: polls GTS for new screening blocks, adjudicates each
BP-address ↔ SPL-entry pair, writes results.

Usage
-----
    export PYTHONIOENCODING=utf-8

    # Live read + real LLM:
    printf '%s\\n' 'PASSWORD' | python run_orchestrator.py

    # Live read, no LLM spend (proves the read/filter/watermark path):
    printf '%s\\n' 'PASSWORD' | python run_orchestrator.py --mock

    # Faster sweeps for development:
    printf '%s\\n' 'PASSWORD' | python run_orchestrator.py --mock --interval 10

Ctrl+C stops gracefully after the current sweep finishes.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.config import MODEL_ID
from core.orchestrator import (
    JsonlWriter,
    KillSwitch,
    Orchestrator,
    OrchestratorState,
)
from core.orchestrator.writer import ZTableWriter
from core.sap.odata import GtsODataClient

BASE = "https://agpapp.sap.pwc.com:8001"
USER = "jtyagi002"


def read_password() -> str:
    pw = sys.stdin.readline().rstrip("\r\n")
    if not pw:
        raise SystemExit(
            "No password on stdin. AGP 300 needs one:\n"
            "  printf '%s\\n' 'PASSWORD' | python run_orchestrator.py"
        )
    return pw


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="§5.6 SPL adjudication orchestrator (long-running)."
    )
    parser.add_argument("--mock", action="store_true",
                        help="use mock extractor — no LLM calls")
    parser.add_argument("--interval", type=int, default=60,
                        help="seconds between sweeps (default: %(default)s)")
    parser.add_argument("--concurrency", type=int, default=5,
                        help="max concurrent LLM calls (default: %(default)s)")
    parser.add_argument("--state-file", default=None,
                        help="state file path (default: sidecar/orchestrator_state.json)")
    parser.add_argument("--output", default=None,
                        help="JSONL output file (default: sidecar/adjudication_results.jsonl)")
    parser.add_argument("--write-to-sap", action="store_true",
                        help="write results to the Z table via OData V4 RAP service (instead of JSONL)")
    parser.add_argument("--error-threshold", type=float, default=0.30,
                        help="kill-switch error rate threshold (default: %(default)s)")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    password = read_password()

    state = OrchestratorState(Path(args.state_file) if args.state_file else None)

    if args.write_to_sap:
        writer = ZTableWriter(BASE, USER, password, sap_client="300")
    else:
        writer = JsonlWriter(Path(args.output) if args.output else None)

    kill_switch = KillSwitch(error_threshold=args.error_threshold)

    client = GtsODataClient(BASE, USER, password, sap_client="300")

    orchestrator = Orchestrator(
        client=client,
        writer=writer,
        state=state,
        concurrency=args.concurrency,
        sweep_interval_s=args.interval,
        kill_switch=kill_switch,
        use_mock=args.mock,
    )

    log = logging.getLogger(__name__)
    log.info(
        "Starting: model=%s interval=%ds concurrency=%d",
        "mock" if args.mock else MODEL_ID,
        args.interval,
        args.concurrency,
    )

    try:
        asyncio.run(orchestrator.run())
    except KeyboardInterrupt:
        log.info("Interrupted.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
