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

import html
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

# Epoch arithmetic, deliberately not datetime.fromtimestamp(): that raises
# OSError on Windows for negative values, and pre-1970 dates are routine here
# — any date of birth before 1970 would crash the reader.
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _parse_sap_datetime(value: Any) -> Any:
    """Decode a V2 date literal; pass anything else through to Pydantic."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        match = _SAP_DATE_RE.match(value.strip())
        if match:
            dt = _EPOCH + timedelta(milliseconds=int(match.group(1)))
            if match.group(2):
                minutes = int(match.group(2))  # signed, already in minutes
                dt += timedelta(minutes=minutes)
            return dt
    return value


SapDateTime = Annotated[Optional[datetime], BeforeValidator(_parse_sap_datetime)]


class _SapModel(BaseModel):
    """Tolerates unmapped SAP fields; the service returns far more than we consume."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


def strip_highlight(value: str) -> str:
    """
    Plain text from a GTS match field.

    GTS returns matched SPL text as HTML with <strong> around the tokens that
    matched and <br> between lines, e.g.
        '<strong>Baring</strong> <strong>Buyeers</strong><br>'
    """
    if not value:
        return ""
    text = re.sub(r"<br\s*/?>", " ", value, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace(";", " ")
    # GTS pads multi-line fields, so collapse runs of whitespace
    return re.sub(r"\s+", " ", text).strip(" ,")


def highlighted_tokens(value: str) -> list[str]:
    """
    The tokens GTS marked as matching, i.e. the contents of each <strong>.

    This is the match basis at token level (§3.1 #6) — knowing that only a
    common surname matched is very different from a full distinctive string.
    """
    if not value:
        return []
    return [html.unescape(t).strip() for t in re.findall(r"<strong>(.*?)</strong>", value, re.I) if t.strip()]


class SplHitDetail(_SapModel):
    """
    One BP-address ↔ SPL-entry match. Entity set: SPLHitsDetailSet.

    MatchedName / MatchedAddress are NOT booleans — they carry the sanctioned
    party's own name and address as HTML, with <strong> around the matched
    tokens. That is the only SPL entry *content* available over OData, so it is
    parsed rather than merely tested for emptiness.

    Still absent: aliases, DOB, nationality, identifiers and remarks (§4.1),
    which need a Z CDS view over /SAPSLL/TSPL*. And no similarity percentage —
    that stays in /SAPSLL/SPLATM.
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

    # The SPL entry's own name / address, HTML-highlighted. Raw form retained
    # so the highlight markers stay available.
    matched_name: str = Field(alias="MatchedName", default="")
    matched_address: str = Field(alias="MatchedAddress", default="")
    matched_id: str = Field(alias="MatchedId", default="")

    gts_data_provider: str = Field(alias="GTSDataProvider", default="")
    gts_data_provider_desc: str = Field(alias="GTSDataProviderDesc", default="")
    spl_group: str = Field(alias="SPLGroup", default="")
    spl_group_desc: str = Field(alias="SPLGroupDesc", default="")

    @property
    def spl_entry_name(self) -> str:
        """The sanctioned party's name, plain text."""
        return strip_highlight(self.matched_name)

    @property
    def spl_entry_address(self) -> str:
        """The sanctioned party's address, plain text."""
        return strip_highlight(self.matched_address)

    @property
    def matched_name_tokens(self) -> list[str]:
        """Which name tokens GTS considered matching (§3.1 #6)."""
        return highlighted_tokens(self.matched_name)

    @property
    def matched_address_tokens(self) -> list[str]:
        return highlighted_tokens(self.matched_address)

    @property
    def match_basis(self) -> str:
        """
        Match basis for the UI's MatchBasis column, including which tokens
        matched — "matched on name" says far less than naming the tokens, and
        §3.1 #6 makes token rarity part of the assessment.
        """
        parts = []
        if self.matched_name:
            tokens = self.matched_name_tokens
            parts.append(f"name ({', '.join(tokens)})" if tokens else "name")
        if self.matched_address:
            tokens = self.matched_address_tokens
            parts.append(f"address ({', '.join(tokens)})" if tokens else "address")
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


class BusinessPartnerMaster(_SapModel):
    """
    Subset of I_BusinessPartner (81 props) carrying §4.1's highest-value
    discriminators. Entity set: I_BusinessPartner.

    Not reachable by navigation from the screening root — to_RefBusinessPartnerExt
    only reaches the external-ID entity — so this needs a separate keyed GET.
    """

    business_partner: str = Field(alias="BusinessPartner")

    # SAP BP category is the authoritative person/organisation flag:
    #   1 = Person, 2 = Organization, 3 = Group
    category: str = Field(alias="BusinessPartnerCategory", default="")
    bp_type: str = Field(alias="BusinessPartnerType", default="")

    # Natural-person discriminators (§4.1)
    first_name: str = Field(alias="FirstName", default="")
    last_name: str = Field(alias="LastName", default="")
    birth_date: SapDateTime = Field(alias="BirthDate", default=None)
    birth_date_status: str = Field(alias="BusinessPartnerBirthDateStatus", default="")
    birth_name: str = Field(alias="BusinessPartnerBirthName", default="")
    birthplace: str = Field(alias="BusinessPartnerBirthplaceName", default="")
    nationality: str = Field(alias="BusPartNationality", default="")

    # Legal-entity discriminators
    foundation_date: SapDateTime = Field(alias="OrganizationFoundationDate", default=None)
    industry: str = Field(alias="Industry", default="")

    is_centrally_blocked: bool = Field(alias="BusinessPartnerIsBlocked", default=False)

    @property
    def is_natural_person(self) -> Optional[bool]:
        """
        True for a person, False for an organisation, None when SAP hasn't
        classified it. None matters: §3.1 #3 makes unknown data neutral, so an
        unclassified BP must not be treated as either.
        """
        if self.category == "1":
            return True
        if self.category in ("2", "3"):
            return False
        return None

    @property
    def entity_type(self) -> str:
        """Authoritative entity type, from BP category rather than inference."""
        return {
            "1": "Natural Person",
            "2": "Legal Entity (Organization)",
            "3": "Group",
        }.get(self.category, "")


# Explicit $select list — declared outside the class so it isn't mistaken for
# a model field.
BP_MASTER_SELECT: tuple[str, ...] = (
    "BusinessPartner",
    "BusinessPartnerCategory",
    "BusinessPartnerType",
    "FirstName",
    "LastName",
    "BirthDate",
    "BusinessPartnerBirthDateStatus",
    "BusinessPartnerBirthName",
    "BusinessPartnerBirthplaceName",
    "BusPartNationality",
    "OrganizationFoundationDate",
    "Industry",
    "BusinessPartnerIsBlocked",
)


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

    # Attached by a separate keyed GET, since I_BusinessPartner is not
    # navigable from this entity. None when enrichment is skipped or fails —
    # never a fabricated stand-in.
    master: Optional[BusinessPartnerMaster] = None

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
        exclusion (§3.2).

        Prefers BusinessPartnerCategory from BP master, which is authoritative.
        Falls back to inferring from PersonFullName only when master data
        wasn't fetched, and returns "" rather than guessing when neither is
        available — an unknown entity type must stay neutral (§3.1 #3).
        """
        if self.master is not None and self.master.entity_type:
            return self.master.entity_type
        if self.person_full_name:
            return "Natural Person"
        if self.gts_bp_type:
            return f"Legal Entity ({self.gts_bp_type})"
        return ""
