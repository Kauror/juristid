from django.urls import path
from django.views.generic import RedirectView

from app.register_watch import candidate_views, views

app_name = "register_watch"

#: «Monitooring» (ADR 0219): the head's tab — new drafts, the Government's agenda and the
#: registers' new documents. Mounted at /monitooring/; /arengud/ redirects here.
urlpatterns = [
    path("", RedirectView.as_view(pattern_name="register_watch:candidates"), name="home"),
    path("eelnoud/", candidate_views.candidate_list, name="candidates"),
    path("eelnoud/<uuid:pk>/", candidate_views.decide, name="candidate_decide"),
    path("nahtavus/", candidate_views.audience, name="candidate_audience"),
    path("valitsus/", candidate_views.agenda_list, name="agendas"),
    path("registrid/", views.review, name="review"),
    path("registrid/<uuid:pk>/", views.decide, name="decide"),
    path("registrid/teema/<uuid:pk>/otsing/", views.phrase, name="phrase"),
]
