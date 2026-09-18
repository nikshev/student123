# verifies: FR-001-06
"""Bunny metadata contract and real XBlock OLX export, entirely offline."""

import json
import unittest
from unittest.mock import Mock, patch

import yaml
from django.test import override_settings
from lxml import etree
from xblock.field_data import DictFieldData
from xblock.test.tools import TestRuntime

from video_xblock.backends import bunny
from video_xblock.bunny_config import DEFAULT_CONFIG_PATH
from video_xblock.tests.fixtures.bunny.create_video import CREATE_VIDEO_200
from video_xblock.tests.fixtures.bunny.video_info import VIDEO_INFO_STATUSES
from video_xblock.video_xblock import VideoXBlock


EXPECTED_METADATA_FIELDS = [
    'bunny_video_id',
    'bunny_library_id',
    'bunny_status',
    'bunny_length_seconds',
    'bunny_title',
    'source_type',
    'token_protected',
    'config_version',
    'upload_signature_expires',
]
API_KEY_SENTINEL = 'test-only-bunny-api-key-must-not-be-exported'
TOKEN_KEY_SENTINEL = 'test-only-bunny-token-key-must-not-be-exported'
# Simulated leak keys: data-model.md §1 invariants 2 and 4 forbid storing the
# API key, the token key and the signed player URL in the block metadata, so
# none of them may ever survive an OLX export (FR-001-06).
LEAK_KEYS = ('api_key', 'token_key', 'signed_player_url')


class BunnyMetadataTests(unittest.TestCase):
    def setUp(self):
        for method in ('request', 'send'):
            self.enterContext(patch(
                'requests.sessions.Session.' + method,
                autospec=True,
                side_effect=AssertionError('Network access is forbidden in unit tests'),
            ))
        self.enterContext(override_settings(
            BUNNY_STREAM_LIBRARY_ID=CREATE_VIDEO_200['body']['videoLibraryId'],
            BUNNY_STREAM_API_KEY=API_KEY_SENTINEL,
            BUNNY_STREAM_TOKEN_KEY=TOKEN_KEY_SENTINEL,
        ))
        self.enterContext(patch.object(bunny.time, 'time', return_value=1750000000))
        with DEFAULT_CONFIG_PATH.open(encoding='utf-8') as config_file:
            self.config = yaml.safe_load(config_file)
        self.xblock = VideoXBlock(
            TestRuntime(),
            DictFieldData({'account_id': '', 'metadata': {}, 'player_name': 'bunny', 'href': ''}),
            scope_ids=Mock(spec=[]),
        )
        # The real exporter uses the block type as the OLX element name.
        self.xblock.scope_ids.block_type = 'video_xblock'

    def test_metadata_fields_returns_exact_ordered_keys(self):
        self.assertEqual(
            bunny.BunnyPlayer(self.xblock).metadata_fields(),
            EXPECTED_METADATA_FIELDS,
        )

    def test_ready_olx_exports_only_metadata_fields_without_secrets_or_signed_url(self):
        """OLX export of a READY video exposes only ``metadata_fields()``.

        A realistic READY state is planted together with simulated leaks — the
        API key, the token key and a signed player URL — that data-model.md §1
        (invariants 2 and 4) forbid ever leaving the server. The exported OLX
        must then contain exactly the backend-declared metadata fields and no
        more, so any OLX export stays safe to share regardless of what was
        written into the ``metadata`` field (FR-001-06).
        """
        player = bunny.BunnyPlayer(self.xblock)

        # A signed player URL (FR-001-09) accidentally stored in block metadata
        # must never survive an export. Build it via the real client so the
        # simulated leak is indistinguishable from a genuine one.
        client = bunny.BunnyApiClient.from_settings(self.config)
        signed_player_url = client.signed_embed_url(CREATE_VIDEO_200['body']['guid'])

        self.xblock.metadata.update({
            'bunny_video_id': CREATE_VIDEO_200['body']['guid'],
            'bunny_library_id': CREATE_VIDEO_200['body']['videoLibraryId'],
            'bunny_status': 'READY',
            'bunny_length_seconds': VIDEO_INFO_STATUSES[4]['body']['length'],
            'bunny_title': CREATE_VIDEO_200['body']['title'],
            'source_type': 'bunny',
            'token_protected': True,
            'config_version': self.config['version'],
            'upload_signature_expires': 1750000000 + self.config['upload_auth_ttl_seconds'],
            # Simulated leak — must be stripped on export:
            'api_key': API_KEY_SENTINEL,
            'token_key': TOKEN_KEY_SENTINEL,
            'signed_player_url': signed_player_url,
        })

        # Let the SDK call the real Dict field's to_string(), including
        # JSON/XML escaping, instead of mocking the exporter.
        node = etree.Element('unknown')
        self.xblock.add_xml_to_node(node)
        olx = etree.tostring(node, encoding='unicode')
        exported = etree.fromstring(olx.encode('utf-8'))
        self.assertEqual(exported.tag, 'video_xblock')
        self.assertEqual(exported.get('player_name'), 'bunny')
        exported_metadata = json.loads(exported.get('metadata'))

        # Invariant: only backend-declared metadata fields may be exported.
        exported_keys = set(exported_metadata)
        allowed_fields = set(player.metadata_fields())
        self.assertTrue(
            exported_keys <= allowed_fields,
            'OLX export must contain only backend-declared metadata fields; '
            'unexpected keys: {keys}'.format(
                keys=sorted(exported_keys - allowed_fields),
            ),
        )
        # The legitimate READY fields must survive the export.
        for key in EXPECTED_METADATA_FIELDS:
            self.assertIn(key, exported_metadata)
        self.assertEqual(exported_metadata['bunny_status'], 'READY')
        self.assertIsNotNone(exported_metadata['bunny_length_seconds'])
        # No secrets and no signed player URL may leak into the export.
        for key in LEAK_KEYS:
            self.assertNotIn(key, exported_metadata)
        for forbidden in (API_KEY_SENTINEL, TOKEN_KEY_SENTINEL, signed_player_url):
            self.assertNotIn(forbidden, olx)
        self.assertNotIn('token=', olx)
        self.assertNotIn('expires=', olx)
