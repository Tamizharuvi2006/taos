from __future__ import annotations

import types

import pytest

from taos.core.tools.builtin.extract_adapter import extract_with_adapter


@pytest.mark.asyncio
async def test_extract_adapter_uses_web_extract_when_flag_disabled(monkeypatch):
    async def _fake_web_extract(*args, **kwargs):
        return {
            "success": True,
            "url": kwargs.get("url") or "",
            "text": "Sample extracted text",
            "quality_score": 0.9,
            "usable_for_research": True,
        }

    monkeypatch.setattr("taos.core.tools.builtin.web_extract.web_extract", _fake_web_extract)

    out = await extract_with_adapter(
        url="https://example.com",
        timeout=5,
        max_chars=1000,
        include_html=False,
        prefer_scrapling_http=False,
    )
    assert out["success"] is True
    assert out["extractor_adapter"] == "web_extract"


@pytest.mark.asyncio
async def test_extract_adapter_falls_back_when_scrapling_unavailable(monkeypatch):
    async def _fake_web_extract(*args, **kwargs):
        return {
            "success": True,
            "url": kwargs.get("url") or "",
            "text": "Fallback extracted text",
            "quality_score": 0.62,
            "usable_for_research": True,
        }

    def _fake_import_module(name: str):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr("taos.core.tools.builtin.web_extract.web_extract", _fake_web_extract)
    monkeypatch.setattr("taos.core.tools.builtin.extract_adapter.importlib.import_module", _fake_import_module)

    out = await extract_with_adapter(
        url="https://example.com",
        timeout=5,
        max_chars=1000,
        include_html=False,
        prefer_scrapling_http=True,
    )
    assert out["success"] is True
    assert out["extractor_adapter"] == "web_extract"
    assert "adapter_fallback_reason" in out


@pytest.mark.asyncio
async def test_extract_adapter_uses_d4vinci_scrapling_fetcher_when_available(monkeypatch):
    class _FakePage:
        status = 200
        url = "https://example.com"
        content_type = "text/html"
        html = (
            "<html><head><title>Example</title></head><body>"
            "<p>CEO profile evidence text with explicit role and company confirmation.</p>"
            "<p>This page provides official leadership details and management summary.</p>"
            "<p>Additional context to exceed minimum extraction threshold for research usability.</p>"
            "</body></html>"
        )

    class _FakeAsyncFetcher:
        @staticmethod
        async def get(**kwargs):
            return _FakePage()

    fake_fetchers_module = types.SimpleNamespace(AsyncFetcher=_FakeAsyncFetcher, Fetcher=None)

    def _fake_import_module(name: str):
        if name == "scrapling.fetchers":
            return fake_fetchers_module
        raise ModuleNotFoundError(name)

    async def _fake_web_extract(*args, **kwargs):
        return {"success": False, "extractor_adapter": "web_extract"}

    monkeypatch.setattr("taos.core.tools.builtin.extract_adapter.importlib.import_module", _fake_import_module)
    monkeypatch.setattr("taos.core.tools.builtin.web_extract.web_extract", _fake_web_extract)
    monkeypatch.setattr(
        "taos.core.tools.builtin.extract_adapter._assess_extraction_quality",
        lambda **kwargs: {
            "quality": "good",
            "quality_score": 0.92,
            "usable_for_research": True,
            "rejection_reason": None,
            "noise_ratio": 0.05,
        },
    )

    out = await extract_with_adapter(
        url="https://example.com",
        timeout=5,
        max_chars=1000,
        include_html=False,
        prefer_scrapling_http=True,
    )
    assert out["success"] is True
    assert out["extractor_adapter"] == "scrapling_http_d4vinci"


@pytest.mark.asyncio
async def test_extract_adapter_dynamic_mode_respects_flag(monkeypatch):
    async def _fake_web_extract(*args, **kwargs):
        return {"success": False, "extractor_adapter": "web_extract"}

    def _fake_import_module(name: str):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr("taos.core.tools.builtin.web_extract.web_extract", _fake_web_extract)
    monkeypatch.setattr("taos.core.tools.builtin.extract_adapter.importlib.import_module", _fake_import_module)

    out = await extract_with_adapter(
        url="https://example.com",
        timeout=5,
        max_chars=1000,
        include_html=False,
        prefer_scrapling_http=True,
        adapter_preference="dynamic",
        dynamic_enabled=False,
    )
    assert out["extractor_adapter"] == "web_extract"
    assert "dynamic_fetcher_disabled" in str(out.get("adapter_fallback_reason") or "")


@pytest.mark.asyncio
async def test_extract_adapter_dynamic_mode_success(monkeypatch):
    class _FakePage:
        status = 200
        url = "https://example.com"
        content_type = "text/html"
        html = (
            "<html><head><title>Example</title></head><body>"
            "<p>Dynamic leadership page content with full CEO details rendered.</p>"
            "<p>Company role verification text from official team page.</p>"
            "<p>Long enough body for quality scoring.</p>"
            "</body></html>"
        )

    class _FakeDynamicFetcher:
        @staticmethod
        async def get(**kwargs):
            return _FakePage()

    fake_fetchers_module = types.SimpleNamespace(
        AsyncFetcher=None,
        Fetcher=None,
        DynamicFetcher=_FakeDynamicFetcher,
        StealthyFetcher=None,
    )

    def _fake_import_module(name: str):
        if name == "scrapling.fetchers":
            return fake_fetchers_module
        raise ModuleNotFoundError(name)

    async def _fake_web_extract(*args, **kwargs):
        return {"success": False, "extractor_adapter": "web_extract"}

    monkeypatch.setattr("taos.core.tools.builtin.extract_adapter.importlib.import_module", _fake_import_module)
    monkeypatch.setattr("taos.core.tools.builtin.web_extract.web_extract", _fake_web_extract)
    monkeypatch.setattr(
        "taos.core.tools.builtin.extract_adapter._assess_extraction_quality",
        lambda **kwargs: {
            "quality": "good",
            "quality_score": 0.91,
            "usable_for_research": True,
            "rejection_reason": None,
            "noise_ratio": 0.04,
        },
    )

    out = await extract_with_adapter(
        url="https://example.com/leadership",
        timeout=5,
        max_chars=1000,
        include_html=False,
        prefer_scrapling_http=True,
        adapter_preference="dynamic",
        dynamic_enabled=True,
    )
    assert out["success"] is True
    assert out["extractor_adapter"] == "scrapling_dynamic_d4vinci"
