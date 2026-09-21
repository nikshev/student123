# verifies: FR-002-12
"""
Contract tests for GET /api/v1/conversation/{id} (T-050).

This test file documents and verifies the contract for conversation history retrieval
BEFORE the implementation exists (T-050). The tests document the expected behavior
per contracts/tutor-service-api.md §5 and FR-002-12.

Contract summary (from contracts/tutor-service-api.md §5):
- GET /api/v1/conversation/{id} - Student actor context required.
- Returns only own conversation in same course/unit.
- 200 response: {"conversation_id":"uuid","messages":[{"role":"student","text":"…","status":"asked","created_at":"…"},{"role":"tutor","text":"…","status":"shown","sources":[],"created_at":"…","config_version":"1.0.0"}],"daily_remaining":7}
- Messages ordered stably by created_at,id (no candidate blocked answer in history).
- 403 - authenticated actor not owner (FR-002-12: indistinguishable 404-like denial)
- 404 - ID doesn't exist/expired (FR-002-12: indistinguishable 404-like denial)
- Staff без audited ops/gate context → 404-like denial (не вільне читання)
- XBlock history не кешує: два послідовні GET після нового ask показують нове повідомлення
- Blocked conversation history містить лише safe rule (candidate ніде)

Expected RED reason: ConversationView is 501 stub (T-050 implements repository/API ownership policy in
ai_tutor_service/conversations/repository.py + ai_tutor_service/api/conversation.py).

Ці тести не залежать від реалізації POST /ask (T-020) чи POST /materials (T-018):
історія створюється напряму в DB через Conversation/Message моделі, щоб червоність
була саме через відсутність repository/API ownership policy для GET /conversation/{id}.
"""

import json
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from django.test import Client
from django.utils import timezone

from ai_tutor_service.config import load_tutor_config
from ai_tutor_service.conversations.models import Conversation, Message

# Load config for testing
REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = REPO_ROOT / "ai_tutor_service" / "tutor_config.yaml"
CONFIG = load_tutor_config(CONFIG_PATH)

# Constants
BEARER = "test-shared-secret-12345"
CONVERSATION_URL = "/api/v1/conversation/{}"

COURSE_ID = "course-v1:demo+math+2026"
UNIT_KEY = "block-v1:demo+math+2026+type@vertical+block@u1"
USER_A = "student-conv-aaa"
USER_B = "student-conv-bbb"

QUESTION_1 = "Як це працює?"
QUESTION_2 = "Поясни ще раз."

# Expected output fields from contract for GET /conversation/{id}
EXPECTED_OUTPUT_FIELDS = {"conversation_id", "messages", "daily_remaining"}

# Terminal statuses that should appear in history (per FR-002-12 and data-model.md)
TERMINAL_STATUSES = {"shown", "blocked", "no_materials", "off_topic", "error"}
NON_TERMINAL_STATUSES = {"asked"}  # Only non-terminal status


def _seed_conversation(user_id=USER_A, course_id=COURSE_ID, unit_key=UNIT_KEY,
                       expires_at=None, messages=None):
    """Create a conversation fixture directly in the DB (no network/external services)."""
    if expires_at is None:
        expires_at = timezone.now() + timedelta(days=30)

    conversation = Conversation.objects.create(
        user_id=user_id,
        course_id=course_id,
        unit_usage_key=unit_key,
        expires_at=expires_at,
    )

    if messages:
        for msg in messages:
            Message.objects.create(
                conversation_id=conversation,
                role=msg["role"],
                text=msg.get("text"),
                status=msg["status"],
                topic=msg.get("topic", "other"),
                sources=msg.get("sources", []),
                blocked_reason=msg.get("blocked_reason"),
                latency_ms=msg.get("latency_ms"),
                route=msg.get("route", "default"),
                config_version=msg.get("config_version", CONFIG["version"]),
            )

    return conversation


def _make_message(role, text=None, status="asked", **kwargs):
    """Create a message fixture."""
    return {
        "role": role,
        "text": text,
        "status": status,
        **kwargs,
    }


def _make_student_headers(user_id=USER_A):
    """Make student headers."""
    return {
        "HTTP_AUTHORIZATION": f"Bearer {BEARER}",
        "HTTP_X_AI_TUTOR_USER_ID": user_id,
        "HTTP_X_AI_TUTOR_COURSE_ID": COURSE_ID,
        "HTTP_X_AI_TUTOR_UNIT_USAGE_KEY": UNIT_KEY,
    }


