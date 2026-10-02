"""The current `Hetkeseis` period of every active Matter that already holds a stage (docs/adr/0131 §3).

**A period with no start, because nobody recorded one.** A Matter that stood in
«Kooskõlastusringil» when this runs has been there since some moment no record
of this system states: not the day of this migration, not the day the Matter was
created, and not the day of its last `MATTER_STAGE_CHANGED` — a stage chosen on
`Uus teema` writes no such event, so the audit trail cannot say when every
period began. The row therefore carries ``origin=CARRIED_OVER`` and
``started_at=NULL``, and `Teema käik` prints only what is known.

**Only what is current, and nothing historical.** No earlier period is
reconstructed — not from the audit trail, not from activity dates and not from
OneNote prose — and no existing activity is attached to this period. Work
recorded before it reads as «Varasem tegevus»; work from now on is tied to the
period it is done in.

Who gets one: every Matter that is not deleted, is open, and holds a stage.
`Määramata` is not a period, so a Matter with no stage gets none and begins its
first when a stage is first chosen. A closed Matter gets none: it is not where
new work is grouped, and reopening it opens a period in the stage named then.

**No Matter is closed, reopened or reclassified.** An open Matter standing in
«Jõustunud» — which, after this release, closing would be the ordinary result of
choosing — keeps its open state exactly; the period is carried over as it is.
Production held none at the time of writing; the integrity check reports any
such row rather than this migration deciding for it.

**Idempotent.** A Matter that already has a current period is skipped, so a
re-run writes nothing. **The reverse is a no-op**: ``0042``'s reverse drops the
table these rows live in.

Reads and writes `matters` alone. `Matter.stage` is read, never written.
"""

from django.db import migrations


def seed(apps, schema_editor):
    Matter = apps.get_model("matters", "Matter")
    MatterStageEpisode = apps.get_model("matters", "MatterStageEpisode")

    already = set(
        MatterStageEpisode.objects.filter(is_current=True).values_list("matter_id", flat=True)
    )
    rows = []
    for matter_id, stage_id in (
        Matter._base_manager.filter(
            deleted_at__isnull=True, is_open=True, stage_id__isnull=False
        )
        .order_by("pk")
        .values_list("pk", "stage_id")
    ):
        if matter_id in already:
            continue
        rows.append(
            MatterStageEpisode(
                matter_id=matter_id,
                stage_id=stage_id,
                sequence=1,
                origin="CARRIED_OVER",
                started_at=None,
                ended_at=None,
                is_current=True,
            )
        )
    MatterStageEpisode.objects.bulk_create(rows)


class Migration(migrations.Migration):
    dependencies = [
        ("matters", "0042_matter_stage_episode"),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
