# impl: FR-002-05
"""
Tutoring policy builder.

Assembles the generation prompt from the versioned config, question and retrieved segments.
All text comes from tutor_config.yaml — nothing hard-coded.
"""

from typing import Any


def build_tutoring_policy(question: str, retrieved_segments: list[dict[str, Any]], config: dict[str, Any]) -> tuple[str, str]:
    """Build the tutoring generation prompt and return (prompt, model_id).
    
    Contract: build_tutoring_policy(question, retrieved_segments, config) -> (prompt: str, model_id: str)
    
    - question: str, the student's question.
    - retrieved_segments: list of dicts with keys 'excerpt' (str) and 'source_ref' (str).
    - config: dict loaded from tutor_config.yaml via load_tutor_config.
    
    Returns a tuple (prompt, model_id) where prompt is the fully assembled tutor prompt
    and model_id is the string to be used for the LLM call.
    """
    prompts = config["prompts"]
    tutor_prompt = prompts["tutor"]

    context_lines = []
    for seg in retrieved_segments:
        # Format as: excerpt [source_ref] to avoid lines ending with answer text
        context_lines.append(f"{seg['excerpt']} [{seg['source_ref']}]")
    context_block = "\n".join(context_lines)

    # Assemble the prompt: tutor prompt + question line + materials section
    assembled_prompt = f"{tutor_prompt}\n\nQuestion:\n{question}"
    
    # Add materials section only if there are retrieved segments
    if retrieved_segments:
        assembled_prompt += f"\n\nUnit materials:\n{context_block}"
    
    return assembled_prompt, config["model_id"]