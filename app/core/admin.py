"""A deliberately thin admin: reference data to maintain, business data to inspect.

Stage 0 exposed everything here with Django's default add and change forms, so a
developer could look at the schema. The product's own screens and services took
over every business write since, and the forms stayed — a second way to write a
`Matter`, a `Document`, a tag, a break-glass grant or a person's role that skips
`visible_to`, the Matter lock, the domain audit and every invariant the services
keep (ENG-008). Under `shared_gate` nobody reaches it; under any mode that signs
a superuser in, the admin was an unaudited back door over every restricted
Matter.

So the line is drawn by what a model *is*:

* **Reference data** — `Organisation`, `Tag`, `PolicyArea`, the stage vocabulary
  and its legacy mapping — stays editable. Renaming one is a supported admin
  task (docs/adr/0041 §6), none of it is restricted business content, and the
  search-debt signals that follow a rename are unchanged.
* **Business records and their audit** are read-only, and scoped to what the
  person looking may read through the product. Technical administration is not
  business access (docs/adr/0005): a superuser sees a RESTRICTED Matter here
  exactly when `Matter.objects.visible_to` would show it to them — a break-glass
  grant, for instance — and not because the model is registered. Tombstones stay
  listed, because administration is where somebody asks what happened to a
  deleted Matter (docs/adr/0096 §4.2).
* **People**: only the display name and whether the account is active. Roles,
  staff and superuser flags and identities go through `provision_user` and the
  product; a break-glass grant goes through `grant_break_glass`, which caps it
  and audits it — so the admin lists grants and cannot mint one.
"""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.db.models import Q

from app.accounts.models import BreakGlassGrant, User
from app.audit.models import ChangeEvent, SecurityAuditEvent
from app.audit.visibility import scope_change_events
from app.core.authorization import apply as apply_scope
from app.core.authorization import matter_visibility_q, scope_for_user
from app.documents.models import Document, DocumentVersion
from app.legacy_import.models import ImportBatch, MatterSourceReference
from app.matters.models import Matter, TagAssignment
from app.organisations.models import Organisation, OrganisationAlias
from app.taxonomy.models import PolicyArea, Tag, TagAlias
from app.workflow.models import LegacyStatusMapping, StageVocabulary


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request: object) -> bool:
        return False

    def has_change_permission(self, request: object, obj: object = None) -> bool:
        return False

    def has_delete_permission(self, request: object, obj: object = None) -> bool:
        return False


def readable_matters(request: Any) -> Any:
    """Every Matter the person at the admin may read — tombstones included.

    `Matter.objects.visible_to` is the product's rule, and it excludes deleted
    Matters because no business surface may show one. The admin is the one place
    that lists them (docs/adr/0096 §4.2), so this applies the same visibility
    predicate to the unfiltered manager: a tombstone is listed exactly when its
    live Matter would have been readable.
    """
    return apply_scope(
        Matter.all_objects.get_queryset(), matter_visibility_q(scope_for_user(request.user))
    )


#: The two things about an account the admin may change. The display name is
#: a supported rename (docs/adr/0041 §6), and switching an account off is how a
#: departing colleague loses access. Everything else — role, staff and superuser
#: flags, the identity Cloudflare Access matches on — is `provision_user` and the
#: product's own rules, never a checkbox (ENG-008).
USER_ADMIN_EDITABLE = ("display_name", "is_active")


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("display_name", "upn", "role", "is_active", "is_synthetic")
    list_filter = ("role", "is_active", "is_synthetic", "is_staff")
    search_fields = ("display_name", "upn", "email")
    # The password hash is nobody's business on a form; neither mode reads one.
    exclude = ("password",)

    def get_readonly_fields(self, request: Any, obj: Any = None) -> tuple[str, ...]:
        names = [field.name for field in User._meta.get_fields() if field.concrete]
        return tuple(
            name for name in names if name not in USER_ADMIN_EDITABLE and name != "password"
        )

    def has_add_permission(self, request: Any) -> bool:
        # `manage.py provision_user`, which refuses what the product would refuse.
        return False

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        return False


@admin.register(BreakGlassGrant)
class BreakGlassGrantAdmin(ReadOnlyAdmin):
    """Listed, never minted here: `grant_break_glass` caps and audits a grant."""

    list_display = ("user", "granted_by", "starts_at", "expires_at", "revoked_at")
    list_filter = ("revoked_at",)


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
class MatterAdmin(ReadOnlyAdmin):
    list_display = ("__str__", "record_mode", "origin", "owner", "is_open", "visibility")
    list_filter = ("record_mode", "origin", "is_open", "visibility", "track")
    search_fields = ("title", "reference_year", "reference_number")
    # A readable Matter may be superseded by one this person may not read, and
    # the raw field would print that Matter's reference and title.
    exclude = ("superseded_by",)

    def get_queryset(self, request: Any) -> Any:
        """Tombstones included, restricted Matters only for those who may read them.

        `Matter.objects` excludes a deleted Matter from every business surface,
        which is the point of it — but administration is where somebody asks
        *what happened to it*, and a row the audit trail names and the admin
        cannot open is a dead end (docs/adr/0096 §4.2). A window, not a way in:
        nothing here changes a Matter, and deleting one is `app.matters.deletion`.
        """
        return readable_matters(request)


@admin.register(TagAssignment)
class TagAssignmentAdmin(ReadOnlyAdmin):
    list_display = ("matter", "tag", "source", "confirmed_by", "confirmed_at")
    list_filter = ("source",)

    def get_queryset(self, request: Any) -> Any:
        return super().get_queryset(request).filter(matter__in=readable_matters(request))


@admin.register(Document)
class DocumentAdmin(ReadOnlyAdmin):
    # effective_visibility is derived, not stored, so it is displayed but never
    # filtered on directly; filter on the two facts it is derived from.
    list_display = ("title", "matter", "role", "effective_visibility", "legal_hold")
    list_filter = ("role", "visibility_override", "legal_hold", "retention_class")
    list_select_related = ("matter",)

    def get_queryset(self, request: Any) -> Any:
        # A document carries its own override, which may be stricter than its
        # Matter's: the product's rule for documents, not the Matter's.
        return Document.objects.visible_to(request.user)


@admin.register(DocumentVersion)
class DocumentVersionAdmin(ReadOnlyAdmin):
    list_display = ("original_filename", "document", "version_number", "size_bytes", "sha256")
    list_filter = ("extraction_state",)

    def get_queryset(self, request: Any) -> Any:
        # A filename is frequently the most telling thing about a file.
        visible = Document.objects.visible_to(request.user).values("pk")
        return super().get_queryset(request).filter(document__in=visible)


@admin.register(ChangeEvent)
class ChangeEventAdmin(ReadOnlyAdmin):
    list_display = ("occurred_at", "event_type", "matter", "actor")
    list_filter = ("event_type",)

    def get_queryset(self, request: Any) -> Any:
        """The events the product would show this person, on any Matter.

        A row names its Matter and its summary names what changed, so an event
        about a restricted Matter — or about a restricted child of a readable
        one — is as much restricted content as the Matter itself
        (`app.audit.visibility`).
        """
        events = (
            super()
            .get_queryset(request)
            .filter(Q(matter__isnull=True) | Q(matter__in=readable_matters(request)))
        )
        return scope_change_events(events, request.user)


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

    def get_queryset(self, request: Any) -> Any:
        return super().get_queryset(request).filter(matter__in=readable_matters(request))
