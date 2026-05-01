from __future__ import annotations

import pytest

from taos.core.search.search_lite import SearchLite


@pytest.mark.asyncio
async def test_search_lite_returns_fast_search_contract():
    async def _fake_web_search(**kwargs):
        return {
            "results": [
                {"title": "Vite docs", "link": "https://vite.dev", "snippet": "Vite 7.1 is the latest stable release."},
                {"title": "npm", "link": "https://npmjs.com/package/vite", "snippet": "Current package release details."},
            ]
        }

    lite = SearchLite()
    result = await lite.run(query="current Vite version", web_search_fn=_fake_web_search)

    assert result["mode"] == "fast_search"
    assert result["sources"]
    assert result["confidence"] >= 0.58


@pytest.mark.asyncio
async def test_search_lite_no_result_returns_limited_verification_fallback():
    async def _fake_web_search(**kwargs):
        return {"results": []}

    lite = SearchLite()
    result = await lite.run(query="current Vite version", web_search_fn=_fake_web_search)

    assert result["mode"] == "fast_search"
    assert result["confidence"] < 0.3
    assert any("limited verification" in warning.lower() for warning in result["warnings"])


@pytest.mark.asyncio
async def test_search_lite_extracts_version_from_source_of_record_rows():
    async def _fake_web_search(**kwargs):
        return {
            "results": [
                {"title": "Vite docs", "link": "https://vite.dev/blog/announcing-vite7", "snippet": "Vite 7.1 is the latest stable release."},
                {"title": "npm vite", "link": "https://npmjs.com/package/vite", "snippet": "Version 7.1 published recently."},
                {"title": "Random post", "link": "https://example.com/vite", "snippet": "Opinion about Vite releases."},
            ]
        }

    lite = SearchLite()
    result = await lite.run(query="current Vite version", web_search_fn=_fake_web_search)

    assert result["mode"] == "fast_search"
    assert "7.1" in result["answer"]
    assert result["confidence"] >= 0.8
    assert (result.get("metadata") or {}).get("verification_state") == "verified"
    assert (result.get("metadata") or {}).get("source_of_record_count", 0) >= 1


@pytest.mark.asyncio
async def test_search_lite_refuses_weak_version_lookup_without_source_of_record():
    async def _fake_web_search(**kwargs):
        return {
            "results": [
                {"title": "Tech Insider", "link": "https://tech-insider.org/vite", "snippet": "Vite 4.0 changed frontend tooling."},
                {"title": "Dev post", "link": "https://dev.to/post", "snippet": "Maybe Vite 4 is still common."},
                {"title": "Forum thread", "link": "https://reddit.com/r/frontend", "snippet": "Some people mention Vite versions."},
            ]
        }

    lite = SearchLite()
    result = await lite.run(query="current Vite version", web_search_fn=_fake_web_search)

    assert result["mode"] == "fast_search"
    assert result["confidence"] < 0.3
    assert "could not verify" in result["answer"].lower()
    assert "strongest quick-search candidate" in result["answer"].lower()
    assert result["sources"]
    assert result["key_points"]
    assert (result.get("metadata") or {}).get("verification_state") == "not_verified"
    assert (result.get("metadata") or {}).get("confidence_reason")


@pytest.mark.asyncio
async def test_search_lite_retries_with_targeted_source_of_record_queries():
    calls = []

    async def _fake_web_search(**kwargs):
        calls.append(kwargs["query"])
        if kwargs["query"] == "current vite version":
            return {
                "results": [
                    {
                        "title": "SDK Platform release notes",
                        "link": "https://developer.android.com/tools/releases/platforms",
                        "snippet": "Latest stable release notes for Android platform tools.",
                    }
                ]
            }
        if kwargs["query"] == "\"vite\" latest version":
            return {"results": []}
        if kwargs["query"] == "site:npmjs.com/package/vite \"vite\"":
            return {
                "results": [
                    {
                        "title": "vite - npm",
                        "link": "https://npmjs.com/package/vite",
                        "snippet": "vite package. Version 7.1.0 published recently.",
                    }
                ]
            }
        return {"results": []}

    lite = SearchLite()
    result = await lite.run(query="current vite version", web_search_fn=_fake_web_search)

    assert "7.1.0" in result["answer"]
    assert any(query == "site:npmjs.com/package/vite \"vite\"" for query in calls)
    assert "developer.android.com" not in " ".join(result["sources"])


@pytest.mark.asyncio
async def test_search_lite_reads_source_page_when_snippet_lacks_version():
    extract_calls = []

    async def _fake_web_search(**kwargs):
        return {
            "results": [
                {
                    "title": "vite - npm",
                    "link": "https://www.npmjs.com/package/vite",
                    "snippet": "vite package release details and download information.",
                }
            ]
        }

    async def _fake_web_extract(**kwargs):
        extract_calls.append(kwargs["url"])
        return {
            "success": True,
            "url": kwargs["url"],
            "title": "vite - npm",
            "text": "vite package. Version 9.2.1 was published recently. Fast frontend tooling.",
            "quality_score": 0.81,
            "usable_for_research": True,
        }

    lite = SearchLite()
    result = await lite.run(
        query="current vite version",
        web_search_fn=_fake_web_search,
        web_extract_fn=_fake_web_extract,
    )

    metadata = result.get("metadata") or {}
    assert "9.2.1" in result["answer"]
    assert extract_calls == ["https://www.npmjs.com/package/vite"]
    assert metadata.get("source_reading_used") is True
    assert metadata.get("extract_success_count") == 1
    assert metadata.get("snippet_only") is False


