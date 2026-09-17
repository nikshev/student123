# verifies: FR-001-02, FR-001-04, FR-001-09, FR-001-14
"""
Tests for the versioned Bunny configuration loader (`video_xblock.bunny_config`).

The loader reads `bunny_config.yaml` once and enforces the hard schema and
bounds validation defined in contracts/bunny-config-contract.md §3:

  - 0 < completion_threshold <= 1
  - token_ttl_seconds > 0
  - max_upload_bytes > 0
  - allowed_extensions non-empty (entries matching [a-z0-9]+)
  - URLs must be https
  - fail-fast on broken YAML / wrong types
"""
import copy

import pytest
import yaml


BASE_CONFIG = {
    "version": "1.0.0",
    "token_ttl_seconds": 86400,
    "completion_threshold": 0.95,
    "max_upload_bytes": 2147483648,
    "max_duration_seconds": 1800,
    "allowed_extensions": ["mp4", "mov", "webm"],
    "upload_auth_ttl_seconds": 86400,
    "video_info_poll_interval_seconds": 5,
    "api_base_url": "https://video.bunnycdn.com",
    "tus_endpoint": "https://video.bunnycdn.com/tusupload",
    "embed_base_url": "https://player.mediadelivery.net",
    "changelog": [
        {
            "version": "1.0.0",
            "date": "2026-09-17",
            "changes": ["Initial values"],
        },
    ],
}


def _loader():
    """
    Return the config loader, importing it lazily.

    The import is deferred so that a missing `video_xblock.bunny_config`
    surfaces as an ImportError inside each test (a failed test), not as a
    collection error.
    """
    from video_xblock.bunny_config import load_bunny_config  # noqa: PLC0415

    return load_bunny_config


def _write_config(tmp_path, overrides):
    """
    Dump BASE_CONFIG (with the given overrides) to a temp YAML file.
    """
    config = copy.deepcopy(BASE_CONFIG)
    config.update(overrides)
    path = tmp_path / "bunny_config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def test_valid_config_loads(tmp_path):
    """
    A canonical valid config loads and exposes the expected values.
    """
    loader = _loader()
    path = _write_config(tmp_path, {})
    config = loader(path)

    assert config["version"] == "1.0.0"
    assert config["completion_threshold"] == 0.95
    assert config["token_ttl_seconds"] == 86400
    assert config["max_upload_bytes"] == 2147483648
    assert config["allowed_extensions"] == ["mp4", "mov", "webm"]


def test_default_config_path_loads_canonical_config():
    """
    Regression (T-005/T-008 review): the bundled canonical ``bunny_config.yaml``
    must load through ``load_bunny_config(DEFAULT_CONFIG_PATH)``.

    Guards against an unquoted changelog ``date:`` value: ``yaml.safe_load``
    parses it as ``datetime.date``, so the loader's string check raises
    ``ValueError`` and the XBlock would fail to start.
    """
    from video_xblock.bunny_config import DEFAULT_CONFIG_PATH, load_bunny_config

    config = load_bunny_config(DEFAULT_CONFIG_PATH)

    assert config["version"] == "1.0.1"
    assert config["changelog"][0]["version"] == "1.0.1"
    for entry in config["changelog"]:
        assert isinstance(entry["date"], str)
        assert entry["date"] == "2026-09-17"


@pytest.mark.parametrize(
    "overrides",
    [
        {"completion_threshold": 0},
        {"completion_threshold": -0.1},
        {"completion_threshold": 1.5},
    ],
)
def test_completion_threshold_out_of_bounds_rejected(tmp_path, overrides):
    """
    completion_threshold must satisfy 0 < x <= 1.
    """
    loader = _loader()
    path = _write_config(tmp_path, overrides)
    with pytest.raises(ValueError):
        loader(path)


def test_completion_threshold_upper_bound_allowed(tmp_path):
    """
    completion_threshold == 1.0 is the inclusive upper bound and must be valid.
    """
    loader = _loader()
    path = _write_config(tmp_path, {"completion_threshold": 1.0})
    config = loader(path)
    assert config["completion_threshold"] == 1.0


@pytest.mark.parametrize(
    "overrides",
    [
        {"token_ttl_seconds": 0},
        {"token_ttl_seconds": -1},
    ],
)
def test_token_ttl_non_positive_rejected(tmp_path, overrides):
    """
    token_ttl_seconds must be > 0.
    """
    loader = _loader()
    path = _write_config(tmp_path, overrides)
    with pytest.raises(ValueError):
        loader(path)


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_upload_bytes": 0},
        {"max_upload_bytes": -1},
    ],
)
def test_max_upload_bytes_non_positive_rejected(tmp_path, overrides):
    """
    max_upload_bytes must be > 0.
    """
    loader = _loader()
    path = _write_config(tmp_path, overrides)
    with pytest.raises(ValueError):
        loader(path)


@pytest.mark.parametrize(
    "overrides",
    [
        {"allowed_extensions": []},
        {"allowed_extensions": ["mp4", "MOV"]},
    ],
)
def test_allowed_extensions_invalid_rejected(tmp_path, overrides):
    """
    allowed_extensions must be non-empty and match [a-z0-9]+.
    """
    loader = _loader()
    path = _write_config(tmp_path, overrides)
    with pytest.raises(ValueError):
        loader(path)


@pytest.mark.parametrize("key", ["api_base_url", "tus_endpoint", "embed_base_url"])
def test_urls_must_be_https(tmp_path, key):
    """
    Every URL key must be an https URL.
    """
    loader = _loader()
    path = _write_config(tmp_path, {key: "http://video.bunnycdn.com"})
    with pytest.raises(ValueError):
        loader(path)


def test_url_not_a_url_rejected(tmp_path):
    """
    A non-URL value for a URL key must be rejected.
    """
    loader = _loader()
    path = _write_config(tmp_path, {"api_base_url": "not a url"})
    with pytest.raises(ValueError):
        loader(path)


def test_broken_yaml_fails_fast(tmp_path):
    """
    Malformed YAML must be rejected with a clear error instead of defaults.
    """
    loader = _loader()
    path = tmp_path / "bunny_config.yaml"
    path.write_text("version: [unclosed\n  bad", encoding="utf-8")
    with pytest.raises(ValueError):
        loader(path)


@pytest.mark.parametrize(
    "overrides",
    [
        {"completion_threshold": "0.95"},
        {"token_ttl_seconds": "86400"},
        {"max_upload_bytes": 2.5},
        {"allowed_extensions": "mp4"},
    ],
)
def test_wrong_types_rejected(tmp_path, overrides):
    """
    Wrong types must be rejected (fail-fast instead of silent defaults).
    """
    loader = _loader()
    path = _write_config(tmp_path, overrides)
    with pytest.raises(ValueError):
        loader(path)
