# impl: FR-001-01, FR-001-04
# impl: FR-001-10
# impl: FR-001-08
# impl: FR-001-02, FR-001-05, FR-001-09
# impl: FR-001-03, FR-001-09
# impl: FR-001-06
# impl: FR-001-16
# impl: FR-001-07
# impl: FR-001-14
# impl: FR-001-12, FR-001-13, FR-001-15
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
import os
import time

import requests
from django.conf import settings
from webob import Response
from xblock.core import XBlock
from xblock.exceptions import JsonHandlerError

from video_xblock.backends.base import BaseApiClient, BaseVideoPlayer
from video_xblock.bunny_config import DEFAULT_CONFIG_PATH, load_bunny_config
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

# Cache-buster query parameter appended to the signed embed URL so that several
# players on one page each get a unique iframe (bunny-api.md §5, player.js
# requirement). The parameter *name* is fixed by the contract; the *value* is a
# fresh random nonce computed per render in ``BunnyPlayer.player_data_setup``.
CACHE_BUSTER_PARAM = "cb"


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
        # The client owns its transport: a dedicated Session isolates it from
        # any global monkeypatch of the module-level requests.get/post/delete
        # helpers (constitution IV).
        self._session = requests.Session()

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
            response = self._session.get(url, headers=headers_)
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
            response = self._session.post(url, json=payload, headers=headers_)
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
            response = self._session.delete(url, headers=headers_)
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


