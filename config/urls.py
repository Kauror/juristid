from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("", include(("app.core.urls", "core"), namespace="core")),
    path("konto/", include(("app.accounts.urls", "accounts"), namespace="accounts")),
    path("admin/", admin.site.urls),
]
