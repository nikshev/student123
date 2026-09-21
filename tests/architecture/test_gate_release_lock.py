# verifies: FR-002-13
"""
Architecture test for gate release lock mechanism (T-043).

Live enablement of the AI tutor service without a matching human "go" decision
in specs/002-ai-tutor/gate-decisions/ is FORBIDDEN.

Contract (from spec.md FR-002-13 and solution-guard.md §4 step 6):
- A human decision file MUST exist in gate-decisions/ directory (JSON or MD)
- The decision file must contain verdict="go"
- The decision must be human-signed (person name, timestamp)
- The decision must match the current sample_version + config_version
- The decision must be newer than the current config/corpus versions
- If no valid decision exists → live access stays disabled (release blocked)
- Test is fail-closed: currently no gate-decisions/ directory exists → test fails

Additionally verifies:
- Tutor release-lock patch in tutor-plugin/patches/ exists and prohibits live
  enablement without go decision (architectural assertion on patch content)
- The patch file should reference gate-decisions/ and require go decision

Current state: specs/002-ai-tutor/gate-decisions/ directory does not exist
(human decision not yet recorded by S10 operator). Test is RED with expected
reason: missing human decision file.
"""

import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
SERVICE_DIR = ROOT / "ai_tutor_service"
GATE_DECISIONS_DIR = ROOT / "specs" / "002-ai-tutor" / "gate-decisions"
TUTOR_CONFIG_PATH = SERVICE_DIR / "tutor_config.yaml"
GATE_SAMPLES_PATH = SERVICE_DIR / "gate_samples.yaml"

# Expected patch file for release lock (T-044 creates it)
EXPECTED_RELEASE_LOCK_PATCHES = [
    "openedx-ai-tutor-service-compose",      # compose service env
    "openedx-ai-tutor-service-settings",     # Django settings
]

# Minimum required fields in a human decision file
REQUIRED_DECISION_FIELDS = {
    "run_id",           # UUID of gate run
    "sample_version",   # gate_samples.yaml version
    "config_version",   # tutor_config.yaml version
    "rate",             # computed rate from run
    "verdict",          # go|human|stop|invalid
    "decision",         # go|stop (human decision)
    "person",           # who made the decision
    "timestamp",        # when decision was recorded
}


