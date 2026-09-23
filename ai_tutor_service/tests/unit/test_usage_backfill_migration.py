# verifies: FR-002-14
"""
T-057: tests for the unknown-usage backfill functions of migration
`0003_backfill_unknown_usage` (FR-002-14 — unknown != zero in LLM usage log).

Expected RED reason (T-058 not implemented yet): ModuleNotFoundError /
ImportError for
`ai_tutor_service.providers.migrations.0003_backfill_unknown_usage` —
the migration module does not exist yet. DO NOT create it here; that is T-058.

Backfill rule (plan.md "Поправка 2026-09-23", DATA-LOSS DECISION):

    forwards : input_tokens = 0 AND output_tokens = 0
               AND estimated_cost_usd = 0
               AND created_at < USAGE_UNKNOWN_BACKFILL_CUTOFF  ->  (NULL, NULL, NULL)
    backwards: every all-NULL usage row -> (0, 0, 0.000000)  (deliberately lossy:
               otherwise the reverse AlterField back to NOT NULL would fail)

Both directions are idempotent (a repeated run must not change rows that are
already converted).

The cutoff date in the migration must be the very same
`USAGE_UNKNOWN_BACKFILL_CUTOFF` object imported from
`ai_tutor_service.providers.models` — not a duplicated `date(2026, 6, 1)`
literal (identity test).

`LLMUsageLog.created_at` is `auto_now_add`: an explicit value passed to
`create()`/`save()` is ignored, so fixture rows are backdated through
queryset `.update()`, which bypasses `auto_now_add`.

No network access — Django ORM only; recorded fixtures only.
"""

# ── Lazy import of the not-yet-existing migration: the expected RED reason ─────
# (importing here would break collection for the whole file; each migration-
# dependent test fails on its own with ModuleNotFoundError instead.)

import ast
import importlib
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from django.apps import apps as django_apps

from ai_tutor_service.providers.models import (
    LLMUsageLog,
    USAGE_UNKNOWN_BACKFILL_CUTOFF,
)

MIGRATION_MODULE = (
    "ai_tutor_service.providers.migrations.0003_backfill_unknown_usage"
)

# created_at values around the historical cutoff (2026-06-01, UTC).
PRE_CUTOFF = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
CUTOFF_MOMENT = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)  # boundary: NOT < cutoff
POST_CUTOFF = datetime(2026, 9, 20, 8, 0, 0, tzinfo=timezone.utc)

ZERO_COST = Decimal("0.000000")
KNOWN_COST = Decimal("0.000375")
TINY_COST = Decimal("0.000001")


def _load_backfill():
    """Return ``(forwards, backwards, module)`` from migration 0003.

    Raises ModuleNotFoundError while T-058 is not done — the expected RED
    reason for every migration-dependent test below.
    """
    module = importlib.import_module(MIGRATION_MODULE)
    return module.forwards, module.backwards, module


def _make_row(*, created_at, input_tokens, output_tokens, cost):
    """Create an LLMUsageLog row and backdate it.

    ``created_at`` is auto_now_add and ignores an explicit value passed to
    ``create()``, so the historical timestamp is applied via ``.update()``.
    """
    row = LLMUsageLog.objects.create(
        request_id=uuid.uuid4(),
        user_id="student-backfill",
        model_id="test-model",
        operation=LLMUsageLog.Operation.GENERATE,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_usd=cost,
        config_version="1.0.0",
    )
    LLMUsageLog.objects.filter(pk=row.pk).update(created_at=created_at)
    row.refresh_from_db()
    return row


def _usage(row):
    """Snapshot of the three usage columns of a row."""
    return (row.input_tokens, row.output_tokens, row.estimated_cost_usd)


def _run_forwards():
    forwards, _, _ = _load_backfill()
    forwards(django_apps, None)


def _run_backwards():
    _, backwards, _ = _load_backfill()
    backwards(django_apps, None)


# =============================================================================
# forwards
# =============================================================================

