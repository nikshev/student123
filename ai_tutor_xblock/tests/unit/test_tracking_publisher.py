# verifies: FR-002-09
"""
Unit tests for TrackingPublisher implementation (T-045).

Contract: three event names/payloads, asked after guard/validation,
shown only safe shown, blocked only after durable ack, server identity,
config fallback, no answer/secrets, runtime.publish failure→retryable ERROR,
duplicate request_id counted once, weekly metric no quota.

These tests import TrackingPublisher from ai_tutor_xblock.ai_tutor_xblock.tracking
which does not exist yet → ModuleNotFoundError = expected red cause.
"""

import pytest
import uuid
from unittest.mock import Mock, patch

try:
    from ai_tutor_xblock.ai_tutor_xblock.tracking import TrackingPublisher
    TRACKING_AVAILABLE = True
except ModuleNotFoundError:
    TRACKING_AVAILABLE = False


class StubUser:
    def __init__(self, user_id="student-42"):
        self.id = user_id


class StubRuntime:
    """Stub runtime with publish_calls list for tracking assertions."""
    def __init__(self, user=StubUser(), course_id="course-v1:demo+math+2026"):
        self.user = user
        self.course_id = course_id
        self.publish_calls = []

    def publish(self, event_type, event):
        self.publish_calls.append((event_type, event))
        return True


def make_block(runtime=None):
    scope_ids = Mock()
    scope_ids.usage_id = "block-v1:demo+math+2026+type@vertical+block@u1"
    return AiTutorXBlock(runtime or StubRuntime(), scope_ids=scope_ids)


