# verifies: FR-002-10

"""Architecture test for AI Tutor Tutor plugin deployment declarations.

Вимагає наявності deployment-декларацій фічі 002 у tutor-plugin/. Червоний
на T-003 з очікуваної причини (plugin.yml і patches/ 001-шні, без декларацій
для ai_tutor_service / ai_tutor_xblock); зелений після T-004, яка розширює
tutor-plugin/ під фічю 002.
"""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
TUTOR_PLUGIN_DIR = ROOT / "tutor-plugin"
PLUGIN_YML = TUTOR_PLUGIN_DIR / "plugin.yml"
PATCHES_DIR = TUTOR_PLUGIN_DIR / "patches"

# Expected secret names that must NOT appear as literal values in YAML/patches
FORBIDDEN_SECRET_NAMES = [
    "AI_TUTOR_LLM_API_KEY",
    "AI_TUTOR_SHARED_SECRET",
]

# Expected patch files for feature 002
EXPECTED_PATCH_FILES = [
    "openedx-lms-common-settings",
    "openedx-lms-production-settings",
    "openedx-cms-production-settings",
    "openedx-dockerfile-post-python-requirements",
    # Feature 002 additions:
    "openedx-ai-tutor-service-compose",      # separate Django process
    "openedx-ai-tutor-service-settings",     # service URL, secrets via settings
]


