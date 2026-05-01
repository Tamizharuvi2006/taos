from __future__ import annotations

from taos.core.research.confusion_explainer import ConfusionExplainer
from taos.core.research.confusion_resolver import ConfusionResolver
from taos.core.research.related_evidence_finder import RelatedEvidenceFinder


def test_related_evidence_is_separated_from_confirming_and_contradicting() -> None:
    split = RelatedEvidenceFinder().split(
        claim_terms=["india", "claude"],
        rows=[
            {"title": "Claude outage India", "snippet": "A Claude outage affected India."},
            {"title": "Supported countries", "snippet": "Claude available in India."},
            {"title": "India Claude block", "snippet": "India Claude blocked claim."},
        ],
    )
    assert split["related"]
    assert split["contradicting"]
    assert split["confirming"]


def test_confusion_explainer_names_possible_confusion() -> None:
    reasons = ConfusionExplainer().explain(
        [{"title": "Claude outage and account suspension", "snippet": "Security concern coverage."}]
    )
    assert any("outage" in reason for reason in reasons)
    assert any("account suspension" in reason for reason in reasons)
    assert any("security" in reason for reason in reasons)


def test_confusion_resolver_does_not_hallucinate_confirmation() -> None:
    resolved = ConfusionResolver().resolve(
        claim_terms=["india", "claude"],
        rows=[{"title": "Claude outage", "snippet": "Temporary service issue."}],
    )
    assert not resolved["confirming"]
    assert resolved["related"]
    assert resolved["possible_confusion"]
