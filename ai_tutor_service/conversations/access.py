# impl: FR-002-14
"""
Audited ops access for AI Tutor Service.

Provides append-only audit logging for authorized staff/ops reads
of conversation history.
"""

from django.conf import settings

from ai_tutor_service.conversations.models import AuditRecord

# Actors that are authorized for ops reads
AUTHORIZED_ACTOR_ROLES = getattr(
    settings, "AI_TUTOR_OPS_ROLES", ("staff", "ops", "admin")
)


def _is_authorized(actor: str) -> bool:
    """Check whether the actor is an authorized ops/staff user."""
    return any(
        actor.lower().startswith(role)
        for role in AUTHORIZED_ACTOR_ROLES
    )


def log_ops_read(actor: str, conversation_id: str) -> AuditRecord:
    """
    Create an append-only audit record when authorized staff/ops
    read a conversation history.

    Parameters
    ----------
    actor : str
        Identifier of the requesting user (e.g. "staff-user").
    conversation_id : str
        UUID string of the conversation being read.

    Returns
    -------
    AuditRecord
        The persisted audit record.

    Raises
    ------
    PermissionError
        If the actor is not authorized for ops reads.
    """
    if not _is_authorized(actor):
        raise PermissionError(
            f"Actor {actor!r} is not authorized for ops reads"
        )

    record = AuditRecord.objects.create(
        actor=actor,
        conversation_id=conversation_id,
        action="read",
    )
    return record
