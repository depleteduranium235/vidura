"""
Tests for the §11.2 Phase 1 backtest harness.

Offline: no AGP, no LLM. The payloads use the real release-reason strings
observed in AGP 300 (§3a) so a change to that vocabulary fails here rather than
silently unlabelling the ground truth.

The load-bearing test in this file is the safety gate — a record the reviewer
labelled "Business partner matched to SPL record" must never come back as a
clear, and if the comparison logic ever counted that as an agreement the whole
backtest would be worthless.
"""

import asyncio
from pathlib import Path

import httpx
import pytest

from core.backtest.harness import (
    BacktestReport,
    Outcome,
    SkipReason,
    classify_outcome,
    run_backtest,
    skip_reason_for,
)
from core.backtest.labels import (
    HumanVerdict,
    classify_release_reason,
    expected_bands,
    is_scorable,
)
from core.backtest.report import (
    dynamic_caveats,
    render_markdown,
    write_all,
    write_workbook,
)
from core.models.schemas import (
    DispositionBand,
    EvidenceCategory,
    EvidenceItem,
    EvidenceLedger,
)
from core.sap.odata import SPL_LEGAL_REGULATIONS, GtsODataClient
from core.sap.schemas import ScreenedPartnerAddress

BASE = "https://agpapp.sap.pwc.com:8001"

# The five reasons actually present in AGP 300, verbatim (§3a).
REASON_FP_LONG = "False-Positive - Business Partner approved for release"
REASON_FP_SHORT = "False-Positive: BP approved for release"
REASON_FALSE_MATCH = "False Match"
REASON_TRUE_POSITIVE = "Business partner matched to SPL record"
REASON_TRANSACTION = "Transaction approved for release"


def decided_row(**overrides) -> dict:
    """A decided SPL record with one hit, shaped like the real V2 payload."""
    row = {
        "LegalRegulation": "SPLUS",
        "AddressID": "0000012345",
        "BusinessPartner": "0000000229",
        "LogicalSystemGroup": "AGPCLNT300",
        "GTSBusinessPartnerType": "BP",
        "GTSBusinessPartnerExternalID": "0000000229",
        "ForeignTradeOrganization": "FTO1",
        "SPLAuditTrailUUID": "3f2b9c14-5d7e-4a81-9c2f-1e6b8d4a7c30",
        # Deliberately NOT blocked: a released record loses this flag, which is
        # exactly why the backtest reads unfiltered.
        "SPLScreenedAddressIsBlocked": False,
        "SPLScreeningResultUserDecision": "A",
        "CstmsCmplncBlockReleaseReason": "Z01",
        "CstmsCmplncBlockReleaseReason_Text": REASON_FP_LONG,
        "SPLScreeningBlockProcessor": "YYUAN062",
        "SPLCheckDateTime": "/Date(1786060800000)/",
        "BusinessPartnerName": "Bearing Buyers Limited",
        "CityName": "Kyiv",
        "Country": "UA",
        "Country_Text": "Ukraine",
        "to_SPLHitsDetailSet": {
            "results": [
                {
                    "AddressID": "0000012345",
                    "ItemNumber": "000001",
                    "LegalRegulation": "SPLUS",
                    "BusinessPartner": "0000000229",
                    "SPLListType": "GPC",
                    "SPLEntity": "100404",
                    "MatchedName": "<strong>Baring</strong> <strong>Buyeers</strong><br>",
                    "MatchedAddress": "Av. de Arag&oacute;n,;   Madrid<br>",
                    "MatchedId": "",
                    "GTSDataProvider": "PWCDATA",
                    "SPLGroup": "GPC",
                    "SPLGroupDesc": "Global Presence Check",
                }
            ]
        },
    }
    row.update(overrides)
    return row


def record(**overrides) -> ScreenedPartnerAddress:
    return GtsODataClient._parse(decided_row(**overrides))


def ledger_with(*categories: EvidenceCategory) -> EvidenceLedger:
    return EvidenceLedger(items=[
        EvidenceItem(
            category=c,
            data_element=f"element {i}",
            assessment="test",
            data_available=True,
        )
        for i, c in enumerate(categories, 1)
    ])


def stub_adjudicator(ledger: EvidenceLedger):
    async def adjudicate(hit, rec):
        return ledger, 7
    return adjudicate


def run(records, adjudicate, **kwargs) -> BacktestReport:
    return asyncio.run(run_backtest(records, adjudicate, **kwargs))


