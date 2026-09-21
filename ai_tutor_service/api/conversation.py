# impl: FR-002-12
"""
GET /api/v1/conversation/{id} — conversation history for FR-002-12.

Student actor context is required.  The endpoint returns the owning student's
conversation only when it exists, is not expired, and belongs to the same
``user_id``/``course_id``/``unit_usage_key``.  Staff without an audited
ops/gate context are never granted read access here (that path is T-052).

Ownership policy (FR-002-12, contracts/tutor-service-api.md §5): every denial —
unknown ID, expired, owner mismatch, or staff without audited context — is
collapsed into the identical 404 "conversation_not_found" envelope so that
existence and ownership are indistinguishable.  candidate answer text and
foreign IDs never appear in any response.
"""

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from ai_tutor_service.api.auth import require_student_context
from ai_tutor_service.api.errors import conversation_not_found
from ai_tutor_service.config import load_tutor_config
from ai_tutor_service.conversations.models import Conversation, Message
from ai_tutor_service.conversations.repository import (
    ActorMismatchError,
    ConversationNotFoundError,
    get_conversation_history,
)

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parents[1] / "tutor_config.yaml"



# Only safe terminal tutor statuses and student questions are eligible for
# history projection; candidate answer never exists as a Message.
TERMINAL_STATUSES = {
    Message.Status.SHOWN,
    Message.Status.BLOCKED,
    Message.Status.NO_MATERIALS,
    Message.Status.OFF_TOPIC,
    Message.Status.ERROR,
}


def _daily_remaining() -> int:
    """
    Project a safe ``daily_remaining`` for the history response.

    This is a read-only projection, not a quota enforcement point: per
    constitution V it must not read the quota counter as a source of metrics,
    and per the T-020 precedent ``daily_remaining`` is projected from the
    versioned config until a quota read-service exists.  T-022 added the
    atomic ``DailyQuota.reserve()`` for ``/ask`` mutation, but the history
    endpoint never charges quota, so the stable, version-controlled config
    ``daily_limit`` is the correct source for the response.
    """
    try:
        config = load_tutor_config(str(CONFIG_PATH))
        return int(config["daily_limit"])
    except Exception:
        # Fail-closed to 0 so a missing/broken config never inflates the
        # projected remaining count.
        logger.exception("Failed to load config for daily_remaining projection")
        return 0


def _serialize_message(message: Message) -> dict:
    """
    Serialize a Message row to the history projection.

    Blocked messages expose only their stored rule text (null or the YAML
    rule from the DB when persisted by the pipeline); candidate answer text is
    never a Message and therefore never reaches this projection.
    """
    created_at = message.created_at
    return {
        "id": str(message.id),
        "role": message.role,
        "text": message.text,
        "status": message.status,
        "created_at": created_at.isoformat().replace("+00:00", "Z"),
        "config_version": message.config_version,
        "sources": list(message.sources) if message.sources is not None else [],
        "blocked_reason": message.blocked_reason,
        "topic": message.topic,
        "latency_ms": message.latency_ms,
        "route": message.route,
    }


class ConversationHistoryView(View):
    """GET /api/v1/conversation/{id}."""

    @require_student_context
    def get(
        self,
        request: HttpRequest,
        conversation_id: str,
        *args: Any,
        **kwargs: Any,
    ) -> HttpResponse:
        actor = request.ai_tutor_actor
        request_id = str(uuid.uuid4())

        # Staff is never granted unaudited read access to conversation history.
        # Authorized staff with audited ops/gate context is handled in T-052;
        # here every staff request collapses into the same 404 envelope.
        if actor.is_staff:
            return conversation_not_found(request_id)

        try:
            conversation, messages = get_conversation_history(
                conversation_id=conversation_id,
                user_id=actor.user_id,
                course_id=actor.course_id,
                unit_usage_key=actor.unit_usage_key,
            )
        except (ConversationNotFoundError, ActorMismatchError):
            return conversation_not_found(request_id)

        response_data = {
            "conversation_id": str(conversation.id),
            "messages": [_serialize_message(m) for m in messages],
            "daily_remaining": _daily_remaining(),
        }
        return JsonResponse(response_data, status=200)


def conversation_history_view(
    request: HttpRequest, *args: Any, **kwargs: Any
) -> HttpResponse:
    """Function wrapper so urls.py can route the endpoint as a plain view."""
    return ConversationHistoryView.as_view()(request, *args, **kwargs)
