from django.urls import path

from app.accounts import views

urlpatterns = [
    path("arendus-sisselogimine/", views.dev_login, name="dev_login"),
    path("valju/", views.sign_out, name="sign_out"),
]
