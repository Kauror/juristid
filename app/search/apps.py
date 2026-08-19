from django.apps import AppConfig


class SearchConfig(AppConfig):
    name = "app.search"
    label = "search"
    verbose_name = "Otsing"

    def ready(self) -> None:
        # Registers the handlers that keep the projection in step with the
        # records it projects. Without them a newly created Matter is not
        # findable until somebody rebuilds the index, which is a silent failure
        # (app/search/signals.py).
        from app.search import signals  # noqa: F401
