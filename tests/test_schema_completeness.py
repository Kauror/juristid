"""What the models promise, the migrated database must actually be holding.

Every other test in this suite proves a rule by breaking it: save the row that
should not exist, and watch something refuse it. That is the right way to prove
a rule *means* something, and it is why this file does not do it again.

What it proves instead is that the rules are **installed**. A guarantee stated
in a model and missing from the database does not fail loudly; it fails by
allowing things, on the one table nobody wrote a behavioural test for, in the
one deployment where a migration was squashed or hand-edited. The behavioural
tests cover the constraints somebody thought to cover. This covers the rest —
by discovering what is declared rather than by listing it.

Two inventories, because they are declared in two different places.

**Model constraints are discoverable.** ``Model._meta.constraints`` is the
project's own statement of every named ``CheckConstraint`` and
``UniqueConstraint`` it expects, so the expected set is read off the models and
compared against what PostgreSQL reports for the real table. There is no second
list here to drift, which is the whole point: a constraint added to a model
tomorrow is checked tomorrow, with no edit to this file.

**Triggers are not.** The safety guarantees this product cannot express as
constraints — append-only audit tables, immutable evidence, an Entra object id
that may never be rewritten, evidence that may not be un-restricted or moved out
from under the opinion relying on it — are ``CREATE TRIGGER`` statements inside
migrations. Django's metadata knows nothing about them, so the expected set is
written down, and it is written down *deliberately*: the registry below was
reconciled against both the migration sources and a freshly migrated database
rather than copied out of documentation.

That registry is asserted in both directions, and the second one is the reason
it exists. A missing trigger is a lost guarantee. An *unexpected* trigger is a
guarantee nobody has reviewed — a rule that fires on every write to a table, is
invisible in the models, and appears in no inventory. Adding one should cost a
line here and the paragraph in a pull request that explains it.
"""

from __future__ import annotations

import pytest
from django.apps import apps
from django.conf import settings
from django.db import connection

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# The project's own apps
# ---------------------------------------------------------------------------

#: Everything this repository ships lives under `app.`; Django's own apps and
#: `django.contrib.postgres` do not. Scoping on the import path rather than on a
#: hand-kept list of labels means a tenth app is included by existing.
PROJECT_PREFIX = "app."


def project_models() -> list[type]:
    """Every concrete, project-owned model with a table of its own."""
    return [
        model
        for model in apps.get_models()
        if model._meta.app_config.name.startswith(PROJECT_PREFIX)
        and model._meta.managed
        and not model._meta.proxy
    ]


def test_the_scope_of_these_guards_is_the_whole_project() -> None:
    """A prefix that stopped matching would silently check nothing.

    `project_models` is the population both inventories below are taken over, so
    it gets the treatment every population in this suite gets: asserted to be
    non-empty and to agree with `INSTALLED_APPS`, rather than trusted because it
    looked right once. A guard that has quietly narrowed to zero models passes
    every assertion it makes.
    """
    declared = {name for name in settings.INSTALLED_APPS if name.startswith(PROJECT_PREFIX)}
    discovered = {model._meta.app_config.name for model in project_models()}

    assert declared, "no project app matched the prefix these guards scope to"
    assert discovered <= declared
    assert len(project_models()) > 40, "the model scan found suspiciously little"


# ---------------------------------------------------------------------------
# D. Declared model constraints exist on the real table
# ---------------------------------------------------------------------------


def test_every_declared_model_constraint_exists_in_the_database() -> None:
    """Discovered from the models, checked against PostgreSQL's own catalogue.

    Structural, not semantic. That `documents_sha256_is_lowercase_hex` rejects an
    uppercase digest is proved where it matters, beside the code that writes
    digests; what is proved here is that a constraint by that name is on
    `documents_documentversion` in a database built by running the migrations.

    The kind is checked as well as the name, because there are two ways this
    fails: a constraint that is gone, and a constraint replaced by something
    weaker wearing its name — a `UniqueConstraint` that became a plain index
    enforces nothing and introspects under exactly the same key.

    A conditional `UniqueConstraint` is a partial unique index in PostgreSQL and
    carries no `pg_constraint` row at all, which is why this reads Django's
    introspection — it reports constraints and indexes together — rather than
    querying `pg_constraint` and quietly missing every conditional one.
    """
    checked: list[str] = []
    missing: list[str] = []
    wrong_kind: list[str] = []

    with connection.cursor() as cursor:
        tables: dict[str, dict] = {}
        for model in project_models():
            declared = getattr(model._meta, "constraints", None) or []
            if not declared:
                continue
            table = model._meta.db_table
            if table not in tables:
                tables[table] = connection.introspection.get_constraints(cursor, table)
            installed = tables[table]

            for constraint in declared:
                where = f"{table}.{constraint.name}"
                checked.append(where)
                found = installed.get(constraint.name)
                if found is None:
                    missing.append(where)
                    continue
                kind = type(constraint).__name__
                if kind == "CheckConstraint" and not found.get("check"):
                    wrong_kind.append(f"{where} is declared a check and the database has none")
                if kind == "UniqueConstraint" and not found.get("unique"):
                    wrong_kind.append(f"{where} is declared unique and the database's is not")

    assert checked, "no model constraint was discovered at all"
    assert not missing, (
        f"declared on a model and absent from the migrated database: {missing}. "
        "The guarantee is written down and is not being enforced."
    )
    assert not wrong_kind, wrong_kind


# ---------------------------------------------------------------------------
# E. Custom triggers — the guarantees no metadata knows about
# ---------------------------------------------------------------------------

