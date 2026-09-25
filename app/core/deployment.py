"""What a deployment can be asked about itself, without changing anything.

Three questions an operator has to answer around every release, and one they
have to answer after a restore:

* **What is this database about to be asked to do?** — the migration plan, read
  before it is applied rather than after (`manage.py migration_plan`).
* **Does the code now running match the database and the storage under it?** —
  the readiness check that fails closed when it does not
  (`manage.py deployment_readiness`).
* **Is the canonical state still the canonical state?** — the fingerprint a
  restore is measured against (`manage.py recovery_fingerprint`).

Everything here is read-only. Nothing in this module migrates, writes evidence
or applies business data: a deployment carries code and schema, and every
consequential write to the register stays a separate reviewed command
(docs/adr/0022).

Django's own migration APIs answer the migration questions. Grepping migration
files for `RemoveField` would be a guess about something the loader already
knows exactly, squashes and replacements included.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.apps import apps
from django.conf import settings
from django.db import connection, migrations
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.state import ProjectState
from django.db.models import NOT_PROVIDED, Field

# --------------------------------------------------------------------------
# Migrations
# --------------------------------------------------------------------------

#: Operations whose forward direction removes or rewrites something, mapped to
#: what an operator needs to have decided before running them. An operator
#: safety gate, not a substitute for reading the migration: it says "somebody
#: must have thought about this", not "this is safe".
#:
#: "Additive" means the release still serving keeps working against the new
#: schema — it can **read and write** it. That is the property that matters,
#: twice: the deployment sequence leaves the old web process serving while
#: migrations run, and the documented rollback after an additive release is a
#: code-only one (deploy/unraid-main/RECOVERY.md). Reading is not enough: an
#: INSERT from code that has never heard of a NOT NULL column fails just as
#: surely as a SELECT of a dropped one (ENG-014).
#:
#: Three kinds of risk. The first four change the shape of data that already
#: exists; the two constraint operations change what the database accepts or
#: stops guaranteeing; the last two run arbitrary code with the whole database
#: in reach.
CONSEQUENTIAL_OPERATIONS: dict[str, str] = {
    "RemoveField": "drops a column; the release still serving may yet select it",
    "DeleteModel": "drops a table; nothing rolls that back but a restore",
    "RenameField": "the old and the new name never both exist",
    "RenameModel": "the old and the new table never both exist",
    "AddConstraint": (
        "the database starts refusing rows it accepted; the release still serving "
        "may write one, and existing rows must already satisfy it"
    ),
    "RemoveConstraint": (
        "the database stops guaranteeing something; rows written from now on may "
        "break what the release still serving, or a rollback to it, assumes"
    ),
    "RunPython": "arbitrary code with the whole database in reach",
    "RunSQL": "arbitrary SQL with the whole database in reach",
}

#: Operations the release still serving survives by construction: a new table
#: it never touches, an index, an extension, or state Django keeps for itself.
#: Anything neither here, in `CONSEQUENTIAL_OPERATIONS` nor judged field by
#: field below is flagged as unclassified — an operation this gate has never
#: seen is not evidence of safety.
ADDITIVE_OPERATIONS: frozenset[str] = frozenset(
    {
        "CreateModel",
        "AddIndex",
        "RemoveIndex",
        "RenameIndex",
        "AddIndexConcurrently",
        "RemoveIndexConcurrently",
        # `app.core.index_operations`: the two above, built plainly only inside
        # a transaction, which the release's migrate step never is.
        "AddIndexConcurrentlyWhenPossible",
        "RemoveIndexConcurrentlyWhenPossible",
        "AlterModelOptions",
        "AlterModelManagers",
        "CreateExtension",
        "TrigramExtension",
        "UnaccentExtension",
        "BtreeGinExtension",
        "BtreeGistExtension",
        "CITextExtension",
        "CryptoExtension",
        "HStoreExtension",
    }
)

UNCLASSIFIED = "not an operation this gate knows; read it before deciding it is safe"


@dataclass(frozen=True)
class PlannedMigration:
    """One migration this process would apply, and why it may need a decision."""

    app_label: str
    name: str
    #: ``operation class name -> why it is consequential``. Empty when the
    #: migration is purely additive.
    consequential: dict[str, str]

    @property
    def label(self) -> str:
        return f"{self.app_label}.{self.name}"

    @property
    def is_additive(self) -> bool:
        return not self.consequential


@dataclass(frozen=True)
class MigrationState:
    """The relationship between the code in this process and its database."""

    #: On disk and not applied. Non-empty means new code, old schema.
    pending: tuple[PlannedMigration, ...]
    #: Applied and not on disk. Non-empty means old code, new schema — the
    #: rollback case, and the one that is invisible unless asked for.
    unknown: tuple[str, ...]
    #: The leaf of every migrated app. A backup manifest records these so a
    #: restore can be matched to the code that wrote it.
    leaves: tuple[str, ...]

    @property
    def is_consistent(self) -> bool:
        return not self.pending and not self.unknown

    @property
    def consequential(self) -> tuple[PlannedMigration, ...]:
        return tuple(migration for migration in self.pending if not migration.is_additive)


def consequential_operations(
    migration: Any, *, state: ProjectState | None = None
) -> dict[str, str]:
    """Which of a migration's operations are not purely additive, and why.

    Separate from the plan so it can be exercised against a hand-built migration
    rather than against whatever the repository happens to contain today.

    Two operations cannot be judged by their class name, because whether the
    release still serving survives them depends on the field (ENG-014):

    * **AddField** is additive when old code can still INSERT without knowing the
      column: it is nullable, the *database* supplies a default (`db_default` —
      Django's `default` is applied by Django and dropped from the column
      afterwards, so the old release's INSERT gets no value), it is generated by
      the database, it is a many-to-many join table, or the table is new in this
      same migration.
    * **AlterField** is additive only when the column does not change — a
      `choices` or `verbose_name` edit, which is most of them here. Telling the
      two apart needs the field as it was, so ``state`` is the project state
      *before* this migration. Without one it cannot be told, and is flagged.
    """
    found: dict[str, list[str]] = {}
    created: set[str] = set()
    state = state.clone() if state is not None else None
    for operation in migration.operations:
        name = type(operation).__name__
        why = _why_consequential(operation, migration.app_label, created, state)
        if why:
            found.setdefault(name, [])
            if why not in found[name]:
                found[name].append(why)
        if isinstance(operation, migrations.CreateModel):
            created.add(operation.name_lower)
        if state is not None:
            operation.state_forwards(migration.app_label, state)
    return {name: "; ".join(reasons) for name, reasons in found.items()}


def _why_consequential(
    operation: Any, app_label: str, created: set[str], state: ProjectState | None
) -> str:
    name = type(operation).__name__
    if isinstance(operation, migrations.AddField):
        return _why_add_field(operation, created)
    if isinstance(operation, migrations.AlterField):
        return _why_alter_field(operation, app_label, created, state)
    if name in CONSEQUENTIAL_OPERATIONS:
        return CONSEQUENTIAL_OPERATIONS[name]
    if name in ADDITIVE_OPERATIONS:
        return ""
    return UNCLASSIFIED


def _why_add_field(operation: migrations.AddField, created: set[str]) -> str:
    field = operation.field
    if (
        operation.model_name_lower in created
        or field.many_to_many
        or field.null
        or field.db_default is not NOT_PROVIDED
        or getattr(field, "generated", False)
    ):
        return ""
    return (
        f"adds NOT NULL {operation.model_name_lower}.{operation.name} with no database "
        "default; the release still serving cannot insert a row without it, while it "
        "serves or after a code-only rollback"
    )


def _why_alter_field(
    operation: migrations.AlterField,
    app_label: str,
    created: set[str],
    state: ProjectState | None,
) -> str:
    column = f"{operation.model_name_lower}.{operation.name}"
    if operation.model_name_lower in created:
        return ""
    before = None
    if state is not None:
        model = state.models.get((app_label, operation.model_name_lower))
        before = model.fields.get(operation.name) if model is not None else None
    if before is None:
        return (
            f"alters {column}, and whether the column changes cannot be told without "
            "the state before it"
        )
    if not _changes_the_column(before, operation.field):
        return ""
    return (
        f"alters the {column} column; the release still serving may write what the "
        "new definition refuses"
    )


def _changes_the_column(before: Field, after: Field) -> bool:
    """Whether the database would see a difference between two definitions.

    Django's own rule, from the public half of it: compare the deconstructed
    fields without the attributes Django says never reach the schema
    (`Field.non_db_attrs` — choices, help text, verbose name and the like).
    `db_column` is one of those only in the sense that it does not change the
    column's type; a different name is a different column, so it is compared.
    """
    _, before_path, before_args, before_kwargs = before.deconstruct()
    _, after_path, after_args, after_kwargs = after.deconstruct()
    if before_kwargs.get("db_column") != after_kwargs.get("db_column"):
        return True
    for attr in before.non_db_attrs:
        before_kwargs.pop(attr, None)
    for attr in after.non_db_attrs:
        after_kwargs.pop(attr, None)
    return (before_path, before_args, before_kwargs) != (after_path, after_args, after_kwargs)


def migration_state() -> MigrationState:
    """Ask Django what it would do, without doing any of it."""
    executor = MigrationExecutor(connection)
    loader = executor.loader
    targets = loader.graph.leaf_nodes()

    pending: list[PlannedMigration] = []
    for migration, backwards in executor.migration_plan(targets):
        if backwards:  # pragma: no cover - a forward plan never contains these
            continue
        before = loader.project_state((migration.app_label, migration.name), at_end=False)
        pending.append(
            PlannedMigration(
                app_label=migration.app_label,
                name=migration.name,
                consequential=consequential_operations(migration, state=before),
            )
        )

    # Applied rows for apps this code still migrates, minus what is on disk.
    # Restricted to `migrated_apps` on purpose: an app removed from
    # INSTALLED_APPS leaves its applied rows behind for ever, and reporting
    # those as "the database is ahead of the code" would cry wolf permanently.
    on_disk = set(loader.disk_migrations)
    replaced = {
        target for replacement in loader.replacements.values() for target in replacement.replaces
    }
    unknown = tuple(
        sorted(
            f"{app_label}.{name}"
            for app_label, name in loader.applied_migrations
            if app_label in loader.migrated_apps
            and (app_label, name) not in on_disk
            and (app_label, name) not in replaced
        )
    )

    leaves = tuple(sorted(f"{app_label}.{name}" for app_label, name in targets))
    return MigrationState(pending=tuple(pending), unknown=unknown, leaves=leaves)


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

#: What a storage class means for recovery. Canonical material must be backed
#: up; rebuildable material must never be mistaken for it; source material is
#: authoritative input and must not be writable.
CANONICAL = "canonical — must be backed up"
REBUILDABLE = "rebuildable — needs no canonical backup"
SOURCE = "source — authoritative input, read-only"


@dataclass(frozen=True)
class StorageRoot:
    """One storage class, and whether the deployment mounted it as intended."""

    name: str
    path: Path
    kind: str
    exists: bool
    writable: bool
    #: The mount contract this class is supposed to satisfy.
    must_be_writable: bool

    @property
    def problem(self) -> str:
        if not self.exists:
            return f"{self.name} ({self.path}) does not exist"
        if self.must_be_writable and not self.writable:
            return f"{self.name} ({self.path}) is not writable by this process"
        if not self.must_be_writable and self.writable:
            return (
                f"{self.name} ({self.path}) is writable; the historical corpus is "
                "authoritative input and belongs on a read-only mount"
            )
        return ""


def _is_writable(path: Path) -> bool:
    """Probe the mount rather than believe the code that reads it.

    `:ro` is enforced by the kernel, so this answers a question the Compose
    test cannot: whether the mount the container actually got is the mount the
    file describes. A probe file rather than `os.access`, because `os.access`
    reports permission bits and knows nothing about mount options.
    """
    probe = path / ".juristid-write-probe"
    try:
        with probe.open("wb"):
            pass
    except OSError:
        return False
    try:
        probe.unlink()
    except OSError:  # pragma: no cover - written but not removable
        pass
    return True


def storage_roots() -> tuple[StorageRoot, ...]:
    """Every storage class this deployment depends on, and its mount contract."""
    declared: list[tuple[str, Path | str, str, bool]] = [
        ("EVIDENCE_ROOT", settings.EVIDENCE_ROOT, CANONICAL, True),
        ("LEGACY_SOURCE_ROOT", settings.LEGACY_SOURCE_ROOT, CANONICAL, True),
        ("DERIVATIVE_ROOT", settings.DERIVATIVE_ROOT, REBUILDABLE, True),
    ]
    # Only where the deployment says it has one. A laptop has no corpus, and a
    # check that invents one would fail everywhere it does not matter.
    if settings.HISTORICAL_SOURCE_ROOT:
        declared.append(("HISTORICAL_SOURCE_ROOT", settings.HISTORICAL_SOURCE_ROOT, SOURCE, False))

    roots: list[StorageRoot] = []
    for name, raw, kind, must_be_writable in declared:
        path = Path(raw)
        exists = path.is_dir()
        roots.append(
            StorageRoot(
                name=name,
                path=path,
                kind=kind,
                exists=exists,
                writable=_is_writable(path) if exists else False,
                must_be_writable=must_be_writable,
            )
        )
    return tuple(roots)


# --------------------------------------------------------------------------
# Canonical, rebuildable and operational data
# --------------------------------------------------------------------------

#: Tables a restore does not have to bring back, because they are a projection
#: of something that does. Named here rather than inferred, so the
#: storage-class distinction lives in one place and a new model is canonical
#: until somebody argues otherwise.
REBUILDABLE_MODELS = frozenset(
    {
        "search.SearchDocument",
        "documents.DocumentDerivative",
        # The archive's own search projection, rebuilt by
        # `opinion_archive_search rebuild` from rows a restore does bring back.
        "legacy_import.OpinionArchiveSearchDocument",
        # Extracted archive text. Rebuildable in the strict sense: it is a pure
        # function of the stored bytes and the parser version, and re-running
        # the extraction in an environment that forbids it writes back the same
        # BLOCKED rows rather than a different answer.
        "legacy_import.OpinionArchiveText",
    }
)


#: Tables that hold neither business state nor a projection of it, but the
#: system's own record of work it still owes itself. A restore brings them back
#: like everything else — they are ordinary rows in the same dump — and their
#: *contents* are nobody's canonical state, so a fingerprint that compared them
#: would report drift because a queue happened to be non-empty at one of the two
#: moments.
#:
#: `SearchRebuildDebt` is the whole membership and the reason this category
#: exists. A pending row there means "a vocabulary edit landed and the rebuild
#: has not run yet", which is a perfectly healthy state a few seconds long, and
#: `recovery_fingerprint --compare` reported it as
#: `canonical_counts.search.SearchRebuildDebt: 0 -> 1` — canonical-state
#: divergence, in the one command whose whole job is to tell an operator whether
#: a restore brought the register back. A probe that cries wolf during a normal
#: restore is worse than no probe (docs/adr/0041).
#:
#: It is deliberately not `REBUILDABLE_MODELS`: nothing rebuilds a debt row.
#: Rebuilding is what *clears* one.
OPERATIONAL_MODELS = frozenset(
    {
        "search.SearchRebuildDebt",
    }
)


def _local_app_labels() -> set[str]:
    return {config.label for config in apps.get_app_configs() if config.name.startswith("app.")}


def canonical_model_labels() -> tuple[str, ...]:
    """Every model whose rows a restore must bring back, in a stable order.

    Everything local that is not a projection and not the system's own work
    queue. Both exclusions are explicit allow-lists rather than a rule about
    names, so a new model is canonical until somebody argues otherwise and
    writes it down.
    """
    local = _local_app_labels()
    excluded = REBUILDABLE_MODELS | OPERATIONAL_MODELS
    return tuple(
        sorted(
            label
            for label in (
                f"{model._meta.app_label}.{model.__name__}" for model in apps.get_models()
            )
            if label.split(".", 1)[0] in local and label not in excluded
        )
    )


def rebuildable_model_labels() -> tuple[str, ...]:
    """Projections a restore may leave empty and rebuild afterwards."""
    known = {f"{model._meta.app_label}.{model.__name__}" for model in apps.get_models()}
    return tuple(sorted(REBUILDABLE_MODELS & known))


def operational_model_labels() -> tuple[str, ...]:
    """Work the system owes itself. Restored, reported, never compared."""
    known = {f"{model._meta.app_label}.{model.__name__}" for model in apps.get_models()}
    return tuple(sorted(OPERATIONAL_MODELS & known))


def model_counts(labels: tuple[str, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for label in labels:
        app_label, model_name = label.split(".", 1)
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:  # pragma: no cover - a stale entry in the frozen set
            continue
        counts[label] = model._default_manager.count()
    return counts


# --------------------------------------------------------------------------
# The running build
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RuntimeIdentity:
    """The three facts an operator confuses at their peril.

    A build time is not a version and an image tag is not a commit. Only the
    source revision answers "what code is this", and it used to be the one
    field somebody had to remember to set (docs/adr/0022).
    """

    revision: str
    revision_is_known: bool
    built_at: str
    environment: str
    stage: str


def runtime_identity() -> RuntimeIdentity:
    revision = (settings.APPLICATION_REVISION or "").strip()
    return RuntimeIdentity(
        revision=revision or "unknown",
        revision_is_known=bool(revision) and revision.lower() != "unknown",
        built_at=(settings.APPLICATION_BUILT_AT or "").strip(),
        environment=settings.APPLICATION_ENVIRONMENT,
        stage=settings.APPLICATION_STAGE,
    )


def postgresql_version() -> tuple[int, int]:
    """The server's major and minor, for a restore that has to match it.

    Asked of the server rather than read off the client: a dump is restored by
    a *server*, and the major it was taken from is the constraint (docs/adr/0022).
    """
    with connection.cursor() as cursor:
        cursor.execute("SHOW server_version_num")
        row = cursor.fetchone()
    raw = int(row[0]) if row else 0
    return raw // 10000, raw % 10000


# --------------------------------------------------------------------------
# Environment hygiene
# --------------------------------------------------------------------------

FALSE_VALUES = frozenset({"0", "false", "no", "off"})

#: Boolean environment variables whose value decides something a deployment
#: cannot afford to get wrong by typing. `config/env.py` treats anything it does
#: not recognise as false — the safe direction, and a silent one.
BOOLEAN_ENVIRONMENT_VARIABLES = (
    "DJANGO_DEBUG",
    "DEV_LOGIN_ENABLED",
    "REAL_DATA_ALLOWED",
    "DJANGO_SECURE_SSL_REDIRECT",
    "DJANGO_BEHIND_TLS_PROXY",
    "DJANGO_STATIC_MANIFEST",
    "EXTRACTION_OCR_ENABLED",
    "SEED_DEV_DATA",
)


def unparseable_boolean_variables(environ: dict[str, str] | None = None) -> dict[str, str]:
    """Boolean variables set to something that is neither true nor false.

    Returns ``name -> value`` so a check can name them. Every variable here
    holds a flag and never a secret, so the value is safe to print.
    """
    from config.env import TRUE_VALUES

    source: Any = os.environ if environ is None else environ
    unparseable: dict[str, str] = {}
    for name in BOOLEAN_ENVIRONMENT_VARIABLES:
        raw = source.get(name)
        if raw is None:
            continue
        value = str(raw).strip().lower()
        if value == "" or value in TRUE_VALUES or value in FALSE_VALUES:
            continue
        unparseable[name] = raw
    return unparseable


# --------------------------------------------------------------------------
# Readiness, as a value
# --------------------------------------------------------------------------


if TYPE_CHECKING:
    from app.search.freshness import SearchIndexState


#: The two moments `deployment_readiness` can be asked about a search index.
#:
#: A release that moves `INDEX_VERSION` is *supposed* to start serving on rows
#: of the old version: the query chokepoint ignores them and search returns too
#: little until step 11 of the runbook rebuilds the index. So a readiness check
#: run before that rebuild must be able to say "stale, as expected" without
#: failing — or every such release would be undeployable — and the check run
#: after it must fail on exactly the same state, because then it means the
#: rebuild was skipped and every search answers nothing (ENG-142).
SEARCH_PHASE_FINAL = "final"
SEARCH_PHASE_PRE_REBUILD = "pre-rebuild"
SEARCH_PHASES = (SEARCH_PHASE_FINAL, SEARCH_PHASE_PRE_REBUILD)


@dataclass(frozen=True)
class ReadinessReport:
    """Everything ``deployment_readiness`` asks, separated from how it prints.

    Extracted so that a second caller can have the verdict without the prose:
    ``production_status`` rolls this up beside the other current-state checks,
    and parsing a command's stdout to get there would make the roll-up depend on
    wording nobody thinks of as an interface (`app/core/production_status.py`).

    The command keeps its own output and its own exit behaviour. This holds the
    facts it prints and the problems it fails on, and nothing about either is
    decided here.
    """

    identity: RuntimeIdentity
    postgresql: tuple[int, int]
    migrations: MigrationState
    storage: tuple[StorageRoot, ...]
    reference: Any
    problems: tuple[str, ...]
    warnings: tuple[str, ...]
    search_index: SearchIndexState | None = None
    search_phase: str = SEARCH_PHASE_FINAL

    @property
    def ok(self) -> bool:
        """Warnings are not failures. Only `problems` decide readiness."""
        return not self.problems


def readiness_report(*, search_phase: str = SEARCH_PHASE_FINAL) -> ReadinessReport:
    """Ask this build about itself. Reads; never migrates and never writes.

    ``search_phase`` says which moment of a deployment this is (see
    `SEARCH_PHASES`). The default is the final one, so a search index left on an
    older version is a problem — which is what makes a skipped rebuild visible
    — and only a caller that names ``pre-rebuild`` gets it as a warning.

    Raises :class:`django.db.DatabaseError` when the database cannot be reached
    at all, which is the one condition that cannot be reported as a finding
    because every other finding needs the database to be established. Both
    callers turn it into their own message.
    """
    from app.core import reference_data

    problems: list[str] = []

    identity = runtime_identity()
    if settings.REAL_DATA_ALLOWED and not identity.revision_is_known:
        problems.append(
            "The running build does not know which commit it came from. "
            "Build with --build-arg GIT_SHA=<sha>, or set APPLICATION_REVISION."
        )

    major, minor = postgresql_version()
    if (major, 0) < settings.MINIMUM_POSTGRESQL_VERSION:
        required = ".".join(str(part) for part in settings.MINIMUM_POSTGRESQL_VERSION)
        problems.append(f"PostgreSQL {major} is older than the required {required}.")

    state = migration_state()
    if state.pending:
        problems.append(
            f"{len(state.pending)} migration(s) are not applied: "
            f"{', '.join(migration.label for migration in state.pending[:5])}"
            f"{' …' if len(state.pending) > 5 else ''}. "
            "This build is running against an older schema."
        )
    if state.unknown:
        problems.append(
            f"The database has applied {len(state.unknown)} migration(s) this build "
            f"does not have: {', '.join(state.unknown[:5])}"
            f"{' …' if len(state.unknown) > 5 else ''}. "
            "This build is older than its database."
        )

    roots = storage_roots()
    problems.extend(root.problem for root in roots if root.problem)

    baseline = reference_data.verify_reference_data()
    problems.extend(reference_data.readiness_problems(baseline))

    warnings = [
        f"{name}={value!r} is neither true nor false and is being read as false."
        for name, value in sorted(unparseable_boolean_variables().items())
    ]

    if search_phase not in SEARCH_PHASES:
        raise ValueError(f"unknown search phase {search_phase!r}")
    search = None
    if not state.pending:
        # Only once the schema is this build's: an unapplied migration is
        # already the problem, and the search tables may not match the model.
        from app.search.freshness import search_index_state

        search = search_index_state()
        if search.problem:
            if search_phase == SEARCH_PHASE_PRE_REBUILD:
                warnings.append(
                    f"{search.problem} Expected before the rebuild of a release that "
                    "changes INDEX_VERSION; the final check (no --search-phase) fails on it."
                )
            else:
                problems.append(search.problem)

    return ReadinessReport(
        identity=identity,
        postgresql=(major, minor),
        migrations=state,
        storage=roots,
        reference=baseline,
        problems=tuple(problems),
        warnings=tuple(warnings),
        search_index=search,
        search_phase=search_phase,
    )
