"""Haldus → Kasutajad. Every route 404s unless `may_administer_accounts` (docs/adr/0145)."""

from django.urls import path

from app.accounts import admin_views

urlpatterns = [
    path("", admin_views.user_list, name="list"),
    path("uus/", admin_views.user_create, name="create"),
    path("<uuid:pk>/", admin_views.user_detail, name="detail"),
    path("<uuid:pk>/link/", admin_views.send_link, name="send_link"),
    path("<uuid:pk>/tuhista-kutse/", admin_views.cancel_invitation, name="cancel_invitation"),
    path("<uuid:pk>/lulita-valja/", admin_views.deactivate, name="deactivate"),
    path("<uuid:pk>/lulita-sisse/", admin_views.reactivate, name="reactivate"),
    path("<uuid:pk>/teine-tegur/", admin_views.reset_second_factor, name="reset_second_factor"),
    path("<uuid:pk>/aadress/", admin_views.change_email, name="change_email"),
]