class TestReleaseReasonLabels:
    """
    The release reason IS the ground-truth label, so a mapping error here is
    indistinguishable from a reasoning error in the results.
    """

    def test_both_false_positive_spellings_map_to_cleared(self):
        """Two spellings of the same reason are already in the data (§3a)."""
        assert classify_release_reason(REASON_FP_LONG) == HumanVerdict.CLEARED
        assert classify_release_reason(REASON_FP_SHORT) == HumanVerdict.CLEARED

    def test_false_match_maps_to_cleared(self):
        assert classify_release_reason(REASON_FALSE_MATCH) == HumanVerdict.CLEARED

    def test_confirmed_match_maps_to_true_positive(self):
        assert classify_release_reason(REASON_TRUE_POSITIVE) == HumanVerdict.TRUE_POSITIVE

    def test_transaction_release_is_out_of_scope_not_cleared(self):
        """
        The document path is Phase 3. Scoring it as a BP-path clear would credit
        the agent for a decision about a transaction, not about an identity.
        """
        assert classify_release_reason(REASON_TRANSACTION) == HumanVerdict.OUT_OF_SCOPE

    def test_blank_reason_is_no_reason(self):
        """124 records are decided but only 91 carry a reason."""
        assert classify_release_reason("") == HumanVerdict.NO_REASON
        assert classify_release_reason("", "") == HumanVerdict.NO_REASON

    def test_unknown_reason_is_unmapped_never_guessed(self):
        assert classify_release_reason("Released under licence 12/2026") == HumanVerdict.UNMAPPED

    def test_bare_approved_for_release_is_not_read_as_cleared(self):
        """
        GTS also releases *confirmed* matches under a licence, so "approved for
        release" without "false" is not a clear. A loose substring rule here
        would label a true positive as cleared and hide a safety violation.
        """
        verdict = classify_release_reason("BP approved for release by compliance")
        assert verdict == HumanVerdict.UNMAPPED
        assert not is_scorable(verdict)

    def test_punctuation_and_case_drift_tolerated(self):
        assert classify_release_reason("false positive: bp approved for release") == \
            HumanVerdict.CLEARED
        assert classify_release_reason("FALSE-POSITIVE — BP APPROVED FOR RELEASE") == \
            HumanVerdict.CLEARED

    def test_code_used_when_text_missing(self):
        """The text is language-dependent; a non-EN logon must not lose the label."""
        assert classify_release_reason("", REASON_TRUE_POSITIVE) == HumanVerdict.TRUE_POSITIVE

    def test_only_cleared_and_true_positive_are_scorable(self):
        assert is_scorable(HumanVerdict.CLEARED)
        assert is_scorable(HumanVerdict.TRUE_POSITIVE)
        for verdict in (HumanVerdict.OUT_OF_SCOPE, HumanVerdict.NO_REASON,
                        HumanVerdict.UNMAPPED):
            assert not is_scorable(verdict)
            assert expected_bands(verdict) == frozenset()

    def test_expected_bands_match_the_spec_table(self):
        assert expected_bands(HumanVerdict.CLEARED) == frozenset(
            {DispositionBand.AUTO_CLEAR, DispositionBand.PROPOSE_CLEAR})
        assert expected_bands(HumanVerdict.TRUE_POSITIVE) == frozenset(
            {DispositionBand.ESCALATE})


