"""MCP writes: format-2 approvals bound to what the trusted page derived, and request budgets.

SEC-009 / SEC-014 (docs/adr/0234). Additive, and safe for the release it
replaces to run against:

* `McpWriteProposal.format_version` (default 1 — a row that names no format is
  an old one, which format-2 code never approves) and ``arguments`` (the
  validated client arguments the approval page derives the change from again).
* `McpWriteApproval` gains the format, the binding digest and copies of the
  canonical arguments, state and preview the person approved, all with
  defaults: an approval row stays append-only (its trigger is untouched).
* `McpWriteCounter`: the gateway's fixed-window counters.
* Two triggers on the proposal table, the database's own guarantee whatever
  role writes:

  - ``mcp_writes_proposal_insert_guard``: a proposal is born PENDING, with no
    result, no failure code and no execution time — nobody can insert an
    already approved or executed one;
  - ``mcp_writes_proposal_update_guard``: what was proposed (identity,
    operation, arguments, canonical arguments, digest, target, state, preview,
    expiry, client, token family, correlation id) never changes after insert;
    the status only moves forward (PENDING → anything, APPROVED → a final
    status, a final status → nothing); an execution time and a result only
    come with EXECUTED; and a proposal is never deleted.

* Database-level defaults for every new column (format 1, empty JSON, empty
  digest), so r94's code — which names none of them — can still insert during
  an app rollback, and what it inserts is format 1.
* Every outstanding format-1 proposal (PENDING or APPROVED, unexecuted) is
  retired as EXPIRED with ``failure_code = format_retired`` and one
  ``FORMAT_RETIRED`` event: an approval given under the old rules is never
  reinterpreted under the new ones. Executed proposals, their receipts, every
  approval and every event stay exactly as they are.

Reversing drops the triggers, the counter table and the new columns; the
retired proposals stay EXPIRED (their events are append-only history).
"""

import django.db.models.deletion  # noqa: F401 - kept for the migration framework's imports
import uuid

from django.db import migrations, models

UPDATE_GUARD = r"""
CREATE OR REPLACE FUNCTION mcp_writes_proposal_update_guard() RETURNS trigger AS $body$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'MCP proposals are never deleted' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.format_version IS DISTINCT FROM OLD.format_version
       OR NEW.user_id IS DISTINCT FROM OLD.user_id
       OR NEW.grant_id IS DISTINCT FROM OLD.grant_id
       OR NEW.token_family_id IS DISTINCT FROM OLD.token_family_id
       OR NEW.client_id IS DISTINCT FROM OLD.client_id
       OR NEW.operation IS DISTINCT FROM OLD.operation
       OR NEW.arguments IS DISTINCT FROM OLD.arguments
       OR NEW.canonical_arguments IS DISTINCT FROM OLD.canonical_arguments
       OR NEW.arguments_digest IS DISTINCT FROM OLD.arguments_digest
       OR NEW.target_matter_id IS DISTINCT FROM OLD.target_matter_id
       OR NEW.target_record_type IS DISTINCT FROM OLD.target_record_type
       OR NEW.target_record_id IS DISTINCT FROM OLD.target_record_id
       OR NEW.state IS DISTINCT FROM OLD.state
       OR NEW.preview IS DISTINCT FROM OLD.preview
       OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
       OR NEW.correlation_id IS DISTINCT FROM OLD.correlation_id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'what an MCP proposal proposes never changes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.status IN ('EXECUTED', 'DECLINED', 'EXPIRED', 'FAILED', 'STALE') THEN
        RAISE EXCEPTION 'a finished MCP proposal never changes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.status = 'APPROVED' AND NEW.status IN ('PENDING') THEN
        RAISE EXCEPTION 'an MCP proposal status only moves forward'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.status <> 'EXECUTED' AND (
        NEW.executed_at IS NOT NULL OR NEW.result IS DISTINCT FROM OLD.result
    ) THEN
        RAISE EXCEPTION 'only an executed MCP proposal has a result'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$body$ LANGUAGE plpgsql;

CREATE TRIGGER mcp_writes_proposal_update_guard
BEFORE UPDATE OR DELETE ON mcp_writes_mcpwriteproposal
FOR EACH ROW EXECUTE FUNCTION mcp_writes_proposal_update_guard();
"""

INSERT_GUARD = r"""
CREATE OR REPLACE FUNCTION mcp_writes_proposal_insert_guard() RETURNS trigger AS $body$
BEGIN
    IF NEW.status <> 'PENDING'
       OR NEW.executed_at IS NOT NULL
       OR NEW.result IS DISTINCT FROM '{}'::jsonb
       OR NEW.failure_code <> '' THEN
        RAISE EXCEPTION 'an MCP proposal is born pending'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$body$ LANGUAGE plpgsql;

CREATE TRIGGER mcp_writes_proposal_insert_guard
BEFORE INSERT ON mcp_writes_mcpwriteproposal
FOR EACH ROW EXECUTE FUNCTION mcp_writes_proposal_insert_guard();
"""

#: Database-level defaults for every new column, so the release this one
#: replaces (r94), which names none of them, can still insert proposals and
#: approvals against this schema during a rollback — and they land as format 1.
ROLLBACK_DEFAULTS = """
ALTER TABLE mcp_writes_mcpwriteproposal ALTER COLUMN format_version SET DEFAULT 1;
ALTER TABLE mcp_writes_mcpwriteproposal ALTER COLUMN arguments SET DEFAULT '{}'::jsonb;
ALTER TABLE mcp_writes_mcpwriteapproval ALTER COLUMN format_version SET DEFAULT 1;
ALTER TABLE mcp_writes_mcpwriteapproval ALTER COLUMN binding_digest SET DEFAULT '';
ALTER TABLE mcp_writes_mcpwriteapproval ALTER COLUMN canonical_arguments SET DEFAULT '{}'::jsonb;
ALTER TABLE mcp_writes_mcpwriteapproval ALTER COLUMN state SET DEFAULT '{}'::jsonb;
ALTER TABLE mcp_writes_mcpwriteapproval ALTER COLUMN preview SET DEFAULT '{}'::jsonb;
"""

