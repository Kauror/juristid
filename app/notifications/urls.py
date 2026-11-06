from django.urls import path

from app.notifications import views

app_name = "notifications"

urlpatterns = [
    path("", views.reminder_list, name="list"),
    path("kell/", views.bell_count, name="bell"),
    # The bell's panel and its further pages (UI-01): GETs that mark nothing.
    path("paneel/", views.panel, name="panel"),
    # The Minu asjad block, redrawn after an answer given elsewhere (UI-01).
    path("plokk/", views.block, name="block"),
    # «Märgi kõik loetuks», up to the boundary the panel or page was drawn with.
    path("koik-loetuks/", views.mark_all_read, name="mark_all_read"),
    path("seaded/", views.notification_settings, name="settings"),
    path("seaded/taasta/", views.reset_settings, name="reset_settings"),
    # «Kontrollitud» (`/<pk>/kontrollitud/`) was retired by the owner on
    # 2026-10-30: a stale POST there is a 404 and writes nothing.
    # «Sulge…» (`/<pk>/sulge/`) and «Peida» (`/teade/<id>/peida/`) were retired
    # by the owner on 2026-11-02: a stale POST there is a 404 and writes nothing.
    path("<uuid:pk>/edasi/", views.defer, name="defer"),
    # The reader's own receipt on a commencement prompt — not an answer.
    path("<uuid:pk>/loetud/", views.mark_reminder_read, name="reminder_read"),
    path("<uuid:pk>/lugemata/", views.mark_reminder_unread, name="reminder_unread"),
    # A personal deadline notification's own presentation state (docs/adr/0198).
    path("teade/<uuid:notification_id>/loetud/", views.mark_read, name="read"),
    path("teade/<uuid:notification_id>/lugemata/", views.mark_unread, name="unread"),
    # «Uus asi» read and unread without opening the Teema (ADR 0051, UI-01).
    path("uus-asi/<uuid:notice_id>/loetud/", views.mark_assignment_read, name="assignment_read"),
    path(
        "uus-asi/<uuid:notice_id>/lugemata/",
        views.mark_assignment_unread,
        name="assignment_unread",
    ),
]
