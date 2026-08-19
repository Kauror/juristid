"""Visibility values are constrained to the vocabulary authorization knows.

An unrecognised value in either column would be a value the authorization code
cannot interpret. The query builders already whitelist rather than blacklist, so
such a row would fail closed rather than leak; this keeps it out of the table in
the first place (docs/adr/0005).
"""

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('matters', '0001_initial'),
        ('organisations', '0001_initial'),
        ('taxonomy', '0001_initial'),
        ('workflow', '0002_era_aware_legacy_status'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='matter',
            constraint=models.CheckConstraint(condition=models.Q(('visibility__in', ['NORMAL', 'RESTRICTED'])), name='matters_visibility_vocabulary'),
        ),
    ]
