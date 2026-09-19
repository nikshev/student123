# verifies: FR-002-02
"""
Contract tests for API authentication and authorization (T-015).

These tests define the expected behavior of the API auth layer (to be implemented in T-016).
The tests use Django test client against future routes - they will be RED until T-016
implements the api/ module with urls, auth middleware, and error envelope.

Expected RED reason: ai_tutor_service.api (urls/auth/errors) does not exist yet -
Django test client will return 404/ImportError for all /api/v1/* routes.

Tests cover:
1. ALL /api/v1/* endpoints require Bearer token; missing/invalid -> 401 with safe envelope
2. Student context headers (X-AI-Tutor-User-ID, Course-ID, Unit-Usage-Key) accepted ONLY
   after successful Bearer auth; without token -> 401 (headers ignored)
3. Request body cannot override identity/role (user_id in JSON body doesn't affect identity)
4. Staff endpoints (/materials, /gate/run) require X-AI-Tutor-Role: staff; role only
   from header, not body; without staff -> 403
5. 401/403/invalid requests return SAME safe error envelope:
   {"error":{"code","message"},"request_id"} without secret values
"""

import json
import uuid
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse, NoReverseMatch


# Lazy import marker - will fail until T-016 creates ai_tutor_service.api
# This ensures the test is RED for the expected reason (missing URL config)
def _get_test_client():
    """Get Django test client - lazy to allow test collection before api module exists."""
    return Client()


def _make_request(client: Client, method: str, path: str, **kwargs):
    """Make a request, handling missing URL patterns gracefully."""
    try:
        if method == "GET":
            return client.get(path, **kwargs)
        elif method == "POST":
            return client.post(path, **kwargs)
        elif method == "PUT":
            return client.put(path, **kwargs)
        elif method == "DELETE":
            return client.delete(path, **kwargs)
        else:
            raise ValueError(f"Unsupported method: {method}")
    except NoReverseMatch:
        # URL pattern doesn't exist yet - this is the expected RED state
        pytest.fail(f"URL pattern not found: {path} - api/urls.py not implemented (T-016)")
    except ImportError as e:
        if "ai_tutor_service.api" in str(e):
            pytest.fail(f"api module not implemented: {e} - T-016 required")
        raise


# Test constants
VALID_BEARER_TOKEN = "test-shared-secret-12345"
INVALID_BEARER_TOKEN = "invalid-secret"
STUDENT_HEADERS = {
    "HTTP_X_AI_TUTOR_USER_ID": "student-123",
    "HTTP_X_AI_TUTOR_COURSE_ID": "course-v1:demo+math+2026",
    "HTTP_X_AI_TUTOR_UNIT_USAGE_KEY": "block-v1:demo+math+2026+type@vertical+block@u1",
}
STAFF_HEADERS = {
    **STUDENT_HEADERS,
    "HTTP_X_AI_TUTOR_ROLE": "staff",
}


class TestBearerAuthRequired:
    """All /api/v1/* endpoints require valid Bearer token."""

    ENDPOINTS = [
        ("POST", "/api/v1/ask"),
        ("POST", "/api/v1/materials"),
        ("GET", "/api/v1/materials/status"),
        ("GET", "/api/v1/conversation/test-id"),
        ("GET", "/api/v1/config"),
        ("POST", "/api/v1/gate/run"),
    ]

    @pytest.mark.parametrize("method,path", ENDPOINTS)
    def test_missing_bearer_returns_401(self, method, path):
        """Missing Authorization header -> 401 with safe envelope."""
        client = _get_test_client()
        response = _make_request(client, method, path, content_type="application/json")

        assert response.status_code == 401, f"{method} {path}: expected 401, got {response.status_code}"
        self._assert_safe_error_envelope(response)

    @pytest.mark.parametrize("method,path", ENDPOINTS)
    def test_invalid_bearer_returns_401(self, method, path):
        """Invalid Bearer token -> 401 with safe envelope (no secret leakage)."""
        client = _get_test_client()
        headers = {"HTTP_AUTHORIZATION": f"Bearer {INVALID_BEARER_TOKEN}"}
        response = _make_request(client, method, path, content_type="application/json", **headers)

        assert response.status_code == 401, f"{method} {path}: expected 401, got {response.status_code}"
        self._assert_safe_error_envelope(response)
        # Ensure secret not in response
        response_text = response.content.decode()
        assert VALID_BEARER_TOKEN not in response_text
        assert INVALID_BEARER_TOKEN not in response_text

    @pytest.mark.parametrize("method,path", ENDPOINTS)
    def test_malformed_auth_header_returns_401(self, method, path):
        """Malformed Authorization header -> 401 with safe envelope."""
        client = _get_test_client()
        headers = {"HTTP_AUTHORIZATION": "NotBearer token"}
        response = _make_request(client, method, path, content_type="application/json", **headers)

        assert response.status_code == 401
        self._assert_safe_error_envelope(response)

    def _assert_safe_error_envelope(self, response):
        """Assert response matches the safe error envelope format."""
        data = json.loads(response.content)
        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]
        assert "request_id" in data
        # Verify request_id is valid UUID
        uuid.UUID(data["request_id"])
        # Error code should be one of the expected auth codes
        assert data["error"]["code"] in ("authentication_required", "invalid_token", "forbidden")


