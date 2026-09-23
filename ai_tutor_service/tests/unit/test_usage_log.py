# verifies: FR-002-09, FR-002-14
"""
Usage log and cost tracking tests for AI Tutor Service (T-047).

Contract for T-048 implementation in ai_tutor_service/providers/usage.py:

1. record_usage(): Append-only records for each LLM operation (generate/guard/off_topic/gate).
   Each record contains: request_id, user_id, model_id, input_tokens, output_tokens,
   config_version (from YAML), created_at.
   Repeated calls for same request_id accumulate (do not overwrite).

2. estimated_cost_usd: = (input_tokens × input_per_million/1e6 +
   output_tokens × output_per_million/1e6) using rates from the SAME YAML version as config_version.
   Rates are loaded from tutor_config.yaml cost section (currency USD, input_per_million_tokens,
   output_per_million_tokens). Gate operations are recorded separately and NOT included in
   learner cost aggregation.

3. Missing usage handling (FR-002-14, NULL semantics): When an operation completes
   WITHOUT usage data (both token counts None), the record is written with all three
   columns NULL — never coerced to 0. Aggregation skips records with
   estimated_cost_usd IS NULL (filtered by the cost column, NOT by zero token
   counts); genuine zero (0, 0, 0.000000) is a known fact and IS included
   (contributes exactly 0.00 to the sum). Skipped unknown records are counted
   separately by count_unknown_usage() so SC-006 has a visible denominator.

4. Config version isolation: Records with different config_version values are grouped
   and filtered separately during aggregation. Same config_version is required for price matching.

5. aggregate_monthly_cost(config_version): Read-only aggregation returning total USD cost
   for learner operations (generate/guard/off_topic) excluding gate records. Result
   must stay under monthly budget threshold (SC-006: < $0.20 per learner).

6. LLMUsageLog model (T-010): Append-only, no update/delete, all fields match spec.
   Fields: request_id (UUID, indexed), user_id (str), model_id (str), operation
   (enum: generate/guard/off_topic/gate), input_tokens (>=0), output_tokens (>=0),
   estimated_cost_usd (Decimal), config_version (str), created_at (auto_now_add).

All tests run without external network calls; fixtures provide realistic token counts.
"""

# T-047 expects RED at collection time due to missing ai_tutor_service.providers.usage module
# This is the expected behavior: ModuleNotFoundError when importing from usage.py
from ai_tutor_service.providers.usage import record_usage, aggregate_monthly_cost  # noqa: F401

import inspect
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from ai_tutor_service.config import load_tutor_config
from ai_tutor_service.providers.models import LLMUsageLog

# Load config for testing (same pattern as test_tutor_config.py, test_relevance_policy.py)
CONFIG_PATH = Path(__file__).resolve().parents[3] / "ai_tutor_service" / "tutor_config.yaml"
CONFIG = load_tutor_config(CONFIG_PATH)


# =============================================================================
# Test Case 1: Append-only record for each operation type
# =============================================================================

