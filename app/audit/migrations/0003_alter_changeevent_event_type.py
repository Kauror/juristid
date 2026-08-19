"""Stage-1 change event vocabulary: stage, next action, entry and submission."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('audit', '0002_append_only_triggers'),
    ]

    operations = [
        migrations.AlterField(
            model_name='changeevent',
            name='event_type',
            field=models.CharField(choices=[('MATTER_CREATED', 'Teema loodud'), ('MATTER_ASSIGNED', 'Teema määratud'), ('MATTER_STAGE_CHANGED', 'Hetkeseis muudetud'), ('MATTER_TRACK_CHANGED', 'Menetlusliik muudetud'), ('MATTER_ORGANISATION_CHANGED', 'Asutus muudetud'), ('MATTER_DATE_CHANGED', 'Kuupäev muudetud'), ('MATTER_POSITION_UPDATED', 'Seisukohta täiendatud'), ('MATTER_VISIBILITY_CHANGED', 'Nähtavus muudetud'), ('MATTER_CLOSED', 'Teema suletud'), ('MATTER_REOPENED', 'Teema taasavatud'), ('NEXT_ACTION_SET', 'Järgmiseks määratud'), ('NEXT_ACTION_COMPLETED', 'Järgmiseks tehtud'), ('NEXT_ACTION_CANCELLED', 'Järgmiseks tühistatud'), ('ENTRY_ADDED', 'Sissekanne lisatud'), ('ENTRY_EDITED', 'Sissekannet muudetud'), ('SUBMISSION_CREATED', 'Arvamus loodud'), ('SUBMISSION_SENT', 'Arvamus välja saadetud'), ('SUBMISSION_WITHDRAWN', 'Arvamus tagasi võetud'), ('SUBMISSION_SUPERSEDED', 'Arvamus asendatud'), ('DOCUMENT_CREATED', 'Dokument loodud'), ('EVIDENCE_VERSION_ADDED', 'Tõendiversioon lisatud'), ('TAG_ASSIGNED', 'Silt lisatud'), ('TAG_REMOVED', 'Silt eemaldatud'), ('IMPORT_APPLIED', 'Import rakendatud')], db_index=True, max_length=64),
        ),
    ]
