from __future__ import annotations

import unittest

from taos.core.entity import (
    EntityAnswerComposer,
    EntityEvidence,
    EntityIntent,
    EntityIntentDetector,
    EntityProfileCandidate,
    EntitySourcePlanner,
)
from taos.core.entity.business_legitimacy_checker import BusinessLegitimacyChecker
from taos.core.entity.legitimacy_models import LegitimacyEvidence


class Phase142EntityRealProviderIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.detector = EntityIntentDetector()
        self.composer = EntityAnswerComposer()

    def test_founder_typo_query_resolves_entity_and_route_contract(self) -> None:
        entity_query = self.detector.detect("who is the founder fo relyce infotech")
        plan = EntitySourcePlanner().plan(entity_query)

        self.assertEqual(entity_query.intent, EntityIntent.FOUNDER_LOOKUP)
        self.assertEqual(entity_query.entity_name, "Relyce Infotech")
        self.assertIn("official_website", plan.required_lanes)
        self.assertIn("linkedin", plan.required_lanes)

    def test_ceo_answer_prefers_official_and_exposes_metadata(self) -> None:
        entity_query = self.detector.detect("who is the ceo of relyce infotech")
        answer = self.composer.compose(
            entity_query=entity_query,
            evidence_rows=[
                EntityEvidence(
                    title="Relyce leadership",
                    url="https://relyce.ai/about",
                    source_type="official_website",
                    candidate_name="Asha Raman",
                    attribute="ceo",
                    requested_role="ceo",
                    supported_role="ceo",
                    role_match=True,
                    source_tier="tier1",
                    usable_for_verification=True,
                    supports_claim=True,
                    snippet="Asha Raman is the CEO of Relyce Infotech.",
                ),
                EntityEvidence(
                    title="Relyce LinkedIn",
                    url="https://linkedin.com/company/relyce-infotech",
                    source_type="company_linkedin",
                    candidate_name="Asha Raman",
                    attribute="ceo",
                    requested_role="ceo",
                    supported_role="ceo",
                    role_match=True,
                    source_tier="tier1",
                    usable_for_verification=True,
                    supports_claim=True,
                ),
            ],
        )

        self.assertEqual(answer.mode, "verified_entity_fact")
        self.assertEqual(answer.verification_state, "confirmed")
        self.assertTrue(answer.official_source_found)
        self.assertTrue(answer.linkedin_source_found)
        self.assertEqual(answer.selected_candidate, "Asha Raman")
        self.assertIn("Evidence", answer.answer)
        self.assertIn("Bottom line", answer.answer)

    def test_founder_candidate_answer_stays_candidate_not_verified(self) -> None:
        entity_query = self.detector.detect("who is the founder fo relyce infotech")
        answer = self.composer.compose(
            entity_query=entity_query,
            evidence_rows=[
                EntityEvidence(
                    title="Relyce LinkedIn",
                    url="https://linkedin.com/company/relyce-infotech",
                    source_type="company_linkedin",
                    candidate_name="Nikhil Dev",
                    attribute="founder",
                    requested_role="founder",
                    supported_role="founder",
                    role_match=True,
                    source_tier="tier1",
                    usable_for_verification=True,
                    supports_claim=True,
                    company_match=True,
                    target_entity_match=True,
                    role_holder_detected="Nikhil Dev",
                    extracted_role="founder",
                    role_applies_to_person=True,
                    source_relevance_score=0.86,
                    snippet="Founder listed on company LinkedIn.",
                )
            ],
        )

        self.assertEqual(answer.mode, "best_supported_candidate")
        self.assertEqual(answer.verification_state, "candidate")
        self.assertIn("candidate", answer.uncertainty.lower())
        self.assertFalse(answer.official_source_found)
        self.assertTrue(answer.linkedin_source_found)

    def test_employee_evidence_does_not_verify_ceo_role(self) -> None:
        entity_query = self.detector.detect("who is the ceo of relyce infotech")
        answer = self.composer.compose(
            entity_query=entity_query,
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

        self.assertEqual(answer.mode, "role_mismatch_not_verified")
        self.assertEqual(answer.verification_state, "not_verified")
        self.assertEqual(answer.selected_candidate, "Tamizh Aruvi")
        self.assertEqual(answer.supported_role, "employee")
        self.assertFalse(answer.role_match)
        self.assertFalse(answer.exact_role_verified)
        self.assertIn("could not verify", answer.answer.lower())
        self.assertIn("employee", answer.answer.lower())

    def test_legitimacy_check_still_gives_useful_public_context(self) -> None:
        result = BusinessLegitimacyChecker().check(
            [
                LegitimacyEvidence(title="Official website", source_type="official_website"),
                LegitimacyEvidence(title="LinkedIn", source_type="company_linkedin"),
            ]
        )
        self.assertEqual(result.status, "some_public_presence")
        self.assertIn("public registry confirmation", result.missing)

    def test_official_linkedin_profile_discovery_stays_public_and_safe(self) -> None:
        entity_query = self.detector.detect("official linkedin profile of relyce infotech")
        answer = self.composer.compose(
            entity_query=entity_query,
            profile_candidates=[
                EntityProfileCandidate(
                    handle="relyce-infotech",
                    platform="linkedin",
                    url="https://linkedin.com/company/relyce-infotech",
                    evidence_type="company_linkedin",
                    name_match=0.98,
                    domain_match=True,
                    linked_from_official_site=True,
                ),
                EntityProfileCandidate(
                    handle="relyce-fans",
                    platform="linkedin",
                    url="https://linkedin.com/company/relyce-fans",
                    evidence_type="random_social",
                    name_match=0.22,
                    domain_match=False,
                ),
            ],
        )

        self.assertIn(entity_query.intent, {EntityIntent.LINKEDIN_PROFILE, EntityIntent.OFFICIAL_SOCIAL_PROFILE})
        self.assertIn("likely public linkedin profile", answer.answer.lower())
        self.assertTrue(answer.selected_candidate)
        self.assertGreaterEqual(answer.candidate_count, 1)


if __name__ == "__main__":
    unittest.main()
