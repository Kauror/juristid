from django.urls import path

from app.notifications import views

app_name = "notifications"

urlpatterns = [
    path("", views.reminder_list, name="list"),
    path("kell/", views.bell_count, name="bell"),
    # «Kontrollitud» (`/<pk>/kontrollitud/`) was retired by the owner on
    # 2026-10-30: a stale POST there is a 404 and writes nothing.
    path("<uuid:pk>/edasi/", views.defer, name="defer"),
    path("<uuid:pk>/sulge/", views.dismiss, name="dismiss"),
]
