from __future__ import annotations

from taos.core.entity import EntityAnswerComposer, EntityEvidence, EntityIntentDetector, EntitySourcePlanner
from taos.core.entity.entity_evidence_ranker import EntityEvidenceRanker
from taos.core.research import ResearchPipeline


def test_ceo_role_mismatch_does_not_verify_employee_as_ceo():
    query = EntityIntentDetector().detect("who is the ceo of relyce infotech")
    answer = EntityAnswerComposer().compose(
        entity_query=query,
        evidence_rows=[
            EntityEvidence(
                title="Relyce team page",
                url="https://relyce.ai/team",
                source_type="official_website",
                candidate_name="Tamizh Aruvi",
                attribute="ceo",
                requested_role="ceo",
                supported_role="employee",
                role_match=False,
                role_mismatch_reason="employee_evidence_does_not_verify_requested_role",
                source_tier="tier1",
                usable_for_verification=True,
                supports_claim=False,
                snippet="Tamizh Aruvi works as an engineer at Relyce Infotech.",
            )
        ],
    )

    assert answer.verification_state == "not_verified"
    assert answer.exact_role_verified is False
    assert answer.role_match is False
    assert answer.supported_role == "employee"
    assert answer.selected_candidate == "Tamizh Aruvi"
    assert "could not verify" in answer.answer.lower()
    assert "employee" in answer.answer.lower()


def test_founder_role_mismatch_does_not_verify_employee_as_founder():
    query = EntityIntentDetector().detect("who is the founder of relyce infotech")
    answer = EntityAnswerComposer().compose(
        entity_query=query,
        evidence_rows=[
            EntityEvidence(
                title="Relyce engineering team",
                url="https://relyce.ai/team",
                source_type="official_website",
                candidate_name="Tamizh Aruvi",
                attribute="founder",
                requested_role="founder",
                supported_role="employee",
                role_match=False,
                role_mismatch_reason="employee_evidence_does_not_verify_requested_role",
                source_tier="tier1",
                usable_for_verification=True,
                supports_claim=False,
                snippet="Tamizh Aruvi is a team member at Relyce Infotech.",
            )
        ],
    )

    assert answer.verification_state == "not_verified"
    assert answer.supported_role == "employee"
    assert answer.exact_role_verified is False
    assert "founder" in answer.answer.lower()
    assert "does not prove" in answer.answer.lower()


def test_tier3_directory_alone_cannot_verify_ceo():
    ranked = EntityEvidenceRanker().rank(
        [
            EntityEvidence(
                title="Directory profile",
                url="https://example-directory.test/relyce",
                source_type="search_snippet",
                candidate_name="Some Person",
                attribute="ceo",
                requested_role="ceo",
                supported_role="ceo",
                role_match=True,
                source_tier="tier3",
                usable_for_verification=True,
                supports_claim=True,
                snippet="Some Person is listed as CEO in a generic directory profile.",
            )
        ]
    )
    assert ranked[0].source_tier == "tier3"
    assert ranked[0].role_match is True
    assert EntityEvidenceRanker().score(ranked[0]) < 0.5


def test_official_source_beats_weak_source_for_role_verification():
    answer = EntityAnswerComposer().compose(
        entity_query=EntityIntentDetector().detect("who is the ceo of relyce infotech"),
        evidence_rows=[
            EntityEvidence(
                title="Generic directory",
                url="https://directory.example/relyce-infotech",
                source_type="search_snippet",
                candidate_name="Wrong Person",
                attribute="ceo",
                requested_role="ceo",
                supported_role="ceo",
                role_match=True,
                source_tier="tier3",
                usable_for_verification=True,
                supports_claim=True,
                snippet="Wrong Person is CEO.",
            ),
            EntityEvidence(
                title="Relyce leadership",
                url="https://relyce.ai/about",
                source_type="official_website",
                candidate_name="Right Person",
                attribute="ceo",
                requested_role="ceo",
                supported_role="ceo",
                role_match=True,
                source_tier="tier1",
                usable_for_verification=True,
                supports_claim=True,
                snippet="Right Person is the CEO of Relyce Infotech.",
            ),
        ],
    )

    assert answer.verification_state == "confirmed"
    assert answer.selected_candidate == "Right Person"
    assert answer.official_source_found is True


