# verifies: FR-002-10
"""
Contract tests for AiTutorXBlock ask/history handlers (T-025).

Handlers contract for T-026 (contracts/xblock-interface.md §2–§3) — the
implementation must satisfy exactly this interface:

- ``AiTutorXBlock.ask(self, request, suffix="")`` / ``.history(self, request, suffix="")``
  are XBlock json handlers: ``request.body`` is a JSON string (bytes or str).
- The block obtains the service client by instantiating the module-level name
  ``TutorServiceClient`` imported in ``ai_tutor_xblock.ai_tutor_xblock.block``;
  tests monkeypatch that symbol with FakeClient.
- Identity comes only from the server runtime:
  ``user_id = str(runtime.user.id)``, ``course_id = runtime.course_id``,
  ``unit_usage_key = str(block.scope_ids.usage_id)``. Browser-supplied
  user/course/unit keys are not part of the request schema and are rejected
  as unknown keys.
- Guard order (EnrollmentGuard from T-024): runtime -> anonymous -> course ->
  _is_enrolled. Any deny -> JsonHandlerError(403) with generic envelope
  ``access_denied``; the client is NOT instantiated/called and config is NOT
  fetched for denied actors.
- ask body schema (exact keys): {"question": str, "conversation_id": uuid|null,
  "request_id": uuid}. Extra/missing keys, invalid UUIDs, non-JSON body ->
  400. question is trimmed and must be non-empty and <= question_max_chars
  from the public config projection (client.config()); request_id is reused
  as the service Idempotency-Key (client.ask(..., request_id=request_id)).
- Success: handler returns a dict with EXACT keys
  {status, answer, topic, sources, conversation_id, daily_remaining,
   latency_ms, route, config_version, can_retry}; can_retry is False for all
  terminal successes. status in shown|blocked|no_materials|off_topic. For
  blocked the answer is only the safe rule and sources == [].
- Errors: handler raises ``JsonHandlerError(mapped_http_status,
  json.dumps(envelope))`` where envelope has EXACT keys
  {status: "error", error_code, message, can_retry, request_id, config_version}
  and never contains an answer or provider/auth details. request_id echoes the
  request body request_id when valid, otherwise a fresh UUID. config_version
  is the last successful client.config() version, else "unknown".
- Local status mapping (xblock-interface.md §2):
  BadRequestError -> 400 "bad_request"; UnauthorizedError/ForbiddenError ->
  503 "service_unavailable" (no auth details); NotFoundError -> 404
  "conversation_not_found" (no leaked IDs); QuotaExceededError -> 429
  "quota_exceeded"; ServiceUnavailableError -> 503 "service_unavailable";
  TimeoutError -> 504 "timeout"; SchemaError/unknown status -> 503
  "invalid_service_response" (never best-effort display).
  can_retry: True only for 503/504; False for 400/403/404/429.
- history body schema (exact keys): {"conversation_id": uuid}. Success dict
  has EXACT keys {conversation_id, messages, daily_remaining, config_version}.
  client.history is called with (conversation_id, user_id, course_id,
  unit_usage_key).

The tests are red right now for the expected reason: T-026 is not implemented
yet — ``ai_tutor_xblock.ai_tutor_xblock.block`` neither imports the
``TutorServiceClient`` seam (the monkeypatch of ``block.TutorServiceClient``
fails with AttributeError at setup) nor defines the ask/history handlers.
Once T-026 adds both, these tests must turn green without modification.
"""

import json
import uuid
from unittest.mock import Mock

import pytest
from xblock.exceptions import JsonHandlerError

from ai_tutor_xblock.ai_tutor_xblock.block import AiTutorXBlock
from ai_tutor_xblock.ai_tutor_xblock.client import (
    AskResult,
    BadRequestError,
    ConfigResult,
    ForbiddenError,
    HistoryResult,
    NotFoundError,
    QuotaExceededError,
    SchemaError,
    ServiceUnavailableError,
    TimeoutError,
    UnauthorizedError,
)

