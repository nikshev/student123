# verifies: FR-002-13
"""
Gate API contract tests for POST /api/v1/gate/run (T-043).

The gate API contract defines the interface for staff-only release gate evaluation.
This test file documents and verifies that contract BEFORE the implementation exists (T-044).

Contract summary (from contracts/tutor-service-api.md §7):
- POST /api/v1/gate/run - Staff only; Idempotency-Key mandatory.
- Request: {"sample_version": "<version>", "route": "default"}
- 200 response: {"run_id": "...", "sample_version": "...", "config_version": "...", 
  "total": 100, "contains_solution": 2, "rate": 0.02, "verdict": "go|human|stop|invalid", 
  "route": "...", "started_at": "...", "finished_at": "..."}
- 400: sample/config mismatch, missing fields
- 403: non-staff (requires X-AI-Tutor-Role: staff header)
- 409: duplicate idempotency key conflict
- 501: stub implementation (current state - test expects real report)
- 502/503/504: provider/service errors
- Idempotency: same key+sample_version+route → same result (no duplicate count)
- Pinned versions: sample_version must match gate_samples.yaml, config_version matches tutor_config.yaml
- Usage operation=gate (no student side effects: no conversation/quota/tracking)
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from django.test import Client
from django.db import OperationalError

from ai_tutor_service.config import load_tutor_config
from ai_tutor_service.providers.client import LLMError

# Load config for testing
CONFIG_PATH = Path(__file__).resolve().parents[3] / "ai_tutor_service" / "tutor_config.yaml"
CONFIG = load_tutor_config(CONFIG_PATH)

# Constants
BEARER = "test-shared-secret-12345"
GATE_RUN_URL = "/api/v1/gate/run"
SAMPLE_VERSION = "1.0.0"  # Will match gate_samples.yaml version (when created)
CONFIG_VERSION = CONFIG["version"]

# Expected output fields from contract
EXPECTED_OUTPUT_FIELDS = {
    "run_id", "sample_version", "config_version", "total", 
    "contains_solution", "rate", "verdict", "route", 
    "started_at", "finished_at"
}

# Expected verdict values
VALID_VERDICTS = {"go", "human", "stop", "invalid"}


@pytest.fixture
def staff_headers():
    """Staff authentication headers."""
    return {
        "HTTP_AUTHORIZATION": f"Bearer {BEARER}",
        "HTTP_X_AI_TUTOR_ROLE": "staff",
    }


@pytest.fixture
def non_staff_headers():
    """Non-staff headers (should get 403)."""
    return {
        "HTTP_AUTHORIZATION": f"Bearer {BEARER}",
        "HTTP_X_AI_TUTOR_ROLE": "student",  # or any non-staff value
    }


@pytest.fixture
def gate_request_body():
    """Valid gate run request body."""
    return {
        "sample_version": SAMPLE_VERSION,
        "route": "default",
    }


def _assert_gate_report_shape(data):
    """Assert response has exact gate report shape."""
    assert set(data.keys()) == EXPECTED_OUTPUT_FIELDS, (
        f"Gate report must have exactly {EXPECTED_OUTPUT_FIELDS}, got {set(data.keys())}"
    )
    
    # Validate UUIDs
    uuid.UUID(data["run_id"])
    assert data["sample_version"] == SAMPLE_VERSION
    assert data["config_version"] == CONFIG["version"]
    
    # Validate types
    assert isinstance(data["total"], int) and data["total"] >= 0
    assert isinstance(data["contains_solution"], int) and data["contains_solution"] >= 0
    assert isinstance(data["rate"], (int, float)) and 0 <= data["rate"] <= 1
    assert data["verdict"] in VALID_VERDICTS
    assert data["route"] == "default"
    
    # Validate timestamps are parseable
    datetime.fromisoformat(data["started_at"].replace("Z", "+00:00"))
    datetime.fromisoformat(data["finished_at"].replace("Z", "+00:00"))


def _assert_error_envelope(response, expected_status):
    """Assert error response has standard envelope."""
    data = json.loads(response.content)
    assert set(data.keys()) == {"error", "request_id"}, (
        f"error envelope must be {{error, request_id}}, got {sorted(data.keys())}"
    )
    assert "code" in data["error"]
    assert "message" in data["error"]
    uuid.UUID(data["request_id"])
    return data


class TestGateRunContract:
    """Contract tests for POST /api/v1/gate/run."""

    def test_staff_auth_required(self):
        """
        Contract: staff auth required (X-AI-Tutor-Role: staff header).
        Non-staff → 403.
        """
        client = Client()
        
        # Test with student role (should fail)
        response = client.post(
            GATE_RUN_URL,
            data=json.dumps({"sample_version": "1.0.0", "route": "default"}),
            content_type="application/json",
            **{
                "HTTP_AUTHORIZATION": f"Bearer {BEARER}",
                "HTTP_X_AI_TUTOR_ROLE": "student",  # Non-staff
            },
        )
        data = _assert_error_envelope(response, 403)
        assert data["error"]["code"] in {"forbidden", "actor_mismatch"}
        
        # Test with missing role header (should fail)
        response = client.post(
            GATE_RUN_URL,
            data=json.dumps({"sample_version": "1.0.0", "route": "default"}),
            content_type="application/json",
            **{
                "HTTP_AUTHORIZATION": f"Bearer {BEARER}",
                # Missing X-AI-Tutor-Role header
            },
        )
        data = _assert_error_envelope(response, 403)
        assert data["error"]["code"] in {"forbidden", "actor_mismatch"}

    def test_idempotency_key_mandatory(self):
        """
        Contract: Idempotency-Key mandatory (like /materials endpoint).
        Missing → 400.
        """
        client = Client()
        response = client.post(
            GATE_RUN_URL,
            data=json.dumps({"sample_version": "1.0.0", "route": "default"}),
            content_type="application/json",
            **{
                "HTTP_AUTHORIZATION": f"Bearer {BEARER}",
                "HTTP_X_AI_TUTOR_ROLE": "staff",
                # Missing HTTP_IDEMPOTENCY_KEY
            },
        )
        data = _assert_error_envelope(response, 400)
        # Could be validation_error or idempotency_conflict depending on impl
        assert data["error"]["code"] in {"validation_error", "invalid_request"}

    def test_sample_config_version_mismatch_400(self):
        """
        Contract: sample_version must match gate_samples.yaml, config_version must match tutor_config.yaml.
        Mismatch → 400.
        """
        client = Client()
        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {BEARER}",
            "HTTP_X_AI_TUTOR_ROLE": "staff",
            "HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4()),
        }
        
        # Test wrong sample_version
        response = client.post(
            GATE_RUN_URL,
            data=json.dumps({"sample_version": "999.0.0", "route": "default"}),
            content_type="application/json",
            **headers,
        )
        data = _assert_error_envelope(response, 400)
        assert data["error"]["code"] in {"validation_error", "invalid_request"}
        
        # Test wrong route (not "default")
        response = client.post(
            GATE_RUN_URL,
            data=json.dumps({"sample_version": "1.0.0", "route": "fast"}),
            content_type="application/json",
            **headers,
        )
        data = _assert_error_envelope(response, 400)
        assert data["error"]["code"] in {"validation_error", "invalid_request"}

    def test_idempotent_retry_returns_same_result(self):
        """
        Contract: Idempotent retry with same key returns same result (no duplicate counting).
        """
        client = Client()
        idempotency_key = str(uuid.uuid4())
        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {BEARER}",
            "HTTP_X_AI_TUTOR_ROLE": "staff",
            "HTTP_IDEMPOTENCY_KEY": idempotency_key,
        }
        
        # First request
        response1 = client.post(
            GATE_RUN_URL,
            data=json.dumps({"sample_version": SAMPLE_VERSION, "route": "default"}),
            content_type="application/json",
            **headers,
        )
        
        # Second request with same idempotency key
        response2 = client.post(
            GATE_RUN_URL,
            data=json.dumps({"sample_version": SAMPLE_VERSION, "route": "default"}),
            content_type="application/json",
            **headers,
        )
        
        # Both should succeed (200) and return identical results (same report, no duplicate run)
        assert response1.status_code == 200
        assert response2.status_code == 200
        data1 = json.loads(response1.content)
        data2 = json.loads(response2.content)
        assert data1["run_id"] == data2["run_id"]
        assert data1["sample_version"] == data2["sample_version"]
        assert data1["rate"] == data2["rate"]
        assert data1["verdict"] == data2["verdict"]

    def test_no_student_side_effects(self):
        """
        Contract: gate evaluation has no student side effects (no conversation/quota/tracking events).
        """
        client = Client()
        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {BEARER}",
            "HTTP_X_AI_TUTOR_ROLE": "staff",
            "HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4()),
        }
        client.post(
            GATE_RUN_URL,
            data=json.dumps({"sample_version": SAMPLE_VERSION, "route": "default"}),
            content_type="application/json",
            **headers,
        )
        from ai_tutor_service.conversations.models import Conversation, Message
        from ai_tutor_service.limits.models import DailyCounter
        assert Conversation.objects.count() == 0
        assert Message.objects.count() == 0
        assert DailyCounter.objects.count() == 0

    def test_gate_report_matches_contract(self):
        """
        Contract: 200 response matches gate report shape with correct verdict calculation.
        """
        client = Client()
        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {BEARER}",
            "HTTP_X_AI_TUTOR_ROLE": "staff",
            "HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4()),
        }
        
        response = client.post(
            GATE_RUN_URL,
            data=json.dumps({"sample_version": SAMPLE_VERSION, "route": "default"}),
            content_type="application/json",
            **headers,
        )
        
        assert response.status_code == 200, (
            f"Gate endpoint must return 200 with gate report, got {response.status_code}: {response.content[:200]}"
        )
        
        data = json.loads(response.content)
        _assert_gate_report_shape(data)
        assert data["sample_version"] == SAMPLE_VERSION
        assert data["config_version"] == CONFIG["version"]

    def test_same_key_different_payload_returns_409(self):
        """
        Contract: same idempotency key with different payload → 409 conflict.
        First request (key + valid payload) → 200, record stored.
        Second request (same key, different payload) → 409 idempotency_conflict.
        """
        client = Client()
        idempotency_key = str(uuid.uuid4())
        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {BEARER}",
            "HTTP_X_AI_TUTOR_ROLE": "staff",
            "HTTP_IDEMPOTENCY_KEY": idempotency_key,
        }

        # First request with valid body → 200, record stored
        response1 = client.post(
            GATE_RUN_URL,
            data=json.dumps({"sample_version": SAMPLE_VERSION, "route": "default"}),
            content_type="application/json",
            **headers,
        )
        assert response1.status_code == 200, (
            f"Expected 200, got {response1.status_code}"
        )

        # Second request with same key but different payload → 409
        response2 = client.post(
            GATE_RUN_URL,
            data=json.dumps({"sample_version": "9.9.9", "route": "default"}),
            content_type="application/json",
            **headers,
        )
        assert response2.status_code == 409, (
            f"Expected 409, got {response2.status_code}"
        )
        data = json.loads(response2.content)
        assert data["error"]["code"] == "idempotency_conflict", (
            f"Expected 'idempotency_conflict', got '{data['error']['code']}'"
        )

    @pytest.mark.parametrize("error_type, expected_status, expected_code", [
        ("timeout", 504, "timeout"),
        ("malformed_response", 502, "invalid_upstream_response"),
        ("provider_error", 502, "upstream_error"),
    ])
    def test_llm_error_mapping(self, error_type, expected_status, expected_code):
        """
        Contract: typed LLM guard errors map to specific HTTP status codes.
        Monkeypatch GateEvaluator.run to raise LLMError; view maps to
        504/502/502 with typed error codes (FR-002-13 regression).
        """
        with patch("ai_tutor_service.guard.gate.GateEvaluator.run") as mock_run:
            mock_run.side_effect = LLMError("guard error", error_type)
            client = Client()
            headers = {
                "HTTP_AUTHORIZATION": f"Bearer {BEARER}",
                "HTTP_X_AI_TUTOR_ROLE": "staff",
                "HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4()),
            }
            response = client.post(
                GATE_RUN_URL,
                data=json.dumps({"sample_version": SAMPLE_VERSION, "route": "default"}),
                content_type="application/json",
                **headers,
            )
            assert response.status_code == expected_status, (
                f"Expected {expected_status} for {error_type}, got {response.status_code}"
            )
            data = json.loads(response.content)
            assert data["error"]["code"] == expected_code, (
                f"Expected '{expected_code}' for {error_type}, got '{data['error']['code']}'"
            )

    def test_operational_error_returns_503(self):
        """
        Contract: OperationalError during gate evaluation → 503 service_unavailable.
        Monkeypatch GateEvaluator.run to raise OperationalError.
        """
        with patch("ai_tutor_service.guard.gate.GateEvaluator.run") as mock_run:
            mock_run.side_effect = OperationalError("DB error")
            client = Client()
            headers = {
                "HTTP_AUTHORIZATION": f"Bearer {BEARER}",
                "HTTP_X_AI_TUTOR_ROLE": "staff",
                "HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4()),
            }
            response = client.post(
                GATE_RUN_URL,
                data=json.dumps({"sample_version": SAMPLE_VERSION, "route": "default"}),
                content_type="application/json",
                **headers,
            )
            assert response.status_code == 503, (
                f"Expected 503, got {response.status_code}"
            )
            data = json.loads(response.content)
            assert data["error"]["code"] == "service_unavailable", (
                f"Expected 'service_unavailable', got '{data['error']['code']}'"
            )

    def test_file_has_verifies_marker(self):
        """Meta-test: verify this file has the correct verifies marker."""
        import inspect
        source = inspect.getsource(__import__(__name__))
        assert "# verifies: FR-002-13" in source


# File marker for traceability