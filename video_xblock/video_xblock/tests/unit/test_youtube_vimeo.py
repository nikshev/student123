# verifies: FR-001-11
"""
Red tests for YouTube/Vimeo external sources (T-036).

This file exercises the future T-037 change to ``BunnyPlayer.save_event``:
for ``player_name`` ``youtube``/``vimeo`` the handler must derive
``video_ref.source_type``/``video_ref.video_id`` from the external URL and must
preserve the client-supplied ``duration`` (data-model.md: "для YouTube/Vimeo —
з плеєра (video.js)"), instead of always emitting ``source_type: 'bunny'`` with
the Bunny metadata ``bunny_video_id``/``bunny_length_seconds``.

Contract under test (tracking-events.md §1–2, data-model.md §2,
contracts/tracking-events.md)
-------------------------------------------------------------------------------------------------
* ``video_ref.source_type`` ∈ {bunny, youtube, vimeo}.
* ``video_ref.video_id`` is a GUID for Bunny or the normalised id/URL of the
  external source.  Normalisation uses the fork's existing ``player.media_id``
  seam (video_xblock.py:479):

    https://www.youtube.com/watch?v=abc123  →  "abc123"
    https://vimeo.com/123456                 →  "123456"

* ``duration`` for YouTube/Vimeo is taken from the client event; it is **not**
  overridden from Bunny metadata (because there is none for external sources).
* ``event_type`` ∈ {play, pause, complete}; publication uses
  ``runtime.publish(..., 'xblock-video.player.{event_type}', payload)``.

Three clauses, all offline
--------------------------
1. Student view for YouTube/Vimeo renders through the existing fork flow and
   contains no ``token=`` / ``expires=`` query parameters (regression guard;
   green now).
2. ``save_event`` payload for YouTube/Vimeo uses the correct ``video_ref`` and
   client ``duration`` (red now: T-037 not implemented; the current code always
   returns ``source_type: 'bunny'`` and ``duration: None``).
3. An unavailable external URL still renders the standard video.js player
   container; video.js shows its native "The media could not be loaded" message.
   No Bunny-specific unavailable markup is emitted (green now).
"""

import unittest
from unittest.mock import Mock, PropertyMock, patch

from django.test import override_settings
from lxml import etree
from xblock.field_data import DictFieldData
from xblock.test.tools import TestRuntime

from video_xblock.backends import bunny, vimeo, youtube
from video_xblock.bunny_config import DEFAULT_CONFIG_PATH, load_bunny_config
from video_xblock.constants import PlayerName
from video_xblock.video_xblock import VideoXBlock


YOUTUBE_HREF = 'https://www.youtube.com/watch?v=abc123'
YOUTUBE_MEDIA_ID = 'abc123'
VIMEO_HREF = 'https://vimeo.com/123456'
VIMEO_MEDIA_ID = '123456'
UNAVAILABLE_HREF = 'https://www.youtube.com/watch?v=nonexistent'


class ExternalStudentViewTests(unittest.TestCase):
    """
    YouTube/Vimeo render through the fork's video.js player without Bunny tokens.
    """

    def setUp(self):
        super().setUp()
        # Network is forbidden in unit tests (constitution II).
        for method in ("request", "send"):
            self.enterContext(patch(
                "requests.sessions.Session." + method,
                autospec=True,
                side_effect=AssertionError("Network access is forbidden in unit tests"),
            ))

        # The fork resolves backends through ``xblock.plugin.PLUGIN_CACHE``; in
        # the test environment the entry points are not installed, so seed the
        # cache exactly as test_student_view.py does for Bunny.
        self.enterContext(patch.dict(
            "xblock.plugin.PLUGIN_CACHE",
            {
                ("video_xblock.v1", PlayerName.YOUTUBE): youtube.YoutubePlayer,
                ("video_xblock.v1", PlayerName.VIMEO): vimeo.VimeoPlayer,
            },
            clear=False,
        ))

    def _render_external_player(self, player_name, href):
        """Run ``render_player`` for an external-source block."""
        xblock = VideoXBlock(
            TestRuntime(),
            DictFieldData({
                "account_id": "",
                "metadata": {},
                "player_name": player_name,
                "href": href,
            }),
            scope_ids=Mock(spec=[]),
        )
        xblock.runtime.handler_url = Mock(return_value="/handler/save_player_state")
        self.enterContext(patch.object(
            xblock, "route_transcripts", return_value=[],
        ))
        self.enterContext(patch.object(
            VideoXBlock, "player_state", new_callable=PropertyMock,
            return_value={"transcripts": [], "currentTime": ""},
        ))
        response = xblock.render_player(Mock(), "")
        return response.body.decode()

    def test_youtube_student_view_has_no_bunny_token_params(self):
        """Existing fork flow: YouTube render must not carry token/expires."""
        body = self._render_external_player(PlayerName.YOUTUBE, YOUTUBE_HREF)
        self.assertNotIn('token=', body)
        self.assertNotIn('expires=', body)

    def test_vimeo_student_view_has_no_bunny_token_params(self):
        """Existing fork flow: Vimeo render must not carry token/expires."""
        body = self._render_external_player(PlayerName.VIMEO, VIMEO_HREF)
        self.assertNotIn('token=', body)
        self.assertNotIn('expires=', body)

    def test_unavailable_external_link_renders_native_videojs_container(self):
        """
        US4 acceptance scenario 2: an unavailable external URL renders the
        standard video.js container. video.js shows its native "The media could
        not be loaded" error; we do not introduce Bunny-specific unavailable UI.
        """
        body = self._render_external_player(PlayerName.YOUTUBE, UNAVAILABLE_HREF)
        tree = etree.HTML(body.encode())
        video_nodes = tree.xpath(".//video[contains(@class, 'video-js')]")
        self.assertTrue(video_nodes, "video.js container must be rendered")
        self.assertNotIn('bunny-video-unavailable', body)
        self.assertNotIn('token=', body)
        self.assertNotIn('expires=', body)