class TestAITutorTutorPlugin:
    """Deployment declaration tests for AI Tutor feature 002."""

    def test_plugin_yml_exists(self):
        """tutor-plugin/plugin.yml уже існує з фічі 001."""
        assert PLUGIN_YML.is_file(), "tutor-plugin/plugin.yml відсутній"

    def test_plugin_yml_installs_both_packages(self):
        """plugin.yml встановлює обидва пакети ai_tutor_service та ai_tutor_xblock у образ edx-platform."""
        if not PLUGIN_YML.is_file():
            pytest.fail("tutor-plugin/plugin.yml відсутній")
        content = PLUGIN_YML.read_text(encoding="utf-8")
        data = yaml.safe_load(content)

        # Check for requirements/patches that install both packages
        # The plugin should have dockerfile patches that COPY and RUN pip install for both packages
        # We check the patches directory content via the plugin structure
        dockerfile_patch = PATCHES_DIR / "openedx-dockerfile-post-python-requirements"
        if not dockerfile_patch.is_file():
            pytest.fail(
                "patch openedx-dockerfile-post-python-requirements відсутній; "
                "потрібен для встановлення ai_tutor_service та ai_tutor_xblock"
            )
        patch_content = dockerfile_patch.read_text(encoding="utf-8")
        assert "ai_tutor_service" in patch_content, (
            "dockerfile patch не встановлює ai_tutor_service"
        )
        assert "ai_tutor_xblock" in patch_content, (
            "dockerfile patch не встановлює ai_tutor_xblock"
        )

    def test_service_runs_as_separate_django_process(self):
        """Сервіс запускається як ОКРЕМИЙ Django-процес (окремий compose-сервіс/процес, не воркер LMS)."""
        compose_patch = PATCHES_DIR / "openedx-ai-tutor-service-compose"
        if not compose_patch.is_file():
            pytest.fail(
                "patch openedx-ai-tutor-service-compose відсутній; "
                "потрібен для оголошення окремого compose-сервісу ai-tutor-service"
            )
        content = compose_patch.read_text(encoding="utf-8")
        # Should define a separate service, not just add to lms/worker
        assert "ai-tutor-service" in content or "ai_tutor_service" in content, (
            "compose patch не оголошує окремий сервіс ai-tutor-service"
        )
        # Should not be just a worker/lms extension
        assert "services:" in content, "compose patch має оголошувати services секцію"

    def test_persistent_sqlite_volume_mounted(self):
        """Для сервісу монтується постійний SQLite-том (persistent volume)."""
        compose_patch = PATCHES_DIR / "openedx-ai-tutor-service-compose"
        if not compose_patch.is_file():
            pytest.fail("patch openedx-ai-tutor-service-compose відсутній")
        content = compose_patch.read_text(encoding="utf-8")
        # Check for volume mount for SQLite persistence
        assert "volume" in content.lower() or "volumes:" in content, (
            "compose patch не монтує persistent volume для SQLite"
        )
        # Volume should be named/path that persists
        assert any(
            keyword in content for keyword in ["ai-tutor-data", "sqlite", "persistent"]
        ), "volume має бути назване/шляхове для SQLite persistence"

    def test_internal_service_url_defined(self):
        """Задається внутрішній service URL (що LMS/XBlock використовуватиме як endpoint)."""
        settings_patch = PATCHES_DIR / "openedx-ai-tutor-service-settings"
        if not settings_patch.is_file():
            pytest.fail(
                "patch openedx-ai-tutor-service-settings відсутній; "
                "потрібен для AI_TUTOR_SERVICE_URL в Django settings"
            )
        content = settings_patch.read_text(encoding="utf-8")
        assert "AI_TUTOR_SERVICE_URL" in content, (
            "settings patch не задає AI_TUTOR_SERVICE_URL"
        )
        # Should be internal Docker network URL, not localhost
        assert "http://" in content, "AI_TUTOR_SERVICE_URL має бути HTTP URL"
        assert "localhost" not in content and "127.0.0.1" not in content, (
            "AI_TUTOR_SERVICE_URL не повинен бути localhost (внутрішня мережа Docker)"
        )

    def test_secrets_only_via_tutor_secrets_no_literals(self):
        """AI_TUTOR_LLM_API_KEY і AI_TUTOR_SHARED_SECRET передаються ЛИШЕ через Tutor secrets → Django settings.
        
        Перевіряє відсутність літеральних ключів/паролів у YAML/патчах, не лише наявність імен.
        """
        # 1. Check plugin.yml config defaults - should NOT have actual secret values
        if PLUGIN_YML.is_file():
            plugin_content = PLUGIN_YML.read_text(encoding="utf-8")
            plugin_data = yaml.safe_load(plugin_content)
            config_defaults = plugin_data.get("config", {}).get("defaults", {})
            for secret_name in FORBIDDEN_SECRET_NAMES:
                # Secret names should not be in config defaults at all, or if present, value must be empty/template
                if secret_name in config_defaults:
                    value = config_defaults[secret_name]
                    assert value == "" or value == "{{ " + secret_name + " }}", (
                        f"plugin.yml config.defaults.{secret_name} містить літеральне значення: {value!r}; "
                        f"секрети мають задаватися лише через `tutor config save`"
                    )

        # 2. Check all patch files for literal secret values
        for patch_file in PATCHES_DIR.iterdir():
            if not patch_file.is_file():
                continue
            patch_content = patch_file.read_text(encoding="utf-8")
            for secret_name in FORBIDDEN_SECRET_NAMES:
                # Check for literal assignments like SECRET = "actual-value" or SECRET = actual-value
                # Allow template placeholders like "{{ SECRET_NAME }}" or empty string
                lines = patch_content.splitlines()
                for i, line in enumerate(lines):
                    stripped = line.strip()
                    # Skip comments
                    if stripped.startswith("#"):
                        continue
                    # Check if line contains the secret name with an assignment
                    if secret_name in stripped and "=" in stripped:
                        # Allow template placeholder
                        if "{{" in stripped and "}}" in stripped:
                            continue
                        # Allow empty string assignment
                        if stripped.endswith('= ""') or stripped.endswith("= ''"):
                            continue
                        # Fail on any other assignment that looks like a literal value
                        pytest.fail(
                            f"{patch_file.name}:{i+1} містить літеральне значення секрету {secret_name}: {stripped!r}; "
                            f"секрети мають передаватися лише через Tutor secrets → Django settings (template {{ {{ {secret_name} }} }})"
                        )

    def test_patches_directory_has_feature_002_patches(self):
        """Перевіряє наявність всіх очікуваних patch-файлів для фічі 002."""
        missing = []
        for expected in EXPECTED_PATCH_FILES:
            if not (PATCHES_DIR / expected).is_file():
                missing.append(expected)
        if missing:
            pytest.fail(
                "Відсутні patch-файли для фічі 002: " + ", ".join(missing)
            )

    def test_no_secret_values_in_any_yaml_or_patch(self):
        """Додаткова перевірка: жоден файл у tutor-plugin/ не містить літеральних секретів."""
        secret_patterns = [
            r"sk-[a-zA-Z0-9]{20,}",  # OpenAI-style API keys
            r"Bearer\s+[a-zA-Z0-9\-_]{20,}",  # Bearer tokens
            r"secret[:\s=]\s*[\"'][^\"']{10,}[\"']",  # secret: "value"
            r"password[:\s=]\s*[\"'][^\"']{5,}[\"']",  # password: "value"
        ]
        import re

        for file_path in TUTOR_PLUGIN_DIR.rglob("*"):
            if not file_path.is_file():
                continue
            if file_path.suffix in {".pyc", ".pyo"}:
                continue
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            for pattern in secret_patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                if matches:
                    # Filter out template placeholders
                    filtered = [m for m in matches if "{{" not in m and "}}" not in m]
                    if filtered:
                        pytest.fail(
                            f"{file_path.relative_to(ROOT)} містить підозрілий патерн секрету: {pattern} -> {filtered[:3]}"
                        )

    def test_service_compose_defines_environment_from_secrets(self):
        """Compose сервісу має environment змінні, що беруться з Tutor secrets."""
        compose_patch = PATCHES_DIR / "openedx-ai-tutor-service-compose"
        if not compose_patch.is_file():
            pytest.fail("patch openedx-ai-tutor-service-compose відсутній")
        content = compose_patch.read_text(encoding="utf-8")
        # Should reference the secret names as environment variables
        for secret_name in FORBIDDEN_SECRET_NAMES:
            assert secret_name in content, (
                f"compose patch не передає {secret_name} через environment"
            )
            # Should be in environment section, typically like:
            # environment:
            #   AI_TUTOR_LLM_API_KEY: "{{ AI_TUTOR_LLM_API_KEY }}"
            assert "{{" in content and "}}" in content, (
                f"compose patch має використовувати template {{ {{ {secret_name} }} }} для secrets"
            )