class TestSafetyGate:
    """
    §8: zero records labelled "matched to SPL record" may come back Auto-clear or
    Propose Clear. If one does, the band logic is broken and nothing else matters.
    """

    @pytest.mark.parametrize("band", [DispositionBand.AUTO_CLEAR,
                                      DispositionBand.PROPOSE_CLEAR])
    def test_clearing_a_confirmed_match_is_a_safety_violation(self, band):
        assert classify_outcome(HumanVerdict.TRUE_POSITIVE, band) == \
            Outcome.SAFETY_VIOLATION

    def test_safety_violation_is_not_diluted_into_divergence(self):
        """
        Reported as its own outcome so it cannot be averaged into a divergence
        count and lost.
        """
        outcome = classify_outcome(HumanVerdict.TRUE_POSITIVE,
                                   DispositionBand.PROPOSE_CLEAR)
        assert outcome != Outcome.DIVERGENCE
        assert outcome == Outcome.SAFETY_VIOLATION

    def test_escalating_a_confirmed_match_agrees(self):
        assert classify_outcome(HumanVerdict.TRUE_POSITIVE, DispositionBand.ESCALATE) == \
            Outcome.AGREEMENT

    def test_review_on_a_confirmed_match_abstains_not_violates(self):
        """Review declines to clear, so it is conservative, not unsafe."""
        assert classify_outcome(HumanVerdict.TRUE_POSITIVE, DispositionBand.REVIEW) == \
            Outcome.ABSTENTION

    def test_gate_fails_the_report_end_to_end(self):
        """
        A dispositive exclusion drives the band engine to Propose Clear; the
        record says the human confirmed the match. The report must fail.
        """
        report = run(
            [record(CstmsCmplncBlockReleaseReason_Text=REASON_TRUE_POSITIVE)],
            stub_adjudicator(ledger_with(EvidenceCategory.DISPOSITIVE_EXCLUSION)),
        )
        assert report.pairs_adjudicated == 1
        assert report.outcomes[0].actual_band == DispositionBand.PROPOSE_CLEAR
        assert not report.safety_gate_passed
        assert len(report.safety_violations) == 1
        assert "FAIL" in render_markdown(report)

    def test_gate_passes_when_confirmed_match_escalates(self):
        report = run(
            [record(CstmsCmplncBlockReleaseReason_Text=REASON_TRUE_POSITIVE)],
            stub_adjudicator(ledger_with(EvidenceCategory.DISPOSITIVE_CONFIRMATION)),
        )
        assert report.outcomes[0].actual_band == DispositionBand.ESCALATE
        assert report.safety_gate_passed
        assert report.outcomes[0].outcome == Outcome.AGREEMENT

    def test_pass_with_no_confirmed_matches_is_reported_as_untested(self):
        """
        A vacuous PASS must not read as a tested PASS — the caveats have to say
        the gate was never exercised.
        """
        report = run([record()], stub_adjudicator(ledger_with(EvidenceCategory.NEUTRAL)))
        assert report.safety_gate_passed
        assert report.true_positive_pairs == 0
        caveats = " ".join(dynamic_caveats(report))
        assert "never exercised" in caveats
        assert "never exercised" in render_markdown(report)


class TestOutcomeClassification:
    def test_cleared_agrees_on_either_clearing_band(self):
        for band in (DispositionBand.AUTO_CLEAR, DispositionBand.PROPOSE_CLEAR):
            assert classify_outcome(HumanVerdict.CLEARED, band) == Outcome.AGREEMENT

    def test_cleared_vs_escalate_is_a_divergence(self):
        assert classify_outcome(HumanVerdict.CLEARED, DispositionBand.ESCALATE) == \
            Outcome.DIVERGENCE

    def test_cleared_vs_review_is_an_abstention(self):
        """
        Review is the designed outcome for a thin file (§3.1 #3), so counting it
        as agreement would flatter the agent and counting it as divergence would
        punish it for behaving correctly.
        """
        assert classify_outcome(HumanVerdict.CLEARED, DispositionBand.REVIEW) == \
            Outcome.ABSTENTION

    def test_no_band_is_an_error_not_an_agreement(self):
        assert classify_outcome(HumanVerdict.CLEARED, None) == Outcome.ERROR

    def test_unscorable_verdicts_can_never_agree(self):
        for verdict in (HumanVerdict.UNMAPPED, HumanVerdict.NO_REASON,
                        HumanVerdict.OUT_OF_SCOPE):
            for band in DispositionBand:
                assert classify_outcome(verdict, band) != Outcome.AGREEMENT


