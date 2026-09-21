"""
Production settings for AI Tutor Service (docker-compose, FR-002-10).

Modeled on ai_tutor_test_settings.py (proven wiring): same INSTALLED_APPS,
same BearerAuthMiddleware order, same ROOT_URLCONF. Differences vs test:
persistent SQLite at /data, secrets/URLs from environment, quiet logging.
Secrets NEVER live here — only os.environ (see .env.example).
"""

import os

SECRET_KEY = os.environ["SECRET_KEY"]
DEBUG = False

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {},
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": "/data/db.sqlite3",
    }
}

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "ai_tutor_service",
    "ai_tutor_service.materials",
    "ai_tutor_service.conversations",
    "ai_tutor_service.guard",
    "ai_tutor_service.limits",
    "ai_tutor_service.providers",
]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}

USE_TZ = True
TIME_ZONE = "UTC"
LANGUAGE_CODE = "en-us"
USE_I18N = True

ALLOWED_HOSTS = ["*"]

# Middleware - Bearer auth middleware MUST run before CommonMiddleware
MIDDLEWARE = [
    "ai_tutor_service.api.auth.BearerAuthMiddleware",
    "django.middleware.common.CommonMiddleware",
]

# URL configuration for API endpoints
ROOT_URLCONF = "ai_tutor_service.api.urls"

# Secrets from environment (Tutor secrets -> Django settings pattern)
AI_TUTOR_LLM_API_KEY = os.environ.get("AI_TUTOR_LLM_API_KEY", "")
AI_TUTOR_SHARED_SECRET = os.environ.get("AI_TUTOR_SHARED_SECRET", "")
