from django.urls import path

from app.register_watch import candidate_views, views

app_name = "register_watch"

urlpatterns = [
    path("", views.review, name="review"),
    path("<uuid:pk>/", views.decide, name="decide"),
    path("teema/<uuid:pk>/otsing/", views.phrase, name="phrase"),
    path("uued-eelnoud/", candidate_views.candidate_list, name="candidates"),
    path("uued-eelnoud/<uuid:pk>/", candidate_views.decide, name="candidate_decide"),
    path("uued-eelnoud/nahtavus/", candidate_views.audience, name="candidate_audience"),
]
