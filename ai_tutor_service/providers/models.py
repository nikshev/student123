# impl: FR-002-14
"""
LLMUsageLog model for AI Tutor Service.

Primary record of SC-006 (costs), append-only service log:
request_id, user_id, model_id, operation (generate/guard/off_topic/gate),
input/output token counts, estimated_cost_usd, config_version, timestamp.
Price calculated only from rates of same YAML version; provider invoice is
reconciliation only, not replacement of primary record.
"""

import uuid
from django.db import models
from django.core.validators import MinValueValidator


class LLMUsageLog(models.Model):
    """
    Append-only log of LLM usage for cost tracking.
    """

    class Operation(models.TextChoices):
        GENERATE = "generate", "Generate"
        GUARD = "guard", "Guard"
        OFF_TOPIC = "off_topic", "Off Topic"
        GATE = "gate", "Gate"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.UUIDField(blank=False, null=False, db_index=True)
    user_id = models.CharField(max_length=255, blank=False, null=False)
    model_id = models.CharField(max_length=255, blank=False, null=False)
    operation = models.CharField(
        max_length=20,
        choices=Operation.choices,
        blank=False,
        null=False,
    )
    input_tokens = models.PositiveIntegerField(
        validators=[MinValueValidator(0)],
        blank=False,
        null=False,
    )
    output_tokens = models.PositiveIntegerField(
        validators=[MinValueValidator(0)],
        blank=False,
        null=False,
    )
    estimated_cost_usd = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        blank=False,
        null=False,
    )
    config_version = models.CharField(max_length=50, blank=False, null=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["request_id"]),
            models.Index(fields=["user_id", "created_at"]),
            models.Index(fields=["model_id", "created_at"]),
            models.Index(fields=["operation", "created_at"]),
            models.Index(fields=["config_version", "created_at"]),
        ]

    def __str__(self):
        return f"LLMUsageLog({self.request_id} {self.operation} ${self.estimated_cost_usd})"

    def clean(self):
        super().clean()
        if self.input_tokens < 0:
            raise ValidationError({"input_tokens": "Input tokens cannot be negative."})
        if self.output_tokens < 0:
            raise ValidationError({"output_tokens": "Output tokens cannot be negative."})
        if self.estimated_cost_usd < 0:
            raise ValidationError({"estimated_cost_usd": "Estimated cost cannot be negative."})
        if not self.config_version or not self.config_version.strip():
            raise ValidationError({"config_version": "Config version is required."})