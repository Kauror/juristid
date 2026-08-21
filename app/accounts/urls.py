"""Sign-in surfaces, one per authentication mode.

`/varav/` and `/kasutaja/` exist only while `AUTH_MODE=shared_gate`; the views
404 in any other mode rather than being conditionally routed, so a mode change
cannot leave a live URL behind that nothing is watching.
"""

from django.urls import path

from app.accounts import views

urlpatterns = [
    path("arendus-sisselogimine/", views.dev_login, name="dev_login"),
    path("varav/", views.gate, name="shared_gate"),
    path("kasutaja/", views.choose_persona, name="choose_persona"),
    path("kasutaja/vaheta/", views.act_as, name="act_as"),
    path("valju/", views.sign_out, name="sign_out"),
]
