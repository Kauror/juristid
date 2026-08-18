from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "app.core"
    label = "core"
    verbose_name = "Alus"

    def ready(self) -> None:
        from app.core import checks  # noqa: F401  (registers system checks)
