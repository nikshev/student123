# verifies: FR-002-01

"""Architecture test for AI Tutor feature layout.

Вимагає наявності каркасів фічі 002. Червоний на T-001 з очікуваної
причини (пакети й документ відсутні); зелений після T-002, яка створює
пакети `ai_tutor_service/`, `ai_tutor_xblock/` і
`specs/002-ai-tutor/upstream-verification.md` (research R16).
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent

SERVICE_DIR = ROOT / "ai_tutor_service"
XBLOCK_DIR = ROOT / "ai_tutor_xblock"
TUTOR_PLUGIN_YML = ROOT / "tutor-plugin" / "plugin.yml"
UPSTREAM_VERIFICATION = ROOT / "specs" / "002-ai-tutor" / "upstream-verification.md"

REQUIRED_SUBSTRINGS = [
    "reference only",
    "endpoint",
    "response shape",
]


class TestAITutorLayout:
    """Layout-existence tests for the AI Tutor feature."""

    def test_ai_tutor_service_package_exists(self):
        """ai_tutor_service/ пакет мусить існувати з packaging metadata."""
        assert (SERVICE_DIR / "__init__.py").is_file(), (
            "ai_tutor_service/__init__.py відсутній; пакет створить T-002"
        )
        assert (SERVICE_DIR / "pyproject.toml").is_file(), (
            "ai_tutor_service/pyproject.toml відсутній; пакет створить T-002"
        )

    def test_ai_tutor_xblock_package_exists(self):
        """ai_tutor_xblock/ пакет мусить існувати з packaging metadata."""
        assert (XBLOCK_DIR / "__init__.py").is_file(), (
            "ai_tutor_xblock/__init__.py відсутній; пакет створить T-002"
        )
        assert (XBLOCK_DIR / "pyproject.toml").is_file(), (
            "ai_tutor_xblock/pyproject.toml відсутній; пакет створить T-002"
        )

    def test_tutor_plugin_yml_exists(self):
        """tutor-plugin/plugin.yml уже існує з фічі 001."""
        assert TUTOR_PLUGIN_YML.is_file(), "tutor-plugin/plugin.yml відсутній"

    def test_upstream_verification_md_exists(self):
        """Документ верифікації upstream learning-assistant (research R16)."""
        assert UPSTREAM_VERIFICATION.is_file(), (
            "specs/002-ai-tutor/upstream-verification.md відсутній; "
            "створить T-002"
        )

    def test_upstream_verification_md_content(self):
        """Документ містить дату, endpoint/response shapes і рішення reference only."""
        if not UPSTREAM_VERIFICATION.is_file():
            pytest.fail(
                "specs/002-ai-tutor/upstream-verification.md відсутній; "
                "створить T-002"
            )
        content = UPSTREAM_VERIFICATION.read_text(encoding="utf-8")
        for marker in REQUIRED_SUBSTRINGS:
            assert marker in content, (
                f"в upstream-verification.md бракує маркера: {marker!r}"
            )
