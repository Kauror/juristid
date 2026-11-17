from django.urls import path

from app.register_watch import views

app_name = "register_watch"

urlpatterns = [
    path("", views.review, name="review"),
    path("<uuid:pk>/", views.decide, name="decide"),
]