class TestSkipping:
    def test_document_path_is_skipped_as_out_of_scope(self):
        rec = record(CstmsCmplncBlockReleaseReason_Text=REASON_TRANSACTION)
        assert skip_reason_for(rec, HumanVerdict.OUT_OF_SCOPE) == SkipReason.OUT_OF_SCOPE

    def test_record_without_hits_is_skipped_not_scored(self):
        """
        431 of 432 blocked records carry no hit detail (§6a). With nothing to
        compare there is no adjudication, so these must be reported as unrunnable
        rather than counted as anything.
        """
        rec = record(to_SPLHitsDetailSet={"results": []})
        assert skip_reason_for(rec, HumanVerdict.CLEARED) == SkipReason.NO_HIT_DETAIL

        report = run([rec], stub_adjudicator(ledger_with(EvidenceCategory.NEUTRAL)))
        assert report.pairs_adjudicated == 0
        assert len(report.skipped) == 1
        assert report.agreement_rate is None

    def test_deferred_navigation_counts_as_no_hits(self):
        """An unexpanded navigation is {"__deferred": ...}, not data (§6 #6)."""
        rec = record(to_SPLHitsDetailSet={"__deferred": {"uri": "x"}})
        assert rec.hits == []
        assert skip_reason_for(rec, HumanVerdict.CLEARED) == SkipReason.NO_HIT_DETAIL

    def test_label_problems_reported_ahead_of_missing_hit_detail(self):
        """
        A document-path release was never in the BP-path population, so filing it
        under "no hit detail" would overstate how much of the real population is
        unrunnable.
        """
        rec = record(
            CstmsCmplncBlockReleaseReason_Text=REASON_TRANSACTION,
            to_SPLHitsDetailSet={"results": []},
        )
        assert skip_reason_for(rec, HumanVerdict.OUT_OF_SCOPE) == SkipReason.OUT_OF_SCOPE

    def test_unmapped_reason_is_skipped_never_scored(self):
        rec = record(CstmsCmplncBlockReleaseReason_Text="Released under licence",
                     CstmsCmplncBlockReleaseReason="")
        report = run([rec], stub_adjudicator(ledger_with(EvidenceCategory.NEUTRAL)))
        assert report.pairs_adjudicated == 0
        assert report.skipped[0].skip_reason == SkipReason.UNMAPPED_REASON

    def test_scorable_record_with_hits_is_not_skipped(self):
        assert skip_reason_for(record(), HumanVerdict.CLEARED) is None


class TestRates:
    def test_no_rate_when_nothing_scorable(self):
        """An empty run has no rate; 0% and 100% would both be lies."""
        report = BacktestReport()
        assert report.agreement_rate is None
        assert report.non_contradiction_rate is None
        assert "n/a" in render_markdown(report)

    def test_agreement_and_non_contradiction_differ_on_abstention(self):
        report = run(
            [record(), record(AddressID="0000099999")],
            stub_adjudicator(ledger_with(
                EvidenceCategory.NEUTRAL,
                EvidenceCategory.NEUTRAL,
                EvidenceCategory.NEUTRAL,
            )),
        )
        # Three neutrals: not thin, no exclusion — the engine returns Review.
        assert {o.actual_band for o in report.outcomes} == {DispositionBand.REVIEW}
        assert report.agreement_rate == 0.0
        assert report.non_contradiction_rate == 1.0

    def test_extraction_failure_counted_as_error_not_agreement(self):
        async def exploding(hit, rec):
            raise RuntimeError("gateway timeout")

        report = run([record()], exploding)
        assert report.outcomes[0].outcome == Outcome.ERROR
        assert report.outcomes[0].actual_band is None
        assert "gateway timeout" in report.outcomes[0].error
        assert report.agreement_rate is None  # nothing scorable survived
        assert report.scorable_pairs == 0

    def test_one_failure_does_not_end_the_run(self):
        calls = {"n": 0}

        async def flaky(hit, rec):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return ledger_with(EvidenceCategory.DISPOSITIVE_EXCLUSION), 1

        report = run([record(), record(AddressID="0000099999")], flaky)
        assert report.pairs_adjudicated == 2
        assert len(report.errors) == 1
        assert report.outcome_counts[Outcome.AGREEMENT.value] == 1


