# verifies: FR-001-08
"""
Access-guard red tests for the Bunny video XBlock.

T-028 is a test-first task for T-029.  The guards do not exist yet, so these
tests are intentionally red for the access-control reasons named in each test,
not because collection, request decoding, or offline Bunny fixtures are broken.

Runtime-context convention for T-029
------------------------------------
Open edX runtimes expose authoring/LMS mode on the runtime rather than in a
handler request body.  These tests therefore use ``runtime.is_author_mode`` as
the unit-test seam, matching XBlock's own ``studio_editable`` convention
(``getattr(self.runtime, 'is_author_mode', False)``):

* ``is_author_mode is False`` means the call comes from LMS/courseware;
* ``is_author_mode is True`` means the call comes from Studio/authoring;
* an absent attribute means LMS/courseware, so Studio-only handlers fail closed
  on runtimes that do not explicitly say they are in authoring mode.

For the unenrolled-student case, T-029 should add a ``BunnyPlayer._is_enrolled()``
seam.  Production code can implement it by asking the Open edX user/enrollment
services; if the platform answer is unavailable, the guard must fail closed and
not issue a signed Bunny URL.  Unit tests patch that seam with ``create=True``;
this file does not create it in production code.
"""

import unittest
from copy import deepcopy
from unittest.mock import Mock, patch

from xblock.exceptions import JsonHandlerError

from video_xblock.backends import bunny
from video_xblock.bunny_config import DEFAULT_CONFIG_PATH, load_bunny_config
from video_xblock.tests.fixtures.bunny.create_video import CREATE_VIDEO_200
from video_xblock.tests.unit import base as test_base


class BunnyAccessGuardHarnessMixin(object):
    """
    Shared offline harness for Bunny access-guard tests.

    The explicit ``runtime.is_author_mode`` marker is the contract that T-029
    should guard on.  An unmarked ``TestRuntime`` is LMS/courseware by the
    standard XBlock default; Studio tests that need authoring mode set the flag
    explicitly in their own harnesses.
    """

    def setUp(self):
        super().setUp()
        self.config = load_bunny_config(DEFAULT_CONFIG_PATH)

        # No network in unit tests: Bunny calls must be mocked or fixture-backed.
        for method in ("request", "send"):
            blocker = patch(
                "requests.sessions.Session." + method,
                autospec=True,
                side_effect=AssertionError("Network access is forbidden in unit tests"),
            )
            blocker.start()
            self.addCleanup(blocker.stop)

        # Handlers construct the client through BunnyApiClient.from_settings();
        # patch that exact factory so a successful handler path returns plain
        # JSON-serialisable data, not MagicMock objects.
        self.client = Mock(name="offline_bunny_client")
        self.client.library_id = CREATE_VIDEO_200["body"]["videoLibraryId"]
        self.client.tus_endpoint = self.config["tus_endpoint"]
        self.client.create_video.side_effect = lambda title: deepcopy(CREATE_VIDEO_200["body"])
        self.client.sign_upload.return_value = {
            "authorization_signature": "a" * 64,
            "authorization_expire": 1750000000,
        }
        self.client.get_video_info.return_value = {"status": 4, "length": 120}
        self.client.delete_video.return_value = {"success": True, "message": "OK"}
        self.client.signed_embed_url.return_value = "https://embed.example.invalid/signed"
        factory_patch = patch.object(
            bunny.BunnyApiClient,
            "from_settings",
            return_value=self.client,
        )
        factory_patch.start()
        self.addCleanup(factory_patch.stop)

        self.player = bunny.BunnyPlayer(self.xblock)

    def _mark_runtime_as_lms(self):
        """Simulate LMS/courseware on TestRuntime using the T-029 convention."""
        self.xblock.runtime.is_author_mode = False

    def _mark_runtime_as_studio(self):
        """Simulate Studio/authoring on TestRuntime using the T-029 convention."""
        self.xblock.runtime.is_author_mode = True

    def _call_json_handler_body(self, handler_name, data):
        """
        Call the undecorated json_handler body with decoded JSON data.

        ``XBlock.json_handler`` turns ``JsonHandlerError`` into a WebOb response;
        these guard tests need to assert that the handler itself raises the
        contract error.  Calling ``__wrapped__`` keeps the test focused on the
        guard instead of the JSON transport wrapper.
        """
        handler = getattr(self.player, handler_name)
        return handler.__wrapped__(self.player, data, "")

    def _ready_bunny_metadata(self):
        """Seed a READY Bunny video using fixture/config values only."""
        self.xblock.metadata.update({
            "bunny_video_id": CREATE_VIDEO_200["body"]["guid"],
            "bunny_library_id": CREATE_VIDEO_200["body"]["videoLibraryId"],
            "bunny_status": "READY",
            "bunny_length_seconds": 120,
            "bunny_title": CREATE_VIDEO_200["body"]["title"],
            "source_type": "bunny",
            "token_protected": True,
            "config_version": self.config["version"],
        })


