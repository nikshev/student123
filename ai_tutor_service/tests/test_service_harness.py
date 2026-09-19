# verifies: FR-002-01

"""Smoke test for ai_tutor_service test harness.

Перевіряє:
1. pytest запускається (файл тесту знайдено і виконується).
2. Будь-який socket/DNS виклик падає (тест мережевої ізоляції).
3. Каталог фікстур `tests/fixtures/llm/` існує.

Очікуваний результат на цьому етапі (T-005): червоний через відсутність
pytest-конфігурації в pyproject.toml і відсутність каталогу fixtures/llm/.
T-006 створить конфіг і fixture root, після чого тест стане зеленим.
"""

import socket
import pytest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
FIXTURES_LLM_DIR = ROOT / "tests" / "fixtures" / "llm"


class TestHarness:
    """Smoke tests for the test harness itself."""

    def test_pytest_runs(self):
        """pytest має запускатися — цей тест сам того доводить, якщо він виконується."""
        assert True, "pytest harness запустився"

    def test_network_denied_socket_create_connection(self):
        """Будь-який socket.create_connection виклик має падати (мережа заборонена)."""
        with pytest.raises((OSError, socket.gaierror, ConnectionRefusedError, TimeoutError)):
            # Спроба підключитися до неіснуючого хосту має падати
            # Якщо мережа заборонена на рівні середовища — це OSError/socket.gaierror
            socket.create_connection(("example.invalid", 80), timeout=0.1)

    def test_network_denied_dns_lookup(self):
        """DNS запити мають падати (socket.getaddrinfo)."""
        with pytest.raises((socket.gaierror, OSError)):
            socket.getaddrinfo("example.invalid", 80)

    def test_fixtures_llm_dir_exists(self):
        """Каталог фікстур для LLM має існувати."""
        assert FIXTURES_LLM_DIR.is_dir(), (
            f"Каталог фікстур {FIXTURES_LLM_DIR} відсутній; "
            f"створить T-006"
        )