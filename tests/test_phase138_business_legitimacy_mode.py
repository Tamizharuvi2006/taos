from __future__ import annotations

from taos.core.entity.business_legitimacy_checker import BusinessLegitimacyChecker
from taos.core.entity.entity_intent_detector import EntityIntentDetector
from taos.core.entity.entity_models import EntityIntent
from taos.core.entity.legitimacy_models import LegitimacyEvidence
from taos.core.entity.registry_source_planner import RegistrySourcePlanner


def test_legitimacy_intent_detected() -> None:
    query = EntityIntentDetector().detect("is relyce infotech real company")
    assert query.intent == EntityIntent.LEGITIMACY_CHECK
    assert query.entity_name == "Relyce Infotech"


def test_registry_source_planner_has_required_lanes() -> None:
    lanes = RegistrySourcePlanner().plan("Relyce Infotech")
    assert lanes["official_website"]
    assert lanes["registry"]
    assert lanes["linkedin"]
    assert lanes["directory"]


def test_strong_public_presence_requires_registry_support() -> None:
    result = BusinessLegitimacyChecker().check(
        [
            LegitimacyEvidence(title="Official website", source_type="official_website"),
            LegitimacyEvidence(title="Registry", source_type="government_registry", supports_registration=True),
            LegitimacyEvidence(title="LinkedIn", source_type="company_linkedin"),
        ]
    )
    assert result.status == "strong_public_presence"
    assert "not legal" in result.caution.lower()


def test_some_public_presence_does_not_overclaim_registration() -> None:
    result = BusinessLegitimacyChecker().check(
        [LegitimacyEvidence(title="Official website", source_type="official_website")]
    )
    assert result.status == "some_public_presence"
    assert "public registry confirmation" in result.missing


def test_weak_public_evidence_is_labeled_weak() -> None:
    result = BusinessLegitimacyChecker().check(
        [LegitimacyEvidence(title="SEO listing", source_type="seo_listing")]
    )
    assert result.status == "weak_public_evidence"
    assert result.confidence == "Low"


def test_private_paid_sources_are_ignored() -> None:
    result = BusinessLegitimacyChecker().check(
        [LegitimacyEvidence(title="Private paid database", source_type="government_registry", supports_registration=True, requires_login=True)]
    )
    assert result.status == "not_enough_public_evidence"
