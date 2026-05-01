from __future__ import annotations

from taos.core.memory.memory_portability import ImportConfirmSelection
from taos.core.memory.onboarding_analyzer import analyze_onboarding_answers
from taos.core.memory.onboarding_memory_builder import OnboardingMemoryBuilder
from taos.core.memory.onboarding_model import OnboardingAnswers
from taos.core.memory.user_memory_store import UserMemoryStore


def test_onboarding_status_returns_incomplete_for_new_user() -> None:
    builder = OnboardingMemoryBuilder(UserMemoryStore())
    assert builder.status("u1").status == "incomplete"


def test_onboarding_analyze_extracts_preferences_goals_projects() -> None:
    session = analyze_onboarding_answers(
        "u1",
        OnboardingAnswers(
            preferred_name="Tamizh",
            assistant_style="Friendly Tanglish mentor",
            goals="Become job-ready Full Stack + AI Engineer",
            active_projects="TAOS AgentOS",
        ),
    )
    types = {item.type for item in session.items}
    assert {"user_identity_preference", "assistant_style", "career_goal", "project_context"}.issubset(types)


def test_onboarding_blocks_secrets() -> None:
    session = analyze_onboarding_answers("u1", OnboardingAnswers(boundaries="API key is sk-secret123456"))
    assert session.items[0].type == "blocked_sensitive"
    assert session.items[0].risk == "blocked"


def test_onboarding_confirm_saves_selected_memories() -> None:
    store = UserMemoryStore()
    builder = OnboardingMemoryBuilder(store)
    session = builder.analyze("u1", OnboardingAnswers(preferred_name="Tamizh", goals="Build TAOS"))
    result = builder.confirm("u1", [ImportConfirmSelection(item_id=session.items[0].id, selected=True)])
    assert result["saved_count"] == 1
    assert store.list("u1")[0].reason_saved == "Created by user during onboarding review"
    assert builder.status("u1").status == "complete"


def test_onboarding_skip_marks_skipped() -> None:
    builder = OnboardingMemoryBuilder(UserMemoryStore())
    assert builder.skip("u1").status == "skipped"
