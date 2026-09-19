# impl: FR-002-10
"""
ai_tutor_service.providers package.

Public API:
- LLMClient: Single external LLM boundary for all LLM operations.
- LLMError: Typed error for LLM operations.
"""

from .client import LLMClient, LLMError

__all__ = ["LLMClient", "LLMError"]