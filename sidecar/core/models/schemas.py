from enum import Enum
from typing import Optional
from uuid import UUID, uuid4
from datetime import datetime

from pydantic import BaseModel, Field


class EvidenceCategory(str, Enum):
    DISPOSITIVE_EXCLUSION = "DISP_EXCL"
    STRONG_DISCRIMINATOR = "STRONG_DISC"
    WEAK_DISCRIMINATOR = "WEAK_DISC"
    NEUTRAL = "NEUTRAL"
    WEAK_CORROBORATOR = "WEAK_CORR"
    STRONG_CORROBORATOR = "STRONG_CORR"
    DISPOSITIVE_CONFIRMATION = "DISP_CONF"


class DispositionBand(str, Enum):
    AUTO_CLEAR = "Auto-clear"
    PROPOSE_CLEAR = "Propose Clear"
    REVIEW = "Review"
    ESCALATE = "Escalate"


class ListSeverity(str, Enum):
    BLOCKING = "BLOCKING"
    ADVISORY = "ADVISORY"
    WATCH = "WATCH"


class IntakePath(str, Enum):
    BP_BLOCK = "BP Block"
    DOC_BLOCK = "Doc Block"
    DELTA = "Delta"


class EvidenceItem(BaseModel):
    evidence_id: UUID = Field(default_factory=uuid4)
    category: EvidenceCategory
    data_element: str
    bp_value: str = ""
    spl_value: str = ""
    assessment: str
    data_available: bool
    source_system: str = ""
    source_field: str = ""


class EvidenceLedger(BaseModel):
    items: list[EvidenceItem] = Field(default_factory=list)

    @property
    def dispositive_exclusions(self) -> list[EvidenceItem]:
        return [i for i in self.items if i.category == EvidenceCategory.DISPOSITIVE_EXCLUSION]

    @property
    def dispositive_confirmations(self) -> list[EvidenceItem]:
        return [i for i in self.items if i.category == EvidenceCategory.DISPOSITIVE_CONFIRMATION]

    @property
    def strong_corroborators(self) -> list[EvidenceItem]:
        return [i for i in self.items if i.category == EvidenceCategory.STRONG_CORROBORATOR]

    @property
    def weak_corroborators(self) -> list[EvidenceItem]:
        return [i for i in self.items if i.category == EvidenceCategory.WEAK_CORROBORATOR]

    @property
    def strong_discriminators(self) -> list[EvidenceItem]:
        return [i for i in self.items if i.category == EvidenceCategory.STRONG_DISCRIMINATOR]

    @property
    def all_corroborators(self) -> list[EvidenceItem]:
        return self.strong_corroborators + self.weak_corroborators

    @property
    def available_count(self) -> int:
        return sum(1 for i in self.items if i.data_available)


class HitInput(BaseModel):
    case_id: str
    bp_id: str
    bp_name: str
    bp_country: str = ""
    bp_entity_type: str = ""
    bp_registration_no: str = ""
    spl_entry_id: str
    spl_entry_name: str
    spl_list_type: str
    spl_programme: str
    spl_entity_type: str = ""
    match_percentage: float
    match_basis: str
    intake_path: IntakePath
    list_severity: ListSeverity = ListSeverity.BLOCKING
    order_value: float = 0.0
    order_currency: str = ""


class AdjudicationResult(BaseModel):
    case_id: str
    disposition_band: DispositionBand
    rationale: str
    what_would_change: str
    evidence_summary: str
    evidence_ledger: EvidenceLedger
    precedent_case_id: Optional[str] = None
    model_version: str = ""
    prompt_version: str = ""
    band_logic_version: str = "v1.0.0"
    taxonomy_version: str = "v1.0.0"
    processed_at: datetime = Field(default_factory=datetime.utcnow)
    elapsed_ms: int = 0
