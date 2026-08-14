"""§11.2 Phase 1 backtest — prove the reasoning offline, no writeback."""

from .harness import (
    BacktestReport,
    CaseOutcome,
    Outcome,
    SkipReason,
    SkippedRecord,
    classify_outcome,
    run_backtest,
    skip_reason_for,
)
from .labels import (
    CLEARING_BANDS,
    EXPECTED_BANDS,
    SCORABLE_VERDICTS,
    HumanVerdict,
    classify_release_reason,
    expected_bands,
    is_scorable,
)
from .report import (
    STATIC_CAVEATS,
    dynamic_caveats,
    render_markdown,
    write_all,
    write_csvs,
    write_workbook,
)

__all__ = [
    "BacktestReport",
    "CaseOutcome",
    "Outcome",
    "SkipReason",
    "SkippedRecord",
    "classify_outcome",
    "run_backtest",
    "skip_reason_for",
    "CLEARING_BANDS",
    "EXPECTED_BANDS",
    "SCORABLE_VERDICTS",
    "HumanVerdict",
    "classify_release_reason",
    "expected_bands",
    "is_scorable",
    "STATIC_CAVEATS",
    "dynamic_caveats",
    "render_markdown",
    "write_all",
    "write_csvs",
    "write_workbook",
]
