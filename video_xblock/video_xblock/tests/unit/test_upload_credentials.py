# verifies: FR-001-03, FR-001-09
"""Resume credentials through the public JSON handler (contract §2.2), offline."""

import json
import unittest
from copy import deepcopy
from unittest.mock import Mock, call, patch

import yaml
from xblock.field_data import DictFieldData
from xblock.test.tools import TestRuntime

from video_xblock.backends import bunny
from video_xblock.bunny_config import DEFAULT_CONFIG_PATH
from video_xblock.tests.fixtures.bunny.create_video import CREATE_VIDEO_200
from video_xblock.tests.unit.base import arrange_request_mock
from video_xblock.video_xblock import VideoXBlock


class UploadCredentialsTests(unittest.TestCase):
    """Refreshing an interrupted upload must not create a replacement video."""

    def setUp(self):
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
        # Sentinel test datum distinguishes the injected config from defaults.
        self.config["upload_auth_ttl_seconds"] += 137
        config_patch = patch.object(bunny, "load_bunny_config", return_value=self.config)
        self.config_loader = config_patch.start()
        self.addCleanup(config_patch.stop)

        client_patch = patch.object(bunny, "BunnyApiClient")
        self.client_class = client_patch.start()
        self.addCleanup(client_patch.stop)
        self.client = self.client_class.return_value
        self.client_class.from_settings.return_value = self.client
        self.video_id = CREATE_VIDEO_200["body"]["guid"]
        self.client.library_id = CREATE_VIDEO_200["body"]["videoLibraryId"]
        self.client.tus_endpoint = self.config["tus_endpoint"]
        self.client.create_video.side_effect = lambda title: deepcopy(CREATE_VIDEO_200["body"])

        runtime = TestRuntime()
        runtime.is_author_mode = True  # T-029 access guards: Studio-authoring context.
        self.xblock = VideoXBlock(
            runtime,
            DictFieldData({
                "account_id": "account_id",
                "metadata": {
                    "bunny_video_id": self.video_id,
                    "bunny_library_id": self.client.library_id,
                    "bunny_status": "UPLOADING",
                    "source_type": "bunny",
                    "token_protected": True,
                },
            }),
            scope_ids=Mock(spec=[]),
        )

    def test_repeated_requests_refresh_credentials_for_same_video_without_creation(self):
        # Signing is the client's responsibility: opaque mock signatures, not
        # a duplicate of its production hash formula. Model two issuance times.
        ttl = self.config["upload_auth_ttl_seconds"]
        issued_at = (1750000000, 1750000000 + ttl + 1)
        credentials = [
            {"authorization_signature": "a" * 64, "authorization_expire": issued_at[0] + ttl},
            {"authorization_signature": "b" * 64, "authorization_expire": issued_at[1] + ttl},
        ]
        self.client.sign_upload.side_effect = deepcopy(credentials)
        player = bunny.BunnyPlayer(self.xblock)
        bodies = []

        for index, expected_credentials in enumerate(credentials):
            request = arrange_request_mock(json.dumps({"video_id": self.video_id}))
            response = player.upload_credentials(request)

            self.assertEqual(response.status_int, 200)
            body = json.loads(response.body)
            self.assertEqual(body, {
                "video_id": self.video_id,
                "library_id": CREATE_VIDEO_200["body"]["videoLibraryId"],
                "tus_endpoint": self.config["tus_endpoint"],
                **expected_credentials,
            })
            self.assertEqual(body["authorization_expire"] - issued_at[index], ttl)
            self.assertEqual(self.xblock.metadata["bunny_video_id"], self.video_id)
            self.client.create_video.assert_not_called()
            self.assertEqual(
                self.client.sign_upload.call_args_list,
                [call(self.video_id)] * (index + 1),
            )
            self.config_loader.assert_called_with(DEFAULT_CONFIG_PATH)
            self.client_class.from_settings.assert_called_with(self.config)
            bodies.append(body)

        self.assertEqual(bodies[0]["video_id"], bodies[1]["video_id"])
        self.assertNotEqual(bodies[0]["authorization_signature"], bodies[1]["authorization_signature"])
        self.assertGreater(bodies[1]["authorization_expire"], bodies[0]["authorization_expire"])

    def _assert_foreign_rejected(self, response):
        """Rejected with a clear 400 and no signing call (ownership check)."""
        self.assertEqual(response.status_int, 400)
        body = json.loads(response.body)
        message = body.get("error", body.get("message", ""))
        self.assertIsInstance(message, str)
        self.assertRegex(
            message.lower(),
            r"block|блок|video|відео|належ|збіга|відповід|власн",
        )
        self.client.sign_upload.assert_not_called()

    def test_rejects_foreign_video_id_without_signing(self):
        # A valid-looking GUID that is not this block's bunny_video_id must be
        # refused: resume may only re-sign the block's own video (FR-001-03).
        player = bunny.BunnyPlayer(self.xblock)
        request = arrange_request_mock(json.dumps({
            "video_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        }))
        response = player.upload_credentials(request)
        self._assert_foreign_rejected(response)

    def test_rejects_block_without_bunny_video_id(self):
        # A block that has no bunny_video_id has nothing to resume (FR-001-03).
        runtime = TestRuntime()
        runtime.is_author_mode = True  # T-029 access guards: Studio-authoring context.
        xblock = VideoXBlock(
            runtime,
            DictFieldData({"account_id": "account_id", "metadata": {}}),
            scope_ids=Mock(spec=[]),
        )
        player = bunny.BunnyPlayer(xblock)
        request = arrange_request_mock(json.dumps({"video_id": self.video_id}))
        response = player.upload_credentials(request)
        self._assert_foreign_rejected(response)