#: Every trigger this project installs, and the table it protects.
#:
#: Reconciled against the `CREATE TRIGGER` sites in the migrations — two of them
#: loop over a tuple of tables — and against a freshly migrated database, rather
#: than copied from documentation. PostgreSQL's own foreign-key and constraint
#: triggers are excluded by `tgisinternal` in the query below, so everything
#: named here is ours.
#:
#: A new entry belongs in the same commit as the migration that creates it. If
#: this list is what is failing, the question to answer in the pull request is
#: not "which name do I add" but "what does this fire on every write to that
#: table, and who has agreed to it".
EXPECTED_TRIGGERS: dict[str, str] = {
    # Identity may not be rewritten. An Entra object id is how a person is
    # recognised across sessions, so changing one silently reassigns their work.
    "accounts_user_entra_object_id_immutable": "accounts_user",
    # Append-only. A record of what happened is worth nothing if the code that
    # did it can also edit the record.
    "audit_changeevent_append_only": "audit_changeevent",
    "audit_securityauditevent_append_only": "audit_securityauditevent",
    "matters_entryrevision_append_only": "matters_entryrevision",
    "legacy_import_importrowledger_append_only": "legacy_import_importrowledger",
    # Immutable evidence. A stored version is a fixed artefact, and the raw
    # provenance of an imported row is what proves nothing was invented.
    "documents_documentversion_immutable": "documents_documentversion",
    "legacy_import_mattersourcereference_raw_immutable": "legacy_import_mattersourcereference",
    # Evidence a sent opinion relies on may not be widened, moved to another
    # Matter, or left behind by its Matter's own visibility changing.
    "submissions_final_evidence_integrity": "submissions_submission",
    "documents_relied_upon_evidence_stays_restricted": "documents_document",
    "documents_relied_upon_evidence_stays_in_matter": "documents_document",
    "matters_relied_upon_evidence_stays_restricted": "matters_matter",
}

#: `O` fires on an ordinary connection and `A` fires always. `D` is disabled,
#: and `R` fires only on a replica — which, on a table whose integrity rule this
#: is, means the rule is off everywhere the application actually writes.
FIRING = ("O", "A")

INSTALLED_TRIGGERS = """
    SELECT t.tgname, c.relname, t.tgenabled
    FROM pg_trigger t
    JOIN pg_class c ON c.oid = t.tgrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE NOT t.tgisinternal
      AND n.nspname = current_schema()
    ORDER BY c.relname, t.tgname
"""


@pytest.fixture
def installed_triggers() -> dict[str, tuple[str, str]]:
    """Our triggers as the migrated database holds them: name to (table, enabled).

    `tgisinternal` is what separates the two populations sharing this catalogue.
    PostgreSQL implements every foreign key and every deferrable constraint as a
    pair of internal triggers — several hundred of them here — and a query that
    forgot the flag would be comparing our eleven against those.
    """
    with connection.cursor() as cursor:
        cursor.execute(INSTALLED_TRIGGERS)
        return {name: (table, enabled) for name, table, enabled in cursor.fetchall()}


def test_every_expected_trigger_is_installed_on_its_table(installed_triggers) -> None:
    """A guarantee that exists only in a migration file is not a guarantee.

    Named table and all: a trigger that survived a migration but landed on the
    wrong relation enforces the wrong rule, and the name alone would not say so.
    """
    missing = sorted(name for name in EXPECTED_TRIGGERS if name not in installed_triggers)
    assert not missing, (
        f"expected custom triggers absent from the migrated database: {missing}. "
        "Every one of these is a safety rule the application relies on the "
        "database to keep."
    )

    misplaced = [
        f"{name}: expected on {table}, found on {installed_triggers[name][0]}"
        for name, table in EXPECTED_TRIGGERS.items()
        if installed_triggers[name][0] != table
    ]
    assert not misplaced, misplaced


def test_no_custom_trigger_is_installed_without_being_declared(installed_triggers) -> None:
    """The direction that matters more, and the one an inventory usually forgets.

    A trigger missing from the database is a guarantee that stopped working, and
    something else usually notices. A trigger in the database and missing from
    here is the opposite problem: a rule that runs on every write, is invisible
    in the models, appears in no inventory, and that nobody has agreed to. This
    is the only thing in the repository that would ever mention it.

    So a new trigger fails CI until it is acknowledged. That is the intent
    rather than an inconvenience — the cost of adding one should be a line in a
    reviewed list, which is exactly what it costs.
    """
    unexpected = sorted(
        f"{name} on {table}"
        for name, (table, _) in installed_triggers.items()
        if name not in EXPECTED_TRIGGERS
    )
    assert not unexpected, (
        f"custom trigger installed and not declared in EXPECTED_TRIGGERS: {unexpected}. "
        "Add it there with a comment saying what it guarantees."
    )


def test_every_expected_trigger_is_actually_firing(installed_triggers) -> None:
    """Present is not the same as switched on.

    `ALTER TABLE ... DISABLE TRIGGER` leaves the trigger in the catalogue,
    exactly where an inventory that checked names would find it, and the rule
    stops running. So does `ENABLE REPLICA`, which is worse for being plausible:
    it reads like the trigger is on, and it is off on every ordinary connection
    the application uses.
    """
    idle = sorted(
        f"{name} (tgenabled={installed_triggers[name][1]})"
        for name in EXPECTED_TRIGGERS
        if name in installed_triggers and installed_triggers[name][1] not in FIRING
    )
    assert not idle, f"installed but not firing on ordinary writes: {idle}"
