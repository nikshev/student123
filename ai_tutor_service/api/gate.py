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

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from django.db import OperationalError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from ai_tutor_service.api.auth import require_staff_role
from ai_tutor_service.api.errors import (
    make_error_response,
    validation_error,
    idempotency_conflict,
    service_unavailable,
    timeout_error,
)
from ai_tutor_service.config import load_tutor_config
from ai_tutor_service.guard.gate import GateEvaluator, GateReport, verdict_for_rate
from ai_tutor_service.limits.models import IdempotencyRecord
from ai_tutor_service.providers.client import LLMError

# Load config once
CONFIG = load_tutor_config(Path(__file__).resolve().parents[1] / "tutor_config.yaml")


def _get_gate_samples_version() -> str:
    """Get version from gate_samples.yaml."""
    import yaml
    gate_samples_path = Path(__file__).resolve().parents[2] / "ai_tutor_service" / "gate_samples.yaml"
    if gate_samples_path.exists():
        with open(gate_samples_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("version", "")
    return ""


def _compute_payload_hash(payload: dict) -> str:
    """Compute SHA-256 hex checksum of canonical JSON payload."""
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode()).hexdigest()


def gate_run(
    sample_version: str,
    route: str,
) -> dict:
    """
    Run gate evaluation and return report dict.

    Args:
        sample_version: Version of gate_samples.yaml
        route: Processing route

    Returns:
        Report dict (GateReport)
    """
    # Validate sample_version matches gate_samples.yaml
    expected_version = _get_gate_samples_version()
    if sample_version != expected_version:
        raise ValueError(
            f"sample_version '{sample_version}' does not match gate_samples.yaml version '{expected_version}'"
        )

    # Run gate evaluation
    evaluator = GateEvaluator(CONFIG)

    # Load from gate_samples.yaml
    import yaml
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

    return report


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

    @require_staff_role
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        # Require Idempotency-Key header
        idempotency_key = request.headers.get("Idempotency-Key")
        if not idempotency_key:
            return validation_error(message="Idempotency-Key header is required")

        # Validate UUID format
        try:
            request_id = uuid.UUID(idempotency_key)
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

        # Build canonical payload for idempotency hash
        payload = {"sample_version": sample_version, "route": route}
        payload_hash = _compute_payload_hash(payload)

        # Check durable idempotency record
        try:
            record = IdempotencyRecord.objects.get(request_id=request_id)
        except IdempotencyRecord.DoesNotExist:
            record = None

        if record is not None:
            if record.payload_hash == payload_hash:
                # Same key + same payload → return stored response with same run_id
                return JsonResponse(record.response, status=200)
            else:
                # Same key + different payload → 409 conflict
                return idempotency_conflict(request_id=str(request_id))

        # Run gate evaluation
        try:
            report = gate_run(sample_version, route)
        except ValueError as e:
            return validation_error(message=str(e))
        except LLMError as e:
            if e.error_type == "timeout":
                return timeout_error(request_id=str(request_id))
            elif e.error_type == "malformed_response":
                return make_error_response("invalid_upstream_response", request_id=str(request_id), status=502)
            else:
                return make_error_response("upstream_error", request_id=str(request_id), status=502)
        except OperationalError:
            return service_unavailable(request_id=str(request_id))
        except Exception:
            return service_unavailable(request_id=str(request_id))

        # Store idempotency record AFTER successful run
        IdempotencyRecord.objects.create(
            request_id=request_id,
            user_id="staff",
            payload_hash=payload_hash,
            response=report,
        )

        return JsonResponse(report, status=200)


# Backwards compatibility - keep GateRunView.as_view() working with the class-based view
gate_run_view = GateRunView.as_view()
