@AccessControl.authorizationCheck: #NOT_REQUIRED
@EndUserText.label: 'SPL Adjudication Case - Interface View'
define root view entity ZI_SPL_AdjCase
  as select from zspl_adj_case
  composition [0..*] of ZI_SPL_AdjEvidence as _EvidenceItems
{
  key case_uuid           as CaseUUID,
      case_id             as CaseID,

      // Business Partner
      bp_id               as BusinessPartnerID,
      bp_name             as BusinessPartnerName,
      bp_country          as BPCountry,
      bp_city             as BPCity,
      bp_entity_type      as BPEntityType,
      bp_registration_no  as BPRegistrationNo,

      // SPL Entry
      spl_entry_id        as SPLEntryID,
      spl_entry_name      as SPLEntryName,
      spl_list_type       as SPLListType,
      spl_programme       as SPLProgramme,
      spl_entity_type     as SPLEntityType,

      // Match
      match_percentage    as MatchPercentage,
      match_basis         as MatchBasis,
      comparison_rule     as ComparisonRule,

      // Classification
      intake_path         as IntakePath,
      disposition_band    as DispositionBand,
      status              as Status,
      priority            as Priority,
      assigned_to         as AssignedTo,

      // Agent output
      agent_rationale     as AgentRationale,
      what_would_change   as WhatWouldChangeMyMind,
      evidence_summary    as EvidenceSummary,

      // Precedent
      precedent_exists    as PrecedentExists,
      precedent_case_id   as PrecedentCaseID,

      // Document path (§4.3)
      doc_type            as DocumentType,
      doc_number          as DocumentNumber,
      order_value         as OrderValue,
      order_currency      as OrderCurrency,
      ship_to_country     as ShipToCountry,
      sold_to_bp          as SoldToPartner,
      end_use_code        as EndUseCode,

      // Versioning / audit (§8)
      model_version       as ModelVersion,
      prompt_version      as PromptVersion,
      band_logic_version  as BandLogicVersion,
      taxonomy_version    as TaxonomyVersion,
      processing_ts       as ProcessingTimestamp,
      elapsed_ms          as ElapsedMilliseconds,

      // Human decision
      human_decision      as HumanDecision,
      human_user          as HumanUser,
      human_comment       as HumanComment,
      decision_ts         as DecisionTimestamp,

      // Criticality (virtual/calculated for UI rendering)
      case cast(
        case disposition_band
          when 'ESCALATE'      then 1
          when 'REVIEW'        then 2
          when 'PROPOSE_CLEAR' then 3
          when 'AUTO_CLEAR'    then 5
          else 0
        end as abap.int4 )   as DispositionBandCriticality,

      case cast(
        case status
          when 'NEW'            then 0
          when 'AGENT_COMPLETE' then 5
          when 'IN_PROGRESS'    then 2
          when 'CLEARED'        then 3
          when 'AUTO_CLEARED'   then 3
          when 'ESCALATED'      then 1
          when 'CONFIRMED'      then 1
          else 0
        end as abap.int4 )   as StatusCriticality,

      case cast(
        case priority
          when 'CRITICAL' then 1
          when 'HIGH'     then 1
          when 'MEDIUM'   then 2
          when 'LOW'      then 0
          else 0
        end as abap.int4 )   as PriorityCriticality,

      // Age in days
      dats_days_between(
        cast( created_at as abap.dats ),
        $session.system_date
      )                      as AgedDays,

      // Admin
      @Semantics.user.createdBy: true
      created_by            as CreatedBy,
      @Semantics.systemDateTime.createdAt: true
      created_at            as CreatedAt,
      @Semantics.user.lastChangedBy: true
      changed_by            as ChangedBy,
      @Semantics.systemDateTime.lastChangedAt: true
      changed_at            as ChangedAt,

      // Composition
      _EvidenceItems
}
