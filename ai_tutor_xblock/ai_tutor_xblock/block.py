# impl: FR-002-01
# impl: FR-002-02
# impl: FR-002-10
"""
AI Tutor XBlock - student gate, ask/history JSON handlers, local error mapping.

Handlers implement FR-002-10: exact JSON request/response contracts,
localized safe error envelopes and terminal status mapping. The service
client seam is the module-level ``TutorServiceClient`` symbol; config is
fetched once per handler call because ``config_version`` must be the last
successful projection for that request and cannot be stale/cached here.
"""

import json
import uuid
from pathlib import Path

from django.conf import settings
from django.template import Context, Template
from xblock.core import XBlock
from xblock.exceptions import JsonHandlerError
from xblock.fields import Scope, String
from xblock.fragment import Fragment

from .client import (
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    QuotaExceededError,
    SchemaError,
    ServiceUnavailableError,
    TimeoutError,
    TutorServiceClient,
    UnauthorizedError,
)
from .guards import EnrollmentGuard, GuardResult


# ---- Local error/status mapping (xblock-interface.md §2) ----
# tuple: (service_error_class, http_status, error_code, safe_message, can_retry)
_ERROR_MAP = {
    BadRequestError: (400, "bad_request", "Невалідний запит", False),
    UnauthorizedError: (
        503,
        "service_unavailable",
        "Репетитор тимчасово недоступний. Спробуйте ще раз.",
        True,
    ),
    ForbiddenError: (
        503,
        "service_unavailable",
        "Репетитор тимчасово недоступний. Спробуйте ще раз.",
        True,
    ),
    NotFoundError: (
        404,
        "conversation_not_found",
        "Діалог не знайдено",
        False,
    ),
    QuotaExceededError: (
        429,
        "quota_exceeded",
        "Щоденний ліміт вичерпано",
        False,
    ),
    ServiceUnavailableError: (
        503,
        "service_unavailable",
        "Репетитор тимчасово недоступний. Спробуйте ще раз.",
        True,
    ),
    TimeoutError: (
        504,
        "timeout",
        "Час очікування відповіді вичерпано. Спробуйте ще раз.",
        True,
    ),
    SchemaError: (
        503,
        "invalid_service_response",
        "Невірний формат відповіді сервісу",
        True,
    ),
}

_ACCESS_DENIED_MESSAGE = "Доступ заборонено"
_GENERIC_SERVICE_MESSAGE = "Репетитор тимчасово недоступний. Спробуйте ще раз."

ASK_EXACT_KEYS = {"question", "conversation_id", "request_id"}
HISTORY_EXACT_KEYS = {"conversation_id"}

# history-specific mapping (xblock-interface.md §3): service 403 (owner
# mismatch) is returned as generic 404 so that the existence of a foreign
# conversation never leaks.
_HISTORY_ERROR_MAP = {
    **_ERROR_MAP,
    ForbiddenError: (404, "conversation_not_found", "Діалог не знайдено", False),
}


def _make_client() -> TutorServiceClient:
    """Build the service client from Django settings (deployment seams)."""
    return TutorServiceClient(
        base_url=getattr(settings, "AI_TUTOR_SERVICE_URL", ""),
        shared_secret=getattr(settings, "AI_TUTOR_SHARED_SECRET", ""),
    )


def _is_valid_uuid(value) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, TypeError):
        return False


def _resolve_request_id(body) -> str:
    """Echo request_id from JSON body when valid, otherwise fresh UUID."""
    try:
        data = json.loads(body)
        if isinstance(data, dict) and "request_id" in data:
            rid = data["request_id"]
            if _is_valid_uuid(rid):
                return rid
    except (ValueError, TypeError):
        pass
    return str(uuid.uuid4())


def _access_denied_envelope(body) -> dict:
    return {
        "status": "error",
        "error_code": "access_denied",
        "message": _ACCESS_DENIED_MESSAGE,
        "can_retry": False,
        "request_id": _resolve_request_id(body),
        "config_version": "unknown",
    }


def _validation_error_envelope(body, config_version: str) -> dict:
    return {
        "status": "error",
        "error_code": "invalid_request",
        "message": "Невалідний запит",
        "can_retry": False,
        "request_id": _resolve_request_id(body),
        "config_version": config_version,
    }


def _service_error_map(exc, history: bool = False) -> tuple:
    """Return (http_status, error_code, message, can_retry) for a service error."""
    error_map = _HISTORY_ERROR_MAP if history else _ERROR_MAP
    return error_map.get(
        type(exc),
        (
            503,
            "invalid_service_response",
            _GENERIC_SERVICE_MESSAGE,
            True,
        ),
    )