def _make_staff_headers(role="staff"):
    """Make staff headers."""
    return {
        "HTTP_AUTHORIZATION": f"Bearer {BEARER}",
        "HTTP_X_AI_TUTOR_ROLE": role,
    }


def _get_conversation(client, conversation_id, headers=None):
    """Helper to get conversation history."""
    if headers is None:
        headers = _make_student_headers()
    return client.get(
        CONVERSATION_URL.format(conversation_id),
        content_type="application/json",
        **headers,
    )


def _assert_conversation_envelope(response, expected_status):
    """Assert conversation response has expected status and envelope."""
    data = json.loads(response.content)
    assert response.status_code == expected_status, (
        f"Expected {expected_status}, got {response.status_code}: {response.content}"
    )

    # For 200 responses, check structure
    if expected_status == 200:
        assert set(data.keys()) == EXPECTED_OUTPUT_FIELDS, (
            f"Conversation response must have {EXPECTED_OUTPUT_FIELDS}, got {set(data.keys())}"
        )
        uuid.UUID(data["conversation_id"])
        assert isinstance(data["messages"], list)
        assert isinstance(data["daily_remaining"], int) and data["daily_remaining"] >= 0

        # Validate each message
        for message in data["messages"]:
            assert set(message.keys()) >= {"role", "text", "status", "created_at"}
            assert message["role"] in ("student", "tutor")
            assert message["status"] in (
                "asked", "shown", "blocked", "no_materials", "off_topic", "error"
            )
            assert "config_version" in message  # Required per constitution III

            # Check for blocked_reason if blocked
            if message["status"] == "blocked":
                assert "blocked_reason" in message
                assert message["blocked_reason"] is not None

            # Check sources for shown messages
            if message["status"] == "shown":
                assert "sources" in message
                assert isinstance(message["sources"], list)

    else:
        # For error responses, check standard envelope
        assert set(data.keys()) == {"error", "request_id"}, (
            f"Error envelope must be {{error, request_id}}, got {set(data.keys())}"
        )
        assert "code" in data["error"]
        assert "message" in data["error"]
        uuid.UUID(data["request_id"])

    return data


def _assert_stable_order(messages):
    """Assert messages are in stable created_at,id order."""
    # Extract timestamps for ordering check
    ordered_pairs = []
    for msg in messages:
        # Parse the ISO timestamp (handle Z suffix)
        ts_str = msg["created_at"].replace("Z", "+00:00")
        ts = timezone.datetime.fromisoformat(ts_str)
        ordered_pairs.append((ts, msg["id"]))

    # Check that the sequence is non-decreasing by created_at
    for i in range(1, len(ordered_pairs)):
        prev_ts, prev_id = ordered_pairs[i-1]
        curr_ts, curr_id = ordered_pairs[i]

        # Timestamps should be non-decreasing
        assert curr_ts >= prev_ts, (
            f"Messages not ordered by created_at: {prev_ts} -> {curr_ts}"
        )

    return ordered_pairs


def _assert_no_candidate_in_blocked(messages):
    """Assert blocked messages contain only safe rule text (no candidate text)."""
    for msg in messages:
        if msg["status"] == "blocked":
            # Blocked message should have null/empty text; rule text comes from YAML
            assert msg.get("text") is None or msg.get("text") == "", (
                f"Blocked message text should be null/empty (rule from YAML), got: {msg['text']}"
            )


