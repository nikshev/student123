# verifies: FR-001-05, FR-001-09
"""
Tests for the Bunny Stream API client (`video_xblock.backends.bunny.BunnyApiClient`).

The client is the single surface between the XBlock and Bunny Stream
(constitution IV, bunny-api.md). Every call goes through ``BunnyApiClient``
(subclass of ``BaseApiClient``), so tests run fully offline against the
recorded fixtures in ``video_xblock/tests/fixtures/bunny/`` (constitution II).

Expected interface (implemented in T-007):

    BunnyApiClient(library_id, api_key, token_security_key,
                   api_base_url, embed_base_url, token_ttl_seconds)

    create_video(title)   -> dict, response body of POST .../videos (bunny-api §1)
    get_video_info(video_id) -> dict with {guid, status, length, title} (bunny-api §3)
    delete_video(video_id)   -> idempotent success, 404 treated as success (bunny-api §4)
    signed_embed_url(video_id) -> str, signed embed URL (bunny-api §5, research R6)

    map_bunny_status(status)  -> str, XBlock state for Bunny status 0-6 (research R12)

The import of ``video_xblock.backends.bunny`` is deferred so that a missing
module/class surfaces as an ImportError inside each test (the expected red of
this TDD task), not as a collection error.
"""
import hashlib
import inspect
import time
from urllib.parse import parse_qs, urlparse

import pytest
import requests
from mock import Mock, patch

from video_xblock.backends.base import BaseApiClient
from video_xblock.bunny_config import load_bunny_config
from video_xblock.exceptions import ApiClientError

from video_xblock.tests.fixtures.bunny.create_video import CREATE_VIDEO_200, CREATE_VIDEO_401
from video_xblock.tests.fixtures.bunny.delete_video import DELETE_VIDEO_200, DELETE_VIDEO_404
from video_xblock.tests.fixtures.bunny.embed import EMBED_403  # noqa: F401 (recorded for T-031)
from video_xblock.tests.fixtures.bunny.video_info import VIDEO_INFO_404, VIDEO_INFO_STATUSES


LIBRARY_ID = 759
VIDEO_ID = "32d140e2-e4f4-4eec-9d53-20371e9be607"
API_KEY = "test-api-key"
TOKEN_SECURITY_KEY = "test-token-security-key"
REQUEST_SIGNATURE = inspect.signature(requests.sessions.Session.request)


@pytest.fixture
def bunny_config(tmp_path):
    """Load a complete noncanonical config, not the bundled defaults."""
    path = tmp_path / "bunny_config.yaml"
    path.write_text(
        """version: '1.0.0'
token_ttl_seconds: 1337
completion_threshold: 0.95
max_upload_bytes: 2147483648
max_duration_seconds: 1800
allowed_extensions: [mp4, mov, webm]
upload_auth_ttl_seconds: 86400
video_info_poll_interval_seconds: 5
api_base_url: https://api.test.invalid
tus_endpoint: https://api.test.invalid/tusupload
embed_base_url: https://player.test.invalid
changelog:
  - version: '1.0.0'
    date: '2026-09-17'
    changes: ['Offline client contract test configuration']
""",
        encoding="utf-8",
    )
    return load_bunny_config(path)


@pytest.fixture(autouse=True)
def transport():
    """Intercept both requests helpers and sessions; reject unrecorded traffic."""
    with patch(
        "requests.sessions.Session.request",
        autospec=True,
        side_effect=AssertionError("Unexpected HTTP request: no recorded response"),
    ) as request, patch(
        "requests.sessions.Session.send",
        autospec=True,
        side_effect=AssertionError("Network access is forbidden"),
    ):
        yield request


def _assert_request(transport, method, url, payload=None):
    """Check the complete contract, irrespective of positional/keyword calling."""
    transport.assert_called_once()
    args, kwargs = transport.call_args
    call = REQUEST_SIGNATURE.bind(*args, **kwargs)
    call.apply_defaults()
    assert isinstance(call.arguments["self"], requests.sessions.Session)
    assert call.arguments["method"].upper() == method
    assert call.arguments["url"] == url
    headers = {"AccessKey": API_KEY, "Accept": "application/json"}
    if method == "POST":
        headers["Content-Type"] = "application/json"
    assert call.arguments["headers"] == headers
    assert call.arguments["json"] == payload
    assert call.arguments["data"] is None
    assert call.arguments["files"] is None
    assert call.arguments["params"] is None


def _client(config, **overrides):
    """
    Build a ``BunnyApiClient``, importing it lazily.

    The import is deferred so a missing ``video_xblock.backends.bunny`` surfaces
    as an ImportError inside each test (a failed test), not a collection error.
    """
    from video_xblock.backends import bunny  # noqa: PLC0415

    params = {
        "library_id": LIBRARY_ID,
        "api_key": API_KEY,
        "token_security_key": TOKEN_SECURITY_KEY,
        "api_base_url": config["api_base_url"],
        "embed_base_url": config["embed_base_url"],
        "token_ttl_seconds": config["token_ttl_seconds"],
    }
    params.update(overrides)
    return bunny.BunnyApiClient(**params)


def _response(fixture):
    """
    Build a ``requests.Response``-like mock from a recorded fixture dict.
    """
    resp = Mock()
    resp.status_code = fixture["status_code"]
    resp.ok = 200 <= resp.status_code < 300
    resp.json.return_value = fixture.get("body", {})
    return resp


def test_bunny_api_client_inherits_base_api_client():
    from video_xblock.backends import bunny  # noqa: PLC0415

    assert issubclass(bunny.BunnyApiClient, BaseApiClient)


