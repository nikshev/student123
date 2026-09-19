# verifies: FR-002-11
"""
Tests for the versioned AI Tutor configuration loader (`ai_tutor_service.config`).

The loader reads `tutor_config.yaml` once and enforces the hard schema and
bounds validation defined in contracts/tutor-config-contract.md §3:

  - Exact schema: known keys accepted; unknown key → error; missing key → error;
    secret-like keys (api_key, secret, token) → error even if unknown-key
    check would already reject them.
  - SemVer `version` + non-empty `changelog` with unique ascending versions;
    version == newest changelog entry; mismatch → error.
  - Bounds:
    - daily_limit >= 1
    - conversation_ttl_days > 0
    - question_max_chars >= 1
    - 0 < request_timeout_seconds <= 30
    - generation_timeout_seconds > 0 and guard_timeout_seconds > 0
      with sum < request_timeout
    - 0 < http_connect_timeout_seconds < request_timeout
    - retrieval.top_k >= 1
    - 0 < retrieval.min_rank_score <= 1
    - 0 < guard.gate_go_threshold <= guard.gate_stop_threshold <= 1
  - Non-empty prompts (4), replies (3), model_id/guard_model_id.
  - Cost rates >= 0.
  - Startup fail-fast: loading without silent defaults — every error explicit.
"""
import copy

import pytest
import yaml


BASE_CONFIG = {
    "version": "1.0.0",
    "daily_limit": 10,
    "conversation_ttl_days": 30,
    "question_max_chars": 2000,
    "request_timeout_seconds": 30,
    "generation_timeout_seconds": 18,
    "guard_timeout_seconds": 7,
    "http_connect_timeout_seconds": 2,
    "model_id": "haiku-level-model",
    "guard_model_id": "haiku-level-guard",
    "retrieval": {
        "top_k": 5,
        "min_rank_score": 0.10,
    },
    "guard": {
        "gate_go_threshold": 0.03,
        "gate_stop_threshold": 0.10,
    },
    "prompts": {
        "tutor": "Explain the method and next step, do not give the final answer.",
        "guard": "Return only JSON with contains_solution and reason.",
        "off_topic": "Classify if the question is off-topic for this unit.",
        "no_materials": "Decide based on outline only; do not use general knowledge.",
    },
    "replies": {
        "blocked": "Допомагаю розібратися, а не розв'язую за тебе.",
        "off_topic": "Повернімося до тем цього уроку.",
        "no_materials": "У матеріалах цього уроку відповіді немає.",
    },
    "cost": {
        "currency": "USD",
        "input_per_million_tokens": 0.25,
        "output_per_million_tokens": 1.25,
    },
    "changelog": [
        {
            "version": "1.0.0",
            "date": "2026-09-19",
            "changes": ["Початкова конфігурація MVP"],
        },
    ],
}


def _loader():
    """
    Return the config loader, importing it lazily.

    The import is deferred so that a missing `ai_tutor_service.config`
    surfaces as an ImportError inside each test (a failed test), not as a
    collection error.
    """
    from ai_tutor_service.config import load_tutor_config  # noqa: PLC0415

    return load_tutor_config


def _write_config(tmp_path, overrides):
    """
    Dump BASE_CONFIG (with the given overrides) to a temp YAML file.
    """
    config = copy.deepcopy(BASE_CONFIG)
    config.update(overrides)
    path = tmp_path / "tutor_config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


# ─── Valid config ────────────────────────────────────────────────────────────