class BunnyPlayer(BaseVideoPlayer):
    """Bunny backend: create an upload and store its metadata."""

    url_re = []
    basic_fields = ['display_name']
    advanced_fields = ['start_time', 'end_time', 'handout', 'download_transcript_allowed']

    def metadata_fields(self):
        """
        Return the exact set of keys stored in the metadata xblock field.

        This is the authoritative declaration of what belongs in the block for
        ``player_name='bunny'`` (data-model.md §1). The video lives only in
        Bunny Stream (FR-001-06): the server keeps metadata and no secrets, and
        the signed player URL is never stored (it is re-signed per render,
        FR-001-09). Keys not listed here — the API key, the token key, a signed
        player URL — must never survive an OLX export.
        """
        return [
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

    def studio_context(self):
        """Render stored state and versioned upload limits without API calls."""
        config = load_bunny_config(DEFAULT_CONFIG_PATH)
        return {
            'studio_tab_template': 'bunny_studio_tab.html',
            'bunny_status': self.xblock.metadata.get('bunny_status', 'EMPTY'),
            'bunny_length_seconds': self.xblock.metadata.get('bunny_length_seconds'),
            'bunny_config': {
                'max_upload_bytes': config['max_upload_bytes'],
                'allowed_extensions': config['allowed_extensions'],
            },
        }

    def player_data_setup(self, context):
        """
        Sign a fresh embed URL and expose the versioned behavioural constants.

        The student view never performs a synchronous Bunny call: the server
        signs the embed URL on every render (contracts/xblock-interface.md §1)
        through ``BunnyApiClient.signed_embed_url``, the single owner of the R6
        token formula (bunny-api.md §5). The signed URL is returned to the
        template context and is never persisted to block metadata, so a copied
        link dies once its token expires (FR-001-09).

        The TTL and completion threshold come from the versioned
        ``bunny_config.yaml`` — not from hard-coded literals (constitution III)
        — and are carried into the JS context together with ``config_version``
        so two runs on different config artefacts are never compared.

        A cache-buster parameter (fixed name ``cb``) is appended to the URL: its
        value is a fresh random nonce per render, independent of the clock, so
        several players on one page never share an iframe URL (bunny-api.md §5).
        """
        config = load_bunny_config(DEFAULT_CONFIG_PATH)
        base_context = {
            'bunny_config': {
                'completion_threshold': config['completion_threshold'],
                'token_ttl_seconds': config['token_ttl_seconds'],
            },
            'bunny_video_id': self.xblock.metadata.get('bunny_video_id'),
            'config_version': config['version'],
        }
        is_author_mode = getattr(self.xblock.runtime, 'is_author_mode', False)

        if not is_author_mode and not self._is_enrolled():
            return base_context

        # If the video is in an ERROR state, do not sign an embed URL.
        if self.xblock.metadata.get('bunny_status') == 'ERROR':
            return base_context

        client = BunnyApiClient.from_settings(config)
        video_id = base_context['bunny_video_id']

        signed_url = client.signed_embed_url(video_id)
        # Unique nonce per render, clock-independent: under a frozen clock the
        # token/expires stay identical while the ``cb`` value keeps iframes
        # unique (test_student_view.py freezes ``bunny.time.time``).
        signed_url = "{url}&{param}={nonce}".format(
            url=signed_url,
            param=CACHE_BUSTER_PARAM,
            nonce=os.urandom(8).hex(),
        )

        base_context['signed_embed_url'] = signed_url
        return base_context

    def _local_resource_url(self, path):
        """
        Return an XBlock-local static URL, with a deterministic unit-test fallback.

        Production runtimes expose ``runtime.local_resource_url`` for static
        assets. ``TestRuntime`` deliberately leaves it unimplemented, so the
        fallback is a relative URL that keeps tests offline and still names the
        resource the student view must load. Missing future assets (notably the
        T-033 Bunny bridge) are therefore referenced, not read from disk here.
        """
        try:
            return self.xblock.runtime.local_resource_url(self.xblock, path)
        except (AttributeError, NotImplementedError):
            return path

    def get_player_html(self, **context):
        """
        Render the Bunny student player iframe and its local bridge scripts.

        ``VideoXBlock.render_player`` reaches this method through the public
        student-view handler. Unlike the video.js backends, Bunny embeds the
        Stream iframe directly from the freshly signed URL produced by
        ``player_data_setup`` and loads only vendored/local JavaScript: player.js
        plus the Bunny bridge that will be implemented by T-033. No absolute
        navigation target other than the configured embed origin is introduced.
        """
        context.update(self.player_data_setup(context))
        # Add script URLs and the event-handler URL only if we have a signed
        # embed URL (i.e., the video is available and the student is enrolled).
        if 'signed_embed_url' in context:
            context.update({
                'save_event_url': self.xblock.runtime.handler_url(self.xblock, 'save_event'),
                'playerjs_url': self._local_resource_url('static/js/lib/playerjs.min.js'),
                'bunny_player_js_url': self._local_resource_url('static/js/student/bunny_player.js'),
                'bunny_unavailable_fallback_url': self._local_resource_url(
                    'static/js/student/bunny_unavailable_fallback.js'
                ),
            })
        # Render the template which handles both available and unavailable states
        html = self.render_template('bunny_student_view.html', **context)
        return Response(html.encode('utf-8'), content_type='text/html; charset=utf-8')

    @XBlock.json_handler
    def create_upload(self, data, suffix=''):  # pylint: disable=unused-argument
        """Validate the format before creating a video (contract §2.1)."""
        if not getattr(self.xblock.runtime, 'is_author_mode', False):
            raise JsonHandlerError(403, _('Доступно лише у Studio.'))
        config = load_bunny_config(DEFAULT_CONFIG_PATH)
        file_name = data.get('file_name') if isinstance(data, dict) else None
        file_type = data.get('file_type') if isinstance(data, dict) else None
        extension = (
            os.path.splitext(file_name)[1][1:].lower()
            if isinstance(file_name, str) else ''
        )
        if extension not in config['allowed_extensions']:
            raise JsonHandlerError(400, _(
                'Непідтримуваний формат файла. Дозволені формати: {formats}.'
            ).format(formats=', '.join(config['allowed_extensions'])))
        if not isinstance(file_type, str) or not file_type.startswith('video/'):
            raise JsonHandlerError(400, _(
                'Непідтримуваний тип файла. Оберіть відеофайл (MIME video/*).'
            ))

        # Recheck the declared size against YAML before contacting Bunny (R4).
        file_size = data.get('file_size')
        if not isinstance(file_size, int) or isinstance(file_size, bool):
            raise JsonHandlerError(400, _(
                'Вкажіть розмір файла (file_size) цілим числом байтів.'
            ))
        max_upload_bytes = config['max_upload_bytes']
        if file_size > max_upload_bytes:
            raise JsonHandlerError(400, _(
                'Розмір файла перевищує ліміт {limit} байт (максимум).'
            ).format(limit=max_upload_bytes))

        client = BunnyApiClient.from_settings(config)
        # Replace path (FR-001-16): a new upload over an existing video deletes
        # the old video id first, so no orphan is left in Bunny (data-model.md
        # §1). The block's own stored id is used, never a client-supplied value.
        old_video_id = self.xblock.metadata.get('bunny_video_id')
        old_video_deleted = False
        try:
            if old_video_id:
                client.delete_video(old_video_id)
                old_video_deleted = True
            created = client.create_video(file_name)
            signature = client.sign_upload(created['guid'])
        except ApiClientError as exc:
            if old_video_deleted:
                # The old object is already gone from Bunny, so the block must
                # not keep referencing the dead guid: reset to the truthful
                # EMPTY state (every metadata key cleared, status EMPTY —
                # data-model.md §1) before surfacing the error to the client.
                self._reset_bunny_metadata()
            raise JsonHandlerError(502, VIDEO_SERVICE_ERROR_MESSAGE) from exc

        self.xblock.metadata.update({
            'bunny_video_id': created['guid'],
            'bunny_library_id': client.library_id,
            'bunny_status': 'UPLOADING',
            # Replace path (FR-001-16): the new object starts UPLOADING, so any
            # duration left over from the previous READY video must be cleared
            # (data-model.md §1), and the fresh TUS signature expiry replaces
            # the stale one used by the resume logic in the UI.
            'bunny_length_seconds': None,
            'bunny_title': file_name,
            'source_type': 'bunny',
            'token_protected': True,
            'config_version': config['version'],
            'upload_signature_expires': signature['authorization_expire'],
        })
        return {
            'video_id': created['guid'],
            'library_id': client.library_id,
            'tus_endpoint': config['tus_endpoint'],
            'authorization_signature': signature['authorization_signature'],
            'authorization_expire': signature['authorization_expire'],
        }

    @XBlock.json_handler
    def upload_credentials(self, data, suffix=''):  # pylint: disable=unused-argument
        """
        Re-sign the same video id for a TUS resume (contract §2.2, research R6).

        An interrupted upload is resumed by re-signing the existing ``video_id``
        — never by creating a replacement object (FR-001-03). The signature is
        produced by ``BunnyApiClient.sign_upload``, the single owner of the R6
        TUS formula; its TTL comes from ``upload_auth_ttl_seconds`` in the
        versioned ``bunny_config.yaml`` and is never hard-coded here
        (constitution III).
        """
        if not getattr(self.xblock.runtime, 'is_author_mode', False):
            raise JsonHandlerError(403, _('Доступно лише у Studio.'))
        config = load_bunny_config(DEFAULT_CONFIG_PATH)
        video_id = data.get('video_id') if isinstance(data, dict) else None
        if not isinstance(video_id, str) or not video_id:
            raise JsonHandlerError(400, _(
                'Вкажіть video_id відео для повторного підпису.'
            ))

        # Resume may only re-sign this block's own video: the submitted
        # ``video_id`` must equal the stored ``bunny_video_id`` (FR-001-03).
        # A block that has no GUID, or a caller-supplied foreign id, is
        # rejected before any signature is produced.
        stored_video_id = self.xblock.metadata.get('bunny_video_id')
        if stored_video_id != video_id:
            raise JsonHandlerError(400, _(
                'Вказаний video_id не належить цьому блоку. '
                'Повторний підпис можливий лише для власного відео блоку.'
            ))

        client = BunnyApiClient.from_settings(config)
        signature = client.sign_upload(video_id)

        return {
            'video_id': video_id,
            'library_id': client.library_id,
            'tus_endpoint': config['tus_endpoint'],
            'authorization_signature': signature['authorization_signature'],
            'authorization_expire': signature['authorization_expire'],
        }

    @XBlock.json_handler
    def video_info(self, data, suffix=''):  # pylint: disable=unused-argument
        """
        Poll the Bunny video status and persist the mapped state (contract §2.3).

        A polling proxy for ``BunnyApiClient.get_video_info`` (bunny-api.md §3):
        it maps the raw Bunny status to the XBlock state through the explicit
        ``map_bunny_status`` function and writes the result into the block
        metadata, so the Studio tab reflects it without a page reload
        (FR-001-05). ``length`` is surfaced only for a finished (READY) video.

        Fail-safety (critical): a silent mapping/duration mistake would leave
        the block in a wrong state and let a teacher publish an unready video.
        Therefore an unknown status is mapped to ERROR — never left in the
        previous state — a 404 (video deleted in Bunny) marks the block ERROR
        with its duration cleared, and a READY video whose ``length`` exceeds
        ``max_duration_seconds`` (from the versioned YAML, constitution III) is
        deleted via the client and marked ERROR (research R4).
        """
        if not getattr(self.xblock.runtime, 'is_author_mode', False):
            raise JsonHandlerError(403, _('Доступно лише у Studio.'))
        config = load_bunny_config(DEFAULT_CONFIG_PATH)
        video_id = data.get('video_id') if isinstance(data, dict) else None
        if not isinstance(video_id, str) or not video_id:
            raise JsonHandlerError(400, _(
                'Вкажіть video_id відео для опитування стану.'
            ))

        client = BunnyApiClient.from_settings(config)
        try:
            info = client.get_video_info(video_id)
        except BunnyApiClientError as exc:
            if exc.status_code == 404:
                # Video deleted in Bunny (bunny-api.md §3): fail-safe ERROR
                # with the duration cleared, so the block never lingers in a
                # stale state a teacher might publish. No further Bunny calls
                # (no DELETE) are made for an already-gone video.
                self.xblock.metadata.update({
                    'bunny_status': 'ERROR',
                    'bunny_length_seconds': None,
                })
                return {'status': None}
            raise JsonHandlerError(502, VIDEO_SERVICE_ERROR_MESSAGE) from exc
        except ApiClientError as exc:
            raise JsonHandlerError(502, VIDEO_SERVICE_ERROR_MESSAGE) from exc

        status = info.get('status')
        # Fail-safe: an unrecognised status is a contract violation, not a
        # value to guess at; the block is marked ERROR rather than kept in a
        # stale state the teacher might publish.
        try:
            state = map_bunny_status(status)
        except BunnyApiClientError:
            state = 'ERROR'

        length = None
        if state == 'READY':
            length = info.get('length')
            if isinstance(length, bool) or not isinstance(length, (int, float)):
                # A Finished video must carry a numeric duration (bunny-api §3).
                # A missing/invalid duration is a contract violation: fail-safe
                # to ERROR instead of publishing a video without a duration.
                state = 'ERROR'
                length = None
            elif length > config['max_duration_seconds']:
                # Over the versioned duration limit: ERROR + DELETE (research R4).
                self.xblock.metadata.update({
                    'bunny_status': 'ERROR',
                    'bunny_length_seconds': None,
                })
                client.delete_video(video_id)
                raise JsonHandlerError(400, _(
                    'Відео довше за максимально допустиму тривалість '
                    '({limit} секунд) і було видалене.'
                ).format(limit=config['max_duration_seconds']))

        self.xblock.metadata.update({
            'bunny_status': state,
            'bunny_length_seconds': length,
        })

        response = {'status': status}
        if state == 'READY':
            response['length'] = length
        return response

    def _reset_bunny_metadata(self):
        """
        Clear every bunny metadata key and return the block to the EMPTY state.

        All nine keys declared by ``metadata_fields()`` (data-model.md §1) are
        dropped so no stale reference survives a delete/replace, and only
        ``bunny_status`` reads EMPTY. An incomplete cleanup — any single key
        left behind — would let the block linger in a wrong state.
        """
        reset = {field: None for field in self.metadata_fields()}
        reset['bunny_status'] = 'EMPTY'
        self.xblock.metadata.update(reset)

    def _is_enrolled(self):
        """Check if the current user is enrolled in the course.

        The production path asks Open edX's ``enrollments`` and ``user`` runtime
        services: current user id comes from ``user.opt_attrs`` and the course
        id from ``scope_ids.usage_id.context_key``.  This method is also the
        test seam for FR-001-08, so unit tests patch it without relying on
        platform services.  Any missing service, missing attribute or service
        error returns ``False`` (fail closed).
        """
        try:
            enrollments_service = self.xblock.runtime.service(self.xblock, 'enrollments')
            user_service = self.xblock.runtime.service(self.xblock, 'user')
            user = user_service.get_current_user()
            user_id = user.opt_attrs.get('edx-platform.user_id')
            course_id = self.xblock.scope_ids.usage_id.context_key

            if not enrollments_service or not user_id or not course_id:
                return False

            enrollment = enrollments_service.get_active_enrollments_by_course_and_user(
                course_id,
                user_id,
            )
            return bool(enrollment)
        except Exception:
            pass
        return False

    @XBlock.json_handler
    def delete_video(self, data, suffix=''):  # pylint: disable=unused-argument
        """
        Delete the block's video in Bunny and return the block to EMPTY (§2.4).

        The video id is taken from the block metadata, never from the request
        body (contracts/xblock-interface.md §2.4), and the DELETE goes through
        ``BunnyApiClient``. A repeated delete of an already-missing video is an
        idempotent success (bunny-api.md §4), and the block is reset to EMPTY
        regardless. A block that already has no video id is simply reset
        without any Bunny call.
        """
        if not getattr(self.xblock.runtime, 'is_author_mode', False):
            raise JsonHandlerError(403, _('Доступно лише у Studio.'))
        config = load_bunny_config(DEFAULT_CONFIG_PATH)
        video_id = self.xblock.metadata.get('bunny_video_id')
        if video_id:
            client = BunnyApiClient.from_settings(config)
            try:
                client.delete_video(video_id)
            except ApiClientError as exc:
                raise JsonHandlerError(502, VIDEO_SERVICE_ERROR_MESSAGE) from exc
        self._reset_bunny_metadata()
        return {}

    @XBlock.json_handler
    def save_event(self, data, suffix=''):  # pylint: disable=unused-argument
        """
        Publish a video player event to the Open edX tracking log (contract §2.5).

        The handler is LMS-only: Studio must not emit viewing events
        (bunny-api.md §7). The server constructs the payload from platform
        context and block metadata; the client only supplies ``event_type`` and
        ``current_time`` (tracking-events.md §1–3, xblock-interface.md §2.3).
        ``duration`` for Bunny is taken from metadata, not from the request, so
        a forged client value cannot inflate the completion metric (FR-001-13).
        """
        if getattr(self.xblock.runtime, 'is_author_mode', False):
            raise JsonHandlerError(403, _('Тільки у LMS доступно.'))

        if not isinstance(data, dict):
            raise JsonHandlerError(400, _('Неправильний формат запиту.'))

        event_type = data.get('event_type')
        if event_type not in {'play', 'pause', 'complete'}:
            raise JsonHandlerError(400, _(
                'Тип події має бути одним із: play, pause, complete.'
            ))

        current_time = data.get('current_time')
        if isinstance(current_time, bool) or not isinstance(current_time, (int, float)):
            raise JsonHandlerError(400, _('current_time має бути числом.'))

        try:
            user_service = self.xblock.runtime.service(self.xblock, 'user')
            if user_service is None:
                return {'result': 'success'}
            user = user_service.get_current_user()
            user_id = user.opt_attrs.get('edx-platform.user_id')
            if user_id is None:
                return {'result': 'success'}
        except Exception:
            return {'result': 'success'}

        config = load_bunny_config(DEFAULT_CONFIG_PATH)
        payload = {
            'user_id': user_id,
            'course_id': str(self.xblock.scope_ids.usage_id.context_key),
            'unit_usage_key': str(self.xblock.scope_ids.usage_id),
            'video_ref': {
                'source_type': 'bunny',
                'video_id': self.xblock.metadata.get('bunny_video_id'),
            },
            'event_type': event_type,
            'current_time': current_time,
            'duration': self.xblock.metadata.get('bunny_length_seconds'),
            'config_version': config['version'],
        }
        self.xblock.runtime.publish(
            self.xblock,
            'xblock-video.player.{}'.format(event_type),
            payload,
        )
        return {'result': 'success'}