def test_conflicting_role_evidence_stays_not_verified():
    answer = EntityAnswerComposer().compose(
        entity_query=EntityIntentDetector().detect("who is the ceo of relyce infotech"),
        evidence_rows=[
            EntityEvidence(
                title="Leadership profile",
                url="https://example.com/relyce-leadership",
                source_type="reputable_news",
                candidate_name="Tamizh Aruvi",
                attribute="ceo",
                requested_role="ceo",
                supported_role="ceo",
                role_match=True,
                source_tier="tier2",
                usable_for_verification=True,
                supports_claim=True,
                snippet="Tamizh Aruvi is identified as CEO.",
            ),
            EntityEvidence(
                title="Official team page",
                url="https://relyce.ai/team",
                source_type="official_website",
                candidate_name="Tamizh Aruvi",
                attribute="ceo",
                requested_role="ceo",
                supported_role="employee",
                role_match=False,
                role_mismatch_reason="employee_evidence_does_not_verify_requested_role",
                source_tier="tier1",
                usable_for_verification=True,
                supports_claim=False,
                snippet="Tamizh Aruvi is an employee at Relyce Infotech.",
            ),
        ],
    )

    assert answer.verification_state == "candidate"
    assert answer.conflict_detected is True
    assert answer.exact_role_verified is False


def test_entity_search_plan_uses_role_specific_lanes():
    query = EntityIntentDetector().detect("who is the ceo of relyce infotech")
    plan = EntitySourcePlanner().plan(query)
    flattened = plan.flatten()

    assert "official_website" in plan.required_lanes
    assert "linkedin" in plan.required_lanes
    assert any("ceo official website" in row.lower() for row in flattened)
    assert any("employee team member" in row.lower() for row in flattened)
    assert any("site:linkedin.com/posts" in row.lower() for row in flattened)
    assert any("founder & ceo" in row.lower() for row in flattened)


def test_rumour_search_depth_uses_multiple_lanes_and_related_evidence():
    pipeline = ResearchPipeline()
    summary = pipeline.search_plan_summary("india blocking claude rumour")
    rows = [
        {
            "title": "Anthropic supported countries",
            "link": "https://support.anthropic.com/en/articles/8461763-supported-countries",
            "snippet": "Anthropic lists Claude as supported and available in India.",
            "tier": "official",
            "published_at": "2026-04-28",
        },
        {
            "title": "Reuters: RBI reviews Claude Mythos cyber risks",
            "link": "https://www.reuters.com/example",
            "snippet": "India's RBI and banks are reviewing cybersecurity risks around Anthropic's Claude Mythos model.",
            "tier": "reporting",
            "published_at": "2026-04-29",
        },
    ]
    quality = pipeline.assess_source_quality(rows=rows, query="india blocking claude rumour", official_source_required=True)
    policy = pipeline.answer_policy(
        quality_summary=quality["summary"],
        citation_coverage={"coverage": 0.0, "claims_supported": 0},
        conflict_summary={},
    )
    answer = pipeline.compose_research_answer(
        query="india blocking claude rumour",
        evidence_rows=quality["usable_rows"],
        answer_policy=policy,
    )

    assert set(summary.get("lanes_used") or []) >= {"official", "news", "contradiction", "background"}
    assert policy["related_evidence_used"] is True
    assert "Rumour status: Not confirmed" in answer


def test_latest_news_answer_exposes_freshness_without_overclaiming():
    pipeline = ResearchPipeline()
    rows = [
        {
            "title": "OpenAI changelog",
            "link": "https://openai.com/changelog",
            "snippet": "OpenAI announced current API updates in the changelog.",
            "tier": "official",
            "published_at": "2026-04-29",
        }
    ]
    quality = pipeline.assess_source_quality(
        rows=rows,
        query="latest OpenAI news today",
        freshness_summary={"freshness_score": 0.88},
        official_source_required=True,
    )
    policy = pipeline.answer_policy(
        quality_summary=quality["summary"],
        citation_coverage={"coverage": 0.8, "claims_supported": 1},
        conflict_summary={},
    )
    answer = pipeline.compose_research_answer(
        query="latest OpenAI news today",
        draft_answer="The best-supported answer is: OpenAI announced current API updates. [S1]",
        evidence_rows=quality["usable_rows"],
        answer_policy=policy,
    )

    assert policy["freshness_status"] == "current_or_recent"
    assert "Freshness status" in answer
