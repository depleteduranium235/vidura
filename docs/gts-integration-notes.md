# GTS Integration Notes — AGP 300

Findings from live investigation of AGP client 300 (GTS E4H 2025), August 2026.
These were expensive to establish; treat the field names as verified fact unless
a later metadata pull contradicts them.

Companion to `spl-adjudication-agent-project-brief.md`. Section references (§) point there.

---

## 1. Connection

| | |
|---|---|
| Host | `https://agpapp.sap.pwc.com:8001` (resolves to `20.65.119.245`, Azure) |
| Client | `300` |
| Auth | **Basic** — `www-authenticate: Basic realm="SAP NetWeaver Application Server [AGP/300]"` |
| Confirms identity | response header `sap-system: AGP` |
| OData version | **V2** (`/sap/opu/odata/sap/…`, not `odata4`) |
| ICF node | `/sap/opu/odata`, "SAP Gateway OData V2", active |
| Processing mode | "Co-deployed only" for most services — Gateway and backend are the same system |
| Latency | ~150 ms connect, ~430 ms for a metadata GET |

### TLS: the non-obvious part

**Python must verify against the OS trust store, not certifi.** AGP's chain goes
through corporate PKI that Windows trusts and certifi does not ship.

- `curl` succeeds because Git-for-Windows curl is built on **Schannel**, which reads the Windows store
- Plain Python fails with `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`
- Fix: the `truststore` package. `core/sap/odata.py::default_verify()` does this and is the default
- The corporate bundle at `~/.claude/certs/corporate-ca-bundle.pem` is **additive** (PwC CAs only) and **fails standalone** — it is not a substitute

---

## 2. Services

18 GTS services registered. **External names are `LLS_*`; technical names are `ZLLS_*`.**
URLs use the external name.

> Filtering `/IWFND/MAINT_SERVICE` on `*SPL*` returns **nothing** — no GTS service
> has "SPL" in its name. Filter on `LLS_*`.

| Service | App | Relevance |
|---|---|---|
| `LLS_BPADDR_MNG_SRV` | Manage Partners - SPL Screening (Fiori **F4542A**) | **What we read.** Superset for our purposes |
| `LLS_BPADDRSCRNGRSLT_MNG` | Manage Partner Screening Result | Adds `SPLAssocPartnerAddr` (shared-address evidence) and `SPLBlkdAddrCmt` |
| `LLS_BLKDBPADDR_MNG` | Manage Blocked Business Partners | Worklist + value helps. **No hits navigation** |
| `LLS_BLKDCCD_MNG` / `_RSV` | Manage Blocked Documents / Release Document Blocks | Document path — §3 Phase 3 |

Why we read `LLS_BPADDR_MNG_SRV` rather than the blocked-only service: it covers
*all* screened partners, so filtering `SPLScreenedAddressIsBlocked eq true`
ourselves also leaves **cleared** records reachable. §7's continuous
re-adjudication needs those — a 2023 clear against an entry amended in 2026 must
be re-openable, and a blocked-only feed cannot see it.

---

## 3. Entity model — `LLS_BPADDR_MNG_SRV`

### Root: `C_SPLScrngScreenedPrtnAddress` (66 properties)

Composite key: `LegalRegulation`, `AddressID`, `BusinessPartner`,
`LogicalSystemGroup`, `GTSBusinessPartnerType`, `GTSBusinessPartnerExternalID`,
`ForeignTradeOrganization`

**The screening unit is the ADDRESS, not the partner.** One BP with three
addresses yields three records.

Seven navigation properties:

