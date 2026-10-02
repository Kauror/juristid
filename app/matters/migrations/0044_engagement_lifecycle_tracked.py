"""A `Kaasamine` is open from the moment it is recorded, deadline or not.

docs/adr/0132. Until now `feedback_deadline` *was* the lifecycle: a round with
a reply-by date was an open wait, a round without one was in no state at all,
and `matters_engagement_feedback_closure_needs_deadline` refused to let it be
completed. The deadline is optional, so the lifecycle needs a column of its own.

**`lifecycle_tracked`, `True` by default in Python and in the database.** Every
interactive door opens a round, and the release still serving while this runs
inserts through `add_engagement` without naming the column — so its inserts
land as open rounds, which is the rule this release introduces, and they
satisfy both new checks whether or not they carry a deadline.

**Every existing row keeps exactly the reading it had.** The `AddField` fills
the column with `True`; the one `UPDATE` then sets it back to `False` on every
row with no deadline and no closure — the rows that were in no state before,
which are the register's imported history and native rounds recorded as
completed acts under docs/adr/0091 §2. Nothing is inferred: a row with a
deadline already *was* an open or a completed wait, and a row without one stays
a record of a consultation that happened, neither open nor completed. No
closure is manufactured and no historical round becomes somebody's work.

**The checks.** `..._closure_needs_deadline` is replaced by
`..._closure_needs_lifecycle` (a completed round is a round), and
`..._deadline_needs_lifecycle` is added (a reply-by date on a historical row is
a wait nobody is in, so setting one starts the lifecycle). Every existing row
satisfies both after the `UPDATE`, in this one transaction.

**Reversible, with one honest failure.** The reverse drops the column and
re-adds the old check, which fails on a database that has since completed a
round with no deadline — that row cannot be expressed in the old schema.
"""

from django.db import migrations, models


def keep_untracked_history(apps, schema_editor):
    engagement = apps.get_model("matters", "MatterEngagement")
    engagement.objects.filter(
        feedback_deadline__isnull=True, feedback_closed_at__isnull=True
    ).update(lifecycle_tracked=False)


class Migration(migrations.Migration):
    dependencies = [
        ("matters", "0043_seed_current_stage_episodes"),
    ]

    operations = [
        migrations.AddField(
            model_name="matterengagement",
            name="lifecycle_tracked",
            field=models.BooleanField(
                db_default=True,
                default=True,
                verbose_name="avatud või lõpetatud kaasamisvoor",
            ),
        ),
        migrations.RunPython(keep_untracked_history, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="matterengagement",
            name="matters_engagement_feedback_closure_needs_deadline",
        ),
        migrations.AddConstraint(
            model_name="matterengagement",
            constraint=models.CheckConstraint(
                condition=models.Q(feedback_closed_at__isnull=True)
                | models.Q(lifecycle_tracked=True),
                name="matters_engagement_closure_needs_lifecycle",
            ),
        ),
        migrations.AddConstraint(
            model_name="matterengagement",
            constraint=models.CheckConstraint(
                condition=models.Q(feedback_deadline__isnull=True)
                | models.Q(lifecycle_tracked=True),
                name="matters_engagement_deadline_needs_lifecycle",
            ),
        ),
    ]