@pytest.mark.django_db
class TestUsageRecordAppendOnly:
    """
    Test Case 1: Append-only запис кожного generate/guard/off_topic/gate.

    Each operation should create a new LLMUsageLog entry.
    Duplicate request_id entries should accumulate (count records grow).
    Model enforces append-only via primary key UUID (no natural PK collision).
    """

    def test_record_generate_operation(self):
        """
        Generate operation creates a usage log entry with correct operation type.
        """
        request_id = uuid.uuid4()
        user_id = "student-test-001"
        model_id = CONFIG["model_id"]
        config_version = CONFIG["version"]

        record_usage(
            request_id=request_id,
            user_id=user_id,
            operation=LLMUsageLog.Operation.GENERATE,
            input_tokens=100,
            output_tokens=50,
            model_id=model_id,
            config_version=config_version,
        )

        records = LLMUsageLog.objects.filter(request_id=request_id)
        assert records.count() == 1
        record = records.first()
        assert record.operation == LLMUsageLog.Operation.GENERATE
        assert record.input_tokens == 100
        assert record.output_tokens == 50
        assert record.user_id == user_id
        assert record.model_id == model_id
        assert record.config_version == config_version

    def test_record_guard_operation(self):
        """
        Guard operation creates a usage log entry with correct operation type.
        """
        request_id = uuid.uuid4()
        user_id = "student-test-002"
        model_id = CONFIG["model_id"]
        config_version = CONFIG["version"]

        record_usage(
            request_id=request_id,
            user_id=user_id,
            operation=LLMUsageLog.Operation.GUARD,
            input_tokens=150,
            output_tokens=30,
            model_id=model_id,
            config_version=config_version,
        )

        records = LLMUsageLog.objects.filter(request_id=request_id)
        assert records.count() == 1
        record = records.first()
        assert record.operation == LLMUsageLog.Operation.GUARD

    def test_record_off_topic_operation(self):
        """
        Off_topic operation creates a usage log entry with correct operation type.
        """
        request_id = uuid.uuid4()
        user_id = "student-test-003"
        model_id = CONFIG["model_id"]
        config_version = CONFIG["version"]

        record_usage(
            request_id=request_id,
            user_id=user_id,
            operation=LLMUsageLog.Operation.OFF_TOPIC,
            input_tokens=80,
            output_tokens=20,
            model_id=model_id,
            config_version=config_version,
        )

        records = LLMUsageLog.objects.filter(request_id=request_id)
        assert records.count() == 1
        record = records.first()
        assert record.operation == LLMUsageLog.Operation.OFF_TOPIC

    def test_record_gate_operation(self):
        """
        Gate operation creates a usage log entry with correct operation type.
        Gate operations are recorded but excluded from learner cost aggregation.
        """
        request_id = uuid.uuid4()
        user_id = "student-test-004"
        model_id = CONFIG["guard_model_id"]
        config_version = CONFIG["version"]

        record_usage(
            request_id=request_id,
            user_id=user_id,
            operation=LLMUsageLog.Operation.GATE,
            input_tokens=200,
            output_tokens=100,
            model_id=model_id,
            config_version=config_version,
        )

        records = LLMUsageLog.objects.filter(request_id=request_id)
        assert records.count() == 1
        record = records.first()
        assert record.operation == LLMUsageLog.Operation.GATE


# =============================================================================
# Test Case 2: estimated_cost_usd calculation from token counts and YAML rates
# =============================================================================

@pytest.mark.django_db
class TestEstimatedCostCalculation:
    """
    Test Case 2: estimated_cost_usd = (input_tokens × input_per_million/1e6 +
                        output_tokens × output_per_million/1e6) using rates from
    the SAME YAML version as config_version.

    Rates from tutor_config.yaml:
    - input_per_million_tokens: 0.25 USD
    - output_per_million_tokens: 1.25 USD
    """

    def test_estimated_cost_matches_yaml_rates(self):
        """
        Cost calculation uses exact rates from tutor_config.yaml version.
        Formula: (input_tokens * 0.25/1e6) + (output_tokens * 1.25/1e6)
        """
        request_id = uuid.uuid4()
        user_id = "student-cost-test"
        model_id = CONFIG["model_id"]
        config_version = CONFIG["version"]

        # Realistic token counts: 500 input, 1500 output
        input_tokens = 500
        output_tokens = 1500

        # Expected cost: (500 * 0.25/1e6) + (1500 * 1.25/1e6)
        # = 0.000125 + 0.001875 = 0.002 USD
        expected_cost = Decimal("0.002")

        record_usage(
            request_id=request_id,
            user_id=user_id,
            operation=LLMUsageLog.Operation.GENERATE,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model_id=model_id,
            config_version=config_version,
        )

        record = LLMUsageLog.objects.get(request_id=request_id)
        assert record.estimated_cost_usd == expected_cost

    def test_estimated_cost_uses_config_version_rates(self):
        """
        Cost uses rates from the SAME config_version that is recorded.
        Different config versions have different rates and must be isolated.
        """
        request_id = uuid.uuid4()
        user_id = "student-rate-test"
        model_id = CONFIG["model_id"]

        # Use current config version
        config_version = CONFIG["version"]

        # Small token counts
        input_tokens = 100
        output_tokens = 100

        record_usage(
            request_id=request_id,
            user_id=user_id,
            operation=LLMUsageLog.Operation.GENERATE,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model_id=model_id,
            config_version=config_version,
        )

        record = LLMUsageLog.objects.get(request_id=request_id)
        # Expected: (100 * 0.25/1e6) + (100 * 1.25/1e6) = 0.000025 + 0.000125 = 0.000150
        expected_cost = Decimal("0.000150")
        assert record.estimated_cost_usd == expected_cost

    def test_zero_tokens_yields_zero_cost(self):
        """
        Genuine zero (0, 0) yields zero cost and is a KNOWN fact: none of the
        three usage columns may be NULL (FR-002-14 — unknown ≠ zero).
        """
        request_id = uuid.uuid4()
        user_id = "student-zero-cost"
        model_id = CONFIG["model_id"]
        config_version = CONFIG["version"]

        record_usage(
            request_id=request_id,
            user_id=user_id,
            operation=LLMUsageLog.Operation.GENERATE,
            input_tokens=0,
            output_tokens=0,
            model_id=model_id,
            config_version=config_version,
        )

        record = LLMUsageLog.objects.get(request_id=request_id)
        assert record.input_tokens is not None, \
            "genuine zero input_tokens must NOT be NULL (it is a known 0)"
        assert record.output_tokens is not None, \
            "genuine zero output_tokens must NOT be NULL (it is a known 0)"
        assert record.estimated_cost_usd is not None, \
            "genuine zero estimated_cost_usd must NOT be NULL (it is a known 0)"
        assert record.estimated_cost_usd == Decimal("0")


