"""Sign-in surfaces, one set per authentication mode.

`/varav/` and `/kasutaja/` exist only while `AUTH_MODE=shared_gate`; the
personal sign-in pages below them exist only while `AUTH_MODE=local_password`
(docs/adr/0145). Every view 404s in any other mode rather than being
conditionally routed, so a mode change cannot leave a live URL behind that
nothing is watching — and a mode that is not running has no pages to probe.
"""

from django.urls import path

from app.accounts import local_views, views

urlpatterns = [
    path("arendus-sisselogimine/", views.dev_login, name="dev_login"),
    path("varav/", views.gate, name="shared_gate"),
    path("kasutaja/", views.choose_persona, name="choose_persona"),
    path("kasutaja/vaheta/", views.act_as, name="act_as"),
    path("valju/", views.sign_out, name="sign_out"),
    # -- personal sign-in (local_password only) -----------------------------
    path("sisene/", local_views.sign_in, name="sign_in"),
    path("sisene/kood/", local_views.sign_in_second_factor, name="sign_in_second_factor"),
    path("aktiveeri/", local_views.activate, name="activate"),
    path("parool/unustasin/", local_views.forgot_password, name="forgot_password"),
    path("parool/taasta/", local_views.reset_password, name="reset_password"),
    path("parool/muuda/", local_views.change_password, name="change_password"),
    path("profiil/", local_views.profile, name="profile"),
    path("turvalisus/", local_views.security, name="security"),
    path("turvalisus/seadista/", local_views.security_enrol, name="security_enrol"),
    path(
        "turvalisus/taastekoodid/",
        local_views.security_recovery_codes,
        name="security_recovery_codes",
    ),
    path("turvalisus/eemalda/", local_views.security_remove, name="security_remove"),
    path("kinnita/", local_views.reauthenticate, name="reauthenticate"),
]
