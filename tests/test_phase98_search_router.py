from taos.core.search.search_depth_router import SearchDepthRouter


def test_current_vite_version_uses_fast_search():
    router = SearchDepthRouter()
    decision = router.route("current Vite version")
    assert decision.mode == "fast_search"


def test_research_ai_job_market_india_2026_uses_deep_search():
    router = SearchDepthRouter()
    decision = router.route("research AI job market India 2026")
    assert decision.mode == "deep_search"


def test_latest_openai_news_today_uses_news_search():
    router = SearchDepthRouter()
    decision = router.route("latest OpenAI news today")
    assert decision.mode == "news_search"


def test_current_ceo_lookup_uses_official_search():
    router = SearchDepthRouter()
    decision = router.route("who is the current CEO of OpenAI")
    assert decision.mode == "official_search"


def test_what_is_docker_avoids_deep_search():
    router = SearchDepthRouter()
    decision = router.route("what is Docker")
    assert decision.mode == "no_search"
