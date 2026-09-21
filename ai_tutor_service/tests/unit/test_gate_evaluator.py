# verifies: FR-002-13
"""
Table-driven unit tests for GateEvaluator (T-043, contract T-044).

The gate evaluator computes a release verdict for a corpus of samples
before allowing a live AI-tutor deployment. The pure function verdict_for_rate
implements the constitution gate boundary:
    rate <= go_threshold (0.03)  → "go"
    rate >  stop_threshold(0.10) → "stop"
    otherwise                    → "human"

Technical failure anywhere → verdict "invalid" (not counted in numerator/denominator).
Empty corpus → verdict "invalid" (never silently "go").

GateEvaluator(config) stores thresholds from tutor_config.yaml (never hard-coded).
evaluate(corpus_results) -> report dict with keys:
    run_id, sample_version, config_version, total, contains_solution,
    rate, verdict, route, started_at, finished_at.

Expected RED reason (collection): ModuleNotFoundError ai_tutor_service.guard.gate
(because ai_tutor_service/guard/gate.py does not exist yet; T-044 will implement it).
"""

import pytest

from pathlib import Path

from ai_tutor_service.config import load_tutor_config
from ai_tutor_service.guard.gate import GateEvaluator, GateReport, verdict_for_rate

# ── Load thresholds from YAML (no hard-coded values) ────────────────────

CONFIG_PATH = Path(__file__).resolve().parents[3] / "ai_tutor_service" / "tutor_config.yaml"
CONFIG = load_tutor_config(CONFIG_PATH)
GO_THRESHOLD = CONFIG["guard"]["gate_go_threshold"]       # 0.03
STOP_THRESHOLD = CONFIG["guard"]["gate_stop_threshold"]   # 0.10


# ═══════════════════════════════════════════════════════════════════════════
# Table-driven: pure verdict_for_rate boundaries (T-043 case 1)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("rate,expected_verdict", [
    (0.0,              "go"),       # 0% ≤ go_threshold → go
    (GO_THRESHOLD,     "go"),       # exactly 3% ≤ go_threshold → go
    (GO_THRESHOLD + 0.01, "human"), # between thresholds → human
    (STOP_THRESHOLD,   "human"),    # exactly 10% NOT > stop_threshold → human
    (STOP_THRESHOLD + 0.01, "stop"),# > 10% → stop
])
def test_verdict_for_rate_boundaries(rate, expected_verdict):
    """
    Pure verdict boundaries per contract §4 of solution-guard.md:
    r ≤ 0.03 → go; r > 0.10 → stop; otherwise → human.
    Thresholds are loaded from tutor_config.yaml (never hard-coded).
    """
    assert verdict_for_rate(rate) == expected_verdict


# ═══════════════════════════════════════════════════════════════════════════
# Table-driven: GateEvaluator stores config thresholds (T-043 case 5)
# ═══════════════════════════════════════════════════════════════════════════

def test_gate_evaluator_stores_config_thresholds():
    """
    GateEvaluator must store thresholds loaded from YAML, not hard-coded.
    Verifies that gate_go_threshold and gate_stop_threshold match the
    values from tutor_config.yaml (constitution III: config-as-source-of-truth).
    """
    evaluator = GateEvaluator(CONFIG)
    assert evaluator.go_threshold == GO_THRESHOLD
    assert evaluator.stop_threshold == STOP_THRESHOLD


# ═══════════════════════════════════════════════════════════════════════════
# Table-driven: evaluate() → report with exact schema (T-043 case 2)
# ═══════════════════════════════════════════════════════════════════════════

REPORT_EXPECTED_KEYS = frozenset({
    "run_id", "sample_version", "config_version", "total",
    "contains_solution", "rate", "verdict", "route",
    "started_at", "finished_at",
})


@pytest.mark.parametrize("sample_version,route,corpus_items", [
    ("v1.0.0", "default", [
        {"id": "s1", "contains_solution": False},
        {"id": "s2", "contains_solution": False},
        {"id": "s3", "contains_solution": False},
    ]),   # 0/3 = 0% → go
    ("v1.1.0", "default", [
        {"id": "s1", "contains_solution": True},
        {"id": "s2", "contains_solution": False},
        {"id": "s3", "contains_solution": False},
    ]),   # 1/3 = 0.333 > 0.10 → stop
])
def test_evaluate_report_schema_and_verdict(sample_version, route, corpus_items):
    """
    GateEvaluator.run() must return a report dict with EXACTLY the
    required keys {run_id, sample_version, config_version, total,
    contains_solution, rate, verdict, route, started_at, finished_at},
    and the verdict must match the rate boundary rules.

    Uses a synthetic corpus with N items and k contains_solution,
    computing rate = k/N and verifying against verdict_for_rate.
    """
    evaluator = GateEvaluator(CONFIG)
    total = len(corpus_items)
    k = sum(1 for item in corpus_items if item["contains_solution"])
    expected_rate = k / total if total > 0 else 0.0
    expected_verdict = verdict_for_rate(expected_rate)

    corpus = {
        "version": "corpus-v1.0.0",
        "items": corpus_items,
    }
    report = evaluator.run(
        sample_version=sample_version,
        route=route,
        corpus=corpus,
    )

    # Report schema check: exactly the expected keys
    assert set(report.keys()) == REPORT_EXPECTED_KEYS

    # Field-by-field contract checks
    assert report["sample_version"] == sample_version
    assert report["config_version"] == CONFIG["version"]
    assert report["route"] == route
    assert report["total"] == total
    assert report["contains_solution"] == k
    assert report["rate"] == pytest.approx(expected_rate, abs=1e-9)
    assert report["verdict"] == expected_verdict


# ═══════════════════════════════════════════════════════════════════════════
# Technical failure → "invalid" (T-043 case 3)
# ═══════════════════════════════════════════════════════════════════════════

def test_technical_failure_yields_invalid():
    """
    If any item in the corpus has a technical failure (e.g., LLM error,
    timeout), the verdict must be "invalid". Technical failures are NOT
    counted in the gate numerator/denominator per contract §4.
    """
    evaluator = GateEvaluator(CONFIG)
    corpus = {
        "version": "corpus-v1.0.0",
        "items": [
            {"id": "s1", "contains_solution": False},
            {"id": "s2", "technical_failure": True, "error": "LLM timeout"},
        ],
    }
    report = evaluator.run(
        sample_version="v1.0.0",
        route="default",
        corpus=corpus,
    )
    assert report["verdict"] == "invalid"


# ═══════════════════════════════════════════════════════════════════════════
# Empty corpus → "invalid" (T-043 case 4)
# ═══════════════════════════════════════════════════════════════════════════

def test_empty_corpus_yields_invalid():
    """
    An empty corpus must yield verdict "invalid" (or raise ValueError).
    It must NEVER silently return "go".
    """
    evaluator = GateEvaluator(CONFIG)
    corpus = {
        "version": "corpus-v1.0.0",
        "items": [],
    }
    report = evaluator.run(
        sample_version="v1.0.0",
        route="default",
        corpus=corpus,
    )
    assert report["verdict"] == "invalid"
    assert report["total"] == 0
    assert report["rate"] == 0.0
