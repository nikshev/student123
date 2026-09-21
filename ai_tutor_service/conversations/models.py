# impl: FR-002-14
"""
Conversation and Message models for AI Tutor Service.

Conversation: user_id, course_id, unit_usage_key immutable; expires_at = created_at + TTL.
Message: role student/tutor; statuses asked/shown/blocked/no_materials/off_topic/error;
terminal status immutable; blocked never becomes shown; shown requires non-empty sources;
blocked text null/rule-only, blocked_reason required; latency_ms >=0 for terminal tutor;
route="default"; config_version present (Constitution III).
"""

import uuid
from django.db import models
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError
from django.conf import settings


class Conversation(models.Model):
    """A conversation scoped to a user, course, and unit."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.CharField(max_length=255, blank=False, null=False)
    course_id = models.CharField(max_length=255, blank=False, null=False)
    unit_usage_key = models.CharField(max_length=255, blank=False, null=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(blank=False, null=False)

    class Meta:
        indexes = [
            models.Index(fields=["user_id", "course_id", "unit_usage_key"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self):
        return f"Conversation({self.user_id} @ {self.course_id}/{self.unit_usage_key})"

    def save(self, *args, **kwargs):
        # Compute expires_at from config on creation.
        # Use _state.adding (not self.pk) because UUIDField default
        # generates a PK on __init__, so self.pk is always truthy for
        # new instances — checking self.pk would block all creates.
        # We use timezone.now() here, not self.created_at, because
        # auto_now_add is applied in super().save() → pre_save, which
        # runs AFTER our custom code.
        if self._state.adding and not self.expires_at:
            from ai_tutor_service.config import get_config
            config = get_config()
            from datetime import timedelta
            from django.utils import timezone
            self.expires_at = timezone.now() + timedelta(days=config.conversation_ttl_days)
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        # course_id and unit_usage_key are immutable after creation
        if self.pk:
            original = Conversation.objects.get(pk=self.pk)
            if original.course_id != self.course_id:
                raise ValidationError({"course_id": "course_id is immutable after creation."})
            if original.unit_usage_key != self.unit_usage_key:
                raise ValidationError(
                    {"unit_usage_key": "unit_usage_key is immutable after creation."}
                )


class Message(models.Model):
    """A message within a conversation."""

    class Role(models.TextChoices):
        STUDENT = "student", "Student"
        TUTOR = "tutor", "Tutor"

    class Status(models.TextChoices):
        ASKED = "asked", "Asked"
        SHOWN = "shown", "Shown"
        BLOCKED = "blocked", "Blocked"
        NO_MATERIALS = "no_materials", "No Materials"
        OFF_TOPIC = "off_topic", "Off Topic"
        ERROR = "error", "Error"

    # Terminal statuses that cannot be changed once set
    TERMINAL_STATUSES = {
        Status.SHOWN,
        Status.BLOCKED,
        Status.NO_MATERIALS,
        Status.OFF_TOPIC,
        Status.ERROR,
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation_id = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
        db_column="conversation_id",
    )
    role = models.CharField(max_length=10, choices=Role.choices, blank=False, null=False)
    text = models.TextField(blank=True, null=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ASKED,
        db_index=True,
    )
    topic = models.CharField(max_length=255, blank=False, null=False, default="other")
    sources = models.JSONField(default=list, blank=True)
    blocked_reason = models.TextField(blank=True, null=True)
    latency_ms = models.PositiveIntegerField(
        validators=[MinValueValidator(0)],
        blank=True,
        null=True,
    )
    route = models.CharField(max_length=50, default="default", blank=False, null=False)
    config_version = models.CharField(max_length=50, blank=False, null=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["conversation_id", "created_at"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"Message({self.conversation_id_id} {self.role} {self.status})"

    def clean(self):
        super().clean()

        # Student messages must have non-empty text
        if self.role == self.Role.STUDENT:
            if not self.text or not self.text.strip():
                raise ValidationError({"text": "Student message text cannot be empty."})

        # Terminal status validation
        if self.pk:
            original = Message.objects.get(pk=self.pk)
            if original.status in self.TERMINAL_STATUSES and original.status != self.status:
                raise ValidationError(
                    {"status": f"Terminal status '{original.status}' is immutable."}
                )

            # blocked never becomes shown
            if original.status == self.Status.BLOCKED and self.status == self.Status.SHOWN:
                raise ValidationError(
                    {"status": "Blocked message cannot transition to shown. Create a new turn."}
                )

        # shown requires non-empty sources
        if self.status == self.Status.SHOWN:
            if not self.sources or len(self.sources) == 0:
                raise ValidationError({"sources": "Shown message must have at least one source."})

        # blocked: text is null or rule-only (from YAML), blocked_reason required
        if self.status == self.Status.BLOCKED:
            if self.blocked_reason is None or not self.blocked_reason.strip():
                raise ValidationError(
                    {"blocked_reason": "Blocked message must have a non-empty blocked_reason."}
                )
            # text can be null or rule-only - we allow null/blank for blocked
            # The actual rule text comes from YAML and is set by the pipeline

        # config_version required for all messages
        if not self.config_version or not self.config_version.strip():
            raise ValidationError({"config_version": "config_version is required."})

        # latency_ms >= 0 for terminal tutor messages (enforced by PositiveIntegerField)
        # route default is "default" (enforced by field default)


class AuditRecord(models.Model):
    """
    Append-only audit record for authorized ops read of conversation history.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.CharField(max_length=255, blank=False, null=False)
    conversation_id = models.UUIDField(blank=False, null=False)
    action = models.CharField(max_length=50, blank=False, null=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["conversation_id", "created_at"], name="conv_audit_cid_created_idx"),
        ]
        # Append-only: no UPDATE/DELETE in normal operation

    def __str__(self):
        return f"AuditRecord({self.actor} -> {self.conversation_id} ({self.action}))"

    def save(self, *args, **kwargs):
        # Append-only: prevent updates
        if not self._state.adding:
            raise ValidationError("AuditRecord is append-only and cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("AuditRecord is append-only and cannot be deleted.")