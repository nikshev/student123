# verifies: FR-001-09
"""
Boundary and golden-file tests for the critical signing logic of
``BunnyApiClient`` (T-007).

The signature/expiry formula (FR-001-09) is security-sensitive: a wrong token
silently opens or closes access to a protected video. These tests pin the exact
formulas from research R6 against the fixed, versioned configuration
(``bunny_config.yaml`` version 1.0.0) so that a change to either the formula or
the result-affecting constants (``token_ttl_seconds``, ``upload_auth_ttl_seconds``,
base URLs) breaks loudly instead of changing access silently (constitution III).

    embed token = sha256_hex(token_security_key + video_id + expires)
    tus signature = sha256_hex(library_id + api_key + expires + video_id)
    expires      = now + ttl        (Unix seconds, not milliseconds)

Time is frozen to a fixed epoch in the golden tests so the expected digests are
stable byte-for-byte. No test performs network I/O (constitution II).
"""
import hashlib

import pytest
from django.test.utils import override_settings
from mock import patch

from video_xblock.bunny_config import load_bunny_config
from video_xblock.exceptions import ApiClientError

VIDEO_ID = "32d140e2-e4f4-4eec-9d53-20371e9be607"
LIBRARY_ID = 759
TOKEN_SECURITY_KEY = "test-token-security-key"
API_KEY = "test-api-key"

# Frozen epoch for deterministic golden digests (does not depend on wall clock).
FIXED_NOW = 1750000000.0

# Golden snapshot of the fixed config version 1.0.0 (bunny_config.yaml): the
# result-affecting constants token_ttl_seconds / upload_auth_ttl_seconds and
# the base URLs. These literals are pinned so a silent change to either the
# formula or the versioned constants breaks the golden tests below
# (constitution III).
CANONICAL_TTL = 86400
CANONICAL_EMBED_BASE_URL = "https://player.mediadelivery.net"
CANONICAL_API_BASE_URL = "https://video.bunnycdn.com"
CANONICAL_TUS_ENDPOINT = "https://video.bunnycdn.com/tusupload"


