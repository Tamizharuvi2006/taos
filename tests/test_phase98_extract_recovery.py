from taos.core.research.extract_recovery import ExtractRecovery


def test_extract_failure_still_returns_snippet_backed_recovery():
    recovery = ExtractRecovery().recover(
        ranked_rows=[
            {
                "title": "Source A",
                "link": "https://example.com/a",
                "snippet": "Recent update with enough snippet detail.",
            }
        ]
    )

    assert recovery["used"] is True
    assert recovery["recovered_rows"][0]["snippet_only"] is True
    assert "snippet" in recovery["warning"].lower()
