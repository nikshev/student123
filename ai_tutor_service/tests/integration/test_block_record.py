# verifies: FR-002-07
"""
Transactional test for blocked response persistence (T-039, FR-002-07).

Tests the contract for T-040: atomic block persistence via guard audit.

Contract for T-040 (audit.py / transaction):
- guard=true a atomically creates blocked Message + append-only BlockRecord
  with user/course/unit/exact question/time/reason/guard model/config version
  before API responds (FR-002-07, SC-004).
- candidate answer never persisted in history/tracking (blocked Message.text is
  rule-only from YAML, BlockRecord has no candidate field).
- DB failure → 503 and candidate hidden (blocked response only after durable ack).
- Blocked response possible only after durable ack (BlockRecord exists iff
  successful 200 blocked response was returned).

Червоний ЗАРАЗ: BlockRecord ще не створюється pipeline'ом (T-040 не реалізовано).
Очікувана причина: BlockRecord не існує в DB, транзакція «blocked→durable ack»
відсутня в pipeline.py. Тести п.2+ (BlockRecord existence) впадають на
відсутності запису; п.5 (DB failure → 503) і п.6 (durable ack) також впадають
бо BlockRecord.save/QuerySet не існує. Усі інші тести проходять.
"""

import json
import uuid
from pathlib import Path

import pytest
from django.db import OperationalError
from django.test import Client

from ai_tutor_service.config import load_tutor_config
from ai_tutor_service.conversations.models import Conversation, Message
from ai_tutor_service.guard.models import BlockRecord
from ai_tutor_service.limits.models import DailyCounter
from ai_tutor_service.providers.client import LLMClient, LLMError

BEARER = "test-shared-secret-12345"
ASK_URL = "/api/v1/ask"
MATERIALS_URL = "/api/v1/materials"

COURSE_ID = "course-v1:demo+math+2026"
UNIT_KEY = "block-v1:demo+math+2026+type@vertical+block@u1"
EMPTY_UNIT_KEY = "block-v1:demo+math+2026+type@vertical+block@u9"
USER_A = "student-block-aaa"
USER_B = "student-block-bbb"

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
        self.guard_result = _load_llm_fixture("guard", "valid_true")
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