USER_ID = "user-42"
COURSE_ID = "course-v1:demo+math+2026"
UNIT_USAGE_KEY = "block-v1:demo+math+2026+type@vertical+block@u1"
CONFIG_VERSION = "1.0.0"
QUESTION = "Чому при множенні двох від'ємних чисел виходить додатне число?"

ASK_SUCCESS_KEYS = {
    "status", "answer", "topic", "sources", "conversation_id",
    "daily_remaining", "latency_ms", "route", "config_version", "can_retry",
}
ERROR_KEYS = {"status", "error_code", "message", "can_retry", "request_id", "config_version"}
HISTORY_KEYS = {"conversation_id", "messages", "daily_remaining", "config_version"}


class StubUser:
    def __init__(self, user_id=USER_ID):
        self.id = user_id


class LMSRuntime:
    def __init__(self, user=StubUser(), course_id=COURSE_ID):
        self.user = user
        self.course_id = course_id
        self.is_author_mode = False
        self.publish_calls = []

    def _is_enrolled(self, user, course_id):
        return True

    def publish(self, event_type, data):
        self.publish_calls.append((event_type, data))


class StudioRuntime(LMSRuntime):
    def __init__(self):
        super().__init__()
        self.is_author_mode = True


class WorkbenchRuntime(LMSRuntime):
    def __init__(self):
        super().__init__()
        self.is_author_mode = True


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


def history_result(conversation_id=None):
    return HistoryResult(
        conversation_id=conversation_id or str(uuid.uuid4()),
        messages=[{"role": "student", "text": QUESTION, "status": "asked"}],
        daily_remaining=7,
        config_version=CONFIG_VERSION,
    )


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


def call_ask(block, body, suffix=""):
    return block.ask(RequestStub(body), suffix)


def error_envelope(exc):
    assert isinstance(exc, JsonHandlerError)
    return json.loads(exc.message)


def assert_error(block, body, status, error_code, env_fake, expect_called=False):
    with pytest.raises(JsonHandlerError) as exc_info:
        call_ask(block, body)
    assert exc_info.value.status_code == status
    envelope = error_envelope(exc_info.value)
    assert set(envelope.keys()) == ERROR_KEYS
    assert envelope["status"] == "error"
    assert envelope["error_code"] == error_code
    assert isinstance(envelope["message"], str) and envelope["message"]
    assert isinstance(envelope["can_retry"], bool)
    uuid.UUID(envelope["request_id"])
    assert isinstance(envelope["config_version"], str)
    if expect_called:
        assert len(env_fake.ask_calls) == 1, "клієнт мав бути викликаний рівно раз"
    else:
        assert env_fake.ask_calls == [], "error-шлях не має викликати сервіс"
    return envelope


class TestAskSuccess:
    def test_ask_shown_exact_schema(self, env):
        request_id = str(uuid.uuid4())
        conversation_id = str(uuid.uuid4())
        env.ask_result = shown_result(
            conversation_id=conversation_id, request_id=request_id)
        block = make_block()

        result = call_ask(block, ask_body(request_id=request_id))

        assert set(result.keys()) == ASK_SUCCESS_KEYS
        assert result["status"] == "shown"
        assert result["answer"] == env.ask_result.answer
        assert result["topic"] == "Правила множення"
        assert result["sources"] == env.ask_result.sources
        assert result["conversation_id"] == conversation_id
        assert result["daily_remaining"] == 7
        assert result["latency_ms"] == 1840
        assert result["route"] == "default"
        assert result["config_version"] == CONFIG_VERSION
        assert result["can_retry"] is False

        call = env.ask_calls[0]
        assert call["question"] == QUESTION
        assert call["conversation_id"] is None
        assert call["route"] == "default"
        assert call["user_id"] == USER_ID
        assert call["course_id"] == COURSE_ID
        assert call["unit_usage_key"] == UNIT_USAGE_KEY
        assert call["request_id"] == request_id, "request_id має йти як Idempotency-Key"

    def test_ask_question_is_trimmed(self, env):
        env.ask_result = shown_result()
        block = make_block()
        call_ask(block, ask_body(question=f"  {QUESTION}  "))
        assert env.ask_calls[0]["question"] == QUESTION

    @pytest.mark.parametrize("status", ["blocked"])
    def test_ask_blocked_safe_rule_no_sources(self, env, status):
        env.ask_result = shown_result(
            status=status, answer="Допомагаю розібратися, а не розв'язую за тебе.")
        block = make_block()
        result = call_ask(block, ask_body())
        assert result["status"] == "blocked"
        assert result["answer"] == "Допомагаю розібратися, а не розв'язую за тебе."
        assert result["sources"] == []
        assert result["can_retry"] is False

    @pytest.mark.parametrize("status", ["no_materials", "off_topic"])
    def test_ask_no_materials_off_topic(self, env, status):
        env.ask_result = shown_result(status=status, answer="Чесна відповідь")
        block = make_block()
        result = call_ask(block, ask_body())
        assert result["status"] == status
        assert result["answer"] == "Чесна відповідь"
        assert set(result.keys()) == ASK_SUCCESS_KEYS


