# verifies: FR-001-16
"""
Tests for the ``delete_video`` Studio handler and the replace path (T-022).

T-022 stops at the missing ``delete_video`` handler; T-023 supplies production
code. The handler deletes the block's video in Bunny and returns the block to
the EMPTY state; deleting an already-missing video is an idempotent success;
and ``create_upload`` over a READY block replaces it by deleting the old video
id first. Every Bunny call goes through the mocked ``BunnyApiClient`` and the
transport is blocked, so no test can reach the network (constitution II).

Because ``BunnyPlayer.delete_video`` does not exist yet, the delete tests fail
at the handler call with ``AttributeError`` — the expected red of this task.
"""

import json
import unittest
from copy import deepcopy
from unittest.mock import Mock, call, patch

import yaml
from xblock.field_data import DictFieldData
from xblock.test.tools import TestRuntime

from video_xblock.backends import bunny
from video_xblock.bunny_config import DEFAULT_CONFIG_PATH
from video_xblock.exceptions import ApiClientError
from video_xblock.tests.fixtures.bunny.create_video import CREATE_VIDEO_200
from video_xblock.tests.fixtures.bunny.delete_video import DELETE_VIDEO_200, DELETE_VIDEO_404
from video_xblock.tests.unit.base import arrange_request_mock
from video_xblock.video_xblock import VideoXBlock


# The block already holds a finished video; ``delete_video``/replace must get
# rid of this exact guid. It deliberately differs from the guid returned by
# ``create_video`` so the replace test can tell the old and new videos apart.
OLD_VIDEO_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

# The TUS signature expiry the mock client hands out for the *new* video. It
# deliberately differs from the stale ``upload_signature_expires`` seeded into
# the block, so the replace test can prove the new value is actually written
# (not just left over from the previous READY video).
NEW_SIGNATURE_EXPIRES = 1750000999


