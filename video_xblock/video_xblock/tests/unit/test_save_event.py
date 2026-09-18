# verifies: FR-001-12, FR-001-13, FR-001-15
"""
Red test for the LMS-only ``save_event`` JSON handler (T-034).

``save_event`` is implemented in T-035, so this file is intentionally red with
``AttributeError: 'BunnyPlayer' object has no attribute 'save_event'``.  The
handler must *not* be simulated or stubbed here: the failure must come from the
single missing method, not from harness errors, missing imports, or network
access.

Contract under test (tracking-events.md §1–3, contracts/xblock-interface.md §2.5)
---------------------------------------------------------------------------------
* Input: ``{event_type ∈ {play,pause,complete}, current_time: float,
  duration: float}``.  For Bunny the server ignores the client ``duration`` and
  takes it from block metadata.
* Validation: unknown ``event_type`` → ``JsonHandlerError`` 400; ``current_time``
  that is not a number or is a ``bool`` → 400.  On validation failure
  ``runtime.publish`` is never called.
* Publication: ``runtime.publish(self.xblock,
  'xblock-video.player.play'|'xblock-video.player.pause'|
  'xblock-video.player.complete', payload)``.
* Server payload (the client is not trusted):

    user_id      = runtime.service(self, 'user').get_current_user()
                   .opt_attrs['edx-platform.user_id']
    course_id    = str(scope_ids.usage_id.context_key)
    unit_usage_key = str(scope_ids.usage_id)
    video_ref    = {'source_type': 'bunny', 'video_id': metadata['bunny_video_id']}
    config_version = bunny_config.yaml version
    current_time = validated client value
    duration     = metadata['bunny_length_seconds']
    event_type   = the validated event string

* Anonymous users (missing user service, missing opt attr, or any service error)
  must not crash the handler and must not trigger ``runtime.publish``; the
  handler returns a successful response such as ``{'result': 'success'}``.
* Studio guard (``runtime.is_author_mode=True``) is covered by
  ``test_access_guards.py::test_save_event_not_allowed_in_studio`` and is not
  duplicated here.

The single test below exercises the full contract: complete/play/pause events,
server-side duration override, input validation, and anonymous-user silence.
"""

import json
import os
import unittest
from unittest.mock import Mock, patch

from xblock.exceptions import JsonHandlerError
from xblock.field_data import DictFieldData
from xblock.test.tools import TestRuntime

from video_xblock.backends import bunny
from video_xblock.bunny_config import DEFAULT_CONFIG_PATH, load_bunny_config
from video_xblock.tests.fixtures.bunny.create_video import CREATE_VIDEO_200
from video_xblock.tests.fixtures.bunny.video_info import VIDEO_INFO_STATUSES
from video_xblock.tests.fixtures.save_event.complete_view import CLIENT_EVENT as COMPLETE_EVENT
from video_xblock.tests.fixtures.save_event.partial_view import CLIENT_EVENT as PARTIAL_EVENT
from video_xblock.video_xblock import VideoXBlock