class TestBlockRecordPersistence:
    """Тести атомарного створення BlockRecord разом із blocked response."""

    def test_blocked_response_creates_block_record(self, fake_llm):
        """guard=true → 200 blocked, BlockRecord створено до відповіді."""
        client = Client()
        _seed_materials(client)
        response = _ask(client, QUESTION, idempotency_key=str(uuid.uuid4()))
        assert response.status_code == 200, (
            f"T-040 не реалізовано: очікувався 200, отримано {response.status_code}"
        )
        data = json.loads(response.content)
        _assert_terminal_shape(data, "blocked")
        assert data["answer"] == _config()["replies"]["blocked"]
        assert data["sources"] == []
        assert data["blocked_reason"] == _load_llm_fixture(
            "guard", "valid_true"
        )["reason"]
        assert "candidate" not in data

        conversation = Conversation.objects.get(
            pk=uuid.UUID(data["conversation_id"])
        )
        messages = list(
            Message.objects.filter(conversation_id=conversation)
            .order_by("created_at", "id")
        )
        assert len(messages) == 2
        tutor_message = messages[1]
        assert tutor_message.status == "blocked"
        assert tutor_message.text == _config()["replies"]["blocked"]
        assert tutor_message.blocked_reason == _load_llm_fixture(
            "guard", "valid_true"
        )["reason"]
        assert tutor_message.sources == []
        assert tutor_message.config_version == _config()["version"]

        block_record = BlockRecord.objects.get(message_id=tutor_message.id)
        assert block_record.user_id == USER_A
        assert block_record.course_id == COURSE_ID
        assert block_record.unit_usage_key == UNIT_KEY
        assert block_record.question == QUESTION
        assert block_record.reason == _load_llm_fixture(
            "guard", "valid_true"
        )["reason"]
        assert block_record.guard_model_id == _config()["guard_model_id"]
        assert block_record.config_version == _config()["version"]
        assert block_record.created_at is not None
        assert block_record.created_at.tzinfo is not None

    def test_repeated_blocked_asks_create_new_block_records(self, fake_llm):
        """Повторні blocked-asks додають нові BlockRecord-записи, не перезаписують."""
        client = Client()
        _seed_materials(client)
        r1 = _ask(client, QUESTION, idempotency_key=str(uuid.uuid4()))
        assert r1.status_code == 200
        d1 = json.loads(r1.content)
        assert d1["status"] == "blocked"
        conversation_id = d1["conversation_id"]

        r2 = _ask(client, QUESTION, conversation_id=conversation_id,
                  idempotency_key=str(uuid.uuid4()))
        assert r2.status_code == 200
        d2 = json.loads(r2.content)
        assert d2["status"] == "blocked"
        assert d2["conversation_id"] == conversation_id

        conversation = Conversation.objects.get(
            pk=uuid.UUID(conversation_id)
        )
        tutor_messages = list(
            Message.objects.filter(
                conversation_id=conversation, status="blocked"
            ).order_by("created_at", "id")
        )
        assert len(tutor_messages) == 2
        records = list(
            BlockRecord.objects.filter(conversation_id=conversation)
            .order_by("created_at", "id")
        )
        assert len(records) == 2
        assert records[0].id != records[1].id

    def test_candidate_not_persisted_in_blocked_message_or_record(self, fake_llm):
        """Candidate answer не зберігається в blocked Message або BlockRecord."""
        client = Client()
        _seed_materials(client)
        response = _ask(client, QUESTION, idempotency_key=str(uuid.uuid4()))
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["status"] == "blocked"
        assert data["answer"] == _config()["replies"]["blocked"]
        candidate_text = _load_llm_fixture("generate", "valid_response")["text"]
        assert candidate_text not in data["answer"]
        assert candidate_text not in data

        conversation = Conversation.objects.get(
            pk=uuid.UUID(data["conversation_id"])
        )
        tutor_message = Message.objects.get(
            conversation_id=conversation, status="blocked"
        )
        assert candidate_text not in tutor_message.text
        block_record = BlockRecord.objects.get(message_id=tutor_message.id)
        assert candidate_text not in block_record.question
        assert candidate_text not in block_record.reason

    def test_db_failure_returns_503_and_hides_candidate(self, fake_llm, monkeypatch):
        """DB failure → 503, candidate не потрапляє в envelope."""
        client = Client()
        _seed_materials(client)

        def _boom_save(self, *args, **kwargs):
            raise OperationalError("disk I/O error")

        monkeypatch.setattr(BlockRecord, "save", _boom_save)
        response = _ask(client, QUESTION, idempotency_key=str(uuid.uuid4()))
        assert response.status_code == 503, (
            f"T-040 не реалізовано: очікувався 503, отримано {response.status_code}"
        )
        data = json.loads(response.content)
        assert data["error"]["code"] in {"service_unavailable", "database_unavailable"}
        assert "disk I/O" not in json.dumps(data, ensure_ascii=False)
        candidate_text = _load_llm_fixture("generate", "valid_response")["text"]
        assert candidate_text not in json.dumps(data, ensure_ascii=False)

    def test_blocked_response_only_after_durable_ack(self, fake_llm):
        """Blocked response можливий лише після durable ack (BlockRecord існує)."""
        client = Client()
        _seed_materials(client)
        response = _ask(client, QUESTION, idempotency_key=str(uuid.uuid4()))
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["status"] == "blocked"

        conversation = Conversation.objects.get(
            pk=uuid.UUID(data["conversation_id"])
        )
        tutor_message = Message.objects.get(
            conversation_id=conversation, status="blocked"
        )
        block_record = BlockRecord.objects.get(message_id=tutor_message.id)
        assert block_record.id is not None

    def test_file_has_verifies_marker(self):
        """Meta-перевірка файла тесту."""
        import inspect
        source = inspect.getsource(__import__(__name__))
        assert "# verifies: FR-002-07" in source


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
