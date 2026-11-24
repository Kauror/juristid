from django.urls import path
from django.views.generic import RedirectView

from app.register_watch import candidate_views, views
from app.riigikogu import views as riigikogu_views

app_name = "register_watch"

#: «Monitooring» (ADR 0219): the head's tab — new drafts, the Government's agenda and the
#: registers' new documents. Mounted at /monitooring/; /arengud/ redirects here.
urlpatterns = [
    path("", RedirectView.as_view(pattern_name="register_watch:candidates"), name="home"),
    path("eelnoud/", candidate_views.candidate_list, name="candidates"),
    path("eelnoud/<uuid:pk>/", candidate_views.decide, name="candidate_decide"),
    path("nahtavus/", candidate_views.audience, name="candidate_audience"),
    path("valitsus/", candidate_views.agenda_list, name="agendas"),
    path("valitsus/<uuid:pk>/", candidate_views.agenda_decide, name="agenda_decide"),
    # Monitooring → Riigikogu (docs/adr/0224).
    path("riigikogu/", riigikogu_views.monitooring, name="riigikogu"),
    path("riigikogu/kutse/<uuid:pk>/", riigikogu_views.decide, name="riigikogu_decide"),
    path("riigikogu/teema/<uuid:pk>/seo/", riigikogu_views.link_bill, name="riigikogu_link"),
    path("riigikogu/seos/<uuid:pk>/lopeta/", riigikogu_views.unlink_bill, name="riigikogu_unlink"),
    path(
        "riigikogu/analuus/<uuid:pk>/<int:index>/toovoit/",
        riigikogu_views.propose_victory,
        name="riigikogu_victory",
    ),
    path("registrid/", views.review, name="review"),
    path("registrid/<uuid:pk>/", views.decide, name="decide"),
    path("registrid/teema/<uuid:pk>/jata/", views.dismiss_all, name="dismiss_all"),
    path("registrid/teema/<uuid:pk>/otsing/", views.phrase, name="phrase"),
]
