from django.urls import path

from app.submissions import views

urlpatterns = [
    path("teema/<uuid:matter_id>/uus/", views.create, name="create"),
    path("<uuid:pk>/toend/", views.attach_evidence, name="attach_evidence"),
    path("<uuid:pk>/saada/", views.mark_sent, name="mark_sent"),
    path("<uuid:pk>/tagasi/", views.withdraw, name="withdraw"),
]