DROP_GUARDS = """
DROP TRIGGER IF EXISTS mcp_writes_proposal_update_guard ON mcp_writes_mcpwriteproposal;
DROP TRIGGER IF EXISTS mcp_writes_proposal_insert_guard ON mcp_writes_mcpwriteproposal;
DROP FUNCTION IF EXISTS mcp_writes_proposal_update_guard();
DROP FUNCTION IF EXISTS mcp_writes_proposal_insert_guard();
"""


def retire_format_one(apps, schema_editor):
    """Every unexecuted format-1 proposal still open becomes EXPIRED, with one event each."""
    Proposal = apps.get_model("mcp_writes", "McpWriteProposal")
    Event = apps.get_model("mcp_writes", "McpWriteEvent")
    open_ones = Proposal.objects.filter(format_version=1, status__in=["PENDING", "APPROVED"])
    for proposal in open_ones.iterator():
        previous = proposal.status
        proposal.status = "EXPIRED"
        proposal.failure_code = "format_retired"
        proposal.save(update_fields=["status", "failure_code", "updated_at"])
        Event.objects.create(
            id=uuid.uuid4(),
            event_type="FORMAT_RETIRED",
            succeeded=False,
            proposal_id=proposal.pk,
            user_id=proposal.user_id,
            grant_id=proposal.grant_id,
            client_id=proposal.client_id,
            operation=proposal.operation,
            correlation_id=proposal.correlation_id,
            detail={"code": "format_retired", "previous_status": previous, "format": 1},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("mcp_writes", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="mcpwriteproposal",
            name="format_version",
            field=models.PositiveSmallIntegerField(default=1, verbose_name="vorming"),
        ),
        migrations.AddField(
            model_name="mcpwriteproposal",
            name="arguments",
            field=models.JSONField(default=dict, verbose_name="argumendid"),
        ),
        migrations.AddField(
            model_name="mcpwriteapproval",
            name="format_version",
            field=models.PositiveSmallIntegerField(default=1, verbose_name="vorming"),
        ),
        migrations.AddField(
            model_name="mcpwriteapproval",
            name="binding_digest",
            field=models.CharField(
                blank=True, default="", max_length=64, verbose_name="sidumise räsi"
            ),
        ),
        migrations.AddField(
            model_name="mcpwriteapproval",
            name="canonical_arguments",
            field=models.JSONField(
                blank=True, default=dict, verbose_name="kinnitatud kanoonilised argumendid"
            ),
        ),
        migrations.AddField(
            model_name="mcpwriteapproval",
            name="state",
            field=models.JSONField(blank=True, default=dict, verbose_name="kinnitatud olek"),
        ),
        migrations.AddField(
            model_name="mcpwriteapproval",
            name="preview",
            field=models.JSONField(blank=True, default=dict, verbose_name="kinnitatud eelvaade"),
        ),
        migrations.AlterField(
            model_name="mcpwriteevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("PREPARED", "Muudatus ette valmistatud"),
                    ("PREPARE_REFUSED", "Ettevalmistamisest keelduti"),
                    ("APPROVED", "Kinnitatud"),
                    ("APPROVAL_REFUSED", "Kinnitamisest keelduti"),
                    ("DECLINED", "Tagasi lükatud"),
                    ("COMMITTED", "Salvestatud"),
                    ("COMMIT_REPLAYED", "Korduv salvestamispäring"),
                    ("COMMIT_REFUSED", "Salvestamisest keelduti"),
                    ("FAILED", "Salvestamine ebaõnnestus"),
                    ("EXPIRED", "Aegunud"),
                    ("RECEIPT_DELIVERED", "Tulemus edastatud kliendile"),
                    ("FORMAT_RETIRED", "Vananenud vorming, aegunud"),
                ],
                max_length=32,
                verbose_name="sündmus",
            ),
        ),
        migrations.CreateModel(
            name="McpWriteCounter",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("principal", models.CharField(max_length=100, verbose_name="tegutseja võti")),
                ("bucket", models.CharField(max_length=120, verbose_name="mõõdetav")),
                ("period", models.CharField(max_length=8, verbose_name="aken")),
                ("period_start", models.DateTimeField(verbose_name="akna algus")),
                ("count", models.BigIntegerField(default=0, verbose_name="arv")),
                ("first_at", models.DateTimeField(verbose_name="esimene")),
                ("last_at", models.DateTimeField(verbose_name="viimane")),
            ],
            options={
                "verbose_name": "MCP muudatuste loendur",
                "verbose_name_plural": "MCP muudatuste loendurid",
                "indexes": [models.Index(fields=["period_start"], name="mcp_write_counter_start")],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("principal", "bucket", "period", "period_start"),
                        name="mcp_write_counter_one_window",
                    )
                ],
            },
        ),
        migrations.RunSQL(sql=ROLLBACK_DEFAULTS, reverse_sql=migrations.RunSQL.noop),
        migrations.RunSQL(sql=INSERT_GUARD, reverse_sql=migrations.RunSQL.noop),
        migrations.RunSQL(sql=UPDATE_GUARD, reverse_sql=DROP_GUARDS),
        migrations.RunPython(retire_format_one, migrations.RunPython.noop),
    ]
