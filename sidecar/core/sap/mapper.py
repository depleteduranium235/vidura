"""
Map GTS screening records onto the adjudication engine's input contract.

This is the seam between "what SAP gives us" and "what the evidence engine
expects", so the engine stays testable without SAP and the SAP field names
stay confined to core/sap/.

§5.5 applies here: send the sidecar a minimised evidence payload, not a BP
extract. Bank account numbers and unrelated master data are deliberately
not carried across — only the screening status of associated banks, which
is what §3.1 #8 actually needs.
"""

from __future__ import annotations

from ..models.schemas import HitInput, IntakePath, ListSeverity
from .schemas import ScreenedPartnerAddress, SplHitDetail

# Blocking regimes require more evidence to clear (§3.1 #7). GTS carries the
# list type on the hit; this maps it to the severity the band engine gates on.
# Extend as AGP's configured list types become known — the default is
# deliberately BLOCKING so an unrecognised list is never treated as lenient.
BLOCKING_LIST_TYPES = {"SDN", "SDNL", "DPL", "EL", "UVL", "MEU", "NS-MBS", "CAP"}
ADVISORY_LIST_TYPES = {"ADV", "WATCH", "WL"}


def list_severity_for(spl_list_type: str) -> ListSeverity:
    """
    Resolve GTS list type to severity.

    Unknown types fall through to BLOCKING. §3.1 #1 puts the burden of proof
    on exclusion, so an unclassified list must not lower the evidence bar.
    """
    key = (spl_list_type or "").strip().upper()
    if key in ADVISORY_LIST_TYPES:
        return ListSeverity.ADVISORY
    return ListSeverity.BLOCKING


def case_id_for(record: ScreenedPartnerAddress, hit: SplHitDetail) -> str:
    """
    Deterministic case ID per BP-address ↔ SPL-entry pair.

    Same inputs always produce the same ID so reprocessing is idempotent
    (§5.6 #4) and the precedent store can match on it.
    """
    return f"{record.case_key}::{hit.spl_list_type}::{hit.spl_entity}"


def to_hit_inputs(record: ScreenedPartnerAddress) -> list[HitInput]:
    """
    Explode one screening record into one HitInput per matched SPL entry.

    A single blocked address can match several entries, and each is a separate
    adjudication — §3.1 #4's non-compensatory logic only makes sense per pair.
    """
    return [_to_hit_input(record, hit) for hit in record.hits]


def _date_only(value) -> str:
    """
    Render a date without a time component.

    A DOB with a spurious 00:00:00 invites the model to treat precision it
    doesn't have as meaningful.
    """
    return value.date().isoformat() if value else ""


def _to_hit_input(record: ScreenedPartnerAddress, hit: SplHitDetail) -> HitInput:
    master = record.master
    return HitInput(
        case_id=case_id_for(record, hit),
        bp_id=record.business_partner,
        bp_name=record.partner_name or record.person_full_name,
        bp_country=record.country,
        bp_entity_type=record.entity_type,
        bp_registration_no=_primary_registration(record),
        # §4.1 discriminators — only present when BP master was fetched.
        bp_date_of_birth=_date_only(master.birth_date) if master else "",
        bp_birthplace=master.birthplace if master else "",
        bp_nationality=master.nationality if master else "",
        bp_name_at_birth=master.birth_name if master else "",
        bp_foundation_date=_date_only(master.foundation_date) if master else "",
        bp_industry=master.industry if master else "",
        bp_all_identifiers=all_identifiers(record),
        spl_entry_id=hit.spl_entity,
        # The entry's name and address DO come through, carried as HTML inside
        # MatchedName / MatchedAddress. Aliases, DOB, nationality, identifiers
        # and remarks still need a Z CDS view over /SAPSLL/TSPL*.
        spl_entry_name=hit.spl_entry_name,
        spl_entry_address=hit.spl_entry_address,
        spl_list_type=hit.spl_list_type,
        spl_programme=hit.spl_group_desc or hit.spl_group,
        spl_entity_type="",
        # GTS does not expose a similarity score on this service. 0.0 is a
        # deliberate "not reported" — never render it as a confidence (§6.4).
        match_percentage=0.0,
        match_basis=hit.match_basis,
        intake_path=IntakePath.BP_BLOCK,
        list_severity=list_severity_for(hit.spl_list_type),
        order_value=0.0,
        order_currency="",
    )


