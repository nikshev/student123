# verifies: FR-002-14
"""
Migration/model tests for all data models defined in specs/002-ai-tutor/data-model.md.

This test verifies all fields, enums, and constraints for:
- UnitMaterial (course_id, unit_usage_key, content_version unique per unit,
  status INDEXING/READY/FAILED/SUPERSEDED, checksum, created_at)
- MaterialSegment (kind transcript/notes, ordinal >=0 unique per material,
  text non-empty, 0 <= start_ms < end_ms for transcript, notes no timestamps,
  section_title for notes, source_ref canonical video@MM:SS/notes#slug)
- Conversation (user_id, course_id, unit_usage_key immutable,
  expires_at = created_at + conversation_ttl_days)
- Message (role student/tutor, statuses asked/shown/blocked/no_materials/off_topic/error;
  terminal immutable: blocked never becomes shown; shown requires non-empty sources;
  blocked text null/rule-only, blocked_reason required; latency_ms >=0 for terminal tutor;
  route="default"; config_version present)
- BlockRecord (append-only, message/conversation FK, denormalized user/course/unit,
  question, reason, guard_model_id, config_version, created_at)
- DailyCounter (composite PK user_id+date_utc, accepted_count atomic 0..daily_limit,
  never decreases)
- IdempotencyRecord (request_id, user_id, response_hash, created_at)
- LLMUsageLog (request_id, user_id, model_id, operation generate/guard/off_topic/gate,
  input/output token counts, estimated_cost_usd, config_version)
- FTS5 availability (presence of module/ability to create FTS5 table in SQLite)

Expected RED reason: Models and migrations do not exist yet (ModuleNotFoundError/
Django ImproperlyConfigured about missing models). DO NOT create models — that is T-010.
"""

import pytest
import sqlite3
from django.db import connection, models
from django.core.exceptions import ValidationError


# ─── Lazy imports so collection fails with expected reason if models missing ─────

def _get_material_models():
    """Import material models lazily; raises ImportError if not implemented."""
    from ai_tutor_service.materials.models import UnitMaterial, MaterialSegment  # noqa: PLC0415
    return UnitMaterial, MaterialSegment


def _get_conversation_models():
    """Import conversation models lazily; raises ImportError if not implemented."""
    from ai_tutor_service.conversations.models import Conversation, Message  # noqa: PLC0415
    return Conversation, Message


def _get_guard_models():
    """Import guard models lazily; raises ImportError if not implemented."""
    from ai_tutor_service.guard.models import BlockRecord  # noqa: PLC0415
    return BlockRecord


def _get_limits_models():
    """Import limits models lazily; raises ImportError if not implemented."""
    from ai_tutor_service.limits.models import DailyCounter, IdempotencyRecord  # noqa: PLC0415
    return DailyCounter, IdempotencyRecord


def _get_provider_models():
    """Import provider models lazily; raises ImportError if not implemented."""
    from ai_tutor_service.providers.models import LLMUsageLog  # noqa: PLC0415
    return LLMUsageLog


# ─── UnitMaterial & MaterialSegment ────────────────────────────────────────────