# =============================================================================
# Test Case 3: Gate operations are separate from learner cost
# =============================================================================

@pytest.mark.django_db
class TestGateSeparateFromLearnerCost:
    """
    Test Case 3: Gate operations are recorded but NOT included in learner cost.

    aggregate_monthly_cost() must filter out operation="gate" records.
    Only generate/guard/off_topic contribute to SC-006 cost tracking.
    """

    def test_gate_excluded_from_monthly_cost(self):
        """
        Gate operations must not contribute to monthly cost aggregation.
        """
        user_id = "student-gate-test"
        config_version = CONFIG["version"]

        # Create a gate record with significant tokens
        gate_request_id = uuid.uuid4()
        record_usage(
            request_id=gate_request_id,
            user_id=user_id,
            operation=LLMUsageLog.Operation.GATE,
            input_tokens=10000,
            output_tokens=5000,
            model_id=CONFIG["guard_model_id"],
            config_version=config_version,
        )

        # Create a generate record
        gen_request_id = uuid.uuid4()
        record_usage(
            request_id=gen_request_id,
            user_id=user_id,
            operation=LLMUsageLog.Operation.GENERATE,
            input_tokens=500,
            output_tokens=200,
            model_id=CONFIG["model_id"],
            config_version=config_version,
        )

        # Monthly cost should only reflect generate, not gate
        monthly_cost = aggregate_monthly_cost(user_id=user_id, config_version=config_version)

        # Gate cost would be: (10000 * 0.25/1e6) + (5000 * 1.25/1e6) = 0.0025 + 0.00625 = 0.00875
        # Generate cost: (500 * 0.25/1e6) + (200 * 1.25/1e6) = 0.000125 + 0.00025 = 0.000375
        # Monthly cost should be ~0.000375, NOT including gate
        expected_gen_cost = Decimal("0.000375")
        assert monthly_cost == expected_gen_cost

    def test_gate_excluded_regardless_of_null_estimated_cost(self):
        """
        operation=gate is excluded from learner cost regardless of NULL (FR-002-14):

        - gate with fully unknown usage (estimated_cost_usd IS NULL) — outside
          learner cost, and its NULL never reaches the summation loop;
        - gate with a known cost — outside learner cost;
        - only learner (generate/guard/off_topic) records form the sum.
        """
        user_id = "student-gate-null-test"
        config_version = CONFIG["version"]

        # Learner record with known cost — must form the whole sum:
        # (100 * 0.25/1e6) + (80 * 1.25/1e6) = 0.000025 + 0.0001 = 0.000125
        # (exact at the field's 6 decimal places — no quantization drift)
        record_usage(
            request_id=uuid.uuid4(),
            user_id=user_id,
            operation=LLMUsageLog.Operation.GENERATE,
            input_tokens=100,
            output_tokens=80,
            model_id=CONFIG["model_id"],
            config_version=config_version,
        )

        # Gate with fully unknown usage → (NULL, NULL, NULL), must stay out
        # of learner cost even though its estimated_cost_usd IS NULL.
        record_usage(
            request_id=uuid.uuid4(),
            user_id=user_id,
            operation=LLMUsageLog.Operation.GATE,
            input_tokens=None,
            output_tokens=None,
            model_id=CONFIG["guard_model_id"],
            config_version=config_version,
        )

        # Gate with known cost → must also stay out of learner cost.
        record_usage(
            request_id=uuid.uuid4(),
            user_id=user_id,
            operation=LLMUsageLog.Operation.GATE,
            input_tokens=10000,
            output_tokens=5000,
            model_id=CONFIG["guard_model_id"],
            config_version=config_version,
        )

        monthly_cost = aggregate_monthly_cost(user_id=user_id, config_version=config_version)

        expected_learner_cost = Decimal("0.000125")
        assert monthly_cost == expected_learner_cost, (
            "both gate records (NULL and known cost) must be outside learner cost"
        )


