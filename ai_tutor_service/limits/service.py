# impl: FR-002-11
"""
Atomic daily quota reservation service (T-022).

DailyQuota.reserve(user_id, request_id, now=None) -> int

- Reserves one slot of the daily quota atomically on the UTC date of `now`
  (defaults to timezone.now()).
- daily_limit is read from ai_tutor_service/tutor_config.yaml via
  load_tutor_config(); never hard-coded.
- request_id is a valid UUID string. The reservation is idempotent per
  request_id: repeating the same request_id returns the same
  daily_remaining and does NOT increment accepted_count again.
- If accepted_count < daily_limit: increments accepted_count by 1
  (never above daily_limit) and returns daily_remaining = daily_limit - accepted_count.
- If accepted_count == daily_limit: raises QuotaExceededError with
  attribute daily_remaining == 0 (the API layer maps it to HTTP 429).
  The rejected reservation does NOT change accepted_count.
- accepted_count never decreases (DailyCounter constraint).
- UTC rollover: the next UTC date starts a fresh counter (accepted_count == 0).

Atomicity: the increment is a single conditional UPDATE
(accepted_count__lt=daily_limit) inside transaction.atomic() with
select_for_update() on the DailyCounter row. On SQLite select_for_update is
ignored, but UPDATE statements are serialized by the engine, so the
read-modify-write race is closed at the statement level on every backend:
two concurrent reservations for the same (user, date) can never both succeed
beyond daily_limit.

Durable idempotency: ReservationRecord(request_id UUID PK, user_id, date_utc,
daily_remaining, created_at) stores the reservation outcome. A repeated
request_id hits the PK and returns the stored daily_remaining without touching
DailyCounter. This is separate from IdempotencyRecord (used by /ask pipeline
for response caching) to avoid PK collision.
"""

import uuid
from datetime import datetime

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from ai_tutor_service.config import load_tutor_config
from ai_tutor_service.limits.models import DailyCounter, ReservationRecord


def _config_path():
    """Return the absolute path to the versioned tutor config YAML."""
    from pathlib import Path
    return Path(__file__).resolve().parents[1] / "tutor_config.yaml"


def _daily_limit() -> int:
    """Read daily_limit from the versioned YAML (cached). Never hard-coded."""
    return int(load_tutor_config(str(_config_path()))["daily_limit"])


class QuotaExceededError(Exception):
    """Raised when the daily quota for the user is exhausted.

    Attributes:
        daily_remaining: int, always 0 for this error.
    """

    def __init__(self, daily_remaining: int = 0):
        super().__init__("Daily quota exceeded")
        self.daily_remaining = daily_remaining


class DailyQuota:
    """Atomic daily quota reservation service."""

    @staticmethod
    def reserve(
        user_id: str,
        request_id: str,
        now: datetime | None = None,
    ) -> int:
        """Reserve one slot of the daily quota atomically.

        Args:
            user_id: The user identifier (string).
            request_id: A valid UUID string used as the idempotency key.
            now: Optional datetime (timezone-aware) used to derive the UTC date.
                 Defaults to timezone.now().

        Returns:
            int: daily_remaining = daily_limit - accepted_count after increment.

        Raises:
            ValueError: If request_id is not a valid UUID string.
            QuotaExceededError: If accepted_count == daily_limit (daily_remaining == 0).
        """
        # Validate request_id as UUID string
        try:
            request_uuid = uuid.UUID(str(request_id))
        except (ValueError, AttributeError, TypeError):
            raise ValueError(f"request_id must be a valid UUID string, got {request_id!r}")

        if now is None:
            now = timezone.now()
        date_utc = now.date()

        daily_limit = _daily_limit()

        # Durable idempotency check: if this request_id already has a
        # ReservationRecord, return the stored daily_remaining without
        # touching DailyCounter.
        try:
            existing = ReservationRecord.objects.get(request_id=request_uuid)
        except ReservationRecord.DoesNotExist:
            pass
        else:
            return existing.daily_remaining

        # Atomic reservation. The increment is a single conditional UPDATE
        # statement: on any backend the row is re-checked inside the statement,
        # so two concurrent reservations for the same (user, date) can never
        # push accepted_count above daily_limit. select_for_update additionally
        # serializes the read-modify-write on backends that support it
        # (SQLite executes UPDATE statements serialized anyway).
        with transaction.atomic():
            counter, created = DailyCounter.objects.select_for_update().get_or_create(
                user_id=user_id,
                date_utc=date_utc,
                defaults={"accepted_count": 0},
            )

            updated = DailyCounter.objects.filter(
                pk=counter.pk,
                accepted_count__lt=daily_limit,
            ).update(accepted_count=F("accepted_count") + 1)

            if not updated:
                # Quota exhausted (or raced): do NOT change accepted_count.
                raise QuotaExceededError(daily_remaining=0)

            counter.refresh_from_db()
            daily_remaining = daily_limit - counter.accepted_count

            # Persist the reservation outcome for durable idempotency.
            ReservationRecord.objects.create(
                request_id=request_uuid,
                user_id=user_id,
                date_utc=date_utc,
                daily_remaining=daily_remaining,
            )
            return daily_remaining