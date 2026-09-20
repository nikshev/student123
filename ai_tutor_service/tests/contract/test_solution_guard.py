# verifies: FR-002-06
"""
Contract tests for SolutionGuard (T-037).

The SolutionGuard contract defines the interface between the tutoring
pipeline and the solution-detection guard. This test file documents and
verifies that contract BEFORE the implementation exists (T-038).

Contract summary (from specs/002-ai-tutor/contracts/solution-guard.md):
- SolutionGuard(config, client=None) receives config from tutor_config.yaml.
- check(candidate_text) returns dict with EXACTLY two fields: 
  contains_solution (bool) and reason (non-empty str).
- Model/prompt/timeout come from YAML (guard_model_id, guard_timeout_seconds=7, prompts.guard).
- malformed_response / timeout / provider_error are FAIL-CLOSED:
  candidate is not shown, request returns controlled 502/504, operational error is logged.
  Technical failure is NOT counted as a semantic block in gate numerator.
- Candidate text must NOT appear in reason or operational logs.
- Technical failure != semantic block: true fixture → semantic block
  (contains_solution=True, technical_failure=False); malformed →
  technical_failure=True and NOT semantic block numerator.

Current state: ai_tutor_service.guard.solution_guard does not exist yet.
This test is RED with ModuleNotFoundError until T-038 implements it.
"""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from ai_tutor_service.config import load_tutor_config
from ai_tutor_service.providers.client import LLMClient, LLMError

# Load config for testing
CONFIG_PATH = Path(__file__).resolve().parents[3] / "ai_tutor_service" / "tutor_config.yaml"
CONFIG = load_tutor_config(CONFIG_PATH)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "llm" / "guard"

# Expected output shape per contract
EXPECTED_OUTPUT_FIELDS = {"contains_solution", "reason"}


def _load_fixture(name: str) -> dict:
    """Load a JSON fixture from the guard fixtures directory."""
    with open(FIXTURES_DIR / name, encoding="utf-8") as f:
        return json.load(f)