# =============================================================================
# Test Case 4: Missing usage is not treated as zero
# =============================================================================

@pytest.mark.django_db
class TestMissingUsageHandling:
    """
    Test Case 4: Missing usage ≠ zero (FR-002-14 NULL semantics for record_usage).

    - Fully unknown usage (both token counts None): all three columns
      (input_tokens, output_tokens, estimated_cost_usd) are written as NULL —
      strictly `is None`, never 0 / Decimal("0"). "all or nothing".
    - Partial usage (exactly one token count None): the known counter is kept
      as-is, the unknown counter is NULL, and estimated_cost_usd is computed
      from the known part (unknown part counts as 0 in the formula) and stays
      NOT NULL. State "both counters known, cost NULL" is forbidden.
    - Genuine zero (0, 0) is a known fact: all three columns are NOT NULL —
      see TestEstimatedCostCalculation::test_zero_tokens_yields_zero_cost.

    Aggregation filters by estimated_cost_usd IS NOT NULL — by the cost
    column, never by zero token counts (see the aggregation tests below):
    fully-unknown records are skipped, genuine zero is included.
    """

    def test_missing_usage_creates_incomplete_record(self):
        """
        Operation without usage data (both counts None) creates a record with
        ALL THREE columns strictly NULL — unknown is not zero (FR-002-14).
        """
        request_id = uuid.uuid4()
        user_id = "student-missing-test"
        model_id = CONFIG["model_id"]
        config_version = CONFIG["version"]

        record_usage(
            request_id=request_id,
            user_id=user_id,
            operation=LLMUsageLog.Operation.GENERATE,
            input_tokens=None,  # Missing usage
            output_tokens=None,
            model_id=model_id,
            config_version=config_version,
        )

        record = LLMUsageLog.objects.get(request_id=request_id)
        assert record.input_tokens is None, \
            "fully unknown input_tokens must be NULL, not 0"
        assert record.output_tokens is None, \
            "fully unknown output_tokens must be NULL, not 0"
        assert record.estimated_cost_usd is None, \
            "fully unknown estimated_cost_usd must be NULL, not Decimal('0')"

    def test_partial_usage_known_input_kept_output_null_cost_from_known_part(self):
        """
        Partial usage with known input_tokens only: the known counter is stored
        as-is, the unknown output_tokens is NULL, and estimated_cost_usd is
        computed from the known part and stays NOT NULL (FR-002-14).
        """
        request_id = uuid.uuid4()
        user_id = "student-partial-input-test"
        model_id = CONFIG["model_id"]
        config_version = CONFIG["version"]
        input_tokens = 500  # known; output_tokens missing

        record_usage(
            request_id=request_id,
            user_id=user_id,
            operation=LLMUsageLog.Operation.GENERATE,
            input_tokens=input_tokens,
            output_tokens=None,  # Missing usage
            model_id=model_id,
            config_version=config_version,
        )

        record = LLMUsageLog.objects.get(request_id=request_id)
        assert record.input_tokens == input_tokens, \
            "known input_tokens must be kept as-is (500), not coerced"
        assert record.output_tokens is None, \
            "unknown output_tokens must be NULL, not 0"
        assert record.estimated_cost_usd is not None, \
            "partial usage cost comes from the known part and must be NOT NULL"
        # Cost from the known part only (unknown part = 0 in the formula):
        # 500 * 0.25 / 1e6 = 0.000125
        assert record.estimated_cost_usd == Decimal("0.000125"), \
            "estimated_cost_usd must be computed from the known input part"

    def test_partial_usage_known_output_kept_input_null_cost_from_known_part(self):
        """
        Partial usage with known output_tokens only: the known counter is stored
        as-is, the unknown input_tokens is NULL, and estimated_cost_usd is
        computed from the known part and stays NOT NULL (FR-002-14).
        """
        request_id = uuid.uuid4()
        user_id = "student-partial-output-test"
        model_id = CONFIG["model_id"]
        config_version = CONFIG["version"]
        output_tokens = 1500  # known; input_tokens missing

        record_usage(
            request_id=request_id,
            user_id=user_id,
            operation=LLMUsageLog.Operation.GENERATE,
            input_tokens=None,  # Missing usage
            output_tokens=output_tokens,
            model_id=model_id,
            config_version=config_version,
        )

        record = LLMUsageLog.objects.get(request_id=request_id)
        assert record.input_tokens is None, \
            "unknown input_tokens must be NULL, not 0"
        assert record.output_tokens == output_tokens, \
            "known output_tokens must be kept as-is (1500), not coerced"
        assert record.estimated_cost_usd is not None, \
            "partial usage cost comes from the known part and must be NOT NULL"
        # Cost from the known part only (unknown part = 0 in the formula):
        # 1500 * 1.25 / 1e6 = 0.001875
        assert record.estimated_cost_usd == Decimal("0.001875"), \
            "estimated_cost_usd must be computed from the known output part"

    def test_aggregation_skips_null_estimated_cost_and_includes_genuine_zero(self):
        """
        Monthly aggregation filters by estimated_cost_usd IS NOT NULL — the
        cost column, NOT zero token counts (FR-002-14, data-model §7
        «Наслідки для агрегації»):

        - fully unknown record (NULL, NULL, NULL) is skipped because its cost
          is NULL (unknown ≠ zero) — never summed, never coerced to 0;
        - genuine zero (0, 0, 0.000000) IS included: it is a known fact and a
          legitimate addend that contributes exactly 0.00 to the sum;
        - the known record contributes its full cost.
        """
        user_id = "student-agg-null-test"
        config_version = CONFIG["version"]

        # Known record — fully included in the sum:
        # (100 * 0.25/1e6) + (80 * 1.25/1e6) = 0.000025 + 0.0001 = 0.000125
        # (exact at the field's 6 decimal places — no quantization drift)
        record_usage(
            request_id=uuid.uuid4(),
            user_id=user_id,
            operation=LLMUsageLog.Operation.GENERATE,
            input_tokens=100,
            output_tokens=80,
            model_id=CONFIG["model_id"],
            config_version=config_version,
        )

        # Fully unknown record → estimated_cost_usd IS NULL → skipped by the
        # cost filter (a token-count heuristic would try to add NULL instead).
        record_usage(
            request_id=uuid.uuid4(),
            user_id=user_id,
            operation=LLMUsageLog.Operation.GENERATE,
            input_tokens=None,
            output_tokens=None,
            model_id=CONFIG["model_id"],
            config_version=config_version,
        )

        # Genuine zero (0, 0, 0.000000) — known fact, stays in the sum and
        # contributes exactly 0.00 (never dropped by a zero-token heuristic).
        record_usage(
            request_id=uuid.uuid4(),
            user_id=user_id,
            operation=LLMUsageLog.Operation.GENERATE,
            input_tokens=0,
            output_tokens=0,
            model_id=CONFIG["model_id"],
            config_version=config_version,
        )

        monthly_cost = aggregate_monthly_cost(user_id=user_id, config_version=config_version)

        # Known contributes 0.000125; unknown contributes nothing (skipped via
        # IS NULL); genuine zero contributes exactly 0.00 and remains part of
        # the aggregation.
        expected_cost = Decimal("0.000125")
        assert monthly_cost == expected_cost

    def test_aggregation_includes_genuine_zero_adds_exactly_zero(self):
        """
        Genuine zero (0, 0, 0.000000) is a known fact and remains in the
        aggregation: it adds exactly 0.00 and never blocks neighbouring
        records from being summed (FR-002-14 — unknown ≠ zero).
        """
        user_id = "student-genuine-zero-agg-test"
        config_version = CONFIG["version"]

        # Known record: (100 * 0.25/1e6) + (80 * 1.25/1e6) = 0.000125
        # (exact at the field's 6 decimal places — no quantization drift)
        record_usage(
            request_id=uuid.uuid4(),
            user_id=user_id,
            operation=LLMUsageLog.Operation.GENERATE,
            input_tokens=100,
            output_tokens=80,
            model_id=CONFIG["model_id"],
            config_version=config_version,
        )

        # Genuine zero — a full-fledged addend contributing exactly 0.00.
        record_usage(
            request_id=uuid.uuid4(),
            user_id=user_id,
            operation=LLMUsageLog.Operation.GENERATE,
            input_tokens=0,
            output_tokens=0,
            model_id=CONFIG["model_id"],
            config_version=config_version,
        )

        monthly_cost = aggregate_monthly_cost(user_id=user_id, config_version=config_version)

        # 0.000125 + 0.00 = 0.000125 — the zero row is included, not dropped.
        assert monthly_cost == Decimal("0.000125")

        # The genuine-zero row is a known fact in the log (cost NOT NULL):
        zero_record = LLMUsageLog.objects.get(
            user_id=user_id, input_tokens=0, output_tokens=0
        )
        assert zero_record.estimated_cost_usd is not None, \
            "genuine zero must stay NOT NULL — it is not an unknown record"
        assert zero_record.estimated_cost_usd == Decimal("0")

    def test_count_unknown_usage_counts_null_cost_without_gate(self):
        """
        count_unknown_usage(user_id, config_version) counts records with
        estimated_cost_usd IS NULL, EXCLUDING operation=gate (FR-002-14,
        data-model §7: skipped unknowns need a visible denominator for SC-006).

        Expected RED until T-062: count_unknown_usage does not exist yet, so
        this test fails with ImportError — the intended red cause for T-061.
        """
        from ai_tutor_service.providers.usage import count_unknown_usage  # added in T-062

        user_id = "student-count-unknown-test"
        config_version = CONFIG["version"]

        # 1. learner fully-unknown → counted
        record_usage(
            request_id=uuid.uuid4(),
            user_id=user_id,
            operation=LLMUsageLog.Operation.GENERATE,
            input_tokens=None,
            output_tokens=None,
            model_id=CONFIG["model_id"],
            config_version=config_version,
        )
        # 2. learner fully-unknown → counted
        record_usage(
            request_id=uuid.uuid4(),
            user_id=user_id,
            operation=LLMUsageLog.Operation.GUARD,
            input_tokens=None,
            output_tokens=None,
            model_id=CONFIG["model_id"],
            config_version=config_version,
        )
        # 3. gate fully-unknown → NOT counted (gate is outside learner metrics)
        record_usage(
            request_id=uuid.uuid4(),
            user_id=user_id,
            operation=LLMUsageLog.Operation.GATE,
            input_tokens=None,
            output_tokens=None,
            model_id=CONFIG["guard_model_id"],
            config_version=config_version,
        )
        # 4. learner with known cost → NOT counted
        record_usage(
            request_id=uuid.uuid4(),
            user_id=user_id,
            operation=LLMUsageLog.Operation.GENERATE,
            input_tokens=100,
            output_tokens=80,
            model_id=CONFIG["model_id"],
            config_version=config_version,
        )
        # 5. learner genuine zero (0, 0, 0.000000) → NOT counted (cost NOT NULL)
        record_usage(
            request_id=uuid.uuid4(),
            user_id=user_id,
            operation=LLMUsageLog.Operation.GENERATE,
            input_tokens=0,
            output_tokens=0,
            model_id=CONFIG["model_id"],
            config_version=config_version,
        )

        unknown_count = count_unknown_usage(
            user_id=user_id, config_version=config_version
        )
        assert unknown_count == 2, (
            "count_unknown_usage must count learner records with "
            "estimated_cost_usd IS NULL and exclude gate"
        )


