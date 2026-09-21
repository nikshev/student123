# impl: FR-002-14
"""
Retention service for AI Tutor Service.

Implements data minimization and TTL-based purge of expired conversations.
"""

from datetime import timedelta
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from ai_tutor_service.config import load_tutor_config
from ai_tutor_service.conversations.models import Conversation

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "tutor_config.yaml"


def _get_ttl_days() -> int:
    """Read conversation TTL (in days) from YAML config."""
    config = load_tutor_config(_CONFIG_PATH)
    return config["conversation_ttl_days"]


def purge_expired(now=None) -> int:
    """
    Physically delete all Conversations whose expires_at <= now.

    Messages are cascade-deleted via the FK (on_delete=CASCADE).
    Returns the number of Conversation rows deleted.
    Idempotent: safe to call repeatedly; subsequent calls with no matching
    rows return 0 and never raise.

    Parameters
    ----------
    now : datetime, optional
        Cutoff timestamp. Defaults to timezone.now().

    Returns
    -------
    int
        Number of Conversation rows deleted.
    """
    if now is None:
        now = timezone.now()

    # The Conversation model has an index on expires_at, so this is efficient.
    # Use a subquery to count before delete to return the exact number of
    # Conversation rows deleted (QuerySet.delete() returns total rows including
    # cascade-deleted Messages, which we don't want).
    expired_qs = Conversation.objects.filter(expires_at__lte=now)
    deleted_count = expired_qs.count()

    if deleted_count > 0:
        with transaction.atomic():
            expired_qs.delete()

    return deleted_count