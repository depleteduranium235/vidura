"""
Persistent state for the polling orchestrator.

Two pieces of state:
  1. Watermark (datetime) — only read records newer than this
  2. Processed IDs (set) — skip records we've already adjudicated

Both persist to a human-readable JSON file. The file is atomically
overwritten on each save (write to .tmp, then os.replace) so a crash
mid-save never corrupts it.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import UUID

DEFAULT_STATE_PATH = Path(__file__).resolve().parent.parent.parent / "orchestrator_state.json"


class OrchestratorState:
    def __init__(self, path: Optional[Path] = None):
        self._path = path or DEFAULT_STATE_PATH
        self.watermark: Optional[datetime] = None
        self.processed_ids: set[str] = set()
        self.last_sweep_at: Optional[datetime] = None
        self.total_adjudicated: int = 0

    def load(self) -> None:
        if not self._path.exists():
            return
        data = json.loads(self._path.read_text(encoding="utf-8"))
        wm = data.get("watermark")
        if wm:
            self.watermark = datetime.fromisoformat(wm)
        self.processed_ids = set(data.get("processed_ids", []))
        lsa = data.get("last_sweep_at")
        if lsa:
            self.last_sweep_at = datetime.fromisoformat(lsa)
        self.total_adjudicated = data.get("total_adjudicated", 0)

    def save(self) -> None:
        data = {
            "watermark": self.watermark.isoformat() if self.watermark else None,
            "processed_ids": sorted(self.processed_ids),
            "last_sweep_at": self.last_sweep_at.isoformat() if self.last_sweep_at else None,
            "total_adjudicated": self.total_adjudicated,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)

    def is_processed(self, record_id: str) -> bool:
        return record_id in self.processed_ids

    def mark_processed(self, record_id: str) -> None:
        self.processed_ids.add(record_id)

    def advance_watermark(self, new_watermark: datetime) -> None:
        if self.watermark is None or new_watermark > self.watermark:
            self.watermark = new_watermark

    def record_identity(self, record) -> str:
        """
        Stable identity for dedup. Prefers SPLAuditTrailUUID; falls back to
        case_key (the composite of the record's key fields).
        """
        if record.spl_audit_trail_uuid:
            return str(record.spl_audit_trail_uuid)
        return record.case_key

    def prune(self, keep_last_n: int = 5000) -> int:
        """
        Prune the oldest processed IDs beyond keep_last_n.

        In practice AGP has hundreds of blocked addresses, not millions, so
        this is a safety net rather than a regular need. Returns the number
        removed.
        """
        if len(self.processed_ids) <= keep_last_n:
            return 0
        excess = len(self.processed_ids) - keep_last_n
        to_drop = sorted(self.processed_ids)[:excess]
        self.processed_ids -= set(to_drop)
        return excess