def test_valid_config_loads(tmp_path):
    """
    A canonical valid config loads and exposes the expected values.
    """
    loader = _loader()
    path = _write_config(tmp_path, {})
    config = loader(path)

    assert config["version"] == "1.0.0"
    assert config["daily_limit"] == 10
    assert config["conversation_ttl_days"] == 30
    assert config["question_max_chars"] == 2000
    assert config["request_timeout_seconds"] == 30
    assert config["generation_timeout_seconds"] == 18
    assert config["guard_timeout_seconds"] == 7
    assert config["http_connect_timeout_seconds"] == 2
    assert config["model_id"] == "haiku-level-model"
    assert config["guard_model_id"] == "haiku-level-guard"
    assert config["retrieval"]["top_k"] == 5
    assert config["retrieval"]["min_rank_score"] == 0.10
    assert config["guard"]["gate_go_threshold"] == 0.03
    assert config["guard"]["gate_stop_threshold"] == 0.10
    assert config["prompts"]["tutor"] == BASE_CONFIG["prompts"]["tutor"]
    assert config["prompts"]["guard"] == BASE_CONFIG["prompts"]["guard"]
    assert config["prompts"]["off_topic"] == BASE_CONFIG["prompts"]["off_topic"]
    assert config["prompts"]["no_materials"] == BASE_CONFIG["prompts"]["no_materials"]
    assert config["replies"]["blocked"] == BASE_CONFIG["replies"]["blocked"]
    assert config["replies"]["off_topic"] == BASE_CONFIG["replies"]["off_topic"]
    assert config["replies"]["no_materials"] == BASE_CONFIG["replies"]["no_materials"]
    assert config["cost"]["currency"] == "USD"
    assert config["cost"]["input_per_million_tokens"] == 0.25
    assert config["cost"]["output_per_million_tokens"] == 1.25
    assert len(config["changelog"]) == 1
    assert config["changelog"][0]["version"] == "1.0.0"


# ─── Unknown / missing / secret-like keys ────────────────────────────────────

def test_unknown_key_rejected(tmp_path):
    """
    Any key not in the canonical schema must be rejected.
    """
    loader = _loader()
    path = _write_config(tmp_path, {"unknown_key": "value"})
    with pytest.raises(ValueError, match="Unknown key"):
        loader(path)


@pytest.mark.parametrize("missing_key", [
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
])
def test_missing_key_rejected(tmp_path, missing_key):
    """
    Every required key must be present; missing key → error.
    """
    loader = _loader()
    config = copy.deepcopy(BASE_CONFIG)
    del config[missing_key]
    path = tmp_path / "tutor_config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError, match=f"Missing required key: {missing_key}"):
        loader(path)


@pytest.mark.parametrize("secret_key", [
    "api_key",
    "secret",
    "token",
    "api_secret",
    "access_token",
])
def test_secret_like_key_rejected(tmp_path, secret_key):
    """
    Secret-like keys must be rejected even if unknown-key check would already
    reject them. Explicit error message for secrets.
    """
    loader = _loader()
    path = _write_config(tmp_path, {secret_key: "some-secret-value"})
    with pytest.raises(ValueError, match="Secret-like key"):
        loader(path)


# ─── SemVer version + changelog ──────────────────────────────────────────────

@pytest.mark.parametrize("bad_version", [
    "not-a-version",
    "1.0",
    "1.0.0.0",
    "v1.0.0",
    "1.0.0-alpha",
    "",
])
def test_invalid_semver_rejected(tmp_path, bad_version):
    """
    version must be a valid SemVer string (major.minor.patch).
    """
    loader = _loader()
    path = _write_config(tmp_path, {"version": bad_version})
    with pytest.raises(ValueError, match="version.*SemVer|Invalid version"):
        loader(path)


def test_changelog_empty_rejected(tmp_path):
    """
    changelog must be non-empty.
    """
    loader = _loader()
    path = _write_config(tmp_path, {"changelog": []})
    with pytest.raises(ValueError, match="changelog.*non-empty|empty changelog"):
        loader(path)


@pytest.mark.parametrize("bad_entry", [
    {"version": "1.0.0"},  # missing date, changes
    {"date": "2026-09-19", "changes": ["x"]},  # missing version
    {"version": "1.0.0", "date": "2026-09-19"},  # missing changes
    {"version": "1.0.0", "changes": ["x"]},  # missing date
    {"version": "1.0.0", "date": "2026-09-19", "changes": "not a list"},  # changes not list
    {"version": "1.0.0", "date": "2026-09-19", "changes": []},  # empty changes
])
def test_changelog_entry_malformed_rejected(tmp_path, bad_entry):
    """
    Each changelog entry must have version, date, and non-empty changes list.
    """
    loader = _loader()
    path = _write_config(tmp_path, {"changelog": [bad_entry]})
    with pytest.raises(ValueError, match="changelog|entry"):
        loader(path)


