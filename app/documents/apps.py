from django.apps import AppConfig


class DocumentsConfig(AppConfig):
    name = "app.documents"
    label = "documents"
    verbose_name = "Dokumendid"

    def ready(self) -> None:
        from app.documents.extraction import isolation_checks  # noqa: F401  (registers)