# =============================================================================
# Test Case 5: Different config versions do not mix
# =============================================================================

@pytest.mark.django_db
class TestConfigVersionIsolation:
    """
    Test Case 5: Records with different config_version are aggregated separately.

    Each config version has its own rate tier. Aggregation must group by
    config_version before summing costs.
    """

    def test_different_config_versions_aggregated_separately(self):
        """
        Records with different config versions are grouped and summed independently.

        The v1.0.0 record is created via record_usage (matches current config).
        The v1.1.0 record is created directly via ORM because record_usage
        loud-fails on config_version mismatch — this is intentional protection
        against silently mistariffing foreign-version records with current rates.
        """
        from decimal import Decimal

        user_id = "student-version-test"
        config_v1 = "1.0.0"
        config_v2 = "1.1.0"

        # Create record with v1.0.0 via record_usage (current config version)
        request_id_v1 = uuid.uuid4()
        record_usage(
            request_id=request_id_v1,
            user_id=user_id,
            operation=LLMUsageLog.Operation.GENERATE,
            input_tokens=1000,
            output_tokens=500,
            model_id=CONFIG["model_id"],
            config_version=config_v1,
        )

        # Create record with v1.1.0 directly via ORM — foreign version cannot
        # be passed to record_usage because it would raise ValueError (loud-fail
        # protection against silently applying current rates to historical data).
        # Cost computed using same rates as current config for test purposes:
        # (1000 * 0.25 + 500 * 1.25) / 1_000_000 = 875 / 1_000_000 = 0.000875
        request_id_v2 = uuid.uuid4()
        LLMUsageLog.objects.create(
            request_id=request_id_v2,
            user_id=user_id,
            model_id=CONFIG["model_id"],
            operation=LLMUsageLog.Operation.GENERATE,
            input_tokens=1000,
            output_tokens=500,
            estimated_cost_usd=Decimal("0.000875"),
            config_version=config_v2,
        )

        # Each version aggregates independently
        cost_v1 = aggregate_monthly_cost(user_id=user_id, config_version=config_v1)
        cost_v2 = aggregate_monthly_cost(user_id=user_id, config_version=config_v2)

        # Both should have records but may have different costs if rates differ
        assert cost_v1 > 0
        assert cost_v2 > 0

    def test_record_usage_rejects_foreign_config_version(self):
        """
        record_usage loud-fails on config_version mismatch (захист від мовчазного mistariff).
        """
        with pytest.raises(ValueError) as exc_info:
            record_usage(
                request_id=uuid.uuid4(),
                user_id="student-reject-test",
                operation=LLMUsageLog.Operation.GENERATE,
                input_tokens=100,
                output_tokens=50,
                model_id=CONFIG["model_id"],
                config_version="9.9.9",
            )
        assert "config_version mismatch" in str(exc_info.value)