def test_changelog_duplicate_versions_rejected(tmp_path):
    """
    Changelog versions must be unique.
    """
    loader = _loader()
    path = _write_config(tmp_path, {
        "changelog": [
            {"version": "1.0.0", "date": "2026-09-19", "changes": ["a"]},
            {"version": "1.0.0", "date": "2026-09-20", "changes": ["b"]},
        ],
    })
    with pytest.raises(ValueError, match="unique|duplicate"):
        loader(path)


def test_changelog_versions_not_ascending_rejected(tmp_path):
    """
    Changelog versions must be in ascending order.
    """
    loader = _loader()
    path = _write_config(tmp_path, {
        "changelog": [
            {"version": "1.1.0", "date": "2026-09-20", "changes": ["b"]},
            {"version": "1.0.0", "date": "2026-09-19", "changes": ["a"]},
        ],
    })
    with pytest.raises(ValueError, match="ascending|order"):
        loader(path)


def test_version_mismatch_changelog_rejected(tmp_path):
    """
    version must equal the newest changelog entry version.
    """
    loader = _loader()
    path = _write_config(tmp_path, {
        "version": "1.0.0",
        "changelog": [
            {"version": "1.0.1", "date": "2026-09-20", "changes": ["b"]},
        ],
    })
    with pytest.raises(ValueError, match="version.*changelog|mismatch|newest"):
        loader(path)


def test_changelog_date_format_rejected(tmp_path):
    """
    Changelog date must be a valid date string (YYYY-MM-DD).
    """
    loader = _loader()
    path = _write_config(tmp_path, {
        "changelog": [
            {"version": "1.0.0", "date": "not-a-date", "changes": ["a"]},
        ],
    })
    with pytest.raises(ValueError, match="date|invalid"):
        loader(path)


# ─── Bounds validation ───────────────────────────────────────────────────────

# daily_limit >= 1
@pytest.mark.parametrize("val", [0, -1, -100])
def test_daily_limit_non_positive_rejected(tmp_path, val):
    loader = _loader()
    path = _write_config(tmp_path, {"daily_limit": val})
    with pytest.raises(ValueError, match="daily_limit|>= 1"):
        loader(path)


def test_daily_limit_min_boundary_allowed(tmp_path):
    loader = _loader()
    path = _write_config(tmp_path, {"daily_limit": 1})
    config = loader(path)
    assert config["daily_limit"] == 1


# conversation_ttl_days > 0
@pytest.mark.parametrize("val", [0, -1, -30])
def test_conversation_ttl_non_positive_rejected(tmp_path, val):
    loader = _loader()
    path = _write_config(tmp_path, {"conversation_ttl_days": val})
    with pytest.raises(ValueError, match="conversation_ttl_days|> 0"):
        loader(path)


# question_max_chars >= 1
@pytest.mark.parametrize("val", [0, -1, -100])
def test_question_max_chars_non_positive_rejected(tmp_path, val):
    loader = _loader()
    path = _write_config(tmp_path, {"question_max_chars": val})
    with pytest.raises(ValueError, match="question_max_chars|>= 1"):
        loader(path)


# request_timeout_seconds: 0 < x <= 30
@pytest.mark.parametrize("val", [0, -1, -5, 30.1, 31, 60])
def test_request_timeout_out_of_bounds_rejected(tmp_path, val):
    loader = _loader()
    path = _write_config(tmp_path, {"request_timeout_seconds": val})
    with pytest.raises(ValueError, match="request_timeout_seconds|0 <.*<= 30"):
        loader(path)


def test_request_timeout_upper_bound_allowed(tmp_path):
    loader = _loader()
    path = _write_config(tmp_path, {"request_timeout_seconds": 30})
    config = loader(path)
    assert config["request_timeout_seconds"] == 30


