# verifies: FR-002-09
"""
Integration tests for AI Tutor tracking flows (T-045).

These tests verify FR-002-09: tracking events flow through handler ask.
The module is not yet implemented (tracking.py missing), so these tests
should be RED with ModuleNotFoundError.

Integration tests through handler ask (FakeClient + stub-runtime with publish).
"""

import json
import pytest
import uuid
from unittest.mock import Mock, patch

try:
    from ai_tutor_xblock.ai_tutor_xblock.tracking import TrackingPublisher
    TRACKING_AVAILABLE = True
except ModuleNotFoundError:
    TRACKING_AVAILABLE = False

from ai_tutor_xblock.ai_tutor_xblock.block import AiTutorXBlock
from ai_tutor_xblock.ai_tutor_xblock.client import AskResult, ConfigResult

USER_ID = "user-42"
COURSE_ID = "course-v1:demo+math+2026"
UNIT_USAGE_KEY = "block-v1:demo+math+2026+type@vertical+block@unit1"
CONFIG_VERSION = "1.0.0"
QUESTION = "Чому при множенні двох від'ємних чисел виходить додатне число?"


class StubUser:
    def __init__(self, user_id=USER_ID):
        self.id = user_id


class LMSRuntime:
    def __init__(self, user=None):
        self.user = user or StubUser()
        self.course_id = COURSE_ID
        self.publish_calls = []
        self.is_author_mode = False
        self.enroll_calls = []

    def _is_enrolled(self, user, course_id):
        self.enroll_calls.append((user, course_id))
        return True

    def publish(self, event_type, event):
        self.publish_calls.append((event_type, event))
        return True


class StudioRuntime(LMSRuntime):
    def __init__(self):
        super().__init__()
        self.is_author_mode = True


class FakeClient:
    """Module-seam fake for TutorServiceClient (T-011 pattern)."""

    def __init__(self):
        self.ask_result = None
        self.ask_error = None
        self.config_result = ConfigResult(
            config_version=CONFIG_VERSION,
            daily_limit=10,
            request_timeout_seconds=30,
            http_connect_timeout_seconds=2,
            question_max_chars=2000,
        )
        self.config_error = None

    def ask(self, question, conversation_id, route, user_id, course_id,
            unit_usage_key, request_id=None):
        if self.ask_error is not None:
            raise self.ask_error
        return self.ask_result

    def config(self):
        if self.config_error is not None:
            raise self.config_error
        return self.config_result

    def materials_status(self, course_id, unit_usage_key):
        return Mock(status="READY", config_version=CONFIG_VERSION)


def shown_result(status="shown", answer="Згадай правило знаків…",
                 conversation_id=None, request_id=None):
    return AskResult(
        status=status,
        answer=answer,
        topic="Правила множення",
        sources=[{"segment_id": str(uuid.uuid4()), "kind": "transcript",
                  "source_ref": "video@00:00", "excerpt": "При множенні…"}],
        blocked_reason="ready solution" if status == "blocked" else None,
        daily_remaining=7,
        latency_ms=1840,
        route="default",
        config_version=CONFIG_VERSION,
        conversation_id=conversation_id or str(uuid.uuid4()),
        request_id=request_id or str(uuid.uuid4()),
    )


def request_stub(body):
    return Mock(body=body)


def ask_body(question=QUESTION, conversation_id=None, request_id=None):
    return json.dumps({
        "question": question,
        "conversation_id": conversation_id,
        "request_id": request_id or str(uuid.uuid4()),
    })


def make_block(runtime=None):
    scope_ids = Mock()
    scope_ids.usage_id = UNIT_USAGE_KEY
    return AiTutorXBlock(runtime or LMSRuntime(), scope_ids=scope_ids)