@pytest.mark.django_db
class TestForwardsBackfill:
    """forwards: (0, 0, 0.00) before the cutoff becomes three NULLs."""

    def test_forwards_converts_pre_cutoff_zeros_to_null(self):
        """Pre-cutoff (0, 0, 0.000000) row -> (NULL, NULL, NULL)."""
        row = _make_row(
            created_at=PRE_CUTOFF,
            input_tokens=0,
            output_tokens=0,
            cost=ZERO_COST,
        )
        # Fixture sanity: the row really is (0, 0, 0.000000) before the run.
        assert _usage(row) == (0, 0, ZERO_COST)
        assert row.created_at == PRE_CUTOFF  # .update() bypassed auto_now_add

        _run_forwards()
        row.refresh_from_db()

        assert row.input_tokens is None, "pre-cutoff unknown usage must become NULL"
        assert row.output_tokens is None, "pre-cutoff unknown usage must become NULL"
        assert row.estimated_cost_usd is None, \
            "pre-cutoff unknown cost must become NULL, not 0"

    def test_forwards_leaves_post_cutoff_zeros_untouched(self):
        """(0, 0, 0.000000) at/after the cutoff is genuine data — untouched."""
        at_cutoff = _make_row(
            created_at=CUTOFF_MOMENT,
            input_tokens=0,
            output_tokens=0,
            cost=ZERO_COST,
        )
        recent = _make_row(
            created_at=POST_CUTOFF,
            input_tokens=0,
            output_tokens=0,
            cost=ZERO_COST,
        )

        _run_forwards()
        at_cutoff.refresh_from_db()
        recent.refresh_from_db()

        assert _usage(at_cutoff) == (0, 0, ZERO_COST), \
            "row exactly at the cutoff is not < cutoff and must stay (0, 0, 0)"
        assert _usage(recent) == (0, 0, ZERO_COST), \
            "post-cutoff zeros must not be converted to NULL"
        assert at_cutoff.created_at == CUTOFF_MOMENT
        assert recent.created_at == POST_CUTOFF

    def test_forwards_keeps_pre_cutoff_known_usage(self):
        """Known pre-cutoff usage is never touched — only the (0,0,0) footprint."""
        known = _make_row(
            created_at=PRE_CUTOFF,
            input_tokens=100,
            output_tokens=50,
            cost=KNOWN_COST,
        )
        nonzero_cost = _make_row(
            created_at=PRE_CUTOFF,
            input_tokens=0,
            output_tokens=0,
            cost=TINY_COST,
        )

        _run_forwards()
        known.refresh_from_db()
        nonzero_cost.refresh_from_db()

        assert _usage(known) == (100, 50, KNOWN_COST), \
            "pre-cutoff known usage (100, 50, 0.000375) must stay untouched"
        assert _usage(nonzero_cost) == (0, 0, TINY_COST), \
            "pre-cutoff (0, 0, 0.000001) has non-zero cost and must stay untouched"

    def test_forwards_is_idempotent(self):
        """A second forwards run changes nothing (already-converted rows included)."""
        pre_zeros = _make_row(
            created_at=PRE_CUTOFF, input_tokens=0, output_tokens=0, cost=ZERO_COST
        )
        pre_known = _make_row(
            created_at=PRE_CUTOFF, input_tokens=100, output_tokens=50, cost=KNOWN_COST
        )
        pre_null = _make_row(
            created_at=PRE_CUTOFF, input_tokens=None, output_tokens=None, cost=None
        )
        post_zeros = _make_row(
            created_at=POST_CUTOFF, input_tokens=0, output_tokens=0, cost=ZERO_COST
        )

        _run_forwards()
        first = {row.pk: _usage(row) for row in LLMUsageLog.objects.all()}
        # First run must actually have converted the pre-cutoff zeros,
        # otherwise the idempotency check below would be vacuous.
        assert first[pre_zeros.pk] == (None, None, None)
        assert first[pre_known.pk] == (100, 50, KNOWN_COST)
        assert first[pre_null.pk] == (None, None, None)
        assert first[post_zeros.pk] == (0, 0, ZERO_COST)

        _run_forwards()
        second = {row.pk: _usage(row) for row in LLMUsageLog.objects.all()}

        assert second == first, \
            "repeated forwards run must not change already-converted rows"


# =============================================================================
# backwards
# =============================================================================

