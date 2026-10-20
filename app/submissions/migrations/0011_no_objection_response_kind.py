"""`Vastus: märkusi ja ettepanekuid ei ole` — Koda's official no-comment answer (docs/adr/0174).

A choices change only: `Submission.kind` has no database constraint over its
vocabulary, so PostgreSQL is not touched, no row changes and every existing
send keeps its kind.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0010_koda_proposal_kind"),
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
                    ("NO_OBJECTION_RESPONSE", "Vastus: märkusi ja ettepanekuid ei ole"),
                    ("OTHER", "Muu"),
                ],
                db_index=True,
                default="FORMAL_OPINION",
                max_length=40,
                verbose_name="liik",
            ),
        ),
    ]
