"""Historical corpus routes.

The lawyer-facing source page sits under `/ajalugu/`; the reconciliation queue
sits under `/haldus/` with the rest of the internal tooling, because 535 pending
migration decisions are not something a lawyer should have to navigate past
(Stage-2D brief 39).
"""

from django.urls import path

from app.legacy_import import archive_views as archive
from app.legacy_import import historical_views as views
from app.legacy_import import opinion_views

urlpatterns = [
    path(
        "haldus/arvamuste-ulevaatus/",
        opinion_views.opinion_queue,
        name="opinion_queue",
    ),
    path(
        "haldus/arvamuste-ulevaatus/<uuid:pk>/",
        opinion_views.opinion_decide,
        name="opinion_decide",
    ),
    path(
        "haldus/arvamuste-arhiiv/",
        archive.archive_browse,
        name="opinion_archive_browse",
    ),
    path(
        "haldus/arvamuste-arhiiv/<uuid:pk>/",
        archive.archive_detail,
        name="opinion_archive_detail",
    ),
    path(
        "haldus/arvamuste-arhiiv/<uuid:pk>/seos/",
        archive.archive_link,
        name="opinion_archive_link",
    ),
    # The bytes. No storage key in the path — the route names the row and the
    # key is read from it, so a crafted URL cannot address an object the
    # archive does not own.
    path(
        "haldus/arvamuste-arhiiv/<uuid:pk>/fail/",
        archive.archive_file,
        name="opinion_archive_file",
    ),
    path("ajalugu/<uuid:pk>/", views.source_page, name="source_page"),
    path("ajalugu/<uuid:pk>/lahtefail/", views.source_xml, name="source_xml"),
    path("haldus/ajaloo-ulevaatus/", views.review_queue, name="review_queue"),
    path("haldus/ajaloo-ulevaatus/<uuid:pk>/", views.review_decide, name="review_decide"),
]
