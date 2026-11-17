"""Statistika routes.

Estonian paths, like the rest of the product. The two list routes sit under
`/statistika/` rather than beside the register because they exist to serve a
statistic's drill-through: `arvamused` is the product's only list of sent
Submissions, and `materjalid` its only list of historical file occurrences.
Everything that already has a surface — the register, the reconciliation queue,
Minu töö — is linked to rather than reimplemented (Stage-2E brief 39).
"""

from django.urls import path

from app.reporting import classification_views, legacy_views, opinion_views, views
from app.reporting.report_builder import views as report_views

urlpatterns = [
    # The first page since 2026-11-16: Koda's opinions, from Juristid's own
    # records. The counts-and-tables page it replaced there is «Seis».
    path("", opinion_views.dashboard, name="dashboard"),
    path("arvamused-valja/", opinion_views.opinion_rows, name="opinion_rows"),
    # Confirming the proposed sender and Valdkond of each Teema (ADR 0215).
    path("liigitamine/", classification_views.review, name="classification"),
    path(
        "liigitamine/kinnita/", classification_views.accept_page, name="classification_accept_page"
    ),
    path("liigitamine/<uuid:pk>/", classification_views.decide, name="classification_decide"),
    path("seis/", views.overview, name="overview"),
    path("teemad/", views.matters, name="matters"),
    path("tegevus/", views.activity, name="activity"),
    path("ajalooline/", views.historical_materials, name="historical"),
    path("andmekvaliteet/", views.data_quality, name="quality"),
    path("arvamused/", views.submissions_list, name="submissions"),
    path("materjalid/", views.materials_list, name="materials"),
    path("loend/", views.metric_rows, name="metric_rows"),
    path("definitsioonid/", views.definitions, name="definitions"),
    path("eksport/<slug:slug>.csv", views.export, name="export"),
    path(
        "eksport/vana-excel.xlsx",
        legacy_views.export_legacy_xlsx,
        name="export_legacy_xlsx",
    ),
    # «Majanduspoliitika ülevaade» (docs/adr/0199): the composer, its preview
    # and refresh, the «Lisa teema» search, and the Word export.
    path("ulevaade/", report_views.composer, name="report_builder"),
    path("ulevaade/eelvaade/", report_views.preview, name="report_preview"),
    path("ulevaade/lisa/", report_views.search_topics, name="report_search"),
    path("ulevaade/laadi/", report_views.export, name="report_export"),
]
