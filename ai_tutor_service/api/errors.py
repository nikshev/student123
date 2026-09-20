# impl: FR-002-02
"""
Shared JSON error envelope for AI Tutor Service API.

All API endpoints return errors in a consistent format:
{
    "error": {
        "code": "error_code",
        "message": "User-friendly Ukrainian message"
    },
    "request_id": "uuid"
}

Secret values are NEVER included in error responses or logs.
"""

import uuid
from typing import Any

from django.http import JsonResponse


# Standard error codes per contract
ERROR_CODES = {
    # 400
    "invalid_request": "Невірний формат запиту. Перевірте дані і спробуйте знову.",
    "validation_error": "Дані не пройшли валідацію. Перевірте обов'язкові поля.",
    "idempotency_conflict": "Конфлікт ідемпотентності: той самий ключ з іншими даними.",
    # 401
    "authentication_required": "Потрібна автентифікація. Надайте Bearer токен.",
    "invalid_token": "Невірний токен автентифікації.",
    # 403
    "forbidden": "Доступ заборонено. Недостатньо прав для цієї операції.",
    "actor_mismatch": "Контекст актора не відповідає запиту.",
    # 404
    "not_found": "Ресурс не знайдено.",
    "conversation_not_found": "Розмова не знайдена або недоступна.",
    # 429
    "quota_exceeded": "Щоденний ліміт запитів вичерпано. Спробуйте завтра.",
    # 502
    "upstream_error": "Помилка зовнішнього сервісу. Спробуйте пізніше.",
    "invalid_upstream_response": "Невірна відповідь від моделі. Спробуйте ще раз.",
    # 503
    "service_unavailable": "Репетитор тимчасово недоступний. Спробуйте ще раз.",
    "config_unavailable": "Конфігурація сервісу недоступна.",
    "database_unavailable": "База даних тимчасово недоступна.",
    # 504
    "timeout": "Час очікування вичерпано. Спробуйте ще раз.",
}

# HTTP status mapping for error codes
ERROR_STATUS_MAP = {
    "invalid_request": 400,
    "validation_error": 400,
    "idempotency_conflict": 409,
    "authentication_required": 401,
    "invalid_token": 401,
    "forbidden": 403,
    "actor_mismatch": 403,
    "not_found": 404,
    "conversation_not_found": 404,
    "quota_exceeded": 429,
    "upstream_error": 502,
    "invalid_upstream_response": 502,
    "service_unavailable": 503,
    "config_unavailable": 503,
    "database_unavailable": 503,
    "timeout": 504,
}


def make_error_response(
    code: str,
    request_id: str | None = None,
    status: int | None = None,
    message: str | None = None,
) -> JsonResponse:
    """
    Create a standardized error response.

    Args:
        code: Error code from ERROR_CODES
        request_id: UUID for tracing (generated if not provided)
        status: HTTP status code (derived from code if not provided)
        message: Override message (use standard message if not provided)

    Returns:
        JsonResponse with error envelope and appropriate status code
    """
    if request_id is None:
        request_id = str(uuid.uuid4())

    if status is None:
        status = ERROR_STATUS_MAP.get(code, 500)

    if message is None:
        message = ERROR_CODES.get(code, "Сталася помилка. Спробуйте пізніше.")

    # Ensure secret values are never leaked
    assert "secret" not in message.lower(), "Error message must not contain secret"

    return JsonResponse(
        {
            "error": {
                "code": code,
                "message": message,
            },
            "request_id": request_id,
        },
        status=status,
    )


def make_error_response_with_details(
    code: str,
    request_id: str | None = None,
    status: int | None = None,
    message: str | None = None,
    **extra_details: Any,
) -> JsonResponse:
    """
    Create a standardized error response with additional details.

    For cases like 429 quota_exceeded where we need to include daily_remaining.
    The extra details are added at the top level (not inside error object).
    """
    if request_id is None:
        request_id = str(uuid.uuid4())

    if status is None:
        status = ERROR_STATUS_MAP.get(code, 500)

    if message is None:
        message = ERROR_CODES.get(code, "Сталася помилка. Спробуйте пізніше.")

    # Ensure secret values are never leaked
    assert "secret" not in message.lower(), "Error message must not contain secret"

    response_data = {
        "error": {
            "code": code,
            "message": message,
        },
        "request_id": request_id,
    }
    response_data.update(extra_details)

    return JsonResponse(response_data, status=status)


# Pre-built error responses for common cases (without request_id, generated per-request)
def authentication_required(request_id: str | None = None) -> JsonResponse:
    """401 - Missing or invalid Authorization header."""
    return make_error_response("authentication_required", request_id)


def invalid_token(request_id: str | None = None) -> JsonResponse:
    """401 - Invalid Bearer token."""
    return make_error_response("invalid_token", request_id)


def forbidden(request_id: str | None = None) -> JsonResponse:
    """403 - Authenticated but not authorized (e.g., missing staff role)."""
    return make_error_response("forbidden", request_id)


def actor_mismatch(request_id: str | None = None) -> JsonResponse:
    """403 - Actor context doesn't match resource ownership."""
    return make_error_response("actor_mismatch", request_id)


def not_found(request_id: str | None = None) -> JsonResponse:
    """404 - Resource not found."""
    return make_error_response("not_found", request_id)


def conversation_not_found(request_id: str | None = None) -> JsonResponse:
    """404 - Conversation not found or expired (same envelope as not_found)."""
    return make_error_response("conversation_not_found", request_id)


def quota_exceeded(request_id: str | None = None, daily_remaining: int = 0) -> JsonResponse:
    """429 - Daily quota exceeded; daily_remaining lives in error.details (§2)."""
    if request_id is None:
        request_id = str(uuid.uuid4())

    return JsonResponse(
        {
            "error": {
                "code": "quota_exceeded",
                "message": ERROR_CODES["quota_exceeded"],
                "details": {"daily_remaining": daily_remaining},
            },
            "request_id": request_id,
        },
        status=429,
    )


def service_unavailable(request_id: str | None = None) -> JsonResponse:
    """503 - Service temporarily unavailable."""
    return make_error_response("service_unavailable", request_id)


def upstream_error(request_id: str | None = None) -> JsonResponse:
    """502 - Upstream LLM/provider error."""
    return make_error_response("upstream_error", request_id)


def invalid_upstream_response(request_id: str | None = None) -> JsonResponse:
    """502 - Invalid response from LLM provider."""
    return make_error_response("invalid_upstream_response", request_id)


def timeout_error(request_id: str | None = None) -> JsonResponse:
    """504 - Request timeout (30s budget exceeded)."""
    return make_error_response("timeout", request_id)


def validation_error(request_id: str | None = None, message: str | None = None) -> JsonResponse:
    """400 - Request validation failed."""
    return make_error_response("validation_error", request_id, message=message)


def invalid_request(request_id: str | None = None, message: str | None = None) -> JsonResponse:
    """400 - Malformed request."""
    return make_error_response("invalid_request", request_id, message=message)


def idempotency_conflict(request_id: str | None = None) -> JsonResponse:
    """409 - Same idempotency key with different payload."""
    return make_error_response("idempotency_conflict", request_id)