def test_request_timeout_lower_bound_allowed(tmp_path):
    """
    Lower bound of request_timeout_seconds (0 < x <= 30) is allowed
    when other cross-field constraints are also satisfied:
    - generation_timeout_seconds + guard_timeout_seconds < request_timeout_seconds
    - 0 < http_connect_timeout_seconds < request_timeout_seconds
    """
    loader = _loader()
    path = _write_config(tmp_path, {
        "request_timeout_seconds": 0.1,
        "generation_timeout_seconds": 0.01,
        "guard_timeout_seconds": 0.01,
        "http_connect_timeout_seconds": 0.01,
    })
    config = loader(path)
    assert config["request_timeout_seconds"] == 0.1


# generation_timeout_seconds > 0, guard_timeout_seconds > 0, sum < request_timeout
@pytest.mark.parametrize("gen,guard", [
    (0, 7),
    (-1, 7),
    (18, 0),
    (18, -1),
    (20, 15),  # sum 35 > request_timeout 30
    (18, 13),  # sum 31 > 30
    (15, 16),  # sum 31 > 30
])
def test_generation_guard_timeouts_invalid_rejected(tmp_path, gen, guard):
    loader = _loader()
    path = _write_config(tmp_path, {
        "generation_timeout_seconds": gen,
        "guard_timeout_seconds": guard,
    })
    with pytest.raises(ValueError, match="generation_timeout_seconds|guard_timeout_seconds|sum|request_timeout"):
        loader(path)


def test_generation_guard_timeouts_valid_boundary(tmp_path):
    """
    generation + guard < request_timeout (strictly less).
    18 + 7 = 25 < 30 ✓
    """
    loader = _loader()
    path = _write_config(tmp_path, {
        "generation_timeout_seconds": 18,
        "guard_timeout_seconds": 7,
        "request_timeout_seconds": 30,
    })
    config = loader(path)
    assert config["generation_timeout_seconds"] == 18
    assert config["guard_timeout_seconds"] == 7


def test_generation_guard_sum_equals_request_timeout_rejected(tmp_path):
    """
    Sum must be strictly less than request_timeout.
    """
    loader = _loader()
    path = _write_config(tmp_path, {
        "generation_timeout_seconds": 15,
        "guard_timeout_seconds": 15,
        "request_timeout_seconds": 30,
    })
    with pytest.raises(ValueError, match="sum|request_timeout"):
        loader(path)


# http_connect_timeout_seconds: 0 < x < request_timeout
@pytest.mark.parametrize("val", [0, -1, -2, 30, 30.1, 31])
def test_http_connect_timeout_out_of_bounds_rejected(tmp_path, val):
    loader = _loader()
    path = _write_config(tmp_path, {"http_connect_timeout_seconds": val})
    with pytest.raises(ValueError, match="http_connect_timeout_seconds|0 <.*< request_timeout"):
        loader(path)


def test_http_connect_timeout_valid_boundary(tmp_path):
    """
    0 < connect_timeout < request_timeout.
    """
    loader = _loader()
    path = _write_config(tmp_path, {
        "http_connect_timeout_seconds": 2,
        "request_timeout_seconds": 30,
    })
    config = loader(path)
    assert config["http_connect_timeout_seconds"] == 2


# retrieval.top_k >= 1
@pytest.mark.parametrize("val", [0, -1, -5])
def test_retrieval_top_k_non_positive_rejected(tmp_path, val):
    loader = _loader()
    path = _write_config(tmp_path, {"retrieval": {"top_k": val, "min_rank_score": 0.10}})
    with pytest.raises(ValueError, match="top_k|>= 1"):
        loader(path)


# retrieval.min_rank_score: 0 < x <= 1
@pytest.mark.parametrize("val", [0, -0.1, -1, 1.1, 2])
def test_retrieval_min_rank_score_out_of_bounds_rejected(tmp_path, val):
    loader = _loader()
    path = _write_config(tmp_path, {"retrieval": {"top_k": 5, "min_rank_score": val}})
    with pytest.raises(ValueError, match="min_rank_score|0 <.*<= 1"):
        loader(path)


def test_retrieval_min_rank_score_upper_bound_allowed(tmp_path):
    loader = _loader()
    path = _write_config(tmp_path, {"retrieval": {"top_k": 5, "min_rank_score": 1.0}})
    config = loader(path)
    assert config["retrieval"]["min_rank_score"] == 1.0


