"""
TAOS Condition Checker — Evaluates if/else conditions against task results.

Supports operators: lt, gt, eq, ne, contains, changed, exists, not_exists

Used for conditional automation:
  "If stock drops 5%, notify me"
  "If price below ₹5000, alert"
  "If status changed, summarize"
"""

from __future__ import annotations

import re
from typing import Any, Optional, Tuple

from taos.core.tasks.task_model import ConditionConfig


class ConditionChecker:
    """
    Evaluates conditions against task execution results.

    Operators:
    - lt: less than (numeric)
    - gt: greater than (numeric)
    - lte: less than or equal
    - gte: greater than or equal
    - eq: equals
    - ne: not equals
    - contains: substring match
    - not_contains: no substring match
    - changed: value differs from previous
    - exists: field is present and non-null
    - not_exists: field is absent or null
    """

    def evaluate(
        self,
        condition: ConditionConfig,
        current_result: Any,
        previous_result: Optional[Any] = None,
    ) -> Tuple[bool, str]:
        """
        Evaluate a condition against current (and optionally previous) results.

        Returns:
            Tuple of (condition_met: bool, explanation: str)
        """
        if not condition.field or not condition.operator:
            return True, "No condition defined, always passes"

        # Extract the field value from the result
        value = self._extract_field(current_result, condition.field)

        # Evaluate based on operator
        operator = condition.operator.lower()

        try:
            if operator == "lt":
                met = self._to_number(value) < self._to_number(condition.threshold)
                return met, f"{condition.field}={value} < {condition.threshold}: {met}"

            elif operator == "gt":
                met = self._to_number(value) > self._to_number(condition.threshold)
                return met, f"{condition.field}={value} > {condition.threshold}: {met}"

            elif operator == "lte":
                met = self._to_number(value) <= self._to_number(condition.threshold)
                return met, f"{condition.field}={value} <= {condition.threshold}: {met}"

            elif operator == "gte":
                met = self._to_number(value) >= self._to_number(condition.threshold)
                return met, f"{condition.field}={value} >= {condition.threshold}: {met}"

            elif operator == "eq":
                met = str(value).lower() == str(condition.threshold).lower()
                return met, f"{condition.field}={value} == {condition.threshold}: {met}"

            elif operator == "ne":
                met = str(value).lower() != str(condition.threshold).lower()
                return met, f"{condition.field}={value} != {condition.threshold}: {met}"

            elif operator == "contains":
                met = str(condition.threshold).lower() in str(value).lower()
                return met, f"{condition.field} contains '{condition.threshold}': {met}"

            elif operator == "not_contains":
                met = str(condition.threshold).lower() not in str(value).lower()
                return met, f"{condition.field} not contains '{condition.threshold}': {met}"

            elif operator == "changed":
                if previous_result is None:
                    return False, "No previous result to compare"
                prev_value = self._extract_field(previous_result, condition.field)
                met = str(value) != str(prev_value)
                return met, f"{condition.field} changed from '{prev_value}' to '{value}': {met}"

            elif operator == "exists":
                met = value is not None
                return met, f"{condition.field} exists: {met}"

            elif operator == "not_exists":
                met = value is None
                return met, f"{condition.field} not exists: {met}"

            else:
                return False, f"Unknown operator: {operator}"

        except (TypeError, ValueError) as e:
            return False, f"Condition evaluation error: {e}"

    def _extract_field(self, result: Any, field: str) -> Any:
        """Extract a field value from various result types."""
        if result is None:
            return None

        # Dict access
        if isinstance(result, dict):
            # Support nested access: "data.price"
            parts = field.split(".")
            current = result
            for part in parts:
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    return None
            return current

        # String — try to find the value using regex
        if isinstance(result, str):
            # Try pattern: "field: value" or "field = value"
            pattern = rf"{re.escape(field)}[\s:=]+([^\n,]+)"
            match = re.search(pattern, result, re.I)
            if match:
                return match.group(1).strip()

            # Try to extract numbers if field suggests numeric
            if field.lower() in ("price", "cost", "amount", "value", "count", "total"):
                numbers = re.findall(r"[\d,]+\.?\d*", result)
                if numbers:
                    return numbers[0].replace(",", "")

            return result  # Return full string as fallback

        return str(result)

    def _to_number(self, value: Any) -> float:
        """Convert a value to a number for comparison."""
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            # Remove currency symbols, commas, and whitespace
            cleaned = re.sub(r"[₹$€£,\s]", "", value)
            # Extract first number from the string
            match = re.search(r"[\d]+\.?\d*", cleaned)
            if match:
                return float(match.group())
            return float(cleaned)
        return float(value)
