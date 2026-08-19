from django.urls import path

from app.search import views

urlpatterns = [
    path("", views.search_view, name="search"),
]
