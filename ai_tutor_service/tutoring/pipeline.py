# impl: FR-002-01
# impl: FR-002-03
# impl: FR-002-04
# impl: FR-002-05
# impl: FR-002-06
# impl: FR-002-07
# impl: FR-002-09
"""
TutoringPipeline — the core AI tutor pipeline.

Executes the seam: student Message → retrieval → [generation → guard] → tutor Message
within a single atomic transaction. All prompts/replies/timeouts/models come from
tutor_config.yaml; nothing is hard-coded.
"""

import hashlib
import json
import logging
import time
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import OperationalError, transaction
from django.utils import timezone

from ai_tutor_service.config import load_tutor_config
from ai_tutor_service.conversations.models import Conversation, Message
from ai_tutor_service.guard.audit import record_block
from ai_tutor_service.limits.models import IdempotencyRecord
from ai_tutor_service.guard.solution_guard import SolutionGuard
from ai_tutor_service.materials.retriever import MaterialRetriever
from ai_tutor_service.providers.client import LLMClient
from ai_tutor_service.providers.models import LLMUsageLog
from ai_tutor_service.providers.usage import record_usage
from ai_tutor_service.tutoring.grounding import build_sources
from ai_tutor_service.tutoring.policy import build_tutoring_policy
from ai_tutor_service.tutoring.relevance import RelevancePolicy

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parents[1] / "tutor_config.yaml"