# Identifier preference, most globally-discriminating first (§3.2 — a matching
# registration number, passport, or LEI is dispositive confirmation).
#
# Matched against the identification type CODE and its TEXT together, because
# SAP's codes are opaque (BUP002, BUP003...) and configuration-dependent, while
# the text is meaningful. The text is language-dependent, so this relies on the
# service being called with sap-language=EN.
_ID_PREFERENCE: tuple[tuple[str, ...], ...] = (
    ("LEI",),
    ("COMMERCIAL", "REGISTER", "TRADE REG", "HRB"),
    ("VAT",),
    ("TAX",),
    ("PASSPORT", "NATIONAL ID", "NATIONAL IDENTIF"),
    ("DUNS",),
)


def _primary_registration(record: ScreenedPartnerAddress) -> str:
    """
    Pick the single most discriminating identifier for HitInput.bp_registration_no.

    Iterates preference-first, not record-first, so a commercial register number
    wins over a VAT number regardless of the order SAP returns them in.
    """
    if not record.identifications:
        return ""

    for group in _ID_PREFERENCE:
        for ident in record.identifications:
            haystack = f"{ident.id_type} {ident.id_type_text}".upper()
            if any(term in haystack for term in group):
                return _format_identifier(ident)

    return _format_identifier(record.identifications[0])


def _format_identifier(ident) -> str:
    label = ident.id_type_text or ident.id_type
    return f"{ident.id_number} ({label})" if label else ident.id_number


def all_identifiers(record: ScreenedPartnerAddress) -> list[str]:
    """
    Every identifier on the BP, formatted for the extractor prompt.

    §3.2 makes a match on *any* identifier dispositive, so the model needs the
    full set rather than just the one chosen for bp_registration_no.
    """
    return [
        _format_identifier(ident) + (f" [{ident.country}]" if ident.country else "")
        for ident in record.identifications
    ]


def network_evidence_notes(record: ScreenedPartnerAddress) -> list[str]:
    """
    Pre-resolved network evidence for the extractor prompt (§3.1 #8).

    GTS computes these associations itself, so they arrive as facts rather
    than inferences — worth passing through verbatim so the model classifies
    them rather than rediscovering them.
    """
    notes: list[str] = []

    if record.block_is_indirect:
        notes.append(
            "GTS flags this as an INDIRECT block: the address is blocked via an "
            "association, not a direct name match against this partner."
        )

    if record.has_blocked_associated_bank:
        notes.append("GTS flags this partner as having a blocked associated bank.")

    for obj in record.associated_blocked_objects:
        loc = ", ".join(p for p in (obj.city, obj.country_name) if p)
        notes.append(
            f"Associated blocked object: {obj.partner_name or obj.business_partner}"
            + (f" ({loc})" if loc else "")
            + (f" — status {obj.screening_status_text}" if obj.screening_status_text else "")
            + (f", type {obj.block_object_type_text}" if obj.block_object_type_text else "")
        )

    for bank in record.associated_banks:
        if not bank.screening_status:
            continue
        notes.append(
            f"Associated bank: {bank.partner_name or bank.bank_business_partner}"
            + (f" ({bank.country_name})" if bank.country_name else "")
            + f" — screening status {bank.screening_status_text or bank.screening_status}"
        )

    for obj in record.associated_blocked_objects:
        if obj.exclusion_category:
            notes.append(
                f"{obj.partner_name or obj.business_partner} carries exclusion "
                f"category (positive/negative list): {obj.exclusion_category}"
            )

    return notes
