@EndUserText.label : 'SPL Adjudication Case'
@AbapCatalog.enhancement.category : #NOT_EXTENSIBLE
@AbapCatalog.tableCategory : #TRANSPARENT
@AbapCatalog.deliveryClass : #A
@AbapCatalog.dataMaintenance : #RESTRICTED
define table zspl_adj_case {

  key client         : abap.clnt not null;
  key case_uuid      : sysuuid_x16 not null;

  // Human-readable case identifier
  case_id            : abap.char(20) not null;

  // Business Partner
  bp_id              : bu_partner;
  bp_name            : bu_name1t;
  bp_country         : land1;
  bp_city            : ort01;
  bp_entity_type     : abap.char(20);
  bp_registration_no : abap.char(40);

  // SPL Entry
  spl_entry_id       : abap.char(30);
  spl_entry_name     : abap.char(200);
  spl_list_type      : abap.char(40);
  spl_programme      : abap.char(60);
  spl_entity_type    : abap.char(20);

  // Match
  match_percentage   : abap.dec(5,1);
  match_basis        : abap.char(200);
  comparison_rule    : abap.char(40);

  // Classification
  intake_path        : abap.char(20);
  disposition_band   : abap.char(20);
  status             : abap.char(20);
  priority           : abap.char(10);
  assigned_to        : abap.char(80);

  // Agent output
  agent_rationale    : abap.string(0);
  what_would_change  : abap.string(0);
  evidence_summary   : abap.char(500);

  // Precedent
  precedent_exists   : abap_boolean;
  precedent_case_id  : abap.char(20);

  // Document path fields (§4.3)
  doc_type           : abap.char(20);
  doc_number         : abap.char(20);
  order_value        : abap.curr(15,2);
  order_currency     : waers;
  ship_to_country    : land1;
  sold_to_bp         : bu_partner;
  end_use_code       : abap.char(20);

  // Versioning and audit (§8)
  model_version      : abap.char(40);
  prompt_version     : abap.char(40);
  band_logic_version : abap.char(20);
  taxonomy_version   : abap.char(20);
  processing_ts      : timestampl;
  elapsed_ms         : abap.int4;

  // Human decision
  human_decision     : abap.char(20);
  human_user         : syuname;
  human_comment      : abap.string(0);
  decision_ts        : timestampl;

  // Admin
  created_by         : syuname;
  created_at         : timestampl;
  changed_by         : syuname;
  changed_at         : timestampl;

}
