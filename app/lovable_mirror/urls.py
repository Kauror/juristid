from django.urls import path

from app.lovable_mirror import views

urlpatterns = [
    path("", views.preview, name="preview"),
]
