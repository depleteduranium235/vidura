@EndUserText.label : 'SPL Adjudication Evidence Item'
@AbapCatalog.enhancement.category : #NOT_EXTENSIBLE
@AbapCatalog.tableCategory : #TRANSPARENT
@AbapCatalog.deliveryClass : #A
@AbapCatalog.dataMaintenance : #RESTRICTED
define table zspl_adj_evidence {

  key client          : abap.clnt not null;
  key evidence_uuid   : sysuuid_x16 not null;

  // Parent
  case_uuid           : sysuuid_x16 not null;

  // Evidence classification per §3.2 taxonomy:
  //   DISP_EXCL | STRONG_DISC | WEAK_DISC | NEUTRAL |
  //   WEAK_CORR | STRONG_CORR | DISP_CONF
  category            : abap.char(20);

  // What data element was compared
  data_element        : abap.char(60);

  // Side-by-side values
  bp_value            : abap.char(500);
  spl_value           : abap.char(500);

  // Agent's assessment of this evidence item
  assessment          : abap.string(0);

  // §3.1 principle 3: missing data is neutral, never clears a hit
  // TRUE = data was available for comparison
  // FALSE = data unavailable (renders distinctly in UI per §6.4)
  data_available      : abap_boolean;

  // Display ordering (dispositive items first)
  sort_order          : abap.int4;

  // Source traceability
  source_system       : abap.char(20);
  source_field        : abap.char(60);

}
