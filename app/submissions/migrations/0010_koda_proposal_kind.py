"""`Koja ettepanek või pöördumine` — one more kind of outbound send (docs/adr/0151 §5).

What Koda sends on a Teema it started itself is a proposal or an appeal, not an
opinion it was asked for, and it is recorded as one. A choices change only:
`Submission.kind` has no database constraint over its vocabulary, so PostgreSQL
is not touched, no row changes and every existing send keeps its kind.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0009_opinion_sent_files"),
    ]

    operations = [
        migrations.AlterField(
            model_name="submission",
            name="kind",
            field=models.CharField(
                choices=[
                    ("FORMAL_OPINION", "Ametlik arvamus"),
                    ("SUPPLEMENTARY_OPINION", "Täiendav arvamus"),
                    ("PARLIAMENTARY_SUBMISSION", "Pöördumine Riigikogule"),
                    ("JOINT_LETTER", "Ühispöördumine"),
                    ("INFORMAL_WRITTEN_RESPONSE", "Mitteametlik kirjalik vastus"),
                    ("KODA_PROPOSAL", "Koja ettepanek või pöördumine"),
                    ("OTHER", "Muu"),
                ],
                db_index=True,
                default="FORMAL_OPINION",
                max_length=40,
                verbose_name="liik",
            ),
        ),
    ]
