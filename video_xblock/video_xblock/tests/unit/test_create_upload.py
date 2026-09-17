# verifies: FR-001-02
"""
Tests for the ``create_upload`` Studio handler (contracts/xblock-interface.md §2.1).

The handler must accept MP4/MOV/WebM uploads and reject anything else with a
clear, human-readable message *before* any Bunny API call. These tests drive
the handler through its public surface — ``BunnyPlayer(xblock).create_upload``
— never through a local re-implementation of the format/MIME checks; the
validation logic itself belongs to the handler (implemented in the follow-up
task). The Bunny API client is mocked offline and the transport is blocked, so
no test can reach the network (constitution II).

Because ``video_xblock.backends.bunny.BunnyPlayer`` does not exist yet, every
test fails at instantiation with an ``AttributeError`` — the expected red of
this test-first task.
"""

import json
import unittest
from copy import deepcopy
from unittest.mock import Mock, patch

import yaml
from xblock.field_data import DictFieldData
from xblock.test.tools import TestRuntime

from video_xblock.backends import bunny
from video_xblock.bunny_config import DEFAULT_CONFIG_PATH
from video_xblock.tests.fixtures.bunny.create_video import CREATE_VIDEO_200
from video_xblock.tests.unit.base import arrange_request_mock
from video_xblock.video_xblock import VideoXBlock


# Formats the feature MUST accept, per the bundled (versioned) config. This is
# test data that mirrors FR-001-02; the handler reads the authoritative list
# from bunny_config.yaml (constitution III).
SUPPORTED_EXTENSIONS = ["mp4", "mov", "webm"]
EXTENSION_MIME = {"mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm"}


class CreateUploadHandlerTests(unittest.TestCase):
    """
    Drive ``BunnyPlayer.create_upload`` with well- and mal-formed uploads.
    """

    def setUp(self):
        # No network in unit tests: fail loudly on any real HTTP transport.
        for method in ("request", "send"):
            blocker = patch(
                "requests.sessions.Session." + method,
                autospec=True,
                side_effect=AssertionError("Network access is forbidden in unit tests"),
            )
            blocker.start()
            self.addCleanup(blocker.stop)

        runtime = TestRuntime()  # pylint: disable=abstract-class-instantiated
        self.xblock = VideoXBlock(
            runtime,
            DictFieldData({"account_id": "account_id", "metadata": {}}),
            scope_ids=Mock(spec=[]),
        )

        # Validation inputs (allowed extensions, size limit, TUS endpoint) come
        # from the versioned YAML, not from literals in this test. Read the raw
        # mapping here: the test only consumes constant values, and must not
        # depend on the production loader's schema validation (which has its own
        # test in test_bunny_config.py).
        with DEFAULT_CONFIG_PATH.open(encoding="utf-8") as config_file:
            self.config = yaml.safe_load(config_file)

        # Mock the Bunny API client so the handler can be exercised offline.
        client_patch = patch.object(bunny, "BunnyApiClient")
        client_class = client_patch.start()
        self.addCleanup(client_patch.stop)
        self.client = client_class.return_value
        client_class.from_settings.return_value = self.client
        self.client.library_id = CREATE_VIDEO_200["body"]["videoLibraryId"]
        self.client.tus_endpoint = self.config["tus_endpoint"]
        self.client.create_video.side_effect = lambda title: deepcopy(CREATE_VIDEO_200["body"])
        self.client.sign_upload.return_value = {
            "authorization_signature": "a" * 64,
            "authorization_expire": 1750000000,
        }

    def _request(self, file_name, file_type):
        """Build a request mock for the create_upload handler."""
        return arrange_request_mock(json.dumps({
            "file_name": file_name,
            "file_size": min(1024, self.config["max_upload_bytes"]),
            "file_type": file_type,
        }))

    def _create_upload(self, file_name, file_type):
        """
        Instantiate ``BunnyPlayer`` and call the ``create_upload`` handler.

        The class does not exist until the follow-up task, so this line is
        where every test turns red with ``AttributeError`` today.
        """
        player = bunny.BunnyPlayer(self.xblock)
        return player.create_upload(self._request(file_name, file_type))

    def _assert_rejected(self, file_name, file_type, message_re):
        """
        Assert the handler rejects the upload with a clear 400 and no Bunny call.

        This only asserts on the handler's *response*; it never re-implements
        the format/MIME rules being verified.
        """
        self.client.reset_mock()
        response = self._create_upload(file_name, file_type)
        self.assertEqual(response.status_int, 400)
        body = json.loads(response.body)
        message = body.get("error", body.get("message", ""))
        self.assertIsInstance(message, str)
        self.assertRegex(message.lower(), message_re)
        self.client.create_video.assert_not_called()
        self.client.sign_upload.assert_not_called()

    def test_accepts_allowed_extensions(self):
        """Each extension from the bundled config is accepted and creates a video."""
        self.assertEqual(self.config["allowed_extensions"], SUPPORTED_EXTENSIONS)
        for extension in self.config["allowed_extensions"]:
            self.client.reset_mock()
            response = self._create_upload("lecture." + extension, EXTENSION_MIME[extension])

            self.assertEqual(response.status_int, 200)
            body = json.loads(response.body)
            self.assertEqual(body["video_id"], CREATE_VIDEO_200["body"]["guid"])
            self.assertEqual(body["library_id"], CREATE_VIDEO_200["body"]["videoLibraryId"])
            self.assertEqual(body["tus_endpoint"], self.config["tus_endpoint"])
            self.assertEqual(body["authorization_signature"], "a" * 64)
            self.assertEqual(body["authorization_expire"], 1750000000)
            self.client.create_video.assert_called_once()

    def test_accepts_video_mime_prefix(self):
        """Any ``video/*`` MIME is accepted for an allowed extension (no exact allowlist)."""
        for extension in self.config["allowed_extensions"]:
            self.client.reset_mock()
            response = self._create_upload("lecture." + extension, "video/x-custom-codec")

            self.assertEqual(response.status_int, 200)
            self.client.create_video.assert_called_once()

    def test_rejects_unsupported_extension(self):
        """Unsupported or missing extensions are rejected before any Bunny call."""
        for file_name in ("lecture.avi", "lecture.mp4.exe", "lecture"):
            self._assert_rejected(
                file_name, "video/mp4",
                r"format|extension|формат|розширенн",
            )

    def test_rejects_non_video_mime(self):
        """A non-``video/*`` MIME is rejected before any Bunny call."""
        for file_type in ("application/octet-stream", "audio/mp4", "application/video", ""):
            self._assert_rejected(
                "lecture.mp4", file_type,
                r"mime|video|відео|тип|type",
            )
