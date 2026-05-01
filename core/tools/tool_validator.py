"""
TAOS Tool Validator — Validates tool inputs against schemas.

Ensures tool inputs match expected schema before execution.
Prevents invalid data from reaching tool handlers.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from taos.core.tools.registry import ToolDefinition, ToolRegistry


class ToolValidationError(Exception):
    """Raised when tool input validation fails."""
    pass


class ToolValidator:
    """Validates tool inputs against registered schemas."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def validate_input(
        self,
        tool_name: str,
        tool_input: Optional[Dict[str, Any]],
    ) -> List[str]:
        """
        Validate tool input against its schema.
        Returns list of violations (empty = valid).
        """
        tool = self._registry.get(tool_name)
        tool_input = tool_input or {}
        violations: List[str] = []

        schema = tool.input_schema
        if not schema:
            return violations  # No schema = accept anything

        # Check required fields
        for field_name, field_type in schema.items():
            is_required = not str(field_type).startswith("Optional")
            if is_required and field_name not in tool_input:
                violations.append(f"Missing required field: '{field_name}'")

        # Check for unknown fields
        for field_name in tool_input:
            if field_name not in schema:
                violations.append(f"Unknown field: '{field_name}'")

        # Type checking (basic)
        for field_name, expected_type in schema.items():
            if field_name in tool_input:
                value = tool_input[field_name]
                if expected_type == "str" and not isinstance(value, str):
                    violations.append(f"Field '{field_name}' must be str, got {type(value).__name__}")
                elif expected_type == "int" and not isinstance(value, int):
                    violations.append(f"Field '{field_name}' must be int, got {type(value).__name__}")
                elif expected_type == "float" and not isinstance(value, (int, float)):
                    violations.append(f"Field '{field_name}' must be float, got {type(value).__name__}")
                elif expected_type == "bool" and not isinstance(value, bool):
                    violations.append(f"Field '{field_name}' must be bool, got {type(value).__name__}")
                elif expected_type == "dict" and not isinstance(value, dict):
                    violations.append(f"Field '{field_name}' must be dict, got {type(value).__name__}")
                elif expected_type == "list" and not isinstance(value, list):
                    violations.append(f"Field '{field_name}' must be list, got {type(value).__name__}")

        return violations

    def validate_or_raise(
        self,
        tool_name: str,
        tool_input: Optional[Dict[str, Any]],
    ) -> None:
        """Validate and raise ToolValidationError on failure."""
        violations = self.validate_input(tool_name, tool_input)
        if violations:
            raise ToolValidationError(
                f"Tool '{tool_name}' input validation failed:\n" +
                "\n".join(f"  - {v}" for v in violations)
            )