class TestSolutionGuardContract:
    """Contract tests for SolutionGuard interface."""

    def test_exact_inputs_output(self):
        """
        Contract: SolutionGuard(config) has check(candidate_text) -> dict.
        Output has EXACTLY two fields: contains_solution (bool), reason (non-empty str).
        """
        from ai_tutor_service.guard.solution_guard import SolutionGuard, GuardTechnicalError

        guard = SolutionGuard(CONFIG)
        with patch.object(LLMClient, 'guard', return_value=_load_fixture("valid_false.json")) as mock_guard:
            result = guard.check("x = 3")
            # Output must have exactly two fields
            assert set(result.keys()) == EXPECTED_OUTPUT_FIELDS, (
                f"Output must have exactly {EXPECTED_OUTPUT_FIELDS}, got {set(result.keys())}"
            )
            assert isinstance(result["contains_solution"], bool), (
                "contains_solution must be boolean"
            )
            assert isinstance(result["reason"], str), (
                "reason must be string"
            )
            assert len(result["reason"]) > 0, (
                "reason must be non-empty"
            )

    def test_valid_false_fixture(self):
        """
        Contract: LLMClient.guard returns valid_false fixture → contains_solution=False with reason.
        """
        from ai_tutor_service.guard.solution_guard import SolutionGuard, GuardTechnicalError

        guard = SolutionGuard(CONFIG)
        false_fixture = _load_fixture("valid_false.json")
        
        # Mock LLMClient.guard at class level
        with patch.object(LLMClient, 'guard', return_value=false_fixture) as mock_guard:
            result = guard.check("This is not a solution")
            assert result["contains_solution"] is False, (
                "valid_false fixture must yield contains_solution=False"
            )
            assert result["reason"] != "", (
                "reason must be non-empty even for false verdict"
            )
            assert set(result.keys()) == EXPECTED_OUTPUT_FIELDS, (
                f"Output must have exactly {EXPECTED_OUTPUT_FIELDS}"
            )
            # Verify the call
            mock_guard.assert_called_once()
            call_args = mock_guard.call_args[0]  # positional args: prompt, model_id, timeout
            assert call_args[1] == CONFIG["guard_model_id"]
            assert call_args[2] == CONFIG["guard_timeout_seconds"]
            assert CONFIG["prompts"]["guard"] in call_args[0]
            assert "This is not a solution" in call_args[0]

    def test_valid_true_fixture(self):
        """
        Contract: LLMClient.guard returns valid_true fixture → contains_solution=True with reason.
        """
        from ai_tutor_service.guard.solution_guard import SolutionGuard, GuardTechnicalError

        guard = SolutionGuard(CONFIG)
        true_fixture = _load_fixture("valid_true.json")
        
        with patch.object(LLMClient, 'guard', return_value=true_fixture) as mock_guard:
            result = guard.check("x = 3")
            assert result["contains_solution"] is True, (
                "valid_true fixture must yield contains_solution=True"
            )
            assert result["reason"] != "", (
                "reason must be non-empty even for true verdict"
            )
            assert set(result.keys()) == EXPECTED_OUTPUT_FIELDS, (
                f"Output must have exactly {EXPECTED_OUTPUT_FIELDS}"
            )
            mock_guard.assert_called_once()
            call_args = mock_guard.call_args[0]
            assert call_args[1] == CONFIG["guard_model_id"]
            assert call_args[2] == CONFIG["guard_timeout_seconds"]
            assert CONFIG["prompts"]["guard"] in call_args[0]
            assert "x = 3" in call_args[0]

    def test_model_prompt_timeout_from_yaml(self):
        """
        Contract: guard calls LLMClient.guard with:
        - prompt containing prompts.guard from YAML + candidate_text
        - model_id == config["guard_model_id"]
        - timeout == config["guard_timeout_seconds"] (== 7, assert == 7 from YAML, not hardcoded)
        """
        from ai_tutor_service.guard.solution_guard import SolutionGuard, GuardTechnicalError

        guard = SolutionGuard(CONFIG)
        false_fixture = _load_fixture("valid_false.json")
        
        with patch.object(LLMClient, 'guard', return_value=false_fixture) as mock_guard:
            guard.check("test candidate")
            mock_guard.assert_called_once()
            call_args = mock_guard.call_args[0]
            # Check model_id from config
            assert call_args[1] == CONFIG["guard_model_id"]
            # Check timeout from config
            assert call_args[2] == CONFIG["guard_timeout_seconds"]
            # Check prompt contains guard template and candidate
            assert CONFIG["prompts"]["guard"] in call_args[0]
            assert "test candidate" in call_args[0]
            # Verify timeout is 7 from YAML (not hardcoded)
            assert CONFIG["guard_timeout_seconds"] == 7

    def test_fail_closed_malformed(self):
        """
        Contract: malformed_response → fail-closed (candidate not shown, 502).
        Technical failure is NOT semantic block/gate numerator.
        """
        from ai_tutor_service.guard.solution_guard import SolutionGuard, GuardTechnicalError

        guard = SolutionGuard(CONFIG)
        
        with patch.object(LLMClient, 'guard', side_effect=LLMError(
            "Provider returned malformed JSON", "malformed_response"
        )) as mock_guard:
            with pytest.raises(GuardTechnicalError) as exc_info:
                guard.check("any candidate")
            # Verify it's a technical failure with correct error_type
            assert exc_info.value.error_type == "malformed_response"
            # Verify it does NOT contain contains_solution (not semantic block)
            assert not hasattr(exc_info.value, 'contains_solution')
            # Verify the call was made
            mock_guard.assert_called_once()

    def test_fail_closed_timeout(self):
        """
        Contract: timeout → fail-closed (candidate not shown, 504).
        Technical failure is NOT semantic block/gate numerator.
        """
        from ai_tutor_service.guard.solution_guard import SolutionGuard, GuardTechnicalError

        guard = SolutionGuard(CONFIG)
        
        with patch.object(LLMClient, 'guard', side_effect=LLMError(
            "Request timed out", "timeout"
        )) as mock_guard:
            with pytest.raises(GuardTechnicalError) as exc_info:
                guard.check("any candidate")
            assert exc_info.value.error_type == "timeout"
            assert not hasattr(exc_info.value, 'contains_solution')
            mock_guard.assert_called_once()

    def test_fail_closed_provider_error(self):
        """
        Contract: provider_error → fail-closed (candidate not shown, 502/503).
        Technical failure is NOT semantic block/gate numerator.
        """
        from ai_tutor_service.guard.solution_guard import SolutionGuard, GuardTechnicalError

        guard = SolutionGuard(CONFIG)
        
        with patch.object(LLMClient, 'guard', side_effect=LLMError(
            "Model overloaded", "provider_error"
        )) as mock_guard:
            with pytest.raises(GuardTechnicalError) as exc_info:
                guard.check("any candidate")
            assert exc_info.value.error_type == "provider_error"
            assert not hasattr(exc_info.value, 'contains_solution')
            mock_guard.assert_called_once()

    def test_operational_log(self, caplog):
        """
        Contract: guard call is logged operationally (usage/error type).
        Verify via captured log (caplog) that a guard record exists.
        """
        from ai_tutor_service.guard.solution_guard import SolutionGuard, GuardTechnicalError

        guard = SolutionGuard(CONFIG)
        false_fixture = _load_fixture("valid_false.json")
        
        with caplog.at_level("INFO"):
            with patch.object(LLMClient, 'guard', return_value=false_fixture):
                guard.check("test candidate")
        
        # Check that we have at least one log record from our logger
        guard_logs = [record for record in caplog.records 
                     if record.name == "ai_tutor_service.guard"]
        assert len(guard_logs) >= 1
        # Verify the log does NOT contain candidate text
        for record in guard_logs:
            assert "test candidate" not in record.getMessage()

    def test_candidate_not_in_reason(self):
        """
        Contract: candidate_text must NOT appear in reason.
        """
        from ai_tutor_service.guard.solution_guard import SolutionGuard, GuardTechnicalError

        guard = SolutionGuard(CONFIG)
        candidate = "x = 3, because the discriminant is negative"
        false_fixture = _load_fixture("valid_false.json")
        true_fixture = _load_fixture("valid_true.json")
        
        # Test false case
        with patch.object(LLMClient, 'guard', return_value=false_fixture):
            result = guard.check(candidate)
            assert candidate not in result["reason"], (
                "candidate text must NOT appear in reason for false verdict"
            )
        
        # Test true case
        with patch.object(LLMClient, 'guard', return_value=true_fixture):
            result = guard.check(candidate)
            assert candidate not in result["reason"], (
                "candidate text must NOT appear in reason for true verdict"
            )

    def test_technical_failure_not_semantic_block(self):
        """
        Contract: Technical failure != semantic block.
        - true fixture → semantic block (contains_solution=True, technical_failure=False)
        - malformed/timeout/provider → technical_failure=True, NOT semantic block numerator
        """
        from ai_tutor_service.guard.solution_guard import SolutionGuard, GuardTechnicalError

        guard = SolutionGuard(CONFIG)
        true_fixture = _load_fixture("valid_true.json")
        
        # Test semantic block case (valid_true)
        with patch.object(LLMClient, 'guard', return_value=true_fixture):
            result = guard.check("x = 3")
            assert result["contains_solution"] is True  # semantic block
        
        # Test technical failure cases - should raise GuardTechnicalError
        for error_type in ["malformed_response", "timeout", "provider_error"]:
            with patch.object(LLMClient, 'guard', side_effect=LLMError(
                "Some error", error_type
            )):
                with pytest.raises(GuardTechnicalError) as exc_info:
                    guard.check("any candidate")
                # Verify it's a technical error (has error_type)
                assert hasattr(exc_info.value, 'error_type')
                assert exc_info.value.error_type == error_type
                # Verify it does NOT have contains_solution (not semantic block)
                assert not hasattr(exc_info.value, 'contains_solution')

    def test_candidate_unchanged(self):
        """
        Contract: guard does not modify candidate; mock.guard receives candidate literally.
        """
        from ai_tutor_service.guard.solution_guard import SolutionGuard, GuardTechnicalError

        guard = SolutionGuard(CONFIG)
        candidate = "original candidate text"
        false_fixture = _load_fixture("valid_false.json")
        
        with patch.object(LLMClient, 'guard', return_value=false_fixture) as mock_guard:
            guard.check(candidate)
            # Verify the mock was called with candidate literally in prompt
            mock_guard.assert_called_once()
            call_args = mock_guard.call_args[0]
            prompt = call_args[0]
            # The candidate should appear literally in the prompt
            assert candidate in prompt
            # Verify candidate is unchanged (we didn't modify it)
            assert candidate == "original candidate text"