# verifies: FR-001-09, FR-001-14
"""
Student-view JS context: signed embed URL, completion threshold, config version.

T-024 (test-first). ``BunnyPlayer.player_data_setup(context)`` extends the
student-view context with the server-side signed embed URL and the versioned
behavioural constants (contracts/xblock-interface.md §1). The URL is signed per
bunny-api.md §5 (research R6):

    token   = sha256_hex(token_security_key + video_id + expires)
    expires = now + token_ttl_seconds        # Unix seconds, from YAML

The signed URL must never be persisted to block metadata — it is re-signed on
every student-view render, so a copied link dies once its token expires
(FR-001-09). The completion threshold that drives the 95 % "watched to the end"
rule (FR-001-14) is carried into the JS context from YAML, together with the
config version so two runs on different config artefacts are never compared
(constitution III).

The two tests below load configuration through a *sentinel* config returned by a
mocked ``load_bunny_config``: ``token_ttl_seconds``, ``completion_threshold`` and
``version`` are deliberately non-canonical, so a hard-coded 86400 / 0.95 / 1.0.1
anywhere in the implementation fails the test instead of accidentally passing.

To keep several Bunny players on one page unique, the embed URL carries a
cache-buster query parameter (bunny-api.md §5, player.js requirement). It is a
fresh nonce per render, independent of the clock: under a frozen ``now`` two
renders keep the same token/expires (R6) and differ only by that parameter.
"""

import hashlib
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from django.test import override_settings
from xblock.field_data import DictFieldData
from xblock.test.tools import TestRuntime

from video_xblock.backends import bunny
from video_xblock.tests.fixtures.bunny.create_video import CREATE_VIDEO_200
from video_xblock.tests.fixtures.bunny.video_info import VIDEO_INFO_STATUSES
from video_xblock.video_xblock import VideoXBlock


LIBRARY_ID = CREATE_VIDEO_200["body"]["videoLibraryId"]
VIDEO_ID = CREATE_VIDEO_200["body"]["guid"]
API_KEY_SENTINEL = "test-only-bunny-api-key-must-stay-server-side"
TOKEN_KEY_SENTINEL = "test-only-bunny-token-key-must-stay-server-side"
NOW = 1750000000

# Cache-buster query parameter appended to the signed embed URL so several
# players on one page get unique iframes (bunny-api.md §5). The implementation
# must choose this exact name.
CACHE_BUSTER_PARAM = "cb"

# Sentinel config returned by the mocked ``load_bunny_config``. The three
# behavioural values the context must expose are deliberately non-canonical, so
# a hard-coded 86400 / 0.95 / 1.0.1 cannot satisfy the assertions below.
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
    "changelog": [
        {
            "version": "9.8.7",
            "date": "2026-09-18",
            "changes": ["sentinel config for T-024 student-view context"],
        },
    ],
}


