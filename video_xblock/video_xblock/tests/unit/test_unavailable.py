# verifies: FR-001-10
"""
Student view must handle the unavailable video state (FR-001-10).

T-030 is test-first for T-031. The current ``player_data_setup`` signs an
embed URL regardless of ``bunny_status`` and ``get_player_html`` always renders
the iframe, so these tests are intentionally red until T-031 adds the
unavailable handling. Each red is caused by the missing handling — not by
collection, fixtures, or the offline harness.

Covered paths (contracts/tracking-events.md §3: a playback error emits no
event and the UI shows a message):

1. ``player_data_setup`` must not issue ``signed_embed_url`` for a block in
   ``ERROR`` state.
2. The rendered student player must contain a readable «Відео недоступне»
   message and no player markup (no iframe, no ``bunny_player.js`` bridge that
   could emit view events).
3. The 404 chain: a Bunny 404 through the public ``video_info`` handler marks
   the block ``ERROR`` (already implemented, bunny.py video_info) and the
   student view must then render the unavailable message.
"""

import json
import unittest
from unittest.mock import Mock, PropertyMock, patch

from django.test import override_settings
from lxml import etree
from xblock.field_data import DictFieldData
from xblock.test.tools import TestRuntime

from video_xblock.backends import bunny
from video_xblock.tests.fixtures.bunny.create_video import CREATE_VIDEO_200
from video_xblock.tests.fixtures.bunny.video_info import VIDEO_INFO_404
from video_xblock.tests.unit.base import arrange_request_mock
from video_xblock.video_xblock import VideoXBlock


LIBRARY_ID = CREATE_VIDEO_200["body"]["videoLibraryId"]
VIDEO_ID = CREATE_VIDEO_200["body"]["guid"]
API_KEY_SENTINEL = "test-only-bunny-api-key-must-stay-server-side"
TOKEN_KEY_SENTINEL = "test-only-bunny-token-key-must-stay-server-side"
NOW = 1750000000

# Sentinel config returned by the mocked ``load_bunny_config``, mirroring
# test_student_view.py: behavioural values are non-canonical so a hard-coded
# literal cannot satisfy the assertions.
SENTINEL_CONFIG = {
    "version": "9.8.7",
    "token_ttl_seconds": 12345,
    "completion_threshold": 0.73,
    "max_upload_bytes": 3141592,
    "max_duration_seconds": 90,
    "allowed_extensions": ["mp4", "mov", "webm"],
    "upload_auth_ttl_seconds": 7200,
    "video_info_poll_interval_seconds": 3,
    "api_base_url": "https://api.sentinel.invalid",
    "tus_endpoint": "https://tus.sentinel.invalid",
    "embed_base_url": "https://player.sentinel.invalid",
    "changelog": [],
}


