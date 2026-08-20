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
    path("konto/", include(("app.accounts.urls", "accounts"), namespace="accounts")),
    path(
        "organisatsioonid/",
        include(("app.organisations.urls", "organisations"), namespace="organisations"),
    ),
    path("admin/", admin.site.urls),
]
