# impl: FR-002-12
"""
Conversation history repository with the ownership policy for FR-002-12.

The repository is the only place that can decide whether a conversation is
visible. It returns ``(conversation, messages)`` only when the conversation
exists, is not expired, and matches the actor's ``user_id``, ``course_id`` and
``unit_usage_key``.  Unknown, expired and ownership-mismatched conversations are
represented by separate typed errors so that the API layer can deliberately map
all of them to the same 404-like envelope; no existence or ownership detail
escapes.
"""

from django.utils import timezone

from ai_tutor_service.conversations.models import Conversation, Message


class ConversationNotFoundError(Exception):
    """The requested conversation does not exist or has expired."""


class ActorMismatchError(Exception):
    """The authenticated actor does not own the requested conversation."""


def get_conversation_history(conversation_id, user_id, course_id, unit_usage_key):
    """
    Return ``(conversation, messages)`` for an owned, non-expired conversation.

    Raises:
        ConversationNotFoundError: the ID is unknown or the conversation is
            expired.
        ActorMismatchError: the actor/course/unit does not match the owner.
    """
    try:
        conversation = Conversation.objects.get(pk=conversation_id)
    except Conversation.DoesNotExist:
        raise ConversationNotFoundError()

    if (
        conversation.user_id != user_id
        or conversation.course_id != course_id
        or conversation.unit_usage_key != unit_usage_key
    ):
        raise ActorMismatchError()

    # Expiry is deliberately treated as a not-found denial.  This prevents an
    # actor from learning that an expired ID ever belonged to them.
    if conversation.expires_at <= timezone.now():
        raise ConversationNotFoundError()

    messages = list(
        conversation.messages.order_by("created_at", "id").only(
            "id",
            "role",
            "text",
            "status",
            "topic",
            "sources",
            "blocked_reason",
            "latency_ms",
            "route",
            "config_version",
            "created_at",
        )
    )
    return conversation, messages
