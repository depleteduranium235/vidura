"""
Tests for the §5.6 polling orchestrator.

Offline: no AGP, no LLM. Exercises state persistence, kill-switch logic,
coverage counters, and a full mock sweep through the pipeline.
"""

import asyncio
import json
from pathlib import Path

import pytest

from core.models.schemas import DispositionBand, EvidenceCategory, EvidenceItem, EvidenceLedger
from core.orchestrator.counters import KillSwitch, KillSwitchTripped, SweepCounters
from core.orchestrator.state import OrchestratorState
from core.orchestrator.writer import JsonlWriter, ResultWriter
from core.orchestrator.poller import Orchestrator
from core.sap.odata import GtsODataClient
from core.sap.schemas import ScreenedPartnerAddress

import httpx


def screened_row(**overrides) -> dict:
    row = {
        "LegalRegulation": "SPLUS",
        "AddressID": "0000012345",
        "BusinessPartner": "0000000229",
        "LogicalSystemGroup": "AGPCLNT300",
        "GTSBusinessPartnerType": "BP",
        "GTSBusinessPartnerExternalID": "0000000229",
        "ForeignTradeOrganization": "FTO1",
        "SPLAuditTrailUUID": "3f2b9c14-5d7e-4a81-9c2f-1e6b8d4a7c30",
        "SPLScreenedAddressIsBlocked": True,
        "SPLScreeningResultUserDecision": "",
        "SPLScrngBlockProcessingStatus": "",
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


class TestState:
    def test_load_missing_file_is_empty(self, tmp_path):
        state = OrchestratorState(tmp_path / "does_not_exist.json")
        state.load()
        assert state.watermark is None
        assert state.processed_ids == set()

    def test_save_and_reload(self, tmp_path):
        from datetime import datetime, timezone
        path = tmp_path / "state.json"
        state = OrchestratorState(path)
        state.watermark = datetime(2026, 8, 14, 10, 0, 0, tzinfo=timezone.utc)
        state.mark_processed("uuid-1")
        state.mark_processed("uuid-2")
        state.total_adjudicated = 7
        state.save()

        reloaded = OrchestratorState(path)
        reloaded.load()
        assert reloaded.watermark == state.watermark
        assert reloaded.processed_ids == {"uuid-1", "uuid-2"}
        assert reloaded.total_adjudicated == 7

    def test_watermark_only_advances(self):
        from datetime import datetime, timezone
        state = OrchestratorState()
        t1 = datetime(2026, 8, 14, 10, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 8, 14, 9, 0, 0, tzinfo=timezone.utc)
        state.advance_watermark(t1)
        state.advance_watermark(t2)
        assert state.watermark == t1

    def test_dedup_check(self):
        state = OrchestratorState()
        state.mark_processed("abc")
        assert state.is_processed("abc")
        assert not state.is_processed("def")

    def test_prune_keeps_last_n(self):
        state = OrchestratorState()
        for i in range(100):
            state.mark_processed(f"id-{i:04d}")
        removed = state.prune(keep_last_n=30)
        assert removed == 70
        assert len(state.processed_ids) == 30

    def test_atomic_save_no_corruption(self, tmp_path):
        path = tmp_path / "state.json"
        state = OrchestratorState(path)
        state.mark_processed("first")
        state.save()
        assert path.exists()
        assert not path.with_suffix(".tmp").exists()


class TestKillSwitch:
    def test_no_trip_when_healthy(self):
        ks = KillSwitch(window_size=5, error_threshold=0.30)
        for _ in range(10):
            ks.record_success()
        ks.check()

    def test_trips_on_high_error_rate(self):
        ks = KillSwitch(window_size=5, error_threshold=0.30)
        for _ in range(5):
            ks.record_success()
        for _ in range(5):
            ks.record_error()
        with pytest.raises(KillSwitchTripped, match="Error rate"):
            ks.check()

    def test_trips_on_consecutive_read_failures(self):
        ks = KillSwitch(max_consecutive_read_failures=3)
        ks.record_read_failure()
        ks.record_read_failure()
        ks.check()
        ks.record_read_failure()
        with pytest.raises(KillSwitchTripped, match="read failures"):
            ks.check()

    def test_read_success_resets_counter(self):
        ks = KillSwitch(max_consecutive_read_failures=3)
        ks.record_read_failure()
        ks.record_read_failure()
        ks.record_read_success()
        ks.record_read_failure()
        ks.check()

    def test_window_trims(self):
        ks = KillSwitch(window_size=5, error_threshold=0.50)
        for _ in range(5):
            ks.record_error()
        for _ in range(5):
            ks.record_success()
        ks.check()
        assert ks.error_rate == 0.0


class TestSweepCounters:
    def test_coverage_delta_zero_when_healthy(self):
        c = SweepCounters(pairs_adjudicated=5, pairs_written=5)
        assert c.coverage_delta == 0

    def test_coverage_delta_nonzero_on_mismatch(self):
        c = SweepCounters(pairs_adjudicated=5, pairs_written=3)
        assert c.coverage_delta == 2


class TestJsonlWriter:
    def test_writes_and_is_idempotent(self, tmp_path):
        from core.models.schemas import AdjudicationResult, EvidenceLedger
        path = tmp_path / "out.jsonl"
        writer = JsonlWriter(path)

        result = AdjudicationResult(
            case_id="test-001",
            disposition_band=DispositionBand.REVIEW,
            rationale="test",
            what_would_change="",
            evidence_summary="1 total",
            evidence_ledger=EvidenceLedger(),
        )

        writer.write(result)
        writer.write(result)

        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1

    def test_reloads_seen_ids_from_existing_file(self, tmp_path):
        from core.models.schemas import AdjudicationResult, EvidenceLedger
        path = tmp_path / "out.jsonl"
        path.write_text('{"case_id":"existing-001"}\n', encoding="utf-8")

        writer = JsonlWriter(path)
        result = AdjudicationResult(
            case_id="existing-001",
            disposition_band=DispositionBand.REVIEW,
            rationale="",
            what_would_change="",
            evidence_summary="",
            evidence_ledger=EvidenceLedger(),
        )
        writer.write(result)
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1

    def test_satisfies_protocol(self):
        assert isinstance(JsonlWriter(), ResultWriter)


class TestOrchestratorSweep:
    """Integration test: one mock sweep through the full pipeline."""

    def _make_client(self, rows):
        def handler(request):
            url = str(request.url)
            if "$count" in url:
                return httpx.Response(200, text=str(len(rows)),
                                      headers={"Content-Type": "text/plain"})
            if "I_BusinessPartner" in url:
                return httpx.Response(200, json={"d": {
                    "BusinessPartner": "0000000229",
                    "BusinessPartnerCategory": "2",
                    "BusinessPartnerType": "",
                    "FirstName": "", "LastName": "",
                    "BirthDate": None, "BusinessPartnerBirthDateStatus": "",
                    "BusinessPartnerBirthName": "",
                    "BusinessPartnerBirthplaceName": "",
                    "BusPartNationality": "",
                    "OrganizationFoundationDate": None,
                    "Industry": "", "BusinessPartnerIsBlocked": False,
                }})
            # Keyed single-entity read (contains parens in the path)
            if "(" in url and "$skip" not in url and "$top" not in url:
                row = rows[0] if rows else {}
                return httpx.Response(200, json={"d": row})
            return httpx.Response(200, json={"d": {"results": rows}})

        transport = httpx.MockTransport(handler)
        http = httpx.Client(transport=transport, headers={"Accept": "application/json"})
        return GtsODataClient("https://mock", "u", "p", http_client=http)

    def test_one_sweep_adjudicates_and_writes(self, tmp_path):
        client = self._make_client([screened_row()])
        state = OrchestratorState(tmp_path / "state.json")
        writer = JsonlWriter(tmp_path / "results.jsonl")

        orchestrator = Orchestrator(
            client=client, writer=writer, state=state, use_mock=True,
            sweep_interval_s=0,
        )

        async def run_one():
            orchestrator._semaphore = asyncio.Semaphore(5)
            state.load()
            await orchestrator._sweep()

        asyncio.run(run_one())

        assert state.total_adjudicated == 1
        assert len(state.processed_ids) == 1
        lines = (tmp_path / "results.jsonl").read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        result = json.loads(lines[0])
        assert result["disposition_band"] in [b.value for b in DispositionBand]

    def test_dedup_skips_already_processed(self, tmp_path):
        client = self._make_client([screened_row()])
        state = OrchestratorState(tmp_path / "state.json")
        state.mark_processed("3f2b9c14-5d7e-4a81-9c2f-1e6b8d4a7c30")
        writer = JsonlWriter(tmp_path / "results.jsonl")

        orchestrator = Orchestrator(
            client=client, writer=writer, state=state, use_mock=True,
            sweep_interval_s=0,
        )

        async def run_one():
            orchestrator._semaphore = asyncio.Semaphore(5)
            state.load()
            await orchestrator._sweep()

        asyncio.run(run_one())
        assert state.total_adjudicated == 0
        assert not (tmp_path / "results.jsonl").exists()

    def test_wrong_regulation_skipped(self, tmp_path):
        client = self._make_client([screened_row(LegalRegulation="EMBUN")])
        state = OrchestratorState(tmp_path / "state.json")
        writer = JsonlWriter(tmp_path / "results.jsonl")

        orchestrator = Orchestrator(
            client=client, writer=writer, state=state, use_mock=True,
            sweep_interval_s=0,
        )

        async def run_one():
            orchestrator._semaphore = asyncio.Semaphore(5)
            state.load()
            await orchestrator._sweep()

        asyncio.run(run_one())
        assert state.total_adjudicated == 0

    def test_no_hits_skipped(self, tmp_path):
        client = self._make_client([screened_row(to_SPLHitsDetailSet={"results": []})])
        state = OrchestratorState(tmp_path / "state.json")
        writer = JsonlWriter(tmp_path / "results.jsonl")

        orchestrator = Orchestrator(
            client=client, writer=writer, state=state, use_mock=True,
            sweep_interval_s=0,
        )

        async def run_one():
            orchestrator._semaphore = asyncio.Semaphore(5)
            state.load()
            await orchestrator._sweep()

        asyncio.run(run_one())
        assert state.total_adjudicated == 0
        assert len(state.processed_ids) == 1
