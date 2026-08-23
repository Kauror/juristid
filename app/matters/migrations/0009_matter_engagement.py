"""`Kaasamine` — a Matter-owned record of how members and stakeholders were asked.

One new table and nothing else. No data migration, because there is nothing to
migrate: the department keeps this in memory and in mail folders today, and
inventing rows from either would be fabricating a record of outreach that may
never have happened.

The check constraints are the ones the service also enforces. Both, because a
service is what the product calls and a constraint is what the database
guarantees when somebody uses a shell (Agent-F brief 17).
"""


import app.core.ids
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('matters', '0008_multiple_source_organisations'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='MatterEngagement',
            fields=[
                ('id', models.UUIDField(default=app.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('visibility_override', models.CharField(blank=True, choices=[('NORMAL', 'Tavaline'), ('RESTRICTED', 'Piiratud')], db_index=True, default='', help_text='Tühi tähendab, et nähtavus päritakse teemalt.', max_length=16, verbose_name='nähtavuse kitsendus')),
                ('kind', models.CharField(choices=[('WEB_CALL', 'Kaasamiskutse veebis'), ('EMAIL_CAMPAIGN', 'E-kiri või kampaania'), ('SURVEY', 'Küsitlus'), ('OTHER', 'Muu')], db_index=True, default='OTHER', max_length=32, verbose_name='liik')),
                ('title', models.CharField(max_length=500, verbose_name='pealkiri')),
                ('url', models.URLField(blank=True, max_length=1000, verbose_name='link')),
                ('note', models.TextField(blank=True, verbose_name='märkus')),
                ('occurred_on', models.DateField(blank=True, db_index=True, null=True, verbose_name='kuupäev')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='recorded_engagements', to=settings.AUTH_USER_MODEL, verbose_name='lisas')),
                ('matter', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='engagements', to='matters.matter', verbose_name='teema')),
            ],
            options={
                'verbose_name': 'kaasamine',
                'verbose_name_plural': 'kaasamised',
                'ordering': [models.OrderBy(models.F('occurred_on'), descending=True, nulls_last=True), '-created_at', '-id'],
                'indexes': [models.Index(fields=['matter', '-occurred_on'], name='matters_engagement_matter_date')],
                'constraints': [models.CheckConstraint(condition=models.Q(('title', ''), _negated=True), name='matters_engagement_title_required'), models.CheckConstraint(condition=models.Q(('kind__in', ['WEB_CALL', 'EMAIL_CAMPAIGN', 'SURVEY', 'OTHER'])), name='matters_engagement_kind_vocabulary'), models.CheckConstraint(condition=models.Q(('visibility_override__in', ['', 'NORMAL', 'RESTRICTED'])), name='matters_engagement_visibility_vocabulary')],
            },
        ),
    ]