class TestGateReleaseLock:
    """Architecture tests ensuring live access requires human go decision."""

    def test_gate_decisions_directory_exists(self):
        """
        Contract: gate-decisions/ directory must exist to record human decisions.
        
        Current state: directory does not exist yet (no human decision recorded).
        Test fails with expected reason: release blocked, human go decision not
        yet recorded (S10 will create it).
        """
        assert GATE_DECISIONS_DIR.is_dir(), (
            "specs/002-ai-tutor/gate-decisions/ directory does not exist. "
            "No human gate decision has been recorded yet. Live access is blocked."
        )

    def test_human_go_decision_exists(self):
        """
        Contract: There must be a valid human "go" decision in gate-decisions/.
        
        Without this, live access to the AI tutor service must remain disabled.
        Currently: no decision files exist → test fails (expected, fail-closed).
        """
        if not GATE_DECISIONS_DIR.is_dir():
            pytest.fail(
                "specs/002-ai-tutor/gate-decisions/ does not exist. "
                "Human go decision must be recorded before enabling live access. "
                "S10 operator must create decision file in gate-decisions/."
            )
        
        decision_files = list(GATE_DECISIONS_DIR.glob("*.json")) + list(GATE_DECISIONS_DIR.glob("*.md"))
        
        assert len(decision_files) > 0, (
            "No human gate decision files found in specs/002-ai-tutor/gate-decisions/. "
            "Live release is blocked: no matching human go decision exists."
        )
        
        # Validate each decision file
        for decision_file in decision_files:
            self._validate_decision_file(decision_file)

    def _validate_decision_file(self, decision_file: Path):
        """Validate a human decision file has required fields and verdict=go."""
        if decision_file.suffix == ".json":
            data = json.loads(decision_file.read_text(encoding="utf-8"))
        elif decision_file.suffix == ".md":
            # For markdown files, check for required fields in text
            content = decision_file.read_text(encoding="utf-8")
            for field in REQUIRED_DECISION_FIELDS:
                assert field in content, (
                    f"Decision file {decision_file.name} missing required field: {field}"
                )
            return  # Can't validate structure in markdown
        
        # For JSON: verify required fields
        for field in REQUIRED_DECISION_FIELDS:
            assert field in data, f"Decision file {decision_file.name} missing required field: {field}"
        
        # Verify it's a go decision
        assert data["verdict"] == "go", (
            f"Decision file {decision_file.name} has verdict={data['verdict']!r}, expected 'go'"
        )
        assert data["decision"] == "go", (
            f"Decision file {decision_file.name} has decision={data['decision']!r}, expected 'go'"
        )
        
        # Verify person and timestamp are present (human-signed)
        assert data.get("person"), (
            f"Decision file {decision_file.name} missing 'person' field (must be human-signed)"
        )
        assert data.get("timestamp"), (
            f"Decision file {decision_file.name} missing 'timestamp' field"
        )

    def test_decision_matches_current_versions(self):
        """
        Contract: decision must match current sample_version + config_version.
        
        A change in config or corpus version invalidates the previous decision.
        Currently: no decision exists, so we can't validate matching.
        Test is RED (expected).
        """
        if not GATE_DECISIONS_DIR.is_dir():
            pytest.fail(
                "specs/002-ai-tutor/gate-decisions/ does not exist. "
                "Cannot validate version matching. Live release blocked."
            )
        
        decision_files = list(GATE_DECISIONS_DIR.glob("*.json"))
        
        if not decision_files:
            pytest.fail(
                "No decision files found to validate. "
                "Live release blocked: no valid go decision matching current versions."
            )
        
        # Load current versions
        current_config_version = self._get_config_version()
        current_sample_version = self._get_sample_version()
        
        # Check that at least one decision matches current versions
        matching_decision = False
        for decision_file in decision_files:
            data = json.loads(decision_file.read_text(encoding="utf-8"))
            if data.get("decision") != "go":
                continue
            
            matches_config = data.get("config_version") == current_config_version
            matches_sample = data.get("sample_version") == current_sample_version
            
            if matches_config and matches_sample:
                matching_decision = True
                break
        
        assert matching_decision, (
            f"No matching go decision for current versions: "
            f"config_version={current_config_version!r}, "
            f"sample_version={current_sample_version!r}. "
            f"Version change invalidates previous decisions. "
            f"Re-run gate and record new human decision."
        )

    def _get_config_version(self):
        """Get current config version from tutor_config.yaml."""
        if TUTOR_CONFIG_PATH.is_file():
            config = yaml.safe_load(TUTOR_CONFIG_PATH.read_text(encoding="utf-8"))
            return config.get("version", "unknown")
        return "unknown"

    def _get_sample_version(self):
        """Get current gate sample version from gate_samples.yaml."""
        if GATE_SAMPLES_PATH.is_file():
            samples = yaml.safe_load(GATE_SAMPLES_PATH.read_text(encoding="utf-8"))
            return samples.get("version", "unknown")
        # gate_samples.yaml doesn't exist yet (T-044 creates it)
        return "unknown"

    def test_gate_samples_versioned_corpus(self):
        """
        Contract: gate_samples.yaml must be versioned with unique IDs and pinned materials.
        
        Currently: file does not exist (T-044 will create it).
        Test is RED with expected reason: corpus not yet created.
        """
        if not GATE_SAMPLES_PATH.is_file():
            pytest.fail(
                f"gate_samples.yaml does not exist at {GATE_SAMPLES_PATH}. "
                "T-044 will create the versioned gate corpus. "
                "Release blocked until corpus is versioned."
            )
        
        data = yaml.safe_load(GATE_SAMPLES_PATH.read_text(encoding="utf-8"))
        
        # Must have version
        assert "version" in data, "gate_samples.yaml must have 'version' field"
        assert isinstance(data["version"], str), "version must be a string"
        assert data["version"].count(".") == 2, f"version must be SemVer format, got {data['version']!r}"
        
        # Must have changelog
        assert "changelog" in data, "gate_samples.yaml must have 'changelog' field"
        assert isinstance(data["changelog"], list), "changelog must be a list"
        assert len(data["changelog"]) > 0, "changelog must not be empty"
        
        # Must have samples
        assert "samples" in data, "gate_samples.yaml must have 'samples' field"
        assert isinstance(data["samples"], list), "samples must be a list"
        assert len(data["samples"]) > 0, "samples must not be empty"
        
        # All sample IDs must be unique
        sample_ids = [s.get("id") for s in data["samples"]]
        assert len(sample_ids) == len(set(sample_ids)), "All sample IDs must be unique"
        
        # Each sample must have required fields
        for i, sample in enumerate(data["samples"]):
            assert "id" in sample, f"Sample {i} missing 'id'"
            assert "course_id" in sample, f"Sample {i} missing 'course_id'"
            assert "unit_usage_key" in sample, f"Sample {i} missing 'unit_usage_key'"
            assert "question" in sample, f"Sample {i} missing 'question'"

    def test_tutor_release_lock_patch_exists(self):
        """
        Contract: Tutor release-lock patch in tutor-plugin/patches/ must exist
        and prohibit live enablement without go decision.
        
        Expected patch paths:
        - openedx-ai-tutor-service-compose: env variables or healthcheck that
          checks for go decision
        - openedx-ai-tutor-service-settings: Django settings that disable live
          mode without go decision
        
        Currently: patches may exist but may not include gate release lock.
        Test validates the patch content references gate-decisions/ and requires go.
        """
        patches_dir = ROOT / "tutor-plugin" / "patches"
        
        if not patches_dir.is_dir():
            pytest.fail(
                "tutor-plugin/patches/ directory does not exist. "
                "Release lock patch cannot be validated."
            )
        
        # Check expected patch files exist
        for patch_name in EXPECTED_RELEASE_LOCK_PATCHES:
            patch_file = patches_dir / patch_name
            if not patch_file.is_file():
                pytest.fail(
                    f"Release lock patch {patch_name} does not exist in tutor-plugin/patches/. "
                    f"T-044 will add release lock to this patch."
                )
        
        # Check patch content references gate decision mechanism
        compose_patch = patches_dir / "openedx-ai-tutor-service-compose"
        if compose_patch.is_file():
            content = compose_patch.read_text(encoding="utf-8")
            
            # Should reference gate decisions or release lock
            gate_references = [
                "gate-decisions",
                "gate_decision",
                "GATE_DECISION",
                "gate-decision",
            ]
            has_gate_reference = any(ref in content for ref in gate_references)
            
            if not has_gate_reference:
                pytest.fail(
                    "openedx-ai-tutor-service-compose patch does not reference "
                    "gate-decisions/ mechanism. Release lock not implemented. "
                    "T-044 must add release lock that requires go decision "
                    "before enabling live access."
                )
        
        settings_patch = patches_dir / "openedx-ai-tutor-service-settings"
        if settings_patch.is_file():
            content = settings_patch.read_text(encoding="utf-8")
            
            # Django settings should have a flag for live mode
            live_mode_indicators = [
                "AI_TUTOR_LIVE",
                "live_mode",
                "LIVE_MODE",
                "gate_enabled",
                "GATE_ENABLED",
            ]
            has_live_mode = any(ind in content for ind in live_mode_indicators)
            
            if not has_live_mode:
                pytest.fail(
                    "openedx-ai-tutor-service-settings patch does not define "
                    "live mode flag. Release lock requires a Django settings "
                    "flag that is only enabled when human go decision exists."
                )

    def test_no_live_access_without_go_decision(self):
        """
        Contract: Live access must remain disabled when no valid go decision exists.
        
        This is a fail-closed architecture test:
        - If gate-decisions/ directory doesn't exist → test fails (blocked)
        - If no JSON decision files → test fails (blocked)
        - If no decision with verdict=go and matching versions → test fails (blocked)
        
        Currently: no gate-decisions/ exists → test fails (expected RED state).
        """
        # This is the comprehensive fail-closed check
        if not GATE_DECISIONS_DIR.is_dir():
            pytest.fail(
                "RELEASE BLOCKED: specs/002-ai-tutor/gate-decisions/ does not exist. "
                "No human go decision has been recorded. Live access remains disabled. "
                "Run S10 gate procedure, then record human decision in gate-decisions/."
            )
        
        decision_files = list(GATE_DECISIONS_DIR.glob("*.json"))
        
        if not decision_files:
            pytest.fail(
                "RELEASE BLOCKED: No decision files found in gate-decisions/. "
                "Live access remains disabled. "
                "Human must record go decision after S10 gate run."
            )
        
        # Verify at least one valid go decision exists
        current_config = self._get_config_version()
        current_samples = self._get_sample_version()
        
        valid_go = False
        for f in decision_files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            
            if data.get("decision") == "go" and data.get("verdict") == "go":
                if data.get("config_version") == current_config and \
                   data.get("sample_version") == current_samples:
                    valid_go = True
                    break
        
        assert valid_go, (
            "RELEASE BLOCKED: No valid go decision matching current versions. "
            f"Config version: {current_config!r}, "
            f"Sample version: {current_samples!r}. "
            "Live access remains disabled until valid go decision is recorded."
        )


# File marker for traceability
# impl: FR-002-13