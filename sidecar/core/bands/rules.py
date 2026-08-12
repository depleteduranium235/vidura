"""
Individual band logic rules.

Each rule is a pure predicate function. They implement the non-compensatory
logic from §3.1: one dispositive exclusion overrides any number of
corroborators, and vice versa.
"""

from ..models.schemas import EvidenceLedger, ListSeverity

THIN_FILE_THRESHOLD = 3


def has_dispositive_confirmation(ledger: EvidenceLedger) -> bool:
    """Identity established (matching registration number, passport, LEI)."""
    return len(ledger.dispositive_confirmations) > 0


def has_strong_corroboration_on_blocking_list(
    ledger: EvidenceLedger,
    list_severity: ListSeverity,
) -> bool:
    """
    Strong corroboration on a blocking/SDN list → escalate.

    §3.1 #7: List severity sets the evidence bar. An SDN hit requires
    more to clear; strong corroboration on SDN always escalates.
    """
    if list_severity != ListSeverity.BLOCKING:
        return False
    return len(ledger.strong_corroborators) > 0


def qualifies_for_auto_clear(
    ledger: EvidenceLedger,
    list_severity: ListSeverity,
    below_materiality: bool,
    precedent_exists: bool,
) -> bool:
    """
    Auto-clear conditions (§3.4):
    - Dispositive exclusion present
    - No corroborators (non-compensatory — §3.1 #4)
    - Non-blocking list OR below materiality threshold
    - Matching precedent exists
    """
    if not ledger.dispositive_exclusions:
        return False
    if ledger.all_corroborators:
        return False
    if list_severity == ListSeverity.BLOCKING and not below_materiality:
        return False
    if not precedent_exists:
        return False
    return True


def qualifies_for_propose_clear(ledger: EvidenceLedger) -> bool:
    """
    Propose-clear conditions:
    - Dispositive exclusion present
    - No corroborating evidence (non-compensatory)

    Note: does NOT require precedent or materiality check — those are
    the additional gates for auto-clear.
    """
    if not ledger.dispositive_exclusions:
        return False
    if ledger.all_corroborators:
        return False
    return True


def is_thin_file(ledger: EvidenceLedger) -> bool:
    """
    §3.1 #5: Score the evidence count, not the average.
    A strong name match plus one mismatch and nothing else is a thin file.
    """
    return ledger.available_count < THIN_FILE_THRESHOLD
