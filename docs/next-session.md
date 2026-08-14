# Start here — session handoff

Read `docs/gts-integration-notes.md` first. It holds every fact established about
AGP 300 and supersedes anything remembered loosely. Note especially **§3a**,
which corrects an earlier wrong conclusion in §4.

---

## Environment (nothing is on PATH permanently)

```bash
# Python
export PATH="/c/Users/jtyagi002/AppData/Local/Programs/Python/Python312:/c/Users/jtyagi002/AppData/Local/Programs/Python/Python312/Scripts:$PATH"
export PYTHONIOENCODING=utf-8      # required — GTS returns CJK and German text

# Node (Fiori UI), via fnm
fnm env --shell power-shell | ForEach-Object { Invoke-Expression $_ }
```

`PYTHONIOENCODING=utf-8` is not optional. Without it any script printing GTS text
dies with a `cp1252` `UnicodeEncodeError`.

Tests: `cd sidecar && python -m pytest tests/ -q` → **125 passing**.

## Credentials

AGP 300 needs `jtyagi002` + password. **The password is not stored anywhere in
this repo** — ask for it. Every script reads it from stdin:

```bash
printf '%s\n' 'PASSWORD' | python <script>.py
```

`getpass()` on Windows reads the console via `msvcrt` and ignores a pipe, so
scripts detect a non-tty and read stdin instead. Don't "fix" that.

A read-only technical user (`S_SERVICE` on the three `LLS_*` services) is still
the right long-term answer and is on the access request to Mike.

**Read-only only.** Every entity set in all 18 services is read-only except the
release-decision entities, which §5.4 reserves for a human in their own session.
Do not write to AGP.

---

## What exists

**Working, needs no client 200:**

| Path | What |
|---|---|
| `sidecar/core/sap/` | OData V2 read layer — client, models, mapper |
| `sidecar/core/bands/` | Deterministic band engine (§3.1 principles) |
| `sidecar/core/evidence/` | LLM extraction; `prompt.py` is a controlled artefact (§8) |
| `sidecar/core/models/` | Shared Pydantic schemas |
| `sidecar/core/backtest/` | §11.2 Phase 1 harness — labels, scoring, spreadsheet + credibility argument |
| `sidecar/run_backtest.py` | Backtest CLI. Read-only; exits non-zero if the §8 safety gate fails |
| `webapp/` | Fiori Elements List Report + Object Page, runs on mock data |
| `backend/ddl/` | Z table, CDS views, RAP behaviour definition — **spec only**, needs 200 |

**Diagnostics, all read-only, all rerunnable:**

- `explore_agp.py` — full survey: all 18 services, counts, samples, live client check
- `analyse_blocked.py` — evidence availability across blocked records
- `analyse_regs.py` — blocked counts per legal regulation
- `analyse_bp_population.py` — BP master richness across all 4,119 partners
- `find_audit_service.py` — enumerates every registered Gateway service
- `find_decisions.py` — whether human decisions exist
- `find_backtest_set.py` — decided SPL records

---

## The Phase 1 backtest harness — BUILT, not yet run against AGP

§11.2 Phase 1 — *"Prove reasoning offline. Batch mode, no writeback. Run against
historical hits, compare to human decisions, tune the evidence taxonomy. Output:
a spreadsheet and a credibility argument."*

Ground truth is already in AGP: **124 human decisions, 91 with release reasons,
18 on SPL regulations.** No client extract needed to start.

**Status:** implemented and tested offline (56 harness tests, 125 total). It has
**not** been run against live AGP — that needs the password. Do that first:

```bash
cd sidecar
printf '%s\n' 'PASSWORD' | python run_backtest.py --dump-records records.json
```

`--dump-records` saves the fetched records so the taxonomy can then be tuned
offline with `--from-records records.json` without re-reading AGP or re-spending
LLM calls. Add `--mock` to prove the read/map/band path with no LLM at all.
Output lands in `sidecar/backtest_out/` — read `backtest.md` first.

