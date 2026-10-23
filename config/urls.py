from django.contrib import admin
from django.urls import include, path

from app.accounts.admin_sign_in import admin_sign_in

# `/admin/login/` is the admin site's own login view, replaced rather than
# shadowed by a second route: outside AUTH_MODE=none the admin is reached
# through the mode's own sign-in, never a second, unthrottled password check
# (app/accounts/admin_sign_in.py, AUD-051 AU-2). Set before `admin.site.urls`
# is read, which is when the admin builds its URL patterns.
admin.site.login = admin_sign_in  # type: ignore[method-assign]

urlpatterns = [
    path("", include(("app.core.urls", "core"), namespace="core")),
    path("", include(("app.matters.urls", "matters"), namespace="matters")),
    path("otsing/", include(("app.search.urls", "search"), namespace="search")),
    path("arvamused/", include(("app.submissions.urls", "submissions"), namespace="submissions")),
    path("dokumendid/", include(("app.documents.urls", "documents"), namespace="documents")),
    path(
        "",
        include(("app.legacy_import.urls", "legacy_import"), namespace="legacy_import"),
    ),
    path(
        "statistika/",
        include(("app.reporting.urls", "reporting"), namespace="reporting"),
    ),
    # Structured Matter facts. Mounted at the root because Olulised tähtajad,
    # Jõustuvad aktid and Töövõidud are department destinations of their own;
    # the Matter-scoped write routes inside it are exact paths and cannot
    # shadow anything app.matters already claims.
    path("", include(("app.intelligence.urls", "intelligence"), namespace="intelligence")),
    # Seotud materjalid. Also at the root: every route in it is an exact
    # Matter-scoped path under `teemad/<uuid>/seotud/`, which app.matters does
    # not claim, and keeping the app's routes with the app keeps the feature
    # out of the Matter views it decorates (docs/adr/0062).
    path(
        "",
        include(("app.related_materials.urls", "related_materials"), namespace="related_materials"),
    ),
    path("konto/", include(("app.accounts.urls", "accounts"), namespace="accounts")),
    # Haldus → Kasutajad. Under `/haldus/` with the other internal tooling, and
    # 404 for everybody without a verified administrator: a personal sign-in
    # under `local_password`, or the administrator session under the shared
    # gate (docs/adr/0145, docs/adr/0186).
    path(
        "haldus/kasutajad/",
        include(("app.accounts.admin_urls", "account_admin"), namespace="account_admin"),
    ),
    # Haldus → Klassifikaatorid: the institution catalogue and the tag
    # vocabulary, behind `reference.manage` (docs/adr/0187).
    path(
        "haldus/klassifikaatorid/",
        include(("app.reference_data.urls", "reference_data"), namespace="reference_data"),
    ),
    # The Haldus shell: landing, Osakonna töökorraldus, Süsteemi seis and the
    # verified administrator session's pages. Exact paths only (docs/adr/0186).
    path("haldus/", include(("app.haldus.urls", "haldus"), namespace="haldus")),
    path(
        "organisatsioonid/",
        include(("app.organisations.urls", "organisations"), namespace="organisations"),
    ),
    # The read-only MCP server's authorization server: RFC 8414 metadata, the
    # OAuth endpoints and the authenticator enrolment page. Exact paths that no
    # other app claims; every one is a 404 unless MCP_ENABLED (docs/adr/0185).
    path("", include(("app.mcp_access.urls", "mcp_access"), namespace="mcp_access")),
    path(
        "teavitused/",
        include(("app.notifications.urls", "notifications"), namespace="notifications"),
    ),
    path("admin/", admin.site.urls),
]
