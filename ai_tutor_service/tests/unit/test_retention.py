# verifies: FR-002-14
"""
Retention and data-minimization tests for FR-002-14.

These tests pin the contract for T-052 (retention service + management
command + audited ops access). The retention service
`ai_tutor_service.conversations.retention`, the management command
`purge_expired_tutor_data`, and the audited ops access module
`ai_tutor_service.conversations.access` do NOT exist yet.

Expected RED reasons (written first, TDD):
1. Module-level import of `purge_expired` from
   `ai_tutor_service.conversations.retention` raises `ModuleNotFoundError`
   at collection time — the retention service is not implemented.
2. `call_command("purge_expired_tutor_data")` raises `CommandError`
   (unknown command) — the Django management command is not registered.

Contract for T-052 (the implementation must satisfy exactly this interface):

    purge_expired(now: datetime | None = None) -> int

    - Deletes every Conversation whose expires_at <= now, with physical
      CASCADE deletion of all child Message rows.
    - Leaves Conversations with expires_at > now untouched.
    - Is idempotent: two consecutive calls produce the same result and
      never raise, even when no rows match.
    - TTL is read from ai_tutor_service/tutor_config.yaml via
      load_tutor_config(); never hard-coded in Python.

    call_command("purge_expired_tutor_data")

    - Django management command wrapping purge_expired; idempotent and
      network-free.

    log_ops_read(actor: str, conversation_id: str) -> AuditRecord

    - Creates an append-only audit record when authorized staff/ops
      reads a conversation history (FR-002-14). The audit record must
      be persisted in the DB and must never contain candidate answer
      text or secrets.

Data-minimization invariants (already enforced by existing models,
verified here):
    - BlockRecord and LLMUsageLog never persist the candidate answer text.
    - No DB record (Conversation, Message, BlockRecord, IdempotencyRecord,
      LLMUsageLog, DailyCounter) contains secret values or "secret"/"api-key"
      markers in any CharField/TextField.
    - Secrets live only in Django settings (AI_TUTOR_SHARED_SECRET,
      AI_TUTOR_LLM_API_KEY) and are absent from DB, logs, HTML and OLX.
      Log/HTML/OLX checks are done by other test files.

Tests never touch the network (autouse _disable_network in conftest.py)
and use the existing models from ai_tutor_service.*.models.
"""

import uuid
from datetime import timedelta, timezone
from pathlib import Path

import pytest
from django.core.management import call_command
from django.utils import timezone as django_timezone

from ai_tutor_service.config import load_tutor_config
from ai_tutor_service.conversations.models import Conversation, Message
from ai_tutor_service.conversations.retention import purge_expired  # noqa: F401
from ai_tutor_service.guard.models import BlockRecord
from ai_tutor_service.limits.models import DailyCounter, IdempotencyRecord
from ai_tutor_service.providers.models import LLMUsageLog

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = REPO_ROOT / "ai_tutor_service" / "tutor_config.yaml"

CANDIDATE_TEXT = "FULL_FINAL_SOLUTION_42"


def _config():
    return load_tutor_config(CONFIG_PATH)


def _conversation(**kwargs):
    """Create a Conversation with safe defaults."""
    defaults = {
        "user_id": "retention-user",
        "course_id": "course-v1:open-edx+ai-tutor+2026",
        "unit_usage_key": "block-v1:open-edx+ai-tutor+2026+type@vertical+block@unit-1",
    }
    defaults.update(kwargs)
    return Conversation.objects.create(**defaults)


def _message(conversation, **kwargs):
    """Create a Message with safe defaults."""
    defaults = {
        "conversation_id": conversation,
        "role": Message.Role.STUDENT,
        "text": "safe question",
        "status": Message.Status.ASKED,
        "config_version": _config()["version"],
    }
    defaults.update(kwargs)
    return Message.objects.create(**defaults)


def _string_values(instance):
    """Return all string values of all concrete fields of a model instance."""
    values = []
    for field in instance._meta.concrete_fields:
        value = getattr(instance, field.attname, None)
        if value is None:
            continue
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, (list, tuple, dict)):
            values.append(str(value))
    return values


def _assert_no_candidate(instance):
    """Assert candidate text is absent from every string field."""
    for value in _string_values(instance):
        assert CANDIDATE_TEXT not in value, (
            f"candidate leaked into {instance.__class__.__name__}.{value!r}"
        )


_STANDALONE_MARKERS = {"secret", "api-key", "api_key", "token", "password",
                       "private_key", "access_token", "refresh_token"}


def _assert_no_secret_markers(instance):
    """Assert no CharField/TextField holds a standalone secret marker."""
    for value in _string_values(instance):
        lowered = value.lower()
        assert lowered not in _STANDALONE_MARKERS, (
            f"secret marker '{value}' found in {instance.__class__.__name__}"
        )


@pytest.mark.django_db
def test_expires_at_is_created_at_plus_yaml_ttl():
    """expires_at = created_at + conversation_ttl_days read from YAML."""
    ttl_days = _config()["conversation_ttl_days"]
    conversation = _conversation()
    expected = conversation.created_at + timedelta(days=ttl_days)
    assert abs((conversation.expires_at - expected).total_seconds()) < 60