# =============================================================================
# Test Case 6: Monthly aggregation < USD 0.20 (SC-006)
# =============================================================================

@pytest.mark.django_db
class TestMonthlyAggregationBudget:
    """
    Test Case 6: Monthly aggregation fixture demonstrating USD 0.20 budget.

    Given multiple realistic usage records, aggregate_monthly_cost must return
    a value that stays under the monthly per-learner budget of $0.20.
    """

    def test_monthly_aggregation_under_usd_0_20(self):
        """
        Multiple realistic token counts aggregated stay under $0.20 monthly budget.

        This fixture demonstrates SC-006 compliance: cost < $0.20 per learner per month.
        """
        user_id = "student-budget-test"
        config_version = CONFIG["version"]

        # Fixture: realistic usage pattern over a month
        # Average per day: 5 conversations, ~400 input + ~800 output tokens each
        # Daily cost: 5 * ((400 * 0.25/1e6) + (800 * 1.25/1e6))
        #            = 5 * (0.0001 + 0.001) = 5 * 0.0011 = 0.0055 USD
        # Monthly (30 days): 0.0055 * 30 = 0.165 USD < 0.20 USD ✓

        daily_records = []
        for day in range(1, 31):  # 30 days of usage
            for conv in range(5):  # 5 conversations per day
                record = LLMUsageLog.objects.create(
                    request_id=uuid.uuid4(),
                    user_id=user_id,
                    model_id=CONFIG["model_id"],
                    operation=LLMUsageLog.Operation.GENERATE,
                    input_tokens=400,
                    output_tokens=800,
                    estimated_cost_usd=Decimal("0.0011"),  # pre-computed
                    config_version=config_version,
                    created_at=datetime.now() - timedelta(days=1, hours=conv),
                )
                daily_records.append(record)

        # Calculate monthly aggregated cost
        total_cost = aggregate_monthly_cost(user_id=user_id, config_version=config_version)

        # Assert budget compliance: must be < $0.20
        assert total_cost < Decimal("0.20"), \
            f"Monthly cost {total_cost} exceeds budget $0.20"

    def test_monthly_aggregation_includes_all_operations(self):
        """
        Monthly aggregation includes generate, guard, off_topic but excludes gate.
        """
        user_id = "student-ops-test"
        config_version = CONFIG["version"]

        # Create records for each operation type
        for op in [LLMUsageLog.Operation.GENERATE,
                   LLMUsageLog.Operation.GUARD,
                   LLMUsageLog.Operation.OFF_TOPIC]:
            LLMUsageLog.objects.create(
                request_id=uuid.uuid4(),
                user_id=user_id,
                model_id=CONFIG["model_id"],
                operation=op,
                input_tokens=100,
                output_tokens=50,
                estimated_cost_usd=Decimal("0.000375"),
                config_version=config_version,
                created_at=datetime.now(),
            )

        # Create gate record (should not count)
        LLMUsageLog.objects.create(
            request_id=uuid.uuid4(),
            user_id=user_id,
            model_id=CONFIG["guard_model_id"],
            operation=LLMUsageLog.Operation.GATE,
            input_tokens=1000,
            output_tokens=500,
            estimated_cost_usd=Decimal("0.001375"),
            config_version=config_version,
            created_at=datetime.now(),
        )

        total_cost = aggregate_monthly_cost(user_id=user_id, config_version=config_version)

        # Should only include 3 operations (generate, guard, off_topic), not gate
        # 3 * 0.000375 = 0.001125
        expected_cost = Decimal("0.001125")
        assert total_cost == expected_cost

    def test_aggregate_monthly_cost_signature_and_decimal_return(self):
        """
        Signature and Decimal return of aggregate_monthly_cost are UNCHANGED
        by the FR-002-14 NULL semantics (contract lock, T-061):

        - parameters stay exactly (user_id, config_version), both required
          with no defaults;
        - the return value is a Decimal, even when no qualifying records
          exist (empty sum).
        """
        sig = inspect.signature(aggregate_monthly_cost)
        params = list(sig.parameters.keys())
        assert params == ["user_id", "config_version"], (
            f"aggregate_monthly_cost signature must stay (user_id, config_version), "
            f"got {params}"
        )
        for name, param in sig.parameters.items():
            assert param.default is inspect.Parameter.empty, (
                f"parameter {name!r} must stay required (no default)"
            )

        result = aggregate_monthly_cost(
            user_id="student-signature-test-no-records",
            config_version=CONFIG["version"],
        )
        assert isinstance(result, Decimal), (
            f"aggregate_monthly_cost must return Decimal, got {type(result).__name__}"
        )


