# impl: FR-002-09, FR-002-14
"""
Usage log and cost tracking for AI Tutor Service.

Primary append-only log of LLM operations (generate/guard/off_topic/gate).
Each record contains: request_id, user_id, model_id, operation (generate/guard/off_topic/gate),
input_tokens, output_tokens, estimated_cost_usd, config_version, created_at.

Cost calculation uses rates from the SAME YAML version as config_version.
Different config versions are grouped and aggregated separately.

Usage columns follow the unknown ≠ zero invariant (FR-002-14, data-model §7):

- fully unknown usage (both token counts None) is written as
  (NULL, NULL, NULL) — never (0, 0, Decimal("0"));
- partial usage keeps the known counter as-is, stores NULL for the unknown
  counter, and computes estimated_cost_usd from the known part (the unknown
  part counts as 0 in the formula) so the cost stays NOT NULL;
- genuine zero usage (0, 0) is a known fact written as (0, 0, 0.000000);
- the state "both counters known, cost NULL" never occurs: cost is a
  deterministic function of the counters.
"""

import logging
from decimal import Decimal
from pathlib import Path
from typing import Optional

from django.db import OperationalError, models

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
        None means unknown and is stored as NULL (never coerced to 0).
    output_tokens : int | None
        Number of output tokens produced by the operation.
        None means unknown and is stored as NULL (never coerced to 0).
    model_id : str
        Model identifier used for the operation.
    config_version : str
        Version of the tutor configuration (from YAML) that governs pricing.

    Notes
    -----
    - unknown ≠ zero (FR-002-14): a counter the provider did not report is
      stored as NULL, not 0; a reported 0 is stored as 0.
    - Fully unknown usage (input_tokens=None, output_tokens=None) is written
      as (NULL, NULL, NULL): the operation's cost is unknown — not zero.
    - Partial usage (exactly one counter None): the known counter is stored
      as-is, the unknown counter is NULL, and estimated_cost_usd is computed
      from the known part (unknown part = 0 in the formula) and stays NOT
      NULL. "Both counters known, cost NULL" is forbidden and never produced.
    - Genuine zero (0, 0) yields estimated_cost_usd = 0.000000 — a known
      fact and a legitimate aggregation addend.
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

    # unknown ≠ zero (FR-002-14): NULL means the provider did not report the
    # value; 0 means a known zero. Never coerce None to 0 on write — doing so
    # silently mixes unknown with zero and understates SC-006.
    if input_tokens is None and output_tokens is None:
        # Fully unknown usage: all three columns NULL (unknown cost is not 0).
        estimated_cost = None
    else:
        # Complete or partial usage: cost is a deterministic function of the
        # counters. For partial usage the unknown part counts as 0 in the
        # formula, so estimated_cost_usd is computed from the known part and
        # stays NOT NULL (state "both known, cost NULL" never happens here).
        estimated_cost = _calculate_cost(
            input_tokens if input_tokens is not None else 0,
            output_tokens if output_tokens is not None else 0,
            config,
        )

    # Best-effort append: a DB failure in the usage log must NEVER break the
    # main tutoring path (FR-002-09 / T-048). Only OperationalError is swallowed
    # here — LLMError and other exceptions are NOT silently eaten, because they
    # would indicate a real bug or a missing dependency, not a transient DB hiccup.
    try:
        LLMUsageLog.objects.create(
            request_id=request_id,
            user_id=user_id,
            operation=operation,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
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
      - Gate operations (not part of learner cost);
      - Unknown records (estimated_cost_usd IS NULL — unknown ≠ zero,
        FR-002-14 / data-model §7).

    The unknown filter runs at the ORM query level on the cost column itself —
    never as a Python-loop heuristic over zero token counts, which would silently
    drop genuine zero (0, 0, 0.000000) or try to sum NULL and distort SC-006.
    Genuine zero is a known fact and IS included (contributes exactly 0.00).
    Skipped unknown records are counted separately by count_unknown_usage().

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
    # Filter at the query level: exclude gate, keep only known costs
    # (estimated_cost_usd IS NOT NULL). NULL rows never reach summation.
    total = (
        LLMUsageLog.objects.filter(
            user_id=user_id,
            config_version=config_version,
            estimated_cost_usd__isnull=False,
        )
        .exclude(operation=LLMUsageLog.Operation.GATE)
        .aggregate(total=models.Sum("estimated_cost_usd"))["total"]
    )

    # Empty qualifying set → Decimal("0.0"); Sum always returns Decimal here.
    return total if total is not None else Decimal("0.0")


def count_unknown_usage(user_id: str, config_version: str) -> int:
    """
    Count learner records whose estimated_cost_usd is unknown (IS NULL).

    Unknown ≠ zero (FR-002-14, data-model §7 «Наслідки для агрегації»):
    records skipped by aggregate_monthly_cost need a visible denominator so
    SC-006 is not silently understated (constitution V — metrics from events).

    Signature is aligned with aggregate_monthly_cost: same (user_id,
    config_version) parameters, same config-version isolation.

    Parameters
    ----------
    user_id : str
        User identifier.
    config_version : str
        Configuration version (e.g., "1.0.0").

    Returns
    -------
    int
        Number of learner records with estimated_cost_usd IS NULL,
        EXCLUDING operation=gate (gate is outside learner metrics).
    """
    return (
        LLMUsageLog.objects.filter(
            user_id=user_id,
            config_version=config_version,
            estimated_cost_usd__isnull=True,
        )
        .exclude(operation=LLMUsageLog.Operation.GATE)
        .count()
    )
