from django.apps import AppConfig


class LegacyImportConfig(AppConfig):
    name = "app.legacy_import"
    label = "legacy_import"
    verbose_name = "Ajalooline import"

    def ready(self) -> None:
        # Registers the handlers that keep the archive's own search projection
        # in step with the links and imports it projects. Without them the
        # archive workspace reports `0 teemaga seotud` over a fully linked
        # corpus, and its verify command says nothing is wrong
        # (app/legacy_import/opinion_search_signals.py, docs/adr/0041).
        from app.legacy_import import opinion_search_signals  # noqa: F401
