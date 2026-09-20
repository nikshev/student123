# verifies: FR-002-01
"""
Student view (student_view) render contract tests (FR-002-01, T-027).

Contract for T-028 (contracts/xblock-interface.md §4):

- student_view runs EnrollmentGuard first: Studio/Workbench/anonymous/
  not-enrolled/missing course -> denied fragment WITHOUT chat markup, handler
  URLs, config or materials status; the service client is not touched.
- Enrolled LMS render:
  * renders ai_tutor_xblock/ai_tutor_xblock/templates/student.html
    (the file must exist; the fragment must not be the old placeholder);
  * rendered HTML contains data-state="loading" (initial UI state),
    data-config-version="<config_version>" from the public config projection,
    data-materials-status="<READY|INDEXING|FAILED|MISSING>" from
    client.materials_status(course_id, unit_usage_key),
    the ask/history handler URLs from runtime.handler_url(block, name);
  * HTML does NOT contain: the runtime user id, the service URL/secret,
    prompts, model IDs, conversation history text;
  * source refs are rendered as plain text (video@/notes# refs allowed as
    text, no script/iframe markup).

The tests are red right now for the expected reason: templates/student.html
does not exist and student_view still returns the T-024 skeleton placeholder,
which satisfies none of the assertions below.
"""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from ai_tutor_xblock.ai_tutor_xblock.block import AiTutorXBlock
from ai_tutor_xblock.ai_tutor_xblock.client import (
    ConfigResult,
    MaterialsStatusResult,
)

USER_ID = "user-42"
COURSE_ID = "course-v1:demo+math+2026"
UNIT_USAGE_KEY = "block-v1:demo+math+2026+type@vertical+block@u1"
CONFIG_VERSION = "1.0.0"

REPO_ROOT = Path(__file__).resolve().parents[2]
STUDENT_TEMPLATE = (
    REPO_ROOT / "ai_tutor_xblock" / "templates" / "student.html"
)

FORBIDDEN_TERMS = [
    USER_ID,
    "test-shared-secret-12345",
    "anthropic",
    "claude",
    "model_id",
    "model-id",
]


class StubUser:
    def __init__(self, user_id=USER_ID):
        self.id = user_id


class LMSRuntime:
    def __init__(self, user=StubUser(), course_id=COURSE_ID):
        self.user = user
        self.course_id = course_id
        self.is_author_mode = False

    def _is_enrolled(self, user, course_id):
        return True

    def handler_url(self, block, name, suffix=""):
        return f"/handler/{name}"


class StudioRuntime(LMSRuntime):
    def __init__(self):
        super().__init__()
        self.is_author_mode = True


class AnonymousRuntime(LMSRuntime):
    def __init__(self):
        super().__init__(user=None)


class NotEnrolledRuntime(LMSRuntime):
    def _is_enrolled(self, user, course_id):
        return False


class FakeClient:
    def __init__(self, materials_status="READY"):
        self.config_calls = 0
        self.materials_calls = 0
        self._materials_status = materials_status

    def config(self):
        self.config_calls += 1
        return ConfigResult(
            config_version=CONFIG_VERSION,
            daily_limit=10,
            request_timeout_seconds=30,
            http_connect_timeout_seconds=2,
            question_max_chars=2000,
        )

    def materials_status(self, course_id, unit_usage_key):
        self.materials_calls += 1
        return MaterialsStatusResult(
            status=self._materials_status,
            content_version="2026-09-20.1",
            segment_count=5,
            config_version=CONFIG_VERSION,
        )


@pytest.fixture
def env(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(
        "ai_tutor_xblock.ai_tutor_xblock.block.TutorServiceClient",
        lambda *a, **k: fake,
    )
    return fake


def make_block(runtime=None):
    scope_ids = Mock()
    scope_ids.usage_id = UNIT_USAGE_KEY
    return AiTutorXBlock(runtime=runtime or LMSRuntime(), scope_ids=scope_ids)


class TestStudentViewRendersContract:
    def test_template_student_html_exists(self, env):
        assert STUDENT_TEMPLATE.is_file(), (
            "templates/student.html відсутній — T-028 має створити шаблон"
        )

    def test_student_view_renders_full_context(self, env):
        block = make_block()
        fragment = block.student_view()
        html = fragment.content
        assert isinstance(html, str) and html
        assert "skeleton" not in html.lower(), "student_view ще плейсхолдер T-024"
        assert 'data-state="loading"' in html, "initial UI state має бути LOADING"
        assert f'data-config-version="{CONFIG_VERSION}"' in html
        assert 'data-materials-status="READY"' in html
        assert "/handler/ask" in html, "handler URL ask відсутній"
        assert "/handler/history" in html, "handler URL history відсутній"

    @pytest.mark.parametrize("status", ["INDEXING", "FAILED", "MISSING"])
    def test_materials_status_reflected(self, env, monkeypatch, status):
        fake = FakeClient(materials_status=status)
        monkeypatch.setattr(
            "ai_tutor_xblock.ai_tutor_xblock.block.TutorServiceClient",
            lambda *a, **k: fake,
        )
        block = make_block()
        html = block.student_view().content
        assert f'data-materials-status="{status}"' in html

    def test_html_has_no_forbidden_data(self, env):
        block = make_block()
        html = block.student_view().content.lower()
        for term in FORBIDDEN_TERMS:
            assert term.lower() not in html, (
                f"заборонений рядок {term!r} витікає у student HTML"
            )
        assert "iframe" not in html
        assert "<script" not in html

    def test_denied_studio_renders_no_chat(self):
        block = make_block(runtime=StudioRuntime())
        fragment = block.student_view()
        html = (fragment.content or "").lower()
        assert "skeleton" not in html, "denied не має рендерити плейсхолдер"
        assert "data-state" not in html, "denied не має рендерити чат"
        assert "/handler/ask" not in html
        for term in FORBIDDEN_TERMS:
            assert term.lower() not in html

    def test_denied_anonymous_renders_no_chat(self):
        block = make_block(runtime=AnonymousRuntime())
        html = (block.student_view().content or "").lower()
        assert "skeleton" not in html
        assert "data-state" not in html
        assert "/handler/ask" not in html
        for term in FORBIDDEN_TERMS:
            assert term.lower() not in html

    def test_denied_not_enrolled_renders_no_chat(self):
        block = make_block(runtime=NotEnrolledRuntime())
        html = (block.student_view().content or "").lower()
        assert "skeleton" not in html
        assert "data-state" not in html
        assert "/handler/ask" not in html

    def test_denied_runtime_does_not_touch_client(self, env):
        block = make_block(runtime=StudioRuntime())
        block.student_view()
        assert env.config_calls == 0
        assert env.materials_calls == 0
