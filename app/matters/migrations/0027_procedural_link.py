"""`Menetluse link` — where the official proceeding on a Matter lives.

One new table and nothing else. **No backfill, no data migration and no
`RunPython`**, deliberately and for a reason this file is the right place to
state: the obvious temptation is to mine the Matters this application already
holds for addresses — a URL in a `Märge`, a `Kaasamine`'s campaign link, the
free text of a historical register row — and file each one as a procedural
link. Every one of those would be a guess about *what the address was for*,
written into a column whose whole content is what the lawyer meant by it, with
no provenance recording that the system rather than a person had decided. A
wrong `EIS` on a file is worse than no link at all, because it is a wrong answer
somebody will act on without checking (docs/adr/0089 §4, §12).

So historical data is left exactly as it is, and the table starts empty.

`matters_procedural_link_one_row_per_address` is a unique index and is created
here on an empty table, so it takes no lock worth naming. It is what makes a
double-click, a browser retry and a stale response unable to leave one Matter
holding one address twice (docs/adr/0089 §6).
"""

import app.core.ids
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("matters", "0026_overview_news_verbose_name"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MatterProceduralLink",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=app.core.ids.uuid7,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "visibility_override",
                    models.CharField(
                        blank=True,
                        choices=[("NORMAL", "Tavaline"), ("RESTRICTED", "Piiratud")],
                        db_index=True,
                        default="",
                        help_text="Tühi tähendab, et nähtavus päritakse teemalt.",
                        max_length=16,
                        verbose_name="nähtavuse kitsendus",
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("EIS", "EIS"),
                            ("MINISTRY_REGISTER", "Ministeeriumi dokumendiregister"),
                            ("EU_PROCEDURE", "ELi menetlus"),
                            ("RIIGIKOGU", "Riigikogu"),
                            ("OTHER", "Muu menetluslink"),
                        ],
                        db_index=True,
                        max_length=32,
                        verbose_name="menetluse liik",
                    ),
                ),
                ("url", models.URLField(max_length=1000, verbose_name="link")),
                (
                    "label",
                    models.CharField(
                        blank=True,
                        help_text=(
                            "Valikuline — näiteks „Eelnõu 123 SE” või „VTK kooskõlastusring”."
                        ),
                        max_length=120,
                        verbose_name="nimetus",
                    ),
                ),
            ],
            options={
                "verbose_name": "menetluse link",
                "verbose_name_plural": "menetluse lingid",
                "ordering": ["kind", "created_at", "id"],
            },
        ),
        migrations.AddField(
            model_name="matterprocedurallink",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="recorded_procedural_links",
                to=settings.AUTH_USER_MODEL,
                verbose_name="lisas",
            ),
        ),
        migrations.AddField(
            model_name="matterprocedurallink",
            name="matter",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="procedural_links",
                to="matters.matter",
                verbose_name="teema",
            ),
        ),
        migrations.AddIndex(
            model_name="matterprocedurallink",
            index=models.Index(fields=["matter", "kind"], name="matters_proclink_matter_kind"),
        ),
        migrations.AddConstraint(
            model_name="matterprocedurallink",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("kind__in", ["EIS", "MINISTRY_REGISTER", "EU_PROCEDURE", "RIIGIKOGU", "OTHER"])
                ),
                name="matters_procedural_link_kind_vocabulary",
            ),
        ),
        migrations.AddConstraint(
            model_name="matterprocedurallink",
            constraint=models.CheckConstraint(
                condition=models.Q(("url", ""), _negated=True),
                name="matters_procedural_link_has_url",
            ),
        ),
        migrations.AddConstraint(
            model_name="matterprocedurallink",
            constraint=models.UniqueConstraint(
                fields=("matter", "url"), name="matters_procedural_link_one_row_per_address"
            ),
        ),
        migrations.AddConstraint(
            model_name="matterprocedurallink",
            constraint=models.CheckConstraint(
                condition=models.Q(("visibility_override__in", ["", "NORMAL", "RESTRICTED"])),
                name="matters_procedural_link_visibility_vocabulary",
            ),
        ),
    ]