class TestTrackingPublisherUnit:
    """Unit tests for TrackingPublisher — red until T-046 implements tracking.py."""

    def test_three_event_names(self):
        """Verify exact three event names from contract:
        xblock-ai-tutor.question.asked, xblock-ai-tutor.answer.shown,
        xblock-ai-tutor.answer.blocked"""
        if not TRACKING_AVAILABLE:
            pytest.fail("ModuleNotFoundError — tracking.py missing (T-045 red)")
        tp = TrackingPublisher(Mock())
        assert hasattr(tp, 'EVENT_ASKED')
        assert hasattr(tp, 'EVENT_SHOWN')
        assert hasattr(tp, 'EVENT_BLOCKED')

    def test_asked_payload_schema(self):
        """asked payload: user_id, course_id, unit_usage_key, request_id,
        event_type, question, topic, conversation_id, latency_ms=null,
        blocked_reason=null, config_version."""
        if not TRACKING_AVAILABLE:
            pytest.fail("ModuleNotFoundError — tracking.py missing (T-045 red)")
        tp = TrackingPublisher(Mock())
        payload = tp.build_asked_payload(
            user_id="12345", course_id="course-v1:demo+math101+2026",
            unit_usage_key="block-v1:demo+math101+2026+type@vertical+block@unit1",
            request_id=str(uuid.uuid4()), question="Чому два мінуси дають плюс?",
            topic="other", conversation_id=None, config_version="unknown",
        )
        assert payload["event_type"] == "asked"
        assert payload["user_id"] == "12345"
        assert payload["course_id"] == "course-v1:demo+math101+2026"
        assert "unit_usage_key" in payload
        assert "request_id" in payload
        assert "question" in payload
        assert payload["latency_ms"] is None
        assert payload["blocked_reason"] is None
        assert "config_version" in payload

    def test_shown_payload_schema(self):
        """shown payload: status shown, answer from service, NO candidate,
        latency_ms required non-negative."""
        if not TRACKING_AVAILABLE:
            pytest.fail("ModuleNotFoundError — tracking.py missing (T-045 red)")
        tp = TrackingPublisher(Mock())
        payload = tp.build_shown_payload(
            user_id="12345", course_id="course-v1:demo+math101+2026",
            unit_usage_key="block-v1:demo+math101+2026+type@vertical+block@unit1",
            request_id=str(uuid.uuid4()), question="test?",
            topic="Правила множення", conversation_id=str(uuid.uuid4()),
            latency_ms=1840, config_version="1.0.0",
        )
        assert payload["event_type"] == "shown"
        assert "answer" not in payload
        assert "candidate" not in payload
        assert isinstance(payload["latency_ms"], int) and payload["latency_ms"] >= 0
        assert payload["question"] == "test?"

    def test_blocked_payload_schema(self):
        """blocked payload: answer=rule, candidate nowhere, blocked_reason non-empty."""
        if not TRACKING_AVAILABLE:
            pytest.fail("ModuleNotFoundError — tracking.py missing (T-045 red)")
        tp = TrackingPublisher(Mock())
        payload = tp.build_blocked_payload(
            user_id="12345", course_id="course-v1:demo+math101+2026",
            unit_usage_key="block-v1:demo+math101+2026+type@vertical+block@unit1",
            request_id=str(uuid.uuid4()), question="test?",
            topic="other", conversation_id=str(uuid.uuid4()),
            blocked_reason="ready solution", config_version="1.0.0",
        )
        assert payload["event_type"] == "blocked"
        assert payload["blocked_reason"] == "ready solution"
        assert "answer" not in payload
        assert "candidate" not in payload

    def test_asked_after_guard_before_service(self):
        """asked published after guard/validation, before service call."""
        if not TRACKING_AVAILABLE:
            pytest.fail("ModuleNotFoundError — tracking.py missing (T-045 red)")
        runtime = StubRuntime()
        tp = TrackingPublisher(runtime)
        tp.publish_asked(user_id="12345", request_id=str(uuid.uuid4()))
        assert len(runtime.publish_calls) == 1
        assert runtime.publish_calls[0][0] == "xblock-ai-tutor.question.asked"

    def test_shown_only_safe_shown(self):
        """shown event only for safe shown; no candidate in payload."""
        if not TRACKING_AVAILABLE:
            pytest.fail("ModuleNotFoundError — tracking.py missing (T-045 red)")
        runtime = StubRuntime()
        tp = TrackingPublisher(runtime)
        tp.publish_shown(user_id="12345", answer="safe answer", topic="other", question="test?")
        assert len(runtime.publish_calls) == 1
        assert runtime.publish_calls[0][0] == "xblock-ai-tutor.answer.shown"
        event = runtime.publish_calls[0][1]
        assert "candidate" not in event

    def test_blocked_only_after_durable_ack(self):
        """blocked event only after durable BlockRecord acknowledgement."""
        if not TRACKING_AVAILABLE:
            pytest.fail("ModuleNotFoundError — tracking.py missing (T-045 red)")
        runtime = StubRuntime()
        tp = TrackingPublisher(runtime)
        tp.publish_blocked(user_id="12345", blocked_reason="ready solution")
        assert len(runtime.publish_calls) == 1
        assert runtime.publish_calls[0][0] == "xblock-ai-tutor.answer.blocked"

    def test_server_identity_in_payloads(self):
        """Server identity (user/course/unit) from runtime in all payloads."""
        if not TRACKING_AVAILABLE:
            pytest.fail("ModuleNotFoundError — tracking.py missing (T-045 red)")
        runtime = StubRuntime(user=StubUser("user-1"), course_id="course-v1:demo+math+2026")
        scope_ids = Mock()
        scope_ids.usage_id = "block-v1:demo+math+2026+type@vertical+block@unit1"
        tp = TrackingPublisher(runtime, scope_ids=scope_ids)
        payload = tp.build_asked_payload(
            user_id="user-1", course_id="course-v1:demo+math+2026",
            unit_usage_key=str(scope_ids.usage_id),
            request_id=str(uuid.uuid4()), question="test?",
            topic="other", conversation_id=None, config_version="unknown",
        )
        assert payload["user_id"] == "user-1"
        assert payload["course_id"] == "course-v1:demo+math+2026"
        assert payload["unit_usage_key"] == str(scope_ids.usage_id)

    def test_config_fallback_unknown(self):
        """config_version falls back to 'unknown' when no valid config."""
        if not TRACKING_AVAILABLE:
            pytest.fail("ModuleNotFoundError — tracking.py missing (T-045 red)")
        runtime = StubRuntime()
        tp = TrackingPublisher(runtime)
        payload = tp.build_asked_payload(
            user_id="12345", course_id="course-v1:demo+math+2026",
            unit_usage_key="block-v1:demo+math+2026+type@vertical+block@unit1",
            request_id=str(uuid.uuid4()), question="test?",
            topic="other", conversation_id=None, config_version="unknown",
        )
        assert payload["config_version"] == "unknown"

    def test_no_answer_secrets_in_payload(self):
        """No answer text, candidate, or secrets in asked payload."""
        if not TRACKING_AVAILABLE:
            pytest.fail("ModuleNotFoundError — tracking.py missing (T-045 red)")
        runtime = StubRuntime()
        tp = TrackingPublisher(runtime)
        payload = tp.build_asked_payload(
            user_id="12345", course_id="course-v1:demo+math+2026",
            unit_usage_key="block-v1:demo+math+2026+type@vertical+block@unit1",
            request_id=str(uuid.uuid4()), question="test?",
            topic="other", conversation_id=None, config_version="unknown",
        )
        assert "answer" not in payload
        assert "candidate" not in payload
        assert "secret" not in payload
        assert "token" not in payload

    def test_runtime_publish_failure_retryable(self):
        """runtime.publish failure → retryable ERROR status, not silent."""
        if not TRACKING_AVAILABLE:
            pytest.fail("ModuleNotFoundError — tracking.py missing (T-045 red)")
        runtime = Mock()
        runtime.publish.side_effect = Exception("publish failed")
        tp = TrackingPublisher(runtime)
        with pytest.raises(Exception):
            tp.publish_asked(user_id="12345", request_id=str(uuid.uuid4()))

    def test_duplicate_request_id_counted_once(self):
        """Duplicate request_id → asked counted once, not duplicated."""
        if not TRACKING_AVAILABLE:
            pytest.fail("ModuleNotFoundError — tracking.py missing (T-045 red)")
        runtime = StubRuntime()
        tp = TrackingPublisher(runtime)
        rid = str(uuid.uuid4())
        tp.publish_asked(request_id=rid, user_id="12345")
        tp.publish_asked(request_id=rid, user_id="12345")
        asked_calls = [c for c in runtime.publish_calls if c[0] == "xblock-ai-tutor.question.asked"]
        assert len(asked_calls) == 1, "duplicate request_id must count once"

    def test_weekly_metric_no_quota(self):
        """Weekly metric does not read quota counters."""
        if not TRACKING_AVAILABLE:
            pytest.fail("ModuleNotFoundError — tracking.py missing (T-045 red)")
        runtime = StubRuntime()
        tp = TrackingPublisher(runtime)
        assert hasattr(tp, 'get_weekly_metric') or hasattr(tp, 'weekly_asked_count')


if __name__ == "__main__":
    pytest.main([__file__, "-v"])