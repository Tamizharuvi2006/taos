from __future__ import annotations

from taos.core.tools.builtin.web_extract import (
    _assess_extraction_quality,
    _extract_author,
    _extract_published_at,
)


def test_extract_published_at_from_time_tag():
    html = '<html><body><time datetime="2026-04-09T11:00:00Z">Apr 9</time></body></html>'
    assert _extract_published_at(html) == "2026-04-09T11:00:00Z"


def test_extract_author_from_meta():
    html = '<meta name="author" content="Jane Reporter">'
    assert _extract_author(html) == "Jane Reporter"


def test_assess_extraction_quality_article_is_usable():
    text = " ".join(["This is a substantive article paragraph."] * 80)
    quality = _assess_extraction_quality(
        page_type="article",
        text=text,
        title="Major policy update announced",
        published_at="2026-04-09",
        author="Staff Writer",
        html="<html><article><p>body</p></article></html>",
    )
    assert quality["usable_for_research"] is True
    assert quality["quality_score"] >= 0.5
    assert quality["quality"] in {"high", "moderate"}


def test_assess_extraction_quality_index_page_rejected():
    text = "Short nav page with links and categories only."
    quality = _assess_extraction_quality(
        page_type="index",
        text=text,
        title="Search results",
        published_at="",
        author="",
        html="<html><body>search index</body></html>",
    )
    assert quality["usable_for_research"] is False
    assert quality["rejection_reason"] in {"index_like_page", "text_too_short", "low_quality_content"}
    assert quality["quality"] in {"poor", "low"}

