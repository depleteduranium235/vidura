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

import json
import os
import time
from typing import Optional

import httpx
from anthropic import Anthropic

from ..config import MODEL_ID, PROMPT_VERSION, ANTHROPIC_BASE_URL, CORP_CERT_BUNDLE
from ..models.schemas import (
    EvidenceCategory,
    EvidenceItem,
    EvidenceLedger,
    HitInput,
)
from .prompt import SYSTEM_PROMPT, build_user_prompt


async def extract_evidence(
    hit: HitInput,
    client: Optional[Anthropic] = None,
    spl_remarks: str = "",
    spl_address: str = "",
    spl_dob: str = "",
    spl_nationality: str = "",
    spl_aliases: str = "",
    spl_identifiers: str = "",
    bp_address: str = "",
    doc_ship_to: str = "",
    doc_goods: str = "",
    doc_value: str = "",
) -> tuple[EvidenceLedger, int]:
    """
    Extract evidence from a hit input using Claude.

    Returns (evidence_ledger, elapsed_ms).
    """
    if client is None:
        ssl_verify: bool | str = True
        if os.environ.get("VIDURA_SSL_VERIFY", "1") == "0":
            ssl_verify = False
        elif CORP_CERT_BUNDLE.exists():
            ssl_verify = str(CORP_CERT_BUNDLE)

        http_client = httpx.Client(verify=ssl_verify)
        client = Anthropic(
            base_url=ANTHROPIC_BASE_URL,
            http_client=http_client,
        )

    user_prompt = build_user_prompt(
        bp_name=hit.bp_name,
        bp_country=hit.bp_country,
        bp_entity_type=hit.bp_entity_type,
        bp_registration_no=hit.bp_registration_no,
        bp_city="",
        spl_entry_name=hit.spl_entry_name,
        spl_entity_type=hit.spl_entity_type,
        spl_programme=hit.spl_programme,
        spl_list_type=hit.spl_list_type,
        match_percentage=hit.match_percentage,
        match_basis=hit.match_basis,
        spl_remarks=spl_remarks,
        spl_address=spl_address,
        spl_dob=spl_dob,
        spl_nationality=spl_nationality,
        spl_aliases=spl_aliases,
        spl_identifiers=spl_identifiers,
        bp_address=bp_address,
        doc_ship_to=doc_ship_to,
        doc_goods=doc_goods,
        doc_value=doc_value,
    )

    start = time.perf_counter_ns()

    response = client.messages.create(
        model=MODEL_ID,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    elapsed_ms = (time.perf_counter_ns() - start) // 1_000_000

    raw_text = response.content[0].text.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    items_data = json.loads(raw_text)

    items = []
    for item_data in items_data:
        items.append(EvidenceItem(
            category=EvidenceCategory(item_data["category"]),
            data_element=item_data["data_element"],
            bp_value=item_data.get("bp_value", ""),
            spl_value=item_data.get("spl_value", ""),
            assessment=item_data["assessment"],
            data_available=item_data.get("data_available", True),
        ))

    return EvidenceLedger(items=items), elapsed_ms


def extract_evidence_mock(hit: HitInput) -> EvidenceLedger:
    """
    Mock implementation for testing the pipeline without LLM calls.
    Returns a minimal evidence ledger based on the hit input.
    """
    items = []

    if hit.bp_entity_type and hit.spl_entity_type:
        if hit.bp_entity_type.lower() != hit.spl_entity_type.lower():
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
