"""
Result writer protocol and implementations.

The writer is the seam between the orchestrator and the result store.
Swapping from stub to production is a one-line change in run_orchestrator.py.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from ..models.schemas import AdjudicationResult

log = logging.getLogger(__name__)

DEFAULT_JSONL_PATH = Path(__file__).resolve().parent.parent.parent / "adjudication_results.jsonl"


@runtime_checkable
class ResultWriter(Protocol):
    def write(self, result: AdjudicationResult) -> None: ...
    def close(self) -> None: ...


class JsonlWriter:
    """
    Development stub: one JSON line per result, idempotent on case_id.

    Production will POST to the Z result table via OData V4, with the same
    write() signature — the orchestrator doesn't know or care which one it's
    talking to.
    """

    def __init__(self, path: Optional[Path] = None):
        self._path = path or DEFAULT_JSONL_PATH
        self._seen: set[str] = set()
        if self._path.exists():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        self._seen.add(json.loads(line)["case_id"])
                    except (json.JSONDecodeError, KeyError):
                        pass

    def write(self, result: AdjudicationResult) -> None:
        if result.case_id in self._seen:
            log.debug("Duplicate write for %s — idempotent skip", result.case_id)
            return
        self._seen.add(result.case_id)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(result.model_dump_json() + "\n")
        log.info("WROTE %s -> %s", result.case_id, result.disposition_band.value)

    def close(self) -> None:
        pass
