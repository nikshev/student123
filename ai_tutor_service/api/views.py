# impl: FR-002-02
"""
Minimal view stubs for AI Tutor Service API endpoints.

These stubs implement the authentication/authorization layer and return
501 Not Implemented for endpoints not yet implemented (T-018+).
The auth behavior is tested by T-015 contract tests.

IMPORTANT: These are explicit stubs marked for replacement in T-018+.
Do NOT add business logic here - only auth checks and 501 responses.
"""

import json
import logging
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from ai_tutor_service.api.auth import (
    ActorContext,
    BearerAuthMiddleware,
    require_staff_role,
    require_student_context,
)
from ai_tutor_service.api.errors import (
    make_error_response,
    service_unavailable,
    validation_error,
)

logger = logging.getLogger(__name__)


class AuthenticatedView(View):
    """
    Base view that requires Bearer authentication.

    The BearerAuthMiddleware runs before this and sets request.ai_tutor_actor.
    """

    # Middleware handles auth, but we can also check here for defense in depth
    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        # Check that middleware ran and set actor
        actor = getattr(request, "ai_tutor_actor", None)
        if actor is None:
            logger.warning("Actor context missing - middleware may not be configured")
            return service_unavailable()

        return super().dispatch(request, *args, **kwargs)


@method_decorator(csrf_exempt, name="dispatch")
class AskView(AuthenticatedView):
    """
    POST /api/v1/ask - Student asks a question (T-018+ will implement pipeline).

    Requires: Bearer token + student context headers.
    Returns: 501 Not Implemented (stub for T-018).
    """

    @require_student_context
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        # Parse and validate request body (schema validation will be in T-018)
        try:
            body = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            return validation_error(message="Invalid JSON body")

        # Check required fields (basic validation for auth test)
        if "question" not in body or "route" not in body:
            return validation_error(message="Missing required fields: question, route")

        # Verify route is "default" (MVP only supports default)
        if body.get("route") != "default":
            return validation_error(message='Only route="default" is supported')

        # TODO(T-018): Implement tutoring pipeline
        # For now return 501 with safe envelope
        logger.info(
            "Ask endpoint called (stub)",
            extra={
                "actor": request.ai_tutor_actor.to_dict(),
                "has_conversation_id": "conversation_id" in body,
            },
        )

        return make_error_response(
            "service_unavailable",
            status=501,
            message="Endpoint not yet implemented",
        )


@method_decorator(csrf_exempt, name="dispatch")
class MaterialsView(AuthenticatedView):
    """
    POST /api/v1/materials - Staff ingests course materials (T-018+ will implement).

    Requires: Bearer token + X-AI-Tutor-Role: staff header.
    Returns: 501 Not Implemented (stub for T-018).
    """

    @require_staff_role
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        # Parse and validate request body
        try:
            body = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            return validation_error(message="Invalid JSON body")

        # Basic field presence check
        required_fields = [
            "course_id",
            "unit_usage_key",
            "content_version",
            "transcript",
            "notes",
        ]
        missing = [f for f in required_fields if f not in body]
        if missing:
            return validation_error(message=f"Missing required fields: {', '.join(missing)}")

        # TODO(T-018): Implement materials ingestion
        logger.info(
            "Materials ingest endpoint called (stub)",
            extra={
                "actor": request.ai_tutor_actor.to_dict(),
                "course_id": body.get("course_id"),
                "unit_usage_key": body.get("unit_usage_key"),
            },
        )

        return make_error_response(
            "service_unavailable",
            status=501,
            message="Endpoint not yet implemented",
        )


class MaterialsStatusView(AuthenticatedView):
    """
    GET /api/v1/materials/status - Check materials indexing status.

    Requires: Bearer token + (student context OR staff role).
    Returns: 501 Not Implemented (stub for T-018).
    """

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        actor = request.ai_tutor_actor

        # Accept either student context or staff role
        if not actor.has_student_context and not actor.is_staff:
            from ai_tutor_service.api.errors import forbidden

            return forbidden()

        # Get query parameters
        course_id = request.GET.get("course_id")
        unit_usage_key = request.GET.get("unit_usage_key")

        if not course_id or not unit_usage_key:
            return validation_error(message="Query parameters required: course_id, unit_usage_key")

        # TODO(T-018): Implement materials status check
        logger.info(
            "Materials status endpoint called (stub)",
            extra={
                "actor": actor.to_dict(),
                "course_id": course_id,
                "unit_usage_key": unit_usage_key,
            },
        )

        return make_error_response(
            "service_unavailable",
            status=501,
            message="Endpoint not yet implemented",
        )


class ConversationView(AuthenticatedView):
    """
    GET /api/v1/conversation/{id} - Get conversation history.

    Requires: Bearer token + student context headers.
    Returns: 501 Not Implemented (stub for T-018).
    """

    @require_student_context
    def get(self, request: HttpRequest, conversation_id: str, *args: Any, **kwargs: Any) -> HttpResponse:
        # TODO(T-018): Implement conversation history retrieval
        logger.info(
            "Conversation endpoint called (stub)",
            extra={
                "actor": request.ai_tutor_actor.to_dict(),
                "conversation_id": conversation_id,
            },
        )

        return make_error_response(
            "service_unavailable",
            status=501,
            message="Endpoint not yet implemented",
        )


class ConfigView(AuthenticatedView):
    """
    GET /api/v1/config - Get public configuration projection.

    Requires: Bearer token (no student context or staff role needed).
    Returns: 501 Not Implemented (stub for T-018).
    """

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        # TODO(T-018): Implement config endpoint
        logger.info(
            "Config endpoint called (stub)",
            extra={"actor": request.ai_tutor_actor.to_dict()},
        )

        return make_error_response(
            "service_unavailable",
            status=501,
            message="Endpoint not yet implemented",
        )


@method_decorator(csrf_exempt, name="dispatch")
class GateRunView(AuthenticatedView):
    """
    POST /api/v1/gate/run - Run release gate evaluation (staff only).

    Requires: Bearer token + X-AI-Tutor-Role: staff header.
    Returns: 501 Not Implemented (stub for T-044).
    """

    @require_staff_role
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        # Parse and validate request body
        try:
            body = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            return validation_error(message="Invalid JSON body")

        # Basic field presence check
        required_fields = ["sample_version", "route"]
        missing = [f for f in required_fields if f not in body]
        if missing:
            return validation_error(message=f"Missing required fields: {', '.join(missing)}")

        # TODO(T-044): Implement gate evaluation
        logger.info(
            "Gate run endpoint called (stub)",
            extra={
                "actor": request.ai_tutor_actor.to_dict(),
                "sample_version": body.get("sample_version"),
                "route": body.get("route"),
            },
        )

        return make_error_response(
            "service_unavailable",
            status=501,
            message="Endpoint not yet implemented",
        )


# Function-based view wrappers for URL routing
ask_view = AskView.as_view()
materials_view = MaterialsView.as_view()
materials_status_view = MaterialsStatusView.as_view()
conversation_view = ConversationView.as_view()
config_view = ConfigView.as_view()
gate_run_view = GateRunView.as_view()