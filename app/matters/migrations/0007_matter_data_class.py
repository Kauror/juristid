"""Real business data and development data become distinguishable.

Every existing row becomes REAL through the column default, which is the whole
reason no RunPython is needed: the historical register, the OneNote corpus and
everything a lawyer has filed is real work, and nothing here has to decide
otherwise on their behalf.

Two constraints rather than Django choices alone. The vocabulary one keeps a
value out that would be missing from `real_data()` *and* from `test_data()` —
invisible to every statistic and to the maintenance planner meant to find it.
The second refuses TEST on anything the system did not create itself, so an
imported register row cannot be marked disposable by a bulk update, a data
migration or a shell session (Agent-C brief 10, 12, 38, 45).
"""


from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('matters', '0006_policy_area_other'),
        ('organisations', '0001_initial'),
        ('taxonomy', '0001_initial'),
        ('workflow', '0005_deadline_requires_a_date'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='matter',
            name='data_class',
            field=models.CharField(choices=[('REAL', 'Pärisandmed'), ('TEST', 'Testandmed')], db_index=True, default='REAL', help_text='Testandmed on arenduseks loodud kirjed; need ei kuulu päris aruandlusse.', max_length=16, verbose_name='andmeklass'),
        ),
        migrations.AddConstraint(
            model_name='matter',
            constraint=models.CheckConstraint(condition=models.Q(('data_class__in', ['REAL', 'TEST'])), name='matters_data_class_vocabulary'),
        ),
        migrations.AddConstraint(
            model_name='matter',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('data_class', 'TEST'), _negated=True), ('origin', 'NATIVE'), _connector='OR'), name='matters_test_data_is_native'),
        ),
    ]
