"""
Test settings for ai_tutor_service.
Minimal Django configuration for running tests without external dependencies.
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

# Disable migrations for faster tests
class DisableMigrations:
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None

MIGRATION_MODULES = DisableMigrations()

# Installed apps - minimal set
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "ai_tutor_service",
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