# impl: FR-002-02
"""
URL configuration for AI Tutor Service API.

Routes for all /api/v1/* endpoints per contract.
"""

from django.urls import path

from ai_tutor_service.api.ask import ask_view
from ai_tutor_service.api.views import (
    config_view,
    conversation_view,
    gate_run_view,
    materials_status_view,
    materials_view,
)

app_name = "ai_tutor_api"

urlpatterns = [
    # POST /api/v1/ask - Student asks a question
    path("api/v1/ask", ask_view, name="ask"),
    # POST /api/v1/materials - Staff ingests materials
    path("api/v1/materials", materials_view, name="materials"),
    # GET /api/v1/materials/status - Check materials status
    path("api/v1/materials/status", materials_status_view, name="materials_status"),
    # GET /api/v1/conversation/{id} - Get conversation history
    path("api/v1/conversation/<uuid:conversation_id>", conversation_view, name="conversation"),
    # GET /api/v1/config - Get public config
    path("api/v1/config", config_view, name="config"),
    # POST /api/v1/gate/run - Run gate evaluation
    path("api/v1/gate/run", gate_run_view, name="gate_run"),
]