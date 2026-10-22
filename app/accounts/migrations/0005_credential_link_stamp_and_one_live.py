"""One live one-time link per person and purpose, bound to the account's credentials.

Three steps (docs/adr/0180 §5):

1. ``credential_stamp`` on every link, empty for the links that exist. A link is
   good only while it equals `tokens.credential_stamp` of its account, which is
   never empty, so every link issued before this migration is dead and must be
   sent again. On every deployment today that is none: the feature is dormant.
   No link becomes good that was not.
2. Any person who holds more than one live link of one purpose — only possible
   through the race this closes — keeps the newest; the older ones are marked
   invalidated, «superseded_by_migration». Nothing is deleted. On a database
   with no such pairs (every deployment today: the feature is dormant) this
   writes nothing.
3. The partial unique index that holds the rule from now on.

Reversing drops the index and the column; the links step 2 invalidated stay
invalidated, which is the state they should have been in.
"""

from django.db import migrations, models
from django.db.models import Count
from django.db.models.functions import Now


def supersede_duplicate_live_links(apps, schema_editor):
    token = apps.get_model("accounts", "AccountCredentialToken")
    live = token.objects.filter(used_at__isnull=True, invalidated_at__isnull=True)
    pairs = (
        live.values("user_id", "purpose")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
        .order_by()
    )
    for pair in pairs:
        rows = list(
            live.filter(user_id=pair["user_id"], purpose=pair["purpose"])
            .order_by("-created_at", "-id")
            .values_list("pk", flat=True)
        )
        token.objects.filter(pk__in=rows[1:]).update(
            invalidated_at=Now(),
            invalidation_reason="superseded_by_migration",
            updated_at=Now(),
        )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_local_authentication"),
    ]

    operations = [
        migrations.AddField(
            model_name="accountcredentialtoken",
            name="credential_stamp",
            field=models.CharField(
                blank=True,
                db_default="",
                default="",
                max_length=64,
                verbose_name="volituste jäljend",
            ),
        ),
        migrations.RunPython(supersede_duplicate_live_links, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="accountcredentialtoken",
            constraint=models.UniqueConstraint(
                condition=models.Q(("invalidated_at__isnull", True), ("used_at__isnull", True)),
                fields=("user", "purpose"),
                name="accounts_token_one_live_per_purpose",
            ),
        ),
    ]
