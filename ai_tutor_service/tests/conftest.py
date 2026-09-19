"""
Pytest configuration and fixtures for ai_tutor_service.
"""
# impl: FR-002-01

import os
import socket
import sys
from pathlib import Path
import pytest


# Ensure the repo root (parent of tests/) is on sys.path for "tests.test_settings" import
# This MUST be done before setting DJANGO_SETTINGS_MODULE
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Ensure the package root is on sys.path for imports
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# Configure Django settings before any Django imports
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.test_settings")

import django  # noqa: E402
django.setup()  # noqa: E402


# === Network deny-by-default ===

# Save original socket functions
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
    # Restore after test (though monkeypatch handles this automatically)


# === Fixture directories ===

@pytest.fixture(scope="session")
def fixtures_llm_dir():
    """Path to LLM fixtures directory."""
    return ROOT / "tests" / "fixtures" / "llm"


# Make fixture directory available as a module-level constant for tests
FIXTURES_LLM_DIR = ROOT / "tests" / "fixtures" / "llm"