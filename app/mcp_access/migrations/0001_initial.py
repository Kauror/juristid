"""MCP access: grants, authenticators, enrolment links, codes, tokens, events (docs/adr/0185).

Additive: seven new tables, no other app's table touched. The event table is
append-only in the database, by the shared `core_reject_mutation()` trigger
function, as every audit table is.
"""

import app.core.ids
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name='McpAccessGrant',
            fields=[
                ('id', models.UUIDField(default=app.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(choices=[('PENDING_ENROLMENT', 'Ootab autentija seadistamist'), ('ACTIVE', 'Aktiivne'), ('REVOKED', 'Tühistatud')], default='PENDING_ENROLMENT', max_length=24, verbose_name='olek')),
                ('created_via', models.CharField(blank=True, default='', max_length=300, verbose_name='päritolu')),
                ('activated_at', models.DateTimeField(blank=True, null=True, verbose_name='aktiveeritud')),
                ('revoked_at', models.DateTimeField(blank=True, null=True, verbose_name='tühistatud')),
                ('revoke_reason', models.CharField(blank=True, default='', max_length=300, verbose_name='tühistamise põhjus')),
                ('failed_attempts', models.PositiveIntegerField(default=0, verbose_name='valed koodid järjest')),
                ('locked_until', models.DateTimeField(blank=True, null=True, verbose_name='lukus kuni')),
                ('lock_count', models.PositiveIntegerField(default=0, verbose_name='lukustusi järjest')),
                ('total_failed_attempts', models.PositiveIntegerField(default=0, verbose_name='valed koodid pärast viimast õnnestumist')),
                ('suspended_at', models.DateTimeField(blank=True, null=True, verbose_name='peatatud')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='andis')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='mcp_access_grants', to=settings.AUTH_USER_MODEL, verbose_name='kasutaja')),
            ],
            options={
                'verbose_name': 'MCP ligipääs',
                'verbose_name_plural': 'MCP ligipääsud',
            },
        ),
        migrations.CreateModel(
            name='McpAccessEvent',
            fields=[
                ('id', models.UUIDField(default=app.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('event_type', models.CharField(choices=[('GRANT_CREATED', 'Ligipääs antud'), ('GRANT_REFUSED', 'Ligipääsu andmisest keelduti'), ('ENROLMENT_LINK_ISSUED', 'Seadistuslink väljastatud'), ('ENROLMENT_COMPLETED', 'Autentija seadistatud'), ('ENROLMENT_REFUSED', 'Seadistamisest keelduti'), ('AUTHORIZATION_GRANTED', 'Ühendus lubatud'), ('AUTHORIZATION_DENIED', 'Kasutaja keeldus'), ('AUTHORIZATION_REFUSED', 'Ühenduse lubamisest keelduti'), ('GRANT_LOCKED', 'Ligipääs ajutiselt lukus'), ('GRANT_SUSPENDED', 'Ligipääs peatatud valede koodide tõttu'), ('GRANT_UNLOCKED', 'Lukk eemaldatud'), ('TOKEN_ISSUED', 'Võtmed väljastatud'), ('TOKEN_REFRESHED', 'Võtmed uuendatud'), ('TOKEN_REFUSED', 'Võtmete väljastamisest keelduti'), ('REFRESH_REUSE_DETECTED', 'Uuendusvõtit kasutati korduvalt'), ('TOKEN_REVOKED', 'Võti tühistatud'), ('GRANT_REVOKED', 'Ligipääs tühistatud')], max_length=32, verbose_name='sündmus')),
                ('succeeded', models.BooleanField(default=True, verbose_name='õnnestus')),
                ('client_id', models.CharField(blank=True, default='', max_length=500, verbose_name='klient')),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True, verbose_name='IP-aadress')),
                ('provenance', models.CharField(blank=True, default='', max_length=300, verbose_name='päritolu')),
                ('detail', models.JSONField(blank=True, default=dict, verbose_name='üksikasjad')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='kasutaja')),
                ('grant', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='events', to='mcp_access.mcpaccessgrant', verbose_name='ligipääs')),
            ],
            options={
                'verbose_name': 'MCP ligipääsu sündmus',
                'verbose_name_plural': 'MCP ligipääsu sündmused',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='McpAuthorizeThrottle',
            fields=[
                ('id', models.UUIDField(default=app.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('key', models.CharField(max_length=64, unique=True, verbose_name='tunnus')),
                ('failures', models.PositiveIntegerField(default=0, verbose_name='ebaõnnestumisi')),
                ('window_started_at', models.DateTimeField(verbose_name='aken algas')),
            ],
            options={
                'verbose_name': 'MCP autoriseerimise piirang',
                'verbose_name_plural': 'MCP autoriseerimise piirangud',
            },
        ),
        migrations.CreateModel(
            name='McpAuthenticator',
            fields=[
                ('id', models.UUIDField(default=app.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('encrypted_secret', models.TextField(verbose_name='krüpteeritud saladus')),
                ('last_used_step', models.BigIntegerField(default=0, verbose_name='viimane kasutatud samm')),
                ('confirmed_at', models.DateTimeField(verbose_name='kinnitatud')),
                ('grant', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='authenticator', to='mcp_access.mcpaccessgrant', verbose_name='ligipääs')),
            ],
            options={
                'verbose_name': 'MCP autentija',
                'verbose_name_plural': 'MCP autentijad',
            },
        ),
        migrations.CreateModel(
            name='McpAuthorizationCode',
            fields=[
                ('id', models.UUIDField(default=app.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('digest', models.CharField(max_length=64, unique=True, verbose_name='räsi')),
                ('client_id', models.CharField(max_length=500, verbose_name='klient')),
                ('redirect_uri', models.CharField(max_length=1000, verbose_name='tagasisuunamise aadress')),
                ('code_challenge', models.CharField(max_length=128, verbose_name='PKCE väljakutse')),
                ('scope', models.CharField(max_length=200, verbose_name='ulatus')),
                ('resource', models.CharField(max_length=1000, verbose_name='ressurss')),
                ('user_epoch', models.PositiveIntegerField(verbose_name='kasutaja turvaversioon')),
                ('expires_at', models.DateTimeField(verbose_name='aegub')),
                ('used_at', models.DateTimeField(blank=True, null=True, verbose_name='kasutatud')),
                ('family_id', models.UUIDField(blank=True, null=True, verbose_name='võtmepere')),
                ('grant', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='authorization_codes', to='mcp_access.mcpaccessgrant', verbose_name='ligipääs')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='kasutaja')),
            ],
            options={
                'verbose_name': 'MCP autoriseerimiskood',
                'verbose_name_plural': 'MCP autoriseerimiskoodid',
            },
        ),
        migrations.CreateModel(
            name='McpEnrolmentLink',
            fields=[
                ('id', models.UUIDField(default=app.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('selector', models.CharField(max_length=32, unique=True, verbose_name='valija')),
                ('verifier_digest', models.CharField(max_length=64, verbose_name='tõendi räsi')),
                ('expires_at', models.DateTimeField(verbose_name='aegub')),
                ('used_at', models.DateTimeField(blank=True, null=True, verbose_name='kasutatud')),
                ('invalidated_at', models.DateTimeField(blank=True, null=True, verbose_name='kehtetuks tehtud')),
                ('issued_via', models.CharField(blank=True, default='', max_length=300, verbose_name='päritolu')),
                ('grant', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='enrolment_links', to='mcp_access.mcpaccessgrant', verbose_name='ligipääs')),
            ],
            options={
                'verbose_name': 'MCP seadistuslink',
                'verbose_name_plural': 'MCP seadistuslingid',
            },
        ),
        migrations.CreateModel(
            name='McpToken',
            fields=[
                ('id', models.UUIDField(default=app.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('kind', models.CharField(choices=[('ACCESS', 'Ligipääsuvõti'), ('REFRESH', 'Uuendusvõti')], max_length=8, verbose_name='liik')),
                ('digest', models.CharField(max_length=64, unique=True, verbose_name='räsi')),
                ('family_id', models.UUIDField(db_index=True, default=uuid.uuid4, verbose_name='võtmepere')),
                ('client_id', models.CharField(max_length=500, verbose_name='klient')),
                ('scopes', models.CharField(max_length=200, verbose_name='ulatus')),
                ('resource', models.CharField(max_length=1000, verbose_name='ressurss')),
                ('user_epoch', models.PositiveIntegerField(verbose_name='kasutaja turvaversioon')),
                ('issued_at', models.DateTimeField(verbose_name='väljastatud')),
                ('expires_at', models.DateTimeField(verbose_name='aegub')),
                ('revoked_at', models.DateTimeField(blank=True, null=True, verbose_name='tühistatud')),
                ('revoke_reason', models.CharField(blank=True, default='', max_length=64, verbose_name='tühistamise põhjus')),
                ('last_used_at', models.DateTimeField(blank=True, null=True, verbose_name='viimati kasutatud')),
                ('grant', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='tokens', to='mcp_access.mcpaccessgrant', verbose_name='ligipääs')),
                ('replaced_by', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='replaces', to='mcp_access.mcptoken', verbose_name='asendaja')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='kasutaja')),
            ],
            options={
                'verbose_name': 'MCP võti',
                'verbose_name_plural': 'MCP võtmed',
            },
        ),
        migrations.AddConstraint(
            model_name='mcpaccessgrant',
            constraint=models.UniqueConstraint(condition=models.Q(('status', 'REVOKED'), _negated=True), fields=('user',), name='mcp_access_one_open_grant_per_user'),
        ),
        migrations.AddConstraint(
            model_name='mcpenrolmentlink',
            constraint=models.UniqueConstraint(condition=models.Q(('invalidated_at__isnull', True), ('used_at__isnull', True)), fields=('grant',), name='mcp_access_one_live_link_per_grant'),
        ),
        migrations.AddIndex(
            model_name='mcptoken',
            index=models.Index(fields=['grant', 'revoked_at'], name='mcp_token_grant_revoked'),
        ),
        migrations.RunSQL(
            sql=(
                "CREATE TRIGGER mcp_access_mcpaccessevent_append_only "
                "BEFORE UPDATE OR DELETE ON mcp_access_mcpaccessevent "
                "FOR EACH ROW EXECUTE FUNCTION core_reject_mutation();"
            ),
            reverse_sql=(
                "DROP TRIGGER IF EXISTS mcp_access_mcpaccessevent_append_only "
                "ON mcp_access_mcpaccessevent;"
            ),
        ),
    ]
