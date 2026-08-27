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
    # `Arvamused` on one Matter. No longer a tab — the Matter has exactly two —
    # but still a destination, reached from the position block and the sent-
    # opinion strip on the main view. It is where a formal Submission is
    # drafted, given its exact evidence, sent and withdrawn: acts that carry
    # recipients, a channel and a reference, and that are not the routine
    # capture the composer exists for (Teema redesign §3, §17, §20).
    path("teemad/<uuid:pk>/seisukoht/", views.matter_position, name="matter_position"),
    path("teemad/<uuid:pk>/dokumendid/", views.matter_documents, name="matter_documents"),
    # `Muuda teemat`. A full page rather than a fragment: it edits the whole
    # record in one transaction, so a partial swap would be describing something
    # the save does not do (app/matters/views.py, `matter_edit`).
    path("teemad/<uuid:pk>/muuda/", views.matter_edit, name="matter_edit"),
    # HTMX surfaces
    path("teemad/<uuid:pk>/sissekanne/", views.compose, name="compose"),
    path("teemad/<uuid:pk>/jargmiseks/", views.set_action, name="set_action"),
    path("teemad/<uuid:pk>/kaasamine/", views.add_engagement_view, name="add_engagement"),
    path(
        "teemad/<uuid:pk>/kaasamine/<uuid:engagement_id>/",
        views.update_engagement_view,
        name="update_engagement",
    ),
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
    # `Lükka edasi`. Its own route rather than a flag on the two above it,
    # because it is one gesture whose meaning depends on the step: a deadline
    # moves by superseding the instruction, and a review date moves by
    # acknowledging the review. The view branches; the caller does not have to
    # know which service it is asking for (design handoff 1c).
    path(
        "teemad/<uuid:pk>/jargmiseks/<uuid:action_id>/lukka/",
        views.defer_action,
        name="defer_action",
    ),
    path("teemad/<uuid:pk>/vali/<str:field>/", views.update_field, name="update_field"),
    path("teemad/<uuid:pk>/luhikokkuvote/", views.update_summary, name="update_summary"),
    path("teemad/<uuid:pk>/markmed/", views.save_note, name="save_note"),
    path(
        "teemad/<uuid:pk>/dokumendid/toodokument/",
        views.add_working_document,
        name="add_working_document",
    ),
    path("teemad/<uuid:pk>/andmeklass/", views.set_data_class, name="set_data_class"),
    path("teemad/<uuid:pk>/ajalugu/", views.timeline_page, name="timeline_page"),
    # Full-page posts
    path("teemad/<uuid:pk>/seisukoht/salvesta/", views.update_position, name="update_position"),
    path("teemad/<uuid:pk>/sulge/", views.close, name="close"),
    path("teemad/<uuid:pk>/taasava/", views.reopen, name="reopen"),
]
