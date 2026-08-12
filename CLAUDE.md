# SPL Adjudication — Fiori Elements App

## Project type
SAP Fiori Elements List Report + Object Page, OData V4, SAPUI5 1.120.

## Commands
- `npm run start-mock` — launch with mock server (primary dev workflow)
- `npm run build` — production build to dist/
- `npm run lint` — ESLint on webapp/

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

## Don't
- Don't hallucinate UI5 APIs. If unsure, check via the MCP server or the official SDK docs.
- Don't add views/controllers for standard List Report or Object Page behaviour.
- Don't use deprecated `sap.ui.commons`, `sap.ui.table` (for this app), or jQuery-based patterns.
- Don't hardcode service URLs — they come from manifest.json dataSources.
