"""
Vidura sidecar configuration.

Prompts and evidence taxonomy are controlled artifacts (§8) — they need
version control, documented change approval, and regression testing.
"""

import os
from pathlib import Path

BAND_LOGIC_VERSION = "v1.0.0"
TAXONOMY_VERSION = "v1.0.0"
PROMPT_VERSION = "v1.0.0"

# PwC shared services gateway (LiteLLM → Bedrock)
ANTHROPIC_BASE_URL = os.environ.get(
    "ANTHROPIC_BASE_URL",
    "https://genai-sharedservice-americas.pwcinternal.com",
)
MODEL_ID = os.environ.get(
    "VIDURA_MODEL_ID",
    "bedrock.anthropic.claude-sonnet-4-6",
)

# Corporate SSL cert bundle
CORP_CERT_BUNDLE = Path.home() / ".claude" / "certs" / "corporate-ca-bundle.pem"

MATERIALITY_THRESHOLD_USD = 100_000

THIN_FILE_THRESHOLD = 3
