"""Three event types for `Menetluse areng`.

One `AlterField` over a `choices` list, which is Python metadata and not a
database object: `ChangeEvent.event_type` is a `CharField` and PostgreSQL has
never enforced the vocabulary. No row is read, written or rewritten, and the
migration is reversible to a list nothing validates against.

The three are `PROCEDURAL_DEVELOPMENT_RECORDED`, `_CORRECTED` and
`_DOCUMENT_LINKED` — because three different things happen to this record and no
more. None of them is in `TIMELINE_EVENT_TYPES`: the chronology renders the
development from the canonical record, and reading the event as well would state
one act twice (docs/adr/0091 §5).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('audit', '0022_procedural_link_events'),
    ]

    operations = [
        migrations.AlterField(
            model_name='changeevent',
            name='event_type',
            field=models.CharField(choices=[('MATTER_CREATED', 'Teema loodud'), ('MATTER_ASSIGNED', 'Teema määratud'), ('MATTER_TITLE_CHANGED', 'Pealkiri muudetud'), ('MATTER_STAGE_CHANGED', 'Hetkeseis muudetud'), ('MATTER_TRACK_CHANGED', 'Menetlusliik muudetud'), ('MATTER_ORGANISATION_CHANGED', 'Asutus muudetud'), ('MATTER_DATE_CHANGED', 'Kuupäev muudetud'), ('MATTER_POSITION_UPDATED', 'Seisukohta täiendatud'), ('MATTER_BRIEF_SUMMARY_SET', 'Lühikokkuvõte muudetud'), ('MATTER_POLICY_AREAS_CHANGED', 'Valdkonnad muudetud'), ('MATTER_POLICY_AREA_OTHER_SET', 'Muu valdkond muudetud'), ('MATTER_LEGAL_INSTRUMENTS_CHANGED', 'Õigusakt muudetud'), ('MATTER_LEGAL_INSTRUMENT_OTHER_SET', 'Muu õigusakt muudetud'), ('MATTER_VISIBILITY_CHANGED', 'Nähtavus muudetud'), ('MATTER_DATA_CLASS_CHANGED', 'Andmeklass muudetud'), ('MATTER_CLOSED', 'Teema suletud'), ('MATTER_REOPENED', 'Teema taasavatud'), ('MATTER_PROMOTED', 'Arhiivikirjest aktiivne teema'), ('NEXT_ACTION_SET', 'Järgmiseks määratud'), ('NEXT_ACTION_COMPLETED', 'Järgmiseks tehtud'), ('NEXT_ACTION_CANCELLED', 'Järgmiseks tühistatud'), ('NEXT_ACTION_REVIEWED', 'Järgmiseks üle vaadatud'), ('ENTRY_ADDED', 'Sissekanne lisatud'), ('ENTRY_EDITED', 'Sissekannet muudetud'), ('SUBMISSION_CREATED', 'Arvamus loodud'), ('SUBMISSION_SENT', 'Arvamus välja saadetud'), ('SUBMISSION_WITHDRAWN', 'Arvamus tagasi võetud'), ('SUBMISSION_SUPERSEDED', 'Arvamus asendatud'), ('SUBMISSION_RECIPIENTS_CHANGED', 'Arvamuse saajad muudetud'), ('DOCUMENT_CREATED', 'Dokument loodud'), ('EVIDENCE_VERSION_ADDED', 'Tõendiversioon lisatud'), ('TAG_ASSIGNED', 'Silt lisatud'), ('TAG_REMOVED', 'Silt eemaldatud'), ('IMPORT_APPLIED', 'Import rakendatud'), ('IMPORTANT_DATE_ADDED', 'Oluline tähtaeg lisatud'), ('IMPORTANT_DATE_CHANGED', 'Olulist tähtaega muudetud'), ('IMPORTANT_DATE_CANCELLED', 'Oluline tähtaeg tühistatud'), ('EFFECTIVE_DATE_ADDED', 'Jõustumine lisatud'), ('EFFECTIVE_DATE_CHANGED', 'Jõustumist muudetud'), ('EFFECTIVE_DATE_CANCELLED', 'Jõustumine tühistatud'), ('WORK_VICTORY_PROPOSED', 'Töövõidu kandidaat lisatud'), ('WORK_VICTORY_CHANGED', 'Töövõidu kirjet muudetud'), ('WORK_VICTORY_CONFIRMED', 'Töövõit kinnitatud'), ('WORK_VICTORY_REJECTED', 'Töövõit ei realiseerunud'), ('MATTER_HISTORICAL_CUTOVER_CLOSED', 'Ajalooline kirje: enam mitte jooksev töö'), ('MATTER_REGISTER_CUTOVER_RETIRED', 'Lõpliku registri järgi enam mitte jooksev töö'), ('MATTER_REGISTER_CUTOVER_ACTIVATED', 'Lõpliku registri järgi jooksev töö'), ('MATTER_SOURCE_FIELDS_REFRESHED', 'Väljad uuendatud registri põhjal'), ('ENGAGEMENT_ADDED', 'Kaasamine lisatud'), ('ENGAGEMENT_CHANGED', 'Kaasamist muudetud'), ('ENGAGEMENT_FEEDBACK_CLOSED', 'Kaasamise tagasiside laekunud'), ('MATTER_RELATION_ADDED', 'Teema seotud teise teemaga'), ('MATTER_RELATION_REMOVED', 'Teemade seos eemaldatud'), ('BACKGROUND_MATERIAL_ADDED', 'Taustmaterjal lisatud'), ('BACKGROUND_MATERIAL_REMOVED', 'Taustmaterjal eemaldatud'), ('WEBSITE_OVERVIEW_PLANNED', 'Ülevaade / uudis plaanis'), ('WEBSITE_OVERVIEW_PUBLISHED', 'Ülevaade / uudis avaldatud'), ('WEBSITE_OVERVIEW_CANCELLED', 'Ülevaade / uudis tühistatud'), ('WEBSITE_OVERVIEW_LINK_CORRECTED', 'Ülevaate või uudise linki või kuupäeva parandatud'), ('EXTERNAL_POSITION_RECORDED', 'Väline seisukoht lisatud'), ('EXTERNAL_POSITION_CORRECTED', 'Välist seisukohta parandatud'), ('EXTERNAL_POSITION_SOURCE_CHANGED', 'Välise seisukoha link muudetud'), ('EXTERNAL_POSITION_DOCUMENT_LINKED', 'Välise seisukoha fail lisatud'), ('PROCEDURAL_LINK_RECORDED', 'Menetluse link lisatud'), ('PROCEDURAL_LINK_CORRECTED', 'Menetluse linki parandatud'), ('PROCEDURAL_DEVELOPMENT_RECORDED', 'Menetluse areng lisatud'), ('PROCEDURAL_DEVELOPMENT_CORRECTED', 'Menetluse arengut parandatud'), ('PROCEDURAL_DEVELOPMENT_DOCUMENT_LINKED', 'Menetluse arengu fail lisatud')], db_index=True, max_length=64),
        ),
    ]
