"""Routes for the structured Matter facts.

Estonian paths, like the rest of the product. The write surfaces sit under the
Matter they belong to, so a bookmarked form always carries the Matter it is
about.

The three generated reading pages that used to live at the top level are gone.
Their addresses are not: every one of them still resolves, and each lands on the
destination that now answers the question it was opened for
(:mod:`app.intelligence.redirects`, docs/adr/0067).
"""

from django.urls import path

from app.intelligence import redirects, views

urlpatterns = [
    # -- the retired reading pages, and where each one goes ----------------
    #
    # Kept as named routes so that nothing in the codebase, in a bookmark or in
    # a pasted message resolves to a 404. The route names are the ones the
    # three views had, which is why no caller had to be rewritten to keep
    # pointing at "the deadlines page" — it simply resolves somewhere else now.
    path("jalgimine/tahtajad/", redirects.important_dates, name="important_dates"),
    path("jalgimine/joustumised/", redirects.effective_dates, name="effective_dates"),
    path("jalgimine/toovoidud/", redirects.work_victories, name="work_victories"),
    # The addresses these pages had before the v2 rebuild grouped them under
    # `/jalgimine/`. Pointed at the new destination directly rather than at the
    # `/jalgimine/` route, so an old bookmark costs one hop rather than two —
    # and so that no chain of redirects exists to reason about.
    path(
        "olulised-tahtajad/",
        redirects.important_dates,
        name="important_dates_legacy",
    ),
    path(
        "joustuvad-aktid/",
        redirects.effective_dates,
        name="effective_dates_legacy",
    ),
    path("toovoidud/", redirects.work_victories, name="work_victories_legacy"),
    # -- Olulised tähtajad, on one Matter ---------------------------------
    path(
        "teemad/<uuid:matter_id>/olulised-tahtajad/lisa/",
        views.add_important_date,
        name="add_important_date",
    ),
    path(
        "teemad/<uuid:matter_id>/olulised-tahtajad/<uuid:pk>/muuda/",
        views.edit_important_date,
        name="edit_important_date",
    ),
    path(
        "teemad/<uuid:matter_id>/olulised-tahtajad/<uuid:pk>/tuhista/",
        views.cancel_important_date,
        name="cancel_important_date",
    ),
    # -- Jõustumine, on one Matter ----------------------------------------
    path(
        "teemad/<uuid:matter_id>/joustumine/lisa/",
        views.add_effective_date,
        name="add_effective_date",
    ),
    path(
        "teemad/<uuid:matter_id>/joustumine/<uuid:pk>/muuda/",
        views.edit_effective_date,
        name="edit_effective_date",
    ),
    path(
        "teemad/<uuid:matter_id>/joustumine/<uuid:pk>/tuhista/",
        views.cancel_effective_date,
        name="cancel_effective_date",
    ),
    # -- Töövõidud, on one Matter -----------------------------------------
    path(
        "teemad/<uuid:matter_id>/toovoidud/lisa/",
        views.add_work_victory,
        name="add_work_victory",
    ),
    path(
        "teemad/<uuid:matter_id>/toovoidud/<uuid:pk>/muuda/",
        views.edit_work_victory,
        name="edit_work_victory",
    ),
    path(
        "teemad/<uuid:matter_id>/toovoidud/<uuid:pk>/kinnita/",
        views.confirm_work_victory,
        name="confirm_work_victory",
    ),
    path(
        "teemad/<uuid:matter_id>/toovoidud/<uuid:pk>/ei-realiseerunud/",
        views.reject_work_victory,
        name="reject_work_victory",
    ),
]
