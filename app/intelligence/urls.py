"""Routes for the structured Matter facts.

Estonian paths, like the rest of the product. The three generated views live at
the top level because they are department destinations in their own right; the
write surfaces sit under the Matter they belong to, so a bookmarked form always
carries the Matter it is about.
"""

from collections.abc import Callable
from typing import Any

from django.urls import path
from django.views.generic import RedirectView

from app.intelligence import views


def _moved(name: str) -> Callable[..., Any]:
    """The address one of these pages had before the v2 rebuild grouped them.

    Permanent, and carrying the query string, so a bookmarked filter still opens
    the view it named. The route names did not change, so nothing in the
    codebase had to be rewritten to keep pointing at these pages
    (03-BACKEND §4).
    """
    return RedirectView.as_view(pattern_name=name, permanent=True, query_string=True)


urlpatterns = [
    # -- generated department views ---------------------------------------
    #
    # One prefix, because they are one destination with three tabs and the bar
    # offers them as one item (02-EKRAANID §D).
    path("jalgimine/tahtajad/", views.important_dates, name="important_dates"),
    path("jalgimine/joustumised/", views.effective_dates, name="effective_dates"),
    path("jalgimine/toovoidud/", views.work_victories, name="work_victories"),
    path(
        "olulised-tahtajad/",
        _moved("intelligence:important_dates"),
        name="important_dates_legacy",
    ),
    path(
        "joustuvad-aktid/",
        _moved("intelligence:effective_dates"),
        name="effective_dates_legacy",
    ),
    path("toovoidud/", _moved("intelligence:work_victories"), name="work_victories_legacy"),
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