@pytest.mark.django_db
class TestBackwardsBackfill:
    """backwards: all-NULL usage rows get zeros back (deliberately lossy)."""

    def test_backwards_returns_zeros_to_all_null_rows(self):
        """Every NULL row becomes (0, 0, 0.000000); known rows stay as-is."""
        null_pre = _make_row(
            created_at=PRE_CUTOFF, input_tokens=None, output_tokens=None, cost=None
        )
        null_post = _make_row(
            created_at=POST_CUTOFF, input_tokens=None, output_tokens=None, cost=None
        )
        known = _make_row(
            created_at=PRE_CUTOFF, input_tokens=100, output_tokens=50, cost=KNOWN_COST
        )

        _run_backwards()
        null_pre.refresh_from_db()
        null_post.refresh_from_db()
        known.refresh_from_db()

        for row in (null_pre, null_post):
            assert row.input_tokens == 0, \
                "backwards must write 0 to NULL input_tokens (lossy by design)"
            assert row.output_tokens == 0, \
                "backwards must write 0 to NULL output_tokens (lossy by design)"
            assert row.estimated_cost_usd == ZERO_COST, \
                "backwards must write 0.000000 to NULL cost (lossy by design)"

        assert _usage(known) == (100, 50, KNOWN_COST), \
            "backwards must not touch rows with known usage"


# =============================================================================
# cutoff constant identity
# =============================================================================

@pytest.mark.django_db
class TestMigrationCutoffConstant:
    """The migration date must be the models constant, not a literal duplicate."""

    def test_migration_cutoff_is_models_constant(self):
        """Migration 0003 uses USAGE_UNKNOWN_BACKFILL_CUTOFF from providers.models.

        Expected RED while T-058 is pending:
        ModuleNotFoundError for `0003_backfill_unknown_usage`.
        """
        _, _, module = _load_backfill()
        source = Path(module.__file__).read_text(encoding="utf-8")

        imported = getattr(module, "USAGE_UNKNOWN_BACKFILL_CUTOFF", None)
        if imported is not None:
            # Imported at module level: must be the *same object* (identity),
            # not an equal-but-re-created `date(2026, 6, 1)` literal.
            assert imported is USAGE_UNKNOWN_BACKFILL_CUTOFF, (
                "migration must import USAGE_UNKNOWN_BACKFILL_CUTOFF from "
                "ai_tutor_service.providers.models (identity), not re-create "
                "the date literal"
            )
        else:
            # Imported lazily inside forwards/backwards — still must reference
            # the named constant from models, never a local date literal.
            assert "USAGE_UNKNOWN_BACKFILL_CUTOFF" in source, (
                "migration must reference USAGE_UNKNOWN_BACKFILL_CUTOFF"
            )
            assert "ai_tutor_service.providers.models" in source, (
                "migration must import the constant from providers.models"
            )

        # No duplicated `date(...)`/`datetime(...)` cutoff literal in code
        # (docstrings/comments are not executable code, so they don't count).
        tree = ast.parse(source)
        duplicate_lines = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name):
                continue
            if node.func.id not in ("date", "datetime"):
                continue
            try:
                args = [ast.literal_eval(arg) for arg in node.args[:3]]
            except (ValueError, TypeError):
                continue
            if args == [2026, 6, 1]:
                duplicate_lines.append(node.lineno)
        assert not duplicate_lines, (
            f"cutoff literal date(2026, 6, 1) duplicated at line(s) "
            f"{duplicate_lines}: import USAGE_UNKNOWN_BACKFILL_CUTOFF instead"
        )


# =============================================================================
# created_at / auto_now_add mechanics (fixture technique used above)
# =============================================================================

@pytest.mark.django_db
class TestCreatedAtAutoNowAdd:
    """auto_now_add ignores an explicit created_at on create(); .update() wins."""

    def test_explicit_created_at_ignored_on_create_but_set_via_update(self):
        """create(created_at=...) is overridden by auto_now_add; .update() is not."""
        explicit = PRE_CUTOFF
        row = LLMUsageLog.objects.create(
            request_id=uuid.uuid4(),
            user_id="student-backfill",
            model_id="test-model",
            operation=LLMUsageLog.Operation.GENERATE,
            input_tokens=0,
            output_tokens=0,
            estimated_cost_usd=ZERO_COST,
            config_version="1.0.0",
            created_at=explicit,  # auto_now_add must override this
        )
        row.refresh_from_db()
        assert row.created_at != explicit, (
            "auto_now_add must override an explicit created_at passed to "
            "create() — that is why backfill fixtures are backdated via .update()"
        )

        LLMUsageLog.objects.filter(pk=row.pk).update(created_at=explicit)
        row.refresh_from_db()
        assert row.created_at == explicit, (
            ".update() bypasses auto_now_add and must backdate the fixture row"
        )
