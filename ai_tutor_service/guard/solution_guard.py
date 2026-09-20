# impl: FR-002-06
"""
Replaceable solution guard for AI Tutor (FR-002-06).

SolutionGuard wraps the LLM guard call and exposes a single
check(candidate_text) -> {contains_solution, reason} interface.
Technical failures (malformed_response, provider_error, timeout) are
re-raised as GuardTechnicalError so that the /ask layer can map them
to 502/504 without ever treating a technical failure as a semantic
block.  Strict parsing is enforced: an invalid verdict structure is
fail-closed and never silently falls back to False.
"""

import logging
from typing import Any

from django.conf import settings

from ai_tutor_service.providers.client import LLMClient, LLMError
from ai_tutor_service.tutoring.prompting import build_guard_prompt

logger = logging.getLogger("ai_tutor_service.guard")


class GuardTechnicalError(LLMError):
    """
    Technical (non-semantic) guard failure.

    Inherits from LLMError so that the existing ``except LLMError``
    handling in api/ask.py continues to map:
      malformed_response -> 502 invalid_upstream_response
      provider_error     -> 502 upstream_error
      timeout            -> 504
    A technical failure is never counted as a semantic block/gate
    numerator and never carries contains_solution.
    """

    def __init__(self, message: str, error_type: str, status_code: int | None = None):
        super().__init__(message=message, error_type=error_type, status_code=status_code)


class SolutionGuard:
    """
    Replaceable solution-detection guard invoked after generation
    and before a Message/API response.
    """

    def __init__(self, config: dict[str, Any], client: LLMClient | None = None):
        self.config = config
        self._client = client

    @property
    def client(self) -> LLMClient:
        if self._client is None:
            return LLMClient(
                config=self.config,
                api_key=getattr(settings, "AI_TUTOR_LLM_API_KEY", ""),
            )
        return self._client

    def check(self, candidate_text: str) -> dict[str, Any]:
        """
        Run the guard over candidate_text.

        Returns a dict with exactly:
          - contains_solution (bool)
          - reason (non-empty str)

        Raises:
            GuardTechnicalError: on LLM transport errors or invalid
                response structure (fail-closed).
        """
        prompt = build_guard_prompt(candidate_text, self.config)
        model_id = self.config["guard_model_id"]
        timeout = self.config["guard_timeout_seconds"]

        try:
            raw = self.client.guard(prompt, model_id, timeout)
        except LLMError as exc:
            raise GuardTechnicalError(
                message=f"Guard {exc.error_type}: {exc.message}",
                error_type=exc.error_type,
                status_code=exc.status_code,
            ) from exc

        self._validate_verdict(raw)

        contains_solution = raw["contains_solution"]
        reason = raw["reason"]

        logger.info(
            "guard check completed: contains_solution=%s",
            contains_solution,
        )

        return {"contains_solution": contains_solution, "reason": reason}

    @staticmethod
    def _validate_verdict(raw: dict[str, Any]) -> None:
        """
        Strict structural validation of the guard response.

        Fail-closed: any structural invalidity (wrong type, missing key,
        empty/whitespace-only reason) raises GuardTechnicalError so the
        candidate is never shown as a result of a parse fallback.
        ``contains_solution`` must be a bool and ``reason`` must be a
        non-empty str (the LLM prompt requests a non-empty reason; a
        blank reason is a malformed verdict, not a valid one — the
        critical invariant enforced here is that an invalid structure
        NEVER silently becomes ``False``).
        """
        if not isinstance(raw.get("contains_solution"), bool):
            raise GuardTechnicalError(
                message="Guard response missing or invalid contains_solution",
                error_type="malformed_response",
            )
        reason = raw.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise GuardTechnicalError(
                message="Guard response missing or invalid reason",
                error_type="malformed_response",
            )
