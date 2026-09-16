"""Provenance, an optional author, a source label and the lawyer's own note.

Additive, and deliberately carrying no data. Five schema changes and one
`choices` edit, none of which reads or writes a row:

* `provenance`, defaulting to `LEGACY` — which is what every existing row
  truthfully says. **No `RunPython` and nothing to backfill from.** The whole
  corpus was recorded through one panel that never asked the question, so the two
  things a backfill could have read — whether a `Kaasamine` is linked, or
  whether the organisation is a member rather than a ministry — are exactly the
  inferences docs/adr/0088 §3 refuses. A ministry does answer consultations and a
  member's position paper does get found on a website, so each rule would be
  wrong for some real record, and a wrong provenance is worse than an
  unspecified one.
* `source_label` and `lawyer_note`, both blank on every existing row, which is
  also true of all of them: neither field existed to be filled.
* `organisation` becomes nullable. **Every stored row keeps its organisation**
  — widening a column refuses nothing and rewrites nothing — and
  `matters_external_position_author_or_label` is what keeps the rule the `NOT
  NULL` used to keep, with the one exception aggregate received feedback needs.
* The three `CHECK`s are validated against the existing table as they are added,
  and every existing row satisfies all three: each has an organisation, each has
  an empty `source_label`, and `LEGACY` is in the vocabulary.
* `Entry.kind` gains `PROCEDURAL_DEVELOPMENT`. An `AlterField` over a `choices`
  list is Python metadata and not a database object — PostgreSQL never saw the
  old list and will never see the new one.

Reversible in full: `AddField`, `AddIndex` and `AddConstraint` all reverse, and
re-narrowing `organisation` to `NOT NULL` succeeds on any database whose rows
this migration did not change. What a reverse would lose is what people wrote
into the three new columns, which is the ordinary cost of any additive column.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('matters', '0026_overview_news_verbose_name'),
        ('organisations', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='matterexternalposition',
            name='lawyer_note',
            field=models.TextField(blank=True, verbose_name='juristi märkus'),
        ),
        migrations.AddField(
            model_name='matterexternalposition',
            name='provenance',
            field=models.CharField(choices=[('RECEIVED', 'Meile saadetud tagasiside'), ('DISCOVERED', 'Teiste arvamus'), ('LEGACY', 'Täpsustamata')], db_index=True, default='LEGACY', max_length=16, verbose_name='päritolu'),
        ),
        migrations.AddField(
            model_name='matterexternalposition',
            name='source_label',
            field=models.CharField(blank=True, max_length=200, verbose_name='allikas'),
        ),
        migrations.AlterField(
            model_name='entry',
            name='kind',
            field=models.CharField(choices=[('NOTE', 'Märkus'), ('MEETING', 'Kohtumine'), ('CALL', 'Telefonikõne'), ('HEARING', 'Istung või kuulamine'), ('WORKING_GROUP', 'Töörühm'), ('JOINT_COORDINATION', 'Ühistegevuse koordineerimine'), ('PUBLIC_STATEMENT', 'Avalik esinemine või kommentaar'), ('PROCEDURAL_DEVELOPMENT', 'Menetluse areng'), ('OTHER', 'Muu')], db_index=True, default='NOTE', max_length=32, verbose_name='liik'),
        ),
        migrations.AlterField(
            model_name='matterexternalposition',
            name='organisation',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='matter_external_positions', to='organisations.organisation', verbose_name='organisatsioon'),
        ),
        migrations.AddIndex(
            model_name='matterexternalposition',
            index=models.Index(fields=['matter', 'provenance'], name='matters_extpos_matter_prov'),
        ),
        migrations.AddConstraint(
            model_name='matterexternalposition',
            constraint=models.CheckConstraint(condition=models.Q(('provenance__in', ['RECEIVED', 'DISCOVERED', 'LEGACY'])), name='matters_external_position_provenance_vocabulary'),
        ),
        migrations.AddConstraint(
            model_name='matterexternalposition',
            constraint=models.CheckConstraint(condition=models.Q(('organisation__isnull', False), models.Q(('provenance', 'RECEIVED'), models.Q(('source_label', ''), _negated=True)), _connector='OR'), name='matters_external_position_author_or_label'),
        ),
        migrations.AddConstraint(
            model_name='matterexternalposition',
            constraint=models.CheckConstraint(condition=models.Q(('source_label', ''), ('provenance', 'RECEIVED'), _connector='OR'), name='matters_external_position_label_is_received'),
        ),
    ]
