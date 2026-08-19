from django.urls import path

from app.organisations import views

urlpatterns = [
    path("kiirlisa/", views.quick_create, name="quick_create"),
]
