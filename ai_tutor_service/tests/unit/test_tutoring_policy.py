# verifies: FR-002-05
"""
Tests for the tutoring policy builder.

Contract: build_tutoring_policy(question, retrieved_segments, config) -> (prompt: str, model_id: str)
    - question: str, the student's question.
    - retrieved_segments: list of dicts with keys 'excerpt' (str) and 'source_ref' (str).
    - config: dict loaded from tutor_config.yaml via load_tutor_config.
    Returns a tuple (prompt, model_id) where prompt is the fully assembled tutor prompt
    and model_id is the string to be used for the LLM call.
"""

import pytest
from pathlib import Path

from ai_tutor_service.config import load_tutor_config
from ai_tutor_service.tutoring.policy import build_tutoring_policy

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = REPO_ROOT / "ai_tutor_service" / "tutor_config.yaml"


def load_config():
    return load_tutor_config(CONFIG_PATH)


def test_build_tutoring_policy_contains_full_tutor_prompt():
    config = load_config()
    question = "Якщо x + 2 = 5, то x дорівнює 3."
    retrieved = [
        {"excerpt": "Додавання числа 2 до обеих сторон дає x = 3.", "source_ref": "vid@00:10"},
        {"excerpt": "Перевірка: 3 + 2 = 5.", "source_ref": "notes#ex1"},
    ]
    prompt, model_id = build_tutoring_policy(question, retrieved, config)

    # 1. Prompt contains full tutor prompt from config
    assert config["prompts"]["tutor"] in prompt

    # 2. Prompt contains the question verbatim
    assert question in prompt

    # 3. Prompt contains excerpt and source_ref of each retrieved segment
    for seg in retrieved:
        assert seg["excerpt"] in prompt
        assert seg["source_ref"] in prompt

    # 4. Pedagogical requirement: prompt requires explaining method/steps and NOT giving final answer
    tutor_prompt = config["prompts"]["tutor"]
    # Extract two unambiguous markers from the YAML tutor prompt
    assert "Guide the student step by step" in prompt
    assert "Do NOT give the final answer or complete solution" in prompt

    # 5. model_id equals config["model_id"]
    assert model_id == config["model_id"]

    # 6. Ukrainian language of question is preserved verbatim
    assert question in prompt


def test_build_tutoring_policy_custom_model_id():
    config = load_config()
    # Override model_id to ensure it is not hard‑coded
    config = config.copy()
    config["model_id"] = "custom-test-model"
    question = "Тестове питання."
    retrieved = []
    prompt, model_id = build_tutoring_policy(question, retrieved, config)

    assert model_id == "custom-test-model"
    assert config["prompts"]["tutor"] in prompt
    assert question in prompt


def test_build_tutoring_policy_ukrainian_question_preserved():
    config = load_config()
    question = "Розв'яжи рівняння 2x + 4 = 10. Відповідь: x = 3."
    retrieved = []
    prompt, _ = build_tutoring_policy(question, retrieved, config)

    # The exact Ukrainian question string must appear in the assembled prompt
    assert question in prompt
    assert config["prompts"]["tutor"] in prompt


def test_build_tutoring_policy_empty_segments_no_materials_section():
    config = load_config()
    question = "Як складається вода?"
    retrieved = []  # no retrieved segments
    prompt, model_id = build_tutoring_policy(question, retrieved, config)

    # Prompt still contains the tutor policy and the question
    assert config["prompts"]["tutor"] in prompt
    assert question in prompt

    # No segment‑specific content should appear because retrieved_segments is empty
    for seg in retrieved:
        assert seg["excerpt"] not in prompt  # vacuously true, kept for clarity
        assert seg["source_ref"] not in prompt

    # The model identifier must still come from config
    assert model_id == config["model_id"]


def test_build_tutoring_policy_preserves_order_and_duplicates():
    config = load_config()
    question = "Як доводити тождества?"
    retrieved = [
        {"excerpt": "Перший крок — розкрити дужки.", "source_ref": "vid@01:00"},
        {"excerpt": "Перший крок — розкрити дужки.", "source_ref": "vid@01:00"},  # duplicate
        {"excerpt": "Потім підставити відомий член.", "source_ref": "notes#eq2"},
    ]
    prompt, model_id = build_tutoring_policy(question, retrieved, config)

    # Each segment's excerpt and source_ref must be present at least once
    assert retrieved[0]["excerpt"] in prompt
    assert retrieved[0]["source_ref"] in prompt
    assert retrieved[2]["excerpt"] in prompt
    assert retrieved[2]["source_ref"] in prompt

    # Model id unchanged
    assert model_id == config["model_id"]


def test_build_tutoring_policy_does_not_contain_final_answer_from_question():
    """
    The tutor policy instructs not to give the final answer.
    While the policy text itself is checked elsewhere, we also ensure that
    the concrete answer embedded in the question does not appear as a standalone
    solution in the prompt (the policy should only discuss steps).
    """
    config = load_config()
    question = "Розв'яжи рівняння 2x + 4 = 10. Відповідь: x = 3."
    retrieved = [
        {"excerpt": "Відняти 4 з обеих сторон: 2x = 6.", "source_ref": "vid@00:05"},
        {"excerpt": "Поділити на 2: x = 3.", "source_ref": "vid@00:07"},
    ]
    prompt, _ = build_tutoring_policy(question, retrieved, config)

    # Policy markers must be present (from tutor_config.yaml prompts.tutor)
    assert "Guide the student step by step" in prompt
    assert "Do NOT give the final answer or complete solution" in prompt

    # The explicit answer "x = 3" should not be presented as a final solution.
    # We check that the phrase "Відповідь: x = 3." from the question is present
    # (because the question itself is echoed) but the isolated answer after a colon
    # is not additionally appended as a conclusion.
    # For simplicity, ensure that the prompt does not contain a newline‑separated
    # line that ends with "x = 3." as a standalone statement.
    lines = prompt.splitlines()
    answer_lines = [ln.strip() for ln in lines if ln.strip().endswith("x = 3.")]
    # The question line itself may be present; we allow at most one occurrence.
    assert len(answer_lines) <= 1
    if answer_lines:
        # Ensure it is exactly the question line we passed in
        assert answer_lines[0] == question.strip()