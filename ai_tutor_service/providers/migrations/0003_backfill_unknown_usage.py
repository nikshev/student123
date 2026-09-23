# impl: FR-002-14
"""
Django data migration 0003: backfill unknown LLM usage (FR-002-14).

Context (specs/002-ai-tutor/plan.md, "Поправка 2026-09-23"): the old
``record_usage`` branch wrote ``0 / 0 / 0.000000`` when the provider usage
was unknown, mixing "unknown cost" with "genuine zero cost" and silently
understating SC-006. Migration 0002 made the three columns nullable
(NULL = unknown); this migration converts the old unknown footprint.

Backfill rule (conservative, all four conditions AND-ed):

    input_tokens = 0 AND output_tokens = 0
    AND estimated_cost_usd = 0
    AND created_at < USAGE_UNKNOWN_BACKFILL_CUTOFF  ->  (NULL, NULL, NULL)

The cutoff is imported from ``ai_tutor_service.providers.models`` — never
re-declared as a date literal here. Rows created exactly at the cutoff are
NOT converted (strict ``<``). Genuine post-cutoff zeros and any row with a
non-zero token count or cost are left untouched.

``backwards`` is deliberately LOSSY: it writes ``0 / 0 / 0.000000`` to every
row that has NULL in any of the three columns (otherwise a reverse
``AlterField`` back to NOT NULL would fail). Converted rows cannot be told
apart from genuine zeros afterwards, so forwards-then-backwards does not
round-trip. This asymmetry is intentional and documented here.

Both directions are idempotent: a repeated run matches zero rows because
already-converted rows no longer satisfy the predicate.

No raw SQL — Django ORM only, historical model via ``apps.get_model``.
"""

from datetime import datetime, time, timezone
from decimal import Decimal

from django.db import migrations
from django.db.models import Q

from ai_tutor_service.providers.models import USAGE_UNKNOWN_BACKFILL_CUTOFF


def _cutoff_moment():
    """Cutoff as an aware UTC midnight datetime for ``created_at`` compare."""
    return datetime.combine(
        USAGE_UNKNOWN_BACKFILL_CUTOFF, time.min, tzinfo=timezone.utc
    )


def forwards(apps, schema_editor):
    """Convert the pre-cutoff (0, 0, 0.000000) unknown footprint to NULLs."""
    LLMUsageLog = apps.get_model("providers", "LLMUsageLog")
    LLMUsageLog.objects.filter(
        input_tokens=0,
        output_tokens=0,
        estimated_cost_usd=0,
        created_at__lt=_cutoff_moment(),
    ).update(input_tokens=None, output_tokens=None, estimated_cost_usd=None)


def backwards(apps, schema_editor):
    """Write zeros back to every row with NULL in any usage column.

    Deliberately lossy (see module docstring): unknown rows become
    indistinguishable from genuine zeros; required so the columns can go
    back to NOT NULL.
    """
    LLMUsageLog = apps.get_model("providers", "LLMUsageLog")
    LLMUsageLog.objects.filter(
        Q(input_tokens__isnull=True)
        | Q(output_tokens__isnull=True)
        | Q(estimated_cost_usd__isnull=True)
    ).update(
        input_tokens=0,
        output_tokens=0,
        estimated_cost_usd=Decimal("0.000000"),
    )


class Migration(migrations.Migration):

    dependencies = [
        ("providers", "0002_usage_nullable"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
