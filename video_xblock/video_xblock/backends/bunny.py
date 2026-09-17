# impl: FR-001-05, FR-001-09
"""
Bunny Stream API client.

``BunnyApiClient`` is the single surface between the XBlock and the Bunny
Stream API (constitution IV, contracts/bunny-api.md). Every call goes through
this class, so it can be tested fully offline against the recorded fixtures in
``video_xblock/tests/fixtures/bunny/`` (constitution II) and never reaches the
network from tests.

Behavioural constants — the three base URLs (``api_base_url``,
``tus_endpoint``, ``embed_base_url``) and the token/upload TTLs — are passed in
from the versioned ``video_xblock/bunny_config.yaml`` (constitution III); the
client does not hard-code them. Secrets (library id, API key, token security
key) are read from Django settings through ``BunnyApiClient.from_settings``
and never leave the server (research R9).

Endpoint and signature rules follow contracts/bunny-api.md §1–5 and research
R6:

- Embed token:  ``sha256_hex(token_security_key + video_id + expires)``
- TUS signature: ``sha256_hex(library_id + api_key + expires + video_id)``
"""

import hashlib
import time

import requests
from django.conf import settings

from video_xblock.backends.base import BaseApiClient
from video_xblock.exceptions import ApiClientError
from video_xblock.utils import ugettext as _


class BunnyApiClientError(ApiClientError):
    """
    Bunny-specific API client error.

    Carries the HTTP status code so callers can distinguish cases that need
    special handling, e.g. a repeated DELETE of a missing video (404) which is
    an idempotent success rather than a failure (bunny-api.md §4).
    """

    default_msg = _("Bunny API error.")

    def __init__(self, detail=None, status_code=None):
        super(BunnyApiClientError, self).__init__(detail)
        self.status_code = status_code


# Bunny Stream video statuses 0-6 -> XBlock states (research R12, bunny-api.md
# §3): 0 Created, 1 Uploaded, 2 Processing, 3 Transcoding, 4 Finished,
# 5 Error, 6 UploadFailed.
_BUNNY_STATUS_MAP = {
    0: "UPLOADING",
    1: "UPLOADING",
    2: "PROCESSING",
    3: "PROCESSING",
    4: "READY",
    5: "ERROR",
    6: "ERROR",
}


def map_bunny_status(status):
    """
    Map a Bunny Stream video status code (0-6) to the XBlock state string.

    Unknown statuses are rejected loudly: they are a contract violation, not a
    value to guess at (constitution IV, research R12).
    """
    try:
        return _BUNNY_STATUS_MAP[status]
    except KeyError:
        raise BunnyApiClientError(
            _("Unknown Bunny video status: {status!r}").format(status=status)
        )


