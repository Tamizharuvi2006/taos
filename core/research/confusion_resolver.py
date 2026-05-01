from __future__ import annotations

from typing import Any, Dict, Iterable

from .confusion_explainer import ConfusionExplainer
from .related_evidence_finder import RelatedEvidenceFinder


class ConfusionResolver:
    def resolve(self, *, claim_terms: Iterable[str], rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        split = RelatedEvidenceFinder().split(claim_terms=claim_terms, rows=rows)
        related_rows = [*split["related"], *split["contradicting"]]
        possible_confusion = ConfusionExplainer().explain(related_rows)
        return {
            **split,
            "possible_confusion": possible_confusion,
        }
