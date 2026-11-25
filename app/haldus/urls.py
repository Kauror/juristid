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
from app.haldus import (
    data_sync_views,
    letters_views,
    onenote_holds,
    register_watch_views,
    views,
)

urlpatterns = [
    path("", views.index, name="index"),
    # -- Isiklikud seaded → Kuvamine (docs/adr/0221) --
    path("seaded/", views.settings_page, name="settings"),
    path("osakond/", views.osakond, name="osakond"),
    path("susteem/", views.susteem, name="susteem"),
    path("lovable/", views.lovable, name="lovable"),
    # -- Ajaloolised kirjad: Postipoiss letters and their Teemad (docs/adr/0216) --
    path("ajaloolised-kirjad/", letters_views.letters, name="letters"),
    path("ajaloolised-kirjad/<uuid:pk>/", letters_views.decide, name="letters_decide"),
    path("lovable/kontrolli/", views.lovable_check, name="lovable_check"),
    # -- Registrite jälgimine: the nightly register watch's log (ADR 0217 §8) --
    path("registrid/", register_watch_views.runs, name="register_watch"),
    path("registrid/allikad/", register_watch_views.sources, name="register_watch_sources"),
    path("registrid/<uuid:pk>/", register_watch_views.run, name="register_watch_run"),
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
    path(
        "andmete-sunkroonimine/ootel-sulgemine/",
        data_sync_views.release_hold,
        name="data_sync_release_hold",
    ),
    path("andmete-sunkroonimine/ajalugu/", data_sync_views.history, name="data_sync_history"),
    path("andmete-sunkroonimine/nimed/", data_sync_views.owner_names, name="data_sync_names"),
    # -- Integratsioonid → OneNote: its own overview since UX-002 (docs/adr/0221) --
    path("andmete-sunkroonimine/onenote/", data_sync_views.onenote, name="onenote"),
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
    path("andmete-sunkroonimine/onenote/ootel/", onenote_holds.holds, name="onenote_holds"),
    # -- the verified administrator session (shared gate only, docs/adr/0186) --
    path("sisene/", admin_session_views.sign_in, name="sign_in"),
    path("sisene/kood/", admin_session_views.sign_in_code, name="sign_in_code"),
    path("sisene/seadista/", admin_session_views.enrol, name="enrol"),
    path("kinnita/", admin_session_views.reauthenticate, name="reauthenticate"),
    path("lopeta/", admin_session_views.sign_out, name="sign_out"),
]