@pytest.mark.django_db
def test_purge_deletes_expired_conversation_and_messages_cascade():
    """Expired conversation and all child messages are physically deleted."""
    conversation = _conversation(
        expires_at=django_timezone.now() - timedelta(days=1)
    )
    _message(conversation)
    _message(
        conversation,
        role=Message.Role.TUTOR,
        text="safe answer",
        status=Message.Status.SHOWN,
    )

    purge_expired()

    assert Conversation.objects.filter(pk=conversation.pk).count() == 0
    assert Message.objects.filter(conversation_id=conversation).count() == 0
    with pytest.raises(Conversation.DoesNotExist):
        Conversation.objects.get(pk=conversation.pk)


@pytest.mark.django_db
def test_purge_leaves_active_conversation_and_messages():
    """Conversations with expires_at in the future are not deleted."""
    conversation = _conversation(
        expires_at=django_timezone.now() + timedelta(days=30)
    )
    _message(conversation)
    _message(
        conversation,
        role=Message.Role.TUTOR,
        text="safe answer",
        status=Message.Status.SHOWN,
    )

    purge_expired()

    assert Conversation.objects.filter(pk=conversation.pk).count() == 1
    assert Message.objects.filter(conversation_id=conversation).count() == 2


@pytest.mark.django_db
def test_purge_is_idempotent_and_repeatable():
    """Two consecutive purge calls produce the same result and never raise."""
    conversation = _conversation(
        expires_at=django_timezone.now() - timedelta(days=1)
    )
    _message(conversation)

    first = purge_expired()
    second = purge_expired()

    assert first == 1
    assert second == 0
    assert Conversation.objects.filter(pk=conversation.pk).count() == 0
    assert Message.objects.filter(conversation_id=conversation).count() == 0


@pytest.mark.django_db
def test_management_command_purge_expired_tutor_data_exists_and_is_repeatable():
    """Django management command wraps purge and is idempotent."""
    conversation = _conversation(
        expires_at=django_timezone.now() - timedelta(days=1)
    )
    _message(conversation)

    call_command("purge_expired_tutor_data")
    call_command("purge_expired_tutor_data")

    assert Conversation.objects.filter(pk=conversation.pk).count() == 0
    assert Message.objects.filter(conversation_id=conversation).count() == 0


@pytest.mark.django_db
def test_block_record_does_not_copy_candidate():
    """BlockRecord persists the question, never the candidate answer."""
    conversation = _conversation()
    message = _message(
        conversation,
        role=Message.Role.TUTOR,
        text=None,
        status=Message.Status.BLOCKED,
        blocked_reason="contains solution",
    )

    record = BlockRecord.objects.create(
        message_id=message,
        conversation_id=conversation,
        user_id=conversation.user_id,
        course_id=conversation.course_id,
        unit_usage_key=conversation.unit_usage_key,
        question="solve this",
        reason="guard blocked",
        guard_model_id=_config()["guard_model_id"],
        config_version=_config()["version"],
    )

    _assert_no_candidate(record)
    _assert_no_candidate(message)


@pytest.mark.django_db
def test_llm_usage_log_does_not_copy_candidate():
    """LLMUsageLog stores token counts, never the candidate answer."""
    record = LLMUsageLog.objects.create(
        request_id=uuid.uuid4(),
        user_id="retention-user",
        model_id=_config()["model_id"],
        operation=LLMUsageLog.Operation.GENERATE,
        input_tokens=100,
        output_tokens=50,
        estimated_cost_usd="0.000125",
        config_version=_config()["version"],
    )

    _assert_no_candidate(record)


@pytest.mark.django_db
def test_secrets_absent_from_db_records():
    """No DB record contains secret values or standalone secret markers."""
    from django.conf import settings

    exact_secrets = [
        getattr(settings, "AI_TUTOR_SHARED_SECRET", ""),
        getattr(settings, "AI_TUTOR_LLM_API_KEY", ""),
        settings.SECRET_KEY,
    ]
    exact_secrets = [s for s in exact_secrets if s]

    conversation = _conversation()
    message = _message(conversation)
    daily = DailyCounter.objects.create(
        user_id=conversation.user_id,
        date_utc=django_timezone.now().date(),
        accepted_count=1,
    )
    idempotency = IdempotencyRecord.objects.create(
        request_id=uuid.uuid4(),
        user_id=conversation.user_id,
        payload_hash="a" * 64,
        response={"status": "ok"},
    )

    for instance in [conversation, message, daily, idempotency]:
        for value in _string_values(instance):
            for secret in exact_secrets:
                assert secret not in value, (
                    f"secret value leaked into {instance.__class__.__name__}: {value!r}"
                )
        _assert_no_secret_markers(instance)


@pytest.mark.django_db
def test_authorized_ops_read_is_audited():
    """Authorized ops/staff read of a conversation history writes audit record."""
    from ai_tutor_service.conversations.access import log_ops_read
    from ai_tutor_service.conversations.models import AuditRecord

    conversation = _conversation()

    log_ops_read(actor="staff", conversation_id=str(conversation.pk))

    # The audit record must exist in the DB and be append-only.
    assert AuditRecord.objects.filter(actor="staff", conversation_id=conversation.pk, action="read").exists()


@pytest.mark.django_db
def test_unauthorized_read_is_denied_without_audit():
    """Non-authorized access is denied and does not create an audit record."""
    from ai_tutor_service.conversations.access import log_ops_read
    from ai_tutor_service.conversations.models import AuditRecord

    conversation = _conversation()

    with pytest.raises(PermissionError):
        log_ops_read(actor="regular-student", conversation_id=str(conversation.pk))

    # Non-authorized access must not create an audit record.
    assert not AuditRecord.objects.filter(conversation_id=conversation.pk).exists()
