# impl: FR-002-14
"""
DailyCounter and IdempotencyRecord models for AI Tutor Service.

DailyCounter: composite PK user_id+date_utc, accepted_count atomic 0..daily_limit,
never decreases. Limit reserved atomically before paid LLM call.

IdempotencyRecord: request_id PK, user_id, payload_hash, response, created_at.
"""

import json
import uuid
from django.db import models
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError


class DailyCounter(models.Model):
    """
    Daily quota counter per user.
    Composite key: user_id + date_utc (enforced via UniqueConstraint).
    accepted_count: atomic 0..daily_limit, never decreases.
    """

    user_id = models.CharField(max_length=255, blank=False, null=False)
    date_utc = models.DateField(blank=False, null=False)
    accepted_count = models.PositiveIntegerField(
        default=0,
        validators=[MinValueValidator(0)],
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user_id", "date_utc"],
                name="unique_user_date",
            ),
        ]
        indexes = [
            models.Index(fields=["date_utc"]),
        ]

    def __str__(self):
        return f"DailyCounter({self.user_id} {self.date_utc}: {self.accepted_count})"

    def clean(self):
        super().clean()
        if self.accepted_count < 0:
            raise ValidationError({"accepted_count": "accepted_count cannot be negative."})


class IdempotencyRecord(models.Model):
    """
    Idempotency record for request deduplication.
    request_id is the primary key.
    Stores payload_hash and serialized response for durable idempotency.
    """

    request_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.CharField(max_length=255, blank=False, null=False)
    payload_hash = models.CharField(max_length=64, blank=False, null=False)  # SHA-256 hex
    response = models.JSONField(blank=False, null=False)  # serialized response
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user_id", "created_at"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"IdempotencyRecord({self.request_id} user={self.user_id})"

    def clean(self):
        super().clean()
        if not self.payload_hash or not self.payload_hash.strip():
            raise ValidationError({"payload_hash": "Payload hash cannot be empty."})
        if self.response is None:
            raise ValidationError({"response": "Response cannot be null."})