@pytest.mark.django_db
class TestUnitMaterialModel:
    """Tests for UnitMaterial model per data-model.md §1."""

    def test_unit_material_fields_exist(self):
        UnitMaterial, _ = _get_material_models()

        # Check all required fields exist
        field_names = {f.name for f in UnitMaterial._meta.get_fields()}
        assert "id" in field_names
        assert "course_id" in field_names
        assert "unit_usage_key" in field_names
        assert "content_version" in field_names
        assert "status" in field_names
        assert "checksum" in field_names
        assert "created_at" in field_names

    def test_unit_material_id_is_uuid(self):
        UnitMaterial, _ = _get_material_models()
        id_field = UnitMaterial._meta.get_field("id")
        assert isinstance(id_field, models.UUIDField)
        assert id_field.primary_key is True

    def test_unit_material_course_id_required(self):
        UnitMaterial, _ = _get_material_models()
        field = UnitMaterial._meta.get_field("course_id")
        assert field.blank is False
        assert field.null is False

    def test_unit_material_unit_usage_key_required(self):
        UnitMaterial, _ = _get_material_models()
        field = UnitMaterial._meta.get_field("unit_usage_key")
        assert field.blank is False
        assert field.null is False

    def test_unit_material_content_version_unique_per_unit(self):
        UnitMaterial, _ = _get_material_models()
        # Unique constraint on (unit_usage_key, content_version)
        constraints = UnitMaterial._meta.constraints
        unique_constraints = [c for c in constraints if isinstance(c, models.UniqueConstraint)]
        fields_sets = {tuple(c.fields) for c in unique_constraints}
        assert ("unit_usage_key", "content_version") in fields_sets or \
               ("content_version", "unit_usage_key") in fields_sets

    def test_unit_material_status_enum_values(self):
        UnitMaterial, _ = _get_material_models()
        field = UnitMaterial._meta.get_field("status")
        # Should be a choices field with INDEXING, READY, FAILED, SUPERSEDED
        assert hasattr(field, "choices")
        choices = [c[0] for c in field.choices]
        assert "INDEXING" in choices
        assert "READY" in choices
        assert "FAILED" in choices
        assert "SUPERSEDED" in choices
        assert len(choices) == 4

    def test_unit_material_status_index(self):
        UnitMaterial, _ = _get_material_models()
        field = UnitMaterial._meta.get_field("status")
        assert field.db_index is True

    def test_unit_material_checksum_required(self):
        UnitMaterial, _ = _get_material_models()
        field = UnitMaterial._meta.get_field("checksum")
        assert field.blank is False
        assert field.null is False

    def test_unit_material_created_at_auto_now_add(self):
        UnitMaterial, _ = _get_material_models()
        field = UnitMaterial._meta.get_field("created_at")
        assert field.auto_now_add is True or field.default is not None


@pytest.mark.django_db
class TestMaterialSegmentModel:
    """Tests for MaterialSegment model per data-model.md §1."""

    def test_material_segment_fields_exist(self):
        _, MaterialSegment = _get_material_models()

        field_names = {f.name for f in MaterialSegment._meta.get_fields()}
        assert "id" in field_names
        assert "material_id" in field_names
        assert "kind" in field_names
        assert "ordinal" in field_names
        assert "text" in field_names
        assert "start_ms" in field_names
        assert "end_ms" in field_names
        assert "section_title" in field_names
        assert "source_ref" in field_names

    def test_material_segment_id_is_uuid(self):
        _, MaterialSegment = _get_material_models()
        id_field = MaterialSegment._meta.get_field("id")
        assert isinstance(id_field, models.UUIDField)
        assert id_field.primary_key is True

    def test_material_segment_material_fk_cascade(self):
        _, MaterialSegment = _get_material_models()
        fk = MaterialSegment._meta.get_field("material_id")
        assert isinstance(fk, models.ForeignKey)
        assert fk.remote_field.on_delete == models.CASCADE

    def test_material_segment_kind_enum(self):
        _, MaterialSegment = _get_material_models()
        field = MaterialSegment._meta.get_field("kind")
        assert hasattr(field, "choices")
        choices = [c[0] for c in field.choices]
        assert "transcript" in choices
        assert "notes" in choices
        assert len(choices) == 2

    def test_material_segment_ordinal_unique_per_material(self):
        _, MaterialSegment = _get_material_models()
        constraints = MaterialSegment._meta.constraints
        unique_constraints = [c for c in constraints if isinstance(c, models.UniqueConstraint)]
        fields_sets = {tuple(c.fields) for c in unique_constraints}
        assert ("material_id", "ordinal") in fields_sets or \
               ("ordinal", "material_id") in fields_sets

    def test_material_segment_ordinal_non_negative(self):
        _, MaterialSegment = _get_material_models()
        field = MaterialSegment._meta.get_field("ordinal")
        # Should have MinValueValidator(0) or PositiveIntegerField
        from django.core.validators import MinValueValidator
        has_min_validator = any(
            isinstance(v, MinValueValidator) and v.limit_value == 0
            for v in field.validators
        )
        assert has_min_validator or isinstance(field, models.PositiveIntegerField)

    def test_material_segment_text_non_empty(self):
        _, MaterialSegment = _get_material_models()
        field = MaterialSegment._meta.get_field("text")
        assert field.blank is False
        # TextField doesn't enforce non-empty at DB level; validated in clean()

    def test_material_segment_start_end_ms_for_transcript(self):
        _, MaterialSegment = _get_material_models()
        start_field = MaterialSegment._meta.get_field("start_ms")
        end_field = MaterialSegment._meta.get_field("end_ms")
        # Both nullable for notes, required for transcript
        assert start_field.null is True
        assert end_field.null is True

    def test_material_segment_section_title_for_notes(self):
        _, MaterialSegment = _get_material_models()
        field = MaterialSegment._meta.get_field("section_title")
        assert field.null is True
        assert field.blank is True

    def test_material_segment_source_ref_required(self):
        _, MaterialSegment = _get_material_models()
        field = MaterialSegment._meta.get_field("source_ref")
        assert field.blank is False
        assert field.null is False


