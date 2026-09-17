# impl: FR-001-02, FR-001-04, FR-001-09, FR-001-14
"""
Versioned Bunny Stream configuration loader.

Reads ``video_xblock/bunny_config.yaml`` and enforces the hard schema and
bounds validation defined in contracts/bunny-config-contract.md §3: broken
YAML, wrong types or out-of-bounds values raise ``ValueError`` (fail-fast
instead of silent defaults). Each path is read and validated exactly once per
process, so the XBlock config is loaded on startup and reused afterwards.

The loader is fully offline. Behavioural constants live in the YAML under git
(constitution III), never in code: this module only encodes the schema bounds.
"""

import re
from pathlib import Path
from urllib.parse import urlparse

import yaml

# Схемні межі валідації (contracts/bunny-config-contract.md §3). Це межі
# схеми, а не поведінкові константи — самі значення читаються з YAML.
TOKEN_TTL_MIN = 1
TOKEN_TTL_MAX = 2592000
UPLOAD_AUTH_TTL_MIN = 3600
POLL_INTERVAL_MIN = 1
POLL_INTERVAL_MAX = 60

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
_EXTENSION_RE = re.compile(r"^[a-z0-9]+$")

_REQUIRED_KEYS = (
    "version",
    "token_ttl_seconds",
    "completion_threshold",
    "max_upload_bytes",
    "max_duration_seconds",
    "allowed_extensions",
    "upload_auth_ttl_seconds",
    "video_info_poll_interval_seconds",
    "api_base_url",
    "tus_endpoint",
    "embed_base_url",
    "changelog",
)

_URL_KEYS = ("api_base_url", "tus_endpoint", "embed_base_url")

DEFAULT_CONFIG_PATH = Path(__file__).resolve().with_name("bunny_config.yaml")

# Кеш «один раз на старті XBlock»: кожен файл конфігурації читається з диска
# і валідується лише один раз за життя процесу.
_CONFIG_CACHE = {}


def _fail(key, expected, value):
    """
    Build a ValueError describing the offending key, expected form and value.
    """
    return ValueError(
        "bunny_config: '{}' has to be {}, got {!r}".format(key, expected, value)
    )


def _is_int(value):
    """True for a genuine int (bool is rejected)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    """True for a genuine int/float (bool is rejected)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_semver(key, value):
    if not isinstance(value, str) or not _SEMVER_RE.match(value):
        raise _fail(key, "a semver string (major.minor.patch)", value)


def _validate_int_range(key, value, minimum, maximum):
    if not _is_int(value):
        raise _fail(key, "an integer", value)
    if not minimum <= value <= maximum:
        raise _fail(
            key,
            "an integer within [{}, {}]".format(minimum, maximum),
            value,
        )


def _validate_int_gt(key, value, minimum):
    if not _is_int(value):
        raise _fail(key, "an integer", value)
    if value <= minimum:
        raise _fail(key, "an integer > {}".format(minimum), value)


def _validate_completion_threshold(value):
    if not _is_number(value):
        raise _fail("completion_threshold", "a number", value)
    if not 0 < value <= 1:
        raise _fail("completion_threshold", "a number in (0, 1]", value)


def _validate_allowed_extensions(value):
    if not isinstance(value, list) or not value:
        raise _fail("allowed_extensions", "a non-empty list", value)
    for entry in value:
        if not isinstance(entry, str) or not _EXTENSION_RE.match(entry):
            raise _fail(
                "allowed_extensions",
                "a non-empty list of [a-z0-9]+ strings",
                value,
            )


def _validate_url(key, value):
    if not isinstance(value, str):
        raise _fail(key, "an https URL string", value)
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise _fail(key, "an https URL", value)


def _validate_changelog(value):
    if not isinstance(value, list) or not value:
        raise _fail("changelog", "a non-empty list", value)
    for entry in value:
        if not isinstance(entry, dict):
            raise _fail("changelog", "a list of {version, date, changes}", value)
        if "version" not in entry or "date" not in entry or "changes" not in entry:
            raise _fail(
                "changelog",
                "entries with version, date and changes",
                value,
            )
        _validate_semver("changelog.version", entry["version"])
        if not isinstance(entry["date"], str) or not entry["date"]:
            raise _fail("changelog.date", "a non-empty string", entry["date"])
        if not isinstance(entry["changes"], list) or not entry["changes"]:
            raise _fail("changelog.changes", "a non-empty list of strings", value)
        for change in entry["changes"]:
            if not isinstance(change, str):
                raise _fail(
                    "changelog.changes",
                    "a list of strings",
                    value,
                )


def _validate_config(config):
    """Validate a parsed mapping against the schema/bounds and return it."""
    if not isinstance(config, dict):
        raise ValueError(
            "bunny_config: top-level YAML value has to be a mapping, "
            "got {!r}".format(type(config).__name__)
        )

    missing = [key for key in _REQUIRED_KEYS if key not in config]
    if missing:
        raise ValueError(
            "bunny_config: missing required keys: {}".format(", ".join(missing))
        )

    _validate_semver("version", config["version"])
    _validate_int_range(
        "token_ttl_seconds",
        config["token_ttl_seconds"],
        TOKEN_TTL_MIN,
        TOKEN_TTL_MAX,
    )
    _validate_completion_threshold(config["completion_threshold"])
    _validate_int_gt("max_upload_bytes", config["max_upload_bytes"], 0)
    _validate_int_gt("max_duration_seconds", config["max_duration_seconds"], 0)
    _validate_allowed_extensions(config["allowed_extensions"])
    _validate_int_range(
        "upload_auth_ttl_seconds",
        config["upload_auth_ttl_seconds"],
        UPLOAD_AUTH_TTL_MIN,
        TOKEN_TTL_MAX,
    )
    _validate_int_range(
        "video_info_poll_interval_seconds",
        config["video_info_poll_interval_seconds"],
        POLL_INTERVAL_MIN,
        POLL_INTERVAL_MAX,
    )
    for key in _URL_KEYS:
        _validate_url(key, config[key])
    _validate_changelog(config["changelog"])

    return config


def load_bunny_config(path):
    """
    Load and validate the versioned Bunny config from ``path``.

    The result is cached: each path is read and validated only once per
    process, so the bundled config is loaded once on XBlock startup. Any
    broken YAML, wrong type or out-of-bounds value raises ``ValueError``.
    """
    key = str(path)
    cached = _CONFIG_CACHE.get(key)
    if cached is not None:
        return cached

    try:
        with open(path, encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ValueError("bunny_config: broken YAML: {}".format(exc))
    except OSError as exc:
        raise ValueError("bunny_config: cannot read {}: {}".format(path, exc))

    config = _validate_config(raw)
    _CONFIG_CACHE[key] = config
    return config
