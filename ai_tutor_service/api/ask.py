# impl: FR-002-01
"""
POST /api/v1/ask - Student asks a question (T-020).

Validates request, resolves conversation, runs TutoringPipeline,
returns terminal response with exact schema.
"""

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from django.db import OperationalError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from ai_tutor_service.api.auth import require_student_context
from ai_tutor_service.api.errors import (
    actor_mismatch,
    idempotency_conflict,
    make_error_response,
    not_found,
    quota_exceeded,
    service_unavailable,
    validation_error,
)
from ai_tutor_service.config import load_tutor_config
from ai_tutor_service.limits.service import DailyQuota, QuotaExceededError
from ai_tutor_service.providers.client import LLMError
from ai_tutor_service.tutoring.pipeline import (
    ActorMismatchError,
    ConversationNotFoundError,
    IdempotencyConflict,
    TutoringPipeline,
)

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parents[1] / "tutor_config.yaml"


@method_decorator(csrf_exempt, name="dispatch")
class AskView(View):
    """POST /api/v1/ask"""

    @require_student_context
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        request_id = str(uuid.uuid4())
        config = load_tutor_config(str(CONFIG_PATH))

        # --- Parse body ---
        try:
            body = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            return validation_error(request_id, message="Invalid JSON body")
        if not isinstance(body, dict):
            return validation_error(request_id, message="Request body must be a JSON object")

        question = body.get("question", "").strip() if isinstance(body, dict) else ""
        route = body.get("route") if isinstance(body, dict) else None
        conversation_id_str = body.get("conversation_id") if isinstance(body, dict) else None

        # --- Validation ---
        if not question:
            return validation_error(request_id, message="Question is required")
        if len(question) > config["question_max_chars"]:
            return validation_error(request_id, message="Question exceeds maximum length")
        if route != "default":
            return validation_error(request_id, message='Only route="default" is supported')
        if "route" not in body:
            return validation_error(request_id, message="Route is required")

        # --- Idempotency key ---
        idempotency_key_str = request.META.get("HTTP_IDEMPOTENCY_KEY")
        if not idempotency_key_str:
            return validation_error(request_id, message="Idempotency-Key header is required")
        try:
            idempotency_key = uuid.UUID(idempotency_key_str)
        except ValueError:
            return validation_error(request_id, message="Idempotency-Key must be a valid UUID")

        # --- Actor context ---
        actor = request.ai_tutor_actor
        user_id = actor.user_id
        course_id = actor.course_id
        unit_usage_key = actor.unit_usage_key

        # --- Resolve conversation_id ---
        conversation_id = None
        if conversation_id_str is not None:
            try:
                conversation_id = uuid.UUID(str(conversation_id_str))
            except (ValueError, TypeError):
                return not_found(request_id)

        # --- Reserve daily quota slot (atomic, idempotent) ---
        try:
            daily_remaining = DailyQuota.reserve(
                user_id=user_id,
                request_id=request_id,
            )
        except QuotaExceededError as exc:
            return quota_exceeded(request_id, daily_remaining=exc.daily_remaining)

        # --- Run pipeline ---
        pipeline = TutoringPipeline(config=config)
        try:
            response_data = pipeline.run(
                question=question,
                user_id=user_id,
                course_id=course_id,
                unit_usage_key=unit_usage_key,
                conversation_id=conversation_id,
                idempotency_key=idempotency_key,
                request_id=uuid.UUID(request_id),
                daily_remaining=daily_remaining,
            )
        except IdempotencyConflict:
            return idempotency_conflict(request_id)
        except ConversationNotFoundError:
            return not_found(request_id)
        except ActorMismatchError:
            return actor_mismatch(request_id)
        except OperationalError as e:
            logger.exception("OperationalError in ask view: %s", e)
            return service_unavailable(request_id)
        except LLMError as e:
            if e.error_type == "timeout":
                return make_error_response("timeout", request_id, status=504, message="Час очікування вичерпано. Спробуйте ще раз.")
            elif e.error_type == "malformed_response":
                return make_error_response("invalid_upstream_response", request_id, status=502, message="Невірна відповідь від моделі. Спробуйте ще раз.")
            else:
                return make_error_response("upstream_error", request_id, status=502, message="Помилка зовнішнього сервісу. Спробуйте пізніше.")

        return JsonResponse(response_data, status=200)


# Function-based view wrapper for URL routing
def ask_view(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
    return AskView().post(request, *args, **kwargs)