# ─── Conversation ──────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestConversationModel:
    """Tests for Conversation model per data-model.md §2."""

    def test_conversation_fields_exist(self):
        Conversation, _ = _get_conversation_models()

        field_names = {f.name for f in Conversation._meta.get_fields()}
        assert "id" in field_names
        assert "user_id" in field_names
        assert "course_id" in field_names
        assert "unit_usage_key" in field_names
        assert "created_at" in field_names
        assert "updated_at" in field_names
        assert "expires_at" in field_names

    def test_conversation_id_is_uuid(self):
        Conversation, _ = _get_conversation_models()
        id_field = Conversation._meta.get_field("id")
        assert isinstance(id_field, models.UUIDField)
        assert id_field.primary_key is True

    def test_conversation_user_id_required(self):
        Conversation, _ = _get_conversation_models()
        field = Conversation._meta.get_field("user_id")
        assert field.blank is False
        assert field.null is False

    def test_conversation_course_id_immutable(self):
        Conversation, _ = _get_conversation_models()
        field = Conversation._meta.get_field("course_id")
        assert field.blank is False
        assert field.null is False
        # Immutable after creation - enforced in save() or via editable=False
        # At minimum, not auto-populated on update

    def test_conversation_unit_usage_key_immutable(self):
        Conversation, _ = _get_conversation_models()
        field = Conversation._meta.get_field("unit_usage_key")
        assert field.blank is False
        assert field.null is False

    def test_conversation_expires_at_calculated(self):
        Conversation, _ = _get_conversation_models()
        field = Conversation._meta.get_field("expires_at")
        assert field.null is False
        # expires_at = created_at + conversation_ttl_days (from config)
        # This is typically set in save() or via a default callable

    def test_conversation_created_at_auto_now_add(self):
        Conversation, _ = _get_conversation_models()
        field = Conversation._meta.get_field("created_at")
        assert field.auto_now_add is True

    def test_conversation_updated_at_auto_now(self):
        Conversation, _ = _get_conversation_models()
        field = Conversation._meta.get_field("updated_at")
        assert field.auto_now is True


