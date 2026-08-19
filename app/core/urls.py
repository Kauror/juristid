from django.urls import path

from app.core import views

urlpatterns = [
    path("", views.home, name="home"),
    path("healthz", views.healthz, name="healthz"),
    path("favicon.ico", views.favicon, name="favicon"),
    path("disainisusteem/", views.design_tokens, name="design_tokens"),
]