def _service_error_envelope(exc, body, config_version: str, history: bool = False) -> dict:
    http_status, error_code, message, can_retry = _service_error_map(exc, history)
    return {
        "status": "error",
        "error_code": error_code,
        "message": message,
        "can_retry": can_retry,
        "request_id": _resolve_request_id(body),
        "config_version": config_version,
    }


def _parse_ask_body(body) -> tuple[dict, str]:
    """Return (parsed dict, config_version). Raises JsonHandlerError on parse/schema failure."""
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        raise JsonHandlerError(
            400,
            json.dumps(
                {
                    "status": "error",
                    "error_code": "invalid_request",
                    "message": "Невалідний запит",
                    "can_retry": False,
                    "request_id": str(uuid.uuid4()),
                    "config_version": "unknown",
                }
            ),
        )

    if not isinstance(data, dict) or set(data.keys()) != ASK_EXACT_KEYS:
        raise JsonHandlerError(
            400,
            json.dumps(
                _validation_error_envelope(body, "unknown")
            ),
        )

    if not _is_valid_uuid(data["request_id"]):
        raise JsonHandlerError(
            400,
            json.dumps(
                _validation_error_envelope(body, "unknown")
            ),
        )

    conversation_id = data["conversation_id"]
    if conversation_id is not None and not _is_valid_uuid(conversation_id):
        raise JsonHandlerError(
            400,
            json.dumps(
                _validation_error_envelope(body, "unknown")
            ),
        )

    return data, "unknown"