class TestPipelineIntegration:
    def test_spl_entry_content_reaches_the_hit(self):
        """
        §3a: MatchedName/MatchedAddress carry the sanctioned party's own name and
        address as HTML. If they were treated as flags this content would be lost.
        """
        report = run([record()], stub_adjudicator(ledger_with(EvidenceCategory.NEUTRAL)))
        outcome = report.outcomes[0]
        assert outcome.spl_entry_name == "Baring Buyeers"
        assert "Arag" in outcome.spl_entry_address and "Madrid" in outcome.spl_entry_address
        assert "Baring" in outcome.match_basis  # matched tokens, not a percentage

    def test_match_basis_carries_no_percentage(self):
        """GTS exposes no similarity score here; never render 0.0 as confidence."""
        report = run([record()], stub_adjudicator(ledger_with(EvidenceCategory.NEUTRAL)))
        assert "%" not in report.outcomes[0].match_basis

    def test_one_outcome_per_matched_entry(self):
        row = decided_row()
        second = dict(row["to_SPLHitsDetailSet"]["results"][0],
                      ItemNumber="000002", SPLEntity="100405")
        row["to_SPLHitsDetailSet"]["results"].append(second)
        rec = GtsODataClient._parse(row)

        report = run([rec], stub_adjudicator(ledger_with(EvidenceCategory.NEUTRAL)))
        assert report.pairs_adjudicated == 2
        assert {o.spl_entry_id for o in report.outcomes} == {"100404", "100405"}
        assert report.records_scored == 1

    def test_human_decision_metadata_carried_into_the_outcome(self):
        report = run([record()], stub_adjudicator(ledger_with(EvidenceCategory.NEUTRAL)))
        outcome = report.outcomes[0]
        assert outcome.release_reason_text == REASON_FP_LONG
        assert outcome.human_verdict == HumanVerdict.CLEARED
        assert outcome.decided_by == "YYUAN062"

    def test_records_round_trip_through_json_for_offline_replay(self):
        """--dump-records / --from-records must not lose the hits or the label."""
        original = record()
        replayed = ScreenedPartnerAddress.model_validate(original.model_dump(mode="json"))
        assert replayed.hits[0].spl_entity == original.hits[0].spl_entity
        assert replayed.hits[0].spl_entry_name == "Baring Buyeers"
        assert replayed.release_reason_text == REASON_FP_LONG
        assert replayed.case_key == original.case_key


class TestDecidedQueryConstruction:
    """
    The read must be unfiltered on the blocked flag. Getting this wrong returns
    only confirmed matches and biases the entire backtest toward the one verdict
    the agent must never get wrong.
    """

    def _capture(self, **kwargs) -> dict:
        seen = {}

        def handler(request):
            seen["params"] = dict(request.url.params)
            return httpx.Response(200, json={"d": {"results": []}})

        http = httpx.Client(transport=httpx.MockTransport(handler))
        with GtsODataClient(BASE, "u", "p", http_client=http) as c:
            list(c.iter_decided_addresses(**kwargs))
        return seen["params"]

    def test_filters_on_user_decision(self):
        params = self._capture()
        assert "SPLScreeningResultUserDecision ne ''" in params["$filter"]

    def test_blocked_flag_never_filtered(self):
        params = self._capture()
        assert "SPLScreenedAddressIsBlocked" not in params["$filter"]

    def test_restricted_to_spl_regulations_by_default(self):
        params = self._capture()
        assert "LegalRegulation eq 'SPLUS'" in params["$filter"]
        assert "LegalRegulation eq 'SPLSY'" in params["$filter"]

    def test_regulation_clause_is_parenthesised(self):
        """
        Without the brackets the trailing `or` would escape the leading `and` and
        return every record in the system, decided or not.
        """
        params = self._capture()
        assert "(LegalRegulation eq 'SPLUS' or LegalRegulation eq 'SPLSY')" in \
            params["$filter"]

    def test_all_regulations_when_none_requested(self):
        params = self._capture(legal_regulations=None)
        assert "LegalRegulation" not in params["$filter"]

    def test_hits_are_expanded(self):
        params = self._capture()
        assert "to_SPLHitsDetailSet" in params["$expand"]

    def test_stable_ordering_for_skip_paging(self):
        params = self._capture()
        assert params["$orderby"] == "SPLCheckDateTime asc"

    def test_spl_regulations_are_the_two_screening_ones(self):
        assert set(SPL_LEGAL_REGULATIONS) == {"SPLUS", "SPLSY"}

    def test_blocked_iterator_still_filters_on_blocked(self):
        """The refactor must not have loosened the polling path's filter."""
        seen = {}

        def handler(request):
            seen["params"] = dict(request.url.params)
            return httpx.Response(200, json={"d": {"results": []}})

        http = httpx.Client(transport=httpx.MockTransport(handler))
        with GtsODataClient(BASE, "u", "p", http_client=http) as c:
            list(c.iter_blocked_addresses(enrich_bp=False))
        assert "SPLScreenedAddressIsBlocked eq true" in seen["params"]["$filter"]


