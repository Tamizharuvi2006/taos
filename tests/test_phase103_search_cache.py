from __future__ import annotations

import asyncio
import unittest

from taos.core.research.evidence_cache import EvidenceCache
from taos.core.research.extract_cache import ExtractCache
from taos.core.search.search_cache import SearchResultCache
from taos.core.search.search_lite import SearchLite


class Phase103SearchCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_lite_uses_cache_on_repeat_query(self):
        calls = {"count": 0}

        async def fake_search(**kwargs):
            calls["count"] += 1
            return {"results": [{"title": "Vite", "link": "https://vite.dev", "snippet": "Vite 7 latest."}]}

        cache = SearchResultCache()
        cache.clear()
        lite = SearchLite(cache=cache)
        first = await lite.run(query="current vite version", web_search_fn=fake_search, freshness_mode="current_lookup")
        second = await lite.run(query="current vite version", web_search_fn=fake_search, freshness_mode="current_lookup")
        self.assertEqual(calls["count"], 1)
        self.assertEqual(first["mode"], "fast_search")
        self.assertEqual((second.get("metadata") or {}).get("cache_status"), "hit")

    async def test_news_like_cache_can_go_stale(self):
        cache = SearchResultCache()
        cache.clear()
        cache.set(query="latest ai news", mode="news_live", value={"mode": "fast_search", "metadata": {}})
        key = cache.key(query="latest ai news", mode="news_live", search_type="search")
        cache._cache[key].created_at -= cache._cache[key].ttl_seconds + 1
        value, status = cache.get(query="latest ai news", mode="news_live")
        self.assertIsNone(value)
        self.assertEqual(status, "stale")

    async def test_extract_cache_uses_mode_specific_ttl(self):
        cache = ExtractCache()
        cache.clear()
        cache.set(
            "https://example.com/news",
            {"success": True, "usable_for_research": True, "text": "Latest"},
            freshness_mode="news_live",
        )
        key = cache.canonical_url("https://example.com/news")
        cache._cache[key].created_at -= cache._cache[key].ttl_seconds + 1
        value, status = cache.get("https://example.com/news", freshness_mode="news_live")
        self.assertIsNone(value)
        self.assertEqual(status, "stale")

    async def test_evidence_cache_hash_stays_stable_with_raw_snippet(self):
        cache = EvidenceCache()
        cache.clear()
        row = {
            "link": "https://example.com/source",
            "title": "Source",
            "snippet": "Short search snippet",
            "raw_snippet": "Short search snippet",
        }
        cache.set(row, {"provider": "example.com"}, freshness_mode="general_research")
        mutated = dict(row)
        mutated["snippet"] = "Short search snippet Extract: Long extracted text"
        value, status = cache.get(mutated, freshness_mode="general_research")
        self.assertEqual(status, "hit")
        self.assertEqual(value["provider"], "example.com")


if __name__ == "__main__":
    unittest.main()
