# Vidura — SPL Hit Adjudication

## Project type
Monorepo: Fiori Elements UI (OData V4, SAPUI5 1.120) + Python adjudication sidecar + Node.js API layer.

## Commands

### Fiori UI
- `npm run start-mock` — launch with mock server (primary dev workflow)
- `npm run build` — production build to dist/
- Open `http://localhost:8081/test/standalone.html` after start

### Sidecar (from `sidecar/` directory)
- `cd sidecar && python -m pytest tests/ -v` — run all tests (19 passing)
- `cd sidecar/core && python run_adjudication.py --mock` — full pipeline, no API key needed
- `cd sidecar/core && python run_adjudication.py '{"case_id":...}'` — real LLM (needs ANTHROPIC_API_KEY)
- `cd sidecar/api && npm run dev` — start REST API on port 3001

### Environment setup
- Node.js via fnm: `fnm env --shell power-shell | ForEach-Object { Invoke-Expression $_ }`
- Python: `$env:PATH = "$env:LOCALAPPDATA\Programs\Python\Python312;$env:LOCALAPPDATA\Programs\Python\Python312\Scripts;$env:PATH"`

## Architecture rules
- **Fiori Elements, not freestyle.** No custom views or controllers unless implementing a custom section via the Object Page extension API.
- **Annotations drive the UI.** Column changes, filter changes, field grouping, criticality — all go in `webapp/annotations/annotation.xml`. Never generate layout XML.
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

## MCP servers (when available)
- `@ui5/mcp-server` — for UI5 API reference and code generation
- `@sap-ux/fiori-mcp-server` — for Fiori Elements app editing

## Sidecar architecture (§3.3, §5.3)
- **LLM populates the evidence ledger; deterministic rules set the band.** The model never emits a confidence number.
- `sidecar/core/bands/` — pure-function band engine, unit-testable, no LLM dependency.
- `sidecar/core/evidence/` — LLM extraction (prompt.py is a controlled artifact per §8).
- `sidecar/core/models/` — Pydantic schemas shared across all layers.
- `sidecar/api/` — Express/TypeScript REST layer, calls Python via child_process.

## Don't
- Don't hallucinate UI5 APIs. If unsure, check via the MCP server or the official SDK docs.
- Don't add views/controllers for standard List Report or Object Page behaviour.
- Don't use deprecated `sap.ui.commons`, `sap.ui.table` (for this app), or jQuery-based patterns.
- Don't hardcode service URLs — they come from manifest.json dataSources.
- Don't let the LLM produce a disposition band or confidence number — that's the band engine's job.
- Don't modify `sidecar/core/evidence/prompt.py` without running regression tests — it's a controlled artifact.