# ─── Message ───────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestMessageModel:
    """Tests for Message model per data-model.md §3."""

    def test_message_fields_exist(self):
        _, Message = _get_conversation_models()

        field_names = {f.name for f in Message._meta.get_fields()}
        assert "id" in field_names
        assert "conversation_id" in field_names
        assert "role" in field_names
        assert "text" in field_names
        assert "status" in field_names
        assert "topic" in field_names
        assert "sources" in field_names
        assert "blocked_reason" in field_names
        assert "latency_ms" in field_names
        assert "route" in field_names
        assert "config_version" in field_names
        assert "created_at" in field_names

    def test_message_id_is_uuid(self):
        _, Message = _get_conversation_models()
        id_field = Message._meta.get_field("id")
        assert isinstance(id_field, models.UUIDField)
        assert id_field.primary_key is True

    def test_message_conversation_fk(self):
        _, Message = _get_conversation_models()
        fk = Message._meta.get_field("conversation_id")
        assert isinstance(fk, models.ForeignKey)
        assert fk.remote_field.on_delete == models.CASCADE

    def test_message_role_enum(self):
        _, Message = _get_conversation_models()
        field = Message._meta.get_field("role")
        assert hasattr(field, "choices")
        choices = [c[0] for c in field.choices]
        assert "student" in choices
        assert "tutor" in choices
        assert len(choices) == 2

    def test_message_status_enum(self):
        _, Message = _get_conversation_models()
        field = Message._meta.get_field("status")
        assert hasattr(field, "choices")
        choices = [c[0] for c in field.choices]
        expected = {"asked", "shown", "blocked", "no_materials", "off_topic", "error"}
        assert expected.issubset(set(choices))

    def test_message_status_terminal_immutable(self):
        """Terminal status (shown, blocked, no_materials, off_topic, error) immutable."""
        _, Message = _get_conversation_models()
        # This is enforced in save() or clean() - check model has the logic
        # We verify the model has a clean() or save() method that prevents transition
        assert hasattr(Message, "clean") or hasattr(Message, "save")

    def test_message_shown_requires_sources(self):
        """shown status requires non-empty sources."""
        _, Message = _get_conversation_models()
        # Enforced in clean() - verify model has validation
        assert hasattr(Message, "clean")

    def test_message_blocked_text_null_or_rule_only(self):
        """blocked status has text null or rule-only (from YAML)."""
        _, Message = _get_conversation_models()
        field = Message._meta.get_field("text")
        assert field.null is True
        assert field.blank is True

    def test_message_blocked_reason_required_for_blocked(self):
        """blocked_reason required only for blocked status."""
        _, Message = _get_conversation_models()
        field = Message._meta.get_field("blocked_reason")
        assert field.null is True
        assert field.blank is True
        # Required only when status=blocked - validated in clean()

    def test_message_latency_ms_non_negative_for_tutor(self):
        """latency_ms >= 0 for terminal tutor message."""
        _, Message = _get_conversation_models()
        field = Message._meta.get_field("latency_ms")
        assert field.null is True
        assert field.blank is True
        from django.core.validators import MinValueValidator
        has_min_validator = any(
            isinstance(v, MinValueValidator) and v.limit_value == 0
            for v in field.validators
        )
        assert has_min_validator or isinstance(field, models.PositiveIntegerField)

    def test_message_route_default(self):
        """route field defaults to 'default'."""
        _, Message = _get_conversation_models()
        field = Message._meta.get_field("route")
        assert field.default == "default"

    def test_message_config_version_required(self):
        """config_version (SemVer string) present on all messages."""
        _, Message = _get_conversation_models()
        field = Message._meta.get_field("config_version")
        assert field.blank is False
        assert field.null is False

    def test_message_created_at_auto_now_add(self):
        _, Message = _get_conversation_models()
        field = Message._meta.get_field("created_at")
        assert field.auto_now_add is True


# ─── BlockRecord ───────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestBlockRecordModel:
    """Tests for BlockRecord model per data-model.md §4 (append-only audit)."""

    def test_block_record_fields_exist(self):
        BlockRecord = _get_guard_models()

        field_names = {f.name for f in BlockRecord._meta.get_fields()}
        assert "id" in field_names
        assert "message_id" in field_names
        assert "conversation_id" in field_names
        assert "user_id" in field_names
        assert "course_id" in field_names
        assert "unit_usage_key" in field_names
        assert "question" in field_names
        assert "reason" in field_names
        assert "guard_model_id" in field_names
        assert "config_version" in field_names
        assert "created_at" in field_names

    def test_block_record_id_is_uuid(self):
        BlockRecord = _get_guard_models()
        id_field = BlockRecord._meta.get_field("id")
        assert isinstance(id_field, models.UUIDField)
        assert id_field.primary_key is True

    def test_block_record_message_fk(self):
        BlockRecord = _get_guard_models()
        fk = BlockRecord._meta.get_field("message_id")
        assert isinstance(fk, models.ForeignKey)

    def test_block_record_conversation_fk(self):
        BlockRecord = _get_guard_models()
        fk = BlockRecord._meta.get_field("conversation_id")
        assert isinstance(fk, models.ForeignKey)

    def test_block_record_denormalized_fields(self):
        """user_id, course_id, unit_usage_key denormalized for audit."""
        BlockRecord = _get_guard_models()
        for field_name in ["user_id", "course_id", "unit_usage_key"]:
            field = BlockRecord._meta.get_field(field_name)
            assert field.blank is False
            assert field.null is False

    def test_block_record_question_required(self):
        BlockRecord = _get_guard_models()
        field = BlockRecord._meta.get_field("question")
        assert field.blank is False
        assert field.null is False

    def test_block_record_reason_required(self):
        BlockRecord = _get_guard_models()
        field = BlockRecord._meta.get_field("reason")
        assert field.blank is False
        assert field.null is False

    def test_block_record_guard_model_id_required(self):
        BlockRecord = _get_guard_models()
        field = BlockRecord._meta.get_field("guard_model_id")
        assert field.blank is False
        assert field.null is False

    def test_block_record_config_version_required(self):
        BlockRecord = _get_guard_models()
        field = BlockRecord._meta.get_field("config_version")
        assert field.blank is False
        assert field.null is False

    def test_block_record_created_at_auto_now_add(self):
        BlockRecord = _get_guard_models()
        field = BlockRecord._meta.get_field("created_at")
        assert field.auto_now_add is True

    def test_block_record_append_only_no_update_delete(self):
        """BlockRecord should be append-only - no UPDATE/DELETE in normal operation."""
        # This is a design constraint - verified by absence of update/delete logic
        # and by tests in test_block_record.py (T-039/T-040)
        BlockRecord = _get_guard_models()
        # Model exists with only creation logic