class TestReportOutput:
    def test_writes_spreadsheet_csvs_markdown_and_json(self, tmp_path: Path):
        report = run([record()], stub_adjudicator(ledger_with(
            EvidenceCategory.DISPOSITIVE_EXCLUSION)))
        written = write_all(report, tmp_path)
        names = {p.name for p in written}
        assert {"backtest.xlsx", "summary.csv", "cases.csv", "evidence.csv",
                "skipped.csv", "backtest.md", "backtest.json"} <= names
        assert all(p.exists() and p.stat().st_size > 0 for p in written)

    def test_workbook_has_the_expected_sheets(self, tmp_path: Path):
        openpyxl = pytest.importorskip("openpyxl")
        report = run([record()], stub_adjudicator(ledger_with(EvidenceCategory.NEUTRAL)))
        path = write_workbook(report, tmp_path / "b.xlsx")
        assert path is not None
        wb = openpyxl.load_workbook(path)
        assert {"Summary", "Cases", "Evidence", "Divergences", "Skipped",
                "Caveats"} <= set(wb.sheetnames)

    def test_empty_run_still_writes_without_crashing(self, tmp_path: Path):
        """A run that reads nothing must produce a report saying so, not a stack."""
        report = run([], stub_adjudicator(ledger_with(EvidenceCategory.NEUTRAL)))
        written = write_all(report, tmp_path)
        assert all(p.exists() for p in written)
        assert "demonstrates nothing" in render_markdown(report)

    def test_divergence_section_includes_the_full_evidence_ledger(self):
        """§11.2 says to investigate each divergence, which needs the ledger."""
        report = run(
            [record(CstmsCmplncBlockReleaseReason_Text=REASON_TRUE_POSITIVE)],
            stub_adjudicator(EvidenceLedger(items=[EvidenceItem(
                category=EvidenceCategory.DISPOSITIVE_EXCLUSION,
                data_element="Entity Type",
                bp_value="Legal Entity",
                spl_value="Natural Person",
                assessment="Listing is a natural person; BP is a registered company.",
                data_available=True,
            )])),
        )
        md = render_markdown(report)
        assert "## Divergences" in md
        assert "Entity Type" in md
        assert "Listing is a natural person" in md

    def test_caveats_state_auto_clear_is_unreachable(self):
        """
        Auto-clear needs a precedent and the store is a stub, so a zero in that
        column is an artefact. Saying so is the difference between an honest
        report and a misleading one.
        """
        report = run([record()], stub_adjudicator(ledger_with(EvidenceCategory.NEUTRAL)))
        md = render_markdown(report)
        assert "Auto-clear is structurally unreachable" in md

    def test_caveats_state_the_population_is_too_small_to_measure(self):
        report = run([record()], stub_adjudicator(ledger_with(EvidenceCategory.NEUTRAL)))
        caveats = " ".join(dynamic_caveats(report))
        assert "no rate below should be read as a performance measure" in caveats

    def test_safety_gate_leads_the_markdown(self):
        """The gate must come before the agreement rate, not after it."""
        report = run([record()], stub_adjudicator(ledger_with(EvidenceCategory.NEUTRAL)))
        md = render_markdown(report)
        assert md.index("The metric that matters") < md.index("## Results")

    def test_mock_run_does_not_claim_the_llm_ran(self):
        """
        A --mock run exercises the band engine only. Reporting it as if the
        evidence extractor had run would overstate the result.
        """
        report = run([record()], stub_adjudicator(ledger_with(EvidenceCategory.NEUTRAL)),
                     mock_extractor=True, live_read=True)
        md = render_markdown(report)
        assert "used the mock extractor, not the LLM" in md
        assert "**not** the LLM evidence extractor" in md

    def test_replayed_run_does_not_claim_a_live_read(self):
        report = run([record()], stub_adjudicator(ledger_with(EvidenceCategory.NEUTRAL)),
                     live_read=False)
        md = render_markdown(report)
        assert "replayed from a saved file" in md
        assert "live GTS read path" not in md

    def test_live_llm_run_claims_both(self):
        report = run([record()], stub_adjudicator(ledger_with(EvidenceCategory.NEUTRAL)),
                     live_read=True, mock_extractor=False)
        md = render_markdown(report)
        assert "the live GTS read path" in md
        assert "used the mock extractor" not in md
        assert "replayed from a saved file" not in md

    def test_confusion_matrix_covers_every_band(self):
        report = run([record()], stub_adjudicator(ledger_with(EvidenceCategory.NEUTRAL)))
        rows, cols, cells = report.confusion_matrix()
        assert [b.value for b in DispositionBand] == cols[:-1]
        assert cols[-1] == "(error)"
        assert cells[(HumanVerdict.CLEARED.value, DispositionBand.REVIEW.value)] == 1
