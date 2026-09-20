# verifies: FR-002-01
"""
Integration tests for POST /api/v1/ask (T-019).

Покриття (contracts/tutor-service-api.md §2, §8; data-model.md §2-3):
- exact schema 200: request_id/conversation_id/status/answer/topic/sources/
  blocked_reason/daily_remaining/latency_ms/route/config_version;
- conversation_id=null створює нову розмову; повторний ask із поверненим ID
  продовжує її; повідомлення накопичуються в порядку student→tutor;
- route == "default"; інший route → 400;
- seam retrieval→generation→guard→persistence: prompt генерації містить
  retrieved матеріал юніту, candidate передається guard, усе записане до 200;
- statuses shown/no_materials/off_topic; shown має server-derived sources і
  config_version; no_materials/off_topic — точні YAML-відповіді з sources=[];
- idempotent retry з тим самим Idempotency-Key не дублює turn;
- бюджети: generation 18 + guard 7 < request 30 (значення з tutor_config.yaml);
- envelopes 400/403/404/502/503/504 — єдина форма
  {"error":{"code","message"},"request_id"} без provider details.

Контрактні очікування до pipeline T-020 (зафіксовані цими тестами):
- pipeline використовує єдину LLM-межу ai_tutor_service.providers.client.LLMClient
  (constitution IV) — тести monkeypatch-ять методи саме цього класу;
- retrieval живить generation (MaterialRetriever, ізоляція course+unit);
- таймаути generation/guard передаються з config (не hard-coded);
- відповідь і повідомлення фіксуються транзакційно до 200;
- Idempotency-Key: replay повертає збережений результат і не створює новий turn;
- LLMError(malformed_response|provider_error)→502, LLMError(timeout)→504,
  django.db.OperationalError→503, чужа розмова→403, невідома→404.

Очікувана причина червоності зараз: AskView — 501-заглушка (T-016),
ai_tutor_service.tutoring відсутній (T-020). Чесні проходи наявного шару
(прецедент T-017): тести 400 на відсутні question/route і не-default route
проходять валідацію заглушки T-016; бюджет-тест проходить на конфігу T-008.
"""

import json
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from django.db import OperationalError
from django.test import Client
from django.utils import timezone

from ai_tutor_service.config import load_tutor_config
from ai_tutor_service.conversations.models import Conversation, Message
from ai_tutor_service.providers.client import LLMClient, LLMError

BEARER = "test-shared-secret-12345"
ASK_URL = "/api/v1/ask"
MATERIALS_URL = "/api/v1/materials"

COURSE_ID = "course-v1:demo+math+2026"
UNIT_KEY = "block-v1:demo+math+2026+type@vertical+block@u1"
EMPTY_UNIT_KEY = "block-v1:demo+math+2026+type@vertical+block@u9"
USER_A = "student-ask-aaa"
USER_B = "student-ask-bbb"

QUESTION = "Чому при множенні двох від'ємних чисел виходить додатне число?"

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = REPO_ROOT / "ai_tutor_service" / "tutor_config.yaml"
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "llm"

TERMINAL_KEYS = {
    "request_id",
    "conversation_id",
    "status",
    "answer",
    "topic",
    "sources",
    "blocked_reason",
    "daily_remaining",
    "latency_ms",
    "route",
    "config_version",
}


def _config():
    return load_tutor_config(CONFIG_PATH)


def _load_llm_fixture(operation, name):
    path = FIXTURES_DIR / operation / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _seed_payload(unit_key=UNIT_KEY):
    return {
        "course_id": COURSE_ID,
        "unit_usage_key": unit_key,
        "content_version": "2026-09-20.1",
        "transcript": [
            {
                "ordinal": 0,
                "start_ms": 0,
                "end_ms": 12000,
                "text": "При множенні двох від'ємних чисел результат додатний.",
                "source_ref": "video@00:00",
            },
        ],
        "notes": [
            {
                "ordinal": 0,
                "section_title": "Правила множення",
                "text": "Множення від'ємних чисел дає додатне число.",
                "source_ref": "notes#multiplication-rules",
            },
        ],
    }


