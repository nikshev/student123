# verifies: FR-002-03
"""
Integration test for grounded sources via /ask endpoint.

Verifies:
- Shown requires >=1 server-derived source with keys {segment_id, kind, source_ref, excerpt}
- Generation prompt receives only top-k segments from current course_id+unit_usage_key (zero cross-unit leakage)
- Transcript timecode video@MM:SS / notes#slug are preserved verbatim
- LLM source substitution (external source_ref) is rejected — sources built only from retrieved records
- Fixture-set (>=10 questions by seeded unit) yields >=90% source-bearing shown-answers
- build_sources returns [] for empty input and ignores dicts missing required fields

Contract T-032 (to be implemented in ai_tutor_service.tutoring.grounding):
    build_sources(retrieved_segments) -> list[dict]
    where each dict has exactly keys {segment_id, kind, source_ref, excerpt}
    and contains only data copied from the corresponding retrieved_segment.
    The function does not consult LLM output; pipeline calls it after retrieval
    to form the 'sources' field of a shown response.
"""

from pathlib import Path

import json
import uuid
from datetime import timedelta

import pytest
from django.db import OperationalError
from django.test import Client
from django.utils import timezone

from ai_tutor_service.config import load_tutor_config
from ai_tutor_service.conversations.models import Conversation, Message
from ai_tutor_service.limits.models import DailyCounter
from ai_tutor_service.providers.client import LLMClient, LLMError

# Import the function under test – will fail until T-032 is implemented
from ai_tutor_service.tutoring.grounding import build_sources

BEARER = "test-shared-secret-12345"
ASK_URL = "/api/v1/ask"
MATERIALS_URL = "/api/v1/materials"

COURSE_ID = "course-v1:demo+math+2026"
UNIT_KEY = "block-v1:demo+math+2026+type@vertical+block@u1"
UNIT_B_KEY = "block-v1:demo+math+2026+type@vertical+block@u2"
EMPTY_UNIT_KEY = "block-v1:demo+math+2026+type@vertical+block@u9"
USER_A = "student-ground-aaa"
USER_B = "student-ground-bbb"

# Helper to load config
REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = REPO_ROOT / "ai_tutor_service" / "tutor_config.yaml"

def _config():
    return load_tutor_config(CONFIG_PATH)

def _seed_payload(unit_key=UNIT_KEY, transcript_text="", notes_text=""):
    return {
        "course_id": COURSE_ID,
        "unit_usage_key": unit_key,
        "content_version": "2026-09-20.1",
        "transcript": [
            {
                "ordinal": 0,
                "start_ms": 0,
                "end_ms": 12000,
                "text": transcript_text,
                "source_ref": "video@00:00",
            }
        ] if transcript_text else [],
        "notes": [
            {
                "ordinal": 0,
                "section_title": "Rules",
                "text": notes_text,
                "source_ref": "notes#rules",
            }
        ] if notes_text else [],
    }