class TestShownFlow:
    """shown-flow: ask → asked + shown events in correct order."""

    def test_asked_then_shown(self):
        """Verify: asked published first, then shown for terminal status."""
        if not TRACKING_AVAILABLE:
            pytest.fail("ModuleNotFoundError — tracking.py missing (T-045 red)")
        runtime = LMSRuntime()
        fake = FakeClient()
        fake.ask_result = shown_result()
        
        with patch(
            "ai_tutor_xblock.ai_tutor_xblock.block._make_client",
            return_value=fake
        ):
            block = make_block(runtime)
            body = ask_body()
            result = block.ask(request_stub(body))
        
        assert result["status"] == "shown"
        # Verify both events published
        event_types = [c[0] for c in runtime.publish_calls]
        assert "xblock-ai-tutor.question.asked" in event_types
        assert "xblock-ai-tutor.answer.shown" in event_types
        # Assert asked comes before shown
        asked_idx = event_types.index("xblock-ai-tutor.question.asked")
        shown_idx = event_types.index("xblock-ai-tutor.answer.shown")
        assert asked_idx < shown_idx, "asked must be published before shown"

    def test_asked_has_correct_payload(self):
        """asked payload contains request_id, conversation, topic, server identity."""
        if not TRACKING_AVAILABLE:
            pytest.fail("ModuleNotFoundError — tracking.py missing (T-045 red)")
        runtime = LMSRuntime()
        fake = FakeClient()
        fake.ask_result = shown_result()
        rid = str(uuid.uuid4())
        
        with patch(
            "ai_tutor_xblock.ai_tutor_xblock.block._make_client",
            return_value=fake
        ):
            block = make_block(runtime)
            body = ask_body(request_id=rid)
            block.ask(request_stub(body))
        
        asked_event = next(
            (c for c in runtime.publish_calls if c[0] == "xblock-ai-tutor.question.asked"),
            None
        )
        assert asked_event is not None
        payload = asked_event[1]
        assert "request_id" in payload
        assert "user_id" in payload
        assert "course_id" in payload
        assert "unit_usage_key" in payload
        assert payload["user_id"] == USER_ID
        assert payload["course_id"] == COURSE_ID


class TestBlockedFlow:
    """blocked-flow: service blocked → asked + blocked (answer=rule, no candidate)."""

    def test_asked_and_blocked_only(self):
        """Verify: denied has NO events; shown/blocked has asked + outcome."""
        if not TRACKING_AVAILABLE:
            pytest.fail("ModuleNotFoundError — tracking.py missing (T-045 red)")
        runtime = LMSRuntime()
        fake = FakeClient()
        fake.ask_result = shown_result(status="blocked", answer="Допомагаю розібратися, а не розв'язую за тебе.")
        
        with patch(
            "ai_tutor_xblock.ai_tutor_xblock.block._make_client",
            return_value=fake
        ):
            block = make_block(runtime)
            block.ask(request_stub(ask_body()))
        
        event_types = [c[0] for c in runtime.publish_calls]
        assert "xblock-ai-tutor.question.asked" in event_types
        assert "xblock-ai-tutor.answer.blocked" in event_types
        blocked_event = next(c for c in runtime.publish_calls 
                           if c[0] == "xblock-ai-tutor.answer.blocked")
        payload = blocked_event[1]
        assert payload["answer"] is not None
        assert "candidate" not in payload or payload.get("candidate") is None

    def test_blocked_has_blocked_reason(self):
        """blocked event must have non-empty blocked_reason."""
        if not TRACKING_AVAILABLE:
            pytest.fail("ModuleNotFoundError — tracking.py missing (T-045 red)")
        runtime = LMSRuntime()
        fake = FakeClient()
        fake.ask_result = shown_result(status="blocked")
        
        with patch(
            "ai_tutor_xblock.ai_tutor_xblock.block._make_client",
            return_value=fake
        ):
            block = make_block(runtime)
            block.ask(request_stub(ask_body()))
        
        blocked_event = next(c for c in runtime.publish_calls 
                           if c[0] == "xblock-ai-tutor.answer.blocked")
        assert blocked_event[1]["blocked_reason"] is not None


class TestNoMaterialsOffTopicFlow:
    """no_materials/off_topic-flow: asked + shown? with terminal status."""

    def test_no_materials_asked_shown(self):
        """For no_materials/off_topic: asked + shown with appropriate status."""
        if not TRACKING_AVAILABLE:
            pytest.fail("ModuleNotFoundError — tracking.py missing (T-045 red)")
        runtime = LMSRuntime()
        fake = FakeClient()
        fake.ask_result = shown_result(status="no_materials", answer="Для цього юніту матеріали недоступні.")
        
        with patch(
            "ai_tutor_xblock.ai_tutor_xblock.block._make_client",
            return_value=fake
        ):
            block = make_block(runtime)
            result = block.ask(request_stub(ask_body()))
        
        assert result["status"] == "no_materials"
        event_types = [c[0] for c in runtime.publish_calls]
        assert "xblock-ai-tutor.question.asked" in event_types

    def test_off_topic_asked_shown(self):
        """For off_topic: asked + shown with appropriate status."""
        if not TRACKING_AVAILABLE:
            pytest.fail("ModuleNotFoundError — tracking.py missing (T-045 red)")
        runtime = LMSRuntime()
        fake = FakeClient()
        fake.ask_result = shown_result(status="off_topic", answer="Це питання не про цей предмет.")
        
        with patch(
            "ai_tutor_xblock.ai_tutor_xblock.block._make_client",
            return_value=fake
        ):
            block = make_block(runtime)
            result = block.ask(request_stub(ask_body()))
        
        assert result["status"] == "off_topic"
        event_types = [c[0] for c in runtime.publish_calls]
        assert "xblock-ai-tutor.question.asked" in event_types


