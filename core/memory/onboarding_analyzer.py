from __future__ import annotations

from typing import List

from .import_review_model import ImportReviewItem
from .memory_import_policy import evaluate_import_line
from .onboarding_model import OnboardingAnswers, OnboardingSession


def analyze_onboarding_answers(user_id: str, answers: OnboardingAnswers) -> OnboardingSession:
    items: List[ImportReviewItem] = []
    _add(items, "user_identity_preference", answers.preferred_name, "Preferred name")
    _add(items, "assistant_style", " ".join(v for v in [answers.assistant_style, answers.language_tone] if v), "Assistant style")
    _add(items, "career_goal", answers.goals, "Goals")
    _add(items, "technical_stack", answers.technical_stack, "Technical stack")
    _add(items, "project_context", answers.active_projects, "Active projects")
    _add(items, "active_task", answers.current_blockers, "Current blockers")
    _add(items, "workflow_preference", answers.output_preferences, "Output preferences")
    _add(items, "long_term_preference", answers.boundaries, "Boundaries")
    return OnboardingSession(user_id=user_id, items=items)


def _add(items: List[ImportReviewItem], memory_type: str, value: str, label: str) -> None:
    cleaned = str(value or "").strip()
    if not cleaned:
        return
    policy = evaluate_import_line(cleaned)
    if not policy.allowed and policy.risk == "blocked":
        items.append(
            ImportReviewItem(
                type="blocked_sensitive",
                content="[REDACTED BLOCKED MEMORY]",
                confidence=0.99,
                risk="blocked",
                recommended=False,
                selected=False,
                editable=False,
                reason=policy.reason,
            ).validate()
        )
        return
    risk = policy.risk
    recommended = risk == "low"
    content = f"{label}: {cleaned}"
    items.append(
        ImportReviewItem(
            type=memory_type if risk == "low" else "long_term_memory",
            content=content,
            confidence=0.9 if risk == "low" else 0.72,
            risk=risk,
            recommended=recommended,
            selected=recommended,
            editable=True,
            reason=f"Onboarding answer for {label.lower()}.",
        ).validate()
    )
