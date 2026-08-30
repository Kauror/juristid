from django.urls import path

from app.core import views

urlpatterns = [
    path("", views.home, name="home"),
    path("healthz", views.healthz, name="healthz"),
    path("favicon.ico", views.favicon, name="favicon"),
    path("disainisusteem/", views.design_tokens, name="design_tokens"),
    # The v2 rebuild's own worklist. Under `/haldus/` with the other internal
    # tooling and deliberately not on the bar (app/core/development_status.py).
    path("haldus/arendus/", views.development_status, name="development_status"),
]
