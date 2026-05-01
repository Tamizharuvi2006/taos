from __future__ import annotations

from taos.core.entity import EntityAnswerComposer, EntityEvidence, EntityIntentDetector, EntitySourcePlanner
from taos.core.entity.entity_evidence_ranker import EntityEvidenceRanker


def test_relyce_linkedin_founder_ceo_selects_ukenthiran_not_tamizharuvi():
    query = EntityIntentDetector().detect("who is the ceo of relyce infotech")
    answer = EntityAnswerComposer().compose(
        entity_query=query,
        evidence_rows=[
            EntityEvidence(
                title="Relyce Infotech LinkedIn company post",
                url="https://linkedin.com/company/relyce-infotech/posts/123",
                source_type="company_linkedin",
                candidate_name="Ukenthiran A",
                attribute="ceo",
                requested_role="ceo",
                supported_role="founder_ceo",
                role_match=True,
                source_tier="tier1",
                usable_for_verification=True,
                supports_claim=True,
                company_match=True,
                target_entity_match=True,
                role_holder_detected="Ukenthiran A",
                extracted_role="founder_ceo",
                role_applies_to_person=True,
                source_relevance_score=0.96,
                snippet="Core Team: Ukenthiran A Founder & CEO of Relyce infotech | Dharsan L | Tamizharuvi p | ...",
            ),
            EntityEvidence(
                title="Relyce Infotech LinkedIn company post",
                url="https://linkedin.com/company/relyce-infotech/posts/123",
                source_type="company_linkedin",
                candidate_name="Tamizharuvi p",
                attribute="ceo",
                requested_role="ceo",
                supported_role="team_member",
                role_match=False,
                role_mismatch_reason="employee_evidence_does_not_verify_requested_role",
                source_tier="tier1",
                usable_for_verification=True,
                supports_claim=False,
                company_match=True,
                target_entity_match=True,
                role_holder_detected="Tamizharuvi p",
                extracted_role="team_member",
                role_applies_to_person=True,
                source_relevance_score=0.72,
                snippet="Core Team: Ukenthiran A Founder & CEO of Relyce infotech | Dharsan L | Tamizharuvi p | ...",
            ),
        ],
    )

    assert answer.selected_candidate == "Ukenthiran A"
    assert answer.verification_state == "candidate"
    assert answer.linkedin_source_found is True
    assert answer.official_source_found is False
    assert "best-supported public evidence points to ukenthiran a" in answer.answer.lower()
    assert "tamizharuvi" in answer.answer.lower()
    assert "supports: team_member" in answer.answer.lower()


def test_irrelevant_facebook_ceo_result_is_rejected_for_entity_mismatch():
    row = EntityEvidence(
        title="CEO fraud story",
        url="https://facebook.com/unrelated-post",
        source_type="random_social",
        candidate_name="Aaron Levie",
        attribute="ceo",
        requested_role="ceo",
        supported_role="ceo",
        role_match=True,
        source_tier="tier3",
        usable_for_verification=False,
        supports_claim=False,
        company_match=False,
        target_entity_match=False,
        role_holder_detected="Aaron Levie",
        extracted_role="ceo",
        role_applies_to_person=True,
        source_relevance_score=0.04,
        rejection_reason="unrelated_company",
        snippet="US-based tech company CEO fraud discussion.",
    )

    ranked = EntityEvidenceRanker().rank([row])
    assert ranked[0].rejection_reason == "unrelated_company"
    assert EntityEvidenceRanker().score(ranked[0]) < 0.1


def test_team_member_evidence_does_not_select_tamizharuvi_as_ceo():
    query = EntityIntentDetector().detect("who is the ceo of relyce infotech")
    answer = EntityAnswerComposer().compose(
        entity_query=query,
        evidence_rows=[
            EntityEvidence(
                title="Relyce team list",
                url="https://linkedin.com/company/relyce-infotech/posts/123",
                source_type="company_linkedin",
                candidate_name="Tamizharuvi p",
                attribute="ceo",
                requested_role="ceo",
                supported_role="team_member",
                role_match=False,
                role_mismatch_reason="employee_evidence_does_not_verify_requested_role",
                source_tier="tier1",
                usable_for_verification=True,
                supports_claim=False,
                company_match=True,
                target_entity_match=True,
                role_holder_detected="Tamizharuvi p",
                extracted_role="team_member",
                role_applies_to_person=True,
                source_relevance_score=0.66,
                snippet="Core Team includes Tamizharuvi p.",
            )
        ],
    )

    assert answer.selected_candidate == "Tamizharuvi p"
    assert answer.supported_role == "team_member"
    assert answer.role_match is False
    assert answer.exact_role_verified is False
    assert "could not verify" in answer.answer.lower()


def test_linkedin_company_evidence_beats_multiple_weak_snippets():
    ranked = EntityEvidenceRanker().rank(
        [
            EntityEvidence(
                title="Weak snippet 1",
                url="https://example1.test/relyce",
                source_type="search_snippet",
                candidate_name="Wrong Person",
                requested_role="ceo",
                supported_role="ceo",
                role_match=True,
                source_tier="tier3",
                usable_for_verification=True,
                supports_claim=True,
                company_match=False,
                target_entity_match=False,
                role_holder_detected="Wrong Person",
                extracted_role="ceo",
                role_applies_to_person=True,
                source_relevance_score=0.1,
                rejection_reason="entity_mismatch",
                snippet="Wrong Person is CEO.",
            ),
            EntityEvidence(
                title="Relyce LinkedIn post",
                url="https://linkedin.com/company/relyce-infotech/posts/123",
                source_type="company_linkedin",
                candidate_name="Ukenthiran A",
                requested_role="ceo",
                supported_role="founder_ceo",
                role_match=True,
                source_tier="tier1",
                usable_for_verification=True,
                supports_claim=True,
                company_match=True,
                target_entity_match=True,
                role_holder_detected="Ukenthiran A",
                extracted_role="founder_ceo",
                role_applies_to_person=True,
                source_relevance_score=0.97,
                snippet="Ukenthiran A Founder & CEO of Relyce Infotech.",
            ),
        ]
    )

    assert ranked[0].candidate_name == "Ukenthiran A"
    assert ranked[0].source_type == "company_linkedin"


def test_query_planner_includes_exact_linkedin_founder_ceo_queries():
    query = EntityIntentDetector().detect("who is the ceo of relyce infotech")
    flattened = EntitySourcePlanner().plan(query).flatten()

    assert any("site:linkedin.com/company/relyce-infotech" in row.lower() for row in flattened)
    assert any("site:linkedin.com/posts/relyce-infotech" in row.lower() for row in flattened)
    assert any("ukenthiran" in row.lower() for row in flattened) or any("founder & ceo" in row.lower() for row in flattened)
