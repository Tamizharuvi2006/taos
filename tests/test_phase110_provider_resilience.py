import time

import httpx
import pytest

from taos.apps.api.routes.agent import _build_trace
from taos.core.reliability.provider_circuit import CircuitState, ProviderCircuit
from taos.core.reliability.provider_health import GLOBAL_PROVIDER_HEALTH
from taos.core.reliability.provider_policy import get_provider_policy
from taos.core.search import SearchLite
from taos.core.search import package_registry
from taos.core.search.package_registry import lookup_npm_latest
from taos.core.tools.builtin import web_search as web_search_module
from taos.orchestration.engine import OrchestrationEngine


@pytest.fixture(autouse=True)
def reset_provider_health():
    GLOBAL_PROVIDER_HEALTH.reset()
    package_registry._CACHE.clear()
    yield
    GLOBAL_PROVIDER_HEALTH.reset()
    package_registry._CACHE.clear()


@pytest.mark.asyncio
async def test_npm_registry_timeout_uses_fresh_cache_if_available():
    package_registry._cache_set(
        "vite",
        {
            "success": True,
            "name": "vite",
            "version": "7.1.9",
            "source_url": "https://www.npmjs.com/package/vite",
        },
    )

    result = await lookup_npm_latest("vite")

    assert result["success"] is True
    assert result["version"] == "7.1.9"
    assert result["cache_status"] == "hit"
    assert GLOBAL_PROVIDER_HEALTH.snapshot()["npm_registry"]["cache_used"] is True


@pytest.mark.asyncio
async def test_npm_registry_timeout_without_cache_does_not_use_junk_generic_search():
    async def _registry_down(**kwargs):
        return {
            "success": False,
            "error": "TimeoutException",
            "provider_health": {"npm_registry": {"state": "open", "failures": 3}},
        }

    async def _web_search_should_not_run(**kwargs):
        raise AssertionError("generic web search must not run for failed package registry lookup")

    result = await SearchLite().run(
        query="current vite version",
        web_search_fn=_web_search_should_not_run,
        package_registry_fn=_registry_down,
    )

    assert result["confidence"] == 0.22
    assert result["metadata"]["package_registry_used"] is True
    assert result["metadata"]["generic_web_used"] is False
    assert result["metadata"]["registry_error"] == "TimeoutException"


@pytest.mark.asyncio
async def test_serper_timeout_returns_controlled_fallback(monkeypatch):
    class _FailingClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            raise httpx.TimeoutException("serper timeout")

    monkeypatch.setattr(web_search_module, "get_settings", lambda: type("S", (), {"serper_api_key": "key"})())
    monkeypatch.setattr(web_search_module.httpx, "AsyncClient", _FailingClient)

    result = await web_search_module.web_search("latest OpenAI API model changes")

    assert result["results"] == []
    assert result["error"].startswith("web_search_failed")
    assert result["provider_health"]["serper"]["fallback_used"] is True


@pytest.mark.asyncio
async def test_web_extract_failure_uses_snippet_backed_recovery_when_snippet_is_good():
    rows = [
        {
            "title": "Official update",
            "link": "https://example.com/update",
            "snippet": "This snippet is long enough to preserve useful evidence when extraction fails for the page.",
            "source_score": 0.8,
            "domain": "example.com",
        }
    ]

    async def _extract_down(**kwargs):
        return {"success": False, "error": "blocked"}

    merged, summary = await SearchLite()._read_source_pages(
        query="latest OpenAI API model changes",
        rows=rows,
        web_extract_fn=_extract_down,
        query_kind="current_lookup",
    )

    assert summary["failed_count"] == 1
    assert summary["extraction_recovery_used"] is True
    assert merged[0]["snippet_recovery_used"] is True


@pytest.mark.asyncio
async def test_openrouter_primary_failure_tries_fallback_model(monkeypatch):
    class _Model:
        def __init__(self, model_id):
            self.model_id = model_id

        def to_api_params(self):
            return {}

    class _Orchestration:
        def get_config(self, role):
            return _Model("primary-model")

        def get_fallback(self, role):
            return _Model("fallback-model")

    calls = []

    class _Response:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload or {}

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers=None, json=None):
            calls.append(json["model"])
            if json["model"] == "primary-model":
                return _Response(500)
            return _Response(200, {"choices": [{"message": {"content": "fallback answer"}}]})

    monkeypatch.setattr("taos.orchestration.engine.ModelOrchestration", _Orchestration)
    monkeypatch.setattr("taos.orchestration.engine.httpx.AsyncClient", _Client)
    engine = OrchestrationEngine()
    engine._active_request_id = "phase110"

    answer = await engine._run_fast_llm("hello")

    assert answer == "fallback answer"
    assert calls == ["primary-model", "fallback-model"]
    assert GLOBAL_PROVIDER_HEALTH.snapshot()["openrouter"]["state"] == "closed"


def test_circuit_opens_after_repeated_provider_failures():
    circuit = ProviderCircuit(get_provider_policy("serper"))

    circuit.record_failure("timeout")
    circuit.record_failure("timeout")
    circuit.record_failure("timeout")

    assert circuit.state == CircuitState.OPEN
    assert circuit.allow_request() is False


def test_circuit_half_opens_after_cooldown_and_closes_on_success():
    policy = get_provider_policy("serper")
    circuit = ProviderCircuit(policy)
    now = time.time()
    circuit.record_failure("timeout", now=now)
    circuit.record_failure("timeout", now=now)
    circuit.record_failure("timeout", now=now)

    assert circuit.allow_request(now=now + policy.cooldown_seconds + 1) is True
    assert circuit.state == CircuitState.HALF_OPEN
    circuit.record_success()
    assert circuit.state == CircuitState.CLOSED


def test_provider_health_metadata_appears_in_trace():
    trace = _build_trace(
        {
            "route_label": "fast_search",
            "provider_health": {
                "npm_registry": {
                    "state": "closed",
                    "failures": 0,
                    "last_error": None,
                    "fallback_used": False,
                    "cache_used": True,
                    "circuit_opened": False,
                }
            },
            "timing": {"total_ms": 250},
        },
        "phase110_trace",
    )

    assert trace is not None
    assert trace.provider_health["npm_registry"]["cache_used"] is True
    assert trace.public_summary["provider_health"]["npm_registry"]["state"] == "closed"


@pytest.mark.asyncio
async def test_fast_package_lookup_cache_path_is_warmed_and_source_of_record():
    package_registry._cache_set(
        "vite",
        {
            "success": True,
            "name": "vite",
            "version": "7.1.9",
            "source_url": "https://www.npmjs.com/package/vite",
        },
    )
    started = time.perf_counter()
    result = await lookup_npm_latest("vite")
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert result["success"] is True
    assert result["source_url"].endswith("/vite")
    assert elapsed_ms < 500