class TestAskValidation:
    def test_ask_invalid_json_400(self, env):
        block = make_block()
        with pytest.raises(JsonHandlerError) as exc_info:
            call_ask(block, "{not-json")
        assert exc_info.value.status_code == 400
        envelope = error_envelope(exc_info.value)
        assert envelope["error_code"] in {"invalid_request", "validation_error"}
        assert env.ask_calls == []

    def test_ask_missing_question_400(self, env):
        block = make_block()
        assert_error(block, json.dumps(
            {"conversation_id": None, "request_id": str(uuid.uuid4())}),
            400, "invalid_request", env)

    def test_ask_whitespace_question_400(self, env):
        block = make_block()
        assert_error(block, ask_body(question="   \t "), 400, "invalid_request", env)

    def test_ask_question_over_config_limit_400(self, env):
        env.config_result = ConfigResult(
            config_version=CONFIG_VERSION, daily_limit=10,
            request_timeout_seconds=30, http_connect_timeout_seconds=2,
            question_max_chars=10,
        )
        block = make_block()
        assert_error(block, ask_body(question="x" * 11), 400, "invalid_request", env)
        assert len(env.config_calls) >= 1, "ліміт має братись із GET /config"

    def test_ask_missing_request_id_400(self, env):
        block = make_block()
        assert_error(block, json.dumps({"question": QUESTION, "conversation_id": None}),
                     400, "invalid_request", env)

    def test_ask_request_id_not_uuid_400(self, env):
        block = make_block()
        assert_error(block, ask_body(request_id="not-a-uuid"),
                     400, "invalid_request", env)

    def test_ask_conversation_id_not_uuid_400(self, env):
        block = make_block()
        assert_error(block, ask_body(conversation_id="abc"),
                     400, "invalid_request", env)

    def test_ask_extra_keys_400_browser_identity_ignored(self, env):
        """Browser-supplied user/course/unit keys are rejected, not trusted."""
        block = make_block()
        body = json.dumps({
            "question": QUESTION,
            "conversation_id": None,
            "request_id": str(uuid.uuid4()),
            "user_id": "attacker",
            "course_id": "course-v1:evil",
            "unit_usage_key": "block-v1:evil",
        })
        assert_error(block, body, 400, "invalid_request", env)

    def test_ask_error_envelope_echoes_request_id(self, env):
        request_id = str(uuid.uuid4())
        block = make_block()
        envelope = assert_error(
            block, ask_body(question="", request_id=request_id),
            400, "invalid_request", env)
        assert envelope["request_id"] == request_id


