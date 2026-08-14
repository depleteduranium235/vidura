"""
§11.2 Phase 1 backtest harness — prove the reasoning offline.

Reads screening records a human has already decided, runs each matched SPL entry
through the real pipeline (to_hit_inputs -> extract_evidence -> determine_band),
and compares the band against the reviewer's release reason.

Batch mode, no writeback: nothing here touches AGP beyond GETs, and the release
entities §5.4 reserves for a human are never addressed at all.

Four outcomes, kept separate on purpose:

  Agreement        the band is one the reviewer's verdict allows
  Abstention       the agent returned Review — it declined to take a position
  Divergence       the band contradicts the reviewer
  Safety violation a record the reviewer confirmed as a true match came back
                   Auto-clear or Propose Clear

Collapsing abstention into either agreement or divergence would misread the
whole run. With no SPL entry content and no BP discriminators (§4, §6a) the band
engine is *designed* to route to Review — missing data is neutral (§3.1 #3) and
neutral can never clear a hit — so a wall of Reviews is the engine working, not
the engine failing, and it is also not evidence that the reasoning is any good.

The safety violation is the only metric that can fail the run outright (§8).
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from enum import Enum
from typing import Awaitable, Callable, Iterable, Optional

from pydantic import BaseModel, Field

from ..bands import determine_band
from ..config import MATERIALITY_THRESHOLD_USD
from ..models.schemas import (
    DispositionBand,
    EvidenceLedger,
    HitInput,
    ListSeverity,
)
from ..sap.mapper import to_hit_inputs
from ..sap.schemas import ScreenedPartnerAddress
from .labels import (
    CLEARING_BANDS,
    HumanVerdict,
    classify_release_reason,
    expected_bands,
    is_scorable,
)


class Outcome(str, Enum):
    AGREEMENT = "Agreement"
    ABSTENTION = "Abstention (Review)"
    DIVERGENCE = "Divergence"
    SAFETY_VIOLATION = "Safety violation"
    ERROR = "Error"


class SkipReason(str, Enum):
    NO_HIT_DETAIL = "No SPL hit detail on the record — nothing to adjudicate"
    NO_REASON = "Decided but no release reason recorded — no label to score against"
    UNMAPPED_REASON = "Release reason not in the mapped vocabulary"
    OUT_OF_SCOPE = "Document-path release — outside the BP-path scope of this agent"


class CaseOutcome(BaseModel):
    """One BP-address <-> SPL-entry pair, adjudicated and compared."""

    case_id: str
    bp_id: str
    bp_name: str
    bp_country: str = ""
    legal_regulation: str = ""
    address_id: str = ""

    spl_entry_id: str = ""
    spl_entry_name: str = ""
    spl_entry_address: str = ""
    spl_list_type: str = ""
    spl_programme: str = ""
    match_basis: str = ""
    list_severity: ListSeverity = ListSeverity.BLOCKING

    release_reason_code: str = ""
    release_reason_text: str = ""
    human_verdict: HumanVerdict
    decided_by: str = ""
    expected_bands: list[DispositionBand] = Field(default_factory=list)

    actual_band: Optional[DispositionBand] = None
    outcome: Outcome
    error: str = ""

    ledger: EvidenceLedger = Field(default_factory=EvidenceLedger)
    rationale: str = ""
    elapsed_ms: int = 0

    @property
    def expected_label(self) -> str:
        return " or ".join(b.value for b in self.expected_bands) or "(unscored)"

    @property
    def category_counts(self) -> dict[str, int]:
        return dict(Counter(i.category.value for i in self.ledger.items))

    @property
    def evidence_available(self) -> int:
        return self.ledger.available_count


class SkippedRecord(BaseModel):
    """A decided record that could not be scored, and why."""

    record_key: str
    bp_id: str
    bp_name: str = ""
    legal_regulation: str = ""
    address_id: str = ""
    release_reason_text: str = ""
    human_verdict: HumanVerdict
    skip_reason: SkipReason


class BacktestReport(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = ""
    regulations: list[str] = Field(default_factory=list)

    # What the run actually did, so the report can't claim more than it earned:
    # a replay from a records file is not a live read, and the mock extractor is
    # not the evidence extractor.
    live_read: bool = False
    mock_extractor: bool = False

    model_version: str = ""
    prompt_version: str = ""
    band_logic_version: str = ""
    taxonomy_version: str = ""

    records_read: int = 0
    outcomes: list[CaseOutcome] = Field(default_factory=list)
    skipped: list[SkippedRecord] = Field(default_factory=list)

    # ------------------------------------------------------------------ counts

    @property
    def records_scored(self) -> int:
        """Decided records that produced at least one adjudicated pair."""
        return len({o.bp_id + "|" + o.address_id + "|" + o.legal_regulation
                    for o in self.outcomes})

    @property
    def pairs_adjudicated(self) -> int:
        return len(self.outcomes)

    @property
    def outcome_counts(self) -> dict[str, int]:
        counts = {o.value: 0 for o in Outcome}
        counts.update(Counter(o.outcome.value for o in self.outcomes))
        return counts

    @property
    def skip_counts(self) -> dict[str, int]:
        return dict(Counter(s.skip_reason.value for s in self.skipped))

    @property
    def band_counts(self) -> dict[str, int]:
        counts = {b.value: 0 for b in DispositionBand}
        counts.update(Counter(o.actual_band.value for o in self.outcomes if o.actual_band))
        return counts

    @property
    def safety_violations(self) -> list[CaseOutcome]:
        """
        The §8 gate: zero records labelled "matched to SPL record" may come back
        Auto-clear or Propose Clear. If one does, the band logic is broken and
        nothing else in this report matters.
        """
        return [o for o in self.outcomes if o.outcome == Outcome.SAFETY_VIOLATION]

    @property
    def safety_gate_passed(self) -> bool:
        return not self.safety_violations

    @property
    def true_positive_pairs(self) -> int:
        return sum(1 for o in self.outcomes if o.human_verdict == HumanVerdict.TRUE_POSITIVE)

    @property
    def divergences(self) -> list[CaseOutcome]:
        """Every case where the agent contradicted the reviewer, worst first."""
        return [
            o for o in self.outcomes
            if o.outcome in (Outcome.SAFETY_VIOLATION, Outcome.DIVERGENCE)
        ]

    @property
    def errors(self) -> list[CaseOutcome]:
        return [o for o in self.outcomes if o.outcome == Outcome.ERROR]

    # ------------------------------------------------------------------- rates

    @property
    def scorable_pairs(self) -> int:
        """Pairs with a usable label that actually produced a band."""
        return sum(1 for o in self.outcomes if o.outcome != Outcome.ERROR)

    @property
    def agreement_rate(self) -> Optional[float]:
        """
        Strict agreement: the band matched what the reviewer's verdict allows.
        None when nothing was scorable — an empty run has no rate, and printing
        0% or 100% for it would be a lie either way.
        """
        total = self.scorable_pairs
        if not total:
            return None
        return self.outcome_counts[Outcome.AGREEMENT.value] / total

    @property
    def non_contradiction_rate(self) -> Optional[float]:
        """Agreements plus abstentions — the agent did not contradict the human."""
        total = self.scorable_pairs
        if not total:
            return None
        agreed = self.outcome_counts[Outcome.AGREEMENT.value]
        abstained = self.outcome_counts[Outcome.ABSTENTION.value]
        return (agreed + abstained) / total

    # -------------------------------------------------------- confusion matrix

    def confusion_matrix(self) -> tuple[list[str], list[str], dict[tuple[str, str], int]]:
        """
        Expected verdict x actual band.

        Returns (row labels, column labels, cell counts) so the writers can
        render it without recomputing. Columns are every band, including ones
        with no hits, because a column of zeros is itself a finding — see
        Auto-clear in the caveats.
        """
        rows = [v.value for v in (HumanVerdict.CLEARED, HumanVerdict.TRUE_POSITIVE)]
        cols = [b.value for b in DispositionBand] + ["(error)"]
        cells: dict[tuple[str, str], int] = {(r, c): 0 for r in rows for c in cols}
        for o in self.outcomes:
            row = o.human_verdict.value
            if row not in rows:
                continue
            col = o.actual_band.value if o.actual_band else "(error)"
            cells[(row, col)] += 1
        return rows, cols, cells


# An adjudicator turns a hit into a ledger. Injected rather than imported so the
# harness is testable without an LLM or a network, and so a caller can swap the
# mock extractor in for a dry run.
Adjudicator = Callable[
    [HitInput, ScreenedPartnerAddress], Awaitable[tuple[EvidenceLedger, int]]
]


def classify_outcome(
    verdict: HumanVerdict,
    band: Optional[DispositionBand],
) -> Outcome:
    """
    Compare one band against one human verdict.

    Pure function, and the one place the four outcomes are defined. A safety
    violation is checked first and reported as its own outcome rather than as a
    divergence, so it can never be averaged away into an agreement rate.
    """
    if band is None:
        return Outcome.ERROR

    if verdict == HumanVerdict.TRUE_POSITIVE and band in CLEARING_BANDS:
        return Outcome.SAFETY_VIOLATION

    if band in expected_bands(verdict):
        return Outcome.AGREEMENT

    if band == DispositionBand.REVIEW:
        return Outcome.ABSTENTION

    return Outcome.DIVERGENCE


def skip_reason_for(
    record: ScreenedPartnerAddress,
    verdict: HumanVerdict,
) -> Optional[SkipReason]:
    """
    Why this decided record cannot be scored, or None if it can.

    Label problems are reported ahead of missing hit detail so the counts stay
    meaningful: a document-path release was never part of the BP-path population,
    and counting it under "no hit detail" would overstate how much of the real
    population is unrunnable.
    """
    if verdict == HumanVerdict.OUT_OF_SCOPE:
        return SkipReason.OUT_OF_SCOPE
    if verdict == HumanVerdict.NO_REASON:
        return SkipReason.NO_REASON
    if verdict == HumanVerdict.UNMAPPED:
        return SkipReason.UNMAPPED_REASON
    if not record.hits:
        return SkipReason.NO_HIT_DETAIL
    return None


def _skipped(record: ScreenedPartnerAddress, verdict: HumanVerdict,
            reason: SkipReason) -> SkippedRecord:
    return SkippedRecord(
        record_key=record.case_key,
        bp_id=record.business_partner,
        bp_name=record.partner_name or record.person_full_name,
        legal_regulation=record.legal_regulation,
        address_id=record.address_id,
        release_reason_text=record.release_reason_text or record.release_reason,
        human_verdict=verdict,
        skip_reason=reason,
    )


async def run_backtest(
    records: Iterable[ScreenedPartnerAddress],
    adjudicate: Adjudicator,
    *,
    source: str = "",
    regulations: Optional[list[str]] = None,
    model_version: str = "",
    prompt_version: str = "",
    band_logic_version: str = "",
    taxonomy_version: str = "",
    live_read: bool = False,
    mock_extractor: bool = False,
    progress: Optional[Callable[[str], None]] = None,
) -> BacktestReport:
    """
    Run the pipeline over decided records and score it against the humans.

    Sequential by design. `extract_evidence` is declared async but drives a
    synchronous Anthropic client, so gathering it would not actually overlap the
    calls — and the runnable population here is tiny (§6a). The polling
    orchestrator is where bounded concurrency belongs.
    """
    report = BacktestReport(
        source=source,
        regulations=regulations or [],
        model_version=model_version,
        prompt_version=prompt_version,
        band_logic_version=band_logic_version,
        taxonomy_version=taxonomy_version,
        live_read=live_read,
        mock_extractor=mock_extractor,
    )

    def note(message: str) -> None:
        if progress:
            progress(message)

    for record in records:
        report.records_read += 1
        verdict = classify_release_reason(record.release_reason_text, record.release_reason)

        skip = skip_reason_for(record, verdict)
        if skip is not None:
            report.skipped.append(_skipped(record, verdict, skip))
            note(f"  skip  BP {record.business_partner} — {skip.value}")
            continue

        for hit in to_hit_inputs(record):
            outcome = await _adjudicate_one(record, hit, verdict, adjudicate)
            report.outcomes.append(outcome)
            note(
                f"  {outcome.outcome.value:<18} BP {record.business_partner} "
                f"entry {hit.spl_entry_id} -> "
                f"{outcome.actual_band.value if outcome.actual_band else 'ERROR'} "
                f"(expected {outcome.expected_label})"
            )

    return report


async def _adjudicate_one(
    record: ScreenedPartnerAddress,
    hit: HitInput,
    verdict: HumanVerdict,
    adjudicate: Adjudicator,
) -> CaseOutcome:
    base = dict(
        case_id=hit.case_id,
        bp_id=hit.bp_id,
        bp_name=hit.bp_name,
        bp_country=hit.bp_country,
        legal_regulation=record.legal_regulation,
        address_id=record.address_id,
        spl_entry_id=hit.spl_entry_id,
        spl_entry_name=hit.spl_entry_name,
        spl_entry_address=hit.spl_entry_address,
        spl_list_type=hit.spl_list_type,
        spl_programme=hit.spl_programme,
        match_basis=hit.match_basis,
        list_severity=hit.list_severity,
        release_reason_code=record.release_reason,
        release_reason_text=record.release_reason_text or record.release_reason,
        human_verdict=verdict,
        decided_by=record.block_processor or record.changed_by,
        expected_bands=sorted(expected_bands(verdict), key=lambda b: b.value),
    )

    try:
        ledger, elapsed_ms = await adjudicate(hit, record)
    except Exception as exc:  # noqa: BLE001 - one bad case must not end the run
        # A failed extraction is recorded as an error, never as an agreement and
        # never as a clear. Silently dropping it would inflate the agreement rate.
        return CaseOutcome(
            **base,
            actual_band=None,
            outcome=Outcome.ERROR,
            error=f"{type(exc).__name__}: {exc}",
        )

    band = determine_band(
        ledger=ledger,
        list_severity=hit.list_severity,
        below_materiality=hit.order_value < MATERIALITY_THRESHOLD_USD,
        # The precedent store is an in-memory stub, so no run can produce a
        # precedent. Auto-clear is therefore unreachable here by construction —
        # stated in the report's caveats rather than hidden behind a default.
        precedent_exists=False,
    )

    return CaseOutcome(
        **base,
        actual_band=band,
        outcome=classify_outcome(verdict, band),
        ledger=ledger,
        rationale="; ".join(i.assessment for i in ledger.items if i.data_available),
        elapsed_ms=elapsed_ms,
    )
