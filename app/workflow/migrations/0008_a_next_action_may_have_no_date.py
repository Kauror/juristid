"""A lawyer may know what happens next before knowing when.

`workflow_deadline_requires_a_date` refused `DO` + `DEADLINE` + `target_date
IS NULL` on the reasoning that a deadline with no date cannot be met, missed or
planned against. That is true of a *deadline* and it was the wrong thing to
enforce: «Vaatan ministeeriumi vastuse üle» is a whole instruction, and the day
it happens is a second fact the lawyer frequently does not have yet. The rule
left them inventing a day, and an invented day is a false statement that the
work queue then reports on (docs/adr/0106, superseding ADR 0052 §5's date
requirement).

**One operation, and it only drops a constraint.** No column changes, no data
migration, no backfill, and not one existing `NextAction` row is read or
written. `target_date` has been nullable since the table was created — WAIT and
MONITOR have always been able to be dateless — so nothing about the column's
shape moves here.

Rolling-safe in both directions, which is what lets it be applied before the new
image is up. Dropping a `CHECK` can never fail on existing data, and the *old*
code cannot write a row the dropped constraint would have caught: the refusal it
enforced is also in `set_next_action` and in `NextActionForm`, and the old image
is still running both. Every reader of `target_date` in the old image already
guards `None`, because WAIT and MONITOR rows reach the same code paths.

Reversing it re-adds the constraint — Django rebuilds it from the historical
model state, so nothing has to be restated here — and that reverse **will fail**
on a database that has since taken an undated `DO`. That is the honest
behaviour: those rows would have to be given dates first, and only somebody who
knows the work can do that.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("workflow", "0007_lawyer_reviewed_stage_vocabulary"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="nextaction",
            name="workflow_deadline_requires_a_date",
        ),
    ]