# --- create_video (bunny-api.md §1) ---------------------------------------

def test_create_video_returns_guid(bunny_config, transport):
    """
    A successful create returns the recorded response, exposing the video guid.
    """
    client = _client(bunny_config)
    transport.side_effect = None
    transport.return_value = _response(CREATE_VIDEO_200)
    result = client.create_video("Лекція 1")

    assert result["guid"] == CREATE_VIDEO_200["body"]["guid"]
    _assert_request(
        transport, "POST",
        "{}/library/{}/videos".format(bunny_config["api_base_url"], LIBRARY_ID),
        {"title": "Лекція 1"},
    )


def test_create_video_401_raises(bunny_config, transport):
    """
    An invalid AccessKey/library (401) raises an ApiClientError.
    """
    client = _client(bunny_config)
    transport.side_effect = None
    transport.return_value = _response(CREATE_VIDEO_401)
    with pytest.raises(ApiClientError):
        client.create_video("Лекція 1")
    _assert_request(
        transport, "POST",
        "{}/library/{}/videos".format(bunny_config["api_base_url"], LIBRARY_ID),
        {"title": "Лекція 1"},
    )


# --- get_video_info (bunny-api.md §3) --------------------------------------

@pytest.mark.parametrize("status", [0, 1, 2, 3, 4, 5, 6])
def test_get_video_info_returns_status_and_length(status, bunny_config, transport):
    """
    get_video_info returns the recorded {guid, status, length, title}.
    """
    fixture = VIDEO_INFO_STATUSES[status]
    client = _client(bunny_config)
    transport.side_effect = None
    transport.return_value = _response(fixture)
    result = client.get_video_info(VIDEO_ID)

    assert result["status"] == status
    assert result["length"] == fixture["body"]["length"]
    assert result["guid"] == fixture["body"]["guid"]
    assert result["title"] == fixture["body"]["title"]
    _assert_request(
        transport, "GET",
        "{}/library/{}/videos/{}".format(
            bunny_config["api_base_url"], LIBRARY_ID, VIDEO_ID,
        ),
    )


def test_get_video_info_404_raises(bunny_config, transport):
    """
    A video deleted in Bunny (404) raises an ApiClientError.
    """
    client = _client(bunny_config)
    transport.side_effect = None
    transport.return_value = _response(VIDEO_INFO_404)
    with pytest.raises(ApiClientError):
        client.get_video_info(VIDEO_ID)
    _assert_request(
        transport, "GET",
        "{}/library/{}/videos/{}".format(
            bunny_config["api_base_url"], LIBRARY_ID, VIDEO_ID,
        ),
    )


# --- status mapping (research R12, bunny-api.md §3) ------------------------

@pytest.mark.parametrize(
    "status,expected",
    [
        (0, "UPLOADING"),
        (1, "UPLOADING"),
        (2, "PROCESSING"),
        (3, "PROCESSING"),
        (4, "READY"),
        (5, "ERROR"),
        (6, "ERROR"),
    ],
)
def test_map_bunny_status(status, expected):
    """
    Bunny statuses 0-6 map to XBlock states per the contract table.
    """
    from video_xblock.backends import bunny  # noqa: PLC0415

    assert bunny.map_bunny_status(status) == expected


# --- delete_video (bunny-api.md §4) ----------------------------------------

def test_delete_video_200_succeeds(bunny_config, transport):
    """
    A successful delete issues DELETE and returns a truthy result.
    """
    client = _client(bunny_config)
    transport.side_effect = None
    transport.return_value = _response(DELETE_VIDEO_200)
    result = client.delete_video(VIDEO_ID)

    _assert_request(
        transport, "DELETE",
        "{}/library/{}/videos/{}".format(
            bunny_config["api_base_url"], LIBRARY_ID, VIDEO_ID,
        ),
    )
    assert result


def test_delete_video_404_is_idempotent(bunny_config, transport):
    """
    Deleting an already-missing video (404) is treated as success.
    """
    client = _client(bunny_config)
    transport.side_effect = None
    transport.return_value = _response(DELETE_VIDEO_404)
    client.delete_video(VIDEO_ID)

    _assert_request(
        transport, "DELETE",
        "{}/library/{}/videos/{}".format(
            bunny_config["api_base_url"], LIBRARY_ID, VIDEO_ID,
        ),
    )


# --- signed_embed_url (bunny-api.md §5, research R6) -----------------------

def test_signed_embed_url_uses_r6_formula(bunny_config):
    """
    The embed URL carries a token per research R6:
    token = sha256_hex(token_security_key + video_id + expires), expires = now + ttl.
    """
    client = _client(bunny_config)
    before = int(time.time())
    url = client.signed_embed_url(VIDEO_ID)
    after = int(time.time())

    parsed = urlparse(url)
    embed_base = urlparse(bunny_config["embed_base_url"])
    assert parsed.scheme == embed_base.scheme
    assert parsed.netloc == embed_base.netloc
    assert parsed.path == "/embed/{}/{}".format(LIBRARY_ID, VIDEO_ID)

    query = parse_qs(parsed.query)
    token = query["token"][0]
    expires = int(query["expires"][0])

    expected_token = hashlib.sha256(
        (TOKEN_SECURITY_KEY + VIDEO_ID + str(expires)).encode("utf-8")
    ).hexdigest()
    assert token == expected_token
    ttl = bunny_config["token_ttl_seconds"]
    assert before + ttl <= expires <= after + ttl