# guard.gate_go_threshold and gate_stop_threshold: 0 < go <= stop <= 1
@pytest.mark.parametrize("go,stop", [
    (0, 0.10),
    (-0.01, 0.10),
    (0.03, 0),
    (0.03, -0.01),
    (0.03, 1.1),
    (1.1, 1.1),
    (0.10, 0.03),  # go > stop
    (0.50, 0.40),  # go > stop
])
def test_guard_thresholds_invalid_rejected(tmp_path, go, stop):
    loader = _loader()
    path = _write_config(tmp_path, {
        "guard": {"gate_go_threshold": go, "gate_stop_threshold": stop}
    })
    with pytest.raises(ValueError, match="gate_go_threshold|gate_stop_threshold|go.*stop|0 <.*<= 1"):
        loader(path)


def test_guard_thresholds_canonical_allowed(tmp_path):
    """
    Canonical values 0.03 / 0.10 must be valid.
    """
    loader = _loader()
    path = _write_config(tmp_path, {
        "guard": {"gate_go_threshold": 0.03, "gate_stop_threshold": 0.10}
    })
    config = loader(path)
    assert config["guard"]["gate_go_threshold"] == 0.03
    assert config["guard"]["gate_stop_threshold"] == 0.10


def test_guard_thresholds_go_equals_stop_allowed(tmp_path):
    """
    go == stop is allowed (0 < go <= stop <= 1).
    """
    loader = _loader()
    path = _write_config(tmp_path, {
        "guard": {"gate_go_threshold": 0.05, "gate_stop_threshold": 0.05}
    })
    config = loader(path)
    assert config["guard"]["gate_go_threshold"] == 0.05
    assert config["guard"]["gate_stop_threshold"] == 0.05


# ─── Non-empty strings ───────────────────────────────────────────────────────

@pytest.mark.parametrize("prompt_key", ["tutor", "guard", "off_topic", "no_materials"])
def test_prompts_non_empty_required(tmp_path, prompt_key):
    """
    All four prompts must be non-empty strings.
    """
    loader = _loader()
    overrides = {"prompts": {prompt_key: ""}}
    path = _write_config(tmp_path, overrides)
    with pytest.raises(ValueError, match=f"prompts\\.{prompt_key}|non-empty"):
        loader(path)


@pytest.mark.parametrize("prompt_key", ["tutor", "guard", "off_topic", "no_materials"])
def test_prompts_missing_key_rejected(tmp_path, prompt_key):
    loader = _loader()
    config = copy.deepcopy(BASE_CONFIG)
    del config["prompts"][prompt_key]
    path = tmp_path / "tutor_config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError, match=f"prompts\\.{prompt_key}|Missing"):
        loader(path)


@pytest.mark.parametrize("reply_key", ["blocked", "off_topic", "no_materials"])
def test_replies_non_empty_required(tmp_path, reply_key):
    loader = _loader()
    overrides = {"replies": {reply_key: ""}}
    path = _write_config(tmp_path, overrides)
    with pytest.raises(ValueError, match=f"replies\\.{reply_key}|non-empty"):
        loader(path)


@pytest.mark.parametrize("reply_key", ["blocked", "off_topic", "no_materials"])
def test_replies_missing_key_rejected(tmp_path, reply_key):
    loader = _loader()
    config = copy.deepcopy(BASE_CONFIG)
    del config["replies"][reply_key]
    path = tmp_path / "tutor_config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError, match=f"replies\\.{reply_key}|Missing"):
        loader(path)


def test_model_id_non_empty_required(tmp_path):
    loader = _loader()
    path = _write_config(tmp_path, {"model_id": ""})
    with pytest.raises(ValueError, match="model_id|non-empty"):
        loader(path)


def test_guard_model_id_non_empty_required(tmp_path):
    loader = _loader()
    path = _write_config(tmp_path, {"guard_model_id": ""})
    with pytest.raises(ValueError, match="guard_model_id|non-empty"):
        loader(path)