class TestAskServiceErrorMapping:
    @pytest.mark.parametrize("error,status,code,can_retry", [
        (BadRequestError("Невалідний запит"), 400, "bad_request", False),
        (UnauthorizedError(), 503, "service_unavailable", True),
        (ForbiddenError(), 503, "service_unavailable", True),
        (NotFoundError(), 404, "conversation_not_found", False),
        (QuotaExceededError("Ліміт вичерпано", daily_remaining=0), 429,
         "quota_exceeded", False),
        (ServiceUnavailableError(), 503, "service_unavailable", True),
        (TimeoutError(), 504, "timeout", True),
        (SchemaError("unknown status"), 503, "invalid_service_response", True),
    ])
    def test_ask_service_error_mapping(self, env, error, status, code, can_retry):
        env.ask_error = error
        block = make_block()
        with pytest.raises(JsonHandlerError) as exc_info:
            call_ask(block, ask_body())
        assert exc_info.value.status_code == status
        envelope = error_envelope(exc_info.value)
        assert envelope["error_code"] == code
        assert envelope["can_retry"] is can_retry
        assert "answer" not in envelope, "error-відповідь не містить answer"
        assert len(env.ask_calls) == 1, "клієнт мав бути викликаний рівно раз"

    def test_ask_unknown_status_never_shows_answer(self, env):
        env.ask_error = SchemaError("service returned status 'weird'")
        block = make_block()
        envelope = assert_error(block, ask_body(), 503,
                                "invalid_service_response", env,
                                expect_called=True)
        assert "answer" not in envelope

    def test_ask_auth_errors_hide_details(self, env):
        env.ask_error = UnauthorizedError("Bearer token invalid for client 0xdead")
        block = make_block()
        with pytest.raises(JsonHandlerError) as exc_info:
            call_ask(block, ask_body())
        envelope = error_envelope(exc_info.value)
        text = json.dumps(envelope, ensure_ascii=False).lower()
        assert "bearer" not in text
        assert "token" not in text
        assert "401" not in text

    def test_ask_config_failure_503(self, env):
        env.config_error = ServiceUnavailableError()
        block = make_block()
        with pytest.raises(JsonHandlerError) as exc_info:
            call_ask(block, ask_body())
        assert exc_info.value.status_code == 503
        envelope = error_envelope(exc_info.value)
        assert envelope["error_code"] == "service_unavailable"
        assert envelope["config_version"] == "unknown"

    def test_ask_config_fetched_once_per_block(self, env):
        """Cached projection: two asks -> one config() call (§6)."""
        env.ask_result = shown_result()
        block = make_block()
        call_ask(block, ask_body())
        call_ask(block, ask_body())
        assert len(env.config_calls) == 1
        assert len(env.ask_calls) == 2

    def test_ask_success_uses_service_result_config_version(self, env):
        """Success config_version comes from the service result, not config()."""
        env.config_result = ConfigResult(
            config_version="9.9.9", daily_limit=10,
            request_timeout_seconds=30, http_connect_timeout_seconds=2,
            question_max_chars=2000,
        )
        env.ask_result = shown_result()
        block = make_block()
        result = call_ask(block, ask_body())
        assert result["config_version"] == CONFIG_VERSION


class TestAskGuard:
    @pytest.mark.parametrize("runtime_cls", [StudioRuntime, WorkbenchRuntime])
    def test_ask_non_lms_runtime_denies_without_client(self, env, runtime_cls):
        block = make_block(runtime=runtime_cls())
        assert_error(block, ask_body(), 403, "access_denied", env)
        assert env.config_calls == [], "denied не має отримувати config"

    def test_ask_anonymous_denies(self, env):
        runtime = LMSRuntime(user=None)
        block = make_block(runtime=runtime)
        assert_error(block, ask_body(), 403, "access_denied", env)
        assert env.config_calls == []

    def test_ask_not_enrolled_denies(self, env):
        runtime = LMSRuntime()
        runtime._is_enrolled = lambda user, course_id: False
        block = make_block(runtime=runtime)
        assert_error(block, ask_body(), 403, "access_denied", env)

    def test_ask_enrollment_exception_fails_closed(self, env):
        runtime = LMSRuntime()

        def boom(user, course_id):
            raise RuntimeError("db down")

        runtime._is_enrolled = boom
        block = make_block(runtime=runtime)
        assert_error(block, ask_body(), 403, "access_denied", env)

    def test_ask_missing_course_denies(self, env):
        runtime = LMSRuntime(course_id=None)
        block = make_block(runtime=runtime)
        assert_error(block, ask_body(), 403, "access_denied", env)