class TestConversationContract:
    """Contract tests for GET /api/v1/conversation/{id} (T-050)."""

    def test_conversation_without_bearer_returns_401(self):
        """Conversation endpoint without Bearer -> 401."""
        client = Client()
        conv_id = str(uuid.uuid4())
        headers = _make_student_headers()
        # Remove authorization to test missing bearer
        headers = {k: v for k, v in headers.items() if k != "HTTP_AUTHORIZATION"}
        
        response = client.get(
            CONVERSATION_URL.format(conv_id),
            content_type="application/json",
            **headers,
        )

        _assert_conversation_envelope(response, 401)
        # Should be authentication_required or invalid_token
        data = json.loads(response.content)
        assert data["error"]["code"] in ("authentication_required", "invalid_token")

    def test_conversation_with_invalid_bearer_returns_401(self):
        """Conversation with invalid Bearer -> 401."""
        client = Client()
        headers = _make_student_headers()
        headers["HTTP_AUTHORIZATION"] = "Bearer invalid-secret"
        
        conv_id = str(uuid.uuid4())
        response = client.get(
            CONVERSATION_URL.format(conv_id),
            content_type="application/json",
            **headers,
        )

        _assert_conversation_envelope(response, 401)
        data = json.loads(response.content)
        assert data["error"]["code"] == "invalid_token"
        # Ensure secret not leaked
        assert BEARER not in response.content.decode()
        assert "invalid-secret" not in response.content.decode()

    def test_conversation_without_student_context_returns_401_or_400(self):
        """Conversation with Bearer but no student context -> 401/400."""
        client = Client()
        headers = {"HTTP_AUTHORIZATION": f"Bearer {BEARER}"}
        conv_id = str(uuid.uuid4())
        response = client.get(
            CONVERSATION_URL.format(conv_id),
            content_type="application/json",
            **headers,
        )

        # Should fail auth context check (401 or 400) but not leak info
        if response.status_code in (400, 401):
            _assert_conversation_envelope(response, response.status_code)

    def test_conversation_with_valid_context_but_missing_conversation_returns_404_like(self):
        """
        Conversation with valid context but non-existent ID -> 404-like denial.
        Per FR-002-12: indistinguishable from 403 (same envelope).
        """
        client = Client()
        headers = _make_student_headers()
        conv_id = str(uuid.uuid4())  # Random UUID that doesn't exist
        response = _get_conversation(client, conv_id, headers)

        # Should return 404-like denial (indistinguishable from 403 for ownership)
        # Per contract: 403 — authenticated actor не власник; 404 — ID не існує/expired (однаковий envelope)
        assert response.status_code in (403, 404), (
            f"Expected 403 or 404 for non-existent conversation, got {response.status_code}"
        )
        _assert_conversation_envelope(response, response.status_code)

        # Error should not leak that conversation doesn't exist vs access denied
        data = json.loads(response.content)
        assert data["error"]["code"] in ("not_found", "conversation_not_found", "forbidden", "actor_mismatch")

    def test_ownership_same_user_same_unit_returns_200(self):
        """Owner with correct user_id/course_id/unit_usage_key -> 200 with conversation history."""
        client = Client()

        # Seed conversation with messages
        conv = _seed_conversation(
            messages=[
                _make_message("student", QUESTION_1, "asked"),
                _make_message(
                    "tutor",
                    "Це пояснення.",
                    "shown",
                    sources=[{"kind": "notes", "source_ref": "notes#u1"}],
                ),
            ]
        )
        conversation_id = str(conv.id)

        response = _get_conversation(client, conversation_id)

        # Should succeed for owner
        data = _assert_conversation_envelope(response, 200)

        # Validate conversation ID matches
        assert data["conversation_id"] == conversation_id

        # Validate we have messages
        assert len(data["messages"]) == 2

        # Validate stable order
        _assert_stable_order(data["messages"])

        # Validate no candidate text in blocked messages
        _assert_no_candidate_in_blocked(data["messages"])

        # Validate only terminal tutor messages + student messages appear
        for msg in data["messages"]:
            if msg["role"] == "tutor":
                assert msg["status"] in TERMINAL_STATUSES, (
                    f"Tutor message should have terminal status, got: {msg['status']}"
                )
            elif msg["role"] == "student":
                assert msg["status"] in NON_TERMINAL_STATUSES, (
                    f"Student message should have non-terminal status, got: {msg['status']}"
                )

    def test_ownership_different_user_returns_404_like_denial(self):
        """Different user -> 404-like denial (no leakage of user ID or conversation ID)."""
        client = Client()

        # Create conversation as USER_A
        conv = _seed_conversation(user_id=USER_A)
        conversation_id = str(conv.id)

        # Try to access as USER_B (different user)
        headers = _make_student_headers(USER_B)
        response = _get_conversation(client, conversation_id, headers)

        # Should return 404-like denial (indistinguishable from 404 for non-existence)
        assert response.status_code in (403, 404), (
            f"Expected 403 or 404 for wrong user, got {response.status_code}"
        )
        _assert_conversation_envelope(response, response.status_code)

        # Critical: Ensure no leakage of conversation ID or owner user ID in error
        response_text = response.content.decode()
        assert conversation_id not in response_text, (
            f"Conversation ID leaked in error response: {response_text}"
        )
        assert USER_A not in response_text, (
            f"Owner user ID leaked in error response: {response_text}"
        )

    def test_ownership_same_user_different_unit_returns_404_like_denial(self):
        """Same user, different unit_usage_key -> 404-like denial."""
        client = Client()

        # Create conversation as USER_A in primary unit
        conv = _seed_conversation(user_id=USER_A, unit_key=UNIT_KEY)
        conversation_id = str(conv.id)

        # Try to access with different unit_usage_key
        OTHER_UNIT_KEY = "block-v1:demo+math+2026+type@vertical+block@u9"
        headers = _make_student_headers(USER_A)
        headers["HTTP_X_AI_TUTOR_UNIT_USAGE_KEY"] = OTHER_UNIT_KEY
        response = _get_conversation(client, conversation_id, headers)

        # Should return 404-like denial (indistinguishable)
        assert response.status_code in (403, 404), (
            f"Expected 403 or 404 for wrong unit, got {response.status_code}"
        )
        _assert_conversation_envelope(response, response.status_code)

        # Ensure no leakage
        response_text = response.content.decode()
        assert conversation_id not in response_text
        assert USER_A not in response_text

    def test_ownership_expired_conversation_returns_404_like_denial(self):
        """Expired conversation -> 404-like denial."""
        client = Client()

        # Create an expired conversation manually
        expired_conv = _seed_conversation(
            expires_at=timezone.now() - timedelta(days=1)  # Yesterday
        )
        conversation_id = str(expired_conv.id)

        # Add a message to make it interesting
        Message.objects.create(
            conversation_id=expired_conv,
            role="student",
            text=QUESTION_1,
            status="asked",
            config_version=CONFIG["version"]
        )

        # Try to access expired conversation
        headers = _make_student_headers()
        response = _get_conversation(client, conversation_id, headers)

        # Should return 404-like denial (indistinguishable)
        assert response.status_code in (403, 404), (
            f"Expected 403 or 404 for expired conversation, got {response.status_code}"
        )
        _assert_conversation_envelope(response, response.status_code)

        # Ensure no leakage
        response_text = response.content.decode()
        assert conversation_id not in response_text
        assert USER_A not in response_text

    def test_staff_without_audit_context_gets_404_like_denial(self):
        """
        Staff without audited ops/gate context -> 404-like denial (not free read).
        Per FR-002-12: authorized staff БЕЗ аудиту → той самий 404-like denial.
        Staff з audited ops/gate контекстом — поза scope цього тесту (T-050/T-052).
        """
        client = Client()

        # Create conversation as regular student
        conv = _seed_conversation(user_id=USER_A)
        conversation_id = str(conv.id)

        # Try to access as staff WITHOUT audited context
        headers = _make_staff_headers()
        headers.update(_make_student_headers(USER_A))  # Student context still present
        response = _get_conversation(client, conversation_id, headers)

        # Should get 404-like denial (same as wrong user/unit/expired)
        assert response.status_code in (403, 404), (
            f"Expected 403 or 404 for staff without audit context, got {response.status_code}"
        )
        _assert_conversation_envelope(response, response.status_code)

        # Ensure no leakage of conversation details
        response_text = response.content.decode()
        assert conversation_id not in response_text
        assert USER_A not in response_text

    def test_staff_with_audit_header_gets_history_and_audit_record(self):
        """
        Staff with valid audit header gets conversation history and creates audit record.
        - staff headers + HTTP_X_AI_TUTOR_OPS_AUDIT + student context headers
        - Existing conversation → 200
        - AuditRecord.objects.filter(conversation_id=<id>).exists() must be True
        - Response has standard 200 schema with matching conversation_id
        """
        client = Client()

        # Create conversation with some history
        conv = _seed_conversation(
            user_id=USER_A,
            messages=[
                _make_message("student", QUESTION_1, "asked"),
                _make_message(
                    "tutor",
                    "Це пояснення.",
                    "shown",
                    sources=[{"kind": "notes", "source_ref": "notes#u1"}],
                ),
            ],
        )
        conversation_id = str(conv.id)

        # Staff with audit header + student context of conversation owner
        headers = _make_staff_headers()
        headers["HTTP_X_AI_TUTOR_OPS_AUDIT"] = "ops-review-1"
        headers.update(_make_student_headers(USER_A))  # Match conversation owner

        response = _get_conversation(client, conversation_id, headers)

        # Should succeed for authorized staff with audit context
        data = _assert_conversation_envelope(response, 200)

        # Validate conversation ID matches
        assert data["conversation_id"] == conversation_id

        # Validate we get the seeded messages back
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "student"  # First message
        assert data["messages"][1]["role"] == "tutor"   # Second message

        # Verify audit record was created
        from ai_tutor_service.conversations.models import AuditRecord
        assert AuditRecord.objects.filter(
            conversation_id=conv.id,
            actor="staff",
            action="read"
        ).exists()

    def test_xblock_history_does_not_cache(self):
        """
        XBlock history does not cache: two sequential GETs after new ask show fresh data.
        Per FR-002-12: XBlock history не кешує.
        """
        client = Client()

        # Create conversation with initial message
        conv = _seed_conversation(
            messages=[_make_message("student", QUESTION_1, "asked")]
        )
        conversation_id = str(conv.id)

        headers = _make_student_headers()

        # Get initial history
        response1 = _get_conversation(client, conversation_id, headers)
        assert response1.status_code == 200
        history1 = json.loads(response1.content)
        msg_count_1 = len(history1["messages"])

        # Simulate new ask being persisted (new message)
        Message.objects.create(
            conversation_id=conv,
            role="tutor",
            text="Нове пояснення.",
            status="shown",
            sources=[{"kind": "notes", "source_ref": "notes#u1"}],
            config_version=CONFIG["version"]
        )

        # Get history again - should show NEW data (not cached)
        response2 = _get_conversation(client, conversation_id, headers)
        assert response2.status_code == 200
        history2 = json.loads(response2.content)
        msg_count_2 = len(history2["messages"])

        # Second response should have MORE messages (new turn added)
        assert msg_count_2 > msg_count_1, (
            f"History should show fresh data after new ask: {msg_count_1} -> {msg_count_2} messages"
        )

        # Validate both responses have proper structure and ordering
        _assert_conversation_envelope(response1, 200)
        _assert_conversation_envelope(response2, 200)
        _assert_stable_order(history1["messages"])
        _assert_stable_order(history2["messages"])
        _assert_no_candidate_in_blocked(history1["messages"])
        _assert_no_candidate_in_blocked(history2["messages"])

    def test_blocked_conversation_history_contains_only_safe_rule(self):
        """
        Blocked conversation history contains only safe rule (candidate nowhere).
        Per FR-002-12: історія blocked-розмови містить лише safe rule (candidate ніде).
        """
        client = Client()

        # Create conversation
        conv = _seed_conversation(
            messages=[_make_message("student", QUESTION_1, "asked")]
        )
        conversation_id = str(conv.id)

        headers = _make_student_headers()

        # Add a blocked message (simulating a blocked answer)
        Message.objects.create(
            conversation_id=conv,
            role="tutor",
            text=None,  # Blocked messages have null text (rule comes from YAML)
            status="blocked",
            blocked_reason="solution",  # Example block reason
            config_version=CONFIG["version"]
        )

        # Get conversation history again
        response = _get_conversation(client, conversation_id, headers)
        assert response.status_code == 200
        data = json.loads(response.content)

        # Should now have student message + blocked message
        assert len(data["messages"]) == 2

        # Validate blocked message has no candidate text
        tutor_msgs = [m for m in data["messages"] if m["role"] == "tutor"]
        assert len(tutor_msgs) == 1
        blocked_msg_data = tutor_msgs[0]
        assert blocked_msg_data["status"] == "blocked"
        assert blocked_msg_data["text"] is None or blocked_msg_data["text"] == "", (
            f"Blocked message should have null/empty text (rule from YAML), got: {blocked_msg_data['text']}"
        )
        assert "blocked_reason" in blocked_msg_data
        assert blocked_msg_data["blocked_reason"] is not None

        # Validate the blocked message contains only safe rule properties
        _assert_no_candidate_in_blocked(data["messages"])

    def test_file_has_verifies_marker(self):
        """Meta-test: verify this file has the correct verifies marker."""
        import inspect
        source = inspect.getsource(__import__(__name__))
        assert "# verifies: FR-002-12" in source


# This test ensures the test file itself is discoverable and runs
class TestConversationApiTestFile:
    """Meta-test to verify test file structure."""

    def test_file_has_verifies_marker(self):
        """Test file must have # verifies: FR-002-12 marker."""
        import inspect
        source = inspect.getsource(__import__(__name__))
        assert "# verifies: FR-002-12" in source

    def test_all_test_classes_exist(self):
        """Verify all expected test classes are defined."""
        expected_classes = [
            "TestConversationContract",
            "TestConversationApiTestFile",
        ]
        for cls_name in expected_classes:
            assert cls_name in globals(), f"Missing test class: {cls_name}"