def _sha256_hex(text):
    """
    Return the lowercase hex SHA-256 digest of a UTF-8 encoded string.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# User-facing message for service-side failures (bunny-api.md §1): an invalid
# AccessKey/library (401) or an unreachable Bunny service must never surface a
# raw English API response to an author.
VIDEO_SERVICE_ERROR_MESSAGE = _("Помилка сервісу відео, спробуйте пізніше")


class BunnyApiClient(BaseApiClient):
    """
    Low-level Bunny Stream API client.

    Implements the abstract ``get``/``post`` of ``BaseApiClient`` and adds
    ``delete``, then exposes the higher-level operations used by the XBlock
    handlers: ``create_video``, ``get_video_info``, ``delete_video``,
    ``sign_upload`` and ``signed_embed_url`` (contracts/bunny-api.md §1–5).
    """

    def __init__(self, library_id, api_key, token_security_key,
                 api_base_url, tus_endpoint, embed_base_url,
                 token_ttl_seconds, upload_auth_ttl_seconds):
        self.library_id = library_id
        self.api_key = api_key
        self.token_security_key = token_security_key
        self.api_base_url = api_base_url.rstrip("/")
        self.tus_endpoint = tus_endpoint.rstrip("/")
        self.embed_base_url = embed_base_url.rstrip("/")
        self.token_ttl_seconds = token_ttl_seconds
        self.upload_auth_ttl_seconds = upload_auth_ttl_seconds

    @classmethod
    def from_settings(cls, config):
        """
        Build a client from Django settings (secrets) and the versioned YAML
        config (base URLs and TTLs).

        Secrets come from ``settings.BUNNY_STREAM_LIBRARY_ID``,
        ``settings.BUNNY_STREAM_API_KEY`` and ``settings.BUNNY_STREAM_TOKEN_KEY``
        (research R9). Importing ``django.conf.settings`` is safe outside a
        configured Django project; only calling this method touches it, which
        is why the module never reads settings at import time.
        """
        return cls(
            library_id=settings.BUNNY_STREAM_LIBRARY_ID,
            api_key=settings.BUNNY_STREAM_API_KEY,
            token_security_key=settings.BUNNY_STREAM_TOKEN_KEY,
            api_base_url=config["api_base_url"],
            tus_endpoint=config["tus_endpoint"],
            embed_base_url=config["embed_base_url"],
            token_ttl_seconds=config["token_ttl_seconds"],
            upload_auth_ttl_seconds=config["upload_auth_ttl_seconds"],
        )

    # --- low-level transport (BaseApiClient interface) ----------------------

    def _headers(self, include_content_type=False):
        """
        Bunny API request headers: AccessKey auth plus JSON accept/content type.
        """
        headers = {"AccessKey": self.api_key, "Accept": "application/json"}
        if include_content_type:
            headers["Content-Type"] = "application/json"
        return headers

    @staticmethod
    def _error_detail(method, response):
        """
        Build a human-readable message from a failed Bunny API response.
        """
        try:
            body = response.json()
        except (ValueError, AttributeError):
            body = None
        message = _("Bunny API {method} request failed with HTTP {code}").format(
            method=method, code=response.status_code
        )
        if isinstance(body, dict) and body.get("message"):
            message = "{message}: {detail}".format(message=message, detail=body["message"])
        return message

    def get(self, url, headers=None, can_retry=True):
        """
        Issue a REST GET request to a given URL (BaseApiClient interface).
        """
        headers_ = self._headers()
        if headers:
            headers_.update(headers)
        try:
            response = requests.get(url, headers=headers_)
        except requests.exceptions.RequestException:
            raise BunnyApiClientError(VIDEO_SERVICE_ERROR_MESSAGE)
        if not 200 <= response.status_code < 300:
            raise BunnyApiClientError(
                self._error_detail("GET", response),
                status_code=response.status_code,
            )
        return response.json()

    def post(self, url, payload, headers=None, can_retry=True):
        """
        Issue a REST POST request to a given URL (BaseApiClient interface).
        """
        headers_ = self._headers(include_content_type=True)
        if headers:
            headers_.update(headers)
        try:
            response = requests.post(url, json=payload, headers=headers_)
        except requests.exceptions.RequestException:
            raise BunnyApiClientError(VIDEO_SERVICE_ERROR_MESSAGE)
        if not 200 <= response.status_code < 300:
            raise BunnyApiClientError(
                self._error_detail("POST", response),
                status_code=response.status_code,
            )
        return response.json()

    def delete(self, url, headers=None, can_retry=True):
        """
        Issue a REST DELETE request to a given URL.
        """
        headers_ = self._headers()
        if headers:
            headers_.update(headers)
        try:
            response = requests.delete(url, headers=headers_)
        except requests.exceptions.RequestException:
            raise BunnyApiClientError(VIDEO_SERVICE_ERROR_MESSAGE)
        if not 200 <= response.status_code < 300:
            raise BunnyApiClientError(
                self._error_detail("DELETE", response),
                status_code=response.status_code,
            )
        return response.json()

    # --- high-level operations (bunny-api.md §1–5) --------------------------

    def _videos_url(self):
        return "{}/library/{}/videos".format(self.api_base_url, self.library_id)

    def _video_url(self, video_id):
        return "{}/{}".format(self._videos_url(), video_id)

    def create_video(self, title):
        """
        Create a video object (POST .../videos, bunny-api.md §1).

        Returns the response body; the ``guid`` field is the video id.

        A 401 (invalid AccessKey/library) is a service-side configuration
        problem: it is surfaced to the author as the friendly
        ``VIDEO_SERVICE_ERROR_MESSAGE``, never as the raw English API response
        (bunny-api.md §1).
        """
        try:
            return self.post(self._videos_url(), {"title": title})
        except BunnyApiClientError as exc:
            if exc.status_code == 401:
                raise BunnyApiClientError(
                    VIDEO_SERVICE_ERROR_MESSAGE, status_code=401
                )
            raise

    def get_video_info(self, video_id):
        """
        Fetch video info for encoding-status polling (bunny-api.md §3).

        Returns the response body with ``{guid, status, length, title}``.
        """
        return self.get(self._video_url(video_id))

    def delete_video(self, video_id):
        """
        Delete a video (DELETE .../videos/{videoId}, bunny-api.md §4).

        A 404 (already deleted) is treated as idempotent success.
        """
        try:
            return self.delete(self._video_url(video_id))
        except BunnyApiClientError as exc:
            if exc.status_code == 404:
                return True
            raise

    def sign_upload(self, video_id):
        """
        Sign a TUS upload for ``video_id`` (bunny-api.md §2, research R6).

        Returns ``{"authorization_signature": ..., "authorization_expire": ...}``
        where the signature is ``sha256_hex(library_id + api_key + expires +
        video_id)`` and ``expires = now + upload_auth_ttl_seconds``. The TTL is
        taken exclusively from the client configuration (set by
        ``from_settings`` from the versioned YAML) and cannot be overridden per
        call (constitution III).
        """
        expires = int(time.time()) + self.upload_auth_ttl_seconds
        signature = _sha256_hex(
            "{}{}{}{}".format(self.library_id, self.api_key, expires, video_id)
        )
        return {
            "authorization_signature": signature,
            "authorization_expire": expires,
        }

    def signed_embed_url(self, video_id):
        """
        Build a signed, expiring embed URL (bunny-api.md §5, research R6).

        ``token = sha256_hex(token_security_key + video_id + expires)`` and
        ``expires = now + token_ttl_seconds`` (Unix seconds, not milliseconds).
        The URL is generated per render and never stored (FR-001-09).
        """
        expires = int(time.time()) + self.token_ttl_seconds
        token = _sha256_hex(
            "{}{}{}".format(self.token_security_key, video_id, expires)
        )
        return "{}/embed/{}/{}?token={}&expires={}".format(
            self.embed_base_url, self.library_id, video_id, token, expires
        )
