"""`Tööplaan` — `MatterPlanStep`, and the one relation from `NextAction` to it.

Additive and rolling-safe (docs/adr/0133 §9):

* **A new table**, `workflow_matterplanstep`, with its vocabularies, its title,
  its completion and skip stamps, its template provenance and the partial
  unique index that makes seeding the standard plan idempotent in the database.
* **A new nullable column**, `workflow_nextaction.plan_step_id`. Every existing
  action reads `NULL` and keeps reading it: an action started from a plan step
  is the only thing that ever writes one.

**No data is written.** No existing Matter gains a plan, no `NextAction` is
linked to anything, and no `Koostan arvamuse` step is touched or superseded —
a plan on an old file would be intent nobody stated (docs/adr/0133 §5). A
writer may add the standard plan to an open Matter later, deliberately.

The reverse drops the column and the table, which loses only plan data.
"""

import app.core.ids
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matters", "0044_engagement_lifecycle_tracked"),
        ("workflow", "0010_monitoring_stopped_stage"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MatterPlanStep",
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
                    "position",
                    models.PositiveIntegerField(default=0, verbose_name="järjekord"),
                ),
                ("title", models.CharField(max_length=300, verbose_name="samm")),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("TEMPLATE", "Tavapärane tööplaan"),
                            ("CUSTOM", "Lisatud käsitsi"),
                        ],
                        default="CUSTOM",
                        max_length=16,
                        verbose_name="päritolu",
                    ),
                ),
                (
                    "operation",
                    models.CharField(
                        choices=[
                            ("GENERIC", "Tavaline tegevus"),
                            ("WEBSITE_OVERVIEW", "Ülevaade / uudis"),
                            ("ENGAGEMENT", "Kaasamine"),
                            ("SUBMISSION", "Koja arvamus"),
                        ],
                        default="GENERIC",
                        max_length=32,
                        verbose_name="seotud toiming",
                    ),
                ),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("SUGGESTED", "Soovitus"),
                            ("PLANNED", "Plaanis"),
                            ("COMPLETED", "Tehtud"),
                            ("SKIPPED", "Vahele jäetud"),
                        ],
                        db_index=True,
                        default="PLANNED",
                        max_length=16,
                        verbose_name="olek",
                    ),
                ),
                (
                    "template_key",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                (
                    "template_version",
                    models.PositiveSmallIntegerField(blank=True, null=True),
                ),
                (
                    "template_step_key",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                (
                    "completed_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="tehtud"),
                ),
                (
                    "skipped_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="vahele jäetud"
                    ),
                ),
                (
                    "completed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="completed_plan_steps",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="created_plan_steps",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "matter",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="plan_steps",
                        to="matters.matter",
                        verbose_name="teema",
                    ),
                ),
                (
                    "skipped_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="skipped_plan_steps",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "tööplaani samm",
                "verbose_name_plural": "tööplaani sammud",
                "ordering": ["matter", "position", "created_at"],
            },
        ),
        migrations.AddField(
            model_name="nextaction",
            name="plan_step",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="actions",
                to="workflow.matterplanstep",
                verbose_name="tööplaani samm",
            ),
        ),
        migrations.AddIndex(
            model_name="matterplanstep",
            index=models.Index(
                fields=["matter", "position"], name="workflow_plan_step_order"
            ),
        ),
        migrations.AddConstraint(
            model_name="matterplanstep",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("state__in", ["SUGGESTED", "PLANNED", "COMPLETED", "SKIPPED"])
                ),
                name="workflow_plan_step_state_vocabulary",
            ),
        ),
        migrations.AddConstraint(
            model_name="matterplanstep",
            constraint=models.CheckConstraint(
                condition=models.Q(("source__in", ["TEMPLATE", "CUSTOM"])),
                name="workflow_plan_step_source_vocabulary",
            ),
        ),
        migrations.AddConstraint(
            model_name="matterplanstep",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "operation__in",
                        ["GENERIC", "WEBSITE_OVERVIEW", "ENGAGEMENT", "SUBMISSION"],
                    )
                ),
                name="workflow_plan_step_operation_vocabulary",
            ),
        ),
        migrations.AddConstraint(
            model_name="matterplanstep",
            constraint=models.CheckConstraint(
                condition=models.Q(("title", ""), _negated=True),
                name="workflow_plan_step_title_required",
            ),
        ),
        migrations.AddConstraint(
            model_name="matterplanstep",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("completed_at__isnull", False), ("state", "COMPLETED")),
                    models.Q(
                        models.Q(("state", "COMPLETED"), _negated=True),
                        ("completed_at__isnull", True),
                    ),
                    _connector="OR",
                ),
                name="workflow_plan_step_completion_stamped",
            ),
        ),
        migrations.AddConstraint(
            model_name="matterplanstep",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("skipped_at__isnull", False), ("state", "SKIPPED")),
                    models.Q(
                        models.Q(("state", "SKIPPED"), _negated=True),
                        ("skipped_at__isnull", True),
                    ),
                    _connector="OR",
                ),
                name="workflow_plan_step_skip_stamped",
            ),
        ),
        migrations.AddConstraint(
            model_name="matterplanstep",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("source", "TEMPLATE"),
                        ("template_version__isnull", False),
                        models.Q(("template_key", ""), _negated=True),
                        models.Q(("template_step_key", ""), _negated=True),
                    ),
                    models.Q(
                        ("source", "CUSTOM"),
                        ("template_key", ""),
                        ("template_step_key", ""),
                        ("template_version__isnull", True),
                    ),
                    _connector="OR",
                ),
                name="workflow_plan_step_template_provenance",
            ),
        ),
        migrations.AddConstraint(
            model_name="matterplanstep",
            constraint=models.UniqueConstraint(
                condition=models.Q(("source", "TEMPLATE")),
                fields=("matter", "template_key", "template_step_key"),
                name="workflow_plan_step_template_once",
            ),
        ),
    ]
