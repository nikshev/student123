# impl: FR-002-10
"""
LLMClient - single external LLM boundary for AI Tutor Service.

Replaceable transport, strict structured output, typed errors for
malformed/invalid/provider/timeout responses, token usage in all operations,
API key only from parameter (never logged).
"""

import json
import urllib.request
import urllib.error
import socket
from typing import Callable, Any, Dict, Optional


class LLMError(Exception):
    """
    Typed error for LLM operations.

    Args:
        message: Human-readable error description.
        error_type: One of "malformed_response", "provider_error", "timeout".
        status_code: HTTP status code for provider errors (optional).
    """

    def __init__(self, message: str, error_type: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.error_type = error_type
        self.status_code = status_code

    def __str__(self):
        return f"LLMError({self.error_type}): {self.message}"

    def __repr__(self):
        return f"LLMError(message={self.message!r}, error_type={self.error_type!r}, status_code={self.status_code!r})"


class _DefaultTransport:
    """
    Default transport implementation using urllib (stdlib only).

    Makes HTTP POST to Anthropic-compatible API endpoint with JSON body.
    This is used when no custom transport is provided to LLMClient.
    """

    def call(self, operation: str, model_id: str, prompt: str, timeout: float, api_key: str) -> Dict[str, Any]:
        """
        Make an LLM API call.

        Args:
            operation: One of "generate", "guard", "off_topic".
            model_id: Model identifier.
            prompt: Full prompt text.
            timeout: Request timeout in seconds.
            api_key: API key for authentication.

        Returns:
            Parsed JSON response from provider.

        Raises:
            LLMError: On timeout, provider error, or malformed response.
        """
        # In production, this would read from Django settings or env.
        # For now, use a placeholder - real deployment configures the base URL.
        base_url = "https://api.anthropic.com/v1/messages"

        # Build request body for Anthropic-compatible API
        # The prompt already contains the full user+system message
        body = {
            "model": model_id,
            "max_tokens": 4096,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.0,
        }

        data = json.dumps(body).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }

        req = urllib.request.Request(base_url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                response_data = response.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            # Provider returned error status (4xx, 5xx)
            error_body = e.read().decode("utf-8")
            try:
                error_json = json.loads(error_body)
                msg = error_json.get("error", {}).get("message", str(e))
            except json.JSONDecodeError:
                msg = error_body or str(e)
            raise LLMError(
                message=f"Provider error: {msg}",
                error_type="provider_error",
                status_code=e.code,
            )
        except urllib.error.URLError as e:
            # Network-level error (DNS, connection refused, etc.)
            if isinstance(e.reason, socket.timeout) or "timeout" in str(e.reason).lower():
                raise LLMError(
                    message=f"Request timed out after {timeout} seconds",
                    error_type="timeout",
                )
            raise LLMError(
                message=f"Network error: {e.reason}",
                error_type="provider_error",
                status_code=None,
            )
        except socket.timeout:
            raise LLMError(
                message=f"Request timed out after {timeout} seconds",
                error_type="timeout",
            )

        # Parse response - strict structured output
        try:
            parsed = json.loads(response_data)
        except json.JSONDecodeError as e:
            raise LLMError(
                message=f"Provider returned malformed JSON: {e}",
                error_type="malformed_response",
            )

        # Validate required fields based on operation
        _validate_response_structure(parsed, operation)

        return parsed


def _handle_provider_error(response: Dict[str, Any], operation: str) -> None:
    """
    Check if response is a provider error and raise appropriate LLMError.

    Provider error responses have 'error' and 'status_code' fields.
    """
    if "error" in response and "status_code" in response:
        error_info = response.get("error", {})
        msg = error_info.get("message", "Provider error") if isinstance(error_info, dict) else str(error_info)
        status_code = response.get("status_code")
        error_type = "timeout" if status_code == 408 else "provider_error"
        raise LLMError(
            message=f"Provider error: {msg}",
            error_type=error_type,
            status_code=status_code,
        )


def _validate_response_structure(response: Dict[str, Any], operation: str) -> None:
    """
    Validate that the provider response has the required structure.

    Args:
        response: Parsed JSON response from provider.
        operation: One of "generate", "guard", "off_topic".

    Raises:
        LLMError: If required fields are missing or malformed.
    """
    if operation == "generate":
        required = ["text", "usage", "model_id", "finish_reason"]
        for field in required:
            if field not in response:
                raise LLMError(
                    message=f"Missing required field in generate response: {field}",
                    error_type="malformed_response",
                )
        if not isinstance(response.get("usage"), dict):
            raise LLMError(
                message="Field 'usage' must be an object",
                error_type="malformed_response",
            )
        usage = response["usage"]
        if "input_tokens" not in usage or "output_tokens" not in usage:
            raise LLMError(
                message="Missing input_tokens or output_tokens in usage",
                error_type="malformed_response",
            )
        if not isinstance(usage["input_tokens"], int) or not isinstance(usage["output_tokens"], int):
            raise LLMError(
                message="input_tokens and output_tokens must be integers",
                error_type="malformed_response",
            )

    elif operation == "guard":
        required = ["contains_solution", "reason", "usage", "model_id", "finish_reason"]
        for field in required:
            if field not in response:
                raise LLMError(
                    message=f"Missing required field in guard response: {field}",
                    error_type="malformed_response",
                )
        if not isinstance(response.get("contains_solution"), bool):
            raise LLMError(
                message="Field 'contains_solution' must be a boolean",
                error_type="malformed_response",
            )
        if not isinstance(response.get("reason"), str):
            raise LLMError(
                message="Field 'reason' must be a string",
                error_type="malformed_response",
            )
        if not isinstance(response.get("usage"), dict):
            raise LLMError(
                message="Field 'usage' must be an object",
                error_type="malformed_response",
            )
        usage = response["usage"]
        if "input_tokens" not in usage or "output_tokens" not in usage:
            raise LLMError(
                message="Missing input_tokens or output_tokens in usage",
                error_type="malformed_response",
            )

    elif operation == "off_topic":
        required = ["classification", "confidence", "top_source_label", "usage", "model_id", "finish_reason"]
        for field in required:
            if field not in response:
                raise LLMError(
                    message=f"Missing required field in off_topic response: {field}",
                    error_type="malformed_response",
                )
        if response.get("classification") not in ("on_topic", "off_topic"):
            raise LLMError(
                message="Field 'classification' must be 'on_topic' or 'off_topic'",
                error_type="malformed_response",
            )
        confidence = response.get("confidence")
        if not isinstance(confidence, (int, float)) or not (0.0 <= confidence <= 1.0):
            raise LLMError(
                message="Field 'confidence' must be a float in [0.0, 1.0]",
                error_type="malformed_response",
            )
        if not isinstance(response.get("top_source_label"), str):
            raise LLMError(
                message="Field 'top_source_label' must be a string",
                error_type="malformed_response",
            )
        if not isinstance(response.get("usage"), dict):
            raise LLMError(
                message="Field 'usage' must be an object",
                error_type="malformed_response",
            )
        usage = response["usage"]
        if "input_tokens" not in usage or "output_tokens" not in usage:
            raise LLMError(
                message="Missing input_tokens or output_tokens in usage",
                error_type="malformed_response",
            )
    else:
        raise LLMError(
            message=f"Unknown operation: {operation}",
            error_type="malformed_response",
        )


class LLMClient:
    """
    Single external LLM boundary for all LLM operations.

    All LLM calls go through this client. It uses a replaceable transport
    (callable) to enable testing with fake transports and production with
    the default HTTP transport.

    Args:
        transport: Callable(operation, model_id, prompt, timeout, api_key) -> dict.
                   If None, uses default urllib-based transport.
        config: Configuration dict from load_tutor_config().
        api_key: API key string (from Django settings in production, never logged).
    """

    def __init__(
        self,
        transport: Optional[Any] = None,
        config: Optional[Dict[str, Any]] = None,
        api_key: str = "",
    ):
        # Transport must have a .call() method with signature:
        # call(operation, model_id, prompt, timeout, api_key) -> dict
        self._transport = transport or _DefaultTransport()
        self._config = config or {}
        self._api_key = api_key

    def generate(
        self,
        prompt: str,
        model_id: str,
        timeout: float,
    ) -> Dict[str, Any]:
        """
        Generate a tutoring response.

        Args:
            prompt: Full prompt including system/user messages.
            model_id: Model identifier (from config).
            timeout: Request timeout in seconds (from config).

        Returns:
            Dict with keys: text, usage (input_tokens, output_tokens), model_id, finish_reason.

        Raises:
            LLMError: On malformed response, provider error, or timeout.
        """
        response = self._transport.call("generate", model_id, prompt, timeout, self._api_key)
        _handle_provider_error(response, "generate")
        _validate_response_structure(response, "generate")
        return {
            "text": response["text"],
            "usage": {
                "input_tokens": response["usage"]["input_tokens"],
                "output_tokens": response["usage"]["output_tokens"],
            },
            "model_id": response["model_id"],
            "finish_reason": response["finish_reason"],
        }

    def guard(
        self,
        prompt: str,
        model_id: str,
        timeout: float,
    ) -> Dict[str, Any]:
        """
        Run solution guard check on a candidate response.

        Args:
            prompt: Full prompt including the candidate response to analyze.
            model_id: Guard model identifier (from config).
            timeout: Request timeout in seconds (from config).

        Returns:
            Dict with keys: contains_solution (bool), reason (str), usage, model_id, finish_reason.

        Raises:
            LLMError: On malformed response, provider error, or timeout.
        """
        response = self._transport.call("guard", model_id, prompt, timeout, self._api_key)
        _handle_provider_error(response, "guard")
        _validate_response_structure(response, "guard")
        return {
            "contains_solution": response["contains_solution"],
            "reason": response["reason"],
            "usage": {
                "input_tokens": response["usage"]["input_tokens"],
                "output_tokens": response["usage"]["output_tokens"],
            },
            "model_id": response["model_id"],
            "finish_reason": response["finish_reason"],
        }

    def off_topic(
        self,
        prompt: str,
        model_id: str,
        timeout: float,
    ) -> Dict[str, Any]:
        """
        Classify whether a question is off-topic for the current unit.

        Args:
            prompt: Full prompt including question and unit context.
            model_id: Model identifier (from config).
            timeout: Request timeout in seconds (from config).

        Returns:
            Dict with keys: classification (on_topic/off_topic), confidence (float),
                           top_source_label (str), usage, model_id, finish_reason.

        Raises:
            LLMError: On malformed response, provider error, or timeout.
        """
        response = self._transport.call("off_topic", model_id, prompt, timeout, self._api_key)
        _handle_provider_error(response, "off_topic")
        _validate_response_structure(response, "off_topic")
        return {
            "classification": response["classification"],
            "confidence": response["confidence"],
            "top_source_label": response["top_source_label"],
            "usage": {
                "input_tokens": response["usage"]["input_tokens"],
                "output_tokens": response["usage"]["output_tokens"],
            },
            "model_id": response["model_id"],
            "finish_reason": response["finish_reason"],
        }