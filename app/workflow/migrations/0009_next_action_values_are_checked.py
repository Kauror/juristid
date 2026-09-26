"""A `NextAction` stores only values the product can mean (ENG-043).

`workflow_nextaction` predates the vocabulary-`CHECK` pattern every sibling
precision column carries (`matters_engagement_occurred_precision_vocabulary`
and the rest), and was never retrofitted. So PostgreSQL accepted
`date_precision='BOGUS'`, which every surface then printed as an exact day, and
`target_date IS NULL` beside `QUARTER`, a period of nothing. Five constraints,
and nothing else:

* `kind`, `date_semantics`, `date_precision` and `status` each within their
  `TextChoices`, the same `__in` shape the sibling columns use;
* `target_date IS NOT NULL OR date_precision = 'EXACT'` — docs/adr/0106's undated
  step stays legal, and only its approximate twin is refused.

**No data is read or written.** No column changes, no backfill, no repair. A row
that already breaks one of these makes this migration fail, loudly and before
anything is committed — which is why `check_domain_invariants` exists and why
the release runbook says **run the invariant preflight before migrating**
(deploy/unraid-main/README.md, step 7). A finding there is a human decision
about what the row was meant to say; this migration never guesses it.

**Labelled consequential, correctly.** `AddConstraint` is in
`app.core.deployment.CONSEQUENTIAL_OPERATIONS`: the database starts refusing
rows it accepted. `migration_plan` says so, and `--fail-on-consequential`
stops an unattended deployment here (ENG-014).

**Rolling safety.** The release still serving while this runs cannot write a
row these refuse. Every `NextAction` writer in it goes through `set_next_action`
(which already refused an unknown `kind` and `date_semantics`), the status
transitions (which write `ActionStatus` members only) or `acknowledge_review`
and `NextActionForm`, which write a precision from the offered chips or `EXACT`
— and an undated step from the form is always `EXACT`. The one path that could
have stored another value is a direct service call with a bad precision, and no
caller in that release makes one (the importer normalises `''` to `EXACT`). The
check is applied under `ALTER TABLE … ADD CONSTRAINT`, which scans the table
once while holding its lock; the table is one row per step ever set, small
enough that the scan is not a window anybody will notice.

**Rollback.** Reversing it drops the five constraints and touches no row, so it
can never fail; the release before this one runs unchanged against the result.
"""

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("matters", "0039_intake_session_records_the_matter_it_created"),
        ("workflow", "0008_a_next_action_may_have_no_date"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="nextaction",
            constraint=models.CheckConstraint(
                condition=models.Q(("kind__in", ["DO", "WAIT", "MONITOR"])),
                name="workflow_next_action_kind_vocabulary",
            ),
        ),
        migrations.AddConstraint(
            model_name="nextaction",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("date_semantics__in", ["DEADLINE", "REVIEW_ON", "EXPECTED_AROUND"])
                ),
                name="workflow_next_action_date_semantics_vocabulary",
            ),
        ),
        migrations.AddConstraint(
            model_name="nextaction",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "date_precision__in",
                        ["EXACT", "MONTH", "QUARTER", "HALF_YEAR", "YEAR", "INFERRED"],
                    )
                ),
                name="workflow_next_action_precision_vocabulary",
            ),
        ),
        migrations.AddConstraint(
            model_name="nextaction",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("status__in", ["OPEN", "COMPLETED", "CANCELLED", "SUPERSEDED"])
                ),
                name="workflow_next_action_status_vocabulary",
            ),
        ),
        migrations.AddConstraint(
            model_name="nextaction",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("target_date__isnull", False), ("date_precision", "EXACT"), _connector="OR"
                ),
                name="workflow_next_action_undated_is_exact",
            ),
        ),
    ]
