"""
Deterministic disposition band engine.

Maps an evidence ledger to a disposition band using non-compensatory logic.
The LLM never touches this — it is unit-testable, versionable, and produces
the same output for the same input every time.

Principles implemented (from §3.1):
  1. Null hypothesis is "this is a match." Burden runs toward exclusion.
  2. Evidence is categorical, not continuous.
  3. Missing data is neutral — can never clear a hit.
  4. Non-compensatory: one dispositive overrides any number of the opposite.
  5. Score the evidence count, not the average (thin files stay in Review).
  6. Match basis / name rarity is part of the score (handled by LLM at extraction).
  7. List severity sets the evidence bar.
  8. Network evidence counts (handled by LLM at extraction).
  9. Every score decomposes (the ledger IS the decomposition).
  10. Agent states what would change its mind (handled at rationale generation).
"""

from ..models.schemas import (
    DispositionBand,
    EvidenceLedger,
    ListSeverity,
)
from .rules import (
    has_dispositive_confirmation,
    has_strong_corroboration_on_blocking_list,
    qualifies_for_auto_clear,
    qualifies_for_propose_clear,
    is_thin_file,
)


def determine_band(
    ledger: EvidenceLedger,
    list_severity: ListSeverity,
    below_materiality: bool = False,
    precedent_exists: bool = False,
) -> DispositionBand:
    """
    Determine the disposition band from an evidence ledger.

    This is a pure function with no side effects. The same inputs always
    produce the same output.
    """

    # Rule 1: Dispositive confirmation → always escalate
    if has_dispositive_confirmation(ledger):
        return DispositionBand.ESCALATE

    # Rule 2: Strong corroboration on a blocking list → escalate
    if has_strong_corroboration_on_blocking_list(ledger, list_severity):
        return DispositionBand.ESCALATE

    # Rule 3: Dispositive exclusion + clean file + below materiality + precedent → auto-clear
    if qualifies_for_auto_clear(ledger, list_severity, below_materiality, precedent_exists):
        return DispositionBand.AUTO_CLEAR

    # Rule 4: Dispositive exclusion + clean file → propose clear
    if qualifies_for_propose_clear(ledger):
        return DispositionBand.PROPOSE_CLEAR

    # Rule 5: Thin file (too few data points) → review
    if is_thin_file(ledger):
        return DispositionBand.REVIEW

    # Default: insufficient evidence to clear → review
    return DispositionBand.REVIEW
