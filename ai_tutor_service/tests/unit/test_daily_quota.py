# verifies: FR-002-11
"""
Tests for the daily quota reservation (FR-002-11, data-model.md §5).

The DailyQuota service (ai_tutor_service.limits.service) does not exist yet
(T-022 implements it). Importing it at module level raises ModuleNotFoundError,
which is the expected reason these tests are red right now: written first,
the service code will turn them green in T-022.

Contract for T-022 (the implementation must satisfy exactly this interface):

    DailyQuota.reserve(user_id: str, request_id: str, now: datetime | None = None) -> int

    - Reserves one slot of the daily quota atomically on the UTC date of `now`
      (now defaults to timezone.now()). date_utc = now.date().
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

Tests never touch the network (autouse _disable_network in conftest.py) and
use the existing models from ai_tutor_service.limits.models.
"""

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ai_tutor_service.config import load_tutor_config
from ai_tutor_service.limits.models import DailyCounter
from ai_tutor_service.limits.service import DailyQuota, QuotaExceededError

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = REPO_ROOT / "ai_tutor_service" / "tutor_config.yaml"

DAY_1 = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
DAY_2 = DAY_1 + timedelta(days=1)


def _config():
    return load_tutor_config(CONFIG_PATH)


def _counter(user_id, day):
    return DailyCounter.objects.get(user_id=user_id, date_utc=day.date())


@pytest.mark.django_db
def test_reservations_decrement_until_limit_then_429():
    """daily_limit reservations succeed; the next one raises with remaining=0."""
    daily_limit = _config()["daily_limit"]
    user_id = "quota-user-1"

    remaining = DailyQuota.reserve(
        user_id=user_id, request_id=str(uuid.uuid4()), now=DAY_1
    )
    assert remaining == daily_limit - 1
    for i in range(2, daily_limit):
        remaining = DailyQuota.reserve(
            user_id=user_id, request_id=str(uuid.uuid4()), now=DAY_1
        )
        assert remaining == daily_limit - i
    remaining = DailyQuota.reserve(
        user_id=user_id, request_id=str(uuid.uuid4()), now=DAY_1
    )
    assert remaining == 0

    with pytest.raises(QuotaExceededError) as exc_info:
        DailyQuota.reserve(user_id=user_id, request_id=str(uuid.uuid4()), now=DAY_1)
    assert exc_info.value.daily_remaining == 0
    assert _counter(user_id, DAY_1).accepted_count == daily_limit


@pytest.mark.django_db
def test_rejected_reservation_does_not_increase_counter():
    """The (daily_limit+1)-th call raises and leaves accepted_count == daily_limit."""
    daily_limit = _config()["daily_limit"]
    user_id = "quota-user-2"

    for _ in range(daily_limit):
        DailyQuota.reserve(user_id=user_id, request_id=str(uuid.uuid4()), now=DAY_1)

    with pytest.raises(QuotaExceededError):
        DailyQuota.reserve(user_id=user_id, request_id=str(uuid.uuid4()), now=DAY_1)
    with pytest.raises(QuotaExceededError):
        DailyQuota.reserve(user_id=user_id, request_id=str(uuid.uuid4()), now=DAY_1)

    counter = _counter(user_id, DAY_1)
    assert counter.accepted_count == daily_limit
    assert counter.accepted_count <= daily_limit


@pytest.mark.django_db
def test_same_request_id_does_not_increment_twice():
    """Repeating the same request_id returns the previous result, one increment only."""
    user_id = "quota-user-idem"
    request_id = str(uuid.uuid4())

    first = DailyQuota.reserve(user_id=user_id, request_id=request_id, now=DAY_1)
    second = DailyQuota.reserve(user_id=user_id, request_id=request_id, now=DAY_1)

    assert first == second
    assert _counter(user_id, DAY_1).accepted_count == 1


@pytest.mark.django_db
def test_utc_rollover_starts_fresh_counter():
    """Same user on the next UTC date gets a fresh daily_limit."""
    daily_limit = _config()["daily_limit"]
    user_id = "quota-user-rollover"

    for _ in range(daily_limit):
        DailyQuota.reserve(user_id=user_id, request_id=str(uuid.uuid4()), now=DAY_1)
    with pytest.raises(QuotaExceededError):
        DailyQuota.reserve(user_id=user_id, request_id=str(uuid.uuid4()), now=DAY_1)

    remaining = DailyQuota.reserve(
        user_id=user_id, request_id=str(uuid.uuid4()), now=DAY_2
    )
    assert remaining == daily_limit - 1
    assert _counter(user_id, DAY_2).accepted_count == 1
    assert _counter(user_id, DAY_1).accepted_count == daily_limit


@pytest.mark.django_db
def test_quotas_are_isolated_between_users():
    """One user exhausting the quota does not affect another user."""
    daily_limit = _config()["daily_limit"]

    for _ in range(daily_limit):
        DailyQuota.reserve(user_id="quota-user-a", request_id=str(uuid.uuid4()), now=DAY_1)

    remaining_b = DailyQuota.reserve(
        user_id="quota-user-b", request_id=str(uuid.uuid4()), now=DAY_1
    )
    assert remaining_b == daily_limit - 1


@pytest.mark.django_db
def test_request_id_must_be_valid_uuid():
    """Non-UUID request_id is rejected (service-level guard)."""
    with pytest.raises(ValueError):
        DailyQuota.reserve(user_id="quota-user-bad", request_id="not-a-uuid", now=DAY_1)


@pytest.mark.django_db
def test_burst_of_attempts_never_exceeds_daily_limit():
    """3x daily_limit attempts yield exactly daily_limit successes; counter capped.

    Deterministic proxy for the concurrency guard: the conditional UPDATE
    (accepted_count__lt=daily_limit) is what caps the counter. A true
    multi-threaded race test is not viable on the shared-cache in-memory
    SQLite test database (SQLITE_LOCKED on concurrent writers), so the
    invariant is pinned by exhausting the quota 3x over.
    """
    daily_limit = _config()["daily_limit"]
    user_id = "quota-user-burst"

    successes = 0
    exceeded = 0
    for _ in range(daily_limit * 3):
        try:
            DailyQuota.reserve(user_id=user_id, request_id=str(uuid.uuid4()), now=DAY_1)
            successes += 1
        except QuotaExceededError:
            exceeded += 1

    assert successes == daily_limit
    assert exceeded == daily_limit * 2
    assert _counter(user_id, DAY_1).accepted_count == daily_limit