class AiTutorXBlock(XBlock):
    """
    Minimal AI Tutor XBlock skeleton that uses EnrollmentGuard as the student gate.
    """

    # Fields (minimal set, can be expanded in later tasks)
    display_name = String(
        display_name="Component Display Name",
        default="AI Tutor",
        scope=Scope.settings,
        help="This name appears in the horizontal navigation at the top of the page."
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._config_cache = None

    def _check_enrollment(self) -> GuardResult:
        """
        Helper that checks enrollment using EnrollmentGuard as the student gate.

        This is the first gate that student handlers (ask/history) and student
        view must pass. Studio view remains author-only via guard's "lms_only"
        deny for Studio runtime.

        Returns:
            GuardResult indicating whether enrollment check passes
        """
        guard = EnrollmentGuard(self.runtime)
        return guard.check()

    def _get_config(self, client, body):
        """Cached public config projection (tutor-service-api.md §6)."""
        if self._config_cache is not None:
            return self._config_cache
        try:
            config = client.config()
        except Exception as exc:
            status_code, error_code, message, can_retry = _service_error_map(exc)
            envelope = {
                "status": "error",
                "error_code": error_code,
                "message": message,
                "can_retry": can_retry,
                "request_id": _resolve_request_id(body),
                "config_version": "unknown",
            }
            raise JsonHandlerError(status_code, json.dumps(envelope))
        self._config_cache = config
        return config

    def ask(self, request, suffix=""):
        """
        Ask a question to the AI tutor service.

        Request body JSON must have exact keys {question, conversation_id,
        request_id}. Identity is derived only from runtime. Errors are returned
        as JsonHandlerError with a safe localized envelope.
        """
        body = request.body if request.body is not None else ""
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")

        guard = self._check_enrollment()
        if not guard.allow:
            raise JsonHandlerError(403, json.dumps(_access_denied_envelope(body)))

        client = _make_client()
        data, _config_version = _parse_ask_body(body)

        # config must be fetched before question limit validation; the block
        # caches the last valid projection per instance (§6).
        config = self._get_config(client, body)

        question = data["question"]
        if not isinstance(question, str):
            raise JsonHandlerError(
                400,
                json.dumps(
                    _validation_error_envelope(body, config.config_version)
                ),
            )

        question = question.strip()
        if not question or len(question) > config.question_max_chars:
            raise JsonHandlerError(
                400,
                json.dumps(
                    _validation_error_envelope(body, config.config_version)
                ),
            )

        request_id = data["request_id"]
        conversation_id = data["conversation_id"]
        user_id = str(self.runtime.user.id)
        course_id = self.runtime.course_id
        unit_usage_key = str(self.scope_ids.usage_id)

        try:
            result = client.ask(
                question=question,
                conversation_id=conversation_id,
                route="default",
                user_id=user_id,
                course_id=course_id,
                unit_usage_key=unit_usage_key,
                request_id=request_id,
            )
        except Exception as exc:
            envelope = _service_error_envelope(exc, body, config.config_version)
            status_code, _, _, _ = _service_error_map(exc)
            raise JsonHandlerError(status_code, json.dumps(envelope))

        return {
            "status": result.status,
            "answer": result.answer,
            "topic": result.topic,
            "sources": [] if result.status == "blocked" else result.sources,
            "conversation_id": result.conversation_id,
            "daily_remaining": result.daily_remaining,
            "latency_ms": result.latency_ms,
            "route": result.route,
            "config_version": result.config_version,
            "can_retry": False,
        }

    def history(self, request, suffix=""):
        """
        Get conversation history.

        Request body JSON must have exact key {conversation_id}. Identity is
        derived only from runtime. Errors are returned as JsonHandlerError
        with a safe localized envelope.
        """
        body = request.body if request.body is not None else ""
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")

        guard = self._check_enrollment()
        if not guard.allow:
            raise JsonHandlerError(403, json.dumps(_access_denied_envelope(body)))

        client = _make_client()

        try:
            data = json.loads(body)
        except (ValueError, TypeError):
            raise JsonHandlerError(
                400,
                json.dumps(
                    {
                        "status": "error",
                        "error_code": "invalid_request",
                        "message": "Невалідний запит",
                        "can_retry": False,
                        "request_id": str(uuid.uuid4()),
                        "config_version": "unknown",
                    }
                ),
            )

        if not isinstance(data, dict) or set(data.keys()) != HISTORY_EXACT_KEYS:
            raise JsonHandlerError(
                400,
                json.dumps(
                    {
                        "status": "error",
                        "error_code": "invalid_request",
                        "message": "Невалідний запит",
                        "can_retry": False,
                        "request_id": str(uuid.uuid4()),
                        "config_version": "unknown",
                    }
                ),
            )

        conversation_id = data["conversation_id"]
        if conversation_id is None or not _is_valid_uuid(conversation_id):
            raise JsonHandlerError(
                400,
                json.dumps(
                    {
                        "status": "error",
                        "error_code": "invalid_request",
                        "message": "Невалідний запит",
                        "can_retry": False,
                        "request_id": str(uuid.uuid4()),
                        "config_version": "unknown",
                    }
                ),
            )

        user_id = str(self.runtime.user.id)
        course_id = self.runtime.course_id
        unit_usage_key = str(self.scope_ids.usage_id)

        try:
            result = client.history(
                conversation_id=conversation_id,
                user_id=user_id,
                course_id=course_id,
                unit_usage_key=unit_usage_key,
            )
        except Exception as exc:
            envelope = _service_error_envelope(exc, body, "unknown", history=True)
            status_code, _, _, _ = _service_error_map(exc, history=True)
            raise JsonHandlerError(status_code, json.dumps(envelope))

        return {
            "conversation_id": result.conversation_id,
            "messages": result.messages,
            "daily_remaining": result.daily_remaining,
            "config_version": result.config_version,
        }

    # TODO: Implement student_view, studio_view, and handlers in subsequent tasks
    # T-025: ask/history handlers
    # T-026: history handler
    # T-027: student_view JS tests
    # T-028: student view implementation
    # T-029: Studio view tests
    # T-030: Studio view implementation

    # Placeholder methods to satisfy XBlock interface (will be implemented later)
    def student_view(self, context=None):
        """
        Student chat view (FR-002-01, xblock-interface.md §4).

        EnrollmentGuard runs first: denied runtimes get an empty fragment with
        no chat markup, handler URLs or service data, and the client is never
        instantiated. Enrolled LMS renders templates/student.html with only
        the allowed projection: initial LOADING state, config_version,
        materials_status and handler URLs.
        """
        guard = self._check_enrollment()
        if not guard.allow:
            return Fragment()

        client = _make_client()
        config = self._get_config(client, "")
        course_id = self.runtime.course_id
        unit_usage_key = str(self.scope_ids.usage_id)
        materials = client.materials_status(course_id, unit_usage_key)

        template_text = (Path(__file__).resolve().parent / "templates" / "student.html").read_text(encoding="utf-8")
        template = Template(template_text)
        html = template.render(Context({
            "config_version": config.config_version,
            "materials_status": materials.status,
            "ask_url": self.runtime.handler_url(self, "ask"),
            "history_url": self.runtime.handler_url(self, "history"),
        }))
        fragment = Fragment()
        fragment.add_content(html)
        return fragment

    def studio_view(self, context=None):
        """Studio view - to be implemented in T-030."""
        # TODO: Implement proper studio view (author-only)
        # The EnrollmentGuard will deny Studio runtime with "lms_only" before
        # reaching any student path, but Studio view itself needs author check
        frag = Fragment()
        frag.add_content("<p>AI Tutor XBlock - Studio View (skeleton)</p>")
        return frag
