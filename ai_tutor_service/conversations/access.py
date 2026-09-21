# impl: FR-002-14
"""
Audited ops access for AI Tutor Service.

Provides append-only audit logging for authorized staff/ops reads
of conversation history.

"Audited context" means an explicit trusted header `X-AI-Tutor-Ops-Audit`
from ops tooling (non-empty string value = audit-id/reason). This header
must be present for staff to be granted read access via the audited path
(see api/conversation.py). Staff requests without this header receive
indistinguishable 404-like denials (FR-002-12).
"""

from django.conf import settings

from ai_tutor_service.conversations.models import AuditRecord

# Actors that are authorized for ops reads. Roles mirror the trusted-header
# roles of the platform (auth.py); this is the deployment identity policy,
# not a behavioral constant of the result.
AUTHORIZED_ACTOR_ROLES = frozenset(
    getattr(settings, "AI_TUTOR_OPS_ROLES", ("staff", "ops", "admin"))
)


def _is_authorized(actor: str) -> bool:
    """Check whether the actor is an authorized ops/staff user.

    Case-insensitive exact match against AUTHORIZED_ACTOR_ROLES.
    """
    return actor.strip().lower() in AUTHORIZED_ACTOR_ROLES


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


def authorize_staff_read(actor: str, conversation_id: str) -> tuple[bool, AuditRecord | None]:
    """Authorize a staff read with audit logging.

    Returns (True, AuditRecord) if the actor is authorized and the read
    was logged; (False, None) otherwise.
    """
    if not _is_authorized(actor):
        return (False, None)
    record = log_ops_read(actor, conversation_id)
    return (True, record)
