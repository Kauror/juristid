"""`Lühikokkuvõte` on the Matter, and a private note per person per Matter.

Both additive, both optional, neither backfilled. Every existing Matter keeps a
blank summary until somebody writes one — a plain-language description is the
one thing an importer cannot invent — and no personal note exists until its
owner types into it (Teema redesign §6, §22.4, §33).
"""


import app.core.ids
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('matters', '0009_matter_engagement'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='matter',
            name='brief_summary',
            field=models.TextField(blank=True, help_text='Mida see teema ettevõtjatele tähendab. Kaks kuni kolm lauset tavakeeles.', verbose_name='lühikokkuvõte'),
        ),
        migrations.CreateModel(
            name='MatterPersonalNote',
            fields=[
                ('id', models.UUIDField(default=app.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('body', models.TextField(blank=True, verbose_name='märkmed')),
                ('author', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='matter_personal_notes', to=settings.AUTH_USER_MODEL, verbose_name='kasutaja')),
                ('matter', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='personal_notes', to='matters.matter', verbose_name='teema')),
            ],
            options={
                'verbose_name': 'isiklik märkmik',
                'verbose_name_plural': 'isiklikud märkmikud',
                'ordering': ['-updated_at'],
                'constraints': [models.UniqueConstraint(fields=('matter', 'author'), name='matters_one_personal_note_per_person')],
            },
        ),
    ]
