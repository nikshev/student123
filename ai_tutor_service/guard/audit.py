# impl: FR-002-07
"""
Atomic block persistence for AI Tutor Service.

Creates BlockRecord atomically with terminal blocked message
before API response (FR-002-07, SC-004).
"""

from typing import Any

from ai_tutor_service.conversations.models import Conversation, Message
from ai_tutor_service.guard.models import BlockRecord


def record_block(
    *,
    message: Message,
    conversation: Conversation,
    user_id: str,
    course_id: str,
    unit_usage_key: str,
    question: str,
    reason: str,
    guard_model_id: str,
    config_version: str,
) -> BlockRecord:
    """
    Create a BlockRecord atomically with the blocked tutor message.

    All parameters are required. The function does NOT log secrets,
    candidate answers, or any sensitive data. It only persists
    the audit fields required by FR-002-07.

    Args:
        message: The blocked tutor Message just created.
        conversation: The Conversation this message belongs to.
        user_id: Student user ID.
        course_id: Course identifier.
        unit_usage_key: Unit usage key.
        question: The student's original question (not the candidate answer).
        reason: The guard's block reason.
        guard_model_id: The guard model ID from config.
        config_version: The tutor config version.

    Returns:
        The created BlockRecord instance.

    Raises:
        OperationalError: If DB write fails (propagates to caller for rollback).
        ValidationError: If required fields are missing/invalid.
    """
    return BlockRecord.objects.create(
        message_id=message,
        conversation_id=conversation,
        user_id=user_id,
        course_id=course_id,
        unit_usage_key=unit_usage_key,
        question=question,
        reason=reason,
        guard_model_id=guard_model_id,
        config_version=config_version,
    )