class DeleteVideoHandlerTests(unittest.TestCase):
    """Drive ``BunnyPlayer`` delete/replace through its public JSON handlers."""

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

        with DEFAULT_CONFIG_PATH.open(encoding="utf-8") as config_file:
            self.config = yaml.safe_load(config_file)

        # Mock the Bunny API client so the handlers can be exercised offline.
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
            "authorization_expire": NEW_SIGNATURE_EXPIRES,
        }

        # A block that already holds a finished (READY) video.
        self.xblock = VideoXBlock(
            TestRuntime(),
            DictFieldData({
                "account_id": "account_id",
                "metadata": {
                    "bunny_video_id": OLD_VIDEO_ID,
                    "bunny_library_id": self.client.library_id,
                    "bunny_status": "READY",
                    "bunny_length_seconds": 612.5,
                    "bunny_title": "Лекція 1",
                    "source_type": "bunny",
                    "token_protected": True,
                    "config_version": self.config["version"],
                    "upload_signature_expires": 1750000000,
                },
            }),
            scope_ids=Mock(spec=[]),
        )

    def _delete_request(self):
        """The delete handler takes the video id from block metadata (contract §2.4)."""
        return arrange_request_mock(json.dumps({}))

    def _assert_reset_to_empty(self):
        """The block must no longer reference any video and read as EMPTY.

        Every bunny metadata key (data-model.md §1) is cleared by the delete
        handler: the video id, library id, duration, title, source type, token
        flag, config version and the upload-signature expiry are all dropped,
        and only the status reads EMPTY. An incomplete cleanup — any single key
        left behind — must fail the test.
        """
        self.assertIsNone(self.xblock.metadata.get("bunny_video_id"))
        self.assertIsNone(self.xblock.metadata.get("bunny_library_id"))
        self.assertEqual(self.xblock.metadata.get("bunny_status", "EMPTY"), "EMPTY")
        self.assertIsNone(self.xblock.metadata.get("bunny_length_seconds"))
        self.assertIsNone(self.xblock.metadata.get("bunny_title"))
        self.assertIsNone(self.xblock.metadata.get("source_type"))
        self.assertIsNone(self.xblock.metadata.get("token_protected"))
        self.assertIsNone(self.xblock.metadata.get("config_version"))
        self.assertIsNone(self.xblock.metadata.get("upload_signature_expires"))

    def test_delete_video_deletes_video_and_resets_to_empty(self):
        self.client.delete_video.return_value = deepcopy(DELETE_VIDEO_200["body"])
        player = bunny.BunnyPlayer(self.xblock)

        response = player.delete_video(self._delete_request())

        self.assertEqual(response.status_int, 200)
        self.client.delete_video.assert_called_once_with(OLD_VIDEO_ID)
        self._assert_reset_to_empty()

    def test_delete_video_is_idempotent_on_already_deleted_video(self):
        # A repeated DELETE of a missing video (DELETE_VIDEO_404) is an
        # idempotent success (bunny-api.md §4): the client maps the 404 to a
        # truthy result, and the handler must still reset the block.
        self.assertEqual(DELETE_VIDEO_404["status_code"], 404)
        self.client.delete_video.return_value = True
        player = bunny.BunnyPlayer(self.xblock)

        response = player.delete_video(self._delete_request())

        self.assertEqual(response.status_int, 200)
        self.client.delete_video.assert_called_once_with(OLD_VIDEO_ID)
        self._assert_reset_to_empty()

    def test_create_upload_replaces_ready_video_by_deleting_old_guid(self):
        player = bunny.BunnyPlayer(self.xblock)
        request = arrange_request_mock(json.dumps({
            "file_name": "new-lecture.mp4",
            "file_size": 1024,
            "file_type": "video/mp4",
        }))

        response = player.create_upload(request)

        self.assertEqual(response.status_int, 200)
        # Replace: the old READY video is deleted before the new one is
        # created, and the block points at the new guid.
        self.client.delete_video.assert_called_once_with(OLD_VIDEO_ID)
        self.client.create_video.assert_called_once_with("new-lecture.mp4")
        self.assertLess(
            self.client.mock_calls.index(call.delete_video(OLD_VIDEO_ID)),
            self.client.mock_calls.index(call.create_video("new-lecture.mp4")),
        )
        self.assertEqual(
            self.xblock.metadata["bunny_video_id"],
            CREATE_VIDEO_200["body"]["guid"],
        )

    def test_delete_video_on_empty_block_resets_without_bunny_call(self):
        # A block that never held a video (data-model §1: no bunny_video_id) is
        # simply reset to EMPTY — no Bunny call must be made for a missing id.
        empty_block = VideoXBlock(
            TestRuntime(),
            DictFieldData({"account_id": "account_id", "metadata": {}}),
            scope_ids=Mock(spec=[]),
        )
        player = bunny.BunnyPlayer(empty_block)

        response = player.delete_video(self._delete_request())

        self.assertEqual(response.status_int, 200)
        self.client.delete_video.assert_not_called()
        self.assertIsNone(empty_block.metadata.get("bunny_video_id"))
        self.assertEqual(empty_block.metadata.get("bunny_status", "EMPTY"), "EMPTY")

    def test_create_upload_replace_clears_stale_fields_and_writes_new(self):
        # Replace (FR-001-16): after a successful create over an existing video
        # the block is UPLOADING with the new video's metadata — the stale
        # duration of the previous READY video is cleared and the new TUS
        # signature expiry replaces the old one (data-model.md §1).
        player = bunny.BunnyPlayer(self.xblock)
        request = arrange_request_mock(json.dumps({
            "file_name": "new-lecture.mp4",
            "file_size": 1024,
            "file_type": "video/mp4",
        }))

        response = player.create_upload(request)

        self.assertEqual(response.status_int, 200)
        self.assertEqual(
            self.xblock.metadata["bunny_video_id"],
            CREATE_VIDEO_200["body"]["guid"],
        )
        self.assertEqual(self.xblock.metadata["bunny_status"], "UPLOADING")
        self.assertIsNone(self.xblock.metadata["bunny_length_seconds"])
        self.assertEqual(
            self.xblock.metadata["upload_signature_expires"],
            NEW_SIGNATURE_EXPIRES,
        )

    def test_create_upload_resets_to_empty_when_create_video_fails_after_delete(self):
        # Replace (FR-001-16): the old video is deleted *first*, so once that
        # DELETE succeeds the object is gone from Bunny. If creating the new
        # video then fails, the block must not keep pointing at the dead guid:
        # it is reset to the truthful EMPTY state (every metadata key cleared,
        # status EMPTY — data-model.md §1) and the 502 error reaches the client.
        self.client.create_video.side_effect = ApiClientError("service down")
        player = bunny.BunnyPlayer(self.xblock)
        request = arrange_request_mock(json.dumps({
            "file_name": "new-lecture.mp4",
            "file_size": 1024,
            "file_type": "video/mp4",
        }))

        response = player.create_upload(request)

        self.assertEqual(response.status_int, 502)
        self.client.delete_video.assert_called_once_with(OLD_VIDEO_ID)
        self._assert_reset_to_empty()

    def test_create_upload_keeps_old_state_when_delete_fails(self):
        # If the DELETE of the old video fails, the old object still exists in
        # Bunny: the block must keep its old reference (fail-safe, FR-001-16),
        # no create is attempted, and the 502 error reaches the client.
        self.client.delete_video.side_effect = ApiClientError("service down")
        player = bunny.BunnyPlayer(self.xblock)
        request = arrange_request_mock(json.dumps({
            "file_name": "new-lecture.mp4",
            "file_size": 1024,
            "file_type": "video/mp4",
        }))

        response = player.create_upload(request)

        self.assertEqual(response.status_int, 502)
        self.client.delete_video.assert_called_once_with(OLD_VIDEO_ID)
        self.client.create_video.assert_not_called()
        self.assertEqual(self.xblock.metadata["bunny_video_id"], OLD_VIDEO_ID)
        self.assertEqual(self.xblock.metadata["bunny_status"], "READY")
        self.assertEqual(self.xblock.metadata["bunny_length_seconds"], 612.5)
