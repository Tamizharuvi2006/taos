from taos.core.research.no_result_handler import NoResultHandler


def test_no_result_case_does_not_hallucinate():
    result = NoResultHandler().build(
        goal="unknown startup x latest funding round",
        checked_queries=["unknown startup x funding", "unknown startup x latest news"],
    )

    lowered = result["answer"].lower()
    assert "couldn’t verify" in lowered or "couldn't verify" in lowered
    assert "no reliable evidence found" in " ".join(result["warnings"]).lower()
    assert result["confidence"] < 0.2