| Navigation | Target | Use |
|---|---|---|
| `to_SPLHitsDetailSet` | `SPLHitsDetail` | The hits |
| `to_SPLScrngBPIdentification` | identifications | Tax / VAT / registration (§4.2) |
| `to_SPLScrngBPAssocdBlkdObj` | associated blocked objects | Network evidence (§3.1 #8) |
| `to_SPLScrngBPAssociatedBank` | associated banks | Network evidence |
| `to_SPLScrngBankAssocdBP` | bank-associated BP | Network evidence |
| `to_SPLScreeningCheckStatus` | status text | — |
| `to_RefBusinessPartnerExt` | `C_GTS_BusinessPartnerExtIDType` (10 props) | **Not** BP master |

### `SPLHitsDetail` — the hit record

Keys: `AddressID`, `ItemNumber`, `LegalRegulation`, `BusinessPartner`,
`SPLListType`, **`SPLEntity`**, `GTSDataProvider`

- **`SPLEntity`** — label "SPL Number". **This is the SPL entry key.** The BP↔entry link *is* persisted and exposed
- `MatchedName` / `MatchedAddress` / `MatchedId` — String flags describing **what** matched
- **No similarity percentage.** It lives in `/SAPSLL/SPLATM` and is not exposed here. Per §2 and §6.4 this is fine — arguably better, since the three flags are *match basis* (§3.1 #6), which is the more informative signal
- `SPLGroup` / `SPLGroupDesc` — the sanctions programme

### `I_BusinessPartner` (81 properties)

**Addressable as an entity set but NOT navigable from the root** — needs a
separate keyed GET: `I_BusinessPartner('0010000108')`.

Carries §4.1's highest-value discriminators, BP side:

| Field | Note |
|---|---|
| `BusinessPartnerCategory` | **Authoritative** person/org: `1`=Person, `2`=Organization, `3`=Group. §3.2's most common dispositive exclusion |
| `BirthDate`, `BusinessPartnerBirthplaceName` | DOB, POB |
| `BusPartNationality` | Nationality |
| `BusinessPartnerBirthName` | Name at birth — BP-side alias analogue |
| `OrganizationFoundationDate` | Enables "BP incorporated *after* the listing" reasoning |
| `Industry` | Sector — §3.2 weak corroborator |

### Fields that matter operationally

| Purpose | Field |
|---|---|
| **Watermark** (§5.6 #5) | `SPLCheckDateTime` — filterable `DateTimeOffset` |
| **Idempotency key** (§5.6 #4) | `SPLAuditTrailUUID` (Guid) |
| Blocked-only filter | `SPLScreenedAddressIsBlocked eq true` |
| Unprocessed filter | `SPLScrngBlockProcessingStatus eq ''` |
| Network evidence | `SPLScreeningBlockIsIndirect`, `SPLScrngBPHasBlkdAssocdBank` |
| GTS's own decisions | `SPLScrngResultSystemDecision`, `SPLScreeningResultUserDecision` |
| Good-guy / exclusion list | `SPLScreeningExclusionCategory` ("Pos./Neg. List") |

Almost every field on the root is filterable — the exceptions are the key fields
themselves and a few texts.

### The write path — do not touch

`SPLBlkdAddrSet` carries `ActionReason`, `ActionConfirmReason`, `ActionComment`,
`ProcessComment`, `Processor`. **This is where a human executes a release**, via
F4542A, under their own user ID.

Per §5.4 the sidecar must never write here. Our output goes to the Z result table.
This entity is also the deep-link target for §6.4's "link out to the standard
GTS transaction".

---

## 4. The gap: no sanctioned-party content

**Verified absent from all three services.** The agent gets the *pointer* to an
SPL entry and nothing to dereference it against.

Evidence for the conclusion:
1. `SPLEntity` has no `sap:text` and no `sap:value-list` annotation
2. None of the root's seven associations reach a sanctioned-party entity
3. A dump of every `SPL*` property across all three services returns only
   screening-**process** fields and the **pointer** — no name, alias, DOB,
   nationality, passport, programme narrative, or remarks

Missing, all from §4.1: alias/AKA set, address history, DOB/POB/nationality,
passport / national ID / registration / LEI, listing and amendment history,
remarks and narrative free text, vessel and aircraft identifiers.

### Closing it

Requires a **Z CDS view over `/SAPSLL/TSPL*`** → needs **client 200** → needs
**§9.2 answered first** (which tables actually hold populated data).

This moves §9.2 from "parallel, non-blocking" to **on the critical path**.

Fallbacks if 200 is slow:
- The Descartes source files already syncing to SharePoint. Uncertainty is whether `SPLEntity` maps to the Descartes record ID — that is §9.2 step 3 territory
- RFC via pyrfc (§5.4's alternative read path); heavier, needs the NW RFC SDK

**Degradation is safe.** With no SPL content the ledger comes out thin and the
band engine routes everything to Review, because §3.1 #3 makes missing data
neutral and neutral can never clear a hit. The pipe still proves out; the
recommendations are just low-value.

---

## 5. Tables — from `ST05` trace of Manage Blocked Documents

Semantics inferred from GTS naming; verify in SE11 before relying on them.

**BP side:** `/SAPSLL/PNTBP`, `/SAPSLL/BPADDR`, `BUT000`, `ADRC`

**SPL screening:** `/SAPSLL/SPLAT` (audit trail), **`/SAPSLL/SPLATM`** — confirmed
to hold the matched **keyword and percentage**

**Document side:** `/SAPSLL/CUHD` (header), `CUHDBLR` (block reason), `CUIT`
(items), `CORPAR` (partners), `CORREF` (feeder doc ref), `CORCMT` (comments)

**Config:** `ALRG01N`, `ALRG02`, `ALRG03`, `LEGLRGI`, `T606GN`, `TAPGR`

**Released CDS view observed:** `I_SPLScrngBlkdPartnerAddress`, SQL view name
`ISPLSCBLKADR`.

> `SE84` search must use the **SQL view name** (`ISPLSC*`), not the CDS entity
> name — searching `I_SPLSCRNGBLKD*` returns nothing. Newer CDS *view entities*
> have no DDIC view at all and may be invisible to SE84 entirely.

Also: the transaction writes to `BALHDR`/`BALDAT`, so activity is visible in `SLG1`.

---

## 6. Gotchas, each found the hard way

1. **OData V2 dates are `/Date(1786579200000)/`**, not ISO 8601. Milliseconds since
   epoch UTC; an optional trailing offset is in **minutes** (`+0120` = 120 min)
2. **`datetime.fromtimestamp()` raises `OSError` on Windows for negative values.**
   Every pre-1970 date of birth would crash the reader. Use epoch arithmetic:
   `datetime(1970,1,1,tz=utc) + timedelta(milliseconds=ms)`
3. **Identification type codes are opaque** (`BUP002`) and configuration-dependent.
   Match preference terms against the readable `BPIdentificationTypeText`, which
   means the service must be called with `sap-language=EN`
4. **certifi fails, OS trust store works** — see §1
5. **V2 datetime literals take no `Z` and no offset**: `datetime'2026-08-13T00:00:00'`
6. **Unexpanded navigations return `{"__deferred": {…}}`**, not data. Treat as empty
7. `$count` returns `text/plain`, not JSON

---

## 6a. Live data survey — 14 Aug 2026

Run against AGP 300 with a dialog user. All 18 services reachable.

**25,066 screened addresses, 432 blocked.** But the blocked population is almost
entirely the wrong *kind*:

| Legal regulation | Screened | Blocked | SPL screening? |
|---|---|---|---|
| `SPLSY` — SPL Screening (Sayari) | 30 | **0** | yes |
| `SPLUS` — SPL Screening (US) | 4,166 | **1** | yes |
| `ZHORG` | 4,161 | **417** | no |
| `ZPLCN` / `ZPLEM` / `ZPLKW` / `ZHIND` | ~4,175 ea. | 14 total | no |

Only `SPLSY` and `SPLUS` are labelled "Sanctioned Party List Screening" in
`I_GTS_LegalRegulationText`. Everything else in the catalogue is customs
(`Zollabwicklung`), transit (`NCTS`), export control (`EAR`, `ITAR`, `AWV`),
embargo (`EMBUN`), excise (`EMCS`) or preference agreements.

**Consequence: 431 of 432 blocked records carry no `SPLHitsDetail` at all**, and
correctly so — a licence or embargo block has no sanctioned party to compare
against. There is no identity question, so nothing for this agent to adjudicate.

### The one adjudicatable record

```
BP 229  'Basam Hasan'  [US / Tal-Kurdi]   reg=SPLUS
  status='Checked/Blocked by System'  system_decision='2'  processing_status='0'
  HIT entry='GL4493524'  list='ACL'  provider='DESCARTES'  basis='Matched on name'
  HIT entry='GL4906807'  list='ACL'  provider='DESCARTES'  basis='Matched on name'
```

The read path handled it correctly end to end: two hits parsed, BP master
resolved, mapped to two `HitInput`s. **The pipe works.** There is simply almost
nothing to run through it.

### Evidence actually present (sample of 60 blocked records)

| | |
|---|---|
| ≥1 hit | 2/60 (and both on non-SPL regs in that sample) |
| identifications | **0/60** |
| associated blocked objects | **0/60** |
| associated banks | **0/60** |
| indirect-block flag | 0/60 |
| BP master fetched | 60/60 |

BP master resolves but is empty of discriminators: **DOB 0/60, nationality 0/60,
birthplace 0/60, name at birth 0/60, foundation date 0/60, industry 0/60**. Only
`BusinessPartnerCategory` is set — and it reads `2` (Organization) for all 60,
including records plainly named after people (`'Basam Hasan'`). So entity type is
**not** a reliable discriminator on this data.

The network evidence I'd counted on (§3.1 #8) is therefore unavailable in AGP:
the `SPLScrngBPIdnIsHidden` / `SPLScrngBPBankIsHidden` / `SPLScrngBkAssocdBPIsHidden`
flags all come back `true`, which is GTS telling the UI those sections are empty.

### What follows

AGP can prove **the pipe**, not **the adjudication**. Options, in order of value:

1. **Seed test data.** Create a handful of BPs that will match SPL entries — one
   natural person with DOB and nationality, one legal entity with a registration
   number — and re-run screening. This is GTS *functional* work, not developer
   work, so it needs no client 200.
2. **Lower the SPLUS similarity threshold** so more of the existing 4,166 screened
   partners produce hits. Config authority required.
3. **Do quality validation offline** — §11.2 Phase 1, backtesting against real
   historical hits from the client system rather than AGP. This is where the brief
   puts it anyway, and it does not depend on AGP at all.

Recommended: 1 for a demo, 3 for credibility. AGP's value is integration proof.

---

## 7. Data reality in AGP 300

- **BP data is dummy test data.** So `BirthDate`, `BusPartNationality`,
  `OrganizationFoundationDate` etc. are likely empty in practice
- **Descartes SPL data is real** (genuine published designations)

Both sides are therefore thin, and adjudication quality in AGP will be limited
regardless of the SPL-content gap. The MVP proves two things separately:

- **The pipe works** — GTS → sidecar → LLM → band engine → Z table → Fiori app. Provable in AGP as-is
- **The adjudication is good** — needs realistic data on both sides. Either seed a few proper BPs in AGP (one natural person with DOB and nationality, one legal entity with a registration number), or run the §11.2 Phase 1 backtest offline, which is where the brief puts it

---

## 8. State of play

**Working, no client 200 needed:**
- Network, auth and TLS to AGP 300 — verified end to end
- `core/sap/` read layer: models, client, mapper, 66 passing tests
- BP master enrichment with `$select` and per-client caching
- `explore_agp.py` — read-only survey (GET only, no `$batch`, no function imports)

**Blocked on client 200:**
- Z result table and the RAP behaviour definition (DDL already drafted in `backend/ddl/`)
- Our own OData V4 service for the write path
- Fiori app deployment to the BSP repository
- The Z CDS view over `/SAPSLL/TSPL*` for SPL entry content

**Open:**
- A read-only technical user for AGP 300 — `S_SERVICE` on the three services above. Last thing between the client and live data
- §9.2 — which `/SAPSLL/TSPL*` tables hold populated data
- Whether `LLS_BPADDRSCRNGRSLT_MNG`'s `SPLAssocPartnerAddr` adds shared-address evidence worth reading
