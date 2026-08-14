"""
GTS release-reason vocabulary -> human verdict -> the bands the agent may return.

§11.2 Phase 1 backtests the agent against decisions humans already made in GTS,
so the label is whatever release reason the reviewer picked. This module is the
label set.

The vocabulary observed in AGP 300 (§3a of the integration notes), counted over
the 91 decided records that carry a reason:

    False-Positive - Business Partner approved for release   40   cleared
    False-Positive: BP approved for release                  33   cleared
    Business partner matched to SPL record                   30   TRUE POSITIVE
    Transaction approved for release                         10   document path
    False Match                                               2   cleared

Two spellings of the same false-positive reason are already present, so matching
is normalised rather than literal.

Anything unrecognised comes back UNMAPPED and is excluded from scoring rather
than guessed into a verdict. That matters more than coverage: "approved for
release" on its own is not a clear — GTS also releases *confirmed* matches under
a licence — so a loose substring rule could label a true positive as cleared and
hide the one failure §8 says must never happen.
"""

from __future__ import annotations

import re
from enum import Enum

from ..models.schemas import DispositionBand


class HumanVerdict(str, Enum):
    """What the reviewer concluded, as far as the release reason reveals it."""

    CLEARED = "Cleared"
    TRUE_POSITIVE = "True positive"
    OUT_OF_SCOPE = "Out of scope (document path)"
    NO_REASON = "No release reason recorded"
    UNMAPPED = "Release reason not in the mapped vocabulary"


#: Bands the agent is allowed to return for a given verdict (§11.2 Phase 1).
#: An empty set means the verdict carries no usable label, so the case is
#: excluded from scoring instead of counted as a pass or a failure.
EXPECTED_BANDS: dict[HumanVerdict, frozenset[DispositionBand]] = {
    HumanVerdict.CLEARED: frozenset(
        {DispositionBand.AUTO_CLEAR, DispositionBand.PROPOSE_CLEAR}
    ),
    HumanVerdict.TRUE_POSITIVE: frozenset({DispositionBand.ESCALATE}),
    HumanVerdict.OUT_OF_SCOPE: frozenset(),
    HumanVerdict.NO_REASON: frozenset(),
    HumanVerdict.UNMAPPED: frozenset(),
}

#: Verdicts that carry a label the backtest can score against.
SCORABLE_VERDICTS = frozenset({HumanVerdict.CLEARED, HumanVerdict.TRUE_POSITIVE})

#: Bands that amount to proposing a release. A record the reviewer confirmed as
#: a true match may never land in one of these — that is the §8 metric.
CLEARING_BANDS = frozenset({DispositionBand.AUTO_CLEAR, DispositionBand.PROPOSE_CLEAR})

#: Exact vocabulary, keyed on the normalised form.
_VOCABULARY: dict[str, HumanVerdict] = {
    "false positive business partner approved for release": HumanVerdict.CLEARED,
    "false positive bp approved for release": HumanVerdict.CLEARED,
    "false match": HumanVerdict.CLEARED,
    "business partner matched to spl record": HumanVerdict.TRUE_POSITIVE,
    "transaction approved for release": HumanVerdict.OUT_OF_SCOPE,
}


def _normalise(text: str) -> str:
    """Casefold and reduce punctuation to single spaces, so ':' vs ' - ' agree."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def classify_release_reason(reason_text: str, reason_code: str = "") -> HumanVerdict:
    """
    Resolve a GTS release reason to a human verdict.

    Matches the exact vocabulary first, then a deliberately narrow set of
    tolerant patterns for spelling drift within reasons already known to exist.
    The true-positive pattern is tested first so no ordering accident can route
    a confirmed match into CLEARED.

    `reason_code` is accepted and searched alongside the text because the text is
    language-dependent (the service must be called with sap-language=EN), so a
    run against another logon language would otherwise lose every label.
    """
    text_key = _normalise(reason_text)
    if text_key in _VOCABULARY:
        return _VOCABULARY[text_key]

    code_key = _normalise(reason_code)
    if code_key in _VOCABULARY:
        return _VOCABULARY[code_key]

    if not text_key and not code_key:
        return HumanVerdict.NO_REASON

    haystack = f"{text_key} {code_key}"

    # Order is load-bearing: confirmed-match first.
    if "matched to spl" in haystack:
        return HumanVerdict.TRUE_POSITIVE
    if "transaction approved" in haystack:
        return HumanVerdict.OUT_OF_SCOPE
    if "false positive" in haystack or "false match" in haystack:
        return HumanVerdict.CLEARED

    return HumanVerdict.UNMAPPED


def expected_bands(verdict: HumanVerdict) -> frozenset[DispositionBand]:
    return EXPECTED_BANDS.get(verdict, frozenset())


def is_scorable(verdict: HumanVerdict) -> bool:
    return verdict in SCORABLE_VERDICTS
