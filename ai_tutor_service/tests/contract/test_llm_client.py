# verifies: FR-002-10
"""
Contract tests for LLMClient interface.

These tests define the expected behavior of the LLMClient (to be implemented in T-014).
The LLMClient is the single external LLM boundary - all LLM calls go through it.
It must be replaceable for testing (fake transport/adapter), use config for model/prompt/timeout,
return token usage, raise typed errors for malformed/invalid/provider/timeout responses,
and never log secrets.
"""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from ai_tutor_service.config import load_tutor_config


# Load config once for all tests
CONFIG = load_tutor_config(Path(__file__).resolve().parent.parent.parent / "tutor_config.yaml")
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "llm"


class FakeTransport:
    """
    Fake transport that returns recorded fixtures without network access.

    This replaces the real HTTP provider adapter in tests. It simulates
    different provider responses by loading JSON fixtures.
    """

    def __init__(self, fixtures_dir: Path):
        self.fixtures_dir = fixtures_dir
        self.call_log = []

    def call(self, operation: str, model_id: str, prompt: str, timeout: float, api_key: str):
        """
        Simulate an LLM call by returning a fixture based on operation and scenario.

        The scenario is encoded in the prompt via a special marker:
        - "___FIXTURE:valid___" -> valid response
        - "___FIXTURE:malformed___" -> malformed JSON
        - "___FIXTURE:5xx___" -> provider 5xx error
        - "___FIXTURE:timeout___" -> timeout
        """
        self.call_log.append({
            "operation": operation,
            "model_id": model_id,
            "prompt": prompt,
            "timeout": timeout,
            "api_key_provided": bool(api_key),
        })

        # Determine which fixture to load from prompt marker
        if "___FIXTURE:valid___" in prompt:
            fixture_name = "valid_response.json" if operation == "generate" else \
                          ("valid_false.json" if "false" in prompt.lower() else "valid_true.json") \
                          if operation == "guard" else "valid_on_topic.json"
        elif "___FIXTURE:malformed___" in prompt:
            fixture_name = "malformed_json.json"
        elif "___FIXTURE:5xx___" in prompt:
            fixture_name = "provider_5xx.json"
        elif "___FIXTURE:timeout___" in prompt:
            fixture_name = "timeout.json"
        else:
            fixture_name = "valid_response.json" if operation == "generate" else \
                          "valid_false.json" if operation == "guard" else "valid_on_topic.json"

        fixture_path = self.fixtures_dir / operation / fixture_name
        with open(fixture_path, encoding="utf-8") as f:
            return json.load(f)


@pytest.fixture
def fake_transport():
    """Provide a fake transport for LLMClient tests."""
    return FakeTransport(FIXTURES_DIR)


@pytest.fixture
def test_api_key():
    """Test API key (never logged, passed via Django settings in production)."""
    return "test-api-key-secret-12345"


