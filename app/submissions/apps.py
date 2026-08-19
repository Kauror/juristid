from django.apps import AppConfig


class SubmissionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "app.submissions"
    label = "submissions"
    verbose_name = "Väljasaadetud arvamused"
