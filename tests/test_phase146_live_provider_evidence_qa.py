from __future__ import annotations

import json
import uuid
from pathlib import Path

from taos.scripts import run_live_evidence_qa as phase146


def _case(**overrides):
    row = {
        "id": "relyce_ceo_role_truth",
        "query": "who is the ceo of relyce infotech",
        "category": "entity_role",
        "expected_route": "entity_lookup",
        "expected_owner": "entity_lookup_pipeline",
        "requested_role": "ceo",
        "expect_not_verified": True,
        "forbid_candidate_name": "Tamizh Aruvi",
        "require_search_lanes": ["official_website", "linkedin"],
        "allow_supported_roles": ["employee", ""],
    }
    row.update(overrides)
    return phase146.LiveEvidenceCase.from_dict(row)


def test_mock_report_shape_contains_evidence_and_trust_metadata():
    report = phase146.run_cases([_case()], live=False)
    row = report["results"][0]
    assert report["phase"] == "146"
    assert row["selected_route"] == "entity_lookup"
    assert row["owner"] == "entity_lookup_pipeline"
    assert isinstance(row["evidence_matrix"], list) and row["evidence_matrix"]
    assert isinstance(row["trust_metadata"], dict) and row["trust_metadata"]
    assert "requested_role" in row["trust_metadata"]
    assert "exact_claim_verified" in row


def test_role_mismatch_detection_marks_case_not_verified():
    report = phase146.run_cases([_case()], live=False)
    row = report["results"][0]
    assert row["exact_claim_verified"] is False
    assert row["role_match"] is False
    assert row["trust_metadata"]["supported_role"] == "employee"
    assert row["trust_metadata"]["role_mismatch_reason"] == "employee_evidence_does_not_verify_requested_role"
    assert row["ok"] is True


def test_overclaim_detection_flags_bad_answer():
    fields = {
        "final_answer": "The best-supported public answer is Tamizh Aruvi. Tamizh Aruvi is the CEO of Relyce Infotech.",
        "trust_metadata": {
            "requested_role": "ceo",
            "exact_role_verified": False,
        },
    }
    assert phase146._answer_overclaims(fields) is True


def test_weak_source_rejection_is_present_in_evidence_matrix():
    report = phase146.run_cases([_case()], live=False)
    row = report["results"][0]
    matrix = row["evidence_matrix"]
    assert any(item["tier"] == "tier3" for item in matrix)
    assert any(item["rejection_reason"] == "tier3_snippet_only" for item in matrix)


def test_research_case_exposes_exact_claim_and_lane_fields():
    case = _case(
        id="claude_india_rumour",
        query="india blocking claude rumour",
        category="research_rumour",
        expected_route="deep_research",
        expected_owner="research_pipeline",
        requested_role="",
        expect_not_verified=False,
        forbid_candidate_name="",
        require_search_lanes=[],
        require_research_lanes=["official", "news", "contradiction", "background"],
        allow_supported_roles=[],
        expect_related_evidence=True,
    )
    report = phase146.run_cases([case], live=False)
    row = report["results"][0]
    assert row["search_lanes_used"]
    assert set(row["search_lanes_used"]) >= {"official", "news", "contradiction", "background"}
    assert row["trust_metadata"]["related_evidence_used"] is True
    assert row["exact_claim_verified"] is False


def test_render_markdown_includes_phase_title():
    report = phase146.run_cases([_case()], live=False)
    md = phase146.render_markdown(report)
    assert "# Phase 146 Live Provider Evidence QA" in md
    assert "Detailed results" in md


def test_write_report_writes_mock_report_shape():
    tmp_root = Path(__file__).resolve().parents[1] / ".tmp_phase146"
    tmp_root.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    cases_path = tmp_root / f"phase146_cases_{token}.json"
    out_json = tmp_root / f"phase146_out_{token}.json"
    out_md = tmp_root / f"phase146_out_{token}.md"
    try:
        cases_path.write_text(json.dumps([_case().raw]), encoding="utf-8")
        cases = phase146.load_cases(cases_path)
        report = phase146.run_cases(cases, live=False)
        phase146.write_report(report, out_json=out_json, out_md=out_md)
        payload = json.loads(out_json.read_text(encoding="utf-8"))
        assert payload["phase"] == "146"
        assert out_md.exists()
    finally:
        for path in (cases_path, out_json, out_md):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def test_live_blocker_is_reported_when_base_url_is_unreachable():
    report = phase146.run_cases([_case()], live=True, base_url="http://127.0.0.1:9", timeout=0.2)
    row = report["results"][0]
    assert row["live_blocker"]
    assert row["ok"] is False
