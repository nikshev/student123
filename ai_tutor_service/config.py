# impl: FR-002-11
"""
Versioned AI Tutor configuration loader.

Reads ``ai_tutor_service/tutor_config.yaml`` and enforces the hard schema and
bounds validation defined in contracts/tutor-config-contract.md §3: broken
YAML, wrong types or out-of-bounds values raise ``ValueError`` (fail-fast
instead of silent defaults). Each path is read and validated exactly once per
process, so the service config is loaded on startup and reused afterwards.

The loader is fully offline. Behavioural constants live in the YAML under git
(constitution III), never in code: this module only encodes the schema bounds.
"""

import re
from pathlib import Path

import yaml

# Схемні межі валідації (contracts/tutor-config-contract.md §3). Це межі
# схеми, а не поведінкові константи — самі значення читаються з YAML.
DAILY_LIMIT_MIN = 1
CONVERSATION_TTL_DAYS_MIN = 1
QUESTION_MAX_CHARS_MIN = 1
REQUEST_TIMEOUT_MAX = 30
REQUEST_TIMEOUT_MIN = 0.1  # > 0
HTTP_CONNECT_TIMEOUT_MIN = 0.1  # > 0
RETRIEVAL_TOP_K_MIN = 1
MIN_RANK_SCORE_MIN = 0.0  # exclusive
MIN_RANK_SCORE_MAX = 1.0
GATE_THRESHOLD_MIN = 0.0  # exclusive
GATE_THRESHOLD_MAX = 1.0

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")

_REQUIRED_KEYS = (
    "version",
    "daily_limit",
    "conversation_ttl_days",
    "question_max_chars",
    "request_timeout_seconds",
    "generation_timeout_seconds",
    "guard_timeout_seconds",
    "http_connect_timeout_seconds",
    "model_id",
    "guard_model_id",
    "retrieval",
    "guard",
    "prompts",
    "replies",
    "cost",
    "changelog",
)

_REQUIRED_PROMPT_KEYS = ("tutor", "guard", "off_topic", "no_materials")
_REQUIRED_REPLY_KEYS = ("blocked", "off_topic", "no_materials")
_REQUIRED_RETRIEVAL_KEYS = ("top_k", "min_rank_score")
_REQUIRED_GUARD_KEYS = ("gate_go_threshold", "gate_stop_threshold")
_REQUIRED_COST_KEYS = ("currency", "input_per_million_tokens", "output_per_million_tokens")

_SECRET_LIKE_KEYS = (
    "api_key",
    "secret",
    "token",
    "api_secret",
    "access_token",
)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().with_name("tutor_config.yaml")

# Кеш «один раз на старті»: кожен файл конфігурації читається з диска
# і валідується лише один раз за життя процесу.
_CONFIG_CACHE = {}


def _fail(key, expected, value):
    """
    Build a ValueError describing the offending key, expected form and value.
    """
    return ValueError(
        "tutor_config: '{}' has to be {}, got {!r}".format(key, expected, value)
    )


def _is_int(value):
    """True for a genuine int (bool is rejected)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    """True for a genuine int/float (bool is rejected)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_semver(key, value):
    if not isinstance(value, str) or not _SEMVER_RE.match(value):
        raise ValueError(
            "tutor_config: Invalid version '{}': must be a SemVer string (major.minor.patch)".format(value)
        )


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


def _validate_number_gt(key, value, minimum):
    if not _is_number(value):
        raise _fail(key, "a number", value)
    if value <= minimum:
        raise _fail(key, "a number > {}".format(minimum), value)


def _validate_number_range(key, value, minimum, maximum):
    if not _is_number(value):
        raise _fail(key, "a number", value)
    if not minimum < value <= maximum:
        raise _fail(
            key,
            "a number in ({}, {}]".format(minimum, maximum),
            value,
        )


def _validate_non_empty_string(key, value):
    if not isinstance(value, str) or not value:
        raise _fail(key, "a non-empty string", value)


