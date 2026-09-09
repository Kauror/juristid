from django.urls import path

from app.core import views

urlpatterns = [
    path("", views.home, name="home"),
    path("healthz", views.healthz, name="healthz"),
    path("favicon.ico", views.favicon, name="favicon"),
    path("disainisusteem/", views.design_tokens, name="design_tokens"),
    # What has changed in the application, by day. Reached from the footer
    # beside the revision it belongs to, and from nowhere else: it is something
    # somebody looks up, not a destination in the daily rotation
    # (docs/release-notes/README.md).
    path("uuendused/", views.release_notes, name="release_notes"),
    # The v2 rebuild's own worklist. Under `/haldus/` with the other internal
    # tooling and deliberately not on the bar (app/core/development_status.py).
    path("haldus/arendus/", views.development_status, name="development_status"),
]
