# impl: FR-002-04
"""
Two-step relevance policy for AI Tutor Service.

Step 1 (fast): Check if retriever returned any segments above min_rank_score.
- If has_ready_materials=False → immediately return NO_MATERIALS (no LLM calls).
- If has_ready_materials=True but retrieved_segments is empty → proceed to Step 2.

Step 2 (LLM): Narrow outline-only off-topic classifier via LLMClient.off_topic.
- Uses prompts.off_topic, model_id, and a reasonable timeout from config.
- Classifier does NOT generate answers; only returns classification (on_topic/off_topic),
  confidence, and top_source_label.
- off_topic → OFF_TOPIC (answer=config["replies"]["off_topic"], sources=[]).
- on_topic but no segments → NO_MATERIALS (answer=config["replies"]["no_materials"], sources=[]).
- LLMError (malformed/timeout) → re-raised as controlled error; NOT converted to
  NO_MATERIALS/OFF_TOPIC.
- generate is NEVER called in no_materials/off_topic paths.

If retrieved_segments is non-empty (≥ min_rank_score) → returns None to signal
"proceed to generation".
"""

from dataclasses import dataclass
from typing import Any, Optional

from ai_tutor_service.providers.client import LLMClient, LLMError


@dataclass(frozen=True)
class RelevanceDecision:
    """
    Decision from the relevance policy.

    Args:
        status: One of "no_materials", "off_topic", or None (proceed to generation).
        answer: Response text to show student (from config.replies).
        topic: Topic label from top source or "other".
        sources: List of source dicts (empty for no_materials/off_topic).
    """
    status: Optional[str]
    answer: str
    topic: str
    sources: list[dict[str, Any]]


class RelevancePolicy:
    """
    Two-step relevance policy for the tutoring pipeline.

    Configuration is taken from tutor_config.yaml via load_tutor_config():
    - retrieval.top_k, retrieval.min_rank_score: retriever thresholds (used by retriever)
    - model_id: model for off-topic classification
    - generation_timeout_seconds: timeout for off-topic classification (reasonable default)
    - prompts.off_topic: prompt template for outline-only classifier
    - replies.no_materials, replies.off_topic: exact responses to student

    The policy does NOT contain hard-coded constants; all thresholds, prompts,
    model IDs, and timeouts come from config.
    """

    def __init__(self, config: dict[str, Any]):
        """
        Initialize policy with validated config.

        Args:
            config: Full tutor config dict from load_tutor_config().
        """
        self.config = config

    def decide(
        self,
        question: str,
        has_ready_materials: bool,
        retrieved_segments: list[dict[str, Any]],
    ) -> Optional[RelevanceDecision]:
        """
        Decide relevance of a question to the current unit materials.

        Args:
            question: Student's question text.
            has_ready_materials: Whether the unit has any READY materials at all.
            retrieved_segments: Segments returned by retriever (already filtered
                               by min_rank_score and top_k).

        Returns:
            RelevanceDecision with status/answer/topic/sources if decision is
            no_materials or off_topic; None if generation should proceed.

        Raises:
            LLMError: If off_topic classifier returns malformed response or times out.
                      This is a controlled error; the caller (pipeline/API) maps it
                      to 502/504. It is NOT swallowed or converted to a status.
        """
        # Step 1: No ready materials at all → immediate NO_MATERIALS, no LLM calls.
        if not has_ready_materials:
            return RelevanceDecision(
                status="no_materials",
                answer=self.config["replies"]["no_materials"],
                topic="other",
                sources=[],
            )

        # Step 1b: Ready materials exist but retrieval returned nothing
        # (i.e., all segments below min_rank_score) → outline classifier.
        if not retrieved_segments:
            # Build off-topic prompt using only unit outline (no segments)
            from ai_tutor_service.tutoring.prompting import build_off_topic_prompt

            prompt = build_off_topic_prompt(question, [], self.config)

            # Use model_id from config and generation_timeout_seconds as a
            # reasonable timeout for the fast classifier call.
            model_id = self.config["model_id"]
            timeout = self.config["generation_timeout_seconds"]

            client = LLMClient(config=self.config)
            off_topic_result = client.off_topic(prompt, model_id, timeout)

            classification = off_topic_result["classification"]
            confidence = off_topic_result["confidence"]
            top_source_label = off_topic_result.get("top_source_label") or "other"

            if classification == "off_topic":
                return RelevanceDecision(
                    status="off_topic",
                    answer=self.config["replies"]["off_topic"],
                    topic=top_source_label,
                    sources=[],
                )
            else:  # on_topic but no segments
                return RelevanceDecision(
                    status="no_materials",
                    answer=self.config["replies"]["no_materials"],
                    topic="other",
                    sources=[],
                )

        # Retrieved segments exist (≥ min_rank_score) → proceed to generation.
        return None