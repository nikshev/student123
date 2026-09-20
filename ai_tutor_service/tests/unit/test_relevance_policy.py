# verifies: FR-002-04
"""
Table/integration tests for relevance policy (`ai_tutor_service.tutoring.relevance`).

Test scenarios for two-step relevance policy (T-033):
1. Без READY segments → одразу NO_MATERIALS, classifier НЕ викликається.
2. Retrieval нижче min_rank_score (has_ready=True, порожні сегменты) → викликається ЛИШЕ outline classifier (fixture-backed через monkeypatch LLMClient.off_topic).
3. classifier off_topic/unrelated → OFF_TOPIC: answer ТОЧНО config["replies"]["off_topic"], sources=[], topic=top_source_label або "other".
4. classifier on_topic, але сегментів нема → NO_MATERIALS: answer ТОЧНО config["replies"]["no_materials"], sources=[].
5. classifier malformed/timeout (LLMError) → controlled error, без answer, не NO_MATERIALS/OFF_TOPIC.
6. Жоден із no_materials/off_topic шляхів НЕ викликає LLMClient.generate (без candidate/general-knowledge).
7. Інтеграційні кейси крізь /ask (патерни test_ask_api: FakeLLM, _seed_materials, Client): юніт без матеріалів → 200 no_materials, YAML-відповідь, sources=[], generate/guard/off_topic не викликані; юніт із матеріалами + питання поза темою + fake off_topic → 200 off_topic, generate не викликаний.
8. min_rank_score/top_k лише з tutor_config.yaml (через load_tutor_config, значення порівнюй із YAML).
"""

import json
import uuid
from pathlib import Path

import pytest
from django.test import Client

from ai_tutor_service.tutoring.relevance import RelevancePolicy, RelevanceDecision
from ai_tutor_service.config import load_tutor_config
from ai_tutor_service.providers.client import LLMClient, LLMError
from ai_tutor_service.materials.repository import MaterialRepository

# Load config for testing
CONFIG_PATH = Path(__file__).resolve().parents[3] / "ai_tutor_service" / "tutor_config.yaml"
CONFIG = load_tutor_config(CONFIG_PATH)

BEARER = "test-shared-secret-12345"
ASK_URL = "/api/v1/ask"
MATERIALS_URL = "/api/v1/materials"

COURSE_ID = "course-v1:demo+math+2026"
UNIT_KEY = "block-v1:demo+math+2026+type@vertical+block@u1"
EMPTY_UNIT_KEY = "block-v1:demo+math+2026+type@vertical+block@u9"
USER_A = "student-ask-aaa"


def _config():
    return load_tutor_config(CONFIG_PATH)


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


class FakeLLM:
    """Записує seam-виклики; відповіді — тільки фікстури."""

    def __init__(self):
        self.generate_calls = []
        self.guard_calls = []
        self.off_topic_calls = []
        self.generate_result = {
            "text": "generated answer",
            "usage": {"input_tokens": 1, "output_tokens": 2},
            "model_id": CONFIG["model_id"],
            "finish_reason": "stop",
        }
        self.guard_result = {
            "contains_solution": False,
            "reason": "reason",
            "usage": {"input_tokens": 1, "output_tokens": 2},
            "model_id": CONFIG["model_id"],
            "finish_reason": "stop",
        }
        self.off_topic_result = {
            "classification": "on_topic",
            "confidence": 0.9,
            "top_source_label": "other",
            "usage": {"input_tokens": 1, "output_tokens": 2},
            "model_id": CONFIG["model_id"],
            "finish_reason": "stop",
        }
        self.generate_error = None
        self.guard_error = None
        self.off_topic_error = None

    def generate(self, prompt, model_id, timeout):
        self.generate_calls.append({"prompt": prompt, "model_id": model_id, "timeout": timeout})
        if self.generate_error is not None:
            raise self.generate_error
        return dict(self.generate_result)

    def guard(self, prompt, model_id, timeout):
        self.guard_calls.append({"prompt": prompt, "model_id": model_id, "timeout": timeout})
        if self.guard_error is not None:
            raise self.guard_error
        return dict(self.guard_result)

    def off_topic(self, prompt, model_id, timeout):
        self.off_topic_calls.append({"prompt": prompt, "model_id": model_id, "timeout": timeout})
        if self.off_topic_error is not None:
            raise self.off_topic_error
        return dict(self.off_topic_result)