def _seed_materials(client, unit_key=UNIT_KEY):
    headers = {
        "HTTP_AUTHORIZATION": f"Bearer {BEARER}",
        "HTTP_X_AI_TUTOR_ROLE": "staff",
        "HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4()),
    }
    response = client.post(
        MATERIALS_URL,
        data=json.dumps(_seed_payload(unit_key)),
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 201, (
        f"T-018 не реалізовано: очікувався 201, отримано {response.status_code}"
    )


def _ask(client, question, conversation_id=None, route="default", user_id=USER_A,
         unit_key=UNIT_KEY, idempotency_key=None):
    body = {"question": question, "conversation_id": conversation_id, "route": route}
    headers = {
        "HTTP_AUTHORIZATION": f"Bearer {BEARER}",
        "HTTP_X_AI_TUTOR_USER_ID": user_id,
        "HTTP_X_AI_TUTOR_COURSE_ID": COURSE_ID,
        "HTTP_X_AI_TUTOR_UNIT_USAGE_KEY": unit_key,
    }
    if idempotency_key is not None:
        headers["HTTP_IDEMPOTENCY_KEY"] = idempotency_key
    return client.post(
        ASK_URL,
        data=json.dumps(body),
        content_type="application/json",
        **headers,
    )


def _assert_envelope(response, expected_status):
    assert response.status_code == expected_status, (
        f"T-020 не реалізовано: очікувався {expected_status}, "
        f"отримано {response.status_code}"
    )
    data = json.loads(response.content)
    assert set(data.keys()) == {"error", "request_id"}, (
        f"envelope має бути {{error, request_id}}, отримано {sorted(data.keys())}"
    )
    uuid.UUID(data["request_id"])
    assert set(data["error"].keys()) == {"code", "message"}
    assert data["error"]["code"]
    assert data["error"]["message"]
    return data


def _assert_terminal_shape(data, status):
    assert set(data.keys()) == TERMINAL_KEYS, (
        f"exact schema 200: отримано ключі {sorted(data.keys())}"
    )
    uuid.UUID(data["request_id"])
    uuid.UUID(data["conversation_id"])
    assert data["status"] == status
    assert isinstance(data["answer"], str) and data["answer"]
    assert isinstance(data["topic"], str)
    assert isinstance(data["sources"], list)
    assert isinstance(data["daily_remaining"], int) and data["daily_remaining"] >= 0
    assert isinstance(data["latency_ms"], int) and data["latency_ms"] >= 0
    assert data["route"] == "default"
    assert data["config_version"] == _config()["version"]


class FakeLLM:
    """Записує seam-виклики; відповіді — лише записані LLM-фікстури."""

    def __init__(self):
        self.generate_calls = []
        self.guard_calls = []
        self.off_topic_calls = []
        self.order = []
        self.generate_result = _load_llm_fixture("generate", "valid_response")
        self.guard_result = _load_llm_fixture("guard", "valid_false")
        self.off_topic_result = None
        self.generate_error = None
        self.guard_error = None

    def generate(self, prompt, model_id, timeout):
        self.generate_calls.append(
            {"prompt": prompt, "model_id": model_id, "timeout": timeout}
        )
        self.order.append("generate")
        if self.generate_error is not None:
            raise self.generate_error
        return dict(self.generate_result)

    def guard(self, prompt, model_id, timeout):
        self.guard_calls.append(
            {"prompt": prompt, "model_id": model_id, "timeout": timeout}
        )
        self.order.append("guard")
        if self.guard_error is not None:
            raise self.guard_error
        return dict(self.guard_result)

    def off_topic(self, prompt, model_id, timeout):
        self.off_topic_calls.append(
            {"prompt": prompt, "model_id": model_id, "timeout": timeout}
        )
        self.order.append("off_topic")
        return dict(self.off_topic_result)


@pytest.fixture
def fake_llm(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(LLMClient, "generate", fake.generate)
    monkeypatch.setattr(LLMClient, "guard", fake.guard)
    monkeypatch.setattr(LLMClient, "off_topic", fake.off_topic)
    return fake


class TestAskShown:
    """200 shown: exact schema, sources, config_version, seam, persistence."""

    def test_shown_exact_schema_with_sources(self, fake_llm):
        client = Client()
        _seed_materials(client)
        response = _ask(client, QUESTION, idempotency_key=str(uuid.uuid4()))
        assert response.status_code == 200, (
            f"T-020 не реалізовано: очікувався 200, отримано {response.status_code}"
        )
        data = json.loads(response.content)
        _assert_terminal_shape(data, "shown")
        assert data["answer"] == _load_llm_fixture("generate", "valid_response")["text"]
        assert data["topic"], "topic має бути непорожнім"
        assert data["sources"], "shown вимагає хоча б одне source"
        for source in data["sources"]:
            assert set(source.keys()) == {"segment_id", "kind", "source_ref", "excerpt"}
            assert source["kind"] in ("transcript", "notes")
            assert source["source_ref"].startswith(("video@", "notes#"))
            assert source["excerpt"]
        assert data["blocked_reason"] is None

    def test_retrieval_generation_guard_seam_and_persistence(self, fake_llm):
        client = Client()
        _seed_materials(client)
        response = _ask(client, QUESTION, idempotency_key=str(uuid.uuid4()))
        assert response.status_code == 200, (
            f"T-020 не реалізовано: очікувався 200, отримано {response.status_code}"
        )
        data = json.loads(response.content)
        assert data["status"] == "shown"

        assert len(fake_llm.generate_calls) == 1, "generate мав викликатись рівно раз"
        assert len(fake_llm.guard_calls) == 1, "guard мав викликатись рівно раз"
        assert fake_llm.order.index("generate") < fake_llm.order.index("guard"), (
            "seam: generation має передувати guard"
        )

        seed_text = _seed_payload()["transcript"][0]["text"]
        assert seed_text in fake_llm.generate_calls[0]["prompt"], (
            "generate prompt мав містити retrieved матеріал юніту"
        )

        generated_text = _load_llm_fixture("generate", "valid_response")["text"]
        assert generated_text in fake_llm.guard_calls[0]["prompt"], (
            "guard мав отримати candidate з generation"
        )

        config = _config()
        assert fake_llm.generate_calls[0]["timeout"] == config["generation_timeout_seconds"]
        assert fake_llm.guard_calls[0]["timeout"] == config["guard_timeout_seconds"]

        conversation = Conversation.objects.get(pk=uuid.UUID(data["conversation_id"]))
        messages = list(
            Message.objects.filter(conversation_id=conversation)
            .order_by("created_at", "id")
        )
        assert [m.role for m in messages] == ["student", "tutor"]
        assert messages[0].status == "asked"
        assert messages[0].text == QUESTION
        assert messages[1].status == "shown"
        assert messages[1].sources
        assert messages[1].config_version == config["version"]
        assert messages[1].text == generated_text

    def test_null_conversation_then_owned_continuation(self, fake_llm):
        client = Client()
        _seed_materials(client)
        r1 = _ask(client, QUESTION, conversation_id=None,
                  idempotency_key=str(uuid.uuid4()))
        assert r1.status_code == 200, (
            f"T-020 не реалізовано: очікувався 200, отримано {r1.status_code}"
        )
        d1 = json.loads(r1.content)
        assert d1["status"] == "shown"
        conversation_id = d1["conversation_id"]
        uuid.UUID(conversation_id)

        r2 = _ask(client, QUESTION, conversation_id=conversation_id,
                  idempotency_key=str(uuid.uuid4()))
        assert r2.status_code == 200, (
            f"T-020 не реалізовано: очікувався 200, отримано {r2.status_code}"
        )
        d2 = json.loads(r2.content)
        assert d2["conversation_id"] == conversation_id, (
            "повторний ask з наявним ID має продовжувати ту саму розмову"
        )
        assert d2["status"] == "shown"

        conversation = Conversation.objects.get(pk=uuid.UUID(conversation_id))
        messages = list(
            Message.objects.filter(conversation_id=conversation)
            .order_by("created_at", "id")
        )
        assert [m.role for m in messages] == ["student", "tutor", "student", "tutor"]

    def test_transaction_persists_before_200(self, fake_llm):
        client = Client()
        _seed_materials(client)
        response = _ask(client, QUESTION, idempotency_key=str(uuid.uuid4()))
        assert response.status_code == 200, (
            f"T-020 не реалізовано: очікувався 200, отримано {response.status_code}"
        )
        data = json.loads(response.content)
        conversation = Conversation.objects.get(pk=uuid.UUID(data["conversation_id"]))
        messages = list(Message.objects.filter(conversation_id=conversation))
        assert len(messages) == 2, (
            "розмова і повідомлення мають бути записані до повернення 200"
        )

    def test_idempotent_retry_does_not_duplicate_turn(self, fake_llm):
        client = Client()
        _seed_materials(client)
        key = str(uuid.uuid4())
        r1 = _ask(client, QUESTION, idempotency_key=key)
        assert r1.status_code == 200, (
            f"T-020 не реалізовано: очікувався 200, отримано {r1.status_code}"
        )
        r2 = _ask(client, QUESTION, idempotency_key=key)
        assert r2.status_code == 200, (
            f"повтор з тим самим Idempotency-Key мав дати 200, отримано {r2.status_code}"
        )
        d1, d2 = json.loads(r1.content), json.loads(r2.content)
        assert d1["request_id"] == d2["request_id"]
        assert d1["conversation_id"] == d2["conversation_id"]
        assert d1["answer"] == d2["answer"]
        assert d1["status"] == d2["status"] == "shown"
        messages = Message.objects.filter(
            conversation_id__pk=uuid.UUID(d1["conversation_id"])
        )
        assert messages.count() == 2, "idempotent retry не має дублювати turn"


class TestAskNoMaterials:
    """Юніт без READY-матеріалів → no_materials без LLM-викликів."""

    def test_no_materials_for_unit_without_materials(self, fake_llm):
        client = Client()
        response = _ask(client, QUESTION, unit_key=EMPTY_UNIT_KEY,
                        idempotency_key=str(uuid.uuid4()))
        assert response.status_code == 200, (
            f"T-020 не реалізовано: очікувався 200, отримано {response.status_code}"
        )
        data = json.loads(response.content)
        _assert_terminal_shape(data, "no_materials")
        assert data["answer"] == _config()["replies"]["no_materials"]
        assert data["sources"] == []
        assert data["blocked_reason"] is None
        assert fake_llm.generate_calls == [], "generate не має викликатись без матеріалів"
        assert fake_llm.guard_calls == [], "guard не має викликатись без candidate"
        assert fake_llm.off_topic_calls == [], (
            "класифікатор не має викликатись без READY segments"
        )


class TestAskOffTopic:
    """Матеріали є, але питання поза темою → off_topic через класифікатор."""

    def test_off_topic_via_outline_classifier(self, fake_llm):
        client = Client()
        _seed_materials(client)
        fake_llm.off_topic_result = _load_llm_fixture("off_topic", "valid_off_topic")
        response = _ask(
            client,
            "Коли відбулася Друга світова війна і хто в ній переміг?",
            idempotency_key=str(uuid.uuid4()),
        )
        assert response.status_code == 200, (
            f"T-020 не реалізовано: очікувався 200, отримано {response.status_code}"
        )
        data = json.loads(response.content)
        _assert_terminal_shape(data, "off_topic")
        assert data["answer"] == _config()["replies"]["off_topic"]
        assert data["sources"] == []
        assert data["blocked_reason"] is None
        assert len(fake_llm.off_topic_calls) >= 1, (
            "порожній retrieval мав піти в outline-класифікатор"
        )
        assert fake_llm.generate_calls == [], (
            "off_topic не має викликати generation"
        )
        assert fake_llm.guard_calls == [], "off_topic не має викликати guard"


class TestAskBudgets:
    """Бюджети: generation 18 + guard 7 < request 30 (значення з YAML)."""

    def test_generation_plus_guard_within_request_budget(self):
        config = _config()
        assert config["generation_timeout_seconds"] == 18
        assert config["guard_timeout_seconds"] == 7
        assert config["request_timeout_seconds"] == 30
        assert (
            config["generation_timeout_seconds"] + config["guard_timeout_seconds"]
            < config["request_timeout_seconds"]
        )


class TestAskValidationErrors:
    """400: schema/route/limits — єдиний envelope."""

    def test_missing_question_returns_400(self, fake_llm):
        client = Client()
        body = {"conversation_id": None, "route": "default"}
        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {BEARER}",
            "HTTP_X_AI_TUTOR_USER_ID": USER_A,
            "HTTP_X_AI_TUTOR_COURSE_ID": COURSE_ID,
            "HTTP_X_AI_TUTOR_UNIT_USAGE_KEY": UNIT_KEY,
            "HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4()),
        }
        response = client.post(
            ASK_URL, data=json.dumps(body),
            content_type="application/json", **headers,
        )
        data = _assert_envelope(response, 400)
        assert data["error"]["code"] in {"validation_error", "invalid_request"}

    def test_whitespace_question_returns_400(self, fake_llm):
        client = Client()
        response = _ask(client, "   \t  ", idempotency_key=str(uuid.uuid4()))
        data = _assert_envelope(response, 400)
        assert data["error"]["code"] in {"validation_error", "invalid_request"}

    def test_missing_route_returns_400(self, fake_llm):
        client = Client()
        body = {"question": QUESTION, "conversation_id": None}
        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {BEARER}",
            "HTTP_X_AI_TUTOR_USER_ID": USER_A,
            "HTTP_X_AI_TUTOR_COURSE_ID": COURSE_ID,
            "HTTP_X_AI_TUTOR_UNIT_USAGE_KEY": UNIT_KEY,
            "HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4()),
        }
        response = client.post(
            ASK_URL, data=json.dumps(body),
            content_type="application/json", **headers,
        )
        data = _assert_envelope(response, 400)
        assert data["error"]["code"] in {"validation_error", "invalid_request"}

    def test_route_not_default_returns_400(self, fake_llm):
        client = Client()
        response = _ask(client, QUESTION, route="fast",
                        idempotency_key=str(uuid.uuid4()))
        data = _assert_envelope(response, 400)
        assert data["error"]["code"] in {"validation_error", "invalid_request"}

    def test_question_too_long_returns_400(self, fake_llm):
        client = Client()
        max_chars = _config()["question_max_chars"]
        response = _ask(client, "x" * (max_chars + 1),
                        idempotency_key=str(uuid.uuid4()))
        data = _assert_envelope(response, 400)
        assert data["error"]["code"] in {"validation_error", "invalid_request"}


