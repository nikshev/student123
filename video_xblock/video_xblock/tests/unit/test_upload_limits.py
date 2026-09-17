# verifies: FR-001-04
"""Upload size boundaries through the public JSON handler, without network access."""

import json
import re
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


class UploadLimitsTests(unittest.TestCase):
    """The size limit is inclusive and checked before contacting Bunny."""

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
        self.config["max_upload_bytes"] = 1_000_000  # Test datum, not the production limit.
        self.limit = self.config["max_upload_bytes"]
        config_patch = patch.object(bunny, "load_bunny_config", return_value=self.config)
        self.config_loader = config_patch.start()
        self.addCleanup(config_patch.stop)
        self.filename = "lecture." + self.config["allowed_extensions"][0]
        client_patch = patch.object(bunny, "BunnyApiClient")
        self.client_class = client_patch.start()
        self.addCleanup(client_patch.stop)
        self.client = self.client_class.return_value
        self.client_class.from_settings.return_value = self.client
        self.client.library_id = CREATE_VIDEO_200["body"]["videoLibraryId"]
        self.client.tus_endpoint = self.config["tus_endpoint"]
        self.client.create_video.side_effect = lambda title: deepcopy(CREATE_VIDEO_200["body"])
        self.client.sign_upload.return_value = {
            "authorization_signature": "a" * 64,
            "authorization_expire": 1750000000,
        }

    def _request(self, **size_data):
        return arrange_request_mock(json.dumps({
            "file_name": self.filename,
            "file_type": "video/mp4",
            **size_data,
        }))

    def _create_upload(self, request):
        # Each call starts from an empty block, including the lower-limit retry.
        xblock = VideoXBlock(
            TestRuntime(),
            DictFieldData({"account_id": "account_id", "metadata": {}}),
            scope_ids=Mock(spec=[]),
        )
        return bunny.BunnyPlayer(xblock).create_upload(request)

    def _assert_no_bunny_calls(self):
        self.client.create_video.assert_not_called()
        self.client.sign_upload.assert_not_called()
        self.assertEqual(self.client.mock_calls, [])
        self.assertEqual(self.client_class.mock_calls, [])

    def _assert_limit_rejected(self, response, limit):
        self.assertEqual(response.status_int, 400)
        body = json.loads(response.body)
        message = body.get("error", body.get("message", ""))
        self.assertIsInstance(message, str)
        self.assertRegex(message.lower(), r"limit|maximum|maximal|exceed|ліміт|максим|перевищ")
        # Accept bytes or common human-readable units, not a particular sentence.
        units = (
            (1, r"bytes?|байт(?:и|ів)?|b"),
            (1024, r"kib|кіб|kb|кб"),
            (1024 ** 2, r"mib|міб|mb|мб"),
            (1024 ** 3, r"gib|гіб|gb|гб"),
            (1000, r"kb|кб"),
            (1000 ** 2, r"mb|мб"),
            (1000 ** 3, r"gb|гб"),
        )
        patterns = []
        for divisor, unit in units:
            magnitude = re.escape(format(limit / divisor, ".9f").rstrip("0").rstrip("."))
            patterns.append(r"(?<![\d.])" + magnitude + r"\s*(?:" + unit + r")(?!\w)")
        self.assertRegex(message.lower().replace(",", "."), "|".join(patterns))
        self._assert_no_bunny_calls()

    def test_rejects_one_byte_over_limit_before_any_bunny_call(self):
        response = self._create_upload(self._request(file_size=self.limit + 1))
        self._assert_limit_rejected(response, self.limit)

    def _assert_acceptance_depends_on_config(self, file_size):
        request = self._request(file_size=file_size)
        response = self._create_upload(request)
        self.assertEqual(response.status_int, 200)
        self.assertEqual(json.loads(response.body)["video_id"], CREATE_VIDEO_200["body"]["guid"])
        self.client.create_video.assert_called_once_with(self.filename)
        self.config_loader.assert_called_once_with(DEFAULT_CONFIG_PATH)

        self.client_class.reset_mock()
        self.client.reset_mock()
        self.config_loader.reset_mock()
        lower_limit = file_size - 1
        self.config_loader.return_value = {**self.config, "max_upload_bytes": lower_limit}
        response = self._create_upload(request)
        self._assert_limit_rejected(response, lower_limit)
        self.config_loader.assert_called_once_with(DEFAULT_CONFIG_PATH)

    def test_accepts_exact_size_limit(self):
        self._assert_acceptance_depends_on_config(self.limit)

    def test_accepts_one_byte_below_limit(self):
        self._assert_acceptance_depends_on_config(self.limit - 1)

    def test_rejects_missing_file_size_before_any_bunny_call(self):
        response = self._create_upload(self._request())
        self.assertEqual(response.status_int, 400)
        self._assert_no_bunny_calls()

    def test_rejects_nonnumeric_file_size_before_any_bunny_call(self):
        response = self._create_upload(self._request(file_size="not-a-number"))
        self.assertEqual(response.status_int, 400)
        self._assert_no_bunny_calls()