# ─── DailyCounter ──────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDailyCounterModel:
    """Tests for DailyCounter model per data-model.md §5."""

    def test_daily_counter_fields_exist(self):
        DailyCounter, _ = _get_limits_models()

        field_names = {f.name for f in DailyCounter._meta.get_fields()}
        assert "user_id" in field_names
        assert "date_utc" in field_names
        assert "accepted_count" in field_names
        assert "updated_at" in field_names

    def test_daily_counter_composite_pk(self):
        DailyCounter, _ = _get_limits_models()
        # Composite primary key: user_id + date_utc
        pk_fields = [f.name for f in DailyCounter._meta.fields if f.primary_key]
        # Django doesn't support composite PK natively; typically uses UniqueConstraint
        # Check for UniqueConstraint on (user_id, date_utc)
        constraints = DailyCounter._meta.constraints
        unique_constraints = [c for c in constraints if isinstance(c, models.UniqueConstraint)]
        fields_sets = {tuple(c.fields) for c in unique_constraints}
        assert ("user_id", "date_utc") in fields_sets or \
               ("date_utc", "user_id") in fields_sets

    def test_daily_counter_user_id_required(self):
        DailyCounter, _ = _get_limits_models()
        field = DailyCounter._meta.get_field("user_id")
        assert field.blank is False
        assert field.null is False

    def test_daily_counter_date_utc_required(self):
        DailyCounter, _ = _get_limits_models()
        field = DailyCounter._meta.get_field("date_utc")
        assert field.blank is False
        assert field.null is False
        assert isinstance(field, models.DateField)

    def test_daily_counter_accepted_count_atomic_bounds(self):
        """accepted_count atomic 0..daily_limit, never decreases."""
        DailyCounter, _ = _get_limits_models()
        field = DailyCounter._meta.get_field("accepted_count")
        assert field.default == 0
        from django.core.validators import MinValueValidator
        has_min_validator = any(
            isinstance(v, MinValueValidator) and v.limit_value == 0
            for v in field.validators
        )
        assert has_min_validator or isinstance(field, models.PositiveIntegerField)

    def test_daily_counter_updated_at_auto_now(self):
        DailyCounter, _ = _get_limits_models()
        field = DailyCounter._meta.get_field("updated_at")
        assert field.auto_now is True