@pytest.mark.asyncio
async def test_search_lite_filters_off_topic_rows_from_unverified_results():
    async def _fake_web_search(**kwargs):
        return {
            "results": [
                {
                    "title": "SDK Platform release notes",
                    "link": "https://developer.android.com/tools/releases/platforms",
                    "snippet": "Latest stable release notes for Android platform tools.",
                },
                {
                    "title": "Stable Channel Update for Desktop",
                    "link": "https://chromereleases.googleblog.com/2026/04/stable-channel-update-for-desktop.html",
                    "snippet": "Chrome stable channel update.",
                },
            ]
        }

    lite = SearchLite()
    result = await lite.run(query="current vite version", web_search_fn=_fake_web_search)

    assert result["confidence"] < 0.3
    assert result["sources"] == []


@pytest.mark.asyncio
async def test_search_lite_ignores_neighbor_package_version_numbers():
    async def _fake_web_search(**kwargs):
        return {
            "results": [
                {
                    "title": "create-vite - NPM",
                    "link": "https://www.npmjs.com/package/create-vite?activeTab=versions",
                    "snippet": "Vite requires Node.js version 20.19+, 22.12+.",
                },
                {
                    "title": "vite@8.0.8 - jsDocs.io",
                    "link": "https://www.jsdocs.io/package/vite",
                    "snippet": "API documentation for vite@8.0.8.",
                },
                {
                    "title": "vite - npm",
                    "link": "https://www.npmjs.com/package/vite",
                    "snippet": "vite package. Version 8.0.8 published recently.",
                },
            ]
        }

    lite = SearchLite()
    result = await lite.run(query="current vite version", web_search_fn=_fake_web_search)

    assert "8.0.8" in result["answer"]
    assert "20.19" not in result["answer"]


@pytest.mark.asyncio
async def test_search_lite_handles_version_typo_with_package_registry():
    search_calls = []

    async def _fake_registry(**kwargs):
        return {
            "success": True,
            "name": kwargs["package_name"],
            "version": "8.0.0",
            "source_url": "https://www.npmjs.com/package/vite",
            "published_at": "2026-04-24T00:00:00+00:00",
        }

    async def _fake_web_search(**kwargs):
        search_calls.append(kwargs["query"])
        return {
            "results": [
                {
                    "title": "Publishing Scam Alerts",
                    "link": "https://authorsguild.org/resource/publishing-scam-alerts/",
                    "snippet": "Unrelated publishing warnings.",
                }
            ]
        }

    lite = SearchLite()
    result = await lite.run(
        query="Research and verify with current sources: current vite versio",
        web_search_fn=_fake_web_search,
        package_registry_fn=_fake_registry,
    )

    metadata = result.get("metadata") or {}
    assert "8.0.0" in result["answer"]
    assert metadata.get("query_kind") == "version_lookup"
    assert metadata.get("package_registry_used") is True
    assert metadata.get("verification_state") == "verified"
    assert metadata.get("source_of_record_count") == 1
    assert metadata.get("source_type") == "package_registry"
    assert metadata.get("source_domain") == "npmjs.com"
    assert metadata.get("generic_web_used") is False
    assert search_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "expected_package"),
    [
        ("current vite versio", "vite"),
        ("current vite version", "vite"),
        ("vite latest version", "vite"),
        ("deep research current vite version", "vite"),
        ("current react versio", "react"),
        ("latest nextjs version", "next"),
        ("current typescript version", "typescript"),
    ],
)
async def test_package_version_lookups_use_npm_source_of_record_before_web_search(query, expected_package):
    search_calls = []
    registry_calls = []

    async def _fake_registry(**kwargs):
        registry_calls.append(kwargs["package_name"])
        package_name = kwargs["package_name"]
        return {
            "success": True,
            "name": package_name,
            "version": "99.0.0",
            "source_url": f"https://www.npmjs.com/package/{package_name}",
            "published_at": "2026-04-24T00:00:00+00:00",
        }

    async def _fake_web_search(**kwargs):
        search_calls.append(kwargs["query"])
        return {
            "results": [
                {
                    "title": "Unrelated generic search result",
                    "link": "https://example.com/noise",
                    "snippet": "This should never be used when registry lookup succeeds.",
                }
            ]
        }

    lite = SearchLite()
    result = await lite.run(
        query=query,
        web_search_fn=_fake_web_search,
        package_registry_fn=_fake_registry,
    )

    metadata = result.get("metadata") or {}
    assert result["mode"] == "fast_search"
    assert "99.0.0" in result["answer"]
    assert registry_calls == [expected_package]
    assert search_calls == []
    assert metadata.get("query_kind") == "version_lookup"
    assert metadata.get("package_registry_used") is True
    assert metadata.get("package_name") == expected_package
    assert metadata.get("source_type") == "package_registry"
    assert metadata.get("source_domain") == "npmjs.com"
    assert metadata.get("generic_web_used") is False
    assert metadata.get("verification_state") == "verified"
    assert metadata.get("source_of_record_count") == 1
    assert metadata.get("fast_search_escalation_recommended") is False
    assert result["sources"] == [f"https://www.npmjs.com/package/{expected_package}"]
