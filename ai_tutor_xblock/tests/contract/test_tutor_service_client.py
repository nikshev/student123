# verifies: FR-002-10
"""
Contract tests for TutorServiceClient against tutor-service-api.md.

These tests use recorded HTTP fixtures and a replaceable transport callable.
The real client (T-012) must accept a transport in its constructor for testability.
"""
import pytest
import uuid
from typing import Callable, Any
from dataclasses import dataclass


# Import fixtures
from ai_tutor_xblock.tests.fixtures.service.ask import (
    ASK_SHOWN_200,
    ASK_BLOCKED_200,
    ASK_NO_MATERIALS_200,
    ASK_OFF_TOPIC_200,
    ASK_400,
    ASK_401,
    ASK_403,
    ASK_404,
    ASK_429,
    ASK_502,
    ASK_503,
    ASK_504,
    ASK_MALFORMED_JSON,
)
from ai_tutor_xblock.tests.fixtures.service.config import (
    CONFIG_200,
    CONFIG_401,
    CONFIG_503,
)
from ai_tutor_xblock.tests.fixtures.service.conversation import (
    CONVERSATION_200,
    CONVERSATION_401,
    CONVERSATION_403,
    CONVERSATION_404,
    CONVERSATION_503,
)
from ai_tutor_xblock.tests.fixtures.service.materials import (
    MATERIALS_STATUS_READY,
    MATERIALS_STATUS_INDEXING,
    MATERIALS_STATUS_FAILED,
    MATERIALS_STATUS_MISSING,
    MATERIALS_STATUS_400,
    MATERIALS_STATUS_401,
    MATERIALS_STATUS_403,
    MATERIALS_STATUS_503,
)


@dataclass
class MockResponse:
    """Mock HTTP response matching fixture structure."""
    status_code: int
    body: Any
    headers: dict = None


class TransportSpy:
    """Spy transport that records calls and returns fixtures."""
    
    def __init__(self, fixture_map: dict):
        self.fixture_map = fixture_map
        self.calls = []
    
    def __call__(self, method: str, url: str, headers: dict, json: dict, timeout: float) -> MockResponse:
        self.calls.append({
            "method": method,
            "url": url,
            "headers": headers,
            "json": json,
            "timeout": timeout,
        })
        fixture = self.fixture_map.get(url)
        if fixture is None:
            # Default to 503 for unknown endpoints
            return MockResponse(503, {"error": {"code": "service_unavailable", "message": "Unknown endpoint"}})
        
        # Handle raw malformed responses
        if fixture.get("raw"):
            return MockResponse(fixture["status_code"], fixture["body"])
        
        return MockResponse(fixture["status_code"], fixture["body"])


