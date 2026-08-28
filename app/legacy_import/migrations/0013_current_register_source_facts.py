"""Six derived columns on ``CurrentRegisterState``, and one backfill.

Every column added here is **derived**: the table can be dropped in full and
rebuilt from the immutable ``MatterSourceReference`` rows it reads, and
``final_cutover.rebuild_current_state`` is what fills them from the reviewed
snapshot. No canonical business field is touched by this migration and none
could be — nothing in the domain writes state through this table.

Why a backfill exists at all
----------------------------
``legacy_register_opinion_sent_presence_agrees`` says the four-way ``VÄLJA``
reading and the older presence flag cannot disagree. Adding the column with its
``BLANK`` default would leave every row that *does* record a ``VÄLJA`` value
saying "nothing was written", and the constraint would refuse to install on any
database holding data.

So the backfill derives the new column from two columns the row already carries,
and derives nothing else:

* a parsed date is present  → ``DATE``
* something was recorded but no date parsed → ``RECORDED_OTHER``
* nothing recorded → ``BLANK``

That is the weakest true statement available from the row itself.
``RECORDED_OTHER`` is deliberately *not* refined into ``NOT_SENT`` here: telling
"ei saatnud" from any other unparseable text needs the raw cell, which lives on
the source reference and not on this row, and guessing which of the two a row
was would be inventing a fact. The first refresh against a reviewed snapshot
reads the cell and records the real answer.

The other five columns stay empty until that refresh, which is the honest state:
blank means the register's value has not been derived yet, and for the two
member-feedback counts ``NULL`` already means exactly that (brief 10).
"""

from django.db import migrations, models


def derive_opinion_sent_state(apps, schema_editor):
    """Fill the new reading from the two columns each row already has."""
    state = apps.get_model("legacy_import", "CurrentRegisterState")
    state.objects.filter(opinion_sent_date__isnull=False).update(opinion_sent_state="DATE")
    state.objects.filter(opinion_sent_date__isnull=True, opinion_sent_recorded=True).update(
        opinion_sent_state="RECORDED_OTHER"
    )
    state.objects.filter(opinion_sent_recorded=False).update(opinion_sent_state="BLANK")


def clear_opinion_sent_state(apps, schema_editor):
    """Reversing drops the column; nothing to undo before it goes."""


class Migration(migrations.Migration):

    dependencies = [
        ('legacy_import', '0012_opinion_recipient_provenance'),
        ('matters', '0011_matter_successor'),
    ]

    operations = [
        migrations.AddField(
            model_name='currentregisterstate',
            name='addressee_cardinality',
            field=models.CharField(choices=[('BLANK', 'Märkimata'), ('SINGLE', 'Üks asutus'), ('MULTIPLE', 'Mitu asutust')], db_index=True, default='BLANK', max_length=16, verbose_name='adressaatide arv'),
        ),
        migrations.AddField(
            model_name='currentregisterstate',
            name='addressee_raw',
            field=models.CharField(blank=True, help_text='Adressaat allika sõnastuses; kanooniline adressaat on Matter.addressee_organisation.', max_length=500, verbose_name='KELLELE allikas'),
        ),
        migrations.AddField(
            model_name='currentregisterstate',
            name='legal_instrument_raw',
            field=models.CharField(blank=True, max_length=200, verbose_name='ÕIGUSAKT allikas'),
        ),
        migrations.AddField(
            model_name='currentregisterstate',
            name='member_feedback_requested',
            field=models.PositiveIntegerField(blank=True, help_text='Registri vaatlus. Tühi tähendab, et arvu ei ole kirjas — mitte nulli.', null=True, verbose_name='otse küsitud liikmeid'),
        ),
        migrations.AddField(
            model_name='currentregisterstate',
            name='member_feedback_responded',
            field=models.PositiveIntegerField(blank=True, help_text='Registri vaatlus. Tühi tähendab, et arvu ei ole kirjas — mitte nulli.', null=True, verbose_name='tagasisidet andnud liikmeid'),
        ),
        migrations.AddField(
            model_name='currentregisterstate',
            name='opinion_sent_state',
            field=models.CharField(choices=[('DATE', 'Kuupäev'), ('NOT_SENT', 'Ei saatnud'), ('RECORDED_OTHER', 'Muu märge'), ('BLANK', 'Märkimata')], db_index=True, default='BLANK', help_text="Kas VÄLJA on kuupäev, sõnaline 'ei saatnud', muu märge või tühi.", max_length=32, verbose_name='VÄLJA seis'),
        ),
        migrations.RunPython(derive_opinion_sent_state, clear_opinion_sent_state),
        migrations.AddConstraint(
            model_name='currentregisterstate',
            constraint=models.CheckConstraint(condition=models.Q(('opinion_sent_state__in', ('DATE', 'NOT_SENT', 'RECORDED_OTHER', 'BLANK'))), name='legacy_register_opinion_sent_vocabulary'),
        ),
        migrations.AddConstraint(
            model_name='currentregisterstate',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('opinion_sent_recorded', True), ('opinion_sent_state__in', ('DATE', 'NOT_SENT', 'RECORDED_OTHER', 'BLANK')), models.Q(('opinion_sent_recorded', True), ('opinion_sent_state', 'BLANK'), _negated=True)), models.Q(('opinion_sent_recorded', False), ('opinion_sent_state', 'BLANK')), _connector='OR'), name='legacy_register_opinion_sent_presence_agrees'),
        ),
    ]
