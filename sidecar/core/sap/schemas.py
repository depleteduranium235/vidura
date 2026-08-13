"""
Pydantic models for the GTS read payload.

Field names mirror LLS_BPADDR_MNG_SRV exactly (OData V2, SAP-delivered,
app F4542A "Manage Business Partners - SPL Screening"). Keeping the SAP
names verbatim means a metadata diff after an upgrade shows up as a
validation error rather than silently reading the wrong field.

Entity model:
    C_SPLScrngScreenedPrtnAddress          <- root, what we poll
      to_SPLHitsDetailSet                  -> SPLHitsDetail       (the hits)
      to_SPLScrngBPIdentification          -> identification numbers
      to_SPLScrngBPAssocdBlkdObj           -> other blocked objects on this BP
      to_SPLScrngBPAssociatedBank          -> associated banks
      to_RefBusinessPartnerExt             -> I_BusinessPartner (full master)
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Optional
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

# OData V2 serialises Edm.DateTime/DateTimeOffset in Microsoft JSON format:
#   /Date(1786579200000)/          ms since epoch, UTC
#   /Date(1786579200000+0120)/     with an offset expressed in MINUTES
# Not ISO 8601, so it needs decoding before Pydantic sees it.
_SAP_DATE_RE = re.compile(r"^/Date\((-?\d+)([+-]\d+)?\)/$")


def _parse_sap_datetime(value: Any) -> Any:
    """Decode a V2 date literal; pass anything else through to Pydantic."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        match = _SAP_DATE_RE.match(value.strip())
        if match:
            dt = datetime.fromtimestamp(int(match.group(1)) / 1000, tz=timezone.utc)
            if match.group(2):
                minutes = int(match.group(2))  # signed, already in minutes
                dt += timedelta(minutes=minutes)
            return dt
    return value


SapDateTime = Annotated[Optional[datetime], BeforeValidator(_parse_sap_datetime)]


class _SapModel(BaseModel):
    """Tolerates unmapped SAP fields; the service returns far more than we consume."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class SplHitDetail(_SapModel):
    """
    One BP-address ↔ SPL-entry match. Entity set: SPLHitsDetailSet.

    Note there is no similarity percentage here — the service exposes only
    *what* matched (name / address / ID), not how closely. Per §3.1 #6 that
    match basis is the more informative signal anyway; the numeric score
    stays in /SAPSLL/SPLATM and is not read for MVP.
    """

    address_id: str = Field(alias="AddressID")
    item_number: str = Field(alias="ItemNumber")
    legal_regulation: str = Field(alias="LegalRegulation")
    business_partner: str = Field(alias="BusinessPartner")

    spl_list_type: str = Field(alias="SPLListType")
    spl_list_type_desc: str = Field(alias="SPLListTypeDesc", default="")

    # The SPL entry key ("SPL Number"). This is the pointer into the
    # /SAPSLL/TSPL* family for entry content.
    spl_entity: str = Field(alias="SPLEntity")

    # Match basis flags, not scores.
    matched_name: str = Field(alias="MatchedName", default="")
    matched_address: str = Field(alias="MatchedAddress", default="")
    matched_id: str = Field(alias="MatchedId", default="")

    gts_data_provider: str = Field(alias="GTSDataProvider", default="")
    gts_data_provider_desc: str = Field(alias="GTSDataProviderDesc", default="")
    spl_group: str = Field(alias="SPLGroup", default="")
    spl_group_desc: str = Field(alias="SPLGroupDesc", default="")

    @property
    def match_basis(self) -> str:
        """Human-readable match basis for the UI's MatchBasis column."""
        parts = []
        if self.matched_name:
            parts.append("name")
        if self.matched_address:
            parts.append("address")
        if self.matched_id:
            parts.append("identifier")
        if not parts:
            return "Match basis not reported by GTS"
        return f"Matched on {' + '.join(parts)}"

    @property
    def has_identifier_match(self) -> bool:
        """
        An ID match is the only dispositive confirmation GTS gives us
        directly (§3.2). Surfaced so the extractor can weight it.
        """
        return bool(self.matched_id)