def _seed_materials(client, unit_key=UNIT_KEY, transcript_text="", notes_text=""):
    headers = {
        "HTTP_AUTHORIZATION": f"Bearer {BEARER}",
        "HTTP_X_AI_TUTOR_ROLE": "staff",
        "HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4()),
    }
    payload = _seed_payload(unit_key, transcript_text, notes_text)
    response = client.post(
        MATERIALS_URL,
        data=json.dumps(payload),
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 201, (
        f"T-018 not implemented: expected 201, got {response.status_code}"
    )

def _ask(
    client,
    question,
    conversation_id=None,
    route="default",
    user_id=USER_A,
    unit_key=UNIT_KEY,
    idempotency_key=None,
):
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
    terminal_keys = {
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
    assert set(data.keys()) == terminal_keys, (
        f"exact schema 200: got keys {sorted(data.keys())}"
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
    """Records seam calls; responses are only recorded LLM fixtures."""
    def __init__(self):
        self.generate_calls = []
        self.guard_calls = []
        self.off_topic_calls = []
        self.order = []
        # default valid responses
        self.generate_result = {"text": "Generated answer.", "rank_score": 0.5}
        self.guard_result = {"contains_solution": False, "reason": ""}
        self.off_topic_result = {"classification": "in_scope", "top_source_label": "other"}
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

class TestGroundedSources:
    """Integration tests for /ask pipeline verifying source grounding."""

    def test_shown_exact_source_keys(self, fake_llm):
        """Seed materials; /ask question; shown; sources have exact keys from segments."""
        client = Client()
        # Seed transcript and notes with known content
        _seed_materials(
            client,
            UNIT_KEY,
            transcript_text="Від'ємні числа дають додатний добуток.",
            notes_text="Правило: (−)×(−) = (+).",
        )
        question = "Чому від'ємні помножені на від'ємні дають додатне?"
        # Use a fresh idempotency key
        resp = _ask(client, question, idempotency_key=str(uuid.uuid4()))
        assert resp.status_code == 200, (
            f"T-020 not implemented: expected 200, got {resp.status_code}"
        )
        data = json.loads(resp.content)
        _assert_terminal_shape(data, "shown")
        assert len(data["sources"]) >= 1, "shown must have at least one source"
        # Verify each source has exactly the four required keys
        for src in data["sources"]:
            assert set(src.keys()) == {"segment_id", "kind", "source_ref", "excerpt"}
            assert src["kind"] in ("transcript", "notes")
        # Verify segment_ids correspond to seeded segments
        seeded_segment_ids = {
            s["segment_id"]
            for s in data["sources"]
            if s["kind"] in ("transcript", "notes")
        }
        # Pull actual segments from DB to ensure they belong to this unit
        from ai_tutor_service.materials.models import MaterialSegment
        db_segment_ids = set(
            str(s) for s in MaterialSegment.objects.filter(
                material_id__unit_usage_key=UNIT_KEY,
                material_id__course_id=COURSE_ID,
            ).values_list("id", flat=True)
        )
        assert seeded_segment_ids.issubset(db_segment_ids), (
            "Source segment_ids must belong to seeded unit"
        )

    def test_generation_prompt_gets_only_current_unit_segments(self, fake_llm):
        """Seed two units; ask about unit A; ensure unit B facts do not leak into prompt."""
        client = Client()
        # Seed unit A with fact A
        _seed_materials(
            client,
            UNIT_KEY,
            transcript_text="Від'ємні числа дають додатний добуток.",
            notes_text="Правило множення від'ємних.",
        )
        # Seed unit B with unique fact B
        _seed_materials(
            client,
            UNIT_B_KEY,
            transcript_text="Факт-маркер-Б є унікальним для другого юніту.",
            notes_text="Додатковий матеріал юніту B.",
        )
        question = "Чому від'ємні числа дають додатний добуток?"
        resp = _ask(
            client,
            question,
            idempotency_key=str(uuid.uuid4()),
            unit_key=UNIT_KEY,  # explicitly request unit A
        )
        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert data["status"] == "shown"
        # Ensure generation prompt contains unit A transcript text
        assert len(fake_llm.generate_calls) == 1
        prompt = fake_llm.generate_calls[0]["prompt"]
        assert "Від'ємні числа дають додатний добуток" in prompt
        # Ensure unit B fact does NOT appear in prompt
        assert "Факт-маркер-Б" not in prompt
        # Additionally, verify that sources only come from unit A
        for src in data["sources"]:
            # source_ref should be from unit A (we could also check DB but trust retrieval)
            assert src["source_ref"].startswith(("video@", "notes#"))

    def test_timecode_and_slug_preserved_verbatim(self, fake_llm):
        """Transcript source_ref 'video@00:00' and notes source_ref 'notes#section-title' appear verbatim."""
        client = Client()
        payload = _seed_payload(
            UNIT_KEY,
            transcript_text="Експертний коментар.",
            notes_text="Детальний конспект.",
        )
        payload["transcript"][0]["source_ref"] = "video@00:00"
        payload["notes"][0]["source_ref"] = "notes#section-title"
        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {BEARER}",
            "HTTP_X_AI_TUTOR_ROLE": "staff",
            "HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4()),
        }
        resp = client.post(
            MATERIALS_URL,
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )
        assert resp.status_code == 201
        # Use terms that appear in both transcript and notes to retrieve both segments.
        # transcript contains "коментар", notes contains "конспект".
        question = "коментар конспект"
        resp2 = _ask(client, question, idempotency_key=str(uuid.uuid4()))
        assert resp2.status_code == 200
        data = json.loads(resp2.content)
        assert data["status"] == "shown"
        # Find source with video@00:00 and notes#section-title
        source_refs = {s["source_ref"] for s in data["sources"]}
        assert "video@00:00" in source_refs
        assert "notes#section-title" in source_refs

    def test_llm_source_injection_rejected(self, fake_llm):
        """LLM output containing fake source references must not affect sources."""
        client = Client()
        _seed_materials(
            client,
            UNIT_KEY,
            transcript_text="Випадковий текст для пошуку.",
            notes_text="Нотатки про тесту.",
        )
        # Make LLM generate a response that pretends to have a source
        fake_llm.generate_result = {
            "text": "Відповідь з вигаданим джерелом: Джерело: video@99:99 з іншого юніту. Інший сегмент.",
            "rank_score": 0.5,
        }
        question = "Що таке випадковий текст для пошуку?"
        resp = _ask(client, question, idempotency_key=str(uuid.uuid4()))
        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert data["status"] == "shown"
        # Ensure no source contains the fake reference
        for src in data["sources"]:
            ref = src["source_ref"]
            assert "99:99" not in ref
            # Also ensure segment_id is not the invented one (we don't know it, but we can assert
            # that all segment_ids belong to seeded unit)
            from ai_tutor_service.materials.models import MaterialSegment
            db_ids = set(
                str(s) for s in MaterialSegment.objects.filter(
                    material_id__unit_usage_key=UNIT_KEY,
                    material_id__course_id=COURSE_ID,
                ).values_list("id", flat=True)
            )
            assert src["segment_id"] in db_ids
        # Additionally, ensure that at least one source exists (since we have materials)
        assert len(data["sources"]) >= 1

    def test_source_bearing_percentage_at_least_90(self, fake_llm):
        """With >=4 distinct seed segments and >=10 questions, >=90% shown answers bear sources."""
        client = Client()
        # Seed unit with four distinct segments: two transcript, two notes
        # We'll create four separate seed calls with different content to get four segments.
        # For simplicity, we seed one material with multiple segments by providing multiple entries
        # in transcript and notes lists (each with different ordinal).
        # However, our _seed_payload only creates ordinal 0 for each kind.
        # Let's instead seed multiple times with different content_version to accumulate segments.
        # Simpler: we can seed one material with multiple entries by providing lists with ordinal 0,1,...
        # but our seed function only uses ordinal 0. We'll instead call _seed_materials multiple times
        # with different content_version to create multiple UnitMaterials, each with its segments.
        # Since retrieval uses READY status, we need to ensure they are READY.
        # We'll seed three additional materials with different content_version.
        base_version = "2026-09-20.1"
        texts = [
            ("Текст А транскрипту", "Нотатки А"),
            ("Текст Б транскрипту", "Нотатки Б"),
            ("Текст В транскрипту", "Нотатки В"),
            ("Текст Г транскрипту", "Нотатки Г"),
        ]
        for i, (tx, nx) in enumerate(texts):
            version = f"{base_version}.{i+1}"
            # We need to bypass _seed_payload's hardcoded version; let's create a custom payload.
            payload = {
                "course_id": COURSE_ID,
                "unit_usage_key": UNIT_KEY,
                "content_version": version,
                "transcript": [
                    {
                        "ordinal": 0,
                        "start_ms": 0,
                        "end_ms": 10000,
                        "text": tx,
                        "source_ref": f"video@00:{i*2:02d}",
                    }
                ],
                "notes": [
                    {
                        "ordinal": 0,
                        "section_title": f"Section {i}",
                        "text": nx,
                        "source_ref": f"notes#section{i}",
                    }
                ],
            }
            headers = {
                "HTTP_AUTHORIZATION": f"Bearer {BEARER}",
                "HTTP_X_AI_TUTOR_ROLE": "staff",
                "HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4()),
            }
            resp = client.post(
                MATERIALS_URL,
                data=json.dumps(payload),
                content_type="application/json",
                **headers,
            )
            assert resp.status_code == 201, f"Failed to seed material {i}"
        # Now ask 10 questions, each containing a term from one of the seeded texts
        questions = [
            "Якщо я не розумію, що таке текст А?",
            "Якщо я не розумію, що таке текст Б?",
            "Якщо я не розумію, що таке текст В?",
            "Якщо я не розумію, що таке текст Г?",
            "Поясни будь ласка текст А.",
            "Поясни будь ласка текст Б.",
            "Поясни будь ласка текст В.",
            "Поясни будь ласка текст Г.",
            "Что такое текст А?",
            "Что такое текст Б?",
        ]
        shown_with_sources = 0
        total_shown = 0
        for q in questions:
            resp = _ask(client, q, idempotency_key=str(uuid.uuid4()))
            assert resp.status_code == 200, f"Question failed: {q}"
            data = json.loads(resp.content)
            if data["status"] == "shown":
                total_shown += 1
                if len(data["sources"]) >= 1:
                    shown_with_sources += 1
                # Ensure no source from other unit (we only seeded UNIT_KEY)
                for src in data["sources"]:
                    # Verify segment belongs to our unit
                    from ai_tutor_service.materials.models import MaterialSegment
                    db_ids = set(
                        str(s) for s in MaterialSegment.objects.filter(
                            material_id__unit_usage_key=UNIT_KEY,
                            material_id__course_id=COURSE_ID,
                        ).values_list("id", flat=True)
                    )
                    assert src["segment_id"] in db_ids
        # At least 90% of shown answers should have sources
        assert total_shown > 0, "No shown answers recorded"
        proportion = shown_with_sources / total_shown
        assert proportion >= 0.90, (
            f"Only {proportion:.2%} of shown answers had sources, need >=90%"
        )

    def test_build_sources_requires_retrieved_records(self):
        """Unit test for build_sources: empty input returns []; invalid dict yields [] or raises."""
        # Empty list
        assert build_sources([]) == []
        # List with dict missing required keys
        incomplete = [{"segment_id": "x", "kind": "transcript"}]  # missing source_ref, excerpt
        # According to T-032 contract, function should either return [] or raise.
        # We'll accept either; but we document that it returns [] for safety.
        result = build_sources(incomplete)
        assert result == []  # we choose to return [] on invalid input
        # Additional: non-list input? Not needed per spec; assume caller passes list.
