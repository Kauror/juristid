"""Search platform prerequisites.

Stage 0 installs only what the search architecture depends on. The rebuildable
``SearchDocument`` projection itself is Stage-2 work
(docs/adr/0006-search-architecture.md).
"""

from django.contrib.postgres.operations import TrigramExtension, UnaccentExtension
from django.db import migrations


class Migration(migrations.Migration):
    initial = True

    dependencies: list[tuple[str, str]] = []

    operations = [
        TrigramExtension(),
        UnaccentExtension(),
    ]