class BpIdentification(_SapModel):
    """Tax / VAT / registration numbers (§4.2). Entity set: C_SPLScrngBPIdentification."""

    business_partner: str = Field(alias="BusinessPartner")
    id_type: str = Field(alias="BPIdentificationType")
    id_number: str = Field(alias="BPIdentificationNumber")
    id_type_text: str = Field(alias="BPIdentificationTypeText", default="")
    country: str = Field(alias="Country", default="")
    country_name: str = Field(alias="CountryShortName", default="")


class AssociatedBlockedObject(_SapModel):
    """
    Another blocked object linked to this BP — §3.1 #8 network evidence,
    delivered by GTS rather than derived by us.
    Entity set: C_SPLScrngBPAssocdBlkdObject.
    """

    business_partner: str = Field(alias="BusinessPartner1", default="")
    legal_regulation: str = Field(alias="LegalRegulation", default="")
    address_id: str = Field(alias="BusinessPartnerAddressID", default="")
    partner_name: str = Field(alias="BusinessPartnerName", default="")
    street: str = Field(alias="StreetName", default="")
    city: str = Field(alias="CityName", default="")
    country: str = Field(alias="Country", default="")
    country_name: str = Field(alias="CountryShortName", default="")
    screening_status: str = Field(alias="SPLScreeningCheckStatus", default="")
    screening_status_text: str = Field(alias="SPLScreeningCheckStatus_Text", default="")
    block_object_type: str = Field(alias="SPLScrngBPIndrctBlkObjType", default="")
    block_object_type_text: str = Field(alias="SPLScrngBPIndrctBlkObjType_Text", default="")
    exclusion_category: str = Field(alias="SPLScreeningExclusionCategory", default="")


class AssociatedBank(_SapModel):
    """Bank linked to this BP, with its own screening status (§3.1 #8)."""

    bank_business_partner: str = Field(alias="BankBusinessPartner", default="")
    partner_name: str = Field(alias="BusinessPartnerName", default="")
    street: str = Field(alias="StreetName", default="")
    city: str = Field(alias="CityName", default="")
    country: str = Field(alias="Country", default="")
    country_name: str = Field(alias="CountryShortName", default="")
    screening_status: str = Field(alias="SPLScreeningCheckStatus", default="")
    screening_status_text: str = Field(alias="SPLScreeningCheckStatus_Text", default="")
    processing_status: str = Field(alias="SPLScrngBlockProcessingStatus", default="")
    exclusion_category: str = Field(alias="SPLScreeningExclusionCategory", default="")


