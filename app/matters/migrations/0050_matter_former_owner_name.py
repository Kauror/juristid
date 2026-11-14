from django.db import migrations, models


class Migration(migrations.Migration):
    """`Matter.former_owner_name`, additive (owner decision 14.11.2026).

    The name Lovable gives as a synchronised Teema's owner when that person has
    no assignable Juristid account (former staff). A text column, not a user
    foreign key: no account is created for anybody. Every existing row reads
    `''`; the nightly synchronisation fills it.

    **A database default (`db_default=""`)**, as 0049 does: the release still
    serving between `migrate` and the swap — and the image a code-only rollback
    returns to — inserts Matters without naming the column, and a NOT NULL
    column without a database default would refuse every one of them (ENG-014).
    """

    dependencies = [
        ("matters", "0049_entry_url"),
    ]

    operations = [
        migrations.AddField(
            model_name="matter",
            name="former_owner_name",
            field=models.CharField(
                blank=True,
                db_default="",
                default="",
                help_text="Lovable'is nimetatud vastutaja, kellel pole Juristides kasutajat.",
                max_length=255,
                verbose_name="Endine vastutaja",
            ),
        ),
    ]
