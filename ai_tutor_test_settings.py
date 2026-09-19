"""
Unified test settings for open-edx AI Tutor project.

This module provides a single Django settings configuration for all test suites
(ai_tutor_service, ai_tutor_xblock, video_xblock, architecture tests) to ensure
consistent test execution regardless of test collection order.

Neutral test infrastructure — not package code.
"""
# impl: FR-002-01

# Minimal required settings - avoid global_settings to prevent conflicts
SECRET_KEY = "test-secret-key-not-for-production"
DEBUG = True

# Required for Django templates
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {},
    },
]

# Database - use in-memory SQLite for tests
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Installed apps - all django contribs + all ai_tutor_service sub-apps + ai_tutor_xblock
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "ai_tutor_service",
    "ai_tutor_service.materials",
    "ai_tutor_service.conversations",
    "ai_tutor_service.guard",
    "ai_tutor_service.limits",
    "ai_tutor_service.providers",
    "ai_tutor_xblock",
]

# Use default auto field
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Storage configuration (required in Django 4.2+)
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

# Logging - quiet during tests
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "null": {
            "class": "logging.NullHandler",
        },
    },
    "root": {
        "handlers": ["null"],
    },
    "loggers": {
        "django": {
            "handlers": ["null"],
            "propagate": False,
        },
    },
}

# Time zone
USE_TZ = True
TIME_ZONE = "UTC"

# Internationalization
LANGUAGE_CODE = "en-us"
USE_I18N = True

# Allowed hosts for test client
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

# Middleware - Bearer auth middleware MUST run before CommonMiddleware
MIDDLEWARE = [
    "ai_tutor_service.api.auth.BearerAuthMiddleware",
    "django.middleware.common.CommonMiddleware",
]

# URL configuration for API endpoints
ROOT_URLCONF = "ai_tutor_service.api.urls"

# Shared secret for Bearer authentication (test value)
AI_TUTOR_SHARED_SECRET = "test-shared-secret-12345"