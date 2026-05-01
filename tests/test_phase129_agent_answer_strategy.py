from __future__ import annotations

from taos.core.answering.answer_strategy import AnswerStrategy, choose_answer_strategy
from taos.core.answering.research_answer_composer_v2 import ResearchAnswerComposerV2


def test_rumour_with_related_evidence_gets_context_strategy() -> None:
    strategy = choose_answer_strategy(
        intent="rumour_verification",
        status="not_confirmed",
        evidence={"related": [{"title": "outage"}]},
    )
    assert strategy == AnswerStrategy.RUMOUR_UNCONFIRMED_WITH_CONTEXT


def test_composer_starts_with_useful_conclusion() -> None:
    answer = ResearchAnswerComposerV2().compose(
        query="India blocked Claude?",
        intent="rumour_verification",
        status="not_confirmed",
        best_supported="Claude appears supported in India.",
        evidence_rows=[{"title": "Supported countries"}],
    )
    assert answer.startswith("I could not confirm the exact claim. The best-supported status is:")
    assert "Answer strategy: rumour_unconfirmed_with_context" in answer


def test_no_generic_failure_when_related_evidence_exists() -> None:
    answer = ResearchAnswerComposerV2().compose(
        query="rumor openai blocked india",
        intent="rumour_verification",
        status="not_confirmed",
        best_supported="Related evidence exists but no block is confirmed.",
        evidence_rows=[{"title": "Account suspension story"}],
    )
    assert "could not verify" not in answer.lower()
