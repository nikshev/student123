# verifies: FR-002-08
"""
Contract tests for blocked response handling in the AI Tutor XBlock ask handler (T-041).

The blocked terminal status must produce a safe response: the rule answer is
shown verbatim, the blocked_reason is included in the result, sources are
normalized to an empty list, and reformulation is a new request with a new
request_id.
"""

import json
import uuid
from unittest.mock import Mock

import pytest

from ai_tutor_xblock.ai_tutor_xblock.block import AiTutorXBlock
from ai_tutor_xblock.ai_tutor_xblock.client import (
    AskResult, ConfigResult, HistoryResult,
)

USER_ID = "user-42"
COURSE_ID = "course-v1:demo+math+2026"
UNIT_USAGE_KEY = "block-v1:demo+math+2026+type@vertical+block@u1"
CONFIG_VERSION = "1.0.0"
QUESTION = "..."
SAFE_RULE = "Допомагаю розібратися, а не розв'язую за тебе."


class StubUser:
    def __init__(self, user_id=USER_ID):
        self.id = user_id


class LMSRuntime:
    def __init__(self, user=StubUser(), course_id=COURSE_ID):
        self.user = user
        self.course_id = course_id
        self.is_author_mode = False

    def _is_enrolled(self, user, course_id):
        return True


class FakeClient:
    """Module-seam fake; records calls, returns typed results or raises."""

    def __init__(self):
        self.ask_calls = []
        self.history_calls = []
        self.config_calls = []
        self.ask_result = None
        self.history_result = None
        self.ask_error = None
        self.history_error = None
        self.config_error = None
        self.config_result = ConfigResult(
            config_version=CONFIG_VERSION,
            daily_limit=10,
            request_timeout_seconds=30,
            http_connect_timeout_seconds=2,
            question_max_chars=2000,
        )

    def ask(self, question, conversation_id, route, user_id, course_id,
            unit_usage_key, request_id=None):
        self.ask_calls.append({
            "question": question,
            "conversation_id": conversation_id,
            "route": route,
            "user_id": user_id,
            "course_id": course_id,
            "unit_usage_key": unit_usage_key,
            "request_id": request_id,
        })
        if self.ask_error is not None:
            raise self.ask_error
        return self.ask_result

    def history(self, conversation_id, user_id, course_id, unit_usage_key):
        self.history_calls.append({
            "conversation_id": conversation_id,
            "user_id": user_id,
            "course_id": course_id,
            "unit_usage_key": unit_usage_key,
        })
        if self.history_error is not None:
            raise self.history_error
        return self.history_result

    def config(self):
        self.config_calls.append({})
        if self.config_error is not None:
            raise self.config_error
        return self.config_result


class RequestStub:
    def __init__(self, body):
        self.body = body


@pytest.fixture
def env(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(
        "ai_tutor_xblock.ai_tutor_xblock.block.TutorServiceClient",
        lambda *args, **kwargs: fake,
    )
    return fake


def make_block(runtime=None, env_fake=None):
    scope_ids = Mock()
    scope_ids.usage_id = UNIT_USAGE_KEY
    return AiTutorXBlock(runtime or LMSRuntime(), scope_ids=scope_ids)


def ask_body(question=QUESTION, conversation_id=None, request_id=None):
    return json.dumps({
        "question": question,
        "conversation_id": conversation_id,
        "request_id": request_id or str(uuid.uuid4()),
    })


class TestBlockedResponse:
    def test_blocked_response_includes_reason(self, env):
        env.ask_result = AskResult(
            status="blocked",
            answer=SAFE_RULE,
            topic="Правила множення",
            sources=[],
            blocked_reason=SAFE_RULE,
            daily_remaining=7,
            latency_ms=100,
            route="default",
            config_version=CONFIG_VERSION,
            conversation_id=str(uuid.uuid4()),
            request_id=str(uuid.uuid4()),
        )
        block = make_block()
        result = block.ask(
            RequestStub(json.dumps({
                "question": QUESTION,
                "conversation_id": None,
                "request_id": str(uuid.uuid4()),
            })),
            "",
        )

        assert "blocked_reason" in result
        assert result["status"] == "blocked"
        assert result["answer"] == SAFE_RULE
        assert result["sources"] == []

    def test_blocked_sources_normalized(self, env):
        env.ask_result = AskResult(
            status="blocked",
            answer=SAFE_RULE,
            topic="Правила множення",
            sources=[{"segment_id": "x", "kind": "transcript",
                      "source_ref": "video@00:00", "excerpt": "y"}],
            blocked_reason=SAFE_RULE,
            daily_remaining=7,
            latency_ms=100,
            route="default",
            config_version=CONFIG_VERSION,
            conversation_id=str(uuid.uuid4()),
            request_id=str(uuid.uuid4()),
        )
        block = make_block()
        result = block.ask(
            RequestStub(json.dumps({
                "question": QUESTION,
                "conversation_id": None,
                "request_id": str(uuid.uuid4()),
            })),
            "",
        )

        assert result["sources"] == []

    def test_blocked_answer_exact_rule(self, env):
        env.ask_result = AskResult(
            status="blocked",
            answer=SAFE_RULE,
            topic="Правила множення",
            sources=[],
            blocked_reason=SAFE_RULE,
            daily_remaining=7,
            latency_ms=100,
            route="default",
            config_version=CONFIG_VERSION,
            conversation_id=str(uuid.uuid4()),
            request_id=str(uuid.uuid4()),
        )
        block = make_block()
        result = block.ask(
            RequestStub(json.dumps({
                "question": QUESTION,
                "conversation_id": None,
                "request_id": str(uuid.uuid4()),
            })),
            "",
        )

        assert result["answer"] == SAFE_RULE

    def test_reformulate_two_request_ids(self, env):
        env.ask_result = AskResult(
            status="shown",
            answer="test",
            topic="Правила множення",
            sources=[],
            blocked_reason=None,
            daily_remaining=7,
            latency_ms=100,
            route="default",
            config_version=CONFIG_VERSION,
            conversation_id=str(uuid.uuid4()),
            request_id=str(uuid.uuid4()),
        )
        block = make_block()
        request_id_1 = str(uuid.uuid4())
        request_id_2 = str(uuid.uuid4())
        block.ask(
            RequestStub(json.dumps({
                "question": QUESTION,
                "conversation_id": None,
                "request_id": request_id_1,
            })),
            "",
        )
        block.ask(
            RequestStub(json.dumps({
                "question": QUESTION,
                "conversation_id": None,
                "request_id": request_id_2,
            })),
            "",
        )

        assert len(env.ask_calls) == 2
        assert env.ask_calls[0]["request_id"] != env.ask_calls[1]["request_id"]