class StudioOnlyHandlerGuardTests(BunnyAccessGuardHarnessMixin, test_base.VideoXBlockTestBase):
    """Studio-authoring Bunny handlers must not execute in LMS/courseware."""

    def setUp(self):
        super().setUp()
        self._mark_runtime_as_lms()

    def test_create_upload_not_allowed_in_lms(self):
        with self.assertRaises(JsonHandlerError):
            self._call_json_handler_body("create_upload", {
                "file_name": "lecture.{}".format(self.config["allowed_extensions"][0]),
                "file_type": "video/mp4",
                "file_size": min(1024, self.config["max_upload_bytes"]),
            })

    def test_upload_credentials_not_allowed_in_lms(self):
        self.xblock.metadata.update({
            "bunny_video_id": CREATE_VIDEO_200["body"]["guid"],
        })
        with self.assertRaises(JsonHandlerError):
            self._call_json_handler_body("upload_credentials", {
                "video_id": CREATE_VIDEO_200["body"]["guid"],
            })

    def test_video_info_not_allowed_in_lms(self):
        with self.assertRaises(JsonHandlerError):
            self._call_json_handler_body("video_info", {
                "video_id": CREATE_VIDEO_200["body"]["guid"],
            })

    def test_delete_video_not_allowed_in_lms(self):
        self.xblock.metadata.update({
            "bunny_video_id": CREATE_VIDEO_200["body"]["guid"],
        })
        with self.assertRaises(JsonHandlerError):
            self._call_json_handler_body("delete_video", {})


class StudioAndEnrollmentGuardTests(BunnyAccessGuardHarnessMixin, test_base.VideoXBlockTestBase):
    """LMS-only event saving and platform enrollment checks for viewing."""

    def test_save_event_not_allowed_in_studio(self):
        """
        ``save_event`` is LMS-only per bunny-api.md §7.

        It is created later in T-035, so this test is expected to stay red with
        ``AttributeError: 'BunnyPlayer' object has no attribute 'save_event'``
        after T-029 and become green in T-035 when the handler exists with its
        LMS-only guard.  Do not substitute the fork's unrelated
        ``VideoXBlock.save_player_state`` here: it is outside FR-001-08.
        """
        self._mark_runtime_as_studio()
        with self.assertRaises(JsonHandlerError):
            self._call_json_handler_body("save_event", {
                "event_type": "play",
                "current_time": 0,
                "duration": 120,
            })

    def test_unenrolled_student_view_does_not_include_signed_url(self):
        self._mark_runtime_as_lms()
        self._ready_bunny_metadata()

        with patch.object(bunny.BunnyPlayer, "_is_enrolled", create=True, return_value=False):
            context = self.player.player_data_setup({})

        self.assertNotIn("signed_embed_url", context)

    def test_falsey_enrollment_response_fails_closed(self):
        """
        A falsey platform enrollment answer (e.g. ``[]``) must be unenrolled.

        The previous ``return enrollment is not None`` would treat ``[]`` as
        enrolled and leak a signed embed URL. The fail-closed rule is: only a
        truthy enrollment response grants access; everything else is denied.
        """
        self._mark_runtime_as_lms()
        self._ready_bunny_metadata()

        user = Mock()
        user.opt_attrs = {'edx-platform.user_id': 42}

        def service_handler(block, service_name):
            if service_name == 'user':
                user_service = Mock()
                user_service.get_current_user.return_value = user
                return user_service
            if service_name == 'enrollments':
                enrollment_service = Mock()
                enrollment_service.get_active_enrollments_by_course_and_user.return_value = []
                return enrollment_service
            raise AssertionError("Unexpected runtime service: {!r}".format(service_name))

        with patch.object(self.xblock.runtime, 'service', create=True,
                          side_effect=service_handler):
            is_enrolled = self.player._is_enrolled()
            context = self.player.player_data_setup({})

        self.assertFalse(is_enrolled,
                         "Falsey enrollment response must fail closed")
        self.assertNotIn("signed_embed_url", context,
                         "Unenrolled student must not receive a signed embed URL")


if __name__ == "__main__":
    unittest.main()
