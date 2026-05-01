"""Runtime tone profiling with light session-memory smoothing."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from threading import Lock
from typing import Dict, Optional


@dataclass
class ToneResult:
    current_label: str
    blended_label: str
    serious: float
    casual: float
    playful: float
    emoji_allowed: bool
    banter_allowed: bool
    brief_hint: str


class ToneProfiler:
    """Classifies current tone and smooths with decayed session memory."""

    # Explicit tuning knobs (Phase 78A).
    SERIOUS_OVERRIDE_THRESHOLD = 0.65
    EMOJI_ALLOWED_SERIOUS_MAX = 0.58
    BANTER_ALLOWED_PLAYFUL_MIN = 0.56
    BLEND_CURRENT_WEIGHT = 0.78
    BLEND_PRIOR_WEIGHT = 0.22
    MIN_DECAY_FACTOR = 0.12

    _TECH_RE = re.compile(
        r"\b(api|endpoint|stack|trace|error|fix|debug|model|schema|pytest|build|deploy|db|query|latency|dag|fsm)\b",
        re.I,
    )
    _CASUAL_RE = re.compile(
        r"\b(hey|hi|hello|bro|macha|buddy|yo|thanks|thx|cool|nice|ok|okay)\b",
        re.I,
    )
    _PLAYFUL_RE = re.compile(
        r"\b(lol|lmao|roast|funny|haha|hehe|banter|savage)\b",
        re.I,
    )
    _TOXIC_RE = re.compile(
        r"\b(hate|kill|die|idiot|stupid|moron|slur)\b",
        re.I,
    )
    _EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF]")

    def __init__(self, memory_ttl_seconds: int = 45 * 60) -> None:
        self._ttl = int(max(300, memory_ttl_seconds))
        self._lock = Lock()
        self._memory: Dict[str, Dict[str, float]] = {}

    def evaluate(self, scope_key: str, message: str) -> ToneResult:
        now = time.time()
        msg = str(message or "")
        current = self._score_current(msg)
        prior = self._load_prior(scope_key=scope_key, now=now)
        blended = self._blend(current=current, prior=prior)
        self._store(scope_key=scope_key, now=now, scores=blended)

        current_label = self._label(current)
        blended_label = self._label(blended)
        toxic = bool(self._TOXIC_RE.search(msg))
        serious_override = blended["serious"] >= self.SERIOUS_OVERRIDE_THRESHOLD
        emoji_allowed = (
            (blended["serious"] <= self.EMOJI_ALLOWED_SERIOUS_MAX)
            and not toxic
            and not serious_override
        )
        banter_allowed = (
            (blended["playful"] >= self.BANTER_ALLOWED_PLAYFUL_MIN)
            and not toxic
            and not serious_override
        )
        brief_hint = self._hint(blended_label=blended_label, emoji_allowed=emoji_allowed, banter_allowed=banter_allowed)
        return ToneResult(
            current_label=current_label,
            blended_label=blended_label,
            serious=round(blended["serious"], 3),
            casual=round(blended["casual"], 3),
            playful=round(blended["playful"], 3),
            emoji_allowed=emoji_allowed,
            banter_allowed=banter_allowed,
            brief_hint=brief_hint,
        )

    def _score_current(self, message: str) -> Dict[str, float]:
        text = str(message or "")
        lowered = text.lower()
        words = max(1, len(lowered.split()))
        has_tech = bool(self._TECH_RE.search(text))
        has_casual = bool(self._CASUAL_RE.search(text))
        has_playful = bool(self._PLAYFUL_RE.search(text))

        serious = 0.2
        casual = 0.2
        playful = 0.1

        if has_tech:
            serious += 0.68
        if "?" in text:
            serious += 0.12
        if words > 16:
            serious += 0.1

        if has_casual:
            casual += 0.45
        if words <= 8:
            casual += 0.12

        if has_playful:
            playful += 0.5
        if "!" in text:
            playful += 0.08
        if self._EMOJI_RE.search(text):
            playful += 0.18

        # Anti-overfitting guardrail:
        # technical intent remains professional even with slang markers.
        if has_tech:
            casual = max(0.08, casual - 0.22)
            playful = max(0.04, playful - 0.08)

        total = max(0.001, serious + casual + playful)
        return {
            "serious": serious / total,
            "casual": casual / total,
            "playful": playful / total,
        }

    def _load_prior(self, scope_key: str, now: float) -> Optional[Dict[str, float]]:
        if not scope_key:
            return None
        with self._lock:
            item = self._memory.get(scope_key)
            if not item:
                return None
            age = now - float(item.get("updated_at", now))
            if age > self._ttl:
                self._memory.pop(scope_key, None)
                return None
            # Decay prior influence as it gets older.
            decay = max(self.MIN_DECAY_FACTOR, 1.0 - (age / self._ttl))
            return {
                "serious": float(item.get("serious", 0.33)) * decay,
                "casual": float(item.get("casual", 0.33)) * decay,
                "playful": float(item.get("playful", 0.34)) * decay,
            }

    def _blend(self, current: Dict[str, float], prior: Optional[Dict[str, float]]) -> Dict[str, float]:
        if not prior:
            return current
        # Current tone primary, history secondary.
        mixed = {
            "serious": (current["serious"] * self.BLEND_CURRENT_WEIGHT) + (prior["serious"] * self.BLEND_PRIOR_WEIGHT),
            "casual": (current["casual"] * self.BLEND_CURRENT_WEIGHT) + (prior["casual"] * self.BLEND_PRIOR_WEIGHT),
            "playful": (current["playful"] * self.BLEND_CURRENT_WEIGHT) + (prior["playful"] * self.BLEND_PRIOR_WEIGHT),
        }
        total = max(0.001, mixed["serious"] + mixed["casual"] + mixed["playful"])
        return {
            "serious": mixed["serious"] / total,
            "casual": mixed["casual"] / total,
            "playful": mixed["playful"] / total,
        }

    def _store(self, scope_key: str, now: float, scores: Dict[str, float]) -> None:
        if not scope_key:
            return
        with self._lock:
            self._memory[scope_key] = {
                "serious": float(scores["serious"]),
                "casual": float(scores["casual"]),
                "playful": float(scores["playful"]),
                "updated_at": float(now),
            }

    @staticmethod
    def _label(scores: Dict[str, float]) -> str:
        top = max(scores, key=lambda k: scores[k])
        return str(top)

    @staticmethod
    def _hint(blended_label: str, emoji_allowed: bool, banter_allowed: bool) -> str:
        if blended_label == "serious":
            return "Use professional concise tone. Avoid decorative emojis."
        if banter_allowed:
            return "Use friendly playful tone. Light banter is okay once."
        if emoji_allowed:
            return "Use warm casual tone with at most one fitting emoji."
        return "Use warm casual tone without emojis."

    @classmethod
    def config_snapshot(cls) -> Dict[str, float]:
        return {
            "serious_override_threshold": float(cls.SERIOUS_OVERRIDE_THRESHOLD),
            "emoji_allowed_serious_max": float(cls.EMOJI_ALLOWED_SERIOUS_MAX),
            "banter_allowed_playful_min": float(cls.BANTER_ALLOWED_PLAYFUL_MIN),
            "blend_current_weight": float(cls.BLEND_CURRENT_WEIGHT),
            "blend_prior_weight": float(cls.BLEND_PRIOR_WEIGHT),
            "decay_min_factor": float(cls.MIN_DECAY_FACTOR),
        }


GLOBAL_TONE_PROFILER = ToneProfiler()