class SaveEventHandlerTests(unittest.TestCase):
    """Offline contract tests for ``BunnyPlayer.save_event``."""

    def setUp(self):
        super().setUp()
        self.config = load_bunny_config(DEFAULT_CONFIG_PATH)

        # Block every live HTTP call (constitution II).
        for method in ("request", "send"):
            blocker = patch(
                "requests.sessions.Session." + method,
                autospec=True,
                side_effect=AssertionError("Network access is forbidden in unit tests"),
            )
            blocker.start()
            self.addCleanup(blocker.stop)

        self.xblock = VideoXBlock(
            TestRuntime(),
            DictFieldData({"account_id": "account_id", "metadata": {}}),
            scope_ids=Mock(spec=[]),
        )
        # LMS/courseware context: save_event is not allowed in Studio.
        self.xblock.runtime.is_author_mode = False

        # Usage id carries both the course key and the block identity.
        self.usage_id = Mock(
            context_key="course-v1:demo+math101+2026",
            spec=["context_key", "__str__"],
        )
        self.usage_id.__str__ = Mock(
            return_value="block-v1:demo+math101+2026+type@video+block@video1"
        )
        self.xblock.scope_ids.usage_id = self.usage_id

        # Authenticated user with a stable platform id.
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
        self._seed_ready_metadata()

    def _seed_ready_metadata(self):
        """Plant a READY Bunny video so server-side payload fields exist."""
        self.xblock.metadata.update({
            "bunny_video_id": CREATE_VIDEO_200["body"]["guid"],
            "bunny_library_id": CREATE_VIDEO_200["body"]["videoLibraryId"],
            "bunny_status": "READY",
            "bunny_length_seconds": VIDEO_INFO_STATUSES[4]["body"]["length"],
            "bunny_title": CREATE_VIDEO_200["body"]["title"],
            "source_type": "bunny",
            "token_protected": True,
            "config_version": self.config["version"],
        })

    def _fixture_path(self, name):
        return os.path.join(
            os.path.dirname(__file__), "..", "fixtures", "save_event", name
        )

    def _load_client_event(self, name):
        with open(self._fixture_path(name), "r", encoding="utf-8") as fobj:
            return json.load(fobj)

    def _call_json_handler_body(self, handler_name, data):
        """Call the undecorated ``@XBlock.json_handler`` body."""
        handler = getattr(self.player, handler_name)
        return handler.__wrapped__(self.player, data, "")

    def test_save_event_contract(self):
        """
        Full contract: valid events publish a server payload, invalid input
        raises 400 without publishing, and anonymous users are silently ignored.
        """
        # 1. Complete event (fixture duration 999 must be ignored).
        data = COMPLETE_EVENT
        result = self._call_json_handler_body("save_event", data)
        self.assertEqual(result, {"result": "success"})

        self.assertEqual(self.runtime_publish.call_count, 1)
        args, _kwargs = self.runtime_publish.call_args
        self.assertIs(args[0], self.xblock)
        self.assertEqual(args[1], "xblock-video.player.complete")
        payload = args[2]
        self.assertEqual(payload["user_id"], 42)
        self.assertEqual(payload["course_id"], "course-v1:demo+math101+2026")
        self.assertEqual(payload["unit_usage_key"], "block-v1:demo+math101+2026+type@video+block@video1")
        self.assertEqual(
            payload["video_ref"],
            {
                "source_type": "bunny",
                "video_id": CREATE_VIDEO_200["body"]["guid"],
            },
        )
        self.assertEqual(payload["event_type"], "complete")
        self.assertEqual(payload["current_time"], 594.25)
        self.assertEqual(payload["duration"], 612.5)
        self.assertEqual(payload["config_version"], self.config["version"])

        # 2. Play event (partial view fixture).
        self.runtime_publish.reset_mock()
        data = PARTIAL_EVENT
        result = self._call_json_handler_body("save_event", data)
        self.assertEqual(result, {"result": "success"})
        self.runtime_publish.assert_called_once()
        args = self.runtime_publish.call_args[0]
        self.assertEqual(args[1], "xblock-video.player.play")
        payload = args[2]
        self.assertEqual(payload["current_time"], 245.0)
        self.assertEqual(payload["duration"], 612.5)

        # 3. Pause event.
        self.runtime_publish.reset_mock()
        result = self._call_json_handler_body(
            "save_event",
            {"event_type": "pause", "current_time": 300.0, "duration": 999},
        )
        self.assertEqual(result, {"result": "success"})
        self.runtime_publish.assert_called_once()
        args = self.runtime_publish.call_args[0]
        self.assertEqual(args[1], "xblock-video.player.pause")
        payload = args[2]
        self.assertEqual(payload["current_time"], 300.0)
        self.assertEqual(payload["duration"], 612.5)

        # 4. Unknown event type -> 400, no publish.
        self.runtime_publish.reset_mock()
        with self.assertRaises(JsonHandlerError) as cm:
            self._call_json_handler_body(
                "save_event",
                {"event_type": "seek", "current_time": 10.0, "duration": 999},
            )
        self.assertEqual(cm.exception.status_code, 400)
        self.runtime_publish.assert_not_called()

        # 5. Non-numeric current_time -> 400, no publish.
        with self.assertRaises(JsonHandlerError) as cm:
            self._call_json_handler_body(
                "save_event",
                {"event_type": "play", "current_time": "abc", "duration": 999},
            )
        self.assertEqual(cm.exception.status_code, 400)
        self.runtime_publish.assert_not_called()

        # 6. Bool current_time (JSON true is a bool, not a number) -> 400.
        with self.assertRaises(JsonHandlerError) as cm:
            self._call_json_handler_body(
                "save_event",
                {"event_type": "play", "current_time": True, "duration": 999},
            )
        self.assertEqual(cm.exception.status_code, 400)
        self.runtime_publish.assert_not_called()

        # 7. Anonymous user -> success response, no publish.
        self.runtime_publish.reset_mock()
        self.user.opt_attrs = {}
        data = PARTIAL_EVENT
        result = self._call_json_handler_body("save_event", data)
        self.assertEqual(result, {"result": "success"})
        self.runtime_publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
