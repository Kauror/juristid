"""Haldus: the shell's own routes and the verified administrator session's pages.

Mounted at `/haldus/` (namespace `haldus`). The older routes under the same
prefix stay where they are and keep their names: `/haldus/kasutajad/`
(`account_admin`), `/haldus/arendus/` (`core:development_status`),
`/haldus/ajaloo-ulevaatus/` and the opinion queues (`legacy_import`), and
`/haldus/klassifikaatorid/` (`reference_data`). Every pattern here is exact, so
none of them can shadow those.
"""

from django.urls import path

from app.accounts import admin_session_views
from app.haldus import data_sync_views, views

urlpatterns = [
    path("", views.index, name="index"),
    path("osakond/", views.osakond, name="osakond"),
    path("susteem/", views.susteem, name="susteem"),
    path("lovable/", views.lovable, name="lovable"),
    path("lovable/kontrolli/", views.lovable_check, name="lovable_check"),
    # -- Andmete sünkroonimine (LS-C): Lovable and OneNote, requests for the host --
    path("andmete-sunkroonimine/", data_sync_views.overview, name="data_sync"),
    path("andmete-sunkroonimine/kontrolli/", data_sync_views.check, name="data_sync_check"),
    path("andmete-sunkroonimine/sunkrooni/", data_sync_views.sync_now, name="data_sync_now"),
    path(
        "andmete-sunkroonimine/erinevused/",
        data_sync_views.differences,
        name="data_sync_differences",
    ),
    path(
        "andmete-sunkroonimine/erinevused/otsusta/",
        data_sync_views.resolve,
        name="data_sync_resolve",
    ),
    path("andmete-sunkroonimine/ajalugu/", data_sync_views.history, name="data_sync_history"),
    path(
        "andmete-sunkroonimine/onenote/kontrolli/",
        data_sync_views.onenote_verify,
        name="data_sync_onenote_verify",
    ),
    path(
        "andmete-sunkroonimine/onenote/impordi/",
        data_sync_views.onenote_import,
        name="data_sync_onenote_import",
    ),
    # -- the verified administrator session (shared gate only, docs/adr/0186) --
    path("sisene/", admin_session_views.sign_in, name="sign_in"),
    path("sisene/kood/", admin_session_views.sign_in_code, name="sign_in_code"),
    path("sisene/seadista/", admin_session_views.enrol, name="enrol"),
    path("kinnita/", admin_session_views.reauthenticate, name="reauthenticate"),
    path("lopeta/", admin_session_views.sign_out, name="sign_out"),
]