class SaveEventExternalSourceTests(unittest.TestCase):
    """
    ``BunnyPlayer.save_event`` must derive video_ref and duration correctly for
    YouTube/Vimeo blocks (red until T-037).
    """

    def setUp(self):
        super().setUp()
        self.config = load_bunny_config(DEFAULT_CONFIG_PATH)

        # No network calls during event saving.
        for method in ("request", "send"):
            blocker = patch(
                "requests.sessions.Session." + method,
                autospec=True,
                side_effect=AssertionError("Network access is forbidden in unit tests"),
            )
            blocker.start()
            self.addCleanup(blocker.stop)

        self._build_xblock(PlayerName.YOUTUBE, YOUTUBE_HREF)

    def _build_xblock(self, player_name, href):
        """Create an external-source block in an LMS runtime."""
        self.xblock = VideoXBlock(
            TestRuntime(),
            DictFieldData({
                "account_id": "",
                "metadata": {},
                "player_name": player_name,
                "href": href,
            }),
            scope_ids=Mock(spec=[]),
        )
        # LMS-only handler: save_event is not allowed in Studio.
        self.xblock.runtime.is_author_mode = False

        self.usage_id = Mock(
            context_key="course-v1:demo+math101+2026",
            spec=["context_key", "__str__"],
        )
        self.usage_id.__str__ = Mock(
            return_value="block-v1:demo+math101+2026+type@video+block@video1"
        )
        self.xblock.scope_ids.usage_id = self.usage_id

        self.user = Mock()
        self.user.opt_attrs = {"edx-platform.user_id": 42}

        def service_handler(block, service_name):
            if service_name == "user":
                user_service = Mock()
                user_service.get_current_user.return_value = self.user
                return user_service
            raise AssertionError("Unexpected runtime service: {!r}".format(service_name))

        service_patcher = patch.object(
            self.xblock.runtime, "service", create=True, side_effect=service_handler
        )
        self.runtime_service = service_patcher.start()
        self.addCleanup(service_patcher.stop)

        publish_patcher = patch.object(
            self.xblock.runtime, "publish", create=True
        )
        self.runtime_publish = publish_patcher.start()
        self.addCleanup(publish_patcher.stop)

        self.player = bunny.BunnyPlayer(self.xblock)

    def _call_json_handler_body(self, handler_name, data):
        """Call the undecorated ``@XBlock.json_handler`` body."""
        handler = getattr(self.player, handler_name)
        return handler.__wrapped__(self.player, data, "")

    def _assert_tracking_payload(self, expected_source_type, expected_video_id,
                                 expected_duration):
        """Common assertions for the published tracking event payload."""
        self.runtime_publish.assert_called_once()
        args, _kwargs = self.runtime_publish.call_args
        self.assertIs(args[0], self.xblock)
        self.assertEqual(args[1], "xblock-video.player.play")
        payload = args[2]
        self.assertEqual(payload["user_id"], 42)
        self.assertEqual(payload["course_id"], "course-v1:demo+math101+2026")
        self.assertEqual(
            payload["unit_usage_key"],
            "block-v1:demo+math101+2026+type@video+block@video1",
        )
        self.assertEqual(
            payload["video_ref"],
            {"source_type": expected_source_type, "video_id": expected_video_id},
        )
        self.assertEqual(payload["event_type"], "play")
        self.assertEqual(payload["current_time"], 10.0)
        self.assertEqual(payload["duration"], expected_duration)
        self.assertEqual(payload["config_version"], self.config["version"])

    def test_youtube_save_event_uses_external_video_ref_and_client_duration(self):
        """
        T-037 must make save_event derive source_type='youtube' and video_id
        from ``YoutubePlayer.media_id``. Client duration is preserved.
        """
        result = self._call_json_handler_body(
            "save_event",
            {"event_type": "play", "current_time": 10.0, "duration": 123.45},
        )
        self.assertEqual(result, {"result": "success"})
        self._assert_tracking_payload("youtube", YOUTUBE_MEDIA_ID, 123.45)

    def test_vimeo_save_event_uses_external_video_ref_and_client_duration(self):
        """
        T-037 must make save_event derive source_type='vimeo' and video_id from
        ``VimeoPlayer.media_id``. Client duration is preserved.
        """
        self._build_xblock(PlayerName.VIMEO, VIMEO_HREF)
        result = self._call_json_handler_body(
            "save_event",
            {"event_type": "play", "current_time": 10.0, "duration": 234.56},
        )
        self.assertEqual(result, {"result": "success"})
        self._assert_tracking_payload("vimeo", VIMEO_MEDIA_ID, 234.56)


if __name__ == "__main__":
    unittest.main()