def _validate_changelog(value):
    if not isinstance(value, list) or not value:
        raise _fail("changelog", "a non-empty list", value)
    seen_versions = set()
    prev_version = None
    for entry in value:
        if not isinstance(entry, dict):
            raise _fail("changelog", "a list of {version, date, changes}", value)
        if "version" not in entry or "date" not in entry or "changes" not in entry:
            raise _fail(
                "changelog",
                "entries with version, date and changes",
                value,
            )
        # Validate semver for changelog entry version
        entry_version = entry["version"]
        if not isinstance(entry_version, str) or not _SEMVER_RE.match(entry_version):
            raise ValueError(
                "tutor_config: Invalid version '{}': must be a SemVer string (major.minor.patch)".format(entry_version)
            )
        if entry_version in seen_versions:
            raise _fail("changelog", "unique ascending versions", value)
        seen_versions.add(entry_version)
        if prev_version is not None:
            if _parse_version(entry_version) <= _parse_version(prev_version):
                raise _fail("changelog", "ascending version order", value)
        prev_version = entry_version
        if not isinstance(entry["date"], str) or not entry["date"]:
            raise _fail("changelog.date", "a non-empty string (YYYY-MM-DD)", entry["date"])
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", entry["date"]):
            raise _fail("changelog.date", "a date string in YYYY-MM-DD format", entry["date"])
        if not isinstance(entry["changes"], list) or not entry["changes"]:
            raise _fail("changelog.changes", "a non-empty list of strings", value)
        for change in entry["changes"]:
            if not isinstance(change, str):
                raise _fail(
                    "changelog.changes",
                    "a list of strings",
                    value,
                )


def _parse_version(version_str):
    """Parse semver string to tuple for comparison."""
    return tuple(int(x) for x in version_str.split("."))


def _check_secret_like_keys(config):
    """Check for secret-like keys at any nesting level."""
    def check_dict(d, prefix=""):
        for key in d:
            full_key = f"{prefix}.{key}" if prefix else key
            if key.lower() in _SECRET_LIKE_KEYS:
                raise ValueError(
                    "tutor_config: Secret-like key '{}' is not allowed".format(full_key)
                )
            if isinstance(d[key], dict):
                check_dict(d[key], full_key)
    check_dict(config)


