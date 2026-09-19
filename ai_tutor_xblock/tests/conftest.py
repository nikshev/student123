"""
Pytest configuration and fixtures for ai_tutor_xblock.
"""
# impl: FR-002-01

import os
import socket
import sys
from pathlib import Path
import pytest


# Ensure the package root is on sys.path for imports
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# Minimal Django configuration for XBlock tests (no full Django project needed)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings_test_xblock")

# Create minimal Django settings inline
from django.conf import settings

if not settings.configured:
    settings.configure(
        SECRET_KEY="test-secret-key-not-for-production",
        DEBUG=True,
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": True,
                "OPTIONS": {},
            },
        ],
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "ai_tutor_xblock",
        ],
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        LOGGING={
            "version": 1,
            "disable_existing_loggers": False,
            "handlers": {"null": {"class": "logging.NullHandler"}},
            "root": {"handlers": ["null"]},
            "loggers": {"django": {"handlers": ["null"], "propagate": False}},
        },
    )

import django
django.setup()


# === Network deny-by-default ===

_original_create_connection = socket.create_connection
_original_getaddrinfo = socket.getaddrinfo


def _denied_create_connection(*args, **kwargs):
    """Block all socket.create_connection calls."""
    raise OSError("Network access denied: socket.create_connection is disabled in tests")


def _denied_getaddrinfo(*args, **kwargs):
    """Block all DNS lookups."""
    raise socket.gaierror("Network access denied: DNS lookup is disabled in tests")


@pytest.fixture(autouse=True)
def _disable_network(monkeypatch):
    """
    Autouse fixture that disables all network access for all tests.
    This ensures tests cannot make any external network calls.
    """
    monkeypatch.setattr(socket, "create_connection", _denied_create_connection)
    monkeypatch.setattr(socket, "getaddrinfo", _denied_getaddrinfo)
    yield


# === Fixture directories ===

@pytest.fixture(scope="session")
def fixtures_service_dir():
    """Path to service HTTP fixtures directory."""
    return ROOT / "tests" / "fixtures" / "service"


# Make fixture directory available as a module-level constant for tests
FIXTURES_SERVICE_DIR = ROOT / "tests" / "fixtures" / "service"