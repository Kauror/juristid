from django.contrib import admin
from django.urls import include, path

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
    path("konto/", include(("app.accounts.urls", "accounts"), namespace="accounts")),
    path(
        "organisatsioonid/",
        include(("app.organisations.urls", "organisations"), namespace="organisations"),
    ),
    path("admin/", admin.site.urls),
]
