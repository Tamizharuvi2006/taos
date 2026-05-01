from __future__ import annotations

from taos.core.entity import (
    EntityAnswerComposer,
    EntityEvidence,
    EntityIntent,
    EntityIntentDetector,
    EntityProfileCandidate,
    EntitySourcePlanner,
    ProfileDiscovery,
)
from taos.core.routing.route_cache import RouteCache
from taos.core.routing.route_decider import RouteDecider
from taos.core.understanding.universal_understanding_gateway import UniversalUnderstandingGateway, frame_to_trace_summary


def test_ceo_lookup_detects_entity_and_source_lanes() -> None:
    entity_query = EntityIntentDetector().detect("who is ceo of relyce infotech")
    plan = EntitySourcePlanner().plan(entity_query)
    assert entity_query.intent == EntityIntent.CEO_LOOKUP
    assert entity_query.entity_name == "Relyce Infotech"
    assert {"official_website", "linkedin", "news_articles", "registry_directory"} <= set(plan.lanes)
    assert "official_website" in plan.required_lanes


def test_instagram_profile_detection_and_profile_confidence() -> None:
    entity_query = EntityIntentDetector().detect("find instagram profile of relyce infotech")
    plan = EntitySourcePlanner().plan(entity_query)
    best = ProfileDiscovery().best(
        [
            EntityProfileCandidate(
                handle="@relyceinfotech",
                platform="instagram",
                url="https://instagram.com/relyceinfotech",
                evidence_type="verified_social_link",
                name_match=0.96,
                domain_match=True,
                linked_from_official_site=True,
            )
        ]
    )
    assert entity_query.intent == EntityIntent.OFFICIAL_SOCIAL_PROFILE
    assert entity_query.platform == "instagram"
    assert plan.lanes["social_profiles"]
    assert best is not None
    assert best.confidence >= 0.82
    assert best.status == "profile_likely_official"


def test_founder_lookup_missing_entity_is_ambiguous() -> None:
    entity_query = EntityIntentDetector().detect("who founded this local company")
    answer = EntityAnswerComposer().compose(entity_query=entity_query)
    assert entity_query.intent == EntityIntent.FOUNDER_LOOKUP
    assert "missing_entity" in entity_query.ambiguity_flags
    assert answer.mode == "unverified_no_answer"


def test_legitimacy_check_detected_with_missing_entity() -> None:
    entity_query = EntityIntentDetector().detect("is this startup real")
    assert entity_query.intent == EntityIntent.LEGITIMACY_CHECK
    assert "missing_entity" in entity_query.ambiguity_flags


def test_conflicting_candidate_names_return_multiple_candidates() -> None:
    entity_query = EntityIntentDetector().detect("who is ceo of relyce infotech")
    answer = EntityAnswerComposer().compose(
        entity_query=entity_query,
        evidence_rows=[
            EntityEvidence(title="Directory A", source_type="registry_directory", candidate_name="A Kumar", supports_claim=True),
            EntityEvidence(title="Directory B", source_type="registry_directory", candidate_name="B Kumar", supports_claim=True),
        ],
    )
    assert answer.mode == "multiple_candidates"
    assert "multiple public candidates" in answer.answer.lower()


def test_weak_evidence_only_does_not_become_verified_fact() -> None:
    entity_query = EntityIntentDetector().detect("who is ceo of relyce infotech")
    answer = EntityAnswerComposer().compose(
        entity_query=entity_query,
        evidence_rows=[
            EntityEvidence(title="Search snippet", source_type="search_snippet", candidate_name="A Kumar", supports_claim=True)
        ],
    )
    assert answer.mode == "best_supported_candidate"
    assert answer.confidence == "Low"
    assert "candidate evidence" in answer.uncertainty.lower()


def test_private_or_login_only_profile_content_is_not_used() -> None:
    best = ProfileDiscovery().best(
        [
            EntityProfileCandidate(handle="@private", platform="instagram", requires_login=True, is_private=True, name_match=1.0),
            EntityProfileCandidate(handle="@weakpublic", platform="instagram", evidence_type="random_social", name_match=0.4),
        ]
    )
    assert best is not None
    assert best.handle == "@weakpublic"
    assert best.status == "profile_unverified"


def test_package_version_path_remains_unaffected() -> None:
    RouteCache().clear()
    decision = RouteDecider().decide_sync_for_tests("current vite version")
    signals = decision.routing_signals["universal_understanding"]
    assert decision.route == "fast_search"
    assert signals["relation"] == "latest_version"
    assert signals["entity_intelligence_summary"] == {}


def test_universal_gateway_exposes_entity_intelligence_summary() -> None:
    summary = frame_to_trace_summary(UniversalUnderstandingGateway().understand("who is ceo of relyce infotech"))
    entity_summary = summary["entity_intelligence_summary"]
    assert summary["route_hint"] == "entity_lookup"
    assert entity_summary["intent"] == "ceo_lookup"
    assert entity_summary["entity_name"] == "Relyce Infotech"
    assert "Do not bypass login" in " ".join(entity_summary["public_only_policy"])
