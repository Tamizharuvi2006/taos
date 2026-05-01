from __future__ import annotations

import asyncio

import httpx
import pytest

from taos.core.entity import EntityAnswerComposer, EntityEvidence, EntityIntentDetector
from taos.core.tools.builtin import web_search as web_search_module
from taos.scripts.run_provider_connectivity_check import collect_provider_connectivity


class _FakeResponse:
    def __init__(self, status_code: int, json_data: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://google.serper.dev/search")
            raise httpx.HTTPStatusError("bad request", request=request, response=self)

    def json(self) -> dict:
        return self._json_data


class _FakeAsyncClient:
    def __init__(self, recorder: dict, response: _FakeResponse, *args, **kwargs) -> None:
        self._recorder = recorder
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, json=None):
        self._recorder["url"] = url
        self._recorder["headers"] = dict(headers or {})
        self._recorder["json"] = dict(json or {})
        return self._response


@pytest.mark.asyncio
async def test_serper_payload_uses_post_json_with_q(monkeypatch):
    recorder: dict = {}
    fake_response = _FakeResponse(
        200,
        {"organic": [{"title": "Microsoft Leadership", "link": "https://www.microsoft.com/en-us/about", "snippet": "Satya Nadella"}]},
    )
    monkeypatch.setattr(web_search_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeAsyncClient(recorder, fake_response, *args, **kwargs))

    result = await web_search_module.web_search("who is the ceo of microsoft", search_type="search")

    assert result["search_http_status"] == 200
    assert recorder["url"] == "https://google.serper.dev/search"
    assert recorder["headers"]["Content-Type"] == "application/json"
    assert "X-API-KEY" in recorder["headers"]
    assert recorder["json"]["q"] == "who is the ceo of microsoft"
    assert result["request_debug"]["has_q"] is True
    assert result["request_debug"]["body_keys"] == ["q"]


@pytest.mark.asyncio
async def test_serper_existing_search_base_url_does_not_duplicate_suffix(monkeypatch):
    recorder: dict = {}
    fake_response = _FakeResponse(200, {"organic": []})
    monkeypatch.setattr(web_search_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeAsyncClient(recorder, fake_response, *args, **kwargs))
    settings = web_search_module.get_settings()
    monkeypatch.setattr(settings, "serper_base_url", "https://google.serper.dev/search")

    await web_search_module.web_search("Relyce Infotech Founder CEO", search_type="search")

    assert recorder["url"] == "https://google.serper.dev/search"


@pytest.mark.asyncio
async def test_http_400_reports_request_contract_error(monkeypatch):
    recorder: dict = {}
    fake_response = _FakeResponse(400, text='{"message":"Missing q"}')
    monkeypatch.setattr(web_search_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeAsyncClient(recorder, fake_response, *args, **kwargs))

    result = await web_search_module.web_search("who is the ceo of microsoft", search_type="search")

    assert result["error_type"] == "request_contract_error"
    assert result["search_http_status"] == 400
    assert result["request_debug"]["has_q"] is True
    assert "Missing q" in result["request_debug"]["response_error_preview"]


