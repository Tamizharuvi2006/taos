"""
TAOS Output Layer — Response Formatter.

Formats raw agent output into structured, user-friendly responses
following the answer-first policy (PRD §12).

Response format:
    👉 <direct answer>

    Key points:
    • ...
    • ...

    Confidence: <level + reason>

Constraints:
- Remove internal logs/traces
- Limit verbosity based on intent
- Adapt length by intent type
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from taos.core.semantic.intent_classifier import IntentType


@dataclass
class FormattedResponse:
    """A formatted response ready for the user."""

    direct_answer: str = ""
    key_points: List[str] = field(default_factory=list)
    confidence_display: str = ""
    sources: List[str] = field(default_factory=list)
    full_text: str = ""
    word_count: int = 0
    was_truncated: bool = False


class ResponseFormatter:
    """
    Adaptive Output System (Content-Aware Formatting).
    
    Dynamically decides structure based on intent, complexity, and content.
    Prevents over-formatting, rigid headers, and messy paragraphs.
    """

    # Max word counts by intent type
    MAX_WORDS = {
        IntentType.SIMPLE_LOOKUP: 40,
        IntentType.DEFINITION: 80,
        IntentType.TRANSFORM: 150,
        IntentType.NEWS: 300,
        IntentType.COMPARISON: 400,
        IntentType.TASK: 600,
        IntentType.RESEARCH: 1000,
    }

    # Patterns to strip from output (internal noise)
    _STRIP_PATTERNS = [
        re.compile(r"\[DEBUG\].*?\n", re.I),
        re.compile(r"\[TRACE\].*?\n", re.I),
        re.compile(r"\[INTERNAL\].*?\n", re.I),
        re.compile(r"Confidence: \d+\.\d+\s*\n"),
        re.compile(r"Cost: \$\d+\.\d+\s*\n"),
        re.compile(r"state_version: \d+\s*\n"),
        re.compile(r"^#+\s*", re.MULTILINE),  # Remove heavy markdown headers locally
        re.compile(r"\{['\"]success['\"]:\s*(True|False|true|false).*?\}", re.DOTALL), # 🚨 Trap raw dictionary leaks
        re.compile(r"```json\s+.*?\s+```", re.DOTALL), # 🚨 Trap embedded JSON logs
    ]

    def format(
        self,
        raw_output: str,
        goal: str,
        intent: IntentType = IntentType.TASK,
        confidence: float = 0.0,
        complexity: str = "medium",  # "low", "medium", "high"
        is_followup: bool = False,
        sources: Optional[List[str]] = None,
    ) -> FormattedResponse:
        """Apply content-aware, adaptive formatting based on intent."""
        # 1. Clean data structures (NEW: Protect against JSON leaks)
        cleaned = self._format_data_structures(raw_output)

        # 2. Clean noise
        for pattern in self._STRIP_PATTERNS:
            cleaned = pattern.sub("", cleaned)
            
        # Remove multiple blank lines
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

        # 3. Content Detection (Overrides intent)
        goal_lower = goal.lower()
        if "compare" in goal_lower or "vs" in goal_lower:
            intent = IntentType.COMPARISON
        elif "fix" in goal_lower or "error" in goal_lower:
            intent = IntentType.TASK
        elif "latest" in goal_lower or "trend" in goal_lower:
            intent = IntentType.NEWS
        elif "steps" in goal_lower or "how" in goal_lower:
            intent = IntentType.TASK

        # 4. Handle Follow-up
        if is_followup or intent == IntentType.TRANSFORM:
            # Compress output. Do not re-explain.
            intent = IntentType.TRANSFORM
            cleaned = self._squash_fluff(cleaned)

        # 5. Adaptive formatting by intent
        formatted_text = cleaned
        if intent in (IntentType.SIMPLE_LOOKUP, IntentType.DEFINITION):
            # Ultra clean: strip all bullets and extra breaks
            cleaned = re.sub(r"[\-\*•]\s+", "", cleaned).replace("\n", " ")
            sentences = re.split(r'(?<=[.!?])\s+', cleaned.strip())
            limit = 1 if intent == IntentType.SIMPLE_LOOKUP else 2
            formatted_text = " ".join(sentences[:limit])
            if formatted_text and not formatted_text.endswith((".", "!", "?")):
                formatted_text += "."
            
        elif intent == IntentType.COMPARISON:
            # Ensure "Verdict" sharpness
            if "Verdict:" not in formatted_text and "verdict:" not in formatted_text.lower():
                formatted_text = f"{formatted_text}\n\n**Verdict:** Final comparison insights pending detailed analysis."

        # 6. Enforce hard max word limits by intent (test + safety guard).
        max_words = self.MAX_WORDS.get(intent, 600)
        words = formatted_text.split()
        was_truncated = False
        if len(words) > max_words:
            formatted_text = " ".join(words[:max_words]).strip()
            if not formatted_text.endswith((".", "!", "?")):
                formatted_text += "..."
            was_truncated = True

        # 7. Edge Rules: Uncertainty
        confidence_display = ""
        uncertainty_wrapper = False
        if confidence > 0:
            if confidence >= 0.9:
                confidence_display = "High"
            elif confidence >= 0.7:
                confidence_display = "Medium"
            else:
                confidence_display = "Low"
                uncertainty_wrapper = True

        if uncertainty_wrapper:
            formatted_text = f"⚠️ I couldn't find reliable data for this.\n\n{formatted_text}\n\n👉 Suggestion: Try refining the query or providing broader context."

        # Build response without rigid template bindings
        return FormattedResponse(
            direct_answer=formatted_text[:250] + "..." if len(formatted_text) > 250 else formatted_text,
            full_text=formatted_text,
            confidence_display=confidence_display,
            sources=sources or [],
            word_count=len(formatted_text.split()),
            was_truncated=was_truncated,
        )

    def _format_data_structures(self, text: str) -> str:
        """Detect and convert raw JSON/Dict tool outputs into human text."""
        import json
        import ast
        
        stripped = text.strip()
        
        # Check for JSON or Python dict-like structures
        if not ((stripped.startswith("{") and stripped.endswith("}")) or
                (stripped.startswith("[") and stripped.endswith("]")) or
                (stripped.startswith("{'") and stripped.endswith("}"))):
            return text

        # Try JSON first, then Python ast.literal_eval
        data = None
        try:
            data = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            try:
                data = ast.literal_eval(stripped)
            except (ValueError, SyntaxError):
                return text
        
        if data is None:
            return text

        if isinstance(data, dict):
            # Handle Serper/Search results
            if "results" in data and isinstance(data["results"], list):
                snippets = []
                for r in data["results"][:5]:
                    title = r.get("title", "No Title")
                    snippet = r.get("snippet", "")
                    if title and snippet:
                        snippets.append("- " + title + ": " + snippet)
                if snippets:
                    return "\n".join(snippets)
            
            # Handle knowledge_graph
            if "knowledge_graph" in data and data["knowledge_graph"]:
                kg = data["knowledge_graph"]
                parts = []
                if "title" in kg:
                    parts.append(kg["title"])
                if "description" in kg:
                    parts.append(kg["description"])
                if parts:
                    return ". ".join(parts)
            
            # Handle generic key-value (skip internal keys)
            skip_keys = {"query", "status", "success", "total_results", "knowledge_graph"}
            summary = []
            for k, v in data.items():
                if k in skip_keys:
                    continue
                summary.append(str(k) + ": " + str(v))
            if summary:
                return "\n".join(summary)

        if isinstance(data, list):
            items = []
            for item in data[:5]:
                if isinstance(item, dict):
                    title = item.get("title", item.get("name", ""))
                    snippet = item.get("snippet", item.get("description", ""))
                    if title:
                        items.append("- " + title + (": " + snippet if snippet else ""))
                else:
                    items.append("- " + str(item))
            if items:
                return "\n".join(items)

        return text


    def _squash_fluff(self, text: str) -> str:
        """Remove long intros ('Here is the answer...') from follow-ups."""
        fluff_patterns = [
            r"^Here is (the|an).*?:?\n*",
            r"^Sure,? (here is|let me).*?:?\n*",
            r"^Okay,? .*?:?\n*",
        ]
        squashed = text
        for pattern in fluff_patterns:
            squashed = re.sub(pattern, "", squashed, flags=re.I).strip()
        return squashed
