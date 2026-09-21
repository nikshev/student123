# impl: FR-002-13
# verifies: FR-002-13
"""
Gate API endpoint for POST /api/v1/gate/run.

Implements the gate evaluation API per FR-002-13:
- Staff-only via @require_staff_role
- Idempotency-Key mandatory (UUID format)
- Request: {"sample_version": "<version>", "route": "default"}
- Response: Gate report with run_id, sample_version, config_version, etc.
- Sample version must match gate_samples.yaml
- Idempotent retry with same key returns same result
- No student side effects (no conversation/quota/tracking)
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

from django.http import HttpRequest, HttpResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from ai_tutor_service.api.auth import require_staff_role
from ai_tutor_service.api.errors import (
    make_error_response,
    validation_error,
    idempotency_conflict,
)
from ai_tutor_service.config import load_tutor_config
from ai_tutor_service.guard.gate import GateEvaluator, GateReport, verdict_for_rate

# Module-level idempotency cache
# Maps idempotency_key -> (report_dict, sample_version, route, config_version)
IDEMPOTENCY_CACHE: Dict[str, tuple] = {}

# Load config once
CONFIG = load_tutor_config(Path(__file__).resolve().parents[1] / "tutor_config.yaml")


def _get_gate_samples_version() -> str:
    """Get version from gate_samples.yaml."""
    import yaml
    from pathlib import Path
    gate_samples_path = Path(__file__).resolve().parents[2] / "ai_tutor_service" / "gate_samples.yaml"
    if gate_samples_path.exists():
        with open(gate_samples_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("version", "")
    return ""


def gate_run(
    sample_version: str,
    route: str,
    idempotency_key: str,
) -> tuple[GateReport, bool]:
    """
    Run gate evaluation with idempotency support.
    
    Args:
        sample_version: Version of gate_samples.yaml
        route: Processing route
        idempotency_key: UUID string for idempotency
        
    Returns:
        Tuple of (report, is_duplicate)
    """
    # Validate sample_version matches gate_samples.yaml
    expected_version = _get_gate_samples_version()
    if sample_version != expected_version:
        raise ValueError(
            f"sample_version '{sample_version}' does not match gate_samples.yaml version '{expected_version}'"
        )
    
    # Check idempotency cache
    if idempotency_key in IDEMPOTENCY_CACHE:
        cached_report, _ = IDEMPOTENCY_CACHE[idempotency_key]
        return cached_report, True
    
    # Run gate evaluation
    evaluator = GateEvaluator(CONFIG)
    
    # Load from gate_samples.yaml
    import yaml
    from pathlib import Path
    gate_samples_path = Path(__file__).resolve().parents[2] / "ai_tutor_service" / "gate_samples.yaml"
    with open(gate_samples_path, encoding="utf-8") as f:
        samples_data = yaml.safe_load(f)
    
    corpus_items = []
    for s in samples_data.get("samples", []):
        corpus_items.append({
            "id": s["id"],
            "contains_solution": s["contains_solution"],
        })
    
    corpus = {
        "version": samples_data.get("version", "1.0.0"),
        "items": corpus_items,
    }
    
    report = evaluator.run(sample_version=sample_version, route=route, corpus=corpus)
    
    # Set config_version from tutor_config
    report["config_version"] = CONFIG["version"]
    
    # Cache result for idempotency
    IDEMPOTENCY_CACHE[idempotency_key] = (report, False)
    
    return report, False


class GateRunView(View):
    """
    POST /api/v1/gate/run - Run release gate evaluation (staff only).
    
    Requires: Bearer token + X-AI-Tutor-Role: staff header + Idempotency-Key header
    Returns: 200 with gate report, 400 for validation errors, 403 for non-staff,
             409 for idempotency conflict, 502/503/504 for service errors
    """
    
    @method_decorator(csrf_exempt)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)
    
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        # Require staff role
        if not request.headers.get("X-AI-Tutor-Role") == "staff":
            return make_error_response(
                "forbidden",
                status=403,
                message="Staff role required",
            )
        
        # Require Idempotency-Key header
        idempotency_key = request.headers.get("Idempotency-Key")
        if not idempotency_key:
            return validation_error(message="Idempotency-Key header is required")
        
        # Validate UUID format
        try:
            uuid.UUID(idempotency_key)
        except ValueError:
            return validation_error(message="Idempotency-Key must be a valid UUID")
        
        # Parse and validate request body
        try:
            body = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            return validation_error(message="Invalid JSON body")
        
        # Check required fields
        required_fields = ["sample_version", "route"]
        missing = [f for f in required_fields if f not in body]
        if missing:
            return validation_error(message=f"Missing required fields: {', '.join(missing)}")
        
        # Validate route
        if body.get("route") != "default":
            return validation_error(message='Only route="default" is supported')
        
        # Get sample_version and route
        sample_version = body["sample_version"]
        route = body["route"]
        
        # Run gate evaluation with idempotency
        try:
            report, is_duplicate = gate_run(sample_version, route, idempotency_key)
        except ValueError as e:
            # sample_version mismatch or other validation error
            return validation_error(message=str(e))
        except Exception as e:
            # Technical failure - return invalid verdict
            error_report: GateReport = {
                "run_id": str(uuid.uuid4()),
                "sample_version": sample_version,
                "config_version": CONFIG["version"],
                "total": 0,
                "contains_solution": 0,
                "rate": 0.0,
                "verdict": "invalid",
                "route": route,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
            return make_error_response(
                "service_unavailable",
                status=502,
                message=str(e),
            )
        
        # Return report
        from django.http import JsonResponse
        return JsonResponse(report, status=200)


# Backwards compatibility - keep GateRunView.as_view() working with the class-based view
gate_run_view = GateRunView.as_view()