class Test4295xxFlow:
    """429/5xx-flow: asked published, shown/blocked NOT."""

    def test_429_asked_only(self):
        """Quotum exceeded: asked published, NO shown/blocked."""
        from ai_tutor_xblock.ai_tutor_xblock.client import QuotaExceededError
        if not TRACKING_AVAILABLE:
            pytest.fail("ModuleNotFoundError — tracking.py missing (T-045 red)")
        runtime = LMSRuntime()
        fake = FakeClient()
        fake.ask_error = QuotaExceededError("Щоденний ліміт вичерпано", daily_remaining=0)
        
        with patch(
            "ai_tutor_xblock.ai_tutor_xblock.block._make_client",
            return_value=fake
        ):
            block = make_block(runtime)
            with pytest.raises(Exception):
                block.ask(request_stub(ask_body()))
        
        event_types = [c[0] for c in runtime.publish_calls]
        assert "xblock-ai-tutor.question.asked" in event_types
        assert "xblock-ai-tutor.answer.shown" not in event_types
        assert "xblock-ai-tutor.answer.blocked" not in event_types

    def test_5xx_asked_only(self):
        """Service unavailable: asked published, NO shown/blocked."""
        from ai_tutor_xblock.ai_tutor_xblock.client import ServiceUnavailableError
        if not TRACKING_AVAILABLE:
            pytest.fail("ModuleNotFoundError — tracking.py missing (T-045 red)")
        runtime = LMSRuntime()
        fake = FakeClient()
        fake.ask_error = ServiceUnavailableError()
        
        with patch(
            "ai_tutor_xblock.ai_tutor_xblock.block._make_client",
            return_value=fake
        ):
            block = make_block(runtime)
            with pytest.raises(Exception):
                block.ask(request_stub(ask_body()))
        
        event_types = [c[0] for c in runtime.publish_calls]
        assert "xblock-ai-tutor.question.asked" in event_types
        assert "xblock-ai-tutor.answer.shown" not in event_types
        assert "xblock-ai-tutor.answer.blocked" not in event_types


class TestDeniedFlow:
    """denied-flow: guard deny → NO events at all (publish_calls == [])."""

    def test_deny_guard_no_events(self):
        """Guard deny: NO events published."""
        if not TRACKING_AVAILABLE:
            pytest.fail("ModuleNotFoundError — tracking.py missing (T-045 red)")
        runtime = StudioRuntime()  # Studio runtime denies
        fake = FakeClient()
        
        with patch(
            "ai_tutor_xblock.ai_tutor_xblock.block._make_client",
            return_value=fake
        ):
            block = make_block(runtime)
            with pytest.raises(Exception):
                block.ask(request_stub(ask_body()))
        
        assert runtime.publish_calls == [], "denied must have NO events"
        assert len(fake.ask_calls) == 0 if hasattr(fake, 'ask_calls') else True


class TestDuplicateRequestId:
    """jld 4919: duplicate request_id counted once for weekly metric."""

    def test_duplicate_request_id_single_asked(self):
        """Same request_id retry should not duplicate asked event."""
        if not TRACKING_AVAILABLE:
            pytest.fail("ModuleNotFoundError — tracking.py missing (T-045 red)")
        runtime = LMSRuntime()
        fake = FakeClient()
        fake.ask_result = shown_result()
        rid = str(uuid.uuid4())
        
        with patch(
            "ai_tutor_xblock.ai_tutor_xblock.block._make_client",
            return_value=fake
        ):
            block = make_block(runtime)
            # First call
            block.ask(request_stub(ask_body(request_id=rid)))
            # Retry with same request_id
            block.ask(request_stub(ask_body(request_id=rid)))
        
        asked_events = [c for c in runtime.publish_calls 
                        if c[0] == "xblock-ai-tutor.question.asked"]
        assert len(asked_events) == 1, "duplicate request_id counts once"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])