"""Stop storing a child record's effective visibility.

The column could go stale whenever a Matter's visibility changed through any
write that did not go through the service maintaining it, and a stale value
reads as less restrictive than the truth. Effective visibility is now derived
from the Matter and the child override at query time (docs/adr/0005).

Also adds a unique constraint on the evidence storage key: two versions may
never address the same stored object.
"""

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0002_evidence_immutability'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='document',
            name='documents_document_effective_not_weaker_than_override',
        ),
        migrations.RemoveField(
            model_name='document',
            name='effective_visibility',
        ),
        migrations.AlterField(
            model_name='document',
            name='visibility_override',
            field=models.CharField(blank=True, choices=[('NORMAL', 'Tavaline'), ('RESTRICTED', 'Piiratud')], db_index=True, default='', help_text='Tühi tähendab, et nähtavus päritakse teemalt.', max_length=16, verbose_name='nähtavuse kitsendus'),
        ),
        migrations.AddConstraint(
            model_name='documentversion',
            constraint=models.UniqueConstraint(fields=('storage_key',), name='documents_unique_storage_key'),
        ),
    ]
