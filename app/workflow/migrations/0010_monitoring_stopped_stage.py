"""`Rohkem ei tegele` — version 3.0 of the reviewed `Hetkeseis` vocabulary.

One row is added, ``monitoring_stopped``, sorted last after «Muu». Nothing else
moves: no key, label, sort order, help text or `Matter.stage` is touched, and
no historical mapping is re-pointed (docs/adr/0131 §9).

**History is not reread.** The workbook's ``rohkem pole tegevusi plaanis`` is
still the *disposition* ``workflow/0004`` read it as. This row is a current
product decision — the ordinary way a lawyer says Koda has stopped — and no
imported row is reinterpreted as having held it.

**Frozen, not imported**, for the reason ``workflow/0007`` gives: a historical
migration that read today's manifest would replay as something else every time
the manifest is edited. ``tests/test_reference_stages.py`` holds the copy and
the manifest to each other.

**Idempotent.** A row with this key already present is left exactly as it is —
somebody created or edited it, and overruling that silently is not this
migration's call.

**The reverse refuses a row in use.** Removing a stage a Matter, a period or a
legacy mapping points at would strand a recorded fact, and every one of those
foreign keys is `PROTECT`, so the delete fails rather than cascading. An unused
row is removed.

Touches ``workflow`` and nothing else, in both directions — the rule
``workflow/0007`` states for data migrations in this app.
"""

from django.db import migrations

#: Frozen copy of `app.workflow.reference_stages.MONITORING_STOPPED_STAGE`.
KEY = "monitoring_stopped"
LABEL = "Rohkem ei tegele"
SORT_ORDER = 110
HELP_TEXT = (
    "Koda ei tegele teemaga edasi. Selle hetkeseisu salvestamine lõpetab teema; "
    "vajadusel saab teema hiljem uuesti avada."
)


def add_stage(apps, schema_editor):
    StageVocabulary = apps.get_model("workflow", "StageVocabulary")
    if StageVocabulary.objects.filter(key=KEY).exists():
        return
    StageVocabulary.objects.create(
        key=KEY,
        label_et=LABEL,
        sort_order=SORT_ORDER,
        help_text=HELP_TEXT,
        is_active=True,
        is_provisional=False,
    )


def remove_stage(apps, schema_editor):
    """A raw delete, so the database's own foreign keys decide — not the ORM's collector.

    `.delete()` would walk every model that points at the row through the
    migration state of *other* apps, rewound to wherever they happen to be —
    which is how `migrate workflow zero` died here in CI, the failure
    `workflow/0007`'s docstring describes. The foreign keys are still in the
    database and still refuse a row in use.
    """
    StageVocabulary = apps.get_model("workflow", "StageVocabulary")
    StageVocabulary.objects.filter(key=KEY)._raw_delete(schema_editor.connection.alias)


class Migration(migrations.Migration):
    dependencies = [
        ("workflow", "0009_next_action_values_are_checked"),
    ]

    operations = [
        migrations.RunPython(add_stage, remove_stage),
    ]