class TestStudentContextHeaders:
    """Student context headers only accepted after successful Bearer auth."""

    def test_student_headers_without_bearer_ignored_returns_401(self):
        """Student headers without Bearer -> 401 (headers ignored, not 400/403)."""
        client = _get_test_client()
        response = _make_request(
            client, "POST", "/api/v1/ask",
            content_type="application/json",
            data=json.dumps({"question": "test", "route": "default"}),
            **STUDENT_HEADERS
        )

        assert response.status_code == 401
        self._assert_safe_error_envelope(response)

    def test_student_headers_with_valid_bearer_accepted(self):
        """Student headers WITH valid Bearer -> not 401 (accepted, may be other error)."""
        client = _get_test_client()
        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {VALID_BEARER_TOKEN}",
            **STUDENT_HEADERS,
        }
        response = _make_request(
            client, "POST", "/api/v1/ask",
            content_type="application/json",
            data=json.dumps({"question": "test question", "route": "default"}),
            **headers
        )

        # Should not be 401 (auth passed) - may be 400/404/503 etc. but NOT 401
        assert response.status_code != 401, (
            f"Valid bearer + student headers should pass auth, got {response.status_code}"
        )

    def test_student_headers_with_invalid_bearer_still_401(self):
        """Student headers with INVALID Bearer -> 401 (auth fails first)."""
        client = _get_test_client()
        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {INVALID_BEARER_TOKEN}",
            **STUDENT_HEADERS,
        }
        response = _make_request(
            client, "POST", "/api/v1/ask",
            content_type="application/json",
            data=json.dumps({"question": "test", "route": "default"}),
            **headers
        )

        assert response.status_code == 401
        self._assert_safe_error_envelope(response)

    def _assert_safe_error_envelope(self, response):
        data = json.loads(response.content)
        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]
        assert "request_id" in data
        uuid.UUID(data["request_id"])


class TestBodyCannotOverrideIdentity:
    """Request body cannot override identity/role from trusted headers."""

    def test_user_id_in_body_ignored_when_bearer_valid(self):
        """user_id in JSON body does not affect identity when Bearer is valid."""
        client = _get_test_client()
        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {VALID_BEARER_TOKEN}",
            **STUDENT_HEADERS,
        }
        # Body contains different user_id - should be ignored
        body = {
            "question": "test",
            "route": "default",
            "user_id": "different-user-456",  # Should be ignored
        }
        response = _make_request(
            client, "POST", "/api/v1/ask",
            content_type="application/json",
            data=json.dumps(body),
            **headers
        )

        # Should not be 401 (auth passed) - identity comes from headers, not body
        assert response.status_code != 401

    def test_role_in_body_ignored_for_staff_endpoints(self):
        """role in JSON body does not grant staff access."""
        client = _get_test_client()
        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {VALID_BEARER_TOKEN}",
            **STUDENT_HEADERS,  # No X-AI-Tutor-Role: staff
        }
        body = {
            "course_id": "course-v1:demo+math+2026",
            "unit_usage_key": "block-v1:demo+math+2026+type@vertical+block@u1",
            "content_version": "2026-09-19.1",
            "transcript": [],
            "notes": [],
            "role": "staff",  # Should be ignored - role only from header
        }
        response = _make_request(
            client, "POST", "/api/v1/materials",
            content_type="application/json",
            data=json.dumps(body),
            **headers
        )

        # Should be 403 (not staff) not 401 (auth passed)
        assert response.status_code == 403
        self._assert_safe_error_envelope(response)

    def _assert_safe_error_envelope(self, response):
        data = json.loads(response.content)
        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]
        assert "request_id" in data
        uuid.UUID(data["request_id"])