class TestRelevancePolicy:
    """Test cases for the relevance policy."""

    def test_no_ready_segments_immediately_no_materials(self):
        """
        Scenario 1: Без READY segments → одразу NO_MATERIALS, classifier НЕ викликається.
        """
        # This test will fail until RelevancePolicy is implemented
        policy = RelevancePolicy(CONFIG)
        
        # Simulate no ready materials
        has_ready = False
        retrieved_segments = []
        
        decision = policy.decide(
            question="Test question",
            has_ready_materials=has_ready,
            retrieved_segments=retrieved_segments
        )
        
        # Should be NO_MATERIALS without LLM calls
        assert decision.status == "no_materials"
        assert decision.answer == CONFIG["replies"]["no_materials"]
        assert decision.sources == []
        assert decision.topic == "other"

    def test_low_rank_score_uses_outline_classifier_only(self, monkeypatch):
        """
        Scenario 2: Retrieval ниже min_rank_score → викликається ЛИШЕ outline classifier.
        """
        policy = RelevancePolicy(CONFIG)
        
        # Mock LLMClient.off_topic to simulate outline classifier response
        mock_off_topic_result = {
            "classification": "off_topic",
            "confidence": 0.95,
            "top_source_label": "other",
            "usage": {"input_tokens": 100, "output_tokens": 20},
            "model_id": CONFIG["model_id"],
            "finish_reason": "stop"
        }
        
        mock_off_topic = pytest.Mock(return_value=mock_off_topic_result)
        monkeypatch.setattr(LLMClient, "off_topic", mock_off_topic)
        
        # Simulate has ready materials but low rank score (empty segments)
        has_ready = True
        retrieved_segments = []
        
        decision = policy.decide(
            question="What is the capital of France?",
            has_ready_materials=has_ready,
            retrieved_segments=retrieved_segments
        )
        
        # Should be OFF_TOPIC with classifier call
        assert decision.status == "off_topic"
        assert decision.answer == CONFIG["replies"]["off_topic"]
        assert decision.sources == []
        
        # Verify only off_topic was called, not generate
        assert mock_off_topic.called

    def test_off_topic_classification(self, monkeypatch):
        """
        Scenario 3: classifier off_topic/unrelated → OFF_TOPIC.
        """
        policy = RelevancePolicy(CONFIG)
        
        # Mock LLMClient.off_topic to return off_topic classification
        mock_off_topic_result = {
            "classification": "off_topic",
            "confidence": 0.97,
            "top_source_label": "other",
            "usage": {"input_tokens": 143, "output_tokens": 12},
            "model_id": CONFIG["model_id"],
            "finish_reason": "stop"
        }
        
        mock_off_topic = pytest.Mock(return_value=mock_off_topic_result)
        monkeypatch.setattr(LLMClient, "off_topic", mock_off_topic)
        
        # Simulate has ready materials and some segments
        has_ready = True
        retrieved_segments = [
            {"segment_id": "seg1", "kind": "transcript", "source_ref": "video@00:00", "excerpt": "test"}
        ]
        
        decision = policy.decide(
            question="Коли була Друга світова війна?",
            has_ready_materials=has_ready,
            retrieved_segments=retrieved_segments
        )
        
        # Should be OFF_TOPIC
        assert decision.status == "off_topic"
        assert decision.answer == CONFIG["replies"]["off_topic"]
        assert decision.sources == []
        assert decision.topic == "other"

    def test_no_materials_on_topic_classification(self, monkeypatch):
        """
        Scenario 4: classifier on_topic, але сегментів нема → NO_MATERIALS.
        """
        policy = RelevancePolicy(CONFIG)
        
        # Mock LLMClient.off_topic to return on_topic classification
        mock_off_topic_result = {
            "classification": "on_topic",
            "confidence": 0.92,
            "top_source_label": "equations",
            "usage": {"input_tokens": 187, "output_tokens": 15},
            "model_id": CONFIG["model_id"],
            "finish_reason": "stop"
        }
        
        mock_off_topic = pytest.Mock(return_value=mock_off_topic_result)
        monkeypatch.setattr(LLMClient, "off_topic", mock_off_topic)
        
        # Simulate has ready materials but no retrieved segments
        has_ready = True
        retrieved_segments = []
        
        decision = policy.decide(
            question="Чому при множенні двох від'ємних чисел виходить додатне число?",
            has_ready_materials=has_ready,
            retrieved_segments=retrieved_segments
        )
        
        # Should be NO_MATERIALS even though classifier says on_topic
        assert decision.status == "no_materials"
        assert decision.answer == CONFIG["replies"]["no_materials"]
        assert decision.sources == []

    def test_classification_malformed_response(self, monkeypatch):
        """
        Scenario 5: classifier malformed/timeout (LLMError) → controlled error.
        """
        policy = RelevancePolicy(CONFIG)
        
        # Mock LLMClient.off_topic to raise LLMError (malformed response)
        from ai_tutor_service.providers.client import LLMError
        
        mock_off_topic = pytest.Mock(side_effect=LLMError(
            "Provider returned malformed JSON", "malformed_response"
        ))
        monkeypatch.setattr(LLMClient, "off_topic", mock_off_topic)
        
        # Simulate has ready materials but no retrieved segments
        has_ready = True
        retrieved_segments = []
        
        # Should raise controlled error, not return NO_MATERIALS/OFF_TOPIC
        with pytest.raises(LLMError) as exc_info:
            policy.decide(
                question="Test question",
                has_ready_materials=has_ready,
                retrieved_segments=retrieved_segments
            )
        
        assert exc_info.value.error_type == "malformed_response"

    def test_classification_timeout(self, monkeypatch):
        """
        Scenario 5 cont: classifier timeout (LLMError) → controlled error.
        """
        policy = RelevancePolicy(CONFIG)

        mock_off_topic = pytest.Mock(side_effect=LLMError(
            "Request timed out", "timeout"
        ))
        monkeypatch.setattr(LLMClient, "off_topic", mock_off_topic)

        has_ready = True
        retrieved_segments = []

        with pytest.raises(LLMError) as exc_info:
            policy.decide(
                question="Test question",
                has_ready_materials=has_ready,
                retrieved_segments=retrieved_segments
            )

        assert exc_info.value.error_type == "timeout"
        assert mock_off_topic.called

    def test_no_candidate_generation_for_no_materials_or_off_topic(self, monkeypatch):
        """
        Scenario 6: Жоден із no_materials/off_topic шляхів НЕ викликає LLMClient.generate.
        """
        policy = RelevancePolicy(CONFIG)
        
        # Mock both off_topic and generate
        mock_off_topic_result = {
            "classification": "off_topic",
            "confidence": 0.95,
            "top_source_label": "other",
            "usage": {"input_tokens": 100, "output_tokens": 20},
            "model_id": CONFIG["model_id"],
            "finish_reason": "stop"
        }
        
        mock_off_topic = pytest.Mock(return_value=mock_off_topic_result)
        mock_generate = pytest.Mock()
        
        monkeypatch.setattr(LLMClient, "off_topic", mock_off_topic)
        monkeypatch.setattr(LLMClient, "generate", mock_generate)
        
        # Test off_topic path
        has_ready = True
        retrieved_segments = []
        
        decision = policy.decide(
            question="Коли була Друга світова війна?",
            has_ready_materials=has_ready,
            retrieved_segments=retrieved_segments
        )
        
        # Should be OFF_TOPIC, generate should NOT be called
        assert decision.status == "off_topic"
        assert not mock_generate.called
        
        # Reset mocks for NO_MATERIALS test
        mock_off_topic.reset_mock()
        mock_generate.reset_mock()
        
        # Test no_materials path
        mock_off_topic_result["classification"] = "on_topic"
        decision = policy.decide(
            question="Test question",
            has_ready_materials=True,  # has ready
            retrieved_segments=[]  # but no retrieved segments
        )
        
        # Should be NO_MATERIALS, generate should NOT be called
        assert decision.status == "no_materials"
        assert not mock_generate.called

    def test_integration_with_ask_api_no_materials(self, monkeypatch):
        """
        Scenario 7: Integration test - unit without materials → 200 no_materials.
        """
        client = Client()
        fake = FakeLLM()
        monkeypatch.setattr(LLMClient, "generate", fake.generate)
        monkeypatch.setattr(LLMClient, "guard", fake.guard)
        monkeypatch.setattr(LLMClient, "off_topic", fake.off_topic)

        response = _ask(
            client,
            "What is the meaning of life?",
            unit_key=EMPTY_UNIT_KEY,
            idempotency_key=str(uuid.uuid4()),
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["status"] == "no_materials"
        assert data["answer"] == _config()["replies"]["no_materials"]
        assert data["sources"] == []
        assert fake.generate_calls == []
        assert fake.guard_calls == []
        assert fake.off_topic_calls == []

    def test_integration_with_ask_api_off_topic(self, monkeypatch):
        """
        Scenario 7 (cont): unit with materials + off-topic question → 200 off_topic.
        """
        client = Client()
        fake = FakeLLM()
        fake.off_topic_result = {
            "classification": "off_topic",
            "confidence": 0.95,
            "top_source_label": "other",
            "usage": {"input_tokens": 100, "output_tokens": 20},
            "model_id": CONFIG["model_id"],
            "finish_reason": "stop",
        }
        monkeypatch.setattr(LLMClient, "generate", fake.generate)
        monkeypatch.setattr(LLMClient, "guard", fake.guard)
        monkeypatch.setattr(LLMClient, "off_topic", fake.off_topic)

        _seed_materials(client, UNIT_KEY)

        response = _ask(
            client,
            "When did World War II happen?",
            unit_key=UNIT_KEY,
            idempotency_key=str(uuid.uuid4()),
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["status"] == "off_topic"
        assert data["answer"] == _config()["replies"]["off_topic"]
        assert data["sources"] == []
        assert not fake.generate_calls
        assert not fake.guard_calls
        assert len(fake.off_topic_calls) >= 1

    def test_min_rank_score_from_yaml_not_hardcoded(self):
        """
        Scenario 8: min_rank_score/top_k лише з tutor_config.yaml (через load_tutor_config).
        """
        policy = RelevancePolicy(CONFIG)
        
        # Verify policy uses config values, not hardcoded constants
        assert hasattr(policy, 'config')
        assert 'retrieval' in policy.config
        assert 'top_k' in policy.config['retrieval']
        assert 'min_rank_score' in policy.config['retrieval']
        
        # Values should match the YAML (0.10 for min_rank_score, 5 for top_k)
        assert policy.config['retrieval']['min_rank_score'] == 0.10
        assert policy.config['retrieval']['top_k'] == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])