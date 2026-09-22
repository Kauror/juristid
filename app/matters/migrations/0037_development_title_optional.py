"""`Märge` may be a file, a stage or a next step, with no sentence over it.

`MatterProceduralDevelopment.title` was the record's one required column, on the
reasoning that a development that does not say what happened is not a record of
anything. What that refused in practice was a save whose content was complete:
the paper that just arrived, the file moving to `Riigikogus`, «vaatan uue
versiooni üle, 25.09». Each is a whole fact, and the form made the lawyer write
a headline restating it before it would take one (docs/adr/0105 §4).

Two operations, and only the second one reaches the database. `blank=True` is a
Django-side statement about forms and validation; the load-bearing half is
dropping `matters_development_title_required`, the `CHECK (title <> '')` that
would otherwise refuse the row the service now writes.

The column stays `NOT NULL` with no default, and an empty title is the empty
string it has always been able to hold. Nothing is backfilled and no existing row
changes: every title recorded so far is a title somebody typed.

**Reversible, and reversing it would fail on data.** Re-adding the constraint on a
database that has since taken a titleless `Märge` is a refusal, which is the
honest behaviour — the rows would have to be given titles first, and only somebody
who knows what happened can do that.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("matters", "0036_removable_records"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="matterproceduraldevelopment",
            name="matters_development_title_required",
        ),
        migrations.AlterField(
            model_name="matterproceduraldevelopment",
            name="title",
            field=models.CharField(blank=True, max_length=500, verbose_name="sündmus"),
        ),
    ]