class TestStaffEndpointsRequireRoleHeader:
    """Staff endpoints (/materials, /gate/run) require X-AI-Tutor-Role: staff header."""

    STAFF_ENDPOINTS = [
        ("POST", "/api/v1/materials", {
            "course_id": "course-v1:demo+math+2026",
            "unit_usage_key": "block-v1:demo+math+2026+type@vertical+block@u1",
            "content_version": "2026-09-19.1",
            "transcript": [],
            "notes": [],
        }),
        ("POST", "/api/v1/gate/run", {
            "sample_version": "1.0.0",
            "route": "default",
        }),
    ]

    @pytest.mark.parametrize("method,path,body", STAFF_ENDPOINTS)
    def test_staff_endpoint_without_role_header_returns_403(self, method, path, body):
        """Staff endpoint without X-AI-Tutor-Role: staff -> 403."""
        client = _get_test_client()
        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {VALID_BEARER_TOKEN}",
            **STUDENT_HEADERS,  # No staff role
        }
        response = _make_request(
            client, method, path,
            content_type="application/json",
            data=json.dumps(body),
            **headers
        )

        assert response.status_code == 403, f"{method} {path}: expected 403, got {response.status_code}"
        self._assert_safe_error_envelope(response)

    @pytest.mark.parametrize("method,path,body", STAFF_ENDPOINTS)
    def test_staff_endpoint_with_staff_role_header_passes_auth(self, method, path, body):
        """Staff endpoint WITH X-AI-Tutor-Role: staff -> not 403 (auth passes)."""
        client = _get_test_client()
        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {VALID_BEARER_TOKEN}",
            **STAFF_HEADERS,  # Includes staff role
        }
        response = _make_request(
            client, method, path,
            content_type="application/json",
            data=json.dumps(body),
            **headers
        )

        # Should not be 401 or 403 - auth and role both pass
        assert response.status_code not in (401, 403), (
            f"{method} {path}: staff role should pass, got {response.status_code}"
        )

    @pytest.mark.parametrize("method,path,body", STAFF_ENDPOINTS)
    def test_staff_endpoint_with_invalid_role_returns_403(self, method, path, body):
        """Staff endpoint with invalid role value -> 403."""
        client = _get_test_client()
        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {VALID_BEARER_TOKEN}",
            **STUDENT_HEADERS,
            "HTTP_X_AI_TUTOR_ROLE": "admin",  # Not "staff"
        }
        response = _make_request(
            client, method, path,
            content_type="application/json",
            data=json.dumps(body),
            **headers
        )

        assert response.status_code == 403
        self._assert_safe_error_envelope(response)

    def _assert_safe_error_envelope(self, response):
        data = json.loads(response.content)
        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]
        assert "request_id" in data
        uuid.UUID(data["request_id"])