# ─── Cost rates >= 0 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("rate_key", ["input_per_million_tokens", "output_per_million_tokens"])
@pytest.mark.parametrize("val", [-0.01, -1, -100])
def test_cost_rates_negative_rejected(tmp_path, rate_key, val):
    loader = _loader()
    overrides = {"cost": {rate_key: val}}
    path = _write_config(tmp_path, overrides)
    with pytest.raises(ValueError, match=f"cost\\.{rate_key}|>= 0"):
        loader(path)


def test_cost_currency_must_be_usd(tmp_path):
    loader = _loader()
    path = _write_config(tmp_path, {"cost": {"currency": "EUR", "input_per_million_tokens": 0.25, "output_per_million_tokens": 1.25}})
    with pytest.raises(ValueError, match="currency|USD"):
        loader(path)


# ─── Broken YAML / wrong types ───────────────────────────────────────────────

def test_broken_yaml_fails_fast(tmp_path):
    """
    Malformed YAML must be rejected with a clear error instead of defaults.
    """
    loader = _loader()
    path = tmp_path / "tutor_config.yaml"
    path.write_text("version: [unclosed\n  bad", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML|yaml|parse"):
        loader(path)


@pytest.mark.parametrize("overrides", [
    {"version": 123},  # not string
    {"daily_limit": "10"},  # not int
    {"conversation_ttl_days": "30"},  # not int
    {"question_max_chars": "2000"},  # not int
    {"request_timeout_seconds": "30"},  # not number
    {"generation_timeout_seconds": "18"},  # not number
    {"guard_timeout_seconds": "7"},  # not number
    {"http_connect_timeout_seconds": "2"},  # not number
    {"model_id": 123},  # not string
    {"guard_model_id": 123},  # not string
    {"retrieval": {"top_k": "5", "min_rank_score": 0.10}},  # top_k not int
    {"retrieval": {"top_k": 5, "min_rank_score": "0.10"}},  # min_rank_score not number
    {"guard": {"gate_go_threshold": "0.03", "gate_stop_threshold": 0.10}},  # not number
    {"guard": {"gate_go_threshold": 0.03, "gate_stop_threshold": "0.10"}},  # not number
    {"prompts": {"tutor": 123, "guard": "x", "off_topic": "x", "no_materials": "x"}},  # not string
    {"replies": {"blocked": 123, "off_topic": "x", "no_materials": "x"}},  # not string
    {"cost": {"currency": "USD", "input_per_million_tokens": "0.25", "output_per_million_tokens": 1.25}},  # not number
    {"cost": {"currency": "USD", "input_per_million_tokens": 0.25, "output_per_million_tokens": "1.25"}},  # not number
    {"changelog": "not a list"},  # not list
])
def test_wrong_types_rejected(tmp_path, overrides):
    """
    Wrong types must be rejected (fail-fast instead of silent defaults).
    """
    loader = _loader()
    path = _write_config(tmp_path, overrides)
    with pytest.raises(ValueError):
        loader(path)


# ─── Nested missing keys ─────────────────────────────────────────────────────

@pytest.mark.parametrize("nested_key", [
    "retrieval.top_k",
    "retrieval.min_rank_score",
    "guard.gate_go_threshold",
    "guard.gate_stop_threshold",
])
def test_nested_missing_key_rejected(tmp_path, nested_key):
    """
    Missing nested keys must be rejected.
    """
    loader = _loader()
    config = copy.deepcopy(BASE_CONFIG)
    parent, child = nested_key.split(".")
    del config[parent][child]
    path = tmp_path / "tutor_config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError, match=f"{nested_key}|Missing"):
        loader(path)


# ─── Extra: changelog version format validation ──────────────────────────────

def test_changelog_version_must_be_semver(tmp_path):
    """
    Changelog entry versions must also be valid SemVer.
    """
    loader = _loader()
    path = _write_config(tmp_path, {
        "changelog": [
            {"version": "not-semver", "date": "2026-09-19", "changes": ["x"]},
        ],
    })
    with pytest.raises(ValueError, match="version.*SemVer|Invalid version"):
        loader(path)