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
from app.haldus import views

urlpatterns = [
    path("", views.index, name="index"),
    path("osakond/", views.osakond, name="osakond"),
    path("susteem/", views.susteem, name="susteem"),
    # -- the verified administrator session (shared gate only, docs/adr/0186) --
    path("sisene/", admin_session_views.sign_in, name="sign_in"),
    path("sisene/kood/", admin_session_views.sign_in_code, name="sign_in_code"),
    path("sisene/seadista/", admin_session_views.enrol, name="enrol"),
    path("kinnita/", admin_session_views.reauthenticate, name="reauthenticate"),
    path("lopeta/", admin_session_views.sign_out, name="sign_out"),
]