def call_history(block, body, suffix=""):
    return block.history(RequestStub(body), suffix)


class TestHistory:
    def test_history_success_exact_schema(self, env):
        conversation_id = str(uuid.uuid4())
        env.history_result = history_result(conversation_id=conversation_id)
        block = make_block()
        result = call_history(block, json.dumps({"conversation_id": conversation_id}))
        assert set(result.keys()) == HISTORY_KEYS
        assert result["conversation_id"] == conversation_id
        assert result["messages"] == env.history_result.messages
        assert result["daily_remaining"] == 7
        assert result["config_version"] == CONFIG_VERSION
        call = env.history_calls[0]
        assert call["conversation_id"] == conversation_id
        assert call["user_id"] == USER_ID
        assert call["course_id"] == COURSE_ID
        assert call["unit_usage_key"] == UNIT_USAGE_KEY

    def test_history_missing_conversation_id_400(self, env):
        block = make_block()
        with pytest.raises(JsonHandlerError) as exc_info:
            call_history(block, json.dumps({}))
        assert exc_info.value.status_code == 400
        assert env.history_calls == []

    def test_history_invalid_uuid_400(self, env):
        block = make_block()
        with pytest.raises(JsonHandlerError) as exc_info:
            call_history(block, json.dumps({"conversation_id": "abc"}))
        assert exc_info.value.status_code == 400
        assert env.history_calls == []

    def test_history_extra_keys_400(self, env):
        block = make_block()
        body = json.dumps({
            "conversation_id": str(uuid.uuid4()),
            "user_id": "attacker",
        })
        with pytest.raises(JsonHandlerError) as exc_info:
            call_history(block, body)
        assert exc_info.value.status_code == 400
        assert env.history_calls == []

    def test_history_not_found_404_no_id_leak(self, env):
        conversation_id = str(uuid.uuid4())
        env.history_error = NotFoundError()
        block = make_block()
        with pytest.raises(JsonHandlerError) as exc_info:
            call_history(block, json.dumps({"conversation_id": conversation_id}))
        assert exc_info.value.status_code == 404
        envelope = error_envelope(exc_info.value)
        assert envelope["error_code"] == "conversation_not_found"
        assert conversation_id not in json.dumps(envelope, ensure_ascii=False)

    def test_history_owner_forbidden_404_no_id_leak(self, env):
        """Service 403 (owner mismatch) -> local 404, indistinguishable from not-found (§3)."""
        conversation_id = str(uuid.uuid4())
        env.history_error = ForbiddenError()
        block = make_block()
        with pytest.raises(JsonHandlerError) as exc_info:
            call_history(block, json.dumps({"conversation_id": conversation_id}))
        assert exc_info.value.status_code == 404
        envelope = error_envelope(exc_info.value)
        assert envelope["error_code"] == "conversation_not_found"
        assert conversation_id not in json.dumps(envelope, ensure_ascii=False)
        assert len(env.history_calls) == 1

    def test_history_service_unavailable_503(self, env):
        env.history_error = ServiceUnavailableError()
        block = make_block()
        with pytest.raises(JsonHandlerError) as exc_info:
            call_history(block, json.dumps({"conversation_id": str(uuid.uuid4())}))
        assert exc_info.value.status_code == 503
        envelope = error_envelope(exc_info.value)
        assert envelope["error_code"] == "service_unavailable"
        assert envelope["can_retry"] is True

    def test_history_guard_denies_without_client(self, env):
        block = make_block(runtime=StudioRuntime())
        with pytest.raises(JsonHandlerError) as exc_info:
            call_history(block, json.dumps({"conversation_id": str(uuid.uuid4())}))
        assert exc_info.value.status_code == 403
        envelope = error_envelope(exc_info.value)
        assert envelope["error_code"] == "access_denied"
        assert env.history_calls == []
        assert env.config_calls == []
