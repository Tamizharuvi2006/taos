"""
TAOS Output Validator — Validates and sanitizes agent output before delivery.

Production features:
- PII detection and redaction (emails, phones, SSNs, credit cards)
- API key / secret detection and redaction
- Output length enforcement
- Completeness checks against the original goal
- Structured output validation
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ═══════════════════════════════════════════════════════════
# VALIDATION RESULT
# ═══════════════════════════════════════════════════════════

@dataclass
class OutputValidationResult:
    """Result of output validation."""

    is_valid: bool = True
    sanitized_output: str = ""
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    redactions_applied: int = 0
    completeness_score: float = 1.0

    def add_error(self, error: str) -> None:
        self.errors.append(error)
        self.is_valid = False

    def add_warning(self, warning: str) -> None:
        self.warnings.append(warning)


# ═══════════════════════════════════════════════════════════
# REDACTION PATTERNS
# ═══════════════════════════════════════════════════════════

_PII_PATTERNS = [
    # Email addresses
    (re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"), "[EMAIL_REDACTED]"),
    # US phone numbers
    (re.compile(r"\b(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"), "[PHONE_REDACTED]"),
    # SSN
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN_REDACTED]"),
    # Credit card numbers (basic)
    (re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"), "[CARD_REDACTED]"),
]

_SECRET_PATTERNS = [
    # API keys (common formats)
    (re.compile(r"(?:sk|pk|api|key|token|secret|password)[-_]?[a-zA-Z0-9_]{20,}",
                re.IGNORECASE), "[API_KEY_REDACTED]"),
    # Bearer tokens
    (re.compile(r"Bearer\s+[a-zA-Z0-9._\-]{20,}"), "Bearer [TOKEN_REDACTED]"),
    # AWS keys
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[AWS_KEY_REDACTED]"),
    # Generic long hex/base64 secrets
    (re.compile(r"\b[a-fA-F0-9]{32,}\b"), "[HEX_SECRET_REDACTED]"),
]


# ═══════════════════════════════════════════════════════════
# OUTPUT VALIDATOR
# ═══════════════════════════════════════════════════════════

class OutputValidator:
    """
    Validates and sanitizes agent output before returning to the user.

    Responsibilities:
    1. Redact PII (emails, phones, SSNs, credit cards)
    2. Redact secrets/API keys
    3. Enforce output length limits
    4. Check output completeness vs goal
    5. Validate structured output format
    """

    def __init__(
        self,
        max_output_length: int = 50_000,
        enable_pii_redaction: bool = True,
        enable_secret_redaction: bool = True,
        min_completeness: float = 0.3,
    ) -> None:
        self._max_output_length = max_output_length
        self._enable_pii = enable_pii_redaction
        self._enable_secrets = enable_secret_redaction
        self._min_completeness = min_completeness

    def validate(
        self,
        output: str,
        goal: str = "",
        strict: bool = False,
    ) -> OutputValidationResult:
        """
        Validate and sanitize agent output.

        Args:
            output: The raw agent output to validate.
            goal: The original goal (for completeness checking).
            strict: If True, apply stricter validation rules.

        Returns:
            OutputValidationResult with sanitized output and metadata.
        """
        result = OutputValidationResult()

        # ─── Empty check ───────────────────
        if not output or not output.strip():
            result.add_error("Output is empty")
            result.completeness_score = 0.0
            return result

        sanitized = output.strip()

        # ─── Length enforcement ─────────────
        if len(sanitized) > self._max_output_length:
            sanitized = sanitized[:self._max_output_length]
            result.add_warning(
                f"Output truncated from {len(output)} to {self._max_output_length} characters"
            )

        # ─── PII redaction ─────────────────
        if self._enable_pii:
            sanitized, pii_count = self._redact_patterns(sanitized, _PII_PATTERNS)
            result.redactions_applied += pii_count
            if pii_count > 0:
                result.add_warning(f"Redacted {pii_count} PII pattern(s)")

        # ─── Secret redaction ──────────────
        if self._enable_secrets:
            sanitized, secret_count = self._redact_patterns(sanitized, _SECRET_PATTERNS)
            result.redactions_applied += secret_count
            if secret_count > 0:
                result.add_warning(f"Redacted {secret_count} secret/key pattern(s)")

        # ─── Completeness check ────────────
        if goal:
            result.completeness_score = self._check_completeness(sanitized, goal)
            if result.completeness_score < self._min_completeness:
                if strict:
                    result.add_error(
                        f"Output completeness too low: {result.completeness_score:.2f} "
                        f"(min {self._min_completeness})"
                    )
                else:
                    result.add_warning(
                        f"Output may be incomplete: score {result.completeness_score:.2f}"
                    )

        # ─── Quality checks ────────────────
        if strict:
            self._quality_checks(sanitized, result)

        result.sanitized_output = sanitized
        return result

    def validate_structured(
        self,
        output: Dict[str, Any],
        required_fields: Optional[List[str]] = None,
    ) -> OutputValidationResult:
        """Validate a structured (dict) output."""
        result = OutputValidationResult()

        if not output:
            result.add_error("Structured output is empty")
            return result

        if required_fields:
            for field_name in required_fields:
                if field_name not in output:
                    result.add_error(f"Missing required field: '{field_name}'")
                elif output[field_name] is None:
                    result.add_warning(f"Field '{field_name}' is null")

        # Serialize and sanitize string values
        sanitized_dict = {}
        for key, value in output.items():
            if isinstance(value, str):
                sub_result = self.validate(value)
                sanitized_dict[key] = sub_result.sanitized_output
                result.redactions_applied += sub_result.redactions_applied
                result.warnings.extend(sub_result.warnings)
            else:
                sanitized_dict[key] = value

        result.sanitized_output = str(sanitized_dict)
        return result

    # ─── Internal ─────────────────────────────────────────

    def _redact_patterns(
        self,
        text: str,
        patterns: list,
    ) -> tuple[str, int]:
        """Apply redaction patterns. Returns (sanitized_text, count)."""
        count = 0
        for pattern, replacement in patterns:
            matches = pattern.findall(text)
            if matches:
                count += len(matches)
                text = pattern.sub(replacement, text)
        return text, count

    def _check_completeness(self, output: str, goal: str) -> float:
        """
        Heuristic completeness check.

        Measures how many key terms from the goal appear in the output.
        """
        # Extract significant words from goal (skip stopwords)
        stopwords = {
            "a", "an", "the", "is", "are", "was", "were", "be", "been",
            "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "could", "should", "may", "might", "can", "shall",
            "to", "of", "in", "for", "on", "with", "at", "by", "from",
            "as", "into", "through", "during", "before", "after",
            "and", "but", "or", "not", "no", "if", "then", "than",
            "that", "this", "it", "i", "me", "my", "we", "you", "your",
        }

        goal_words = set(goal.lower().split())
        key_words = goal_words - stopwords
        key_words = {w for w in key_words if len(w) > 2}

        if not key_words:
            return 1.0  # No key words to check

        output_lower = output.lower()
        matches = sum(1 for w in key_words if w in output_lower)

        return matches / len(key_words)

    def _quality_checks(self, output: str, result: OutputValidationResult) -> None:
        """Additional quality checks for strict mode."""
        # Check for error-only output
        error_indicators = ["error:", "exception:", "traceback", "failed to"]
        lower = output.lower()

        error_lines = sum(1 for line in output.split("\n") if any(
            ind in line.lower() for ind in error_indicators
        ))
        total_lines = max(output.count("\n") + 1, 1)

        if error_lines / total_lines > 0.5:
            result.add_warning("Output appears to be mostly error messages")

        # Check for placeholder/incomplete output
        placeholder_patterns = [
            r"\[TODO\]",
            r"\[PLACEHOLDER\]",
            r"\.\.\.\s*$",
            r"not\s+implemented",
        ]
        for pattern in placeholder_patterns:
            if re.search(pattern, output, re.IGNORECASE):
                result.add_warning("Output may contain placeholder content")
                break
