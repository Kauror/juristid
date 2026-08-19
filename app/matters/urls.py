from django.urls import path

from app.matters import views

urlpatterns = [
    path("ulevaade/", views.overview, name="overview"),
    path("minu-too/", views.my_work, name="my_work"),
    path("saabunud/", views.inbox, name="inbox"),
    path("saabunud/lisa/", views.intake, name="intake"),
    path("teemad/", views.matter_list, name="matter_list"),
    path("teemad/uus/", views.matter_create, name="matter_create"),
    path("teemad/<uuid:pk>/", views.matter_detail, name="matter_detail"),
    path("teemad/<uuid:pk>/seisukoht/", views.matter_position, name="matter_position"),
    path("teemad/<uuid:pk>/dokumendid/", views.matter_documents, name="matter_documents"),
    # HTMX surfaces
    path("teemad/<uuid:pk>/sissekanne/", views.compose, name="compose"),
    path("teemad/<uuid:pk>/jargmiseks/", views.set_action, name="set_action"),
    path(
        "teemad/<uuid:pk>/jargmiseks/<uuid:action_id>/valmis/",
        views.complete_action,
        name="complete_action",
    ),
    path(
        "teemad/<uuid:pk>/jargmiseks/<uuid:action_id>/vaadatud/",
        views.review_action,
        name="review_action",
    ),
    path("teemad/<uuid:pk>/vali/<str:field>/", views.update_field, name="update_field"),
    path("teemad/<uuid:pk>/ajalugu/", views.timeline_page, name="timeline_page"),
    # Full-page posts
    path("teemad/<uuid:pk>/seisukoht/salvesta/", views.update_position, name="update_position"),
    path("teemad/<uuid:pk>/sulge/", views.close, name="close"),
    path("teemad/<uuid:pk>/taasava/", views.reopen, name="reopen"),
]