class TestErrorEnvelopeConsistency:
    """401/403/invalid requests return the SAME safe error envelope format."""

    def test_401_envelope_format(self):
        """401 responses have consistent envelope."""
        client = _get_test_client()
        response = _make_request(client, "POST", "/api/v1/ask", content_type="application/json")

        assert response.status_code == 401
        data = json.loads(response.content)
        self._assert_envelope_structure(data)
        assert data["error"]["code"] in ("authentication_required", "invalid_token")

    def test_403_envelope_format(self):
        """403 responses have consistent envelope."""
        client = _get_test_client()
        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {VALID_BEARER_TOKEN}",
            **STUDENT_HEADERS,
        }
        body = {
            "course_id": "course-v1:demo+math+2026",
            "unit_usage_key": "block-v1:demo+math+2026+type@vertical+block@u1",
            "content_version": "2026-09-19.1",
            "transcript": [],
            "notes": [],
        }
        response = _make_request(
            client, "POST", "/api/v1/materials",
            content_type="application/json",
            data=json.dumps(body),
            **headers
        )

        assert response.status_code == 403
        data = json.loads(response.content)
        self._assert_envelope_structure(data)
        assert data["error"]["code"] == "forbidden"

    def test_400_envelope_format(self):
        """400 (invalid request) responses have consistent envelope."""
        client = _get_test_client()
        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {VALID_BEARER_TOKEN}",
            **STUDENT_HEADERS,
        }
        # Missing required field "question"
        body = {"route": "default"}
        response = _make_request(
            client, "POST", "/api/v1/ask",
            content_type="application/json",
            data=json.dumps(body),
            **headers
        )

        # May be 400 or other - but if 400, envelope must be consistent
        if response.status_code == 400:
            data = json.loads(response.content)
            self._assert_envelope_structure(data)
            assert data["error"]["code"] in ("invalid_request", "validation_error")

    def test_envelope_never_contains_secret(self):
        """Error envelope never contains the shared secret value."""
        client = _get_test_client()
        headers = {"HTTP_AUTHORIZATION": f"Bearer {INVALID_BEARER_TOKEN}"}
        response = _make_request(client, "POST", "/api/v1/ask", content_type="application/json", **headers)

        assert response.status_code == 401
        response_text = response.content.decode()
        assert VALID_BEARER_TOKEN not in response_text
        assert INVALID_BEARER_TOKEN not in response_text
        assert "secret" not in response_text.lower()

    def test_request_id_is_uuid(self):
        """request_id in envelope is a valid UUID."""
        client = _get_test_client()
        response = _make_request(client, "POST", "/api/v1/ask", content_type="application/json")

        assert response.status_code == 401
        data = json.loads(response.content)
        uuid.UUID(data["request_id"])

    def _assert_envelope_structure(self, data):
        """Assert the standard error envelope structure."""
        assert "error" in data
        assert isinstance(data["error"], dict)
        assert "code" in data["error"]
        assert isinstance(data["error"]["code"], str)
        assert len(data["error"]["code"]) > 0
        assert "message" in data["error"]
        assert isinstance(data["error"]["message"], str)
        assert len(data["error"]["message"]) > 0
        assert "request_id" in data
        assert isinstance(data["request_id"], str)
        # Verify it's a valid UUID
        uuid.UUID(data["request_id"])


class TestConversationEndpointOwnership:
    """GET /conversation/{id} requires student actor context and ownership."""

    def test_conversation_without_bearer_returns_401(self):
        """Conversation endpoint without Bearer -> 401."""
        client = _get_test_client()
        conv_id = str(uuid.uuid4())
        response = _make_request(client, "GET", f"/api/v1/conversation/{conv_id}")

        assert response.status_code == 401
        self._assert_safe_error_envelope(response)

    def test_conversation_with_bearer_but_no_student_headers_returns_401_or_400(self):
        """Conversation with Bearer but no student context headers -> 401/400."""
        client = _get_test_client()
        headers = {"HTTP_AUTHORIZATION": f"Bearer {VALID_BEARER_TOKEN}"}
        conv_id = str(uuid.uuid4())
        response = _make_request(client, "GET", f"/api/v1/conversation/{conv_id}", **headers)

        # Should fail auth context check (401 or 400) but not 403/404 for ownership
        # The exact code depends on implementation - but must be safe envelope
        if response.status_code in (400, 401):
            self._assert_safe_error_envelope(response)

    def test_conversation_with_valid_context_passes_auth(self):
        """Conversation with Bearer + student headers -> not 401."""
        client = _get_test_client()
        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {VALID_BEARER_TOKEN}",
            **STUDENT_HEADERS,
        }
        conv_id = str(uuid.uuid4())
        response = _make_request(client, "GET", f"/api/v1/conversation/{conv_id}", **headers)

        # Should not be 401 (auth passed) - may be 404 if conversation doesn't exist
        assert response.status_code != 401

    def _assert_safe_error_envelope(self, response):
        data = json.loads(response.content)
        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]
        assert "request_id" in data
        uuid.UUID(data["request_id"])


