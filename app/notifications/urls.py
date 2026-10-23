from django.urls import path

from app.notifications import views

app_name = "notifications"

urlpatterns = [
    path("", views.reminder_list, name="list"),
    path("kell/", views.bell_count, name="bell"),
    path("<uuid:pk>/kontrollitud/", views.acknowledge, name="acknowledge"),
    path("<uuid:pk>/edasi/", views.defer, name="defer"),
    path("<uuid:pk>/sulge/", views.dismiss, name="dismiss"),
]
