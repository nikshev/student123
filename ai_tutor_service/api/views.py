# impl: FR-002-03
"""
AI Tutor Service API views for materials endpoints.

Implements POST /materials and GET /materials/status per contracts/tutor-service-api.md §3–4.
Other endpoints remain as stubs for future implementation.
"""

import json
import logging
import uuid
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from ai_tutor_service.api.auth import (
    require_staff_role,
    require_student_context,
)
from ai_tutor_service.api.errors import (
    make_error_response,
    validation_error,
    idempotency_conflict,
    service_unavailable,
)
from ai_tutor_service.materials.repository import MaterialRepository

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
class MaterialsView(View):
    """
    POST /api/v1/materials - Staff ingests course materials.

    Requires: Bearer token + X-AI-Tutor-Role: staff header + Idempotency-Key.
    Returns: 201 with material_id, status, segment_count, checksum.
    """

    # Instantiate repository once per class (stateless)
    _repository = MaterialRepository()

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        # Check authentication via middleware
        actor = getattr(request, "ai_tutor_actor", None)
        if actor is None:
            return service_unavailable()

        # Require staff role
        if not actor.is_staff:
            from ai_tutor_service.api.errors import forbidden
            return forbidden()

        # Parse and validate request body
        try:
            body = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            return validation_error(message="Invalid JSON body")

        # Check required fields
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

        # Idempotency-Key is mandatory
        idempotency_key = request.META.get("HTTP_IDEMPOTENCY_KEY")
        if not idempotency_key:
            return validation_error(message="Idempotency-Key header is required")

        # Validate idempotency key format (UUID)
        try:
            uuid.UUID(idempotency_key)
        except ValueError:
            return validation_error(message="Idempotency-Key must be a valid UUID")

        # Check idempotency
        user_id_for_idempotency = actor.user_id if actor.user_id else ("staff" if actor.is_staff else "anonymous")
        try:
            cached_response = self._repository.check_idempotency(idempotency_key, body, user_id_for_idempotency)
        except ValueError as e:
            if str(e) == "idempotency_conflict":
                return idempotency_conflict()
            raise

        if cached_response is not None:
            # Return cached response for same key + same payload
            return JsonResponse(cached_response, status=201)

        # Create material
        try:
            material_id = self._repository.create_material(
                course_id=body["course_id"],
                unit_usage_key=body["unit_usage_key"],
                content_version=body["content_version"],
                transcript=body["transcript"],
                notes=body["notes"],
            )
        except ValueError as e:
            return validation_error(message=str(e))
        except Exception as e:
            logger.exception("Failed to create material")
            return service_unavailable()

        # Compute checksum for response
        from ai_tutor_service.materials.repository import MaterialRepository
        checksum = MaterialRepository()._compute_checksum(body)

        response_data = {
            "material_id": str(material_id),
            "status": "READY",
            "segment_count": len(body["transcript"]) + len(body["notes"]),
            "checksum": checksum,
        }

        # Store idempotency record
        # For staff operations without user_id header, use "staff" as fallback
        user_id_for_idempotency = actor.user_id if actor.user_id else ("staff" if actor.is_staff else "anonymous")
        self._repository.store_idempotency(idempotency_key, body, response_data, material_id, user_id_for_idempotency)

        logger.info(
            "Materials ingest successful",
            extra={
                "material_id": str(material_id),
                "course_id": body["course_id"],
                "unit_usage_key": body["unit_usage_key"],
                "content_version": body["content_version"],
            },
        )

        return JsonResponse(response_data, status=201)


class MaterialsStatusView(View):
    """
    GET /api/v1/materials/status - Check materials indexing status.

    Requires: Bearer token + (student context OR staff role).
    Returns: 200 with status, content_version, segment_count, config_version.
    """

    _repository = MaterialRepository()

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        # Check authentication via middleware
        actor = getattr(request, "ai_tutor_actor", None)
        if actor is None:
            return service_unavailable()

        # Accept either student context or staff role
        if not actor.has_student_context and not actor.is_staff:
            from ai_tutor_service.api.errors import forbidden
            return forbidden()

        # Get query parameters
        course_id = request.GET.get("course_id")
        unit_usage_key = request.GET.get("unit_usage_key")

        if not course_id or not unit_usage_key:
            return validation_error(message="Query parameters required: course_id, unit_usage_key")

        try:
            status_data = self._repository.get_status_summary(course_id, unit_usage_key)
        except Exception as e:
            logger.exception("Failed to get material status")
            return service_unavailable()

        return JsonResponse(status_data, status=200)


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
config_view = ConfigView.as_view()
gate_run_view = GateRunView.as_view()