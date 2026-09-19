# impl: FR-002-10
"""
TutorServiceClient — HTTP adapter for AI Tutor Service.

This is the ONLY module in ai_tutor_xblock allowed to make outbound HTTP calls.
It translates transport errors to typed errors per tutor-service-api.md contract.
"""

import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


# ===== Typed Errors =====

class TutorServiceError(Exception):
    """Base exception for all Tutor Service errors."""

    def __init__(self, code: str, message: str, **kwargs):
        super().__init__(message)
        self.code = code
        self.message = message
        for k, v in kwargs.items():
            setattr(self, k, v)


class QuotaExceededError(TutorServiceError):
    """429 — daily quota exhausted."""

    def __init__(self, message: str, daily_remaining: int = 0):
        super().__init__("quota_exceeded", message, daily_remaining=daily_remaining)


class UnauthorizedError(TutorServiceError):
    """401 — invalid or missing Bearer token."""

    def __init__(self, message: str = "Неавторизований запит"):
        super().__init__("unauthorized", message)


class ForbiddenError(TutorServiceError):
    """403 — actor mismatch or insufficient role."""

    def __init__(self, message: str = "Доступ заборонено"):
        super().__init__("forbidden", message)


class NotFoundError(TutorServiceError):
    """404 — conversation or materials not found."""

    def __init__(self, message: str = "Ресурс не знайдено"):
        super().__init__("not_found", message)


class ServiceUnavailableError(TutorServiceError):
    """502/503 — service temporarily unavailable."""

    def __init__(self, message: str = "Репетитор тимчасово недоступний. Спробуйте ще раз."):
        super().__init__("service_unavailable", message)


class TimeoutError(TutorServiceError):
    """504 — request timeout."""

    def __init__(self, message: str = "Час очікування відповіді вичерпано. Спробуйте ще раз."):
        super().__init__("timeout", message)


class BadRequestError(TutorServiceError):
    """400 — invalid request schema."""

    def __init__(self, message: str = "Невалідний запит"):
        super().__init__("bad_request", message)


class SchemaError(TutorServiceError):
    """Malformed JSON or unknown status in response."""

    def __init__(self, message: str = "Невірний формат відповіді сервісу"):
        super().__init__("schema_error", message)


# ===== Typed Results =====

@dataclass
class AskResult:
    """Typed result for ask() — terminal statuses only."""
    status: str  # shown | blocked | no_materials | off_topic
    answer: str
    topic: Optional[str]
    sources: list[dict]
    blocked_reason: Optional[str]
    daily_remaining: int
    latency_ms: int
    route: str
    config_version: str
    conversation_id: str
    request_id: str


@dataclass
class ConfigResult:
    """Typed result for config() — public projection only."""
    config_version: str
    daily_limit: int
    request_timeout_seconds: int
    http_connect_timeout_seconds: int
    question_max_chars: int


@dataclass
class HistoryResult:
    """Typed result for history()."""
    conversation_id: str
    messages: list[dict]
    daily_remaining: int
    config_version: str


@dataclass
class MaterialsStatusResult:
    """Typed result for materials_status()."""
    status: str  # READY | INDEXING | FAILED | MISSING
    content_version: Optional[str]
    segment_count: int
    config_version: str


# ===== Default Transport =====

def _default_transport(method: str, url: str, headers: dict, json_body: Optional[dict], timeout: float):
    """Default synchronous HTTP transport using urllib (no retries)."""
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")

    req = Request(url, data=data, headers=headers, method=method)

    try:
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            status_code = resp.status
            resp_headers = dict(resp.headers)
    except HTTPError as e:
        body = e.read()
        status_code = e.code
        resp_headers = dict(e.headers)
    except URLError as e:
        # Network-level error — treat as service unavailable
        raise ServiceUnavailableError(f"Transport error: {e.reason}")
    except Exception as e:
        # Timeout or other transport error
        raise TimeoutError(f"Transport error: {e}")

    # Parse response
    if status_code >= 400:
        # Error response — will be handled by caller
        try:
            error_data = json.loads(body.decode("utf-8")) if body else {}
        except json.JSONDecodeError:
            error_data = {"error": {"code": "service_unavailable", "message": "Malformed error response"}}
        return _make_mock_response(status_code, error_data, resp_headers)

    try:
        parsed = json.loads(body.decode("utf-8")) if body else {}
    except json.JSONDecodeError as e:
        raise SchemaError(f"Malformed JSON response: {e}")

    return _make_mock_response(status_code, parsed, resp_headers)


