from django.urls import path

from app.matters import department_views, views

urlpatterns = [
    path("ulevaade/", views.overview, name="overview"),
    path("minu-too/", views.my_work, name="my_work"),
    path("osakonna-too/", department_views.department_work, name="department_work"),
    path("saabunud/", views.inbox, name="inbox"),
    path("saabunud/lisa/", views.intake, name="intake"),
    path("teemad/", views.matter_list, name="matter_list"),
    path("teemad/uus/", views.matter_create, name="matter_create"),
    # The searchable institution control on Tapsem otsing. A fragment route
    # because it swaps one field; the register itself deliberately does not
    # have one (app/matters/views.py, `_wants_fragment`).
    path("teemad/asutused/", views.organisation_choices, name="organisation_choices"),
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
    path("teemad/<uuid:pk>/andmeklass/", views.set_data_class, name="set_data_class"),
    path("teemad/<uuid:pk>/ajalugu/", views.timeline_page, name="timeline_page"),
    # Full-page posts
    path("teemad/<uuid:pk>/seisukoht/salvesta/", views.update_position, name="update_position"),
    path("teemad/<uuid:pk>/sulge/", views.close, name="close"),
    path("teemad/<uuid:pk>/taasava/", views.reopen, name="reopen"),
]
