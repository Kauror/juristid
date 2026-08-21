"""Statistika routes.

Estonian paths, like the rest of the product. The two list routes sit under
`/statistika/` rather than beside the register because they exist to serve a
statistic's drill-through: `arvamused` is the product's only list of sent
Submissions, and `materjalid` its only list of historical file occurrences.
Everything that already has a surface — the register, the reconciliation queue,
Minu töö — is linked to rather than reimplemented (Stage-2E brief 39).
"""

from django.urls import path

from app.reporting import views

urlpatterns = [
    path("", views.overview, name="overview"),
    path("teemad/", views.matters, name="matters"),
    path("tegevus/", views.activity, name="activity"),
    path("ajalooline/", views.historical_materials, name="historical"),
    path("andmekvaliteet/", views.data_quality, name="quality"),
    path("arvamused/", views.submissions_list, name="submissions"),
    path("materjalid/", views.materials_list, name="materials"),
    path("definitsioonid/", views.definitions, name="definitions"),
    path("eksport/<slug:slug>.csv", views.export, name="export"),
]
