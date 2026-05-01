from __future__ import annotations

from taos.core.debate.debate_system import DebateSystem


def test_debate_trigger_rules():
    debate = DebateSystem()

    assert debate.should_trigger("high", 0.9, False) is True
    assert debate.should_trigger("medium", 0.5, False) is True
    assert debate.should_trigger("medium", 0.95, True) is True
    assert debate.should_trigger("low", 0.95, False) is False