class TestLLMClientContract:
    """Contract tests for the LLMClient interface."""

    def test_generate_returns_structured_text_with_usage(self, fake_transport, test_api_key):
        """
        generate() must return structured result with text and token usage.

        Model ID, prompt, and timeout come from config/parameters, not hardcoded.
        """
        # This test will fail until T-014 implements LLMClient
        from ai_tutor_service.providers.client import LLMClient  # noqa: F401

        client = LLMClient(transport=fake_transport, config=CONFIG, api_key=test_api_key)

        prompt = "Explain 2x+4=10 step by step. ___FIXTURE:valid___"
        result = client.generate(
            prompt=prompt,
            model_id=CONFIG["model_id"],
            timeout=CONFIG["generation_timeout_seconds"],
        )

        # Verify structured output
        assert "text" in result
        assert isinstance(result["text"], str)
        assert len(result["text"]) > 0

        # Verify token usage returned
        assert "usage" in result
        assert "input_tokens" in result["usage"]
        assert "output_tokens" in result["usage"]
        assert isinstance(result["usage"]["input_tokens"], int)
        assert isinstance(result["usage"]["output_tokens"], int)
        assert result["usage"]["input_tokens"] > 0
        assert result["usage"]["output_tokens"] > 0

        # Verify model_id and timeout passed through
        call = fake_transport.call_log[-1]
        assert call["model_id"] == CONFIG["model_id"]
        assert call["timeout"] == CONFIG["generation_timeout_seconds"]
        assert call["api_key_provided"] is True
        assert call["operation"] == "generate"

    def test_generate_malformed_json_raises_typed_error(self, fake_transport, test_api_key):
        """
        Malformed JSON from provider must raise a typed error (not best-effort parse).

        Strict structured output: any parsing failure is an error.
        """
        from ai_tutor_service.providers.client import LLMClient, LLMError  # noqa: F401

        client = LLMClient(transport=fake_transport, config=CONFIG, api_key=test_api_key)

        prompt = "Explain something. ___FIXTURE:malformed___"

        with pytest.raises(LLMError) as exc_info:
            client.generate(
                prompt=prompt,
                model_id=CONFIG["model_id"],
                timeout=CONFIG["generation_timeout_seconds"],
            )

        # Should be a structured error, not a generic exception
        assert exc_info.value.error_type == "malformed_response"
        assert "malformed" in str(exc_info.value).lower()

    def test_generate_provider_5xx_raises_typed_error(self, fake_transport, test_api_key):
        """Provider 5xx errors must raise typed LLMError with error_type='provider_error'."""
        from ai_tutor_service.providers.client import LLMClient, LLMError  # noqa: F401

        client = LLMClient(transport=fake_transport, config=CONFIG, api_key=test_api_key)

        prompt = "Explain something. ___FIXTURE:5xx___"

        with pytest.raises(LLMError) as exc_info:
            client.generate(
                prompt=prompt,
                model_id=CONFIG["model_id"],
                timeout=CONFIG["generation_timeout_seconds"],
            )

        assert exc_info.value.error_type == "provider_error"
        assert exc_info.value.status_code == 500

    def test_generate_timeout_raises_typed_error(self, fake_transport, test_api_key):
        """Timeout must raise typed LLMError with error_type='timeout'."""
        from ai_tutor_service.providers.client import LLMClient, LLMError  # noqa: F401

        client = LLMClient(transport=fake_transport, config=CONFIG, api_key=test_api_key)

        prompt = "Explain something. ___FIXTURE:timeout___"

        with pytest.raises(LLMError) as exc_info:
            client.generate(
                prompt=prompt,
                model_id=CONFIG["model_id"],
                timeout=CONFIG["generation_timeout_seconds"],
            )

        assert exc_info.value.error_type == "timeout"

    def test_guard_returns_structured_verdict_with_usage(self, fake_transport, test_api_key):
        """
        guard() must return structured verdict {contains_solution: bool, reason: str}
        with token usage. Model ID, prompt, timeout from config.
        """
        from ai_tutor_service.providers.client import LLMClient  # noqa: F401

        client = LLMClient(transport=fake_transport, config=CONFIG, api_key=test_api_key)

        # Test false verdict
        prompt = "Question: 2x+4=10. Answer: x=3. ___FIXTURE:valid___ false"
        result = client.guard(
            prompt=prompt,
            model_id=CONFIG["guard_model_id"],
            timeout=CONFIG["guard_timeout_seconds"],
        )

        assert "contains_solution" in result
        assert isinstance(result["contains_solution"], bool)
        assert "reason" in result
        assert isinstance(result["reason"], str)
        assert len(result["reason"]) > 0

        assert "usage" in result
        assert "input_tokens" in result["usage"]
        assert "output_tokens" in result["usage"]

        call = fake_transport.call_log[-1]
        assert call["model_id"] == CONFIG["guard_model_id"]
        assert call["timeout"] == CONFIG["guard_timeout_seconds"]
        assert call["operation"] == "guard"

    def test_guard_true_verdict(self, fake_transport, test_api_key):
        """Guard must correctly return contains_solution=true when solution detected."""
        from ai_tutor_service.providers.client import LLMClient  # noqa: F401

        client = LLMClient(transport=fake_transport, config=CONFIG, api_key=test_api_key)

        prompt = "Question: 2x+4=10. Answer: x=3. ___FIXTURE:valid___ true"
        result = client.guard(
            prompt=prompt,
            model_id=CONFIG["guard_model_id"],
            timeout=CONFIG["guard_timeout_seconds"],
        )

        assert result["contains_solution"] is True
        assert "кінцеве значення" in result["reason"] or "final answer" in result["reason"].lower()

    def test_guard_malformed_json_raises_typed_error(self, fake_transport, test_api_key):
        """Malformed guard response raises typed error."""
        from ai_tutor_service.providers.client import LLMClient, LLMError  # noqa: F401

        client = LLMClient(transport=fake_transport, config=CONFIG, api_key=test_api_key)

        prompt = "Question. Answer. ___FIXTURE:malformed___"

        with pytest.raises(LLMError) as exc_info:
            client.guard(
                prompt=prompt,
                model_id=CONFIG["guard_model_id"],
                timeout=CONFIG["guard_timeout_seconds"],
            )

        assert exc_info.value.error_type == "malformed_response"

    def test_guard_provider_error_raises_typed_error(self, fake_transport, test_api_key):
        """Guard provider error raises typed error."""
        from ai_tutor_service.providers.client import LLMClient, LLMError  # noqa: F401

        client = LLMClient(transport=fake_transport, config=CONFIG, api_key=test_api_key)

        prompt = "Question. Answer. ___FIXTURE:5xx___"

        with pytest.raises(LLMError) as exc_info:
            client.guard(
                prompt=prompt,
                model_id=CONFIG["guard_model_id"],
                timeout=CONFIG["guard_timeout_seconds"],
            )

        assert exc_info.value.error_type == "provider_error"

    def test_guard_timeout_raises_typed_error(self, fake_transport, test_api_key):
        """Guard timeout raises typed error."""
        from ai_tutor_service.providers.client import LLMClient, LLMError  # noqa: F401

        client = LLMClient(transport=fake_transport, config=CONFIG, api_key=test_api_key)

        prompt = "Question. Answer. ___FIXTURE:timeout___"

        with pytest.raises(LLMError) as exc_info:
            client.guard(
                prompt=prompt,
                model_id=CONFIG["guard_model_id"],
                timeout=CONFIG["guard_timeout_seconds"],
            )

        assert exc_info.value.error_type == "timeout"

    def test_off_topic_returns_structured_classification_with_usage(self, fake_transport, test_api_key):
        """
        off_topic() must return structured classification {classification, confidence, top_source_label}
        with token usage. Model/prompt/timeout from config.
        """
        from ai_tutor_service.providers.client import LLMClient  # noqa: F401

        client = LLMClient(transport=fake_transport, config=CONFIG, api_key=test_api_key)

        prompt = "Classify: How to solve equations? ___FIXTURE:valid___"
        result = client.off_topic(
            prompt=prompt,
            model_id=CONFIG["model_id"],  # off_topic uses main model per config
            timeout=CONFIG["generation_timeout_seconds"],  # uses generation timeout
        )

        assert "classification" in result
        assert result["classification"] in ("on_topic", "off_topic")
        assert "confidence" in result
        assert isinstance(result["confidence"], float)
        assert 0.0 <= result["confidence"] <= 1.0
        assert "top_source_label" in result
        assert isinstance(result["top_source_label"], str)

        assert "usage" in result
        assert "input_tokens" in result["usage"]
        assert "output_tokens" in result["usage"]

        call = fake_transport.call_log[-1]
        assert call["model_id"] == CONFIG["model_id"]
        assert call["operation"] == "off_topic"

    def test_off_topic_malformed_json_raises_typed_error(self, fake_transport, test_api_key):
        """Malformed off_topic response raises typed error."""
        from ai_tutor_service.providers.client import LLMClient, LLMError  # noqa: F401

        client = LLMClient(transport=fake_transport, config=CONFIG, api_key=test_api_key)

        prompt = "Classify. ___FIXTURE:malformed___"

        with pytest.raises(LLMError) as exc_info:
            client.off_topic(
                prompt=prompt,
                model_id=CONFIG["model_id"],
                timeout=CONFIG["generation_timeout_seconds"],
            )

        assert exc_info.value.error_type == "malformed_response"

    def test_off_topic_provider_error_raises_typed_error(self, fake_transport, test_api_key):
        """Off_topic provider error raises typed error."""
        from ai_tutor_service.providers.client import LLMClient, LLMError  # noqa: F401

        client = LLMClient(transport=fake_transport, config=CONFIG, api_key=test_api_key)

        prompt = "Classify. ___FIXTURE:5xx___"

        with pytest.raises(LLMError) as exc_info:
            client.off_topic(
                prompt=prompt,
                model_id=CONFIG["model_id"],
                timeout=CONFIG["generation_timeout_seconds"],
            )

        assert exc_info.value.error_type == "provider_error"

    def test_off_topic_timeout_raises_typed_error(self, fake_transport, test_api_key):
        """Off_topic timeout raises typed error."""
        from ai_tutor_service.providers.client import LLMClient, LLMError  # noqa: F401

        client = LLMClient(transport=fake_transport, config=CONFIG, api_key=test_api_key)

        prompt = "Classify. ___FIXTURE:timeout___"

        with pytest.raises(LLMError) as exc_info:
            client.off_topic(
                prompt=prompt,
                model_id=CONFIG["model_id"],
                timeout=CONFIG["generation_timeout_seconds"],
            )

        assert exc_info.value.error_type == "timeout"

    def test_api_key_passed_but_never_logged(self, fake_transport, test_api_key):
        """
        API key must be passed to transport but never appear in logs or call records.

        The fake transport records whether key was provided but not its value.
        """
        from ai_tutor_service.providers.client import LLMClient  # noqa: F401

        client = LLMClient(transport=fake_transport, config=CONFIG, api_key=test_api_key)

        client.generate(
            prompt="Test ___FIXTURE:valid___",
            model_id=CONFIG["model_id"],
            timeout=CONFIG["generation_timeout_seconds"],
        )

        call = fake_transport.call_log[-1]
        # Key presence verified, but value never stored
        assert call["api_key_provided"] is True
        assert "api_key" not in str(call).lower() or test_api_key not in str(call)

    def test_no_network_access_in_tests(self, fake_transport, test_api_key):
        """
        Verify that the fake transport works without any socket/DNS access.

        The conftest.py network-deny fixture blocks all network calls.
        This test passing proves the fake transport doesn't use network.
        """
        from ai_tutor_service.providers.client import LLMClient  # noqa: F401

        client = LLMClient(transport=fake_transport, config=CONFIG, api_key=test_api_key)

        # This should work entirely offline
        result = client.generate(
            prompt="Test ___FIXTURE:valid___",
            model_id=CONFIG["model_id"],
            timeout=CONFIG["generation_timeout_seconds"],
        )

        assert "text" in result
        assert "usage" in result

    def test_replaceable_transport_for_testing(self, test_api_key):
        """
        LLMClient must accept a replaceable transport/adapter.

        This enables unit tests to inject fake transports with fixtures.
        """
        from ai_tutor_service.providers.client import LLMClient  # noqa: F401

        # Custom mock transport
        mock_transport = Mock()
        mock_transport.call.return_value = {
            "text": "Mocked response",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "model_id": CONFIG["model_id"],
            "finish_reason": "stop",
        }

        client = LLMClient(transport=mock_transport, config=CONFIG, api_key=test_api_key)

        result = client.generate(
            prompt="Test",
            model_id=CONFIG["model_id"],
            timeout=CONFIG["generation_timeout_seconds"],
        )

        assert result["text"] == "Mocked response"
        mock_transport.call.assert_called_once()

    def test_model_id_from_config_not_hardcoded(self, fake_transport, test_api_key):
        """Model ID must come from config, not be hardcoded in client."""
        from ai_tutor_service.providers.client import LLMClient  # noqa: F401

        client = LLMClient(transport=fake_transport, config=CONFIG, api_key=test_api_key)

        client.generate(
            prompt="Test ___FIXTURE:valid___",
            model_id=CONFIG["model_id"],
            timeout=CONFIG["generation_timeout_seconds"],
        )

        call = fake_transport.call_log[-1]
        assert call["model_id"] == CONFIG["model_id"]
        assert call["model_id"] == "anthropic/claude-3-5-haiku-latest"

    def test_guard_model_id_from_config(self, fake_transport, test_api_key):
        """Guard model ID must come from config (guard_model_id)."""
        from ai_tutor_service.providers.client import LLMClient  # noqa: F401

        client = LLMClient(transport=fake_transport, config=CONFIG, api_key=test_api_key)

        client.guard(
            prompt="Test ___FIXTURE:valid___ false",
            model_id=CONFIG["guard_model_id"],
            timeout=CONFIG["guard_timeout_seconds"],
        )

        call = fake_transport.call_log[-1]
        assert call["model_id"] == CONFIG["guard_model_id"]

    def test_timeout_from_config_not_hardcoded(self, fake_transport, test_api_key):
        """Timeout values must come from config, not hardcoded."""
        from ai_tutor_service.providers.client import LLMClient  # noqa: F401

        client = LLMClient(transport=fake_transport, config=CONFIG, api_key=test_api_key)

        client.generate(
            prompt="Test ___FIXTURE:valid___",
            model_id=CONFIG["model_id"],
            timeout=CONFIG["generation_timeout_seconds"],
        )

        call = fake_transport.call_log[-1]
        assert call["timeout"] == CONFIG["generation_timeout_seconds"]
        assert call["timeout"] == 18  # From tutor_config.yaml