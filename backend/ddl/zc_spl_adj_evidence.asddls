@AccessControl.authorizationCheck: #NOT_REQUIRED
@EndUserText.label: 'SPL Adjudication Evidence - Projection'
@Metadata.allowExtensions: true

define view entity ZC_SPL_AdjEvidence
  as projection on ZI_SPL_AdjEvidence
{
  key EvidenceUUID,
      CaseUUID,

      Category,
      CategoryCriticality,
      DataElement,
      BPValue,
      SPLValue,
      Assessment,
      DataAvailable,
      SortOrder,
      SourceSystem,
      SourceField,

      _Case : redirected to parent ZC_SPL_AdjCase
}
