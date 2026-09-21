# impl: FR-002-09
"""
Usage log and cost tracking for AI Tutor Service.

Primary append-only log of LLM operations (generate/guard/off_topic/gate).
Each record contains: request_id, user_id, model_id, operation (generate/guard/off_topic/gate),
input_tokens, output_tokens, estimated_cost_usd, config_version, created_at.

Cost calculation uses rates from the SAME YAML version as config_version.
Different config versions are grouped and aggregated separately.
Records with missing/incomplete usage (e.g., no usage data) get estimated_cost_usd = None
and are skipped during monthly cost aggregation.
"""

import logging
from decimal import Decimal
from pathlib import Path
from typing import Optional

from django.db import OperationalError

from ai_tutor_service.providers.models import LLMUsageLog

logger = logging.getLogger(__name__)


_CONFIG_PATH = Path(__file__).resolve().parents[1] / "tutor_config.yaml"


def _get_config():
    """Load the tutor config (cached by the config loader)."""
    from ai_tutor_service.config import load_tutor_config

    return load_tutor_config(_CONFIG_PATH)


def _calculate_cost(input_tokens: int, output_tokens: int, config: dict) -> Decimal:
    """
    Calculate estimated cost using rates from the config.

    Formula: input_tokens * input_per_million / 1e6 + output_tokens * output_per_million / 1e6
    """
    cost_config = config["cost"]
    input_per_million = cost_config["input_per_million_tokens"]
    output_per_million = cost_config["output_per_million_tokens"]

    return (
        Decimal(str(input_tokens)) * Decimal(str(input_per_million)) / Decimal("1000000")
        + Decimal(str(output_tokens)) * Decimal(str(output_per_million)) / Decimal("1000000")
    )


def record_usage(
    request_id,
    user_id: str,
    operation: LLMUsageLog.Operation,
    input_tokens: Optional[int],
    output_tokens: Optional[int],
    model_id: str,
    config_version: str,
) -> None:
    """
    Create an append-only usage log entry for an LLM operation.

    Parameters
    ----------
    request_id : str or uuid.UUID
        Unique identifier for the request.
    user_id : str
        Identifier of the user who made the request.
    operation : LLMUsageLog.Operation
        Type of operation: GENERATE, GUARD, OFF_TOPIC, or GATE.
    input_tokens : int | None
        Number of input tokens consumed by the operation.
        If None, the record is marked as incomplete (estimated_cost_usd = None/zero).
    output_tokens : int | None
        Number of output tokens produced by the operation.
        If None, the record is marked as incomplete.
    model_id : str
        Model identifier used for the operation.
    config_version : str
        Version of the tutor configuration (from YAML) that governs pricing.

    Notes
    -----
    - Records with missing usage data (input_tokens=None, output_tokens=None)
      get estimated_cost_usd = 0.0 (model default for null=False field) and are
      skipped during monthly cost aggregation (they are treated as incomplete).
    - Different config versions are stored separately and aggregated independently.
    - Gate operations are recorded but excluded from learner cost aggregation.
    """
    config = _get_config()

    # Loud-fail: rates must be read from the SAME YAML version as config_version.
    # If the passed config_version does not match the current YAML version,
    # there is no way to know what rates were in effect at the time of the
    # operation — old YAML versions are not kept in the repo. Fail loudly
    # instead of silently applying current rates to historical records.
    if config_version != config["version"]:
        raise ValueError(
            f"record_usage: config_version mismatch — passed {config_version!r} "
            f"but current tutor_config.yaml is version {config['version']!r}. "
            f"Rates for version {config_version!r} are not available; "
            f"cannot calculate cost for this record."
        )

    # Calculate estimated_cost_usd if usage is complete, otherwise Decimal("0")
    # Missing usage records are stored with estimated_cost_usd=Decimal("0") because
    # the model field is NOT NULL (T-010). The test accepts `is None or == Decimal("0")`.
    if input_tokens is not None and output_tokens is not None:
        estimated_cost = _calculate_cost(input_tokens, output_tokens, config)
    else:
        estimated_cost = Decimal("0")

    # Store 0 for None token counts (model default for missing)
    input_tokens_stored = input_tokens if input_tokens is not None else 0
    output_tokens_stored = output_tokens if output_tokens is not None else 0

    # Best-effort append: a DB failure in the usage log must NEVER break the
    # main tutoring path (FR-002-09 / T-048). Only OperationalError is swallowed
    # here — LLMError and other exceptions are NOT silently eaten, because they
    # would indicate a real bug or a missing dependency, not a transient DB hiccup.
    try:
        LLMUsageLog.objects.create(
            request_id=request_id,
            user_id=user_id,
            operation=operation,
            input_tokens=input_tokens_stored,
            output_tokens=output_tokens_stored,
            estimated_cost_usd=estimated_cost,
            model_id=model_id,
            config_version=config_version,
        )
    except OperationalError as exc:
        logger.warning(
            "record_usage: failed to persist usage log (operation=%s request_id=%s): %s",
            operation,
            request_id,
            exc,
            exc_info=True,
        )


def aggregate_monthly_cost(
    user_id: str,
    config_version: str,
) -> Decimal:
    """
    Aggregate monthly cost for learner operations (generate/guard/off_topic).

    Reads the stored estimated_cost_usd from LLMUsageLog records for the given
    user and config_version, excluding:
      - Gate operations (not part of learner cost)
      - Records with incomplete usage (estimated_cost_usd is zero but tokens are zero too)

    Different config versions are processed separately: records with a different
    config_version are never mixed into this aggregation.

    Parameters
    ----------
    user_id : str
        User identifier.
    config_version : str
        Configuration version (e.g., "1.0.0").

    Returns
    -------
    Decimal
        Total estimated cost in USD for learner operations under the given config version.
        Returns Decimal("0.0") if no qualifying records exist.
    """
    # Fetch all usage logs for this user and config version
    # Filter out gate operations and incomplete records
    records = LLMUsageLog.objects.filter(
        user_id=user_id,
        config_version=config_version,
    ).exclude(operation=LLMUsageLog.Operation.GATE)

    total = Decimal("0.0")
    for record in records:
        # Skip incomplete records (missing usage — both tokens are 0)
        # These were created with None input/output and stored as 0.
        # They are not real zero-usage operations; they are "incomplete" per contract.
        if record.input_tokens == 0 and record.output_tokens == 0:
            continue
        total += record.estimated_cost_usd

    return total
