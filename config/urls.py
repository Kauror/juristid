from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("", include(("app.core.urls", "core"), namespace="core")),
    path("", include(("app.matters.urls", "matters"), namespace="matters")),
    path("otsing/", include(("app.search.urls", "search"), namespace="search")),
    path("arvamused/", include(("app.submissions.urls", "submissions"), namespace="submissions")),
    path("dokumendid/", include(("app.documents.urls", "documents"), namespace="documents")),
    path("konto/", include(("app.accounts.urls", "accounts"), namespace="accounts")),
    path("admin/", admin.site.urls),
]
