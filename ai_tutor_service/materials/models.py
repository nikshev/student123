# impl: FR-002-14
"""
Material models for AI Tutor Service.

UnitMaterial groups one actual ingest version; MaterialSegment is the atomic
unit of retrieval and source of citations (FR-002-03/04).
"""

import uuid
from django.db import models
from django.core.validators import MinValueValidator


class UnitMaterial(models.Model):
    """Represents one ingest version of a unit's material."""

    class Status(models.TextChoices):
        INDEXING = "INDEXING", "Indexing"
        READY = "READY", "Ready"
        FAILED = "FAILED", "Failed"
        SUPERSEDED = "SUPERSEDED", "Superseded"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    course_id = models.CharField(max_length=255, blank=False, null=False)
    unit_usage_key = models.CharField(max_length=255, blank=False, null=False)
    content_version = models.CharField(max_length=255, blank=False, null=False)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.INDEXING,
        db_index=True,
    )
    checksum = models.CharField(max_length=64, blank=False, null=False)  # SHA-256 hex
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["unit_usage_key", "content_version"],
                name="unique_content_version_per_unit",
            ),
        ]
        indexes = [
            models.Index(fields=["course_id", "unit_usage_key"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"UnitMaterial({self.unit_usage_key} v{self.content_version})"


class MaterialSegment(models.Model):
    """Atomic unit of retrieval and source of citations."""

    class Kind(models.TextChoices):
        TRANSCRIPT = "transcript", "Transcript"
        NOTES = "notes", "Notes"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    material_id = models.ForeignKey(
        UnitMaterial,
        on_delete=models.CASCADE,
        related_name="segments",
        db_column="material_id",
    )
    kind = models.CharField(max_length=20, choices=Kind.choices, blank=False, null=False)
    ordinal = models.PositiveIntegerField(
        validators=[MinValueValidator(0)],
        blank=False,
        null=False,
    )
    text = models.TextField(blank=False, null=False)
    start_ms = models.PositiveIntegerField(blank=True, null=True)
    end_ms = models.PositiveIntegerField(blank=True, null=True)
    section_title = models.CharField(max_length=255, blank=True, null=True)
    source_ref = models.CharField(max_length=255, blank=False, null=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["material_id", "ordinal"],
                name="unique_ordinal_per_material",
            ),
        ]
        indexes = [
            models.Index(fields=["material_id", "ordinal"]),
            models.Index(fields=["kind"]),
        ]

    def __str__(self):
        return f"MaterialSegment({self.material_id_id} #{self.ordinal} {self.kind})"

    def clean(self):
        from django.core.exceptions import ValidationError

        super().clean()

        # Text must be non-empty after trim
        if self.text and not self.text.strip():
            raise ValidationError({"text": "Segment text cannot be empty after trim."})

        # For transcript: 0 <= start < end; for notes: both null
        if self.kind == self.Kind.TRANSCRIPT:
            if self.start_ms is None or self.end_ms is None:
                raise ValidationError(
                    {"start_ms": "Transcript segments require start_ms and end_ms."}
                )
            if self.start_ms < 0:
                raise ValidationError({"start_ms": "start_ms must be >= 0."})
            if self.start_ms >= self.end_ms:
                raise ValidationError(
                    {"end_ms": "end_ms must be greater than start_ms for transcript."}
                )
        elif self.kind == self.Kind.NOTES:
            if self.start_ms is not None or self.end_ms is not None:
                raise ValidationError(
                    "Notes segments must not have start_ms or end_ms."
                )
            if not self.section_title or not self.section_title.strip():
                raise ValidationError(
                    {"section_title": "Notes segments require a non-empty section_title."}
                )

        # source_ref canonical format
        if self.source_ref:
            if self.kind == self.Kind.TRANSCRIPT:
                if not self.source_ref.startswith("video@"):
                    raise ValidationError(
                        {"source_ref": "Transcript source_ref must be in format 'video@MM:SS'."}
                    )
            elif self.kind == self.Kind.NOTES:
                if not self.source_ref.startswith("notes#"):
                    raise ValidationError(
                        {"source_ref": "Notes source_ref must be in format 'notes#slug'."}
                    )