from django.urls import path

from app.reference_data import views

urlpatterns = [
    path("", views.index, name="index"),
    path("organisatsioonid/", views.organisations, name="organisations"),
    path("organisatsioonid/uus/", views.organisation_create, name="organisation_create"),
    path(
        "organisatsioonid/kattuvused/",
        views.organisation_duplicates,
        name="organisation_duplicates",
    ),
    path("organisatsioonid/<uuid:pk>/", views.organisation, name="organisation"),
    path(
        "organisatsioonid/<uuid:pk>/nimi/",
        views.organisation_rename,
        name="organisation_rename",
    ),
    path(
        "organisatsioonid/<uuid:pk>/nimekujud/",
        views.organisation_alias_add,
        name="organisation_alias_add",
    ),
    path(
        "organisatsioonid/<uuid:pk>/nimekujud/<uuid:alias_pk>/eemalda/",
        views.organisation_alias_remove,
        name="organisation_alias_remove",
    ),
    path("sildid/", views.tags, name="tags"),
    path("sildid/uus/", views.tag_create, name="tag_create"),
    path("sildid/<uuid:pk>/", views.tag, name="tag"),
    path("sildid/<uuid:pk>/muuda/", views.tag_edit, name="tag_edit"),
    path("sildid/<uuid:pk>/nimekujud/", views.tag_alias_add, name="tag_alias_add"),
    path(
        "sildid/<uuid:pk>/nimekujud/<uuid:alias_pk>/eemalda/",
        views.tag_alias_remove,
        name="tag_alias_remove",
    ),
    path("sildid/<uuid:pk>/liida/", views.tag_merge, name="tag_merge"),
    path("sildid/<uuid:pk>/seis/", views.tag_status, name="tag_status"),
    path("<slug:key>/", views.controlled_vocabulary, name="vocabulary"),
]
