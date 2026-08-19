from django.urls import path

from app.documents import views

urlpatterns = [
    path("teema/<uuid:matter_id>/laadi-ules/", views.upload_evidence, name="upload_evidence"),
    path("<uuid:pk>/uus-versioon/", views.add_version, name="add_version"),
    path("tõend/<uuid:pk>/", views.download, name="download"),
    path("<uuid:pk>/", views.document_detail, name="document_detail"),
]
