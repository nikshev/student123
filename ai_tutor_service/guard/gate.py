# impl: FR-002-13
# verifies: FR-002-13
# impl: FR-002-09
"""
Gate evaluation logic for AI Tutor release gate (T-044).

Implements the gate decision algorithm per FR-002-13:
- Rate = count(samples with contains_solution=True) / total_samples
- Rate ≤ gate_go_threshold → "go"
- Rate > gate_stop_threshold → "stop"
- Otherwise → "human"
- Technical failure or empty corpus → "invalid"

GateEvaluator stores thresholds from tutor_config.yaml and provides
deterministic gate evaluation for reproducible testing.
"""

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, TypedDict

from django.db import OperationalError

from ai_tutor_service.config import load_tutor_config
from ai_tutor_service.providers.models import LLMUsageLog
from ai_tutor_service.providers.usage import record_usage

logger = logging.getLogger(__name__)

_DEFAULT_THRESHOLDS: Optional[Tuple[float, float]] = None

# Resolve config path at module load time
try:
    _CONFIG_PATH = Path(__file__).resolve().parents[1] / "tutor_config.yaml"
except Exception:
    _CONFIG_PATH = None


class GateSample(TypedDict):
    """Individual sample in gate corpus."""
    id: str
    contains_solution: bool
    technical_failure: bool


class GateCorpus(TypedDict):
    """Gate evaluation corpus."""
    version: str
    items: List[GateSample]


class GateReport(TypedDict):
    """Gate evaluation report."""
    run_id: str
    sample_version: str
    config_version: str
    total: int
    contains_solution: int
    rate: float
    verdict: str
    route: str
    started_at: str
    finished_at: str


def verdict_for_rate(
    rate: float,
    go_threshold: float | None = None,
    stop_threshold: float | None = None,
) -> str:
    """
    Pure function to determine gate verdict based on solution rate.
    
    Implements constitution gate boundary:
        rate <= go_threshold -> "go"
        rate > stop_threshold -> "stop"
        otherwise -> "human"
    
    Thresholds are loaded from tutor_config.yaml when not provided explicitly.
    
    Args:
        rate: Solution rate (0.0 to 1.0)
        go_threshold: Maximum rate for automatic "go" (e.g., 0.03)
        stop_threshold: Minimum rate for automatic "stop" (e.g., 0.10)
        
    Returns:
        One of: "go", "human", "stop"
        
    Raises:
        ValueError: If rate is not between 0.0 and 1.0 inclusive
    """
    if not 0.0 <= rate <= 1.0:
        raise ValueError(f"Rate must be between 0.0 and 1.0, got {rate}")
    
    if go_threshold is None or stop_threshold is None:
        global _DEFAULT_THRESHOLDS
        if _DEFAULT_THRESHOLDS is None:
            _DEFAULT_THRESHOLDS = (
                load_tutor_config(_CONFIG_PATH)["guard"]["gate_go_threshold"],
                load_tutor_config(_CONFIG_PATH)["guard"]["gate_stop_threshold"],
            )
        go_threshold, stop_threshold = _DEFAULT_THRESHOLDS
    
    if rate <= go_threshold:
        return "go"
    elif rate > stop_threshold:
        return "stop"
    else:
        return "human"


class GateEvaluator:
    """
    Gate evaluation engine that computes release verdict from corpus.
    
    Stores thresholds from tutor_config.yaml and provides deterministic
    evaluation of gate corpus samples.
    
    Thread-safe: instances are immutable after construction.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize gate evaluator with configuration.
        
        Args:
            config: Loaded tutor configuration from tutor_config.yaml
        """
        self.config = config
        self.go_threshold = config["guard"]["gate_go_threshold"]
        self.stop_threshold = config["guard"]["gate_stop_threshold"]
        self.config_version = config["version"]
        
    def run(
        self,
        sample_version: str,
        route: str,
        corpus: Dict[str, Any],
    ) -> GateReport:
        """
        Evaluate gate corpus and return report.
        
        Args:
            sample_version: Version of gate_samples.yaml being evaluated
            route: Processing route (must be "default" for MVP)
            corpus: Gate corpus with version and items list
            
        Returns:
            GateReport with evaluation results
            
        Raises:
            ValueError: If corpus is empty or malformed
        """
        started_at = datetime.now(timezone.utc).isoformat()
        
        # Validate corpus structure
        if not isinstance(corpus, dict):
            raise ValueError("Corpus must be a dictionary")
        
        if "version" not in corpus or "items" not in corpus:
            raise ValueError("Corpus must have 'version' and 'items' keys")
        
        if not isinstance(corpus["items"], list):
            raise ValueError("Corpus items must be a list")
        
        total = len(corpus["items"])
        
        # Handle empty corpus - technical failure per contract
        if total == 0:
            finished_at = datetime.now(timezone.utc).isoformat()
            return {
                "run_id": str(uuid.uuid4()),
                "sample_version": sample_version,
                "config_version": "",
                "total": 0,
                "contains_solution": 0,
                "rate": 0.0,
                "verdict": "invalid",
                "route": route,
                "started_at": started_at,
                "finished_at": finished_at,
            }
        
        # Count solutions and technical failures
        contains_solution_count = 0
        technical_failure_count = 0
        
        for item in corpus["items"]:
            if not isinstance(item, dict):
                raise ValueError("Each corpus item must be a dictionary")
            
            # Technical failure (explicit flag or missing contains_solution)
            # → whole gate verdict is invalid, but rate still counts k/total
            if item.get("technical_failure", False):
                technical_failure_count += 1
            elif "contains_solution" not in item:
                # Item without a valid contains_solution definition is a
                # technical failure for gate purposes (fail closed).
                technical_failure_count += 1
            elif item["contains_solution"]:
                contains_solution_count += 1
        
        # Calculate rate as k/total across the whole corpus (per contract)
        rate = contains_solution_count / total if total > 0 else 0.0
        
        # Determine verdict — any technical failure invalidates the whole gate
        if technical_failure_count > 0:
            verdict = "invalid"
        else:
            verdict = verdict_for_rate(rate, self.go_threshold, self.stop_threshold)
        
        # Record gate evaluation usage (FR-002-09 / T-048). The gate is a
        # read-only evaluation of a corpus; no LLM tokens are consumed here,
        # so the record is marked incomplete (excluded from learner cost).
        run_id = str(uuid.uuid4())
        try:
            record_usage(
                request_id=run_id,
                user_id="gate",
                operation=LLMUsageLog.Operation.GATE,
                input_tokens=None,
                output_tokens=None,
                model_id=self.config["guard_model_id"],
                config_version=self.config_version,
            )
        except OperationalError as exc:
            logger.warning("record_usage failed for gate run (%s): %s", run_id, exc, exc_info=True)
        
        finished_at = datetime.now(timezone.utc).isoformat()
        
        return {
            "run_id": run_id,
            "sample_version": sample_version,
            "config_version": self.config_version,
            "total": total,
            "contains_solution": contains_solution_count,
            "rate": rate,
            "verdict": verdict,
            "route": route,
            "started_at": started_at,
            "finished_at": finished_at,
        }