from __future__ import annotations

from taos.core.research.claim_verification import ClaimVerificationAgent
from taos.core.research.rumour_status import RumourStatus, status_label


def test_claim_verification_separates_exact_and_related_evidence() -> None:
    result = ClaimVerificationAgent().verify(
        query="I heard India blocked Claude is it true",
        evidence_rows=[
            {"title": "Claude supported countries", "snippet": "Claude is supported and available in India."},
            {"title": "Claude outage", "snippet": "A temporary outage affected some Claude users."},
        ],
    )
    assert result["status"] == "not_confirmed"
    assert result["evidence"]["contradicting"]
    assert result["evidence"]["related"]
    assert "Rumour status: Not confirmed" in result["answer"]
    assert "What may be causing confusion" in result["answer"]


def test_rumour_status_labels_are_explicit() -> None:
    assert status_label(RumourStatus.CONFIRMED) == "Confirmed"
    assert status_label(RumourStatus.NOT_CONFIRMED) == "Not confirmed"


def test_claim_verification_does_not_return_generic_could_not_verify_when_related_exists() -> None:
    result = ClaimVerificationAgent().verify(
        query="rumor openai blocked in india",
        evidence_rows=[{"title": "OpenAI account suspension", "snippet": "An account suspension story caused confusion."}],
    )
    assert "could not verify" not in result["answer"].lower()
    assert result["status"] == "not_confirmed"
