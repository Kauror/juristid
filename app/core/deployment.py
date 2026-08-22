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
from typing import Any

from django.apps import apps
from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

# --------------------------------------------------------------------------
# Migrations
# --------------------------------------------------------------------------

#: Operations whose forward direction removes or rewrites something, mapped to
#: what an operator needs to have decided before running them. An operator
#: safety gate, not a substitute for reading the migration: it says "somebody
#: must have thought about this", not "this is safe".
#:
#: Two different risks. The first four change the shape of data that already
#: exists, so the previous release's code may stop working against the new
#: schema — which matters because the deployment sequence deliberately leaves
#: the old web process serving while migrations run. The last two run arbitrary
#: code with the whole database in reach.
CONSEQUENTIAL_OPERATIONS: dict[str, str] = {
    "RemoveField": "drops a column; the release still serving may yet select it",
    "DeleteModel": "drops a table; nothing rolls that back but a restore",
    "RenameField": "the old and the new name never both exist",
    "RenameModel": "the old and the new table never both exist",
    "RunPython": "arbitrary code with the whole database in reach",
    "RunSQL": "arbitrary SQL with the whole database in reach",
}


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


def consequential_operations(migration: Any) -> dict[str, str]:
    """Which of a migration's operations are not purely additive, and why.

    Separate from the plan so it can be exercised against a hand-built migration
    rather than against whatever the repository happens to contain today — which
    is currently nothing but additive migrations, and would make this look
    correct while proving nothing.
    """
    return {
        type(operation).__name__: CONSEQUENTIAL_OPERATIONS[type(operation).__name__]
        for operation in migration.operations
        if type(operation).__name__ in CONSEQUENTIAL_OPERATIONS
    }


def migration_state() -> MigrationState:
    """Ask Django what it would do, without doing any of it."""
    executor = MigrationExecutor(connection)
    loader = executor.loader
    targets = loader.graph.leaf_nodes()

    pending: list[PlannedMigration] = []
    for migration, backwards in executor.migration_plan(targets):
        if backwards:  # pragma: no cover - a forward plan never contains these
            continue
        pending.append(
            PlannedMigration(
                app_label=migration.app_label,
                name=migration.name,
                consequential=consequential_operations(migration),
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
# Canonical and rebuildable data
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


def _local_app_labels() -> set[str]:
    return {config.label for config in apps.get_app_configs() if config.name.startswith("app.")}


def canonical_model_labels() -> tuple[str, ...]:
    """Every model whose rows a restore must bring back, in a stable order."""
    local = _local_app_labels()
    return tuple(
        sorted(
            label
            for label in (
                f"{model._meta.app_label}.{model.__name__}" for model in apps.get_models()
            )
            if label.split(".", 1)[0] in local and label not in REBUILDABLE_MODELS
        )
    )


def rebuildable_model_labels() -> tuple[str, ...]:
    """Projections a restore may leave empty and rebuild afterwards."""
    known = {f"{model._meta.app_label}.{model.__name__}" for model in apps.get_models()}
    return tuple(sorted(REBUILDABLE_MODELS & known))


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
