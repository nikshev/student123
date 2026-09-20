# impl: FR-002-01
"""
Prompt builders for the AI Tutor tutoring pipeline.

All prompts are assembled from versioned tutor_config.yaml — nothing hard-coded.
"""

from typing import Any


def build_guard_prompt(candidate_text: str, config: dict[str, Any]) -> str:
    """Build the solution-guard prompt wrapping the candidate response."""
    prompts = config["prompts"]
    return f"{prompts['guard']}\n\nCandidate response:\n{candidate_text}"


def build_off_topic_prompt(question: str, segments: list[dict[str, Any]], config: dict[str, Any]) -> str:
    """Build the off-topic classifier prompt."""
    prompts = config["prompts"]
    off_topic_prompt = prompts["off_topic"]

    context_lines = []
    for seg in segments:
        context_lines.append(f"[{seg['kind']}] {seg['source_ref']}: {seg['excerpt']}")
    context_block = "\n".join(context_lines)

    return f"{off_topic_prompt}\n\nQuestion: {question}\n\nUnit materials:\n{context_block}"
