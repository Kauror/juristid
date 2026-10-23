"""AuthMethod gains `mcp`: an approved MCP write (docs/adr/0189). Choices only."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('audit', '0039_r1_admin_reference_reminder_events'),
    ]

    operations = [
        migrations.AlterField(
            model_name='changeevent',
            name='auth_method',
            field=models.CharField(blank=True, choices=[('shared_gate', 'Ühisparool ja valitud kasutaja'), ('local_password', 'Isiklik parool'), ('administrator_session', 'Halduri isiklik kinnitus ühisparooli taga'), ('cloudflare_access', 'Cloudflare Access'), ('mcp', 'AI-klient (MCP), isiklikult kinnitatud'), ('dev_login', 'Arenduse sisselogimine'), ('system', 'Süsteem või haldusskript')], default='', max_length=32, verbose_name='autentimisviis'),
        ),
        migrations.AlterField(
            model_name='securityauditevent',
            name='auth_method',
            field=models.CharField(blank=True, choices=[('shared_gate', 'Ühisparool ja valitud kasutaja'), ('local_password', 'Isiklik parool'), ('administrator_session', 'Halduri isiklik kinnitus ühisparooli taga'), ('cloudflare_access', 'Cloudflare Access'), ('mcp', 'AI-klient (MCP), isiklikult kinnitatud'), ('dev_login', 'Arenduse sisselogimine'), ('system', 'Süsteem või haldusskript')], default='', max_length=32, verbose_name='autentimisviis'),
        ),
    ]
