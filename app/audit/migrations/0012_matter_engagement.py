"""The vocabulary learns ENGAGEMENT_ADDED and ENGAGEMENT_CHANGED.

Choices only: `event_type` is a plain CharField, so nothing about the stored
column changes and no existing audit row is touched. Same shape as every earlier
event-type migration in this app.

Their own types rather than a reused `ENTRY_ADDED` or `MATTER_DATE_CHANGED`,
neither of which would be a true statement about somebody recording that the
Chamber asked its members something (docs/adr/0027).
"""


from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('audit', '0011_matter_data_class_event'),
    ]

    operations = [
        migrations.AlterField(
            model_name='changeevent',
            name='event_type',
            field=models.CharField(choices=[('MATTER_CREATED', 'Teema loodud'), ('MATTER_ASSIGNED', 'Teema määratud'), ('MATTER_STAGE_CHANGED', 'Hetkeseis muudetud'), ('MATTER_TRACK_CHANGED', 'Menetlusliik muudetud'), ('MATTER_ORGANISATION_CHANGED', 'Asutus muudetud'), ('MATTER_DATE_CHANGED', 'Kuupäev muudetud'), ('MATTER_POSITION_UPDATED', 'Seisukohta täiendatud'), ('MATTER_POLICY_AREA_OTHER_SET', 'Muu valdkond muudetud'), ('MATTER_VISIBILITY_CHANGED', 'Nähtavus muudetud'), ('MATTER_DATA_CLASS_CHANGED', 'Andmeklass muudetud'), ('MATTER_CLOSED', 'Teema suletud'), ('MATTER_REOPENED', 'Teema taasavatud'), ('MATTER_PROMOTED', 'Arhiivikirjest aktiivne teema'), ('NEXT_ACTION_SET', 'Järgmiseks määratud'), ('NEXT_ACTION_COMPLETED', 'Järgmiseks tehtud'), ('NEXT_ACTION_CANCELLED', 'Järgmiseks tühistatud'), ('NEXT_ACTION_REVIEWED', 'Järgmiseks üle vaadatud'), ('ENTRY_ADDED', 'Sissekanne lisatud'), ('ENTRY_EDITED', 'Sissekannet muudetud'), ('SUBMISSION_CREATED', 'Arvamus loodud'), ('SUBMISSION_SENT', 'Arvamus välja saadetud'), ('SUBMISSION_WITHDRAWN', 'Arvamus tagasi võetud'), ('SUBMISSION_SUPERSEDED', 'Arvamus asendatud'), ('SUBMISSION_RECIPIENTS_CHANGED', 'Arvamuse saajad muudetud'), ('DOCUMENT_CREATED', 'Dokument loodud'), ('EVIDENCE_VERSION_ADDED', 'Tõendiversioon lisatud'), ('TAG_ASSIGNED', 'Silt lisatud'), ('TAG_REMOVED', 'Silt eemaldatud'), ('IMPORT_APPLIED', 'Import rakendatud'), ('IMPORTANT_DATE_ADDED', 'Oluline tähtaeg lisatud'), ('IMPORTANT_DATE_CHANGED', 'Olulist tähtaega muudetud'), ('IMPORTANT_DATE_CANCELLED', 'Oluline tähtaeg tühistatud'), ('EFFECTIVE_DATE_ADDED', 'Jõustumine lisatud'), ('EFFECTIVE_DATE_CHANGED', 'Jõustumist muudetud'), ('EFFECTIVE_DATE_CANCELLED', 'Jõustumine tühistatud'), ('WORK_VICTORY_PROPOSED', 'Töövõidu kandidaat lisatud'), ('WORK_VICTORY_CHANGED', 'Töövõidu kirjet muudetud'), ('WORK_VICTORY_CONFIRMED', 'Töövõit kinnitatud'), ('WORK_VICTORY_REJECTED', 'Töövõit ei realiseerunud'), ('MATTER_HISTORICAL_CUTOVER_CLOSED', 'Ajalooline kirje: enam mitte jooksev töö'), ('MATTER_REGISTER_CUTOVER_RETIRED', 'Lõpliku registri järgi enam mitte jooksev töö'), ('MATTER_REGISTER_CUTOVER_ACTIVATED', 'Lõpliku registri järgi jooksev töö'), ('MATTER_SOURCE_FIELDS_REFRESHED', 'Väljad uuendatud registri põhjal'), ('ENGAGEMENT_ADDED', 'Kaasamine lisatud'), ('ENGAGEMENT_CHANGED', 'Kaasamist muudetud')], db_index=True, max_length=64),
        ),
    ]
