"""Which single professional action wrote this audit row.

Nullable and additive. Every existing row keeps the null it was written with,
which the timeline reads as "this stands alone" — exactly what those rows have
always meant. Nothing is merged retroactively and no row is rewritten; the
append-only trigger on this table is untouched (Teema redesign §11.1).
"""


from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('audit', '0012_matter_engagement'),
    ]

    operations = [
        migrations.AddField(
            model_name='changeevent',
            name='operation_id',
            field=models.UUIDField(blank=True, db_index=True, null=True, verbose_name='tegevuse tunnus'),
        ),
    ]
