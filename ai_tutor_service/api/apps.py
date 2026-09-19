# impl: FR-002-02
"""
Django app configuration for the AI Tutor Service API.
"""

from django.apps import AppConfig


class ApiConfig(AppConfig):
    """Configuration for the ai_tutor_service.api Django app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "ai_tutor_service.api"
    verbose_name = "AI Tutor API"