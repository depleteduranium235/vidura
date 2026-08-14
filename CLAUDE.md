# Vidura — SPL Hit Adjudication

## Project type
Monorepo: Fiori Elements UI (OData V4, SAPUI5 1.120) + Python adjudication sidecar + Node.js API layer.

## Commands

### Fiori UI
- `npm run start-mock` — launch with mock server (primary dev workflow)
- `npm run build` — production build to dist/
- Open `http://localhost:8081/test/standalone.html` after start

### Sidecar (from `sidecar/` directory)
- `cd sidecar && python -m pytest tests/ -v` — run all tests (125 passing)
- `cd sidecar/core && python run_adjudication.py --mock` — full pipeline, no API key needed
- `cd sidecar/core && python run_adjudication.py '{"case_id":...}'` — real LLM (needs ANTHROPIC_API_KEY)
- `cd sidecar/api && npm run dev` — start REST API on port 3001

### Phase 1 backtest (§11.2) — from `sidecar/`
Read-only. Exits non-zero if the §8 safety gate fails, so it can gate a build.
- `printf '%s\n' 'PASSWORD' | python run_backtest.py` — live AGP read + real LLM
- `printf '%s\n' 'PASSWORD' | python run_backtest.py --mock` — no LLM spend; proves read/map/band only
- `printf '%s\n' 'PASSWORD' | python run_backtest.py --dump-records records.json` then
  `python run_backtest.py --from-records records.json` — fetch once, tune the taxonomy offline
- Output in `sidecar/backtest_out/`: `backtest.xlsx`, CSVs, `backtest.md`, `backtest.json`

### Environment setup
- Node.js via fnm: `fnm env --shell power-shell | ForEach-Object { Invoke-Expression $_ }`
- Python: `$env:PATH = "$env:LOCALAPPDATA\Programs\Python\Python312;$env:LOCALAPPDATA\Programs\Python\Python312\Scripts;$env:PATH"`

## Architecture rules
- **Fiori Elements, not freestyle.** No custom views or controllers unless implementing a custom section via the Object Page extension API.
- **Annotations drive the UI.** Column changes, filter changes, field grouping, criticality — all go in `webapp/localService/metadata.xml`, inside the `<Annotations Target="...AdjudicationCaseType">` blocks. Never generate layout XML.
- **`webapp/annotations/annotation.xml` is dead code.** `manifest.json` declares `"annotations": []`, so it is never loaded, and it is a stale subset of metadata.xml (8 columns vs 13, no SelectionVariants). Editing it changes nothing. Delete it or wire it up — until then, ignore it.
- **Property labels need `Common.Label`.** `UI.LineItem` carries inline labels per column, so table headers read fine without it — but the filter bar reads `Common.Label` on the property and falls back to the raw technical name (`IntakePath:`) when it is missing.
- **Verify UI changes in a browser.** Fiori Elements builds the UI at runtime from annotations, so a bad `annotationPath` does not fail loudly — sap.fe silently drops the tab, column or section. `node demo/verify_app.mjs <url> <out-prefix>` loads the app in Edge and reports rendered columns, view tabs, sections and console errors.
- **OData V4 only.** No V2 patterns, no `sap.ui.model.odata.v2.*`.
- **All strings through i18n.** `webapp/i18n/i18n.properties`. No hardcoded text in annotations (use String, not i18n keys, only in local annotation.xml where i18n is not supported).
- **SAP semantic colours only.** Use Criticality integer values (0=None, 1=Error/Negative, 2=Warning/Critical, 3=Success/Positive, 5=Information). Never custom CSS colours.
- **Compact density for desktop.** Already set in index.html and flpSandbox.html.

## Entity model
- `AdjudicationCase` — the main worklist entity (Z table, written by sidecar)
- `EvidenceItem` — the evidence ledger per case (child entity, navigation `_EvidenceItems`)

## Disposition bands (domain values)
Auto-clear | Propose Clear | Review | Escalate

## Evidence categories (domain values)
DispositiveExclusion | StrongDiscriminator | WeakDiscriminator | Neutral | WeakCorroborator | StrongCorroborator | DispositiveConfirmation

## Key design decisions
- The agent never emits a confidence number — show the band and the evidence.
- Missing data must look different from mismatched (DataAvailable boolean).
- Never show a bare percentage without context (MatchBasis accompanies MatchPercentage).
- Evidence ledger sorted by SortOrder (dispositive items first).

## MCP servers
- `playwright` — configured in `.mcp.json` (`@playwright/mcp`, driving installed Edge). Connects at session start; needs a restart after being added.
- Not configured: `@ui5/mcp-server`, `@sap-ux/fiori-mcp-server`. Referenced here previously as if available — they are not.

## Demo and verification tooling (`demo/`)
- `verify_app.mjs` — loads the running app in Edge, reports rendered columns/tabs/sections and console errors, screenshots. Add `--width=N`.
- `capture_demo.mjs` — captures the 5-screen walkthrough of the running app.
- `build_share_page.mjs` — packages those screenshots into `fiori-app-walkthrough.html`, one self-contained file with no dependencies.
- Uses `playwright-core` with `channel: "msedge"`, so no browser download is needed.

## Sidecar architecture (§3.3, §5.3)
- **LLM populates the evidence ledger; deterministic rules set the band.** The model never emits a confidence number.
- `sidecar/core/bands/` — pure-function band engine, unit-testable, no LLM dependency.
- `sidecar/core/backtest/` — §11.2 Phase 1 harness. `labels.py` maps GTS release reasons to ground-truth verdicts; `harness.py` scores bands against them; `report.py` writes the spreadsheet and the credibility argument.
- `sidecar/core/evidence/` — LLM extraction (prompt.py is a controlled artifact per §8).
- `sidecar/core/models/` — Pydantic schemas shared across all layers.
- `sidecar/api/` — Express/TypeScript REST layer, calls Python via child_process.

## Don't
- Don't hallucinate UI5 APIs. If unsure, check via the MCP server or the official SDK docs.
- Don't add views/controllers for standard List Report or Object Page behaviour.
- Don't use deprecated `sap.ui.commons`, `sap.ui.table` (for this app), or jQuery-based patterns.
- Don't hardcode service URLs — they come from manifest.json dataSources.
- Don't let the LLM produce a disposition band or confidence number — that's the band engine's job.
- Don't filter the backtest read on `SPLScreenedAddressIsBlocked`. Releasing a record clears that flag, so the blocked-only feed cannot see any *cleared* decision — filtering would return only confirmed matches and bias the backtest toward the one verdict the agent must never get wrong. Use `iter_decided_addresses()`.
- Don't map a GTS release reason to "cleared" on a loose substring. "Approved for release" without "false" is not a clear — GTS also releases confirmed matches under a licence. Unknown reasons must come back `UNMAPPED` and stay unscored.
- Don't modify `sidecar/core/evidence/prompt.py` without running regression tests — it's a controlled artifact.