@pytest.mark.asyncio
async def test_provider_diagnostics_exposes_request_debug(monkeypatch):
    async def _fake_search(*args, **kwargs):
        return {
            "results": [],
            "search_api_key_present": True,
            "search_endpoint_configured": True,
            "search_http_status": 400,
            "error": "web_search_failed: request_contract_error",
            "error_type": "request_contract_error",
            "error_safe": "Serper request contract rejected the search payload.",
            "request_debug": {
                "method": "POST",
                "endpoint_host": "google.serper.dev",
                "endpoint_path": "/search",
                "body_keys": ["q"],
                "has_q": True,
                "content_type": "application/json",
                "auth_header_present": True,
                "response_error_preview": "Missing q",
            },
        }

    async def _fake_extract(*args, **kwargs):
        return {"success": True, "extractor_adapter": "http", "text": "Microsoft leadership"}

    monkeypatch.setattr("taos.scripts.run_provider_connectivity_check.web_search", _fake_search)
    monkeypatch.setattr("taos.scripts.run_provider_connectivity_check.extract_with_adapter", _fake_extract)
    monkeypatch.setattr(
        "taos.scripts.run_provider_connectivity_check.provider_health_snapshot",
        lambda: {"serper": {"state": "closed", "last_error": ""}, "web_extract": {"state": "closed", "last_error": ""}},
    )

    report = await collect_provider_connectivity(base_url="http://127.0.0.1:8000", timeout=5.0)

    assert report["search_request_debug"]["method"] == "POST"
    assert report["search_request_debug"]["body_keys"] == ["q"]
    assert report["search_request_debug"]["has_q"] is True
    assert report["search_provider_ready"] is False
    assert report["search_error_type"] == "request_contract_error"


def test_source_link_only_rows_cannot_support_ceo_claim():
    query = EntityIntentDetector().detect("who is the ceo of relyce infotech")
    answer = EntityAnswerComposer().compose(
        entity_query=query,
        evidence_rows=[
            EntityEvidence(
                title="https://linkedin.com/company/relyce-infotech",
                url="https://linkedin.com/company/relyce-infotech",
                source_type="company_linkedin",
                requested_role="ceo",
                supported_role="founder_ceo",
                role_match=True,
                source_tier="tier1",
                extraction_quality=0.0,
                usable_for_verification=False,
                supports_claim=False,
                company_match=False,
                target_entity_match=False,
                role_holder_detected="",
                extracted_role="",
                role_applies_to_person=False,
                source_relevance_score=0.0,
            )
        ],
    )

    assert answer.mode == "unverified_no_answer"
    assert answer.selected_candidate == ""
    assert answer.exact_role_verified is False
    assert "could not verify" in answer.answer.lower()


def test_best_supported_candidate_requires_real_role_holder():
    query = EntityIntentDetector().detect("who is the founder of relyce infotech")
    answer = EntityAnswerComposer().compose(
        entity_query=query,
        evidence_rows=[
            EntityEvidence(
                title="Relyce Infotech official/company candidate",
                url="https://linkedin.com/company/relyce-infotech",
                source_type="company_linkedin",
                snippet="",
                requested_role="founder",
                supported_role="founder_ceo",
                role_match=True,
                source_tier="tier1",
                extraction_quality=0.0,
                usable_for_verification=False,
                supports_claim=False,
                company_match=True,
                target_entity_match=True,
                role_holder_detected="",
                extracted_role="",
                role_applies_to_person=False,
                source_relevance_score=0.0,
            )
        ],
    )

    assert answer.mode == "unverified_no_answer"
    assert answer.selected_candidate == ""
    assert "placeholder source links" in answer.confidence_reason.lower()


def test_explicit_role_holder_still_produces_candidate_answer():
    query = EntityIntentDetector().detect("who is the ceo of relyce infotech")
    answer = EntityAnswerComposer().compose(
        entity_query=query,
        evidence_rows=[
            EntityEvidence(
                title="Relyce Infotech LinkedIn company post",
                url="https://linkedin.com/company/relyce-infotech/posts/123",
                source_type="company_linkedin",
                snippet="Core Team: Ukenthiran A Founder & CEO of Relyce Infotech.",
                candidate_name="Ukenthiran A",
                requested_role="ceo",
                supported_role="founder_ceo",
                role_match=True,
                source_tier="tier1",
                extraction_quality=0.7,
                usable_for_verification=True,
                supports_claim=True,
                company_match=True,
                target_entity_match=True,
                role_holder_detected="Ukenthiran A",
                extracted_role="founder_ceo",
                role_applies_to_person=True,
                source_relevance_score=0.92,
            )
        ],
    )

    assert answer.mode == "best_supported_candidate"
    assert answer.selected_candidate == "Ukenthiran A"