The run exits non-zero if the §8 gate fails, so it can gate a build.

### Design

1. **Read decided records UNFILTERED.** Critical: a released record is no longer
   `SPLScreenedAddressIsBlocked = true`, so the blocked-only feed cannot see any
   decided case. Filter on `SPLScreeningResultUserDecision ne ''` instead, and
   restrict to `LegalRegulation eq 'SPLUS' or eq 'SPLSY'`.
2. **Map release reason → expected band:**

   | Release reason | Human verdict | Agent should say |
   |---|---|---|
   | False-Positive - Business Partner approved for release | cleared | Propose Clear / Auto-clear |
   | False-Positive: BP approved for release | cleared | Propose Clear / Auto-clear |
   | False Match | cleared | Propose Clear / Auto-clear |
   | **Business partner matched to SPL record** | **true positive** | **Escalate — never a clear** |
   | Transaction approved for release | cleared (document path) | out of BP-path scope |

3. **Run the pipeline** per record: `to_hit_inputs()` → `extract_evidence()` →
   `determine_band()`.
4. **Report:** agreement rate, a confusion matrix of expected vs actual band, and
   every divergence with its full evidence ledger. §11.2 says to investigate each
   divergence — some will be human error, which is a finding worth having.
5. **The metric that matters (§8):** *zero* records labelled "matched to SPL
   record" may come back Auto-clear or Propose Clear. If one does, the band logic
   is broken and nothing else matters.

### Caveats to state honestly in the output

- Only ~2 of the 18 currently carry hit detail, so the runnable set is tiny
- No BP discriminators exist anywhere in AGP (DOB/nationality/identifiers: 0 of 4,119)
- SPL entry **name and address** are available (§3a); aliases, DOB, nationality,
  identifiers and remarks are not
- So this validates the *mechanism*, not adjudication quality. Quality needs
  either the Z view (client 200) or a real client extract

The harness emits all of these itself — `core/backtest/report.py` holds the
established facts as `STATIC_CAVEATS` and computes the run-specific ones from the
run's own numbers, so the prose can't drift from the figures. It also flags two
things worth knowing before quoting any result:

- **Auto-clear is structurally unreachable.** It requires a matching precedent
  (§3.4) and `core/precedent/store.py` is still an in-memory stub, so the best a
  correctly-cleared case can score is Propose Clear. The zero in that column is
  an artefact, not a finding
- **Review is counted as an abstention**, separately from both agreement and
  divergence. On this data a thin ledger routing to Review is the band engine
  working as specified (§3.1 #3), so scoring it either way would mislead — and a
  wall of Reviews with a 0% agreement rate is the expected result, not a failure

---

## Then

1. **Band → release-reason mapping** in the Z table, so the Fiori app speaks the
   vocabulary reviewers already use. The codes are in §3a of the notes.
2. **Polling orchestrator** — watermark on `SPLCheckDateTime`, idempotency on
   `SPLAuditTrailUUID`, bounded LLM concurrency, coverage counters (§8), kill
   switch (§5.6 #6). BP enrichment is already batched. The write half needs 200.
3. **Z CDS view over `/SAPSLL/TSPL*`** for the remaining §4.1 fields. Needs 200,
   and needs §9.2 answered first — which tables actually hold populated data.

---

## Open, waiting on someone else

- **Mike** — AGP 200 developer access + SAP BAS (`Z008`, ID unconfirmed). Email sent.
- **`/IWFND/MAINT_SERVICE` → Add Service** — is there an unactivated service
  exposing SPL master data or an audit trail? Results were promised for this
  session. An SPL-master service would close the biggest gap **without** needing 200.
- **§9.2** — which `/SAPSLL/TSPL*` tables hold populated data. Now on the critical
  path, not parallel.
- **`ZHORG`** — 417 of 432 blocked records sit under it and nobody has said what it
  is. If humans review that population manually, it may be a separate opportunity.