def _hash_payload(payload: dict[str, Any]) -> str:
    """SHA-256 hex digest of a normalized JSON payload."""
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class TutoringPipeline:
    """Runs the tutoring pipeline for a single /ask request."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or load_tutor_config(str(CONFIG_PATH))
        self._client: LLMClient | None = None

    @property
    def client(self) -> LLMClient:
        if self._client is None:
            self._client = LLMClient(
                config=self.config,
                api_key=getattr(settings, "AI_TUTOR_LLM_API_KEY", ""),
            )
        return self._client

    def run(
        self,
        *,
        question: str,
        user_id: str,
        course_id: str,
        unit_usage_key: str,
        conversation_id: uuid.UUID | None,
        idempotency_key: uuid.UUID,
        request_id: uuid.UUID,
        daily_remaining: int,
    ) -> dict[str, Any]:
        """Execute the full tutoring pipeline and return the terminal response."""
        payload = {
            "question": question,
            "user_id": user_id,
            "course_id": course_id,
            "unit_usage_key": unit_usage_key,
            "conversation_id": str(conversation_id) if conversation_id else None,
        }
        payload_hash = _hash_payload(payload)

        # --- idempotency check (before mutation) ---
        try:
            existing = IdempotencyRecord.objects.get(request_id=idempotency_key)
        except IdempotencyRecord.DoesNotExist:
            pass
        else:
            if existing.payload_hash != payload_hash:
                raise IdempotencyConflict()
            return existing.response

        # --- conversation resolution ---
        if conversation_id is None:
            conversation = self._create_conversation(user_id, course_id, unit_usage_key)
        else:
            conversation = self._resolve_conversation(conversation_id, user_id, course_id, unit_usage_key)

        start_ms = time.monotonic()
        status = "error"
        answer = ""
        topic = "other"
        sources: list[dict[str, Any]] = []
        blocked_reason: str | None = None

        with transaction.atomic():
            # student message
            Message.objects.create(
                conversation_id=conversation,
                role=Message.Role.STUDENT,
                text=question,
                status=Message.Status.ASKED,
                config_version=self.config["version"],
            )

            # --- retrieval ---
            retriever = MaterialRetriever(self.config)
            segments = retriever.search(question, course_id, unit_usage_key)

            # Check if unit has any READY materials at all
            from ai_tutor_service.materials.repository import MaterialRepository
            repo = MaterialRepository()
            has_ready_materials = repo.get_latest_ready_material(course_id, unit_usage_key) is not None

            # --- relevance policy (replaces inline T-020 logic) ---
            policy = RelevancePolicy(self.config, client=self.client)
            decision = policy.decide(
                question=question,
                has_ready_materials=has_ready_materials,
                retrieved_segments=segments,
                user_id=user_id,
                request_id=request_id,
            )

            if decision is not None:
                # no_materials or off_topic — decision already contains answer/topic
                status = decision.status
                answer = decision.answer
                topic = decision.topic
                sources = decision.sources
            else:
                # --- generation ---
                tutor_prompt, model_id = build_tutoring_policy(question, segments, self.config)
                gen_timeout = self.config["generation_timeout_seconds"]
                gen_result = self.client.generate(tutor_prompt, model_id, gen_timeout)
                try:
                    gen_usage = gen_result.get("usage") or {}
                    record_usage(
                        request_id=request_id,
                        user_id=user_id,
                        operation=LLMUsageLog.Operation.GENERATE,
                        input_tokens=gen_usage.get("input_tokens"),
                        output_tokens=gen_usage.get("output_tokens"),
                        model_id=model_id,
                        config_version=self.config["version"],
                    )
                except OperationalError as exc:
                    logger.warning(
                        "record_usage failed for generate (request_id=%s): %s",
                        request_id,
                        exc,
                        exc_info=True,
                    )
                candidate_text = gen_result["text"]

                # --- guard ---
                guard = SolutionGuard(self.config, client=self.client)
                verdict = guard.check(candidate_text)
                try:
                    guard_usage = guard.last_usage or {}
                    record_usage(
                        request_id=request_id,
                        user_id=user_id,
                        operation=LLMUsageLog.Operation.GUARD,
                        input_tokens=guard_usage.get("input_tokens"),
                        output_tokens=guard_usage.get("output_tokens"),
                        model_id=self.config["guard_model_id"],
                        config_version=self.config["version"],
                    )
                except OperationalError as exc:
                    logger.warning(
                        "record_usage failed for guard (request_id=%s): %s",
                        request_id,
                        exc,
                        exc_info=True,
                    )

                if verdict["contains_solution"]:
                    status = "blocked"
                    answer = self.config["replies"]["blocked"]
                    blocked_reason = verdict["reason"]
                    sources = []
                    topic = "other"
                else:
                    status = "shown"
                    answer = candidate_text
                    topic = self._pick_topic(segments)
                    sources = build_sources(segments)
                    # safety: ensure shown never has empty sources (Message.clean will raise)
                    if not sources:
                        # This should be impossible with valid segments from retriever,
                        # but if it happens, fall back to no_materials behavior.
                        status = "no_materials"
                        answer = self.config["replies"]["no_materials"]
                        sources = []

            # --- tutor message ---
            tutor_message = Message.objects.create(
                conversation_id=conversation,
                role=Message.Role.TUTOR,
                text=answer,
                status=status,
                topic=topic,
                sources=sources,
                blocked_reason=blocked_reason,
                latency_ms=int((time.monotonic() - start_ms) * 1000),
                route="default",
                config_version=self.config["version"],
            )

            # --- atomic block persistence (FR-002-07) ---
            # Blocked response is only returned after durable ack:
            # BlockRecord is created in the SAME transaction as the
            # blocked Message. If BlockRecord.save fails (OperationalError),
            # the entire transaction rolls back → no blocked response,
            # no candidate persisted → /ask maps to 503.
            if status == "blocked":
                record_block(
                    message=tutor_message,
                    conversation=conversation,
                    user_id=user_id,
                    course_id=course_id,
                    unit_usage_key=unit_usage_key,
                    question=question,
                    reason=blocked_reason,
                    guard_model_id=self.config["guard_model_id"],
                    config_version=self.config["version"],
                )

            elapsed_ms = int((time.monotonic() - start_ms) * 1000)

            response = {
                "request_id": str(request_id),
                "conversation_id": str(conversation.id),
                "status": status,
                "answer": answer,
                "topic": topic,
                "sources": sources,
                "blocked_reason": blocked_reason,
                "daily_remaining": daily_remaining,
                "latency_ms": elapsed_ms,
                "route": "default",
                "config_version": self.config["version"],
            }

            # Store idempotency record AFTER success (not before)
            IdempotencyRecord.objects.create(
                request_id=idempotency_key,
                user_id=user_id,
                payload_hash=payload_hash,
                response=response,
            )

        return response

    def _create_conversation(
        self, user_id: str, course_id: str, unit_usage_key: str
    ) -> Conversation:
        expires_at = timezone.now() + timedelta(days=self.config["conversation_ttl_days"])
        return Conversation.objects.create(
            user_id=user_id,
            course_id=course_id,
            unit_usage_key=unit_usage_key,
            expires_at=expires_at,
        )

    def _resolve_conversation(
        self,
        conversation_id: uuid.UUID,
        user_id: str,
        course_id: str,
        unit_usage_key: str,
    ) -> Conversation:
        try:
            conversation = Conversation.objects.get(pk=conversation_id)
        except Conversation.DoesNotExist:
            raise ConversationNotFoundError()
        if (
            conversation.user_id != user_id
            or conversation.course_id != course_id
            or conversation.unit_usage_key != unit_usage_key
        ):
            raise ActorMismatchError()
        return conversation

    @staticmethod
    def _pick_topic(segments: list[dict[str, Any]]) -> str:
        """Pick topic from the top source_ref or 'other'."""
        if segments:
            source_ref = segments[0].get("source_ref", "")
            if source_ref:
                return source_ref
        return "other"


class IdempotencyConflict(Exception):
    """Same idempotency key with different payload."""


class ConversationNotFoundError(Exception):
    """Conversation ID not found."""


class ActorMismatchError(Exception):
    """Conversation belongs to a different actor."""