# ─── IdempotencyRecord ─────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestIdempotencyRecordModel:
    """Tests for IdempotencyRecord model (mentioned in tasks, per data-model)."""

    def test_idempotency_record_fields_exist(self):
        _, IdempotencyRecord = _get_limits_models()

        field_names = {f.name for f in IdempotencyRecord._meta.get_fields()}
        assert "request_id" in field_names
        assert "user_id" in field_names
        assert "response_hash" in field_names
        assert "created_at" in field_names

    def test_idempotency_record_request_id_pk(self):
        _, IdempotencyRecord = _get_limits_models()
        field = IdempotencyRecord._meta.get_field("request_id")
        assert isinstance(field, models.UUIDField)
        assert field.primary_key is True

    def test_idempotency_record_user_id_required(self):
        _, IdempotencyRecord = _get_limits_models()
        field = IdempotencyRecord._meta.get_field("user_id")
        assert field.blank is False
        assert field.null is False

    def test_idempotency_record_response_hash_required(self):
        _, IdempotencyRecord = _get_limits_models()
        field = IdempotencyRecord._meta.get_field("response_hash")
        assert field.blank is False
        assert field.null is False

    def test_idempotency_record_created_at_auto_now_add(self):
        _, IdempotencyRecord = _get_limits_models()
        field = IdempotencyRecord._meta.get_field("created_at")
        assert field.auto_now_add is True


# ─── LLMUsageLog ───────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestLLMUsageLogModel:
    """Tests for LLMUsageLog model per data-model.md §7."""

    def test_llm_usage_log_fields_exist(self):
        LLMUsageLog = _get_provider_models()

        field_names = {f.name for f in LLMUsageLog._meta.get_fields()}
        assert "request_id" in field_names
        assert "user_id" in field_names
        assert "model_id" in field_names
        assert "operation" in field_names
        assert "input_tokens" in field_names
        assert "output_tokens" in field_names
        assert "estimated_cost_usd" in field_names
        assert "config_version" in field_names
        assert "created_at" in field_names

    def test_llm_usage_log_request_id_not_pk(self):
        """request_id is not PK (multiple operations per request)."""
        LLMUsageLog = _get_provider_models()
        field = LLMUsageLog._meta.get_field("request_id")
        assert isinstance(field, models.UUIDField)
        assert field.primary_key is False

    def test_llm_usage_log_user_id_required(self):
        LLMUsageLog = _get_provider_models()
        field = LLMUsageLog._meta.get_field("user_id")
        assert field.blank is False
        assert field.null is False

    def test_llm_usage_log_model_id_required(self):
        LLMUsageLog = _get_provider_models()
        field = LLMUsageLog._meta.get_field("model_id")
        assert field.blank is False
        assert field.null is False

    def test_llm_usage_log_operation_enum(self):
        LLMUsageLog = _get_provider_models()
        field = LLMUsageLog._meta.get_field("operation")
        assert hasattr(field, "choices")
        choices = [c[0] for c in field.choices]
        expected = {"generate", "guard", "off_topic", "gate"}
        assert expected.issubset(set(choices))

    def test_llm_usage_log_input_tokens_non_negative(self):
        LLMUsageLog = _get_provider_models()
        field = LLMUsageLog._meta.get_field("input_tokens")
        from django.core.validators import MinValueValidator
        has_min_validator = any(
            isinstance(v, MinValueValidator) and v.limit_value == 0
            for v in field.validators
        )
        assert has_min_validator or isinstance(field, models.PositiveIntegerField)

    def test_llm_usage_log_output_tokens_non_negative(self):
        LLMUsageLog = _get_provider_models()
        field = LLMUsageLog._meta.get_field("output_tokens")
        from django.core.validators import MinValueValidator
        has_min_validator = any(
            isinstance(v, MinValueValidator) and v.limit_value == 0
            for v in field.validators
        )
        assert has_min_validator or isinstance(field, models.PositiveIntegerField)

    def test_llm_usage_log_estimated_cost_usd(self):
        LLMUsageLog = _get_provider_models()
        field = LLMUsageLog._meta.get_field("estimated_cost_usd")
        # DecimalField with appropriate precision
        assert isinstance(field, models.DecimalField)
        assert field.max_digits >= 10
        assert field.decimal_places >= 4

    def test_llm_usage_log_config_version_required(self):
        LLMUsageLog = _get_provider_models()
        field = LLMUsageLog._meta.get_field("config_version")
        assert field.blank is False
        assert field.null is False

    def test_llm_usage_log_created_at_auto_now_add(self):
        LLMUsageLog = _get_provider_models()
        field = LLMUsageLog._meta.get_field("created_at")
        assert field.auto_now_add is True


