"""
TAOS Goal Validator — Validates and classifies user goals before planning.

Production features:
- Length and content validation
- Complexity classification (LOW/MEDIUM/HIGH)
- Injection / prompt attack detection
- Goal decomposition hints
- Safety checks for dangerous requests

PRD Reference: §15
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from taos.config.constants import GoalComplexity


# ═══════════════════════════════════════════════════════════
# VALIDATION RESULT
# ═══════════════════════════════════════════════════════════

@dataclass
class GoalValidationResult:
    """Result of goal validation with classification metadata."""

    is_valid: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    complexity: GoalComplexity = GoalComplexity.LOW
    requires_planning: bool = True
    estimated_steps: int = 1
    suggested_tools: List[str] = field(default_factory=list)

    def add_error(self, error: str) -> None:
        self.errors.append(error)
        self.is_valid = False

    def add_warning(self, warning: str) -> None:
        self.warnings.append(warning)


# ═══════════════════════════════════════════════════════════
# GOAL VALIDATOR
# ═══════════════════════════════════════════════════════════

# Patterns that suggest injection / prompt attack
_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"disregard\s+(all\s+)?prior",
    r"you\s+are\s+now\s+(?:a|an)\s+",
    r"system\s*:\s*",
    r"<\s*/?script\s*>",
    r"\\x[0-9a-fA-F]{2}",
    r"ADMIN_MODE|ROOT_ACCESS|BYPASS",
]

# Patterns that suggest dangerous intent
_SAFETY_PATTERNS = [
    (r"(?:hack|exploit|attack)\s+(?:a|the|this)\s+", "Potentially malicious intent detected"),
    (r"generate\s+(?:malware|virus|ransomware)", "Malware generation not supported"),
    (r"(?:steal|exfiltrate)\s+(?:data|credentials|passwords)", "Data theft not supported"),
    (r"bypass\s+(?:security|auth|authentication)", "Security bypass not supported"),
]

# Tool hint patterns
_TOOL_HINTS = {
    "web_search": [r"search\s+(?:for|the|online)", r"find\s+(?:information|data|results)", r"look\s+up", r"google"],
    "code_executor": [r"run\s+(?:code|python|script)", r"execute\s+(?:code|python)", r"calculate", r"compute"],
    "http_request": [r"fetch\s+(?:from|url|api)", r"call\s+(?:api|endpoint)", r"http\s+(?:get|post)"],
    "file_read": [r"read\s+(?:file|document)", r"open\s+(?:file|document)", r"load\s+(?:file|data)"],
    "file_write": [r"write\s+(?:to\s+)?(?:file|document)", r"save\s+(?:to\s+)?(?:file|data)", r"create\s+(?:file|document)"],
}


class GoalValidator:
    """
    Production goal validator with safety checks and complexity analysis.

    Validates user goals before the planner processes them.
    Rejects dangerous, empty, or malformed goals.
    """

    def __init__(
        self,
        min_length: int = 3,
        max_length: int = 2000,
        enable_safety_checks: bool = True,
        enable_injection_detection: bool = True,
    ) -> None:
        self._min_length = min_length
        self._max_length = max_length
        self._enable_safety = enable_safety_checks
        self._enable_injection = enable_injection_detection

        # Pre-compile regex patterns
        self._injection_re = [
            re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS
        ]
        self._safety_re = [
            (re.compile(p, re.IGNORECASE), msg) for p, msg in _SAFETY_PATTERNS
        ]
        self._tool_hint_re = {
            tool: [re.compile(p, re.IGNORECASE) for p in patterns]
            for tool, patterns in _TOOL_HINTS.items()
        }

    def validate(self, goal: str) -> GoalValidationResult:
        """
        Validate a user goal. Returns GoalValidationResult with
        errors, warnings, complexity, and tool hints.
        """
        result = GoalValidationResult()

        # ─── Basic checks ──────────────────
        if not goal or not goal.strip():
            result.add_error("Goal is empty")
            return result

        goal = goal.strip()

        if len(goal) < self._min_length:
            result.add_error(f"Goal is too short (min {self._min_length} characters)")

        if len(goal) > self._max_length:
            result.add_error(f"Goal is too long (max {self._max_length} characters)")

        # ─── Injection detection ───────────
        if self._enable_injection:
            self._check_injection(goal, result)

        # ─── Safety checks ─────────────────
        if self._enable_safety:
            self._check_safety(goal, result)

        # ─── Complexity classification ─────
        self._classify_complexity(goal, result)

        # ─── Tool hints ────────────────────
        self._detect_tool_hints(goal, result)

        return result

    def _check_injection(self, goal: str, result: GoalValidationResult) -> None:
        """Detect potential prompt injection attacks."""
        for pattern in self._injection_re:
            if pattern.search(goal):
                result.add_error(
                    "Goal contains potentially dangerous prompt injection pattern"
                )
                return  # One error is enough

    def _check_safety(self, goal: str, result: GoalValidationResult) -> None:
        """Check for dangerous or prohibited goal content."""
        for pattern, message in self._safety_re:
            if pattern.search(goal):
                result.add_error(message)
                return

    def _classify_complexity(self, goal: str, result: GoalValidationResult) -> None:
        """Classify goal complexity using heuristics."""
        words = goal.split()
        word_count = len(words)

        # Count complexity indicators
        conjunctions = sum(1 for w in words if w.lower() in {"and", "then", "also", "additionally"})
        has_multi_sentence = goal.count(".") > 1 or goal.count(";") > 0
        has_conditional = any(w.lower() in {"if", "unless", "when", "while"} for w in words)
        has_comparison = any(w.lower() in {"compare", "versus", "vs", "between", "against"} for w in words)

        # Complexity scoring
        score = 0
        score += min(word_count // 5, 4)         # Length contribution (0-4)
        score += conjunctions                     # Multi-task indicator
        score += 2 if has_multi_sentence else 0   # Multi-sentence
        score += 1 if has_conditional else 0      # Conditional logic
        score += 1 if has_comparison else 0       # Comparison task

        if score <= 2:
            result.complexity = GoalComplexity.LOW
            result.requires_planning = False
            result.estimated_steps = max(1, word_count // 8)
        elif score <= 5:
            result.complexity = GoalComplexity.MEDIUM
            result.requires_planning = True
            result.estimated_steps = max(2, word_count // 5)
        else:
            result.complexity = GoalComplexity.HIGH
            result.requires_planning = True
            result.estimated_steps = max(4, word_count // 4)

        # Cap estimated steps
        result.estimated_steps = min(result.estimated_steps, 15)

    def _detect_tool_hints(self, goal: str, result: GoalValidationResult) -> None:
        """Detect which tools the goal likely needs."""
        for tool_name, patterns in self._tool_hint_re.items():
            for pattern in patterns:
                if pattern.search(goal):
                    result.suggested_tools.append(tool_name)
                    break  # One match per tool is enough
