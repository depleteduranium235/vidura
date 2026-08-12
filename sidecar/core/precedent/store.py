"""
Precedent store — recalls prior adjudications of the same or structurally
similar BP-entry pairs.

In production this reads from a persistent store (the Z result table via
OData, or a dedicated vector store for similarity search). For now it's
an in-memory stub.
"""

from typing import Optional
from ..models.schemas import AdjudicationResult


class PrecedentStore:
    """In-memory precedent store for development."""

    def __init__(self):
        self._cases: dict[str, AdjudicationResult] = {}

    def store(self, result: AdjudicationResult) -> None:
        self._cases[result.case_id] = result

    def find_precedent(
        self,
        bp_id: str,
        spl_entry_id: str,
    ) -> Optional[AdjudicationResult]:
        """Find a prior adjudication for the same BP-entry pair."""
        for result in self._cases.values():
            if result.case_id == f"{bp_id}_{spl_entry_id}":
                return result
        return None

    def find_similar(
        self,
        bp_entity_type: str,
        spl_entity_type: str,
        spl_programme: str,
    ) -> list[AdjudicationResult]:
        """Find structurally similar prior adjudications."""
        return [
            r for r in self._cases.values()
            if r.evidence_ledger.items  # non-empty
        ]
