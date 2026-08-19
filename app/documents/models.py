"""Document metadata is canonical in PostgreSQL; bytes live elsewhere.

``Document`` is the logical artefact and its role. ``DocumentVersion`` is one
exact immutable binary with a checksum and provenance. A logical document may
simultaneously carry a mutable SharePoint working reference and immutable
evidence versions, and the two are never presented as the same thing
(master specification 15.1–15.3).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from app.core.authorization import apply as apply_scope
from app.core.authorization import (
    child_visibility_q,
    effective_visibility_expression,
    scope_for_user,
)
from app.core.enums import Visibility
from app.core.models import BaseModel, VisibilityInheritingModel
from app.documents.enums import (
    DocumentRole,
    ExtractionState,
    MalwareScanState,
    RetentionClass,
)


class DocumentQuerySet(models.QuerySet):
    def visible_to(self, user: object | None) -> DocumentQuerySet:
        return apply_scope(self, child_visibility_q(scope_for_user(user)))

    def with_effective_visibility(self) -> DocumentQuerySet:
        """Annotate the derived visibility so lists do not query per row."""
        return self.annotate(derived_visibility=effective_visibility_expression())


class Document(VisibilityInheritingModel):
    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.PROTECT,
        related_name="documents",
        verbose_name="teema",
    )
    role = models.CharField(
        max_length=32,
        choices=DocumentRole.choices,
        default=DocumentRole.OTHER,
        db_index=True,
        verbose_name="roll",
    )
    title = models.CharField(max_length=400, verbose_name="pealkiri")
    current_version = models.ForeignKey(
        "documents.DocumentVersion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="kehtiv tõendiversioon",
    )

    # -- optional mutable working document (never evidence) ----------------
    sharepoint_site_id = models.CharField(max_length=200, blank=True)
    sharepoint_drive_id = models.CharField(max_length=200, blank=True)
    sharepoint_item_id = models.CharField(max_length=200, blank=True)
    sharepoint_web_url = models.URLField(max_length=1000, blank=True)
    sharepoint_etag = models.CharField(max_length=200, blank=True)
    sharepoint_observed_at = models.DateTimeField(null=True, blank=True)

    # -- retention --------------------------------------------------------
    retention_class = models.CharField(
        max_length=32,
        choices=RetentionClass.choices,
        default=RetentionClass.UNCLASSIFIED,
        verbose_name="säilitusklass",
    )
    legal_hold = models.BooleanField(default=False, verbose_name="õiguslik säilituskohustus")
    legal_hold_reason = models.TextField(blank=True, verbose_name="säilituskohustuse põhjus")
    legal_hold_set_at = models.DateTimeField(null=True, blank=True)
    legal_hold_set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="legal_holds_set",
    )

    provenance_note = models.TextField(blank=True, verbose_name="päritolu märkus")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="created_documents",
    )

    objects = DocumentQuerySet.as_manager()

    class Meta:
        verbose_name = "dokument"
        verbose_name_plural = "dokumendid"
        ordering = ["-created_at"]
        constraints = [
            # The override participates in every authorization decision, so the
            # database refuses a value the authorization code cannot interpret.
            models.CheckConstraint(
                condition=models.Q(
                    visibility_override__in=["", Visibility.NORMAL, Visibility.RESTRICTED]
                ),
                name="documents_visibility_override_vocabulary",
            ),
        ]
        indexes = [
            models.Index(fields=["matter", "role"], name="documents_matter_role"),
        ]

    def __str__(self) -> str:
        return self.title

    def parent_visibility(self) -> str:
        return self.matter.visibility

    @property
    def has_working_document(self) -> bool:
        return bool(self.sharepoint_item_id)

    @property
    def has_evidence(self) -> bool:
        return self.current_version_id is not None


class DocumentVersion(BaseModel):
    """One exact binary. Bytes never change; a correction is a new version."""

    document = models.ForeignKey(
        Document,
        on_delete=models.PROTECT,
        related_name="versions",
        verbose_name="dokument",
    )
    version_number = models.PositiveIntegerField(verbose_name="versioon")

    storage_key = models.CharField(max_length=500, verbose_name="hoidla võti")
    original_filename = models.CharField(max_length=400, verbose_name="algne failinimi")
    mime_type = models.CharField(max_length=200, verbose_name="MIME tüüp")
    size_bytes = models.BigIntegerField(verbose_name="suurus baitides")
    sha256 = models.CharField(max_length=64, db_index=True, verbose_name="SHA-256")

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="uploaded_document_versions",
    )
    acquired_at = models.DateTimeField(verbose_name="saadi")

    # Where this exact binary came from. Kept verbatim.
    source_path = models.TextField(blank=True)
    source_url = models.URLField(max_length=1000, blank=True)
    source_identifier = models.CharField(max_length=400, blank=True)
    sharepoint_item_version = models.CharField(max_length=200, blank=True)

    malware_scan_state = models.CharField(
        max_length=32,
        choices=MalwareScanState.choices,
        default=MalwareScanState.PENDING,
        verbose_name="pahavarakontroll",
    )
    extraction_state = models.CharField(
        max_length=32,
        choices=ExtractionState.choices,
        default=ExtractionState.PENDING,
        verbose_name="teksti eraldamine",
    )

    class Meta:
        verbose_name = "dokumendi versioon"
        verbose_name_plural = "dokumendi versioonid"
        ordering = ["document", "version_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "version_number"],
                name="documents_unique_version_per_document",
            ),
            models.CheckConstraint(
                condition=models.Q(sha256__regex=r"^[0-9a-f]{64}$"),
                name="documents_sha256_is_lowercase_hex",
            ),
            models.CheckConstraint(
                condition=models.Q(size_bytes__gte=0),
                name="documents_size_not_negative",
            ),
            # Two versions may never address the same stored object.
            models.UniqueConstraint(
                fields=["storage_key"],
                name="documents_unique_storage_key",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.original_filename} v{self.version_number}"
