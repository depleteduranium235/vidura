"""
Integration tests for the full adjudication pipeline (mock mode).

These verify that the components wire together correctly:
hit input → evidence extraction → band logic → result output.
"""

import asyncio
import pytest

from core.models import (
    HitInput,
    DispositionBand,
    ListSeverity,
    IntakePath,
)
from core.bands import determine_band
from core.evidence.extractor import extract_evidence_mock


class TestPipelineEndToEnd:
    """Full pipeline from HitInput to DispositionBand."""

    def test_entity_type_mismatch_with_enough_evidence(self):
        """Legal entity vs individual with country data → Propose Clear (if no corroborators)."""
        hit = HitInput(
            case_id="PIPE-001",
            bp_id="001",
            bp_name="Schmidt & Weber GmbH",
            bp_country="DE",
            bp_entity_type="Legal Entity",
            bp_registration_no="HRB 54321",
            spl_entry_id="EU-9921",
            spl_entry_name="SCHMIDT, Hans-Peter",
            spl_list_type="EU Sanctions",
            spl_programme="RUSSIA",
            spl_entity_type="Individual",
            match_percentage=71.5,
            match_basis="Surname token match only",
            intake_path=IntakePath.BP_BLOCK,
            list_severity=ListSeverity.ADVISORY,
        )
        ledger = extract_evidence_mock(hit)
        assert len(ledger.items) >= 2

        band = determine_band(
            ledger=ledger,
            list_severity=hit.list_severity,
            below_materiality=True,
            precedent_exists=False,
        )
        assert band == DispositionBand.REVIEW

    def test_same_entity_type_on_blocking_list(self):
        """Same entity type produces weak corroborator → blocking list escalates."""
        hit = HitInput(
            case_id="PIPE-002",
            bp_id="002",
            bp_name="Petrochemical Supplies FZE",
            bp_country="AE",
            bp_entity_type="Legal Entity",
            bp_registration_no="SHJ-FZ-2019-4421",
            spl_entry_id="SDN-31002",
            spl_entry_name="PETRO SUPPLIES TRADING FZE",
            spl_list_type="SDN",
            spl_programme="IRAN",
            spl_entity_type="Legal Entity",
            match_percentage=91.3,
            match_basis="Near-exact name match",
            intake_path=IntakePath.DOC_BLOCK,
            list_severity=ListSeverity.BLOCKING,
        )
        ledger = extract_evidence_mock(hit)
        has_corroborator = any("CORR" in i.category.value for i in ledger.items)
        assert has_corroborator

    def test_mock_produces_valid_evidence_items(self):
        """Mock extractor produces items with all required fields."""
        hit = HitInput(
            case_id="PIPE-003",
            bp_id="003",
            bp_name="Test Corp",
            bp_country="US",
            bp_entity_type="Legal Entity",
            bp_registration_no="",
            spl_entry_id="SDN-999",
            spl_entry_name="TEST PERSON",
            spl_list_type="SDN",
            spl_programme="IRAN",
            spl_entity_type="Individual",
            match_percentage=60.0,
            match_basis="Name token",
            intake_path=IntakePath.BP_BLOCK,
            list_severity=ListSeverity.BLOCKING,
        )
        ledger = extract_evidence_mock(hit)
        for item in ledger.items:
            assert item.category is not None
            assert item.data_element != ""
            assert item.assessment != ""

    def test_empty_entity_types_produce_country_only(self):
        """When entity types are missing, only country evidence is generated."""
        hit = HitInput(
            case_id="PIPE-004",
            bp_id="004",
            bp_name="Unknown Entity",
            bp_country="TR",
            bp_entity_type="",
            bp_registration_no="",
            spl_entry_id="EU-100",
            spl_entry_name="SOME ENTITY",
            spl_list_type="EU Sanctions",
            spl_programme="RUSSIA",
            spl_entity_type="",
            match_percentage=55.0,
            match_basis="Name match",
            intake_path=IntakePath.DELTA,
            list_severity=ListSeverity.ADVISORY,
        )
        ledger = extract_evidence_mock(hit)
        assert len(ledger.items) == 1
        assert ledger.items[0].data_element == "Country"
