"""
Band → GTS release-reason mapping.

When a reviewer confirms an agent recommendation in the Fiori app, the action
that hot-wires to Manage Blocked Partners (F4542A) needs to pass the correct
release-reason code and text. This module maps disposition bands to the
vocabulary GTS already uses, so the release is documented in terms the existing
process recognises.

The codes come from §3a of the integration notes — the vocabulary observed in
AGP 300 across 91 decided records with release reasons:

    Code   Text                                                    Band
    ----   ----                                                    ----
    Z01    False-Positive - Business Partner approved for release  Propose Clear / Auto-clear
    Z02    False Match                                             Propose Clear / Auto-clear
    Z03    Business partner matched to SPL record                  Escalate (confirmed match)
    Z04    Transaction approved for release                        (document path — Phase 3)

The Z table (backend/ddl/) stores both the band and the release reason, so the
Fiori app can display either vocabulary in the worklist.
"""

from __future__ import annotations

from .models.schemas import DispositionBand


BAND_TO_RELEASE_REASON: dict[DispositionBand, tuple[str, str]] = {
    DispositionBand.AUTO_CLEAR: (
        "Z01",
        "False-Positive - Business Partner approved for release",
    ),
    DispositionBand.PROPOSE_CLEAR: (
        "Z01",
        "False-Positive - Business Partner approved for release",
    ),
    DispositionBand.ESCALATE: (
        "Z03",
        "Business partner matched to SPL record",
    ),
    # Review has no release reason — the reviewer decides what it is.
    DispositionBand.REVIEW: ("", ""),
}


def release_reason_for(band: DispositionBand) -> tuple[str, str]:
    """
    Return (code, text) for the release reason matching this band.

    For Review the tuple is empty strings: the reviewer will pick the reason
    themselves, since Review means the agent declined to take a position.
    """
    return BAND_TO_RELEASE_REASON.get(band, ("", ""))


def release_reason_code(band: DispositionBand) -> str:
    return release_reason_for(band)[0]


def release_reason_text(band: DispositionBand) -> str:
    return release_reason_for(band)[1]
