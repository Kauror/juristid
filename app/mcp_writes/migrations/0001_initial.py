"""MCP writes: proposals, approvals and their events (docs/adr/0189; CONTRACT §5).

Additive: three new tables, no other app's table touched. The event and the
approval tables are append-only in the database, by the shared
`core_reject_mutation()` trigger function, as every audit table is: an approval
is the person's act and is never edited, and the trail of what happened to a
proposal is worth nothing if the code that acted can also edit it.
"""

import app.core.ids
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("core", "0001_initial"),
        ('mcp_access', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='McpWriteProposal',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('token_family_id', models.UUIDField(blank=True, null=True, verbose_name='võtmepere')),
                ('client_id', models.CharField(max_length=500, verbose_name='klient')),
                ('operation', models.CharField(max_length=64, verbose_name='toiming')),
                ('canonical_arguments', models.JSONField(default=dict, verbose_name='kanoonilised argumendid')),
                ('arguments_digest', models.CharField(max_length=64, verbose_name='argumentide räsi')),
                ('target_matter_id', models.UUIDField(blank=True, db_index=True, null=True, verbose_name='teema')),
                ('target_record_type', models.CharField(blank=True, default='', max_length=100, verbose_name='kirje liik')),
                ('target_record_id', models.CharField(blank=True, default='', max_length=100, verbose_name='kirje')),
                ('state', models.JSONField(default=dict, verbose_name='olek ettevalmistamisel')),
                ('preview', models.JSONField(default=dict, verbose_name='eelvaade')),
                ('status', models.CharField(choices=[('PENDING', 'Ootab kinnitamist'), ('APPROVED', 'Kinnitatud'), ('EXECUTED', 'Salvestatud'), ('DECLINED', 'Tagasi lükatud'), ('EXPIRED', 'Aegunud'), ('FAILED', 'Ebaõnnestus'), ('STALE', 'Vananenud')], default='PENDING', max_length=16, verbose_name='olek')),
                ('expires_at', models.DateTimeField(verbose_name='aegub')),
                ('executed_at', models.DateTimeField(blank=True, null=True, verbose_name='salvestatud')),
                ('result', models.JSONField(blank=True, default=dict, verbose_name='tulemus')),
                ('failure_code', models.CharField(blank=True, default='', max_length=32, verbose_name='ebaõnnestumise kood')),
                ('correlation_id', models.UUIDField(verbose_name='päringu tunnus')),
                ('grant', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='mcp_access.mcpaccessgrant', verbose_name='MCP ligipääs')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='kasutaja')),
            ],
            options={
                'verbose_name': 'MCP muudatuse ettepanek',
                'verbose_name_plural': 'MCP muudatuste ettepanekud',
            },
        ),
        migrations.CreateModel(
            name='McpWriteEvent',
            fields=[
                ('id', models.UUIDField(default=app.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('event_type', models.CharField(choices=[('PREPARED', 'Muudatus ette valmistatud'), ('PREPARE_REFUSED', 'Ettevalmistamisest keelduti'), ('APPROVED', 'Kinnitatud'), ('APPROVAL_REFUSED', 'Kinnitamisest keelduti'), ('DECLINED', 'Tagasi lükatud'), ('COMMITTED', 'Salvestatud'), ('COMMIT_REPLAYED', 'Korduv salvestamispäring'), ('COMMIT_REFUSED', 'Salvestamisest keelduti'), ('FAILED', 'Salvestamine ebaõnnestus'), ('EXPIRED', 'Aegunud')], max_length=32, verbose_name='sündmus')),
                ('succeeded', models.BooleanField(default=True, verbose_name='õnnestus')),
                ('client_id', models.CharField(blank=True, default='', max_length=500, verbose_name='klient')),
                ('operation', models.CharField(blank=True, default='', max_length=64, verbose_name='toiming')),
                ('correlation_id', models.UUIDField(blank=True, null=True, verbose_name='päringu tunnus')),
                ('detail', models.JSONField(blank=True, default=dict, verbose_name='üksikasjad')),
                ('grant', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='mcp_access.mcpaccessgrant', verbose_name='MCP ligipääs')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='kasutaja')),
                ('proposal', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='events', to='mcp_writes.mcpwriteproposal', verbose_name='ettepanek')),
            ],
            options={
                'verbose_name': 'MCP muudatuse sündmus',
                'verbose_name_plural': 'MCP muudatuste sündmused',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='McpWriteApproval',
            fields=[
                ('id', models.UUIDField(default=app.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('arguments_digest', models.CharField(max_length=64, verbose_name='argumentide räsi')),
                ('approved_at', models.DateTimeField(verbose_name='kinnitatud')),
                ('expires_at', models.DateTimeField(verbose_name='kehtib kuni')),
                ('method', models.CharField(choices=[('totp', 'Autentija kood'), ('recent_totp', 'Hiljutine autentija kood samas seansis')], max_length=16, verbose_name='viis')),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True, verbose_name='IP-aadress')),
                ('grant', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='mcp_access.mcpaccessgrant', verbose_name='MCP ligipääs')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='kasutaja')),
                ('proposal', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='approval', to='mcp_writes.mcpwriteproposal', verbose_name='ettepanek')),
            ],
            options={
                'verbose_name': 'MCP muudatuse kinnitus',
                'verbose_name_plural': 'MCP muudatuste kinnitused',
            },
        ),
        migrations.AddIndex(
            model_name='mcpwriteproposal',
            index=models.Index(fields=['user', 'status'], name='mcp_write_user_status'),
        ),
        migrations.AddIndex(
            model_name='mcpwriteproposal',
            index=models.Index(fields=['status', 'expires_at'], name='mcp_write_status_expiry'),
        ),
        migrations.RunSQL(
            sql=(
                "CREATE TRIGGER mcp_writes_mcpwriteevent_append_only "
                "BEFORE UPDATE OR DELETE ON mcp_writes_mcpwriteevent "
                "FOR EACH ROW EXECUTE FUNCTION core_reject_mutation();"
            ),
            reverse_sql=(
                "DROP TRIGGER IF EXISTS mcp_writes_mcpwriteevent_append_only "
                "ON mcp_writes_mcpwriteevent;"
            ),
        ),
        migrations.RunSQL(
            sql=(
                "CREATE TRIGGER mcp_writes_mcpwriteapproval_append_only "
                "BEFORE UPDATE OR DELETE ON mcp_writes_mcpwriteapproval "
                "FOR EACH ROW EXECUTE FUNCTION core_reject_mutation();"
            ),
            reverse_sql=(
                "DROP TRIGGER IF EXISTS mcp_writes_mcpwriteapproval_append_only "
                "ON mcp_writes_mcpwriteapproval;"
            ),
        ),
    ]
