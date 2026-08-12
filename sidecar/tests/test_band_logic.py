"""
Unit tests for the deterministic band logic engine.

These tests verify the 10 principles from §3.1. If these pass,
the LLM layer can be iterated freely — the decision rules are stable.
"""

import pytest
from core.models.schemas import (
    EvidenceCategory,
    EvidenceItem,
    EvidenceLedger,
    DispositionBand,
    ListSeverity,
)
from core.bands.engine import determine_band


def make_item(category: EvidenceCategory, available: bool = True) -> EvidenceItem:
    return EvidenceItem(
        category=category,
        data_element="test",
        bp_value="bp",
        spl_value="spl",
        assessment="test assessment",
        data_available=available,
    )


class TestDispositiveConfirmation:
    """§3.1 #4: Dispositive confirmation always escalates, regardless of other evidence."""

    def test_confirmation_alone_escalates(self):
        ledger = EvidenceLedger(items=[
            make_item(EvidenceCategory.DISPOSITIVE_CONFIRMATION),
        ])
        assert determine_band(ledger, ListSeverity.BLOCKING) == DispositionBand.ESCALATE

    def test_confirmation_with_exclusions_still_escalates(self):
        """Non-compensatory: exclusions cannot override a confirmation."""
        ledger = EvidenceLedger(items=[
            make_item(EvidenceCategory.DISPOSITIVE_CONFIRMATION),
            make_item(EvidenceCategory.DISPOSITIVE_EXCLUSION),
            make_item(EvidenceCategory.STRONG_DISCRIMINATOR),
        ])
        assert determine_band(ledger, ListSeverity.BLOCKING) == DispositionBand.ESCALATE


class TestStrongCorroborationBlocking:
    """§3.1 #7: SDN/blocking list + strong corroboration → escalate."""

    def test_strong_corroboration_on_sdn_escalates(self):
        ledger = EvidenceLedger(items=[
            make_item(EvidenceCategory.STRONG_CORROBORATOR),
            make_item(EvidenceCategory.NEUTRAL),
            make_item(EvidenceCategory.NEUTRAL),
        ])
        assert determine_band(ledger, ListSeverity.BLOCKING) == DispositionBand.ESCALATE

    def test_strong_corroboration_on_advisory_does_not_escalate(self):
        """Advisory list has lower evidence bar — strong corroboration alone doesn't escalate."""
        ledger = EvidenceLedger(items=[
            make_item(EvidenceCategory.STRONG_CORROBORATOR),
            make_item(EvidenceCategory.NEUTRAL),
            make_item(EvidenceCategory.NEUTRAL),
        ])
        assert determine_band(ledger, ListSeverity.ADVISORY) != DispositionBand.ESCALATE


class TestAutoClear:
    """§3.4: Auto-clear requires dispositive exclusion + no corroborators + below materiality + precedent."""

    def test_full_auto_clear_conditions(self):
        ledger = EvidenceLedger(items=[
            make_item(EvidenceCategory.DISPOSITIVE_EXCLUSION),
            make_item(EvidenceCategory.STRONG_DISCRIMINATOR),
            make_item(EvidenceCategory.NEUTRAL),
        ])
        result = determine_band(
            ledger,
            ListSeverity.BLOCKING,
            below_materiality=True,
            precedent_exists=True,
        )
        assert result == DispositionBand.AUTO_CLEAR

    def test_no_auto_clear_without_precedent(self):
        ledger = EvidenceLedger(items=[
            make_item(EvidenceCategory.DISPOSITIVE_EXCLUSION),
            make_item(EvidenceCategory.STRONG_DISCRIMINATOR),
            make_item(EvidenceCategory.NEUTRAL),
        ])
        result = determine_band(
            ledger,
            ListSeverity.BLOCKING,
            below_materiality=True,
            precedent_exists=False,
        )
        assert result == DispositionBand.PROPOSE_CLEAR

    def test_no_auto_clear_above_materiality_on_blocking(self):
        ledger = EvidenceLedger(items=[
            make_item(EvidenceCategory.DISPOSITIVE_EXCLUSION),
            make_item(EvidenceCategory.STRONG_DISCRIMINATOR),
            make_item(EvidenceCategory.NEUTRAL),
        ])
        result = determine_band(
            ledger,
            ListSeverity.BLOCKING,
            below_materiality=False,
            precedent_exists=True,
        )
        assert result == DispositionBand.PROPOSE_CLEAR

    def test_auto_clear_on_advisory_above_materiality(self):
        """Advisory list doesn't require materiality check for auto-clear."""
        ledger = EvidenceLedger(items=[
            make_item(EvidenceCategory.DISPOSITIVE_EXCLUSION),
            make_item(EvidenceCategory.STRONG_DISCRIMINATOR),
            make_item(EvidenceCategory.NEUTRAL),
        ])
        result = determine_band(
            ledger,
            ListSeverity.ADVISORY,
            below_materiality=False,
            precedent_exists=True,
        )
        assert result == DispositionBand.AUTO_CLEAR