# =============================================================================
# Test Case 7: LLMUsageLog model validates append-only contract
# =============================================================================

@pytest.mark.django_db
class TestLLMUsageLogModelContract:
    """
    Test Case 7: LLMUsageLog model fields match contract (append-only, no update).

    Model is append-only by design:
    - Primary key is UUID (prevents natural key overwrites)
    - All required fields enforced (not null)
    - Timestamps auto-generated
    - No update/delete methods exposed through business logic
    """

    def test_operation_enum_has_all_four_values(self):
        """
        Operation enum includes all four: generate, guard, off_topic, gate.
        """
        operations = [choice[0] for choice in LLMUsageLog.Operation.choices]
        assert "generate" in operations
        assert "guard" in operations
        assert "off_topic" in operations
        assert "gate" in operations

    def test_input_tokens_non_negative(self):
        """
        input_tokens field enforces non-negative values.
        """
        field = LLMUsageLog._meta.get_field("input_tokens")
        from django.core.validators import MinValueValidator
        validators = [v for v in field.validators]
        has_min = any(isinstance(v, MinValueValidator) and v.limit_value == 0 for v in validators)
        assert has_min or field.__class__.__name__ == "PositiveIntegerField"

    def test_output_tokens_non_negative(self):
        """
        output_tokens field enforces non-negative values.
        """
        field = LLMUsageLog._meta.get_field("output_tokens")
        from django.core.validators import MinValueValidator
        validators = [v for v in field.validators]
        has_min = any(isinstance(v, MinValueValidator) and v.limit_value == 0 for v in validators)
        assert has_min or field.__class__.__name__ == "PositiveIntegerField"

    def test_estimated_cost_has_sufficient_precision(self):
        """
        estimated_cost_usd DecimalField has sufficient precision (max_digits >= 12, decimal_places >= 6).
        """
        field = LLMUsageLog._meta.get_field("estimated_cost_usd")
        assert field.max_digits >= 12
        assert field.decimal_places >= 6

    def test_config_version_required(self):
        """
        config_version is required (not blank, not null).
        """
        field = LLMUsageLog._meta.get_field("config_version")
        assert field.blank is False
        assert field.null is False

    def test_created_at_auto_now_add(self):
        """
        created_at is auto-populated on creation (append-only audit trail).
        """
        field = LLMUsageLog._meta.get_field("created_at")
        assert field.auto_now_add is True or field.has_default()

    def test_request_id_indexed(self):
        """
        request_id is indexed for fast lookups.
        """
        # Check index on request_id
        indexes = LLMUsageLog._meta.indexes
        request_id_indexed = any(
            "request_id" in list(idx.fields) for idx in indexes
        )
        assert request_id_indexed

    def test_user_id_and_created_at_indexed(self):
        """
        (user_id, created_at) composite index for user activity queries.
        """
        indexes = LLMUsageLog._meta.indexes
        user_time_indexed = any(
            set(idx.fields) == {"user_id", "created_at"} for idx in indexes
        )
        assert user_time_indexed
