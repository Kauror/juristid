"""«Eeldatav jõustumine (allika hinnang)» reads «Eeldatav jõustumine» (owner, 2026-11-23).

The kind's label changes, and so does the title every Lovable-recorded estimate
was given from it (`lovable_pilot.COMMENCEMENT_TITLES`), because the title is
what the rail, `Tegevused`, the calendar and search print. Only a title that is
exactly the old default is renamed: a title somebody wrote stays theirs.

The search corpus holds those titles too. A queryset update fires no signal, so
the rename leaves one `SearchRebuildDebt` row behind when it changed anything,
and the search worker rebuilds from it (`app.search.freshness`).
"""

from django.db import migrations, models

OLD = "Eeldatav jõustumine (allika hinnang)"
NEW = "Eeldatav jõustumine"


def rename(apps, schema_editor):
    MatterImportantDate = apps.get_model("intelligence", "MatterImportantDate")
    changed = MatterImportantDate.objects.filter(
        kind="ESTIMATED_COMMENCEMENT", title=OLD
    ).update(title=NEW)
    if changed:
        SearchRebuildDebt = apps.get_model("search", "SearchRebuildDebt")
        SearchRebuildDebt.objects.create(reason="Eeldatava jõustumise pealkiri lühenes")


def restore(apps, schema_editor):
    MatterImportantDate = apps.get_model("intelligence", "MatterImportantDate")
    changed = MatterImportantDate.objects.filter(
        kind="ESTIMATED_COMMENCEMENT", title=NEW
    ).update(title=OLD)
    if changed:
        SearchRebuildDebt = apps.get_model("search", "SearchRebuildDebt")
        SearchRebuildDebt.objects.create(reason="Eeldatava jõustumise pealkiri taastati")


class Migration(migrations.Migration):
    dependencies = [
        ("intelligence", "0004_informational_commencement_kinds"),
        ("search", "0014_overview_deadline_and_victory_sources"),
    ]

    operations = [
        migrations.AlterField(
            model_name="matterimportantdate",
            name="kind",
            field=models.CharField(
                choices=[
                    ("OTHER", "Muu tähtaeg"),
                    ("TRANSPOSITION_DEADLINE", "ELi õiguse ülevõtmise tähtaeg"),
                    ("PLANNED_COMMENCEMENT", "Eelnõus kavandatud jõustumine"),
                    ("ESTIMATED_COMMENCEMENT", "Eeldatav jõustumine"),
                ],
                db_index=True,
                default="OTHER",
                max_length=32,
                verbose_name="tähtaja liik",
            ),
        ),
        migrations.RunPython(rename, restore),
    ]
