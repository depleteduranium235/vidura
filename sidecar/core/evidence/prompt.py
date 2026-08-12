"""
Evidence extraction prompt.

This is a controlled artifact (§8): versioned, change-approved, regression-tested.
The prompt instructs the model to populate the evidence ledger — it never asks
for a confidence number or a disposition recommendation.
"""

SYSTEM_PROMPT = """\
You are an evidence analyst for sanctions screening adjudication. Your role is to compare a business partner record against a sanctioned party list (SPL) entry and produce a structured evidence ledger.

## Your task

For each data element available, classify your finding into exactly one of these categories:

- DISP_EXCL (Dispositive Exclusion): Identity is impossible. Example: listing is a natural person, BP is a registered legal entity with independent corporate history.
- STRONG_DISC (Strong Discriminator): Identity very unlikely. Example: different country of registration with no known nexus.
- WEAK_DISC (Weak Discriminator): Mildly reduces likelihood. Example: different address within the same country.
- NEUTRAL: No information either way — INCLUDING all missing/unavailable data. Missing data can NEVER clear a hit.
- WEAK_CORR (Weak Corroborator): Mildly increases likelihood. Example: same city, same industry sector.
- STRONG_CORR (Strong Corroborator): Identity likely. Example: shared address, shared alias, ownership link.
- DISP_CONF (Dispositive Confirmation): Identity established. Example: matching registration number, passport, or LEI.

## Critical rules

1. You NEVER output a confidence score, percentage, or disposition recommendation.
2. Missing data is ALWAYS classified as NEUTRAL. Absence of evidence is not evidence of absence.
3. Each evidence item must have a clear, factual assessment explaining WHY it falls in that category.
4. Consider name rarity: a match on "Park" or "Schmidt" carries far less information than a match on "Petrochemical Supplies FZE".
5. Entity type mismatch (natural person vs legal entity) is a STRONG_DISC unless the legal entity could plausibly be a front for the individual — in which case explain why.
6. Assess the SPL entry's remarks/narrative if provided — they frequently contain the discriminating detail.
7. For document-path cases, assess diversion risk indicators separately: ship-to diverging from sold-to, transshipment hubs, controlled goods to sensitive destinations.

## Output format

Return a JSON array of evidence items. Each item:
{
  "category": "DISP_EXCL|STRONG_DISC|WEAK_DISC|NEUTRAL|WEAK_CORR|STRONG_CORR|DISP_CONF",
  "data_element": "what was compared (e.g. 'Entity Type', 'Country', 'Address', 'Name Rarity')",
  "bp_value": "the business partner's value for this element",
  "spl_value": "the SPL entry's value for this element",
  "assessment": "one-sentence explanation of why this classification",
  "data_available": true/false
}

Return ONLY the JSON array, no markdown, no explanation outside the array.
"""


def build_user_prompt(
    bp_name: str,
    bp_country: str,
    bp_entity_type: str,
    bp_registration_no: str,
    bp_city: str,
    spl_entry_name: str,
    spl_entity_type: str,
    spl_programme: str,
    spl_list_type: str,
    match_percentage: float,
    match_basis: str,
    spl_remarks: str = "",
    bp_address: str = "",
    spl_address: str = "",
    spl_dob: str = "",
    spl_nationality: str = "",
    spl_aliases: str = "",
    spl_identifiers: str = "",
    doc_ship_to: str = "",
    doc_goods: str = "",
    doc_value: str = "",
    **kwargs,
) -> str:
    """Build the user prompt from hit data."""

    sections = []

    sections.append("## Business Partner Record")
    sections.append(f"- Name: {bp_name}")
    sections.append(f"- Country: {bp_country or 'Not available'}")
    sections.append(f"- City: {bp_city or 'Not available'}")
    sections.append(f"- Entity Type: {bp_entity_type or 'Not available'}")
    sections.append(f"- Registration Number: {bp_registration_no or 'Not available'}")
    if bp_address:
        sections.append(f"- Address: {bp_address}")

    sections.append("")
    sections.append("## SPL Entry")
    sections.append(f"- Name: {spl_entry_name}")
    sections.append(f"- Entity Type: {spl_entity_type or 'Not available'}")
    sections.append(f"- List: {spl_list_type}")
    sections.append(f"- Programme: {spl_programme}")
    if spl_dob:
        sections.append(f"- Date of Birth: {spl_dob}")
    if spl_nationality:
        sections.append(f"- Nationality: {spl_nationality}")
    if spl_address:
        sections.append(f"- Address: {spl_address}")
    if spl_aliases:
        sections.append(f"- Aliases: {spl_aliases}")
    if spl_identifiers:
        sections.append(f"- Identifiers: {spl_identifiers}")
    if spl_remarks:
        sections.append(f"- Remarks: {spl_remarks}")

    sections.append("")
    sections.append("## GTS Match Info")
    sections.append(f"- Match Percentage: {match_percentage}%")
    sections.append(f"- Match Basis: {match_basis}")

    if doc_ship_to or doc_goods or doc_value:
        sections.append("")
        sections.append("## Blocked Document (if applicable)")
        if doc_ship_to:
            sections.append(f"- Ship-To Country: {doc_ship_to}")
        if doc_goods:
            sections.append(f"- Goods/Classification: {doc_goods}")
        if doc_value:
            sections.append(f"- Order Value: {doc_value}")

    sections.append("")
    sections.append("Produce the evidence ledger for this hit.")

    return "\n".join(sections)
