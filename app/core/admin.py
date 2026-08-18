"""A deliberately thin admin.

Stage 0 exposes reference data and provenance so a developer can inspect the
schema. Product screens are Stage-1 work and are not built here.
"""

from __future__ import annotations

from django.contrib import admin

from app.accounts.models import BreakGlassGrant, User
from app.audit.models import ChangeEvent, SecurityAuditEvent
from app.documents.models import Document, DocumentVersion
from app.legacy_import.models import ImportBatch, MatterSourceReference
from app.matters.models import Matter, TagAssignment
from app.organisations.models import Organisation, OrganisationAlias
from app.taxonomy.models import PolicyArea, Tag, TagAlias
from app.workflow.models import LegacyStatusMapping, StageVocabulary


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("display_name", "upn", "role", "is_active", "is_synthetic")
    list_filter = ("role", "is_active", "is_synthetic", "is_staff")
    search_fields = ("display_name", "upn", "email")
    readonly_fields = ("entra_object_id", "created_at", "updated_at", "last_login")


@admin.register(BreakGlassGrant)
class BreakGlassGrantAdmin(admin.ModelAdmin):
    list_display = ("user", "granted_by", "starts_at", "expires_at", "revoked_at")
    list_filter = ("revoked_at",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(StageVocabulary)
class StageVocabularyAdmin(admin.ModelAdmin):
    list_display = ("label_et", "key", "is_active", "is_provisional", "sort_order")
    list_filter = ("is_active", "is_provisional")


@admin.register(LegacyStatusMapping)
class LegacyStatusMappingAdmin(admin.ModelAdmin):
    list_display = ("raw_label", "stage", "disposition", "source_era", "reviewed_by")
    readonly_fields = ("created_at", "updated_at")


@admin.register(PolicyArea)
class PolicyAreaAdmin(admin.ModelAdmin):
    list_display = ("name_et", "key", "is_active", "sort_order")


class TagAliasInline(admin.TabularInline):
    model = TagAlias
    extra = 0


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name_et", "key", "is_active", "merged_into")
    list_filter = ("is_active",)
    inlines = [TagAliasInline]


class OrganisationAliasInline(admin.TabularInline):
    model = OrganisationAlias
    extra = 0


@admin.register(Organisation)
class OrganisationAdmin(admin.ModelAdmin):
    list_display = ("name", "organisation_type", "registry_code", "valid_from", "valid_to")
    list_filter = ("organisation_type",)
    search_fields = ("name", "normalized_name", "registry_code")
    inlines = [OrganisationAliasInline]


@admin.register(Matter)
class MatterAdmin(admin.ModelAdmin):
    list_display = ("__str__", "record_mode", "origin", "owner", "is_open", "visibility")
    list_filter = ("record_mode", "origin", "is_open", "visibility", "track")
    search_fields = ("title", "reference_year", "reference_number")
    readonly_fields = ("created_at", "updated_at")


@admin.register(TagAssignment)
class TagAssignmentAdmin(admin.ModelAdmin):
    list_display = ("matter", "tag", "source", "confirmed_by", "confirmed_at")
    list_filter = ("source",)


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "matter", "role", "effective_visibility", "legal_hold")
    list_filter = ("role", "effective_visibility", "legal_hold", "retention_class")
    readonly_fields = ("effective_visibility", "created_at", "updated_at")


@admin.register(DocumentVersion)
class DocumentVersionAdmin(admin.ModelAdmin):
    list_display = ("original_filename", "document", "version_number", "size_bytes", "sha256")
    list_filter = ("malware_scan_state", "extraction_state")
    readonly_fields = tuple(
        field.name for field in DocumentVersion._meta.fields if field.name != "id"
    )

    def has_change_permission(self, request: object, obj: object = None) -> bool:
        return False

    def has_delete_permission(self, request: object, obj: object = None) -> bool:
        return False


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request: object) -> bool:
        return False

    def has_change_permission(self, request: object, obj: object = None) -> bool:
        return False

    def has_delete_permission(self, request: object, obj: object = None) -> bool:
        return False


@admin.register(ChangeEvent)
class ChangeEventAdmin(ReadOnlyAdmin):
    list_display = ("occurred_at", "event_type", "matter", "actor")
    list_filter = ("event_type",)


@admin.register(SecurityAuditEvent)
class SecurityAuditEventAdmin(ReadOnlyAdmin):
    list_display = ("occurred_at", "event_type", "actor", "succeeded")
    list_filter = ("event_type", "succeeded")


@admin.register(ImportBatch)
class ImportBatchAdmin(ReadOnlyAdmin):
    list_display = ("source_system", "started_at", "finished_at", "reconciliation_status")


@admin.register(MatterSourceReference)
class MatterSourceReferenceAdmin(ReadOnlyAdmin):
    list_display = ("matter", "source_system", "source_sheet", "source_row_number", "match_method")
    list_filter = ("match_method", "conflict_state")
