"""
TAOS Tests — Execution Tests.

Tests the executor, result handler, and output validator.
"""

from __future__ import annotations

import pytest

from taos.core.execution.result_handler import ResultHandler
from taos.core.state.state_schema import PlanStep, StepResult, GlobalState
from taos.core.validation.output_validator import OutputValidator


# ═══════════════════════════════════════════════════════════
# RESULT HANDLER TESTS
# ═══════════════════════════════════════════════════════════

class TestResultHandler:
    @pytest.fixture
    def handler(self):
        return ResultHandler()

    def test_process_successful_result(self, handler):
        result = StepResult(
            step_id="s1",
            result="Some output data",
            success=True,
            cost=0.001,
        )
        step = PlanStep(id="s1", action="Test action")
        state = GlobalState(goal="Test goal")

        processed = handler.process(result, step, state)
        assert processed.success is True
        assert processed.step_id == "s1"

    def test_truncate_large_output(self, handler):
        large_output = "x" * 100_000
        result = StepResult(
            step_id="s1",
            result=large_output,
            success=True,
        )
        step = PlanStep(id="s1", action="Test")
        state = GlobalState(goal="Test")

        processed = handler.process(result, step, state)
        assert len(str(processed.result)) <= 51_000  # 50KB + some overhead

    def test_aggregate_results(self, handler):
        results = [
            StepResult(step_id="s1", result="Result 1", success=True),
            StepResult(step_id="s2", result="Result 2", success=True),
            StepResult(step_id="s3", result=None, success=False, error="Failed"),
        ]
        aggregated = handler.aggregate_results(results)
        assert "final_output" in aggregated
        assert "all_results" in aggregated


# ═══════════════════════════════════════════════════════════
# OUTPUT VALIDATOR TESTS
# ═══════════════════════════════════════════════════════════

class TestOutputValidator:
    @pytest.fixture
    def validator(self):
        return OutputValidator()

    def test_valid_output(self, validator):
        result = validator.validate("This is a valid output")
        assert result.is_valid is True
        assert result.sanitized_output == "This is a valid output"

    def test_empty_output_invalid(self, validator):
        result = validator.validate("")
        assert result.is_valid is False

    def test_email_redaction(self, validator):
        result = validator.validate("Contact us at test@example.com for help")
        assert "[EMAIL_REDACTED]" in result.sanitized_output
        assert result.redactions_applied > 0

    def test_phone_redaction(self, validator):
        result = validator.validate("Call us at (555) 123-4567")
        assert "[PHONE_REDACTED]" in result.sanitized_output

    def test_ssn_redaction(self, validator):
        result = validator.validate("SSN: 123-45-6789")
        assert "[SSN_REDACTED]" in result.sanitized_output

    def test_api_key_redaction(self, validator):
        result = validator.validate("Use key sk_test_abcdefghijklmnopqrstuvwxyz")
        assert "[API_KEY_REDACTED]" in result.sanitized_output

    def test_output_truncation(self):
        validator = OutputValidator(max_output_length=100)
        result = validator.validate("x" * 200)
        assert len(result.sanitized_output) == 100
        assert len(result.warnings) > 0

    def test_completeness_check(self, validator):
        result = validator.validate(
            "Python 3.12 has great new features",
            goal="Tell me about Python 3.12 features"
        )
        assert result.completeness_score > 0.5

    def test_low_completeness_warning(self, validator):
        result = validator.validate(
            "The weather is nice today",
            goal="Search for Python release notes and summarize"
        )
        # Output is unrelated to goal
        assert result.completeness_score < 0.5

    def test_structured_output_validation(self, validator):
        output = {"name": "test", "value": 42}
        result = validator.validate_structured(output, required_fields=["name"])
        assert result.is_valid is True

    def test_structured_output_missing_field(self, validator):
        output = {"name": "test"}
        result = validator.validate_structured(output, required_fields=["name", "value"])
        assert result.is_valid is False

    def test_pii_disabled(self):
        validator = OutputValidator(enable_pii_redaction=False)
        result = validator.validate("Email: test@example.com")
        assert "[EMAIL_REDACTED]" not in result.sanitized_output
