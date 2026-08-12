@AccessControl.authorizationCheck: #NOT_REQUIRED
@EndUserText.label: 'SPL Adjudication Case - Projection'
@Metadata.allowExtensions: true

define root view entity ZC_SPL_AdjCase
  provider contract transactional_query
  as projection on ZI_SPL_AdjCase
{
  key CaseUUID,
      CaseID,

      BusinessPartnerID,
      BusinessPartnerName,
      BPCountry,
      BPCity,
      BPEntityType,
      BPRegistrationNo,

      SPLEntryID,
      SPLEntryName,
      SPLListType,
      SPLProgramme,
      SPLEntityType,

      MatchPercentage,
      MatchBasis,
      ComparisonRule,

      IntakePath,
      DispositionBand,
      Status,
      Priority,
      AssignedTo,

      AgentRationale,
      WhatWouldChangeMyMind,
      EvidenceSummary,

      PrecedentExists,
      PrecedentCaseID,

      DocumentType,
      DocumentNumber,
      OrderValue,
      OrderCurrency,
      ShipToCountry,
      SoldToPartner,
      EndUseCode,

      ModelVersion,
      PromptVersion,
      BandLogicVersion,
      TaxonomyVersion,
      ProcessingTimestamp,
      ElapsedMilliseconds,

      HumanDecision,
      HumanUser,
      HumanComment,
      DecisionTimestamp,

      DispositionBandCriticality,
      StatusCriticality,
      PriorityCriticality,
      AgedDays,

      CreatedBy,
      CreatedAt,
      ChangedBy,
      ChangedAt,

      _EvidenceItems : redirected to composition child ZC_SPL_AdjEvidence
}