def _validate_config(config):
    """Validate a parsed mapping against the schema/bounds and return it."""
    if not isinstance(config, dict):
        raise ValueError(
            "tutor_config: top-level YAML value has to be a mapping, "
            "got {!r}".format(type(config).__name__)
        )

    # Check for secret-like keys first (before unknown key check)
    _check_secret_like_keys(config)

    # Check for unknown keys
    known_keys = set(_REQUIRED_KEYS)
    unknown_keys = set(config.keys()) - known_keys
    if unknown_keys:
        raise ValueError(
            "tutor_config: Unknown key: {}".format(", ".join(sorted(unknown_keys)))
        )

    missing = [key for key in _REQUIRED_KEYS if key not in config]
    if missing:
        raise ValueError(
            "tutor_config: Missing required key: {}".format(", ".join(missing))
        )

    # version
    _validate_semver("version", config["version"])

    # daily_limit >= 1
    _validate_int_range("daily_limit", config["daily_limit"], DAILY_LIMIT_MIN, 1000000)

    # conversation_ttl_days > 0
    _validate_int_gt("conversation_ttl_days", config["conversation_ttl_days"], 0)

    # question_max_chars >= 1
    _validate_int_gt("question_max_chars", config["question_max_chars"], 0)

    # request_timeout_seconds: 0 < x <= 30
    request_timeout = config["request_timeout_seconds"]
    _validate_number_range("request_timeout_seconds", request_timeout, 0, REQUEST_TIMEOUT_MAX)

    # generation_timeout_seconds > 0
    _validate_number_gt("generation_timeout_seconds", config["generation_timeout_seconds"], 0)

    # guard_timeout_seconds > 0
    _validate_number_gt("guard_timeout_seconds", config["guard_timeout_seconds"], 0)

    # sum of generation + guard < request_timeout
    gen_timeout = config["generation_timeout_seconds"]
    guard_timeout = config["guard_timeout_seconds"]
    if gen_timeout + guard_timeout >= request_timeout:
        raise _fail(
            "generation_timeout_seconds + guard_timeout_seconds",
            "sum strictly less than request_timeout_seconds ({})".format(request_timeout),
            gen_timeout + guard_timeout,
        )

    # http_connect_timeout_seconds: 0 < x < request_timeout
    http_connect_timeout = config["http_connect_timeout_seconds"]
    _validate_number_gt("http_connect_timeout_seconds", http_connect_timeout, 0)
    if http_connect_timeout >= request_timeout:
        raise _fail(
            "http_connect_timeout_seconds",
            "a number < request_timeout_seconds ({})".format(request_timeout),
            http_connect_timeout,
        )

    # model_id, guard_model_id: non-empty strings
    _validate_non_empty_string("model_id", config["model_id"])
    _validate_non_empty_string("guard_model_id", config["guard_model_id"])

    # retrieval
    retrieval = config["retrieval"]
    if not isinstance(retrieval, dict):
        raise _fail("retrieval", "a mapping", retrieval)
    # Validate present keys first
    if "top_k" in retrieval:
        _validate_int_gt("retrieval.top_k", retrieval["top_k"], 0)
    if "min_rank_score" in retrieval:
        _validate_number_range("retrieval.min_rank_score", retrieval["min_rank_score"], 0, 1.0)
    # Then check for missing keys
    for key in _REQUIRED_RETRIEVAL_KEYS:
        if key not in retrieval:
            raise ValueError("tutor_config: missing required key: retrieval.{}".format(key))

    # guard
    guard = config["guard"]
    if not isinstance(guard, dict):
        raise _fail("guard", "a mapping", guard)
    # Validate present keys first
    if "gate_go_threshold" in guard:
        _validate_number_range("guard.gate_go_threshold", guard["gate_go_threshold"], 0, 1.0)
    if "gate_stop_threshold" in guard:
        _validate_number_range("guard.gate_stop_threshold", guard["gate_stop_threshold"], 0, 1.0)
    if "gate_go_threshold" in guard and "gate_stop_threshold" in guard:
        go_threshold = guard["gate_go_threshold"]
        stop_threshold = guard["gate_stop_threshold"]
        if go_threshold > stop_threshold:
            raise _fail(
                "guard.gate_go_threshold",
                "a number <= guard.gate_stop_threshold ({})".format(stop_threshold),
                go_threshold,
            )
    # Then check for missing keys
    for key in _REQUIRED_GUARD_KEYS:
        if key not in guard:
            raise ValueError("tutor_config: missing required key: guard.{}".format(key))

    # prompts
    prompts = config["prompts"]
    if not isinstance(prompts, dict):
        raise _fail("prompts", "a mapping", prompts)
    # Validate present keys first
    for key in _REQUIRED_PROMPT_KEYS:
        if key in prompts:
            _validate_non_empty_string("prompts.{}".format(key), prompts[key])
    # Then check for missing keys
    for key in _REQUIRED_PROMPT_KEYS:
        if key not in prompts:
            raise ValueError("tutor_config: missing required key: prompts.{}".format(key))

    # replies
    replies = config["replies"]
    if not isinstance(replies, dict):
        raise _fail("replies", "a mapping", replies)
    # Validate present keys first
    for key in _REQUIRED_REPLY_KEYS:
        if key in replies:
            _validate_non_empty_string("replies.{}".format(key), replies[key])
    # Then check for missing keys
    for key in _REQUIRED_REPLY_KEYS:
        if key not in replies:
            raise ValueError("tutor_config: missing required key: replies.{}".format(key))

    # cost
    cost = config["cost"]
    if not isinstance(cost, dict):
        raise _fail("cost", "a mapping", cost)
    # Validate present keys first
    if "currency" in cost and cost["currency"] != "USD":
        raise _fail("cost.currency", "USD", cost["currency"])
    if "input_per_million_tokens" in cost:
        if not _is_number(cost["input_per_million_tokens"]):
            raise _fail("cost.input_per_million_tokens", "a number", cost["input_per_million_tokens"])
        if cost["input_per_million_tokens"] < 0:
            raise _fail("cost.input_per_million_tokens", "a number >= 0", cost["input_per_million_tokens"])
    if "output_per_million_tokens" in cost:
        if not _is_number(cost["output_per_million_tokens"]):
            raise _fail("cost.output_per_million_tokens", "a number", cost["output_per_million_tokens"])
        if cost["output_per_million_tokens"] < 0:
            raise _fail("cost.output_per_million_tokens", "a number >= 0", cost["output_per_million_tokens"])
    # Then check for missing keys
    for key in _REQUIRED_COST_KEYS:
        if key not in cost:
            raise ValueError("tutor_config: missing required key: cost.{}".format(key))

    # changelog
    _validate_changelog(config["changelog"])

    # version must equal newest changelog entry version
    newest_changelog_version = config["changelog"][-1]["version"]
    if config["version"] != newest_changelog_version:
        raise _fail(
            "version",
            "equal to newest changelog entry version ({})".format(newest_changelog_version),
            config["version"],
        )

    return config


def load_tutor_config(path):
    """
    Load and validate the versioned AI Tutor config from ``path``.

    The result is cached: each path is read and validated only once per
    process, so the bundled config is loaded once on service startup. Any
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
        raise ValueError("tutor_config: broken YAML: {}".format(exc))
    except OSError as exc:
        raise ValueError("tutor_config: cannot read {}: {}".format(path, exc))

    config = _validate_config(raw)
    _CONFIG_CACHE[key] = config
    return config