class TestProposeClear:
    """§3.4: Propose clear when exclusion exists but conditions for auto aren't met."""

    def test_exclusion_without_corroborators(self):
        ledger = EvidenceLedger(items=[
            make_item(EvidenceCategory.DISPOSITIVE_EXCLUSION),
            make_item(EvidenceCategory.STRONG_DISCRIMINATOR),
            make_item(EvidenceCategory.NEUTRAL),
        ])
        assert determine_band(ledger, ListSeverity.WATCH) == DispositionBand.PROPOSE_CLEAR

    def test_exclusion_with_corroborator_goes_to_review(self):
        """§3.1 #4: Non-compensatory — corroborator prevents clearing even with exclusion."""
        ledger = EvidenceLedger(items=[
            make_item(EvidenceCategory.DISPOSITIVE_EXCLUSION),
            make_item(EvidenceCategory.STRONG_CORROBORATOR),
            make_item(EvidenceCategory.NEUTRAL),
        ])
        result = determine_band(ledger, ListSeverity.ADVISORY)
        assert result == DispositionBand.REVIEW


class TestThinFile:
    """§3.1 #5: Score the evidence count. Thin files cannot clear."""

    def test_two_items_is_thin(self):
        ledger = EvidenceLedger(items=[
            make_item(EvidenceCategory.STRONG_DISCRIMINATOR),
            make_item(EvidenceCategory.NEUTRAL),
        ])
        assert determine_band(ledger, ListSeverity.ADVISORY) == DispositionBand.REVIEW

    def test_unavailable_data_does_not_count(self):
        """§3.1 #3: Missing data is neutral — it can never clear."""
        ledger = EvidenceLedger(items=[
            make_item(EvidenceCategory.STRONG_DISCRIMINATOR, available=True),
            make_item(EvidenceCategory.NEUTRAL, available=False),
            make_item(EvidenceCategory.NEUTRAL, available=False),
        ])
        assert determine_band(ledger, ListSeverity.ADVISORY) == DispositionBand.REVIEW


class TestDefaultReview:
    """Default: mixed or inconclusive evidence → review."""

    def test_only_neutral_evidence(self):
        ledger = EvidenceLedger(items=[
            make_item(EvidenceCategory.NEUTRAL),
            make_item(EvidenceCategory.NEUTRAL),
            make_item(EvidenceCategory.NEUTRAL),
        ])
        assert determine_band(ledger, ListSeverity.ADVISORY) == DispositionBand.REVIEW

    def test_weak_discriminators_only(self):
        ledger = EvidenceLedger(items=[
            make_item(EvidenceCategory.WEAK_DISCRIMINATOR),
            make_item(EvidenceCategory.WEAK_DISCRIMINATOR),
            make_item(EvidenceCategory.NEUTRAL),
        ])
        assert determine_band(ledger, ListSeverity.ADVISORY) == DispositionBand.REVIEW

    def test_empty_ledger(self):
        """No evidence at all → review (principle 1: null hypothesis is match)."""
        ledger = EvidenceLedger(items=[])
        assert determine_band(ledger, ListSeverity.ADVISORY) == DispositionBand.REVIEW