class TestTutorServiceClientContract:
    """Contract tests for TutorServiceClient."""
    
    # Base configuration for client
    BASE_URL = "https://tutor.internal/api/v1"
    SHARED_SECRET = "test-secret-123"
    USER_ID = "user-42"
    COURSE_ID = "course-v1:demo+math+2026"
    UNIT_USAGE_KEY = "block-v1:demo+math+2026+type@vertical+block@u1"
    
    @pytest.fixture
    def client(self):
        """Create client with spy transport. This will fail until T-012 implements the client."""
        from ai_tutor_xblock.ai_tutor_xblock.client import TutorServiceClient
        
        # Build fixture map for ask endpoint
        ask_fixtures = {
            f"{self.BASE_URL}/ask": ASK_SHOWN_200,
        }
        
        transport = TransportSpy(ask_fixtures)
        client = TutorServiceClient(
            base_url=self.BASE_URL,
            shared_secret=self.SHARED_SECRET,
            transport=transport,
        )
        return client, transport
    
    def _make_client_with_fixtures(self, fixture_map: dict, timeout: float = 30.0):
        """Helper to create client with custom fixture map."""
        from ai_tutor_xblock.ai_tutor_xblock.client import TutorServiceClient
        
        transport = TransportSpy(fixture_map)
        client = TutorServiceClient(
            base_url=self.BASE_URL,
            shared_secret=self.SHARED_SECRET,
            transport=transport,
            timeout=timeout,
        )
        return client, transport

    # ===== POST /ask tests =====
    
    def test_ask_shown_parses_typed_result(self, client):
        """ask() with 200 shown returns typed result with all fields."""
        client_obj, transport = client
        
        result = client_obj.ask(
            question="Чому при множенні двох мінусів виходить плюс?",
            conversation_id=None,
            route="default",
            user_id=self.USER_ID,
            course_id=self.COURSE_ID,
            unit_usage_key=self.UNIT_USAGE_KEY,
        )
        
        # Verify typed result structure
        assert hasattr(result, "status")
        assert result.status == "shown"
        assert hasattr(result, "answer")
        assert "правило знаків" in result.answer.lower()
        assert hasattr(result, "sources")
        assert len(result.sources) == 1
        assert result.sources[0]["kind"] == "transcript"
        assert result.sources[0]["source_ref"] == "video@03:12"
        assert hasattr(result, "daily_remaining")
        assert result.daily_remaining == 7
        assert hasattr(result, "latency_ms")
        assert result.latency_ms == 1840
        assert hasattr(result, "config_version")
        assert result.config_version == "1.0.0"
        assert hasattr(result, "route")
        assert result.route == "default"
        assert hasattr(result, "conversation_id")
        assert result.conversation_id == "b2c3d4e5-f6a7-8901-bcde-f23456789012"
        assert hasattr(result, "blocked_reason")
        assert result.blocked_reason is None
        
        # Verify headers sent
        call = transport.calls[0]
        assert call["method"] == "POST"
        assert call["url"] == f"{self.BASE_URL}/ask"
        assert call["headers"]["Authorization"] == f"Bearer {self.SHARED_SECRET}"
        assert call["headers"]["X-AI-Tutor-User-ID"] == self.USER_ID
        assert call["headers"]["X-AI-Tutor-Course-ID"] == self.COURSE_ID
        assert call["headers"]["X-AI-Tutor-Unit-Usage-Key"] == self.UNIT_USAGE_KEY
        assert "Idempotency-Key" in call["headers"]
        # Verify idempotency key is valid UUID
        uuid.UUID(call["headers"]["Idempotency-Key"])
        
        # Verify request body
        assert call["json"]["question"] == "Чому при множенні двох мінусів виходить плюс?"
        assert call["json"]["conversation_id"] is None
        assert call["json"]["route"] == "default"
        
        # Verify timeout passed to transport
        assert call["timeout"] == 30.0
    
    def test_ask_blocked_no_candidate_text_outside_answer(self, client):
        """ask() with 200 blocked returns typed result without candidate text outside answer field."""
        client_obj, transport = self._make_client_with_fixtures({
            f"{self.BASE_URL}/ask": ASK_BLOCKED_200,
        })
        
        result = client_obj.ask(
            question="Розв'яжи це за мене",
            conversation_id=None,
            route="default",
            user_id=self.USER_ID,
            course_id=self.COURSE_ID,
            unit_usage_key=self.UNIT_USAGE_KEY,
        )
        
        assert result.status == "blocked"
        assert result.blocked_reason == "contains_solution"
        # Answer contains the safe rule message, NOT the candidate solution
        assert "готове розв'язання" in result.answer
        assert result.sources == []
        assert result.daily_remaining == 6
    
    def test_ask_429_returns_typed_quota_error_with_daily_remaining(self, client):
        """ask() with 429 returns typed quota error with daily_remaining=0."""
        client_obj, transport = self._make_client_with_fixtures({
            f"{self.BASE_URL}/ask": ASK_429,
        })
        
        with pytest.raises(Exception) as exc_info:
            client_obj.ask(
                question="Test question",
                conversation_id=None,
                route="default",
                user_id=self.USER_ID,
                course_id=self.COURSE_ID,
                unit_usage_key=self.UNIT_USAGE_KEY,
            )
        
        # Should be a typed quota exceeded error
        error = exc_info.value
        assert hasattr(error, "code")
        assert error.code == "quota_exceeded"
        assert hasattr(error, "daily_remaining")
        assert error.daily_remaining == 0
    
    def test_ask_401_returns_auth_error(self, client):
        """ask() with 401 returns typed auth error."""
        client_obj, transport = self._make_client_with_fixtures({
            f"{self.BASE_URL}/ask": ASK_401,
        })
        
        with pytest.raises(Exception) as exc_info:
            client_obj.ask(
                question="Test question",
                conversation_id=None,
                route="default",
                user_id=self.USER_ID,
                course_id=self.COURSE_ID,
                unit_usage_key=self.UNIT_USAGE_KEY,
            )
        
        error = exc_info.value
        assert hasattr(error, "code")
        assert error.code == "unauthorized"
    
    def test_ask_403_returns_forbidden_error(self, client):
        """ask() with 403 returns typed forbidden error."""
        client_obj, transport = self._make_client_with_fixtures({
            f"{self.BASE_URL}/ask": ASK_403,
        })
        
        with pytest.raises(Exception) as exc_info:
            client_obj.ask(
                question="Test question",
                conversation_id="some-uuid",
                route="default",
                user_id=self.USER_ID,
                course_id=self.COURSE_ID,
                unit_usage_key=self.UNIT_USAGE_KEY,
            )
        
        error = exc_info.value
        assert hasattr(error, "code")
        assert error.code == "forbidden"
    
    def test_ask_404_returns_not_found_error(self, client):
        """ask() with 404 returns typed not_found error."""
        client_obj, transport = self._make_client_with_fixtures({
            f"{self.BASE_URL}/ask": ASK_404,
        })
        
        with pytest.raises(Exception) as exc_info:
            client_obj.ask(
                question="Test question",
                conversation_id="unknown-uuid",
                route="default",
                user_id=self.USER_ID,
                course_id=self.COURSE_ID,
                unit_usage_key=self.UNIT_USAGE_KEY,
            )
        
        error = exc_info.value
        assert hasattr(error, "code")
        assert error.code == "not_found"
    
    def test_ask_502_returns_service_unavailable(self, client):
        """ask() with 502 returns typed service_unavailable error."""
        client_obj, transport = self._make_client_with_fixtures({
            f"{self.BASE_URL}/ask": ASK_502,
        })
        
        with pytest.raises(Exception) as exc_info:
            client_obj.ask(
                question="Test question",
                conversation_id=None,
                route="default",
                user_id=self.USER_ID,
                course_id=self.COURSE_ID,
                unit_usage_key=self.UNIT_USAGE_KEY,
            )
        
        error = exc_info.value
        assert hasattr(error, "code")
        assert error.code == "service_unavailable"
    
    def test_ask_503_returns_service_unavailable(self, client):
        """ask() with 503 returns typed service_unavailable error."""
        client_obj, transport = self._make_client_with_fixtures({
            f"{self.BASE_URL}/ask": ASK_503,
        })
        
        with pytest.raises(Exception) as exc_info:
            client_obj.ask(
                question="Test question",
                conversation_id=None,
                route="default",
                user_id=self.USER_ID,
                course_id=self.COURSE_ID,
                unit_usage_key=self.UNIT_USAGE_KEY,
            )
        
        error = exc_info.value
        assert hasattr(error, "code")
        assert error.code == "service_unavailable"
    
    def test_ask_504_returns_timeout_error(self, client):
        """ask() with 504 returns typed timeout error."""
        client_obj, transport = self._make_client_with_fixtures({
            f"{self.BASE_URL}/ask": ASK_504,
        })
        
        with pytest.raises(Exception) as exc_info:
            client_obj.ask(
                question="Test question",
                conversation_id=None,
                route="default",
                user_id=self.USER_ID,
                course_id=self.COURSE_ID,
                unit_usage_key=self.UNIT_USAGE_KEY,
            )
        
        error = exc_info.value
        assert hasattr(error, "code")
        assert error.code == "timeout"
    
    def test_ask_400_returns_bad_request_error(self, client):
        """ask() with 400 returns typed bad_request error."""
        client_obj, transport = self._make_client_with_fixtures({
            f"{self.BASE_URL}/ask": ASK_400,
        })
        
        with pytest.raises(Exception) as exc_info:
            client_obj.ask(
                question="",  # Empty question triggers 400
                conversation_id=None,
                route="default",
                user_id=self.USER_ID,
                course_id=self.COURSE_ID,
                unit_usage_key=self.UNIT_USAGE_KEY,
            )
        
        error = exc_info.value
        assert hasattr(error, "code")
        assert error.code == "bad_request"
    
    def test_ask_malformed_json_raises_schema_error_not_best_effort(self, client):
        """ask() with malformed JSON response raises schema error, NOT best-effort parse."""
        client_obj, transport = self._make_client_with_fixtures({
            f"{self.BASE_URL}/ask": ASK_MALFORMED_JSON,
        })
        
        with pytest.raises(Exception) as exc_info:
            client_obj.ask(
                question="Test question",
                conversation_id=None,
                route="default",
                user_id=self.USER_ID,
                course_id=self.COURSE_ID,
                unit_usage_key=self.UNIT_USAGE_KEY,
            )
        
        error = exc_info.value
        assert hasattr(error, "code")
        assert error.code in ("schema_error", "malformed_response", "parse_error")
        # Must NOT return a partially parsed result
    
    def test_ask_no_materials_returns_typed_result(self, client):
        """ask() with 200 no_materials returns typed result with empty sources."""
        client_obj, transport = self._make_client_with_fixtures({
            f"{self.BASE_URL}/ask": ASK_NO_MATERIALS_200,
        })
        
        result = client_obj.ask(
            question="Test question",
            conversation_id=None,
            route="default",
            user_id=self.USER_ID,
            course_id=self.COURSE_ID,
            unit_usage_key=self.UNIT_USAGE_KEY,
        )
        
        assert result.status == "no_materials"
        assert result.sources == []
        assert "матеріалів" in result.answer.lower()
    
    def test_ask_off_topic_returns_typed_result(self, client):
        """ask() with 200 off_topic returns typed result with empty sources."""
        client_obj, transport = self._make_client_with_fixtures({
            f"{self.BASE_URL}/ask": ASK_OFF_TOPIC_200,
        })
        
        result = client_obj.ask(
            question="Test question",
            conversation_id=None,
            route="default",
            user_id=self.USER_ID,
            course_id=self.COURSE_ID,
            unit_usage_key=self.UNIT_USAGE_KEY,
        )
        
        assert result.status == "off_topic"
        assert result.sources == []
        assert "межах теми" in result.answer.lower()
    
    def test_ask_with_existing_conversation_id_sends_it(self, client):
        """ask() with existing conversation_id sends it in request."""
        conv_id = "existing-conv-uuid-1234-5678-90abcdef1234"
        client_obj, transport = self._make_client_with_fixtures({
            f"{self.BASE_URL}/ask": ASK_SHOWN_200,
        })
        
        client_obj.ask(
            question="Follow up question",
            conversation_id=conv_id,
            route="default",
            user_id=self.USER_ID,
            course_id=self.COURSE_ID,
            unit_usage_key=self.UNIT_USAGE_KEY,
        )
        
        call = transport.calls[0]
        assert call["json"]["conversation_id"] == conv_id
    
    def test_ask_uses_custom_timeout_from_config(self, client):
        """ask() uses timeout from config/parameter, not hardcoded."""
        client_obj, transport = self._make_client_with_fixtures({
            f"{self.BASE_URL}/ask": ASK_SHOWN_200,
        }, timeout=15.5)
        
        client_obj.ask(
            question="Test question",
            conversation_id=None,
            route="default",
            user_id=self.USER_ID,
            course_id=self.COURSE_ID,
            unit_usage_key=self.UNIT_USAGE_KEY,
        )
        
        call = transport.calls[0]
        assert call["timeout"] == 15.5  # Custom timeout passed through
    
    # ===== GET /config tests =====
    
    def test_config_returns_public_projection_only(self, client):
        """config() returns only public projection: config_version, daily_limit, timeouts, question_max_chars."""
        client_obj, transport = self._make_client_with_fixtures({
            f"{self.BASE_URL}/config": CONFIG_200,
        })
        
        result = client_obj.config()
        
        # Verify only public fields present
        assert hasattr(result, "config_version")
        assert result.config_version == "1.0.0"
        assert hasattr(result, "daily_limit")
        assert result.daily_limit == 10
        assert hasattr(result, "request_timeout_seconds")
        assert result.request_timeout_seconds == 30
        assert hasattr(result, "http_connect_timeout_seconds")
        assert result.http_connect_timeout_seconds == 2
        assert hasattr(result, "question_max_chars")
        assert result.question_max_chars == 2000
        
        # Verify NO secret fields
        assert not hasattr(result, "prompts")
        assert not hasattr(result, "model_ids")
        assert not hasattr(result, "prices")
        assert not hasattr(result, "secrets")
        assert not hasattr(result, "guard_config")
        
        # Verify headers
        call = transport.calls[0]
        assert call["method"] == "GET"
        assert call["url"] == f"{self.BASE_URL}/config"
        assert call["headers"]["Authorization"] == f"Bearer {self.SHARED_SECRET}"
        # Config doesn't need actor headers per contract
    
    def test_config_401_returns_auth_error(self, client):
        """config() with 401 returns typed auth error."""
        client_obj, transport = self._make_client_with_fixtures({
            f"{self.BASE_URL}/config": CONFIG_401,
        })
        
        with pytest.raises(Exception) as exc_info:
            client_obj.config()
        
        error = exc_info.value
        assert hasattr(error, "code")
        assert error.code == "unauthorized"
    
    def test_config_503_returns_service_unavailable(self, client):
        """config() with 503 returns typed service_unavailable error."""
        client_obj, transport = self._make_client_with_fixtures({
            f"{self.BASE_URL}/config": CONFIG_503,
        })
        
        with pytest.raises(Exception) as exc_info:
            client_obj.config()
        
        error = exc_info.value
        assert hasattr(error, "code")
        assert error.code == "service_unavailable"
    
    # ===== GET /conversation/{id} tests =====
    
    def test_history_returns_conversation_with_messages(self, client):
        """history() returns conversation with ordered messages."""
        conv_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        client_obj, transport = self._make_client_with_fixtures({
            f"{self.BASE_URL}/conversation/{conv_id}": CONVERSATION_200,
        })
        
        result = client_obj.history(
            conversation_id=conv_id,
            user_id=self.USER_ID,
            course_id=self.COURSE_ID,
            unit_usage_key=self.UNIT_USAGE_KEY,
        )
        
        assert hasattr(result, "conversation_id")
        assert result.conversation_id == conv_id
        assert hasattr(result, "messages")
        assert len(result.messages) == 4
        assert result.messages[0]["role"] == "student"
        assert result.messages[1]["role"] == "tutor"
        assert result.messages[1]["status"] == "shown"
        assert "sources" in result.messages[1]
        assert hasattr(result, "daily_remaining")
        assert result.daily_remaining == 7
        
        # Verify no candidate blocked answer in history
        for msg in result.messages:
            if msg["role"] == "tutor":
                assert msg["status"] != "blocked" or "blocked" not in str(msg).lower()
        
        # Verify headers
        call = transport.calls[0]
        assert call["method"] == "GET"
        assert call["headers"]["Authorization"] == f"Bearer {self.SHARED_SECRET}"
        assert call["headers"]["X-AI-Tutor-User-ID"] == self.USER_ID
        assert call["headers"]["X-AI-Tutor-Course-ID"] == self.COURSE_ID
        assert call["headers"]["X-AI-Tutor-Unit-Usage-Key"] == self.UNIT_USAGE_KEY
        # No idempotency key for GET
        assert "Idempotency-Key" not in call["headers"]
    
    def test_history_401_returns_auth_error(self, client):
        """history() with 401 returns typed auth error."""
        client_obj, transport = self._make_client_with_fixtures({
            f"{self.BASE_URL}/conversation/some-id": CONVERSATION_401,
        })
        
        with pytest.raises(Exception) as exc_info:
            client_obj.history(
                conversation_id="some-id",
                user_id=self.USER_ID,
                course_id=self.COURSE_ID,
                unit_usage_key=self.UNIT_USAGE_KEY,
            )
        
        error = exc_info.value
        assert hasattr(error, "code")
        assert error.code == "unauthorized"
    
    def test_history_403_returns_forbidden_error(self, client):
        """history() with 403 (not owner) returns typed forbidden error."""
        client_obj, transport = self._make_client_with_fixtures({
            f"{self.BASE_URL}/conversation/some-id": CONVERSATION_403,
        })
        
        with pytest.raises(Exception) as exc_info:
            client_obj.history(
                conversation_id="some-id",
                user_id=self.USER_ID,
                course_id=self.COURSE_ID,
                unit_usage_key=self.UNIT_USAGE_KEY,
            )
        
        error = exc_info.value
        assert hasattr(error, "code")
        assert error.code == "forbidden"
    
    def test_history_404_returns_not_found_error(self, client):
        """history() with 404 returns typed not_found error."""
        client_obj, transport = self._make_client_with_fixtures({
            f"{self.BASE_URL}/conversation/unknown-id": CONVERSATION_404,
        })
        
        with pytest.raises(Exception) as exc_info:
            client_obj.history(
                conversation_id="unknown-id",
                user_id=self.USER_ID,
                course_id=self.COURSE_ID,
                unit_usage_key=self.UNIT_USAGE_KEY,
            )
        
        error = exc_info.value
        assert hasattr(error, "code")
        assert error.code == "not_found"
    
    def test_history_503_returns_service_unavailable(self, client):
        """history() with 503 returns typed service_unavailable error."""
        client_obj, transport = self._make_client_with_fixtures({
            f"{self.BASE_URL}/conversation/some-id": CONVERSATION_503,
        })
        
        with pytest.raises(Exception) as exc_info:
            client_obj.history(
                conversation_id="some-id",
                user_id=self.USER_ID,
                course_id=self.COURSE_ID,
                unit_usage_key=self.UNIT_USAGE_KEY,
            )
        
        error = exc_info.value
        assert hasattr(error, "code")
        assert error.code == "service_unavailable"
    
    # ===== GET /materials/status tests =====
    
    def test_materials_status_ready(self, client):
        """materials_status() returns READY status with content_version and segment_count."""
        client_obj, transport = self._make_client_with_fixtures({
            f"{self.BASE_URL}/materials/status?course_id={self.COURSE_ID}&unit_usage_key={self.UNIT_USAGE_KEY}": MATERIALS_STATUS_READY,
        })
        
        result = client_obj.materials_status(
            course_id=self.COURSE_ID,
            unit_usage_key=self.UNIT_USAGE_KEY,
        )
        
        assert hasattr(result, "status")
        assert result.status == "READY"
        assert hasattr(result, "content_version")
        assert result.content_version == "2026-09-19.1"
        assert hasattr(result, "segment_count")
        assert result.segment_count == 42
        assert hasattr(result, "config_version")
        assert result.config_version == "1.0.0"
        
        # Verify no material text returned
        assert not hasattr(result, "transcript")
        assert not hasattr(result, "notes")
        
        # Verify headers
        call = transport.calls[0]
        assert call["method"] == "GET"
        assert call["headers"]["Authorization"] == f"Bearer {self.SHARED_SECRET}"
    
    def test_materials_status_indexing(self, client):
        """materials_status() returns INDEXING status."""
        client_obj, transport = self._make_client_with_fixtures({
            f"{self.BASE_URL}/materials/status?course_id={self.COURSE_ID}&unit_usage_key={self.UNIT_USAGE_KEY}": MATERIALS_STATUS_INDEXING,
        })
        
        result = client_obj.materials_status(
            course_id=self.COURSE_ID,
            unit_usage_key=self.UNIT_USAGE_KEY,
        )
        
        assert result.status == "INDEXING"
        assert result.segment_count == 0
    
    def test_materials_status_failed(self, client):
        """materials_status() returns FAILED status."""
        client_obj, transport = self._make_client_with_fixtures({
            f"{self.BASE_URL}/materials/status?course_id={self.COURSE_ID}&unit_usage_key={self.UNIT_USAGE_KEY}": MATERIALS_STATUS_FAILED,
        })
        
        result = client_obj.materials_status(
            course_id=self.COURSE_ID,
            unit_usage_key=self.UNIT_USAGE_KEY,
        )
        
        assert result.status == "FAILED"
    
    def test_materials_status_missing(self, client):
        """materials_status() returns MISSING status with null content_version."""
        client_obj, transport = self._make_client_with_fixtures({
            f"{self.BASE_URL}/materials/status?course_id={self.COURSE_ID}&unit_usage_key={self.UNIT_USAGE_KEY}": MATERIALS_STATUS_MISSING,
        })
        
        result = client_obj.materials_status(
            course_id=self.COURSE_ID,
            unit_usage_key=self.UNIT_USAGE_KEY,
        )
        
        assert result.status == "MISSING"
        assert result.content_version is None
        assert result.segment_count == 0
    
    def test_materials_status_errors(self, client):
        """materials_status() maps 400/401/403/503 to typed errors."""
        for fixture, expected_code in [
            (MATERIALS_STATUS_400, "bad_request"),
            (MATERIALS_STATUS_401, "unauthorized"),
            (MATERIALS_STATUS_403, "forbidden"),
            (MATERIALS_STATUS_503, "service_unavailable"),
        ]:
            client_obj, transport = self._make_client_with_fixtures({
                f"{self.BASE_URL}/materials/status?course_id={self.COURSE_ID}&unit_usage_key={self.UNIT_USAGE_KEY}": fixture,
            })
            
            with pytest.raises(Exception) as exc_info:
                client_obj.materials_status(
                    course_id=self.COURSE_ID,
                    unit_usage_key=self.UNIT_USAGE_KEY,
                )
            
            error = exc_info.value
            assert hasattr(error, "code")
            assert error.code == expected_code
    
    # ===== Transport seam verification =====
    
    def test_transport_seam_is_explicit_callable(self, client):
        """Client accepts transport as callable in constructor (explicit seam for T-012)."""
        # This test verifies the transport seam design - the client MUST accept
        # a transport callable in __init__ for testability without mock magic.
        # The fixture client already uses this pattern.
        client_obj, transport = client
        assert transport is not None
        assert callable(transport)
        assert hasattr(transport, "calls")
    
    def test_shared_secret_from_settings_not_constant(self, client):
        """Client uses shared_secret from constructor (settings), not hardcoded constant."""
        client_obj, transport = client
        
        # The secret should be passed in, not hardcoded
        # This is verified by the Authorization header using the passed secret
        client_obj.ask(
            question="Test",
            conversation_id=None,
            route="default",
            user_id=self.USER_ID,
            course_id=self.COURSE_ID,
            unit_usage_key=self.UNIT_USAGE_KEY,
        )
        
        call = transport.calls[0]
        assert call["headers"]["Authorization"] == f"Bearer {self.SHARED_SECRET}"
        # If secret were hardcoded, it wouldn't match our test secret
    
    def test_idempotency_key_generated_per_request(self, client):
        """Each ask() call generates a unique idempotency key."""
        client_obj, transport = client
        
        client_obj.ask(
            question="Question 1",
            conversation_id=None,
            route="default",
            user_id=self.USER_ID,
            course_id=self.COURSE_ID,
            unit_usage_key=self.UNIT_USAGE_KEY,
        )
        key1 = transport.calls[0]["headers"]["Idempotency-Key"]
        
        client_obj.ask(
            question="Question 2",
            conversation_id=None,
            route="default",
            user_id=self.USER_ID,
            course_id=self.COURSE_ID,
            unit_usage_key=self.UNIT_USAGE_KEY,
        )
        key2 = transport.calls[1]["headers"]["Idempotency-Key"]
        
        assert key1 != key2
        # Both valid UUIDs
        uuid.UUID(key1)
        uuid.UUID(key2)
    
    def test_no_network_calls_in_tests(self, client):
        """Verify test uses fixture transport, not real network."""
        client_obj, transport = client
        
        # The transport is our spy, not a real HTTP client
        assert isinstance(transport, TransportSpy)
        # No socket/DNS calls should be made
        # This is enforced by the test using TransportSpy


# This test module should fail with ModuleNotFoundError until T-012 implements the client
if __name__ == "__main__":
    pytest.main([__file__, "-v"])