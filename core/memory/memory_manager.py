"""
TAOS Memory Manager — Write policy, context management, and memory lifecycle.

Manages memory across the agent lifecycle:
- Stores step results and reflections as memory entries
- Builds context windows for LLM prompts
- Applies compression when context exceeds token limits
- Extracts and stores tool outputs for cross-step reference
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Set

from taos.config.constants import (
    CompressionStrategy,
    DEFAULT_MAX_TOKENS_CONTEXT,
    DEFAULT_MEMORY_MIN_CONFIDENCE,
    DEFAULT_SUMMARIZATION_TRIGGER,
)
from taos.config.settings import get_settings
from taos.core.memory.memory_store import MemoryEntry, MemoryStore
from taos.core.state.state_schema import (
    GlobalState,
    PlanStep,
    ReflectionResult,
    StepResult,
)


class MemoryManager:
    """
    High-level memory operations for the agent lifecycle.

    Responsibilities:
    1. Auto-store step results and reflections
    2. Build compressed context windows for LLM calls
    3. Provide cross-step result lookups
    4. Manage memory lifecycle (init, store, compress, clear)
    """

    def __init__(
        self,
        store: Optional[MemoryStore] = None,
        max_context_tokens: int = DEFAULT_MAX_TOKENS_CONTEXT,
        compression_strategy: CompressionStrategy = CompressionStrategy.HYBRID,
    ) -> None:
        self._store = store or MemoryStore()
        self._max_context_tokens = max_context_tokens
        self._summarization_trigger = DEFAULT_SUMMARIZATION_TRIGGER
        self._compression_strategy = compression_strategy
        self._last_response: Optional[str] = None

    @property
    def store(self) -> MemoryStore:
        return self._store

    def set_last_response(self, text: str) -> None:
        """Store the final response for follow-up transforms."""
        self._last_response = text

    def has_context(self) -> bool:
        """Check if memory has any entries or previous responses."""
        return self._store.size > 0 or self._last_response is not None

    def get_last_response(self) -> Optional[str]:
        return self._last_response

    # ═══════════════════════════════════════════════════════════
    # STORING
    # ═══════════════════════════════════════════════════════════

    def store_step_result(
        self,
        step_id: str,
        step: PlanStep,
        result: StepResult,
    ) -> str:
        """
        Store a step result as a memory entry.

        Returns the memory key used for storage.
        """
        key = f"step_result:{step_id}"

        # Build a structured value dict
        value = {
            "action": step.action,
            "tool": step.tool,
            "success": result.success,
            "result": _truncate(str(result.result), 5000) if result.result else None,
            "error": result.error,
            "latency": result.latency,
            "cost": result.cost,
        }

        confidence = 0.9 if result.success else 0.3
        tags = {"step_result", f"step_{step_id}"}
        if step.tool:
            tags.add(f"tool:{step.tool}")
        if not result.success:
            tags.add("failed")

        self._store.put(
            key=key,
            value=value,
            confidence=confidence,
            source=f"step_{step_id}",
            tags=tags,
        )

        return key

    def store_reflection(
        self,
        step_id: str,
        reflection: ReflectionResult,
    ) -> str:
        """Store a reflection result as a memory entry."""
        key = f"reflection:{step_id}"

        value = {
            "success": reflection.success,
            "confidence": reflection.confidence,
            "reasoning": reflection.reasoning,
            "suggestions": reflection.suggestions,
            "retry_recommended": reflection.retry_recommended,
        }

        self._store.put(
            key=key,
            value=value,
            confidence=reflection.confidence,
            source=f"reflection_{step_id}",
            tags={"reflection", f"step_{step_id}"},
        )

        return key

    def store_tool_output(
        self,
        tool_name: str,
        step_id: str,
        output: Any,
        confidence: float = 0.85,
    ) -> str:
        """Store a specific tool output for cross-step reference."""
        key = f"tool_output:{tool_name}:{step_id}"

        self._store.put(
            key=key,
            value=_truncate(str(output), 8000) if output else None,
            confidence=confidence,
            source=f"tool_{tool_name}",
            tags={"tool_output", f"tool:{tool_name}", f"step_{step_id}"},
        )

        return key

    def store_context(
        self,
        key: str,
        value: Any,
        confidence: float = 0.8,
        tags: Optional[Set[str]] = None,
    ) -> str:
        """Store arbitrary context with a custom key."""
        self._store.put(
            key=key,
            value=value,
            confidence=confidence,
            source="context",
            tags=tags or {"context"},
        )
        return key

    # ═══════════════════════════════════════════════════════════
    # CONTEXT BUILDING
    # ═══════════════════════════════════════════════════════════

    def build_context_window(
        self,
        state: GlobalState,
        max_entries: int = 10,
    ) -> List[str]:
        """
        Build a context window for LLM calls.

        Combines recent step results with relevant memory entries,
        applying compression if the context exceeds token limits.
        """
        context_parts: List[str] = []

        # 1. Goal context
        if state.goal:
            context_parts.append(f"[Goal] {state.goal}")

        # 2. Recent step results (most important)
        recent_results = self._store.get_by_tag("step_result")
        recent_results.sort(key=lambda e: e.created_at, reverse=True)

        for entry in recent_results[:max_entries]:
            val = entry.value
            if isinstance(val, dict):
                status = "✓" if val.get("success") else "✗"
                action = val.get("action", "unknown")
                result_str = _truncate(str(val.get("result", "")), 500)
                context_parts.append(
                    f"[Step {status}] {action}: {result_str}"
                )

        # 3. Recent reflections (for pattern awareness)
        reflections = self._store.get_by_tag("reflection")
        reflections.sort(key=lambda e: e.created_at, reverse=True)

        for entry in reflections[:3]:
            val = entry.value
            if isinstance(val, dict):
                reasoning = val.get("reasoning", "")
                if reasoning:
                    context_parts.append(f"[Reflection] {_truncate(reasoning, 200)}")

        # 4. Apply compression if needed
        total_chars = sum(len(p) for p in context_parts)
        estimated_tokens = total_chars // 4  # rough estimate

        if estimated_tokens > self._max_context_tokens:
            context_parts = self._compress_context(
                context_parts, self._max_context_tokens
            )

        return context_parts

    def get_step_result_value(self, step_id: str) -> Optional[Dict]:
        """Retrieve the stored result for a specific step."""
        entry = self._store.get(f"step_result:{step_id}")
        return entry.value if entry else None

    def get_tool_output(self, tool_name: str, step_id: str) -> Optional[Any]:
        """Retrieve a stored tool output."""
        entry = self._store.get(f"tool_output:{tool_name}:{step_id}")
        return entry.value if entry else None

    def get_recent_failures(self, n: int = 5) -> List[MemoryEntry]:
        """Get recent failed step results for error pattern detection."""
        failed = self._store.get_by_tag("failed")
        failed.sort(key=lambda e: e.created_at, reverse=True)
        return failed[:n]

    # ═══════════════════════════════════════════════════════════
    # COMPRESSION
    # ═══════════════════════════════════════════════════════════

    def _compress_context(
        self,
        parts: List[str],
        max_tokens: int,
    ) -> List[str]:
        """
        Compress context to fit within token limits.

        Strategies:
        - DROP_OLD: Remove oldest entries first
        - SUMMARIZE: Collapse early entries into summary
        - HYBRID: Drop old, then summarize if still over
        """
        strategy = self._compression_strategy

        if strategy == CompressionStrategy.DROP_OLD:
            return self._drop_old_compression(parts, max_tokens)
        elif strategy == CompressionStrategy.SUMMARIZE:
            return self._summarize_compression(parts, max_tokens)
        else:  # HYBRID
            result = self._drop_old_compression(parts, max_tokens)
            total = sum(len(p) for p in result) // 4
            if total > max_tokens:
                result = self._summarize_compression(result, max_tokens)
            return result

    def _drop_old_compression(
        self, parts: List[str], max_tokens: int
    ) -> List[str]:
        """Drop oldest entries (from the front) until under limit."""
        while parts and (sum(len(p) for p in parts) // 4) > max_tokens:
            # Keep the goal (first entry) if it exists
            if len(parts) > 1:
                parts.pop(1)  # Remove second-oldest
            else:
                break
        return parts

    def _summarize_compression(
        self, parts: List[str], max_tokens: int
    ) -> List[str]:
        """
        Collapse early entries into a single summary line.
        (Heuristic — does not use LLM to keep this sync.)
        """
        if len(parts) <= 2:
            return parts

        # Keep first (goal) and last 3 entries, summarize middle
        keep_first = parts[:1]
        keep_last = parts[-3:]
        middle = parts[1:-3]

        if middle:
            success_count = sum(1 for p in middle if "✓" in p)
            fail_count = sum(1 for p in middle if "✗" in p)
            summary = (
                f"[Summary] {len(middle)} earlier steps: "
                f"{success_count} succeeded, {fail_count} failed"
            )
            return keep_first + [summary] + keep_last

        return keep_first + keep_last

    # ═══════════════════════════════════════════════════════════
    # LIFECYCLE
    # ═══════════════════════════════════════════════════════════

    def reset(self) -> None:
        """Clear all memory for a new task."""
        self._store.clear()

    def summary(self) -> str:
        """Return a human-readable summary."""
        return self._store.to_summary()


# ═══════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════

def _truncate(text: str, max_len: int) -> str:
    """Truncate text to max length with ellipsis."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."
