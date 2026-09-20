# verifies: FR-002-03
"""
Studio view (studio_view) render contract tests (FR-002-03, T-029).

Contract for T-030 (contracts/xblock-interface.md §5):

- studio_view is author-only: renders only for StudioRuntime
  (is_author_mode=True); for LMSRuntime (is_author_mode=False) it
  must NOT render Studio content and must NOT touch the service client.
- The rendered fragment MUST contain:
  * block display_name (default "AI Tutor");
  * config_version from client.config();
  * materials_status from client.materials_status(course_id, unit_usage_key),
    parameterized over READY/INDEXING/FAILED/MISSING;
  * an authorized-ingest instruction marked by data-ingest-instruction.
- The rendered fragment MUST NOT contain:
  * handler URLs (/handler/ask, /handler/history);
  * upload forms (input[type=file], multipart);
  * student data (user id, conversation history);
  * content text excerpts (transcript/notes);
  * prompts/model IDs ("anthropic", "claude", "model_id");
  * secrets ("test-shared-secret-12345");
  * the word "skeleton".
- No unconditional 'or skeleton' loopholes: every assertion targets
  the specific field that the real implementation must produce.

The tests are red right now for the expected reason:
studio_view in block.py still returns the T-024 skeleton placeholder
Fragment("AI Tutor XBlock - Studio View (skeleton)"), which satisfies
none of the assertions below and contains the forbidden "skeleton" string.
"""

import pytest
from unittest.mock import Mock

from ai_tutor_xblock.ai_tutor_xblock.block import AiTutorXBlock
from ai_tutor_xblock.ai_tutor_xblock.client import (
    ConfigResult,
    MaterialsStatusResult,
)

DISPLAY_NAME = "AI Tutor"
CONFIG_VERSION = "1.0.0"
COURSE_ID = "course-v1:demo+math+2026"
UNIT_USAGE_KEY = "block-v1:demo+math+2026+type@vertical+block@u1"

FORBIDDEN_TERMS = [
    "test-shared-secret-12345",
    "anthropic",
    "claude",
    "model_id",
    "model-id",
    "/handler/ask",
    "/handler/history",
    'input[type=file]',
    "multipart",
    "skeleton",
]


class StubUser:
    def __init__(self, user_id="student-1"):
        self.id = user_id


class LMSRuntime:
    def __init__(self, user=StubUser(), course_id=COURSE_ID):
        self.user = user
        self.course_id = course_id
        self.is_author_mode = False

    def _is_enrolled(self, user, course_id):
        return True


class StudioRuntime(LMSRuntime):
    def __init__(self):
        super().__init__()
        self.is_author_mode = True


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


class TestStudioViewAuthorOnly:
    """Author-only Studio view contract (FR-002-03, T-029/T-030)."""

    def test_studio_view_renders_for_author(self, env):
        """StudioRuntime (is_author_mode=True) must render a non-empty fragment."""
        block = make_block(runtime=StudioRuntime())
        fragment = block.studio_view()
        html = fragment.content or ""
        assert isinstance(html, str) and html, "Studio view must render for author"

    def test_studio_view_not_rendered_for_lms(self, env):
        """LMSRuntime (is_author_mode=False) must NOT render Studio content."""
        block = make_block(runtime=LMSRuntime())
        fragment = block.studio_view()
        html = fragment.content or ""
        assert html == "", "LMS runtime must get empty fragment from studio_view"

    def test_studio_view_does_not_touch_client_for_lms(self, env):
        """LMS runtime must not invoke client from studio_view."""
        block = make_block(runtime=LMSRuntime())
        block.studio_view()
        assert env.config_calls == 0, "studio_view на LMS не має викликати config"
        assert env.materials_calls == 0, "studio_view на LMS не має викликати materials_status"

    def test_studio_view_contains_display_name(self, env):
        """Fragment must contain the block display_name."""
        block = make_block(runtime=StudioRuntime())
        html = block.studio_view().content
        assert DISPLAY_NAME in html, "display_name 'AI Tutor' має бути у studio_view"

    def test_studio_view_contains_config_version(self, env):
        """Fragment must contain config_version from client.config()."""
        block = make_block(runtime=StudioRuntime())
        html = block.studio_view().content
        assert CONFIG_VERSION in html, "config_version має бути у studio_view"

    def test_studio_view_contains_materials_status(self, env):
        """Fragment must contain materials_status from client.materials_status()."""
        block = make_block(runtime=StudioRuntime())
        html = block.studio_view().content
        assert 'data-materials-status="READY"' in html, "materials_status=READY має бути у studio_view"

    @pytest.mark.parametrize("status", ["INDEXING", "FAILED", "MISSING"])
    def test_materials_status_reflected(self, env, monkeypatch, status):
        """Fragment must reflect all materials statuses."""
        fake = FakeClient(materials_status=status)
        monkeypatch.setattr(
            "ai_tutor_xblock.ai_tutor_xblock.block.TutorServiceClient",
            lambda *a, **k: fake,
        )
        block = make_block(runtime=StudioRuntime())
        html = block.studio_view().content
        assert f'data-materials-status="{status}"' in html, (
            f"materials_status={status} має бути у studio_view"
        )

    def test_studio_view_contains_ingest_instruction(self, env):
        """Fragment must contain the authorized-ingest instruction marker."""
        block = make_block(runtime=StudioRuntime())
        html = block.studio_view().content
        assert 'data-ingest-instruction' in html, (
            "data-ingest-instruction має бути у studio_view"
        )

    def test_studio_view_has_no_handler_urls(self, env):
        """Fragment must NOT contain handler URLs."""
        block = make_block(runtime=StudioRuntime())
        html = block.studio_view().content.lower()
        for term in ["/handler/ask", "/handler/history"]:
            assert term not in html, f"заборонений handler URL {term} у studio_view"

    def test_studio_view_has_no_upload_forms(self, env):
        """Fragment must NOT contain upload forms."""
        block = make_block(runtime=StudioRuntime())
        html = block.studio_view().content.lower()
        for term in ['input[type=file]', "multipart"]:
            assert term not in html, f"заборонений upload-елемент {term} у studio_view"

    def test_studio_view_has_no_student_data(self, env):
        """Fragment must NOT contain student data (user id, conversation history)."""
        block = make_block(runtime=StudioRuntime())
        html = block.studio_view().content.lower()
        for term in ["student-1", "conversation", "messages", "question"]:
            assert term not in html, (
                f"student data term {term!r} не має витікати у studio_view"
            )

    def test_studio_view_has_no_forbidden_content(self, env):
        """Fragment must NOT contain forbidden terms: secrets, prompts, model IDs."""
        block = make_block(runtime=StudioRuntime())
        html = block.studio_view().content.lower()
        for term in FORBIDDEN_TERMS:
            assert term.lower() not in html, (
                f"заборонений рядок {term!r} витікає у studio_view"
            )

    def test_studio_view_has_no_skeleton(self, env):
        """Fragment must NOT contain 'skeleton' placeholder."""
        block = make_block(runtime=StudioRuntime())
        html = block.studio_view().content.lower()
        assert "skeleton" not in html, "studio_view ще плейсхолдер T-029"

    def test_studio_view_client_called_for_author(self, env):
        """Author runtime must invoke client from studio_view."""
        block = make_block(runtime=StudioRuntime())
        block.studio_view()
        assert env.config_calls >= 1, "studio_view для автора має викликати config"
        assert env.materials_calls >= 1, "studio_view для автора має викликати materials_status"
