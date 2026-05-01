from __future__ import annotations

from taos.core.semantic.tone_profile import ToneProfiler


def test_technical_with_slang_keeps_professional_guardrails():
    profiler = ToneProfiler()
    result = profiler.evaluate(
        scope_key="u1:chat1",
        message="bro macha please fix this api endpoint latency and pytest dag failure quickly",
    )

    assert result.blended_label == "serious"
    assert result.serious >= ToneProfiler.SERIOUS_OVERRIDE_THRESHOLD
    assert result.emoji_allowed is False
    assert result.banter_allowed is False


def test_single_playful_message_does_not_make_next_technical_reply_goofy():
    profiler = ToneProfiler()
    first = profiler.evaluate(scope_key="u1:chat1", message="hey haha roast me da 😄")
    second = profiler.evaluate(
        scope_key="u1:chat1",
        message="explain dag dependency ordering and retry strategy for required nodes",
    )

    assert first.playful > 0.2
    assert second.blended_label == "serious"
    assert second.serious > second.playful
    assert second.emoji_allowed is False


def test_threshold_snapshot_is_explicit_and_stable():
    snapshot = ToneProfiler.config_snapshot()

    assert snapshot["serious_override_threshold"] == ToneProfiler.SERIOUS_OVERRIDE_THRESHOLD
    assert snapshot["emoji_allowed_serious_max"] == ToneProfiler.EMOJI_ALLOWED_SERIOUS_MAX
    assert snapshot["banter_allowed_playful_min"] == ToneProfiler.BANTER_ALLOWED_PLAYFUL_MIN
    assert snapshot["blend_current_weight"] == ToneProfiler.BLEND_CURRENT_WEIGHT
    assert snapshot["blend_prior_weight"] == ToneProfiler.BLEND_PRIOR_WEIGHT
    assert snapshot["decay_min_factor"] == ToneProfiler.MIN_DECAY_FACTOR