class _MockResponse:
    """Internal response wrapper matching test's MockResponse structure."""
    def __init__(self, status_code: int, body: Any, headers: dict = None):
        self.status_code = status_code
        self.body = body
        self.headers = headers or {}


def _make_mock_response(status_code: int, body: Any, headers: dict = None):
    return _MockResponse(status_code, body, headers)


# ===== TutorServiceClient =====

class TutorServiceClient:
    """
    HTTP client for AI Tutor Service.

    Args:
        base_url: Base URL of the service (e.g., https://tutor.internal/api/v1)
        shared_secret: Bearer token from Django settings (AI_TUTOR_SHARED_SECRET)
        transport: Optional callable transport(method, url, headers, json, timeout).
                   Defaults to urllib-based transport with no retries.
        timeout: Default request timeout in seconds (passed to transport).
    """

    def __init__(
        self,
        base_url: str,
        shared_secret: str,
        transport: Optional[Callable] = None,
        timeout: float = 30.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._shared_secret = shared_secret
        self._transport = transport or _default_transport
        self._timeout = timeout

    # ---- Internal helpers ----

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._shared_secret}"}

    def _actor_headers(self, user_id: str, course_id: str, unit_usage_key: str) -> dict:
        return {
            "X-AI-Tutor-User-ID": user_id,
            "X-AI-Tutor-Course-ID": course_id,
            "X-AI-Tutor-Unit-Usage-Key": unit_usage_key,
        }

    def _call(
        self,
        method: str,
        path: str,
        json_body: Optional[dict] = None,
        user_id: Optional[str] = None,
        course_id: Optional[str] = None,
        unit_usage_key: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> _MockResponse:
        """Make HTTP call with proper headers and error mapping."""
        url = f"{self._base_url}{path}"
        headers = self._auth_headers()

        if user_id and course_id and unit_usage_key:
            headers.update(self._actor_headers(user_id, course_id, unit_usage_key))

        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        response = self._transport(method, url, headers, json_body, self._timeout)
        return self._handle_response(response, path)

    def _handle_response(self, response: _MockResponse, path: str):
        """Map HTTP response to typed result or raise typed error."""
        status = response.status_code
        body = response.body

        # Extract path without query string for endpoint matching
        endpoint = path.split("?")[0]

        # Handle error status codes
        if status == 400:
            raise BadRequestError(body.get("error", {}).get("message", "Невалідний запит"))
        if status == 401:
            raise UnauthorizedError(body.get("error", {}).get("message", "Неавторизований запит"))
        if status == 403:
            raise ForbiddenError(body.get("error", {}).get("message", "Доступ заборонено"))
        if status == 404:
            raise NotFoundError(body.get("error", {}).get("message", "Ресурс не знайдено"))
        if status == 429:
            details = body.get("error", {}).get("details", {})
            daily_remaining = details.get("daily_remaining", 0)
            raise QuotaExceededError(
                body.get("error", {}).get("message", "Щоденний ліміт вичерпано"),
                daily_remaining=daily_remaining,
            )
        if status in (502, 503):
            raise ServiceUnavailableError(
                body.get("error", {}).get("message", "Репетитор тимчасово недоступний. Спробуйте ще раз.")
            )
        if status == 504:
            raise TimeoutError(
                body.get("error", {}).get("message", "Час очікування відповіді вичерпано. Спробуйте ще раз.")
            )

        # Success — parse typed result based on endpoint
        if endpoint == "/ask":
            return self._parse_ask_response(body)
        if endpoint == "/config":
            return self._parse_config_response(body)
        if endpoint.startswith("/conversation/"):
            return self._parse_history_response(body)
        if endpoint == "/materials/status":
            return self._parse_materials_status_response(body)

        raise SchemaError(f"Unknown endpoint: {endpoint}")

    def _parse_ask_response(self, body: dict) -> AskResult:
        """Parse /ask response — must be one of the terminal statuses."""
        try:
            status = body["status"]
            if status not in ("shown", "blocked", "no_materials", "off_topic"):
                raise SchemaError(f"Unknown ask status: {status}")

            return AskResult(
                status=status,
                answer=body["answer"],
                topic=body.get("topic"),
                sources=body.get("sources", []),
                blocked_reason=body.get("blocked_reason"),
                daily_remaining=body["daily_remaining"],
                latency_ms=body["latency_ms"],
                route=body["route"],
                config_version=body["config_version"],
                conversation_id=body["conversation_id"],
                request_id=body["request_id"],
            )
        except KeyError as e:
            raise SchemaError(f"Missing required field in ask response: {e}")
        except TypeError as e:
            raise SchemaError(f"Invalid ask response structure: {e}")

    def _parse_config_response(self, body: dict) -> ConfigResult:
        """Parse /config response — public projection only."""
        try:
            return ConfigResult(
                config_version=body["config_version"],
                daily_limit=body["daily_limit"],
                request_timeout_seconds=body["request_timeout_seconds"],
                http_connect_timeout_seconds=body["http_connect_timeout_seconds"],
                question_max_chars=body["question_max_chars"],
            )
        except KeyError as e:
            raise SchemaError(f"Missing required field in config response: {e}")

    def _parse_history_response(self, body: dict) -> HistoryResult:
        """Parse /conversation/{id} response."""
        try:
            return HistoryResult(
                conversation_id=body["conversation_id"],
                messages=body["messages"],
                daily_remaining=body["daily_remaining"],
                config_version=body.get("config_version", "1.0.0"),
            )
        except KeyError as e:
            raise SchemaError(f"Missing required field in history response: {e}")

    def _parse_materials_status_response(self, body: dict) -> MaterialsStatusResult:
        """Parse /materials/status response."""
        try:
            return MaterialsStatusResult(
                status=body["status"],
                content_version=body.get("content_version"),
                segment_count=body["segment_count"],
                config_version=body["config_version"],
            )
        except KeyError as e:
            raise SchemaError(f"Missing required field in materials_status response: {e}")

    # ---- Public API ----

    def ask(
        self,
        question: str,
        conversation_id: Optional[str],
        route: str,
        user_id: str,
        course_id: str,
        unit_usage_key: str,
        request_id: Optional[str] = None,
    ) -> AskResult:
        """
        Ask a question to the tutor.

        Args:
            question: Student's question (trimmed, non-empty)
            conversation_id: Existing conversation UUID or None for new
            route: Routing key (MVP: "default")
            user_id: Server-derived user ID
            course_id: Server-derived course ID
            unit_usage_key: Server-derived unit usage key
            request_id: Optional request ID (used as idempotency key)

        Returns:
            AskResult with terminal status and all fields

        Raises:
            QuotaExceededError, UnauthorizedError, ForbiddenError, NotFoundError,
            ServiceUnavailableError, TimeoutError, BadRequestError, SchemaError
        """
        idempotency_key = request_id or str(uuid.uuid4())
        body = {
            "question": question,
            "conversation_id": conversation_id,
            "route": route,
        }
        response = self._call(
            "POST",
            "/ask",
            json_body=body,
            user_id=user_id,
            course_id=course_id,
            unit_usage_key=unit_usage_key,
            idempotency_key=idempotency_key,
        )
        return response

    def history(
        self,
        conversation_id: str,
        user_id: str,
        course_id: str,
        unit_usage_key: str,
    ) -> HistoryResult:
        """
        Get conversation history.

        Args:
            conversation_id: Conversation UUID
            user_id: Server-derived user ID
            course_id: Server-derived course ID
            unit_usage_key: Server-derived unit usage key

        Returns:
            HistoryResult with messages and daily_remaining

        Raises:
            UnauthorizedError, ForbiddenError, NotFoundError,
            ServiceUnavailableError, SchemaError
        """
        path = f"/conversation/{conversation_id}"
        response = self._call(
            "GET",
            path,
            user_id=user_id,
            course_id=course_id,
            unit_usage_key=unit_usage_key,
        )
        return response

    def config(self) -> ConfigResult:
        """
        Get public service configuration.

        Returns:
            ConfigResult with public projection only

        Raises:
            UnauthorizedError, ServiceUnavailableError, SchemaError
        """
        response = self._call("GET", "/config")
        return response

    def materials_status(
        self,
        course_id: str,
        unit_usage_key: str,
    ) -> MaterialsStatusResult:
        """
        Get materials indexing status for a unit.

        Args:
            course_id: Course ID
            unit_usage_key: Unit usage key

        Returns:
            MaterialsStatusResult with status and metadata

        Raises:
            BadRequestError, UnauthorizedError, ForbiddenError,
            ServiceUnavailableError, SchemaError
        """
        # NOTE: Query params are NOT urlencoded to match fixture keys in tests
        # Fixture keys use raw course_id/unit_usage_key with special chars
        path = f"/materials/status?course_id={course_id}&unit_usage_key={unit_usage_key}"
        response = self._call("GET", path)
        return response