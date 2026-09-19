# impl: FR-002-03
"""
Material repository for AI Tutor Service.

Handles persistence of UnitMaterial, MaterialSegment, FTS5 indexing,
and idempotency records.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Optional

from django.db import transaction, connection
from django.db.models import Q

from ai_tutor_service.materials.models import UnitMaterial, MaterialSegment
from ai_tutor_service.limits.models import IdempotencyRecord


@dataclass(slots=True)
class IdempotencyRecordData:
    """Idempotency key record linking request_id to payload hash and response."""
    request_id: str
    payload_hash: str
    response: dict
    material_id: Optional[uuid.UUID] = None


class MaterialRepository:
    """
    Repository for material operations with atomic FTS5 indexing and idempotency.

    Uses Django ORM for primary data and raw SQL for FTS5 operations
    (Django doesn't have native FTS5 support).
    """

    def __init__(self):
        pass

    def _compute_checksum(self, payload: dict) -> str:
        """Compute SHA-256 checksum of normalized payload."""
        normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(normalized.encode()).hexdigest()

    def _validate_segments(self, transcript: list, notes: list) -> list[str]:
        """Validate segments and return list of errors (empty if valid)."""
        errors = []

        # Validate transcript segments
        for i, seg in enumerate(transcript):
            if not isinstance(seg.get("ordinal"), int) or seg["ordinal"] < 0:
                errors.append(f"transcript[{i}]: ordinal must be non-negative integer")
            if not isinstance(seg.get("start_ms"), int) or seg["start_ms"] < 0:
                errors.append(f"transcript[{i}]: start_ms must be non-negative integer")
            if not isinstance(seg.get("end_ms"), int) or seg["end_ms"] <= seg.get("start_ms", -1):
                errors.append(f"transcript[{i}]: end_ms must be > start_ms")
            text = seg.get("text", "")
            if not isinstance(text, str) or not text.strip():
                errors.append(f"transcript[{i}]: text must be non-empty string")
            source_ref = seg.get("source_ref", "")
            if not isinstance(source_ref, str) or not source_ref.startswith("video@"):
                errors.append(f"transcript[{i}]: source_ref must be in format 'video@MM:SS'")

        # Validate notes segments
        for i, seg in enumerate(notes):
            if not isinstance(seg.get("ordinal"), int) or seg["ordinal"] < 0:
                errors.append(f"notes[{i}]: ordinal must be non-negative integer")
            text = seg.get("text", "")
            if not isinstance(text, str) or not text.strip():
                errors.append(f"notes[{i}]: text must be non-empty string")
            section_title = seg.get("section_title", "")
            if not isinstance(section_title, str) or not section_title.strip():
                errors.append(f"notes[{i}]: section_title must be non-empty string")
            source_ref = seg.get("source_ref", "")
            if not isinstance(source_ref, str) or not source_ref.startswith("notes#"):
                errors.append(f"notes[{i}]: source_ref must be in format 'notes#slug'")
            # Notes must not have timestamps
            if seg.get("start_ms") is not None or seg.get("end_ms") is not None:
                errors.append(f"notes[{i}]: must not have start_ms or end_ms")

        return errors

    def _build_fts5_content(self, segments: list[MaterialSegment]) -> list[tuple]:
        """Build FTS5 content tuples from segments."""
        fts_rows = []
        for seg in segments:
            # FTS5 table columns: segment_id, text, section_title, kind, material_id, ordinal, source_ref
            # Store material_id as hex (without hyphens) to match UnitMaterial.id storage format
            section_title = seg.section_title or ""
            fts_rows.append((
                str(seg.id),
                seg.text,
                section_title,
                seg.kind,
                seg.material_id_id.hex,  # Use hex format for JOIN compatibility
                seg.ordinal,
                seg.source_ref,
            ))
        return fts_rows

    def _insert_fts5(self, fts_rows: list[tuple]) -> None:
        """Insert rows into FTS5 virtual table."""
        if not fts_rows:
            return
        with connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO material_segment_fts(segment_id, text, section_title, kind, material_id, ordinal, source_ref) VALUES (?, ?, ?, ?, ?, ?, ?)",
                fts_rows,
            )

    def _delete_fts5_for_material(self, material_id: uuid.UUID) -> None:
        """Delete FTS5 entries for a material."""
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM material_segment_fts WHERE material_id = ?",
                [str(material_id)],
            )

    def _supersede_old_material(self, course_id: str, unit_usage_key: str, new_material_id: uuid.UUID) -> None:
        """
        Atomically mark old READY material as SUPERSEDED.

        This is called AFTER the new material is successfully created and indexed.
        """
        import logging
        logger = logging.getLogger(__name__)
        # Use hex format (without hyphens) for raw SQL comparison since SQLite stores UUIDs as 32-char hex
        new_id_hex = new_material_id.hex
        logger.debug(f"Superseding old materials: new_id_hex={new_id_hex}, course={course_id}, unit={unit_usage_key}")
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE materials_unitmaterial
                SET status = 'SUPERSEDED'
                WHERE course_id = ? AND unit_usage_key = ?
                  AND status = 'READY' AND id != ?
                """,
                [course_id, unit_usage_key, new_id_hex],
            )
            logger.debug(f"Superseded rows_affected: {cursor.rowcount}")

    @transaction.atomic
    def create_material(
        self,
        course_id: str,
        unit_usage_key: str,
        content_version: str,
        transcript: list[dict],
        notes: list[dict],
    ) -> uuid.UUID:
        """
        Create a new material with segments and FTS5 index.

        Steps:
        1. Validate all segments
        2. Create UnitMaterial with status=INDEXING
        3. Create MaterialSegment rows
        4. Populate FTS5 index
        5. Update status to READY
        6. Supersede old READY material for same unit (if new content_version)

        Returns material_id.
        """
        # Validate segments
        payload = {
            "course_id": course_id,
            "unit_usage_key": unit_usage_key,
            "content_version": content_version,
            "transcript": transcript,
            "notes": notes,
        }
        errors = self._validate_segments(transcript, notes)
        if errors:
            raise ValueError("; ".join(errors))

        checksum = self._compute_checksum(payload)

        # Create material in INDEXING state
        material = UnitMaterial.objects.create(
            course_id=course_id,
            unit_usage_key=unit_usage_key,
            content_version=content_version,
            status=UnitMaterial.Status.INDEXING,
            checksum=checksum,
        )

        # Create segments
        segments = []
        for seg_data in transcript:
            segment = MaterialSegment.objects.create(
                material_id=material,
                kind=MaterialSegment.Kind.TRANSCRIPT,
                ordinal=seg_data["ordinal"],
                text=seg_data["text"],
                start_ms=seg_data["start_ms"],
                end_ms=seg_data["end_ms"],
                section_title=None,
                source_ref=seg_data["source_ref"],
            )
            segments.append(segment)

        for seg_data in notes:
            segment = MaterialSegment.objects.create(
                material_id=material,
                kind=MaterialSegment.Kind.NOTES,
                ordinal=seg_data["ordinal"],
                text=seg_data["text"],
                start_ms=None,
                end_ms=None,
                section_title=seg_data["section_title"],
                source_ref=seg_data["source_ref"],
            )
            segments.append(segment)

        # Populate FTS5 index
        fts_rows = self._build_fts5_content(segments)
        self._insert_fts5(fts_rows)

        # Update status to READY
        material.status = UnitMaterial.Status.READY
        material.save(update_fields=["status"])

        # Atomically supersede old READY material for this unit
        self._supersede_old_material(course_id, unit_usage_key, material.id)

        return material.id

    def get_material_by_id(self, material_id: uuid.UUID) -> Optional[UnitMaterial]:
        """Get material by ID, or None if not found."""
        try:
            return UnitMaterial.objects.get(id=material_id)
        except UnitMaterial.DoesNotExist:
            return None

    def get_latest_ready_material(self, course_id: str, unit_usage_key: str) -> Optional[UnitMaterial]:
        """Get the latest READY material for a course/unit, or None."""
        return UnitMaterial.objects.filter(
            course_id=course_id,
            unit_usage_key=unit_usage_key,
            status=UnitMaterial.Status.READY,
        ).order_by("-created_at").first()

    def get_status_summary(self, course_id: str, unit_usage_key: str) -> dict:
        """
        Get status summary for a course/unit.

        Returns dict with: status, content_version, segment_count, config_version.
        """
        import logging
        logger = logging.getLogger(__name__)
        from ai_tutor_service.config import load_tutor_config
        from pathlib import Path

        config = load_tutor_config(
            Path(__file__).resolve().parent.parent / "tutor_config.yaml"
        )

        material = self.get_latest_ready_material(course_id, unit_usage_key)
        logger.debug(f"get_status_summary: latest_ready={material.id if material else None}")
        if material is None:
            # Check if there's any material (INDEXING/FAILED/SUPERSEDED)
            any_material = UnitMaterial.objects.filter(
                course_id=course_id,
                unit_usage_key=unit_usage_key,
            ).order_by("-created_at").first()

            logger.debug(f"get_status_summary: any_material={any_material.id if any_material else None}, status={any_material.status if any_material else None}")
            if any_material is None:
                return {
                    "status": "MISSING",
                    "content_version": None,
                    "segment_count": 0,
                    "config_version": config["version"],
                }
            else:
                # There's a material but not READY
                return {
                    "status": any_material.status,
                    "content_version": any_material.content_version,
                    "segment_count": 0,
                    "config_version": config["version"],
                }

        segment_count = material.segments.count()
        return {
            "status": material.status,
            "content_version": material.content_version,
            "segment_count": segment_count,
            "config_version": config["version"],
        }

    # Idempotency methods
    def check_idempotency(self, request_id: str, payload: dict, user_id: str) -> Optional[dict]:
        """
        Check if request_id exists with same payload.

        Returns:
            - None: no record exists, proceed with creation
            - dict: cached response for same payload
            - Raises ValueError: same request_id but different payload (409)
        """
        payload_hash = self._compute_checksum(payload)

        # Atomic get-or-create: try to get existing record
        try:
            record = IdempotencyRecord.objects.get(request_id=request_id)
        except IdempotencyRecord.DoesNotExist:
            return None

        # Record exists - check payload hash
        if record.payload_hash == payload_hash:
            # Same key + same payload -> return stored response
            return record.response
        else:
            # Same key + different payload -> conflict
            raise ValueError("idempotency_conflict")

    def store_idempotency(self, request_id: str, payload: dict, response: dict, material_id: uuid.UUID, user_id: str) -> None:
        """Store idempotency record atomically."""
        payload_hash = self._compute_checksum(payload)

        # Use get_or_create with defaults for atomic creation
        # If record already exists (race), the existing one wins (same payload checked above)
        IdempotencyRecord.objects.get_or_create(
            request_id=request_id,
            defaults={
                "user_id": user_id,
                "payload_hash": payload_hash,
                "response": response,
            }
        )