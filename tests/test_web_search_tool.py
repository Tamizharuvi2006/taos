from __future__ import annotations

from typing import Any, Dict

import pytest

from taos.config.settings import get_settings
from taos.core.tools.builtin.web_search import web_search


class _FakeResponse:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Dict[str, Any]:
        return self._payload


@pytest.mark.asyncio
async def test_web_search_news_parses_news_array(monkeypatch):
    captured: Dict[str, Any] = {}

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["json"] = dict(json or {})
            return _FakeResponse(
                {
                    "news": [
                        {
                            "title": "Reuters: latest update",
                            "link": "https://example.com/news-1",
                            "snippet": "Breaking developments.",
                        }
                    ]
                }
            )

    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    get_settings.cache_clear()
    monkeypatch.setattr("taos.core.tools.builtin.web_search.httpx.AsyncClient", _FakeClient)

    out = await web_search(
        query="iran israel war current status",
        search_type="news",
        num_results=3,
        recency_days=2,
    )

    assert out["search_type"] == "news"
    assert out["total_results"] == 1
    assert out["results"][0]["title"] == "Reuters: latest update"
    assert out["results"][0]["link"] == "https://example.com/news-1"
    assert captured["url"].endswith("/news")
    assert captured["json"]["tbs"] == "qdr:w"


@pytest.mark.asyncio
async def test_web_search_handles_provider_error_without_raising(monkeypatch):
    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers=None, json=None):
            raise RuntimeError("provider down")

    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    get_settings.cache_clear()
    monkeypatch.setattr("taos.core.tools.builtin.web_search.httpx.AsyncClient", _FakeClient)

    out = await web_search("any query", search_type="news")
    assert out["total_results"] == 0
    assert out["results"] == []
    assert "error" in out
    assert "web_search_failed" in out["error"]
