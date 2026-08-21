"""
§5.6 Polling orchestrator — the production sweep loop.

Watches GTS for new screening blocks, adjudicates each BP-address ↔ SPL-entry
pair with bounded LLM concurrency, writes results, and advances the watermark.

This is glue. The heavy lifting is in:
  - core.sap.odata (read)
  - core.sap.mapper (explode to pairs)
  - core.evidence.extractor (LLM)
  - core.bands.engine (deterministic band)
  - writer (persist)
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from datetime import datetime, timezone
from typing import Optional

from ..bands import determine_band
from ..config import (
    BAND_LOGIC_VERSION,
    MATERIALITY_THRESHOLD_USD,
    MODEL_ID,
    PROMPT_VERSION,
    TAXONOMY_VERSION,
)
from ..evidence.extractor import extract_evidence_async, extract_evidence_mock
from ..models.schemas import AdjudicationResult, EvidenceLedger, HitInput
from ..sap.mapper import to_hit_inputs
from ..sap.odata import GtsODataClient, ODataError, SPL_LEGAL_REGULATIONS
from ..sap.schemas import ScreenedPartnerAddress
from .counters import KillSwitch, KillSwitchTripped, SweepCounters
from .state import OrchestratorState
from .writer import ResultWriter

log = logging.getLogger(__name__)

DEFAULT_CONCURRENCY = 5
DEFAULT_SWEEP_INTERVAL_S = 60


class Orchestrator:
    def __init__(
        self,
        client: GtsODataClient,
        writer: ResultWriter,
        state: OrchestratorState,
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        sweep_interval_s: int = DEFAULT_SWEEP_INTERVAL_S,
        kill_switch: Optional[KillSwitch] = None,
        use_mock: bool = False,
    ):
        self._client = client
        self._writer = writer
        self._state = state
        self._concurrency = concurrency
        self._sweep_interval_s = sweep_interval_s
        self._kill_switch = kill_switch or KillSwitch()
        self._use_mock = use_mock
        self._shutdown = False
        self._semaphore: Optional[asyncio.Semaphore] = None

    async def run(self) -> None:
        self._semaphore = asyncio.Semaphore(self._concurrency)
        self._install_signal_handlers()
        self._state.load()
        log.info(
            "Orchestrator starting. watermark=%s processed=%d concurrency=%d mock=%s",
            self._state.watermark or "(none — full sweep)",
            len(self._state.processed_ids),
            self._concurrency,
            self._use_mock,
        )

        while not self._shutdown:
            try:
                await self._sweep()
            except KillSwitchTripped as exc:
                log.critical("KILL SWITCH: %s", exc)
                break
            except ODataError as exc:
                log.error("OData read failure: %s", exc)
                self._kill_switch.record_read_failure()
                try:
                    self._kill_switch.check()
                except KillSwitchTripped as ks:
                    log.critical("KILL SWITCH (read failures): %s", ks)
                    break
            except Exception:
                log.exception("Unexpected error in sweep")

            if self._shutdown:
                break
            await self._interruptible_sleep(self._sweep_interval_s)

        log.info("Orchestrator stopped. total_adjudicated=%d", self._state.total_adjudicated)
        self._writer.close()
        self._state.save()

    async def _sweep(self) -> None:
        start = time.perf_counter_ns()
        counters = SweepCounters()
        max_ts: Optional[datetime] = None

        records: list[ScreenedPartnerAddress] = []
        # Query each SPL regulation separately at the OData level so GTS
        # filters server-side (avoids reading 400+ non-SPL records).
        # Only SPLUS/SPLSY need keyed re-reads for empty hits; ZHORG and
        # others already have hits populated in the collection expand.
        REGS_NEEDING_REFETCH = ("SPLUS", "SPLSY")
        for reg in SPL_LEGAL_REGULATIONS:
            for record in self._client.iter_blocked_addresses(
                since=self._state.watermark,
                legal_regulation=reg,
                unprocessed_only=True,
                expand=True,
                enrich_bp=True,
                refetch_empty_hits=(reg in REGS_NEEDING_REFETCH),
            ):
                records.append(record)

        self._kill_switch.record_read_success()
        counters.records_read = len(records)

        if not records:
            log.debug("Sweep: 0 new records")
            return

        pairs: list[tuple[ScreenedPartnerAddress, HitInput]] = []

        for record in records:
            if record.spl_check_datetime:
                if max_ts is None or record.spl_check_datetime > max_ts:
                    max_ts = record.spl_check_datetime

            # No regulation check needed — we pre-filtered at the OData level

            record_id = self._state.record_identity(record)
            if self._state.is_processed(record_id):
                counters.records_skipped_already_processed += 1
                continue

            hits = to_hit_inputs(record)
            if not hits:
                counters.records_skipped_no_hits += 1
                self._state.mark_processed(record_id)
                continue

            for hit in hits:
                pairs.append((record, hit))

        if not pairs:
            if max_ts:
                self._state.advance_watermark(max_ts)
                self._state.save()
            counters.elapsed_ms = (time.perf_counter_ns() - start) // 1_000_000
            counters.log_summary(str(max_ts))
            return

        tasks = [self._adjudicate_one(r, h, counters) for r, h in pairs]
        await asyncio.gather(*tasks)

        # Mark all processed records after the sweep completes
        seen_records: set[str] = set()
        for record, _ in pairs:
            rid = self._state.record_identity(record)
            if rid not in seen_records:
                self._state.mark_processed(rid)
                seen_records.add(rid)

        if max_ts:
            self._state.advance_watermark(max_ts)
        self._state.last_sweep_at = datetime.now(timezone.utc)
        self._state.save()

        counters.elapsed_ms = (time.perf_counter_ns() - start) // 1_000_000
        counters.log_summary(str(max_ts))
        self._kill_switch.check()

    async def _adjudicate_one(
        self,
        record: ScreenedPartnerAddress,
        hit: HitInput,
        counters: SweepCounters,
    ) -> None:
        async with self._semaphore:
            try:
                if self._use_mock:
                    ledger = extract_evidence_mock(hit)
                    elapsed_ms = 0
                else:
                    ledger, elapsed_ms = await extract_evidence_async(
                        hit,
                        spl_address=hit.spl_entry_address,
                        bp_address=record.full_address,
                    )
            except Exception as exc:
                log.error("Extraction failed for %s: %s", hit.case_id, exc)
                counters.errors += 1
                self._kill_switch.record_error()
                return

            band = determine_band(
                ledger=ledger,
                list_severity=hit.list_severity,
                below_materiality=hit.order_value < MATERIALITY_THRESHOLD_USD,
                precedent_exists=False,
            )

            result = AdjudicationResult(
                case_id=hit.case_id,
                disposition_band=band,
                rationale="; ".join(i.assessment for i in ledger.items if i.data_available),
                what_would_change="",
                evidence_summary=self._summary(ledger),
                evidence_ledger=ledger,
                model_version="mock" if self._use_mock else MODEL_ID,
                prompt_version=PROMPT_VERSION,
                band_logic_version=BAND_LOGIC_VERSION,
                taxonomy_version=TAXONOMY_VERSION,
                elapsed_ms=elapsed_ms,
            )

            try:
                self._writer.write(result)
                counters.pairs_written += 1
            except Exception as exc:
                log.error("Write failed for %s: %s", hit.case_id, exc)
                counters.errors += 1
                self._kill_switch.record_error()
                return

            counters.pairs_adjudicated += 1
            self._kill_switch.record_success()
            self._state.total_adjudicated += 1

    @staticmethod
    def _summary(ledger: EvidenceLedger) -> str:
        parts = []
        n = len(ledger.dispositive_exclusions)
        if n:
            parts.append(f"{n} exclusion(s)")
        n = len(ledger.dispositive_confirmations)
        if n:
            parts.append(f"{n} confirmation(s)")
        n = len(ledger.strong_discriminators)
        if n:
            parts.append(f"{n} strong disc.")
        n = len(ledger.all_corroborators)
        if n:
            parts.append(f"{n} corroborator(s)")
        parts.append(f"{len(ledger.items)} total")
        return ", ".join(parts)

    def _install_signal_handlers(self) -> None:
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, self._request_shutdown)
        except (NotImplementedError, AttributeError):
            signal.signal(signal.SIGINT, lambda s, f: self._request_shutdown())

    def _request_shutdown(self) -> None:
        log.info("Shutdown requested — finishing current sweep")
        self._shutdown = True

    async def _interruptible_sleep(self, seconds: int) -> None:
        for _ in range(seconds):
            if self._shutdown:
                return
            await asyncio.sleep(1)
