"""Routes for the structured Matter facts.

Estonian paths, like the rest of the product. The three generated views live at
the top level because they are department destinations in their own right; the
write surfaces sit under the Matter they belong to, so a bookmarked form always
carries the Matter it is about.
"""

from django.urls import path

from app.intelligence import views

urlpatterns = [
    # -- generated department views ---------------------------------------
    path("olulised-tahtajad/", views.important_dates, name="important_dates"),
    path("joustuvad-aktid/", views.effective_dates, name="effective_dates"),
    path("toovoidud/", views.work_victories, name="work_victories"),
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