class BunnyStudentViewUnavailableTests(unittest.TestCase):
    """ERROR/404 states render a message instead of the player (FR-001-10)."""

    def setUp(self):
        # Network is forbidden: any live Bunny/HTTP call fails the test.
        for method in ("request", "send"):
            self.enterContext(patch(
                "requests.sessions.Session." + method,
                autospec=True,
                side_effect=AssertionError("Network access is forbidden in unit tests"),
            ))
        self.enterContext(override_settings(
            BUNNY_STREAM_LIBRARY_ID=LIBRARY_ID,
            BUNNY_STREAM_API_KEY=API_KEY_SENTINEL,
            BUNNY_STREAM_TOKEN_KEY=TOKEN_KEY_SENTINEL,
        ))
        self.enterContext(patch.object(
            bunny, "load_bunny_config", return_value=SENTINEL_CONFIG,
        ))
        self.enterContext(patch.object(
            bunny.BunnyPlayer, "_is_enrolled", create=True, return_value=True,
        ))
        # Resolve player_name='bunny' through the real plugin registry so the
        # public render_player handler exercises the actual BunnyPlayer.
        self.enterContext(patch.dict(
            "xblock.plugin.PLUGIN_CACHE",
            {("video_xblock.v1", "bunny"): bunny.BunnyPlayer},
        ))
        self.config = SENTINEL_CONFIG

        self.xblock = VideoXBlock(
            TestRuntime(),
            DictFieldData({
                "account_id": "",
                "metadata": {},
                "player_name": "bunny",
                "href": "",
            }),
            scope_ids=Mock(spec=[]),
        )
        self._seed_error_metadata()
        self.player = bunny.BunnyPlayer(self.xblock)

        # Isolate the render_player inputs unrelated to the unavailable-state
        # HTML under test (same isolation as test_student_view.py).
        self.xblock.runtime.handler_url = Mock(return_value="/handler/save_player_state")
        self.enterContext(patch.object(
            self.xblock, "route_transcripts", return_value=[],
        ))
        self.enterContext(patch.object(
            VideoXBlock, "player_state", new_callable=PropertyMock,
            return_value={"transcripts": [], "currentTime": ""},
        ))

    def _seed_error_metadata(self):
        """Plant a truthful ERROR state: the video is gone or broken."""
        self.xblock.metadata.update({
            "bunny_video_id": VIDEO_ID,
            "bunny_library_id": LIBRARY_ID,
            "bunny_status": "ERROR",
            "bunny_length_seconds": None,
            "bunny_title": CREATE_VIDEO_200["body"]["title"],
            "source_type": "bunny",
            "token_protected": True,
            "config_version": self.config["version"],
        })

    def _render_student_player(self):
        """Render the public render_player handler with a frozen clock."""
        with patch.object(bunny.time, "time", return_value=NOW):
            response = self.xblock.render_player(Mock(), "")
        return response.body.decode()

    def _assert_unavailable_page(self, body):
        """The page must show the message and carry no player that could play."""
        tree = etree.fromstring(
            body.encode("utf-8"), etree.HTMLParser(encoding="utf-8"),
        )
        self.assertEqual(tree.xpath('.//iframe'), [],
                         "Unavailable video must not render an embed iframe")
        text = etree.tostring(tree, encoding="unicode", method="text")
        self.assertIn("Відео недоступне", text,
                      "Student view must display the unavailable message")
        self.assertNotIn("bunny_player.js", body,
                         "The player bridge must not load for an unavailable video")
        self.assertNotIn("bunny_unavailable_fallback.js", body,
                         "The unavailable view must not load the embed-error fallback")

    def test_player_data_setup_omits_signed_url_for_error_state(self):
        """ERROR state: no signed embed URL is issued to the browser."""
        context = self.player.player_data_setup({})
        self.assertNotIn("signed_embed_url", context,
                         "ERROR state must not receive a signed embed URL")

    def test_error_state_renders_unavailable_message_instead_of_player(self):
        """ERROR state renders the message, not an empty or live player."""
        body = self._render_student_player()
        self._assert_unavailable_page(body)

    def test_bunny_404_marks_error_and_student_view_shows_message(self):
        """
        A 404 from Bunny (video deleted there) flows to the student view.

        ``video_info`` already maps a 404 to the ERROR state (bunny-api.md §3);
        the student render must then refuse to embed a player and show the
        unavailable message instead.
        """
        client = Mock(name="offline_bunny_client")
        client.get_video_info.side_effect = bunny.BunnyApiClientError(
            VIDEO_INFO_404["body"]["message"],
            status_code=VIDEO_INFO_404["status_code"],
        )
        self.enterContext(patch.object(
            bunny.BunnyApiClient, "from_settings", return_value=client,
        ))
        self.xblock.runtime.is_author_mode = True

        request = arrange_request_mock(json.dumps({"video_id": VIDEO_ID}))
        response = self.player.video_info(request)

        self.assertEqual(response.status_int, 200)
        self.assertEqual(self.xblock.metadata["bunny_status"], "ERROR")
        self.assertIsNone(self.xblock.metadata.get("bunny_length_seconds"))

        body = self._render_student_player()
        self._assert_unavailable_page(body)