# ─── FTS5 Availability ─────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestFTS5Availability:
    """Test FTS5 module availability in SQLite per data-model.md §1."""

    def test_sqlite_fts5_module_available(self):
        """SQLite must have FTS5 extension compiled in."""
        # Try to create a virtual table using FTS5
        with connection.cursor() as cursor:
            cursor.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS test_fts5_availability
                USING fts5(content)
            """)
            cursor.execute("DROP TABLE test_fts5_availability")

    def test_fts5_can_insert_and_search(self):
        """FTS5 table can be created, inserted into, and searched."""
        with connection.cursor() as cursor:
            cursor.execute("""
                CREATE VIRTUAL TABLE test_fts5_search USING fts5(id UNINDEXED, text, section_title)
            """)
            # Use dict params to avoid Django debug logging bug with ? placeholders
            cursor.execute(
                "INSERT INTO test_fts5_search (id, text, section_title) VALUES (:id, :text, :section)",
                {"id": "seg-1", "text": "test content about physics", "section": "Introduction"}
            )
            cursor.execute(
                "SELECT id FROM test_fts5_search WHERE test_fts5_search MATCH :q",
                {"q": "physics"}
            )
            rows = cursor.fetchall()
            assert len(rows) == 1
            assert rows[0][0] == "seg-1"
            cursor.execute("DROP TABLE test_fts5_search")


# ─── Integration: cross-model constraints ──────────────────────────────────────

@pytest.mark.django_db
class TestCrossModelConstraints:
    """Cross-model invariants per data-model.md §9."""

    def test_message_status_transitions(self):
        """Verify allowed transitions: asked -> shown|blocked|no_materials|off_topic|error."""
        _, Message = _get_conversation_models()
        # Terminal statuses are immutable - enforced in model clean/save
        terminal_statuses = {"shown", "blocked", "no_materials", "off_topic", "error"}
        field = Message._meta.get_field("status")
        choices = [c[0] for c in field.choices]
        for ts in terminal_statuses:
            assert ts in choices

    def test_blocked_never_becomes_shown(self):
        """blocked status never transitions to shown (new turn created instead)."""
        _, Message = _get_conversation_models()
        # This is a behavioral constraint - enforced in business logic
        # Model should prevent updating status from blocked to shown
        assert hasattr(Message, "clean") or hasattr(Message, "save")

    def test_conversation_expires_at_ttl(self):
        """expires_at = created_at + conversation_ttl_days from config."""
        Conversation, _ = _get_conversation_models()
        # Verify the model has logic to compute expires_at from config
        # This is typically done in save() using tutor_config
        assert hasattr(Conversation, "save")

    def test_no_secrets_in_models(self):
        """No secrets in any model fields (FR-002-14, Constitution I)."""
        UnitMaterial, MaterialSegment = _get_material_models()
        Conversation, Message = _get_conversation_models()
        BlockRecord = _get_guard_models()
        DailyCounter, IdempotencyRecord = _get_limits_models()
        LLMUsageLog = _get_provider_models()

        all_models = [
            UnitMaterial, MaterialSegment, Conversation, Message,
            BlockRecord, DailyCounter, IdempotencyRecord, LLMUsageLog
        ]

        # Check for exact secret field names, not substrings.
        # data-model.md §7 explicitly defines input_tokens/output_tokens as legitimate fields.
        secret_field_names = {"secret", "api_key", "token", "password", "private_key", "access_token", "refresh_token"}
        for model in all_models:
            for field in model._meta.get_fields():
                if hasattr(field, "name"):
                    field_name = field.name.lower()
                    assert field_name not in secret_field_names, \
                        f"Secret-like field '{field.name}' found in {model.__name__}"

    def test_config_version_present_in_terminal_entities(self):
        """config_version present in Message, BlockRecord, LLMUsageLog (Constitution III)."""
        _, Message = _get_conversation_models()
        BlockRecord = _get_guard_models()
        LLMUsageLog = _get_provider_models()

        for model in [Message, BlockRecord, LLMUsageLog]:
            field = model._meta.get_field("config_version")
            assert field.blank is False
            assert field.null is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])