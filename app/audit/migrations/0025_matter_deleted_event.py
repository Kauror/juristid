"""`MATTER_DELETED`, the one event that describes a record which is no longer there.

One `AlterField` over a `choices` list, which is Python metadata and not a
database object: `ChangeEvent.event_type` is a `CharField` and PostgreSQL has
never enforced the vocabulary. No row is read, written or rewritten — the same
shape, and the same reasoning, as `0024_opinion_marksona_and_overview_link_events`.

The row this type is written on is load-bearing in a way the others are not.
`ChangeEvent.matter` is `PROTECT` onto an append-only table, so this event is
itself the reason the `Matter` row survives its own deletion as a tombstone: the
audit trail cannot be removed and cannot be detached, and a record of the
deletion is exactly what ought to be permanent (docs/adr/0096 §4).
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0024_opinion_marksona_and_overview_link_events"),
    ]

    operations = [
        migrations.AlterField(
            model_name="changeevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("MATTER_CREATED", "Teema loodud"),
                    ("MATTER_ASSIGNED", "Teema määratud"),
                    ("MATTER_TITLE_CHANGED", "Pealkiri muudetud"),
                    ("MATTER_STAGE_CHANGED", "Hetkeseis muudetud"),
                    ("MATTER_TRACK_CHANGED", "Menetlusliik muudetud"),
                    ("MATTER_ORGANISATION_CHANGED", "Asutus muudetud"),
                    ("MATTER_DATE_CHANGED", "Kuupäev muudetud"),
                    ("MATTER_POSITION_UPDATED", "Seisukohta täiendatud"),
                    ("MATTER_BRIEF_SUMMARY_SET", "Lühikokkuvõte muudetud"),
                    ("MATTER_POLICY_AREAS_CHANGED", "Valdkonnad muudetud"),
                    ("MATTER_POLICY_AREA_OTHER_SET", "Muu valdkond muudetud"),
                    ("MATTER_LEGAL_INSTRUMENTS_CHANGED", "Õigusakt muudetud"),
                    ("MATTER_LEGAL_INSTRUMENT_OTHER_SET", "Muu õigusakt muudetud"),
                    ("MATTER_VISIBILITY_CHANGED", "Nähtavus muudetud"),
                    ("MATTER_DATA_CLASS_CHANGED", "Andmeklass muudetud"),
                    ("MATTER_CLOSED", "Teema suletud"),
                    ("MATTER_DELETED", "Teema kustutatud"),
                    ("MATTER_REOPENED", "Teema taasavatud"),
                    ("MATTER_PROMOTED", "Arhiivikirjest aktiivne teema"),
                    ("NEXT_ACTION_SET", "Järgmiseks määratud"),
                    ("NEXT_ACTION_COMPLETED", "Järgmiseks tehtud"),
                    ("NEXT_ACTION_CANCELLED", "Järgmiseks tühistatud"),
                    ("NEXT_ACTION_REVIEWED", "Järgmiseks üle vaadatud"),
                    ("ENTRY_ADDED", "Sissekanne lisatud"),
                    ("ENTRY_EDITED", "Sissekannet muudetud"),
                    ("SUBMISSION_CREATED", "Arvamus loodud"),
                    ("SUBMISSION_SENT", "Arvamus välja saadetud"),
                    ("SUBMISSION_WITHDRAWN", "Arvamus tagasi võetud"),
                    ("SUBMISSION_SUPERSEDED", "Arvamus asendatud"),
                    ("SUBMISSION_RECIPIENTS_CHANGED", "Arvamuse saajad muudetud"),
                    ("SUBMISSION_TAG_ASSIGNED", "Arvamuse märksõna lisatud"),
                    ("SUBMISSION_TAG_REMOVED", "Arvamuse märksõna eemaldatud"),
                    ("SUBMISSION_OVERVIEW_LINKED", "Arvamus seotud ülevaate või uudisega"),
                    (
                        "SUBMISSION_OVERVIEW_UNLINKED",
                        "Arvamuse seos ülevaate või uudisega eemaldatud",
                    ),
                    ("DOCUMENT_CREATED", "Dokument loodud"),
                    ("EVIDENCE_VERSION_ADDED", "Tõendiversioon lisatud"),
                    ("TAG_ASSIGNED", "Silt lisatud"),
                    ("TAG_REMOVED", "Silt eemaldatud"),
                    ("IMPORT_APPLIED", "Import rakendatud"),
                    ("IMPORTANT_DATE_ADDED", "Oluline tähtaeg lisatud"),
                    ("IMPORTANT_DATE_CHANGED", "Olulist tähtaega muudetud"),
                    ("IMPORTANT_DATE_CANCELLED", "Oluline tähtaeg tühistatud"),
                    ("EFFECTIVE_DATE_ADDED", "Jõustumine lisatud"),
                    ("EFFECTIVE_DATE_CHANGED", "Jõustumist muudetud"),
                    ("EFFECTIVE_DATE_CANCELLED", "Jõustumine tühistatud"),
                    ("WORK_VICTORY_PROPOSED", "Töövõidu kandidaat lisatud"),
                    ("WORK_VICTORY_CHANGED", "Töövõidu kirjet muudetud"),
                    ("WORK_VICTORY_CONFIRMED", "Töövõit kinnitatud"),
                    ("WORK_VICTORY_REJECTED", "Töövõit ei realiseerunud"),
                    (
                        "MATTER_HISTORICAL_CUTOVER_CLOSED",
                        "Ajalooline kirje: enam mitte jooksev töö",
                    ),
                    (
                        "MATTER_REGISTER_CUTOVER_RETIRED",
                        "Lõpliku registri järgi enam mitte jooksev töö",
                    ),
                    ("MATTER_REGISTER_CUTOVER_ACTIVATED", "Lõpliku registri järgi jooksev töö"),
                    ("MATTER_SOURCE_FIELDS_REFRESHED", "Väljad uuendatud registri põhjal"),
                    ("ENGAGEMENT_ADDED", "Kaasamine lisatud"),
                    ("ENGAGEMENT_CHANGED", "Kaasamist muudetud"),
                    ("ENGAGEMENT_FEEDBACK_CLOSED", "Kaasamise tagasiside laekunud"),
                    ("MATTER_RELATION_ADDED", "Teema seotud teise teemaga"),
                    ("MATTER_RELATION_REMOVED", "Teemade seos eemaldatud"),
                    ("BACKGROUND_MATERIAL_ADDED", "Taustmaterjal lisatud"),
                    ("BACKGROUND_MATERIAL_REMOVED", "Taustmaterjal eemaldatud"),
                    ("WEBSITE_OVERVIEW_PLANNED", "Ülevaade / uudis plaanis"),
                    ("WEBSITE_OVERVIEW_PUBLISHED", "Ülevaade / uudis avaldatud"),
                    ("WEBSITE_OVERVIEW_CANCELLED", "Ülevaade / uudis tühistatud"),
                    (
                        "WEBSITE_OVERVIEW_LINK_CORRECTED",
                        "Ülevaate või uudise linki või kuupäeva parandatud",
                    ),
                    ("EXTERNAL_POSITION_RECORDED", "Väline seisukoht lisatud"),
                    ("EXTERNAL_POSITION_CORRECTED", "Välist seisukohta parandatud"),
                    ("EXTERNAL_POSITION_SOURCE_CHANGED", "Välise seisukoha link muudetud"),
                    ("EXTERNAL_POSITION_DOCUMENT_LINKED", "Välise seisukoha fail lisatud"),
                    ("PROCEDURAL_LINK_RECORDED", "Menetluse link lisatud"),
                    ("PROCEDURAL_LINK_CORRECTED", "Menetluse linki parandatud"),
                    ("PROCEDURAL_DEVELOPMENT_RECORDED", "Menetluse areng lisatud"),
                    ("PROCEDURAL_DEVELOPMENT_CORRECTED", "Menetluse arengut parandatud"),
                    ("PROCEDURAL_DEVELOPMENT_DOCUMENT_LINKED", "Menetluse arengu fail lisatud"),
                ],
                db_index=True,
                max_length=64,
            ),
        ),
    ]
