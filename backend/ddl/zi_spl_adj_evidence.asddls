@AccessControl.authorizationCheck: #NOT_REQUIRED
@EndUserText.label: 'SPL Adjudication Evidence - Interface View'
define view entity ZI_SPL_AdjEvidence
  as select from zspl_adj_evidence
  association to parent ZI_SPL_AdjCase as _Case
    on $projection.CaseUUID = _Case.CaseUUID
{
  key evidence_uuid    as EvidenceUUID,
      case_uuid        as CaseUUID,

      // §3.2 taxonomy classification
      category         as Category,

      // Criticality per taxonomy band:
      //   DispositiveExclusion/Confirmation = 1 (red — demands attention)
      //   Strong discriminator/corroborator = 2 (yellow)
      //   Weak discriminator/corroborator = 0 (neutral)
      //   Neutral = 0
      case cast(
        case category
          when 'DISP_EXCL'   then 5
          when 'STRONG_DISC' then 3
          when 'WEAK_DISC'   then 0
          when 'NEUTRAL'     then 0
          when 'WEAK_CORR'   then 0
          when 'STRONG_CORR' then 2
          when 'DISP_CONF'   then 1
          else 0
        end as abap.int4 )  as CategoryCriticality,

      data_element     as DataElement,
      bp_value         as BPValue,
      spl_value        as SPLValue,
      assessment       as Assessment,

      // §3.1 principle 3: missing ≠ mismatched
      data_available   as DataAvailable,

      sort_order       as SortOrder,
      source_system    as SourceSystem,
      source_field     as SourceField,

      _Case
}