class BunnyStudentViewContextTests(unittest.TestCase):
    """``player_data_setup`` signs the embed URL and exposes YAML constants."""

    def setUp(self):
        # Network is forbidden: any live Bunny/HTTP call fails the test
        # (constitution II, fixtures only).
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
        # Serve the sentinel config: the values under test must come from here,
        # never from the canonical bundled YAML nor from hard-coded literals.
        self.enterContext(patch.object(
            bunny, "load_bunny_config", return_value=SENTINEL_CONFIG,
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
        self.player = bunny.BunnyPlayer(self.xblock)
        self._seed_ready_metadata()

    def _seed_ready_metadata(self):
        """Plant a realistic READY state so the block has a video to sign."""
        self.xblock.metadata.update({
            "bunny_video_id": VIDEO_ID,
            "bunny_library_id": LIBRARY_ID,
            "bunny_status": "READY",
            "bunny_length_seconds": VIDEO_INFO_STATUSES[4]["body"]["length"],
            "bunny_title": CREATE_VIDEO_200["body"]["title"],
            "source_type": "bunny",
            "token_protected": True,
            "config_version": self.config["version"],
        })

    def _expected_expires(self, now):
        """Reproduce the R6 expiry: now + token_ttl_seconds from the config."""
        return now + self.config["token_ttl_seconds"]

    def _expected_token(self, expires):
        """Independently reproduce the R6 token formula from bunny-api.md §5."""
        return hashlib.sha256(
            "{}{}{}".format(TOKEN_KEY_SENTINEL, VIDEO_ID, expires).encode("utf-8")
        ).hexdigest()

    def _expected_embed_base(self):
        """The unsigned portion of the embed URL, from the sentinel config."""
        return "{}/embed/{}/{}".format(
            self.config["embed_base_url"].rstrip("/"), LIBRARY_ID, VIDEO_ID,
        )

    @staticmethod
    def _split_url(url):
        """Split an embed URL into its (path-part, query-params) components."""
        parts = urlsplit(url)
        return "{scheme}://{netloc}{path}".format(
            scheme=parts.scheme, netloc=parts.netloc, path=parts.path,
        ), parse_qs(parts.query)

    def test_player_data_setup_signs_embed_url_and_exposes_yaml_context(self):
        with patch.object(bunny.time, "time", return_value=NOW):
            context = self.player.player_data_setup({})

        # The versioned constants must come from the (sentinel) config, not from
        # hard-coded 86400 / 0.95 / 1.0.1 — these asserts fail on any literal.
        self.assertEqual(context["config_version"], self.config["version"])
        self.assertEqual(
            context["bunny_config"]["completion_threshold"],
            self.config["completion_threshold"],
        )
        self.assertEqual(
            context["bunny_config"]["token_ttl_seconds"],
            self.config["token_ttl_seconds"],
        )
        self.assertEqual(context["bunny_video_id"], VIDEO_ID)

        base, params = self._split_url(context["signed_embed_url"])
        self.assertEqual(base, self._expected_embed_base())
        expires = int(params["expires"][0])
        self.assertEqual(expires, self._expected_expires(NOW))
        self.assertEqual(params["token"][0], self._expected_token(expires))
        # The signed URL is computed per render and never persisted (FR-001-09).
        self.assertNotIn("signed_embed_url", self.xblock.metadata)
        self.assertNotIn("signed_player_url", self.xblock.metadata)

    def test_player_data_setup_adds_unique_cache_buster_per_render(self):
        with patch.object(bunny.time, "time", return_value=NOW):
            first = self.player.player_data_setup({})
            second = self.player.player_data_setup({})

        first_base, first_params = self._split_url(first["signed_embed_url"])
        second_base, second_params = self._split_url(second["signed_embed_url"])
        self.assertEqual(first_base, self._expected_embed_base())
        self.assertEqual(second_base, self._expected_embed_base())

        # Under the same frozen clock the token and expiry are stable and valid
        # per the R6 formula (expires = now + token_ttl_seconds from config).
        self.assertEqual(first_params["expires"], second_params["expires"])
        self.assertEqual(first_params["token"], second_params["token"])
        expires = int(first_params["expires"][0])
        self.assertEqual(expires, self._expected_expires(NOW))
        self.assertEqual(first_params["token"][0], self._expected_token(expires))

        # The URLs differ, and only through the cache-buster parameter: several
        # players on one page must never share an iframe URL (player.js).
        self.assertNotEqual(first["signed_embed_url"], second["signed_embed_url"])
        self.assertIn(CACHE_BUSTER_PARAM, first_params)
        self.assertIn(CACHE_BUSTER_PARAM, second_params)
        self.assertNotEqual(
            first_params[CACHE_BUSTER_PARAM][0],
            second_params[CACHE_BUSTER_PARAM][0],
        )
        # A copied link dies when its token expires; neither render is stored
        # anywhere on the block (FR-001-09).
        self.assertNotIn("signed_embed_url", self.xblock.metadata)
