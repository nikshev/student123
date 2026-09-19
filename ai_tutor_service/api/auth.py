# impl: FR-002-02
"""
Bearer token authentication and actor context middleware for AI Tutor Service API.

This module handles:
1. Bearer token validation against AI_TUTOR_SHARED_SECRET from Django settings
2. Student context extraction from trusted headers (only after successful auth)
3. Staff role verification from X-AI-Tutor-Role header (only from header, never body)
3. Constant-time token comparison to prevent timing attacks
"""

import hmac
import logging
from dataclasses import dataclass
from functools import wraps
from typing import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils.deprecation import MiddlewareMixin

from ai_tutor_service.api.errors import (
    authentication_required,
    forbidden,
    invalid_token,
)

logger = logging.getLogger(__name__)

# Header names
HEADER_AUTHORIZATION = "HTTP_AUTHORIZATION"
HEADER_USER_ID = "HTTP_X_AI_TUTOR_USER_ID"
HEADER_COURSE_ID = "HTTP_X_AI_TUTOR_COURSE_ID"
HEADER_UNIT_USAGE_KEY = "HTTP_X_AI_TUTOR_UNIT_USAGE_KEY"
HEADER_ROLE = "HTTP_X_AI_TUTOR_ROLE"

# Trusted header prefix for student context
TRUSTED_HEADERS = {
    "user_id": HEADER_USER_ID,
    "course_id": HEADER_COURSE_ID,
    "unit_usage_key": HEADER_UNIT_USAGE_KEY,
}


@dataclass(slots=True)
class ActorContext:
    """
    Authenticated actor context from trusted headers.

    Only populated after successful Bearer authentication.
    Student context comes ONLY from headers, never from request body.
    Staff role comes ONLY from X-AI-Tutor-Role header, never from body.
    """

    user_id: str | None = None
    course_id: str | None = None
    unit_usage_key: str | None = None
    is_staff: bool = False

    @property
    def has_student_context(self) -> bool:
        """True if all student context headers are present."""
        return all([self.user_id, self.course_id, self.unit_usage_key])

    def to_dict(self) -> dict:
        """Convert to dictionary for logging/debugging (no secrets)."""
        return {
            "user_id": self.user_id,
            "course_id": self.course_id,
            "unit_usage_key": self.unit_usage_key,
            "is_staff": self.is_staff,
        }


def get_shared_secret() -> str:
    """
    Get the shared secret from Django settings.

    In tests, this comes from ai_tutor_test_settings.AI_TUTOR_SHARED_SECRET.
    In production, from Tutor secrets -> Django settings.
    """
    return getattr(settings, "AI_TUTOR_SHARED_SECRET", "")


def verify_bearer_token(auth_header: str | None) -> bool:
    """
    Verify Bearer token using constant-time comparison.

    Args:
        auth_header: Value of Authorization header (e.g., "Bearer secret123")

    Returns:
        True if token matches shared secret, False otherwise
    """
    if not auth_header:
        return False

    # Parse "Bearer <token>"
    parts = auth_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False

    provided_token = parts[1]
    expected_token = get_shared_secret()

    # Constant-time comparison to prevent timing attacks
    return hmac.compare_digest(provided_token, expected_token)


def extract_actor_context(request: HttpRequest) -> ActorContext:
    """
    Extract actor context from trusted headers.

    ONLY called after successful Bearer authentication.
    Headers are trusted because they come from XBlock (server-side), not browser.
    """
    context = ActorContext()

    # Student context from trusted headers
    context.user_id = request.META.get(HEADER_USER_ID)
    context.course_id = request.META.get(HEADER_COURSE_ID)
    context.unit_usage_key = request.META.get(HEADER_UNIT_USAGE_KEY)

    # Staff role ONLY from header (never from request body)
    role_header = request.META.get(HEADER_ROLE, "").strip().lower()
    context.is_staff = role_header == "staff"

    return context


class BearerAuthMiddleware(MiddlewareMixin):
    """
    Django middleware for Bearer token authentication.

    Adds `request.ai_tutor_actor` (ActorContext) after successful auth.
    Returns 401 response immediately if authentication fails.
    """

    # Endpoints that don't require authentication (none in MVP)
    EXEMPT_PATHS = frozenset()

    def process_request(self, request: HttpRequest) -> HttpResponse | None:
        # Skip exempt paths
        if request.path in self.EXEMPT_PATHS:
            return None

        # Skip non-API paths (let other middleware handle them)
        if not request.path.startswith("/api/v1/"):
            return None

        # Verify Bearer token
        auth_header = request.META.get(HEADER_AUTHORIZATION)

        if not verify_bearer_token(auth_header):
            # Log auth failure without secret values
            logger.warning(
                "API auth failed: missing or invalid Bearer token",
                extra={
                    "path": request.path,
                    "method": request.method,
                    "has_auth_header": bool(auth_header),
                },
            )
            # Return 401 with safe envelope - code depends on whether header was present
            if not auth_header or not auth_header.lower().startswith("bearer "):
                return authentication_required()
            return invalid_token()

        # Authentication successful - extract actor context from trusted headers
        request.ai_tutor_actor = extract_actor_context(request)

        logger.debug(
            "API auth successful",
            extra={
                "path": request.path,
                "actor": request.ai_tutor_actor.to_dict(),
            },
        )

        return None


def require_staff_role(view_func: Callable) -> Callable:
    """
    Decorator to require staff role for an endpoint.

    Works with both function-based views and class-based view methods.
    Must be used AFTER BearerAuthMiddleware (which sets request.ai_tutor_actor).
    Returns 403 if actor is not staff.
    """

    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        # Handle both function-based views (request, *args, **kwargs)
        # and class-based view methods (self, request, *args, **kwargs)
        if len(args) >= 2 and hasattr(args[0], 'dispatch') and hasattr(args[1], 'META'):
            # Class-based view method: (self, request, ...)
            request = args[1]
        elif len(args) >= 1 and hasattr(args[0], 'META'):
            # Function-based view: (request, ...)
            request = args[0]
        else:
            # Fallback: assume first arg is request
            request = args[0]

        actor = getattr(request, "ai_tutor_actor", None)
        if not actor or not actor.is_staff:
            return forbidden()
        return view_func(*args, **kwargs)

    return wrapped_view


def require_student_context(view_func: Callable) -> Callable:
    """
    Decorator to require student context (user_id, course_id, unit_usage_key).

    Works with both function-based views and class-based view methods.
    Must be used AFTER BearerAuthMiddleware.
    Returns 400 if student context is incomplete.
    """

    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        # Handle both function-based views (request, *args, **kwargs)
        # and class-based view methods (self, request, *args, **kwargs)
        if len(args) >= 2 and hasattr(args[0], 'dispatch') and hasattr(args[1], 'META'):
            # Class-based view method: (self, request, ...)
            request = args[1]
        elif len(args) >= 1 and hasattr(args[0], 'META'):
            # Function-based view: (request, ...)
            request = args[0]
        else:
            # Fallback: assume first arg is request
            request = args[0]

        actor = getattr(request, "ai_tutor_actor", None)
        if not actor or not actor.has_student_context:
            from ai_tutor_service.api.errors import validation_error

            return validation_error(
                message="Student context headers required: X-AI-Tutor-User-ID, "
                "X-AI-Tutor-Course-ID, X-AI-Tutor-Unit-Usage-Key"
            )
        return view_func(*args, **kwargs)

    return wrapped_view