def _sha256_hex(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _client(**overrides):
    from video_xblock.backends import bunny  # noqa: PLC0415

    params = {
        "library_id": LIBRARY_ID,
        "api_key": API_KEY,
        "token_security_key": TOKEN_SECURITY_KEY,
        "api_base_url": CANONICAL_API_BASE_URL,
        "tus_endpoint": CANONICAL_TUS_ENDPOINT,
        "embed_base_url": CANONICAL_EMBED_BASE_URL,
        "token_ttl_seconds": CANONICAL_TTL,
        "upload_auth_ttl_seconds": CANONICAL_TTL,
    }
    params.update(overrides)
    return bunny.BunnyApiClient(**params)


@pytest.fixture(autouse=True)
def no_network():
    """Reject any accidental HTTP traffic — signing is pure server-side logic."""
    with patch(
        "requests.sessions.Session.request",
        autospec=True,
        side_effect=AssertionError("Unexpected HTTP request in signing tests"),
    ), patch(
        "requests.sessions.Session.send",
        autospec=True,
        side_effect=AssertionError("Network access is forbidden"),
    ):
        yield


# --- golden-file: token formula pinned to config version 1.0.0 -------------

def test_signed_embed_url_golden_against_canonical_config():
    """
    The embed token must match the golden digest for the fixed config version
    1.0.0 (token_ttl_seconds=86400) and a frozen timestamp.
    """
    client = _client()
    expires = int(FIXED_NOW) + CANONICAL_TTL
    with patch("video_xblock.backends.bunny.time.time", return_value=FIXED_NOW):
        url = client.signed_embed_url(VIDEO_ID)

    expected_token = _sha256_hex(TOKEN_SECURITY_KEY + VIDEO_ID + str(expires))
    assert expected_token == "009403ec1291317ed45359b3b4040d368a298f5b6e60eb98e8cafca76afc864e"
    # The exact URL must be the golden one — no extra or missing parameters.
    assert url == "https://player.mediadelivery.net/embed/{}/{}?token={}&expires={}".format(
        LIBRARY_ID, VIDEO_ID, expected_token, expires
    )


def test_sign_upload_golden_tus_signature():
    """
    The TUS upload signature must match the golden digest (research R6) for a
    frozen timestamp and the canonical upload_auth_ttl_seconds.
    """
    client = _client()
    expires = int(FIXED_NOW) + CANONICAL_TTL
    with patch("video_xblock.backends.bunny.time.time", return_value=FIXED_NOW):
        result = client.sign_upload(VIDEO_ID)

    expected_signature = _sha256_hex(
        str(LIBRARY_ID) + API_KEY + str(expires) + VIDEO_ID
    )
    assert expected_signature == "aba6f568031eaedcbf6b530301c59d71913965bbbf61d896308333414e04c792"
    assert result == {
        "authorization_signature": expected_signature,
        "authorization_expire": expires,
    }


# --- boundary cases ---------------------------------------------------------

def test_signed_embed_url_expires_is_unix_seconds_not_milliseconds():
    """
    ``expires`` must be Unix seconds (10 digits), never milliseconds — the
    Bunny embed endpoint rejects a millisecond timestamp, silently locking the
    video (FR-001-09).
    """
    client = _client()
    with patch("video_xblock.backends.bunny.time.time", return_value=FIXED_NOW):
        url = client.signed_embed_url(VIDEO_ID)

    from urllib.parse import parse_qs, urlparse

    expires = int(parse_qs(urlparse(url).query)["expires"][0])
    assert expires == int(FIXED_NOW) + CANONICAL_TTL
    assert len(str(expires)) == 10


@pytest.mark.parametrize("status", [-1, 7, 99, None, "4"])
def test_map_bunny_status_rejects_unknown(status):
    """
    Unknown Bunny statuses must fail loudly rather than map to a wrong XBlock
    state (research R12 only defines 0-6).
    """
    from video_xblock.backends import bunny  # noqa: PLC0415

    with pytest.raises(ApiClientError):
        bunny.map_bunny_status(status)


def test_sign_upload_uses_configured_ttl_not_runtime_override():
    """
    ``sign_upload`` must derive the expiry solely from the client's configured
    ``upload_auth_ttl_seconds`` (loaded from the versioned YAML). A non-canonical
    TTL must therefore flow through to ``expires`` — a hard-coded TTL in the
    implementation would fail this test (constitution III).
    """
    sentinel_ttl = 3600
    client = _client(upload_auth_ttl_seconds=sentinel_ttl)
    with patch("video_xblock.backends.bunny.time.time", return_value=FIXED_NOW):
        result = client.sign_upload(VIDEO_ID)

    assert result["authorization_expire"] == int(FIXED_NOW) + sentinel_ttl
    expected_signature = _sha256_hex(
        str(LIBRARY_ID) + API_KEY + str(int(FIXED_NOW) + sentinel_ttl) + VIDEO_ID
    )
    assert result["authorization_signature"] == expected_signature


def test_sign_upload_rejects_runtime_ttl_argument():
    """
    ``sign_upload`` must not accept a per-call TTL override: the TTL comes only
    from the versioned configuration, so passing a TTL argument is a TypeError
    (constitution III, bunny-api.md §2).
    """
    client = _client()
    with pytest.raises(TypeError):
        client.sign_upload(VIDEO_ID, 3600)


# --- full chain: bunny_config.yaml -> from_settings -> BunnyApiClient --------

def test_from_settings_wires_config_and_settings(tmp_path):
    """
    End-to-end wiring test (constitution III, bunny-config-contract.md §3): a
    real YAML config (sentinel values in ``tmp_path``) plus overridden Django
    settings must flow — secrets, all three base URLs and both TTLs — into a
    ``BunnyApiClient`` built by ``from_settings``, and those YAML TTLs must
    determine the TUS signature and embed ``expires``.

    No TTL or URL is hard-coded here: everything is read back from the loaded
    config, so a client that ignores ``from_settings`` input cannot pass.
    """
    from video_xblock.backends import bunny  # noqa: PLC0415

    path = tmp_path / "bunny_config.yaml"
    path.write_text(
        """version: '1.0.0'
token_ttl_seconds: 1337
completion_threshold: 0.95
max_upload_bytes: 2147483648
max_duration_seconds: 1800
allowed_extensions: [mp4, mov, webm]
upload_auth_ttl_seconds: 43200
video_info_poll_interval_seconds: 5
api_base_url: https://api.sentinel.invalid
tus_endpoint: https://tus.sentinel.invalid
embed_base_url: https://player.sentinel.invalid
changelog:
  - version: '1.0.0'
    date: '2026-09-17'
    changes: ['Sentinel config for from_settings integration test']
""",
        encoding="utf-8",
    )
    config = load_bunny_config(path)

    library_id = "lib-sentinel-42"
    api_key = "api-key-sentinel"
    token_key = "token-key-sentinel"
    with override_settings(
        BUNNY_STREAM_LIBRARY_ID=library_id,
        BUNNY_STREAM_API_KEY=api_key,
        BUNNY_STREAM_TOKEN_KEY=token_key,
    ):
        client = bunny.BunnyApiClient.from_settings(config)

    # Secrets and all three base URLs reach the client from settings + YAML.
    assert client.library_id == library_id
    assert client.api_key == api_key
    assert client.token_security_key == token_key
    assert client.api_base_url == config["api_base_url"].rstrip("/")
    assert client.tus_endpoint == config["tus_endpoint"].rstrip("/")
    assert client.embed_base_url == config["embed_base_url"].rstrip("/")
    assert client.token_ttl_seconds == config["token_ttl_seconds"]
    assert client.upload_auth_ttl_seconds == config["upload_auth_ttl_seconds"]

    # The YAML TTLs must drive the produced signature/expires.
    with patch("video_xblock.backends.bunny.time.time", return_value=FIXED_NOW):
        upload = client.sign_upload(VIDEO_ID)
        embed_url = client.signed_embed_url(VIDEO_ID)

    upload_expires = int(FIXED_NOW) + config["upload_auth_ttl_seconds"]
    assert upload["authorization_expire"] == upload_expires
    assert upload["authorization_signature"] == _sha256_hex(
        str(library_id) + api_key + str(upload_expires) + VIDEO_ID
    )

    from urllib.parse import parse_qs, urlparse

    embed_query = parse_qs(urlparse(embed_url).query)
    embed_expires = int(FIXED_NOW) + config["token_ttl_seconds"]
    assert int(embed_query["expires"][0]) == embed_expires
    assert embed_query["token"][0] == _sha256_hex(
        token_key + VIDEO_ID + str(embed_expires)
    )
