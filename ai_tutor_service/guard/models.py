# impl: FR-002-14
"""
BlockRecord model for AI Tutor Service.

Append-only audit record created atomically with terminal blocked message
and before API response (FR-002-07, SC-004).
"""

import uuid
from django.db import models
from django.core.exceptions import ValidationError


class BlockRecord(models.Model):
    """
    Append-only audit record for blocked messages.
    Created atomically with terminal blocked message.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message_id = models.ForeignKey(
        "conversations.Message",
        on_delete=models.PROTECT,
        related_name="block_records",
        db_column="message_id",
    )
    conversation_id = models.ForeignKey(
        "conversations.Conversation",
        on_delete=models.PROTECT,
        related_name="block_records",
        db_column="conversation_id",
    )
    # Denormalized for audit
    user_id = models.CharField(max_length=255, blank=False, null=False)
    course_id = models.CharField(max_length=255, blank=False, null=False)
    unit_usage_key = models.CharField(max_length=255, blank=False, null=False)
    question = models.TextField(blank=False, null=False)
    reason = models.TextField(blank=False, null=False)
    guard_model_id = models.CharField(max_length=255, blank=False, null=False)
    config_version = models.CharField(max_length=50, blank=False, null=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["conversation_id", "created_at"]),
            models.Index(fields=["user_id", "created_at"]),
            models.Index(fields=["course_id", "unit_usage_key", "created_at"]),
        ]
        # Append-only: no UPDATE/DELETE in normal operation
        # This is enforced by application logic and database permissions

    def __str__(self):
        return f"BlockRecord({self.message_id_id} reason={self.reason[:50]})"

    def clean(self):
        super().clean()

        # All fields required (enforced by field definitions)
        if not self.question or not self.question.strip():
            raise ValidationError({"question": "Question text cannot be empty."})
        if not self.reason or not self.reason.strip():
            raise ValidationError({"reason": "Block reason cannot be empty."})
        if not self.guard_model_id or not self.guard_model_id.strip():
            raise ValidationError({"guard_model_id": "Guard model ID is required."})
        if not self.config_version or not self.config_version.strip():
            raise ValidationError({"config_version": "Config version is required."})

    def save(self, *args, **kwargs):
        # Prevent updates to existing records (append-only)
        if self.pk:
            raise ValidationError("BlockRecord is append-only and cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # Prevent deletion of records (append-only)
        raise ValidationError("BlockRecord is append-only and cannot be deleted.")