class TestAskActorErrors:
    """403/404: чужі та невідомі розмови — нерозрізнюваний безпечний envelope."""

    def test_other_actor_conversation_returns_403(self, fake_llm):
        client = Client()
        conversation = Conversation.objects.create(
            user_id=USER_A,
            course_id=COURSE_ID,
            unit_usage_key=UNIT_KEY,
            expires_at=timezone.now() + timedelta(days=30),
        )
        response = _ask(client, QUESTION, conversation_id=str(conversation.id),
                        user_id=USER_B, idempotency_key=str(uuid.uuid4()))
        data = _assert_envelope(response, 403)
        assert data["error"]["code"] == "actor_mismatch"
        payload = json.dumps(data, ensure_ascii=False)
        assert USER_A not in payload, "чужий user_id не має витікати"
        assert str(conversation.id) not in payload, "чужа розмова не має витікати"

    def test_unknown_conversation_returns_404(self, fake_llm):
        client = Client()
        response = _ask(client, QUESTION, conversation_id=str(uuid.uuid4()),
                        idempotency_key=str(uuid.uuid4()))
        data = _assert_envelope(response, 404)
        assert data["error"]["code"] in {"not_found", "conversation_not_found"}


class TestAskUpstreamErrors:
    """502/503/504: LLM-помилки, DB-збій, таймаут — локалізовані envelopes."""

    def test_llm_malformed_response_returns_502(self, fake_llm):
        client = Client()
        _seed_materials(client)
        fake_llm.generate_error = LLMError(
            "Provider returned malformed JSON", "malformed_response"
        )
        response = _ask(client, QUESTION, idempotency_key=str(uuid.uuid4()))
        data = _assert_envelope(response, 502)
        assert data["error"]["code"] == "invalid_upstream_response"
        assert "anthropic" not in json.dumps(data, ensure_ascii=False).lower()
        assert "malformed" not in json.dumps(data, ensure_ascii=False).lower()

    def test_llm_provider_error_returns_502(self, fake_llm):
        client = Client()
        _seed_materials(client)
        fake_llm.generate_error = LLMError(
            "Provider error: 500", "provider_error", status_code=500
        )
        response = _ask(client, QUESTION, idempotency_key=str(uuid.uuid4()))
        data = _assert_envelope(response, 502)
        assert data["error"]["code"] == "upstream_error"
        assert "500" not in data["error"]["message"]

    def test_db_failure_returns_503(self, fake_llm, monkeypatch):
        client = Client()
        _seed_materials(client)

        def _boom_save(self, *args, **kwargs):
            raise OperationalError("disk I/O error")

        monkeypatch.setattr(Message, "save", _boom_save)
        monkeypatch.setattr(Conversation, "save", _boom_save)
        response = _ask(client, QUESTION, idempotency_key=str(uuid.uuid4()))
        data = _assert_envelope(response, 503)
        assert data["error"]["code"] in {"service_unavailable", "database_unavailable"}
        assert "disk I/O" not in json.dumps(data, ensure_ascii=False)

    def test_llm_timeout_returns_504(self, fake_llm):
        client = Client()
        _seed_materials(client)
        fake_llm.generate_error = LLMError(
            "Request timed out after 18 seconds", "timeout"
        )
        response = _ask(client, QUESTION, idempotency_key=str(uuid.uuid4()))
        data = _assert_envelope(response, 504)
        assert data["error"]["code"] == "timeout"
        assert "timed out" not in data["error"]["message"].lower()


class TestAskApiTestFile:
    """Мета-перевірка файла тесту."""

    def test_file_has_verifies_marker(self):
        import inspect
        source = inspect.getsource(__import__(__name__))
        assert "# verifies: FR-002-01" in source


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
