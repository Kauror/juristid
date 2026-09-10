"""Two change-event types for Õigusakt.

``MATTER_LEGAL_INSTRUMENTS_CHANGED`` moves canonical relations;
``MATTER_LEGAL_INSTRUMENT_OTHER_SET`` records the free text beside them, which
is not taxonomy and moves nothing. The same split ``MATTER_POLICY_AREAS_CHANGED``
and ``MATTER_POLICY_AREA_OTHER_SET`` make, for the same reason: a timeline that
called one of them by the other's name would describe a change that did not
happen.

Neither is in ``matters.timeline.TIMELINE_EVENT_TYPES``, exactly as the two
policy-area events are not. Correcting how a file is classified is data
management, not authored chronology, and it would sit in the narrative saying
nothing about the policy work.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('audit', '0016_alter_changeevent_event_type'),
    ]

    operations = [
        migrations.AlterField(
            model_name='changeevent',
            name='event_type',
            field=models.CharField(choices=[('MATTER_CREATED', 'Teema loodud'), ('MATTER_ASSIGNED', 'Teema määratud'), ('MATTER_TITLE_CHANGED', 'Pealkiri muudetud'), ('MATTER_STAGE_CHANGED', 'Hetkeseis muudetud'), ('MATTER_TRACK_CHANGED', 'Menetlusliik muudetud'), ('MATTER_ORGANISATION_CHANGED', 'Asutus muudetud'), ('MATTER_DATE_CHANGED', 'Kuupäev muudetud'), ('MATTER_POSITION_UPDATED', 'Seisukohta täiendatud'), ('MATTER_BRIEF_SUMMARY_SET', 'Lühikokkuvõte muudetud'), ('MATTER_POLICY_AREAS_CHANGED', 'Valdkonnad muudetud'), ('MATTER_POLICY_AREA_OTHER_SET', 'Muu valdkond muudetud'), ('MATTER_LEGAL_INSTRUMENTS_CHANGED', 'Õigusakt muudetud'), ('MATTER_LEGAL_INSTRUMENT_OTHER_SET', 'Muu õigusakt muudetud'), ('MATTER_VISIBILITY_CHANGED', 'Nähtavus muudetud'), ('MATTER_DATA_CLASS_CHANGED', 'Andmeklass muudetud'), ('MATTER_CLOSED', 'Teema suletud'), ('MATTER_REOPENED', 'Teema taasavatud'), ('MATTER_PROMOTED', 'Arhiivikirjest aktiivne teema'), ('NEXT_ACTION_SET', 'Järgmiseks määratud'), ('NEXT_ACTION_COMPLETED', 'Järgmiseks tehtud'), ('NEXT_ACTION_CANCELLED', 'Järgmiseks tühistatud'), ('NEXT_ACTION_REVIEWED', 'Järgmiseks üle vaadatud'), ('ENTRY_ADDED', 'Sissekanne lisatud'), ('ENTRY_EDITED', 'Sissekannet muudetud'), ('SUBMISSION_CREATED', 'Arvamus loodud'), ('SUBMISSION_SENT', 'Arvamus välja saadetud'), ('SUBMISSION_WITHDRAWN', 'Arvamus tagasi võetud'), ('SUBMISSION_SUPERSEDED', 'Arvamus asendatud'), ('SUBMISSION_RECIPIENTS_CHANGED', 'Arvamuse saajad muudetud'), ('DOCUMENT_CREATED', 'Dokument loodud'), ('EVIDENCE_VERSION_ADDED', 'Tõendiversioon lisatud'), ('TAG_ASSIGNED', 'Silt lisatud'), ('TAG_REMOVED', 'Silt eemaldatud'), ('IMPORT_APPLIED', 'Import rakendatud'), ('IMPORTANT_DATE_ADDED', 'Oluline tähtaeg lisatud'), ('IMPORTANT_DATE_CHANGED', 'Olulist tähtaega muudetud'), ('IMPORTANT_DATE_CANCELLED', 'Oluline tähtaeg tühistatud'), ('EFFECTIVE_DATE_ADDED', 'Jõustumine lisatud'), ('EFFECTIVE_DATE_CHANGED', 'Jõustumist muudetud'), ('EFFECTIVE_DATE_CANCELLED', 'Jõustumine tühistatud'), ('WORK_VICTORY_PROPOSED', 'Töövõidu kandidaat lisatud'), ('WORK_VICTORY_CHANGED', 'Töövõidu kirjet muudetud'), ('WORK_VICTORY_CONFIRMED', 'Töövõit kinnitatud'), ('WORK_VICTORY_REJECTED', 'Töövõit ei realiseerunud'), ('MATTER_HISTORICAL_CUTOVER_CLOSED', 'Ajalooline kirje: enam mitte jooksev töö'), ('MATTER_REGISTER_CUTOVER_RETIRED', 'Lõpliku registri järgi enam mitte jooksev töö'), ('MATTER_REGISTER_CUTOVER_ACTIVATED', 'Lõpliku registri järgi jooksev töö'), ('MATTER_SOURCE_FIELDS_REFRESHED', 'Väljad uuendatud registri põhjal'), ('ENGAGEMENT_ADDED', 'Kaasamine lisatud'), ('ENGAGEMENT_CHANGED', 'Kaasamist muudetud'), ('MATTER_RELATION_ADDED', 'Teema seotud teise teemaga'), ('MATTER_RELATION_REMOVED', 'Teemade seos eemaldatud'), ('BACKGROUND_MATERIAL_ADDED', 'Taustmaterjal lisatud'), ('BACKGROUND_MATERIAL_REMOVED', 'Taustmaterjal eemaldatud')], db_index=True, max_length=64),
        ),
    ]
