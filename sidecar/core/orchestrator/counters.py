"""
Coverage counters and kill-switch logic.

§8: hits in must equal decisions out.
§5.6 #6: if things go wrong, stop rather than produce bad results.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


class KillSwitchTripped(RuntimeError):
    pass


@dataclass
class SweepCounters:
    records_read: int = 0
    records_skipped_no_hits: int = 0
    records_skipped_already_processed: int = 0
    records_skipped_wrong_regulation: int = 0
    pairs_adjudicated: int = 0
    pairs_written: int = 0
    errors: int = 0
    elapsed_ms: int = 0

    @property
    def coverage_delta(self) -> int:
        return self.pairs_adjudicated - self.pairs_written

    def log_summary(self, watermark: str) -> None:
        skipped = (self.records_skipped_no_hits +
                   self.records_skipped_already_processed +
                   self.records_skipped_wrong_regulation)
        log.info(
            "SWEEP  read=%d skipped=%d (no_hits=%d dedup=%d wrong_reg=%d) "
            "pairs=%d written=%d errors=%d delta=%d elapsed=%dms watermark=%s",
            self.records_read, skipped,
            self.records_skipped_no_hits,
            self.records_skipped_already_processed,
            self.records_skipped_wrong_regulation,
            self.pairs_adjudicated, self.pairs_written, self.errors,
            self.coverage_delta, self.elapsed_ms, watermark,
        )
        if self.coverage_delta != 0:
            log.warning(
                "COVERAGE MISMATCH: %d pair(s) adjudicated but not written "
                "(errors account for %d)", self.coverage_delta, self.errors,
            )


@dataclass
class KillSwitch:
    error_threshold: float = 0.30
    window_size: int = 20
    max_consecutive_read_failures: int = 3

    _results: list[bool] = field(default_factory=list)
    _consecutive_read_failures: int = 0

    def record_success(self) -> None:
        self._results.append(True)
        self._trim()
        self._consecutive_read_failures = 0

    def record_error(self) -> None:
        self._results.append(False)
        self._trim()

    def record_read_failure(self) -> None:
        self._consecutive_read_failures += 1

    def record_read_success(self) -> None:
        self._consecutive_read_failures = 0

    def check(self) -> None:
        if self._consecutive_read_failures >= self.max_consecutive_read_failures:
            raise KillSwitchTripped(
                f"{self._consecutive_read_failures} consecutive OData read failures"
            )
        if len(self._results) >= self.window_size:
            rate = self.error_rate
            if rate > self.error_threshold:
                raise KillSwitchTripped(
                    f"Error rate {rate:.0%} exceeds threshold "
                    f"{self.error_threshold:.0%} over last {len(self._results)} adjudications"
                )

    @property
    def error_rate(self) -> float:
        if not self._results:
            return 0.0
        return self._results.count(False) / len(self._results)

    def _trim(self) -> None:
        if len(self._results) > self.window_size:
            self._results = self._results[-self.window_size:]
