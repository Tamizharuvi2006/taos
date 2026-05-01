from __future__ import annotations

from taos.core.entity.candidate_clusterer import CandidateClusterer
from taos.core.entity.disambiguation_models import EntityCandidate
from taos.core.entity.entity_disambiguator import EntityDisambiguator, selected_evidence


def test_two_same_name_companies_without_context_ask_clarification() -> None:
    result = EntityDisambiguator().disambiguate(
        query="who is ceo of apex technologies",
        candidates=[
            EntityCandidate(name="Apex Technologies", location="Chennai", industry="software", source_count=2),
            EntityCandidate(name="Apex Technologies", location="Austin", industry="hardware", source_count=2),
        ],
    )
    assert result.mode == "clarification_needed"
    assert len(result.candidates) == 2


def test_location_resolves_candidate() -> None:
    result = EntityDisambiguator().disambiguate(
        query="who is ceo of apex technologies chennai",
        candidates=[
            EntityCandidate(name="Apex Technologies", location="Chennai", industry="software", source_count=2),
            EntityCandidate(name="Apex Technologies", location="Austin", industry="hardware", source_count=2),
        ],
    )
    assert result.mode == "selected_candidate"
    assert result.selected is not None
    assert result.selected.location == "Chennai"


def test_similar_names_do_not_merge() -> None:
    clusters = CandidateClusterer().cluster(
        [
            EntityCandidate(name="Reliance Infotech", domain="reliance.example"),
            EntityCandidate(name="Relyce Infotech", domain="relyce.example"),
        ]
    )
    assert len(clusters) == 2


def test_social_profile_same_handle_different_category_needs_context() -> None:
    result = EntityDisambiguator().disambiguate(
        query="find instagram of nova labs",
        candidates=[
            EntityCandidate(name="Nova Labs", industry="biotech", social_handle="@novalabs", source_count=1),
            EntityCandidate(name="Nova Labs", industry="creator", social_handle="@novalabs", source_count=1),
        ],
    )
    assert result.mode == "clarification_needed"


def test_domain_context_selects_candidate() -> None:
    result = EntityDisambiguator().disambiguate(
        query="is relyce infotech relyce.ai real",
        candidates=[
            EntityCandidate(name="Relyce Infotech", domain="relyce.ai", source_count=2),
            EntityCandidate(name="Reliance Infotech", domain="reliance.example", source_count=4),
        ],
    )
    assert result.mode == "selected_candidate"
    assert result.selected is not None
    assert result.selected.domain == "relyce.ai"


def test_evidence_from_selected_candidate_only() -> None:
    candidate = EntityCandidate(name="Relyce Infotech", domain="relyce.ai", evidence={"ceo": "Candidate A"})
    other = EntityCandidate(name="Reliance Infotech", domain="reliance.example", evidence={"ceo": "Candidate B"})
    result = EntityDisambiguator().disambiguate(query="relyce.ai ceo", candidates=[candidate, other])
    assert result.selected is not None
    assert selected_evidence(result.selected) == {"ceo": "Candidate A"}
