from __future__ import annotations

from taos.core.semantic.intent_classifier import IntentType
from taos.core.semantic.tone_profile import ToneResult
from taos.orchestration.engine import OrchestrationEngine


def _tone(emoji_allowed: bool) -> ToneResult:
    return ToneResult(
        current_label="casual",
        blended_label="casual",
        serious=0.2,
        casual=0.6,
        playful=0.2,
        emoji_allowed=emoji_allowed,
        banter_allowed=False,
        brief_hint="test",
    )


def test_apply_tone_output_styling_adds_contextual_emoji():
    engine = OrchestrationEngine()
    engine._active_tone = _tone(emoji_allowed=True)
    out = engine._apply_tone_output_styling(
        text="Decaf coffee is usually better at night",
        intent=IntentType.SIMPLE_LOOKUP,
        goal="what coffee should i drink at night",
    )
    assert out.endswith("☕")


def test_apply_tone_output_styling_skips_research_mode():
    engine = OrchestrationEngine()
    engine._active_tone = _tone(emoji_allowed=True)
    out = engine._apply_tone_output_styling(
        text="Evidence indicates conflicting reports.",
        intent=IntentType.RESEARCH,
        goal="latest conflict update",
    )
    assert out == "Evidence indicates conflicting reports."
