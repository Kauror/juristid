from django.urls import path
from django.views.generic import RedirectView

from app.matters import department_views, views

urlpatterns = [
    # The department page. One route where there were two: `/ulevaade/` and
    # `/osakonna-too/` answered the same question — «kus osakond seisab» — and
    # printed several of the same numbers twice (docs/adr/0049).
    path("osakond/", department_views.department, name="department"),
    # The name Ülevaade's route carried, resolving to the page that replaced it.
    #
    # A second name on the canonical path rather than on the compatibility
    # redirect below, and deliberately: `app/core/views.py::home` and the
    # sign-in redirect in `app/accounts/views.py` both reverse `matters:overview`
    # to decide where somebody lands, and those two lines belong to the parallel
    # branch that is moving the root to Minu asjad. Pointing the name here means
    # neither file had to be touched and neither now sends a reader through a
    # 301 they do not need. Resolution is unaffected — `/osakond/` resolves to
    # the entry above, which is the one that names the view.
    path("osakond/", department_views.department, name="overview"),
    # Both old addresses, permanently, with their query strings. Every bookmark,
    # every pasted link and every `?vaade=`, `?periood=` or custom date range
    # somebody saved still opens the page it described: `?vaade=valdkonniti` is
    # a scope of the new route, and the Tehtud period parameters are read there
    # unchanged. One hop each — they redirect to the canonical route rather than
    # to each other.
    path(
        "ulevaade/",
        RedirectView.as_view(pattern_name="matters:department", permanent=True, query_string=True),
        name="overview_legacy",
    ),
    path(
        "osakonna-too/",
        RedirectView.as_view(pattern_name="matters:department", permanent=True, query_string=True),
        name="department_work",
    ),
    path("minu-asjad/", views.my_work, name="my_work"),
    # The address the page had until the v2 rebuild renamed the surface.
    # A permanent redirect rather than a second view: every bookmark, every
    # message somebody pasted and every historical link still lands on the
    # page, and the route name did not change, so nothing in the codebase had
    # to be rewritten to keep pointing at it (03-BACKEND §4).
    path(
        "minu-too/",
        RedirectView.as_view(pattern_name="matters:my_work", permanent=True, query_string=True),
        name="my_work_legacy",
    ),
    # One person's desk. Keyed on the id and never on a display name: a name
    # changes and is not unique, and a URL that carried one would break the day
    # somebody married (03-BACKEND §4).
    path("inimesed/<uuid:pk>/asjad/", views.person_work, name="person_work"),
    # The scratchpad's autosave. `request.user` only — there is deliberately no
    # subject in this path (03-BACKEND §2).
    path("minu-asjad/markmed/", views.save_scratchpad, name="save_scratchpad"),
    # Opening a newly assigned Teema *from* «Uus asi». Keyed on the notice and
    # never on the Matter, because the notice is the thing being acknowledged
    # and it is the recipient's own: the view looks it up by
    # `(id, recipient=request.user)`, so there is no id in this path that would
    # let one person clear another's queue. A POST, so an ordinary GET of the
    # Matter — by any route, in any workflow — cannot mark it read
    # (docs/adr/0051).
    path(
        "minu-asjad/uus/<uuid:notice_id>/ava/",
        views.open_assignment_notice,
        name="open_assignment_notice",
    ),
    # One click to done, from the list rather than from the Matter. Its own
    # route because it ends where the reader was rather than on a Matter page,
    # and it calls the same service the Matter page's «✓ Tehtud» calls
    # (design handoff 1e).
    path(
        "minu-too/valmis/<uuid:action_id>/",
        views.complete_work_item,
        name="complete_work_item",
    ),
    path("saabunud/", views.inbox, name="inbox"),
    path("saabunud/lisa/", views.intake, name="intake"),
    path("teemad/", views.matter_list, name="matter_list"),
    path("teemad/uus/", views.matter_create, name="matter_create"),
    # The searchable institution control on Tapsem otsing. A fragment route
    # because it swaps one field; the register itself deliberately does not
    # have one (app/matters/views.py, `_wants_fragment`).
    path("teemad/asutused/", views.organisation_choices, name="organisation_choices"),
    # Assigning an owner from a register row. Its own route because it returns
    # the reader to the list rather than to a Matter page: the whole point of
    # the control is that triaging four unassigned files does not cost four
    # round trips through four Matters (design handoff 2d).
    path("teemad/<uuid:pk>/vastutaja/", views.assign_owner, name="assign_owner"),
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
    # The same edit page with what the documents say beside it. Its own route
    # and GET-only: it computes and shows, and the form on it posts to
    # `matter_edit` like the plain one, so there is one write path
    # (app/matters/intake_suggestions, docs/adr/0060).
    path(
        "teemad/<uuid:pk>/muuda/dokumendist/",
        views.matter_edit_assisted,
        name="matter_edit_assisted",
    ),
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