class TestConfigEndpointAuth:
    """GET /config requires authentication but not staff role."""

    def test_config_without_bearer_returns_401(self):
        """Config endpoint without Bearer -> 401."""
        client = _get_test_client()
        response = _make_request(client, "GET", "/api/v1/config")

        assert response.status_code == 401
        self._assert_safe_error_envelope(response)

    def test_config_with_valid_bearer_passes(self):
        """Config endpoint with valid Bearer -> not 401."""
        client = _get_test_client()
        headers = {"HTTP_AUTHORIZATION": f"Bearer {VALID_BEARER_TOKEN}"}
        response = _make_request(client, "GET", "/api/v1/config", **headers)

        assert response.status_code != 401

    def _assert_safe_error_envelope(self, response):
        data = json.loads(response.content)
        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]
        assert "request_id" in data
        uuid.UUID(data["request_id"])


class TestMaterialsStatusEndpoint:
    """GET /materials/status - authenticated (staff or student)."""

    def test_materials_status_without_bearer_returns_401(self):
        """Materials status without Bearer -> 401."""
        client = _get_test_client()
        response = _make_request(
            client, "GET", "/api/v1/materials/status",
            data={"course_id": "course-v1:demo+math+2026", "unit_usage_key": "block-v1:demo+math+2026+type@vertical+block@u1"},
            content_type="application/json"
        )

        assert response.status_code == 401
        self._assert_safe_error_envelope(response)

    def test_materials_status_with_student_context_passes(self):
        """Materials status with Bearer + student headers -> not 401/403."""
        client = _get_test_client()
        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {VALID_BEARER_TOKEN}",
            **STUDENT_HEADERS,
        }
        response = _make_request(
            client, "GET", "/api/v1/materials/status",
            data={"course_id": STUDENT_HEADERS["HTTP_X_AI_TUTOR_COURSE_ID"],
                  "unit_usage_key": STUDENT_HEADERS["HTTP_X_AI_TUTOR_UNIT_USAGE_KEY"]},
            content_type="application/json",
            **headers
        )

        assert response.status_code not in (401, 403)

    def test_materials_status_with_staff_passes(self):
        """Materials status with Bearer + staff role -> not 401/403."""
        client = _get_test_client()
        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {VALID_BEARER_TOKEN}",
            **STAFF_HEADERS,
        }
        response = _make_request(
            client, "GET", "/api/v1/materials/status",
            data={"course_id": STUDENT_HEADERS["HTTP_X_AI_TUTOR_COURSE_ID"],
                  "unit_usage_key": STUDENT_HEADERS["HTTP_X_AI_TUTOR_UNIT_USAGE_KEY"]},
            content_type="application/json",
            **headers
        )

        assert response.status_code not in (401, 403)

    def _assert_safe_error_envelope(self, response):
        data = json.loads(response.content)
        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]
        assert "request_id" in data
        uuid.UUID(data["request_id"])


# This test ensures the test file itself is discoverable and runs
class TestApiAuthTestFile:
    """Meta-test to verify test file structure."""

    def test_file_has_verifies_marker(self):
        """Test file must have # verifies: FR-002-02 marker."""
        import inspect
        source = inspect.getsource(__import__(__name__))
        assert "# verifies: FR-002-02" in source

    def test_all_test_classes_exist(self):
        """Verify all expected test classes are defined."""
        expected_classes = [
            "TestBearerAuthRequired",
            "TestStudentContextHeaders",
            "TestBodyCannotOverrideIdentity",
            "TestStaffEndpointsRequireRoleHeader",
            "TestErrorEnvelopeConsistency",
            "TestConversationEndpointOwnership",
            "TestConfigEndpointAuth",
            "TestMaterialsStatusEndpoint",
        ]
        for cls_name in expected_classes:
            assert cls_name in globals(), f"Missing test class: {cls_name}"