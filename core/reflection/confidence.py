"""
TAOS Confidence Scoring — Utilities for confidence computation and aggregation.

Provides:
- Rolling confidence across multiple steps
- Weighted confidence aggregation
- Confidence trend analysis
- Threshold-based decision helpers
"""

from __future__ import annotations

from typing import List, Optional

from taos.config.settings import get_settings
from taos.core.state.state_schema import ReflectionResult


class ConfidenceScorer:
    """
    Production confidence scoring engine.
    
    Tracks confidence across steps and provides
    aggregation and trend analysis.
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._history: List[float] = []

    def record(self, confidence: float) -> None:
        """Record a new confidence value."""
        self._history.append(max(0.0, min(1.0, confidence)))

    def get_rolling_average(self, window: int = 3) -> float:
        """Get rolling average confidence over last N steps."""
        if not self._history:
            return 1.0
        recent = self._history[-window:]
        return sum(recent) / len(recent)

    def get_weighted_average(self) -> float:
        """Get weighted average with more recent values weighted higher."""
        if not self._history:
            return 1.0
        total_weight = 0.0
        weighted_sum = 0.0
        for i, conf in enumerate(self._history):
            weight = i + 1  # More recent = higher weight
            weighted_sum += conf * weight
            total_weight += weight
        return weighted_sum / total_weight

    def get_trend(self) -> str:
        """Analyze confidence trend: 'improving', 'degrading', 'stable'."""
        if len(self._history) < 2:
            return "stable"
        recent = self._history[-3:]
        if len(recent) < 2:
            return "stable"

        diffs = [recent[i] - recent[i-1] for i in range(1, len(recent))]
        avg_diff = sum(diffs) / len(diffs)

        if avg_diff > 0.05:
            return "improving"
        elif avg_diff < -0.05:
            return "degrading"
        return "stable"

    def should_retry(self, confidence: float) -> bool:
        """Check if confidence warrants a retry."""
        return confidence < self._settings.confidence_retry_threshold

    def should_terminate(self, confidence: float) -> bool:
        """Check if confidence is too low to continue."""
        return confidence < self._settings.confidence_terminate_threshold

    def should_replan(self, confidence: float) -> bool:
        """Check if confidence suggests replanning."""
        return (
            confidence < self._settings.confidence_retry_threshold
            and confidence >= self._settings.confidence_terminate_threshold
        )

    def aggregate_reflections(self, reflections: List[ReflectionResult]) -> float:
        """Aggregate confidence from multiple reflections."""
        if not reflections:
            return 1.0
        
        confidences = [r.confidence for r in reflections]
        # Use geometric mean for better aggregation of probabilities
        product = 1.0
        for c in confidences:
            product *= max(c, 0.01)  # Avoid zero
        return product ** (1.0 / len(confidences))

    @property
    def history(self) -> List[float]:
        return list(self._history)

    def reset(self) -> None:
        """Reset confidence history."""
        self._history.clear()
