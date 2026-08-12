"""
Evidence extraction via LLM.

The extractor takes raw hit data (BP + SPL record) and produces a structured
evidence ledger. This is where the LLM earns its keep — reading listing
narratives, resolving entity type, assessing nexus plausibility, parsing
free-text remarks.

The model never emits a confidence number (§3.3). It outputs classified
evidence items with justifications. The band engine then maps the ledger
to a disposition.
"""

from ..models.schemas import EvidenceItem, EvidenceLedger, HitInput


async def extract_evidence(hit: HitInput) -> EvidenceLedger:
    """
    Extract evidence from a hit input using the LLM.

    TODO: Replace stub with actual Anthropic API call.
    The prompt instructs the model to:
    1. Compare BP data against SPL record field by field
    2. Classify each comparison into the 7-band taxonomy
    3. Explicitly mark unavailable data as NEUTRAL
    4. Never emit a confidence number
    5. Return structured JSON matching EvidenceItem schema
    """
    raise NotImplementedError(
        "LLM extraction not yet implemented. "
        "Use mock data or call extract_evidence_mock() for testing."
    )


def extract_evidence_mock(hit: HitInput) -> EvidenceLedger:
    """
    Mock implementation for testing the pipeline without LLM calls.
    Returns a minimal evidence ledger based on the hit input.
    """
    from ..models.schemas import EvidenceCategory

    items = []

    if hit.bp_entity_type and hit.spl_entity_type:
        if hit.bp_entity_type != hit.spl_entity_type:
            items.append(EvidenceItem(
                category=EvidenceCategory.STRONG_DISCRIMINATOR,
                data_element="Entity Type",
                bp_value=hit.bp_entity_type,
                spl_value=hit.spl_entity_type,
                assessment=f"BP is {hit.bp_entity_type}; SPL entry is {hit.spl_entity_type}.",
                data_available=True,
            ))
        else:
            items.append(EvidenceItem(
                category=EvidenceCategory.WEAK_CORROBORATOR,
                data_element="Entity Type",
                bp_value=hit.bp_entity_type,
                spl_value=hit.spl_entity_type,
                assessment="Same entity type.",
                data_available=True,
            ))

    if hit.bp_country:
        items.append(EvidenceItem(
            category=EvidenceCategory.NEUTRAL,
            data_element="Country",
            bp_value=hit.bp_country,
            spl_value=hit.spl_programme,
            assessment="Country comparison requires deeper analysis.",
            data_available=True,
        ))

    return EvidenceLedger(items=items)
