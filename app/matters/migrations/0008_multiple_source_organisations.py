"""A Matter may have been sent by several organisations, not one.

`KELLELT` becomes 0..N. `KELLELE` is untouched and stays 0..1: the two remain
different facts, and this migration moves only the sender side.

**Every existing sender is preserved exactly.** The forward data step copies
each non-null ``source_organisation_id`` into one relation row and invents
nothing — no re-resolution of the historical register, no re-reading of a
workbook, no splitting of a raw counterparty string that happens to mention two
names. A Matter that had no sender ends with an empty set. The relation count
after the copy is therefore exactly the number of Matters whose old field was
set (Agent-E brief 12, 14, 17).

**The reverse step fails closed.** A plural model cannot always be squeezed back
into one column, and the ways of pretending it can — take the first, take the
alphabetically smallest, drop the rest — all destroy data while reporting
success. So the reverse restores the singular field faithfully while every
Matter still has at most one sender, and refuses outright once any Matter has
two. Rollback stays available right up to the moment real multi-sender data
exists, and after that it is a decision a person has to make (brief 15).

The through table exists to keep ``PROTECT`` on the organisation side, which is
what the removed foreign key guaranteed (brief 73).
"""

from django.db import migrations, models

import app.core.ids

#: Rows per INSERT. Production holds ~2546 matters; the batching is here so
#: the step stays a bounded amount of memory whatever the table grows to.
BATCH = 1000


def copy_senders_forward(apps, schema_editor):
    """One relation row per Matter that had a sender."""
    Matter = apps.get_model("matters", "Matter")
    MatterSourceOrganisation = apps.get_model("matters", "MatterSourceOrganisation")

    rows = (
        Matter.objects.filter(source_organisation__isnull=False)
        .order_by()
        .values_list("id", "source_organisation_id")
    )
    MatterSourceOrganisation.objects.bulk_create(
        (
            MatterSourceOrganisation(matter_id=matter_id, organisation_id=organisation_id)
            for matter_id, organisation_id in rows.iterator(chunk_size=BATCH)
        ),
        batch_size=BATCH,
    )


def restore_single_sender(apps, schema_editor):
    """Put the senders back in one column, or refuse to.

    Raising here aborts the whole reverse migration inside its transaction, so
    a rollback attempted against multi-sender data leaves the database as it
    was rather than half-converted.
    """
    Matter = apps.get_model("matters", "Matter")
    MatterSourceOrganisation = apps.get_model("matters", "MatterSourceOrganisation")

    plural = (
        MatterSourceOrganisation.objects.values("matter_id")
        .annotate(total=models.Count("organisation_id", distinct=True))
        .filter(total__gt=1)
    )
    offenders = [str(row["matter_id"]) for row in plural[:5]]
    if offenders:
        raise RuntimeError(
            "Cannot reverse 0008_multiple_source_organisations: "
            f"{len(offenders)}+ matters have more than one sender "
            f"(for example {', '.join(offenders)}). The singular "
            "Matter.source_organisation column cannot hold them, and choosing "
            "which sender to keep is not a decision a migration may make. "
            "Reduce every matter to at most one sender first, deliberately."
        )

    restored = [
        Matter(pk=matter_id, source_organisation_id=organisation_id)
        for matter_id, organisation_id in MatterSourceOrganisation.objects.order_by().values_list(
            "matter_id", "organisation_id"
        )
    ]
    Matter.objects.bulk_update(restored, ["source_organisation"], batch_size=BATCH)


class Migration(migrations.Migration):

    dependencies = [
        ("matters", "0007_matter_data_class"),
        ("organisations", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="MatterSourceOrganisation",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=app.core.ids.uuid7,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "matter",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="source_links",
                        to="matters.matter",
                        verbose_name="teema",
                    ),
                ),
                (
                    "organisation",
                    models.ForeignKey(
                        on_delete=models.deletion.PROTECT,
                        related_name="matter_source_links",
                        to="organisations.organisation",
                        verbose_name="organisatsioon",
                    ),
                ),
            ],
            options={
                "verbose_name": "teema saatja",
                "verbose_name_plural": "teema saatjad",
            },
        ),
        migrations.AddConstraint(
            model_name="mattersourceorganisation",
            constraint=models.UniqueConstraint(
                fields=("matter", "organisation"),
                name="matters_unique_source_organisation_per_matter",
            ),
        ),
        migrations.AddField(
            model_name="matter",
            name="source_organisations",
            field=models.ManyToManyField(
                blank=True,
                related_name="matters_as_sources",
                through="matters.MatterSourceOrganisation",
                to="organisations.organisation",
                verbose_name="algatajad või saatjad",
            ),
        ),
        migrations.RunPython(copy_senders_forward, restore_single_sender),
        migrations.RemoveField(
            model_name="matter",
            name="source_organisation",
        ),
    ]