class ScreenedPartnerAddress(_SapModel):
    """
    Root entity: C_SPLScrngScreenedPrtnAddress.

    One record per (BP address × legal regulation) screening result — note the
    screening unit is the *address*, not the partner, so one BP with three
    addresses yields three records.
    """

    # Composite key
    legal_regulation: str = Field(alias="LegalRegulation")
    address_id: str = Field(alias="AddressID")
    business_partner: str = Field(alias="BusinessPartner")
    logical_system_group: str = Field(alias="LogicalSystemGroup")
    gts_bp_type: str = Field(alias="GTSBusinessPartnerType")
    gts_bp_external_id: str = Field(alias="GTSBusinessPartnerExternalID")
    foreign_trade_org: str = Field(alias="ForeignTradeOrganization", default="")

    # Stable identity for the case (§5.6 #4 — reprocessing must be safe)
    spl_audit_trail_uuid: Optional[UUID] = Field(alias="SPLAuditTrailUUID", default=None)
    business_partner_uuid: Optional[UUID] = Field(alias="BusinessPartnerUUID", default=None)

    # Block state
    is_blocked: bool = Field(alias="SPLScreenedAddressIsBlocked", default=False)
    block_is_indirect: bool = Field(alias="SPLScreeningBlockIsIndirect", default=False)
    has_blocked_associated_bank: bool = Field(alias="SPLScrngBPHasBlkdAssocdBank", default=False)

    screening_status: str = Field(alias="SPLScreeningCheckStatus", default="")
    screening_status_text: str = Field(alias="SPLScreeningCheckStatus_Text", default="")
    processing_status: str = Field(alias="SPLScrngBlockProcessingStatus", default="")
    processing_status_desc: str = Field(alias="CstmsCmplncBlkProcgStatusDesc", default="")
    block_processor: str = Field(alias="SPLScreeningBlockProcessor", default="")

    # GTS's own decisions — distinct from our recommendation, which is why
    # the agent writes to the Z table and never here (§5.4).
    system_decision: str = Field(alias="SPLScrngResultSystemDecision", default="")
    user_decision: str = Field(alias="SPLScreeningResultUserDecision", default="")
    release_reason: str = Field(alias="CstmsCmplncBlockReleaseReason", default="")
    release_reason_text: str = Field(alias="CstmsCmplncBlockReleaseReason_Text", default="")
    process_comment: str = Field(alias="ProcessComment", default="")

    # Timestamps — SPLCheckDateTime is the watermark field (§5.6 #5)
    spl_check_datetime: SapDateTime = Field(alias="SPLCheckDateTime", default=None)
    first_screening_datetime: SapDateTime = Field(alias="FirstSPLScreeningDateTime", default=None)
    created_at: SapDateTime = Field(alias="CreationUTCDateTime", default=None)
    changed_at: SapDateTime = Field(alias="LastChangeDateTime", default=None)
    created_by: str = Field(alias="CreatedByUser", default="")
    changed_by: str = Field(alias="LastChangedByUser", default="")

    # BP identity and address
    partner_name: str = Field(alias="BusinessPartnerName", default="")
    person_full_name: str = Field(alias="PersonFullName", default="")
    care_of_name: str = Field(alias="CareOfName", default="")
    street: str = Field(alias="StreetName", default="")
    city: str = Field(alias="CityName", default="")
    country: str = Field(alias="Country", default="")
    country_name: str = Field(alias="Country_Text", default="")
    region: str = Field(alias="Region", default="")
    region_name: str = Field(alias="RegionName", default="")
    search_term_1: str = Field(alias="SearchTerm1", default="")
    search_term_2: str = Field(alias="SearchTerm2", default="")
    name_and_address: str = Field(alias="BPCnctntdNameAndAddrTxt", default="")

    gts_bp_role: str = Field(alias="GTSBusinessPartnerRole", default="")
    legal_regulation_name: str = Field(alias="LegalRegulationName", default="")

    # Expanded children (populated when $expand is used)
    hits: list[SplHitDetail] = Field(default_factory=list)
    identifications: list[BpIdentification] = Field(default_factory=list)
    associated_blocked_objects: list[AssociatedBlockedObject] = Field(default_factory=list)
    associated_banks: list[AssociatedBank] = Field(default_factory=list)

    @property
    def case_key(self) -> str:
        """
        Stable, deterministic key for this screening result (§5.6 #4).

        Prefers the audit trail GUID. Falls back to the composite key when
        the GUID is absent, so reprocessing is still idempotent.
        """
        if self.spl_audit_trail_uuid:
            return str(self.spl_audit_trail_uuid)
        return "|".join([
            self.logical_system_group,
            self.gts_bp_type,
            self.gts_bp_external_id,
            self.business_partner,
            self.address_id,
            self.legal_regulation,
        ])

    @property
    def full_address(self) -> str:
        parts = [p for p in (self.street, self.city, self.region_name, self.country_name) if p]
        return ", ".join(parts)

    @property
    def entity_type(self) -> str:
        """
        Natural person vs legal entity — the most common dispositive
        exclusion (§3.2). GTS carries this as the BP category; PersonFullName
        is only populated for natural persons.
        """
        if self.person_full_name:
            return "Natural Person"
        if self.gts_bp_type:
            return f"Legal Entity ({self.gts_bp_type})"
        return ""
