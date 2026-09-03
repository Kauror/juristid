"""The migrated reference-data baseline outlives every transactional test.

A ``django_db(transaction=True)`` test cannot be wrapped in a transaction —
that is the point of it — so Django cleans up by flushing every table
afterwards. The flush takes the rows *data migrations* wrote with it, and
nothing re-runs a historical migration, so the canonical vocabularies are gone
for whatever runs next. Measured on this repository, before the contract below
existed, in two ordinary pytest invocations with no xdist and no plugin::

    pytest --create-db --reuse-db \
        tests/test_documents.py::test_concurrent_writers_get_distinct_version_numbers
    pytest --reuse-db \
        tests/test_stage_vocabulary_seed.py::test_ten_canonical_stages_are_seeded

    E   AssertionError: assert {...the ten canonical stage keys...} <= set()

``taxonomy.PolicyArea`` (28 rows), ``workflow.StageVocabulary`` (10) and
``workflow.LegacyStatusMapping`` (11) were all empty in the second invocation.
``contenttypes.ContentType`` and ``auth.Permission`` survived, because ``flush``
emits ``post_migrate`` and their receivers rebuild them; nothing emits a data
migration.

``docs/ci-architecture.md`` reported the same defect from the other end: under
``pytest -n --dist loadfile``, between 28 and 82 tests failed with
``StageVocabulary.DoesNotExist`` or ``PolicyArea.DoesNotExist``.

The contract now is:

    a test whose database teardown is a flush leaves the migrated baseline
    restored for whatever runs next

and it is held up by three things — the ``serialized_rollback=True`` marker on
every transactional test, the restore in ``tests/conftest.py`` (after each
transactional teardown, and once more while the database is still there at the
end of a session), and this file, which exists to make sure the first two
cannot quietly stop applying.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from collections import Counter
from collections.abc import Generator
from functools import cache
from pathlib import Path

import pytest
from django.apps import apps
from django.db import connection

from app.taxonomy.models import PolicyArea
from app.workflow.models import LegacyStatusMapping, StageVocabulary
from tests import reference_baseline

ROOT = Path(__file__).resolve().parent.parent

#: The directory the teardown hook covers. It lives in ``tests/conftest.py``,
#: so a transactional test anywhere else would be outside the contract.
GUARDED_TREE = "tests"

#: The directories a Django test can live in. ``e2e`` is scanned too, and a
#: transactional test found there is reported whatever it declares: nothing
#: there touches the database today, and the hook does not reach it.
TEST_TREES = (GUARDED_TREE, "e2e")

#: Transactional tests that deliberately do not declare ``serialized_rollback``,
#: by node id, each carrying the reason it is safe.
#:
#: ``test_the_migrated_baseline_is_back_after_that_flush`` is the mutation proof
#: for the whole contract. ``serialized_rollback=True`` restores the baseline in
#: Django's ``_fixture_setup``, *before* the test body runs — so a test that
#: declared it would find the vocabularies present whether or not the teardown
#: hook still exists, and would go green against a broken suite. This one has to
#: arrive at a database nobody restored for it and report what the previous
#: test's teardown actually left behind. Safe because it only reads, and because
#: the restore truncates before it deserializes: this is the one transactional
#: test whose own flush still emits ``post_migrate``, and the truncate is what
#: keeps those freshly-keyed content types from colliding with the snapshot's.
DOCUMENTED_EXCEPTIONS = frozenset(
    {
        "tests/test_reference_data_isolation.py"
        "::test_the_migrated_baseline_is_back_after_that_flush",
    }
)


# ---------------------------------------------------------------------------
# 1. The mechanism does what it says
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_the_migrated_state_was_snapshotted_at_all() -> None:
    """Everything else here is vacuous without this.

    Django serializes the test database inside ``setup_databases()``, right
    after ``create_test_db()`` and before any test has run — but only for the
    connections pytest-django asks it to, and pytest-django asks only when some
    collected test declares ``serialized_rollback``. No declaration, no
    snapshot, and the restore quietly has nothing to restore.
    """
    assert reference_baseline.connections_with_a_snapshot(), (
        "no connection carries a migrated-state snapshot, which means no "
        "collected test declared serialized_rollback=True"
    )


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_the_contract_does_not_put_the_test_back_inside_a_transaction() -> None:
    """`serialized_rollback` restores rows; it must not restore a transaction.

    Every transactional test in this suite exists for behaviour a *second*
    connection can observe — a lock, a commit, a race — and a wrapping
    transaction would hide all of it. A green suite bought by moving the work
    back inside one would be worth nothing, so this pins the thing that would
    have changed.
    """
    assert not connection.in_atomic_block
    assert connection.get_autocommit()


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_the_restore_reproduces_every_row_the_migrations_seeded() -> None:
    """Generic, so a vocabulary seeded later is covered without editing this.

    The expected state is read out of Django's own snapshot rather than listed
    here. A second hand-maintained copy of the reviewed vocabularies is the
    thing this whole mechanism exists to avoid.
    """
    snapshot = reference_baseline.snapshot_for(connection)
    assert snapshot is not None
    expected = Counter(row["model"] for row in json.loads(snapshot))
    assert expected, "the migrated database serialized to nothing at all"

    reference_baseline.restore_migrated_baseline()

    assert _live_census() == expected


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_the_restore_survives_being_run_twice() -> None:
    """It runs after every transactional test, so it had better be idempotent.

    ``deserialize_db_from_string`` saves each row under its original primary
    key, which is what makes a second pass an update rather than a duplicate.
    """
    reference_baseline.restore_migrated_baseline()
    once = _live_census()
    reference_baseline.restore_migrated_baseline()

    assert _live_census() == once


def _live_census() -> Counter[str]:
    """Row counts per model, labelled the way the serializer labels them.

    Counted through ``_base_manager`` because that is the manager
    ``serialize_db_to_string`` reads: a model whose default manager filters
    would otherwise be compared against a snapshot that did not.
    """
    census: Counter[str] = Counter()
    for model in apps.get_models():
        count = model._base_manager.count()
        if count:
            census[model._meta.label_lower] = count
    return census


# ---------------------------------------------------------------------------
# 2. The regression itself, in two tests that must stay adjacent
# ---------------------------------------------------------------------------
#
# pytest-django sorts transactional tests after every other database test, and
# the sort is stable, so these two keep both their order and their adjacency in
# any run that collects the file — including a CI shard, which takes whole
# files. The first flushes; the second is the only test in the suite that gets
# to see what a flush left behind.


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_a_transactional_test_flushes_everything_it_can_see() -> None:
    """The half that does the damage, and proves it starts from a full house."""
    assert StageVocabulary.objects.filter(key="consultation").exists()
    assert PolicyArea.objects.filter(key="keskkond").exists()
    assert LegacyStatusMapping.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_the_migrated_baseline_is_back_after_that_flush() -> None:
    """The mutation proof. Deliberately no ``serialized_rollback`` — see above.

    Nothing between this test and the one above restored anything except the
    teardown hook in ``tests/conftest.py``. Remove that hook, or the marker on
    the test above, and this fails the way the defect always failed:
    ``StageVocabulary.DoesNotExist`` and ``PolicyArea.DoesNotExist``.
    """
    assert StageVocabulary.objects.get(key="consultation")
    assert PolicyArea.objects.get(key="keskkond")
    assert LegacyStatusMapping.objects.exists()


# ---------------------------------------------------------------------------
# 3. The hand-over between two processes
# ---------------------------------------------------------------------------
#
# Everything above runs inside one pytest session, and one session is exactly
# where the defect is hardest to see: pytest-django sorts transactional tests
# after every other database test, so in-process the flush usually lands after
# the last test that needed the rows. The failure people actually hit is the
# next *process* — `--reuse-db` picking up a database the previous run left
# flushed, including a run `-x` abandoned inside a transactional test. That
# needs real processes, so this starts them.
#
# It also covers a case one session cannot reach at all. At the end of a run
# pytest finalises the last item's fixtures and the session's fixtures in one
# pass, so a restore hooked onto the item's teardown runs *after* the test
# database has been dropped. Every child below ends on a transactional test on
# purpose, and the first one is the one that drops.
#
# Two of the three children migrate a database from zero, which is most of what
# this costs. It is one file in one shard, and the alternative is a defect that
# reaches every developer who runs a concurrency module on its own and nothing
# in CI at all — a shard ends on a test needing no database, which is where this
# hid for a whole round.

#: The child's database, kept away from the one this very test is running in.
#: Suffixed with the process id so two shards, or two developers, never collide.
CHILD_DATABASE = f"juristid_isolation_probe_{os.getpid()}"

#: The transactional test the children run: cheap, and it flushes.
CHILD_FLUSHES = (
    "tests/test_reference_data_isolation.py"
    "::test_a_transactional_test_flushes_everything_it_can_see"
)

#: The seed-dependent test the last child runs: it reads rows only a migration wrote.
CHILD_READS_THE_BASELINE = (
    "tests/test_stage_vocabulary_seed.py::test_ten_canonical_stages_are_seeded"
)


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_a_second_process_reusing_the_database_still_finds_the_baseline(
    child_database: str,
) -> None:
    """The reproduction from `docs/ci-architecture.md`, run as a test.

    Before the contract, the third invocation failed with
    `assert {...the ten canonical stage keys...} <= set()`. Ordinary pytest
    runs; no xdist, no plugin, nothing reordered.
    """
    # No `--reuse-db`, so this one drops its database on the way out — while the
    # last item it ran was the transactional one whose teardown asks for a
    # restore. Reaching for a database that is already gone reads as an
    # `ERROR at teardown` on a test that passed, and nothing else notices.
    disposable = _run_child(CHILD_FLUSHES, "--create-db", database=child_database)
    assert disposable.returncode == 0, _report(disposable)
    assert "ERROR at teardown" not in disposable.stdout, _report(disposable)
    assert "1 passed" in disposable.stdout, _report(disposable)

    # Now the hand-over. This one keeps its database, and the last thing it did
    # was flush every table.
    keeps = _run_child(CHILD_FLUSHES, "--create-db", "--reuse-db", database=child_database)
    assert keeps.returncode == 0, _report(keeps)
    assert "ERROR at teardown" not in keeps.stdout, _report(keeps)

    inherits = _run_child(CHILD_READS_THE_BASELINE, "--reuse-db", database=child_database)

    assert inherits.returncode == 0, _report(inherits)
    assert "1 passed" in inherits.stdout, _report(inherits)


@pytest.fixture
def child_database() -> Generator[str]:
    """A database of the child's own, dropped however the test ends.

    `DROP DATABASE` cannot run inside a transaction, which is one more reason
    this test is transactional: there is no transaction to be inside.
    """
    yield CHILD_DATABASE
    with connection.cursor() as cursor:
        cursor.execute(f'DROP DATABASE IF EXISTS "test_{CHILD_DATABASE}" WITH (FORCE)')


def _run_child(target: str, *options: str, database: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - a fixed interpreter and a repository path
        [
            sys.executable,
            "-m",
            "pytest",
            target,
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
            *options,
        ],
        cwd=ROOT,
        env={**os.environ, "POSTGRES_DB": database},
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )


def _report(result: subprocess.CompletedProcess[str]) -> str:
    return f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


# ---------------------------------------------------------------------------
# 4. Nothing joins the suite outside the contract
# ---------------------------------------------------------------------------


def test_every_collected_transactional_test_declares_serialized_rollback(
    request: pytest.FixtureRequest,
) -> None:
    """The live check, against pytest's own metadata for this very run.

    Sees what a source scan cannot — a marker applied by a hook, by
    ``usefixtures``, by parametrisation, by a ``TransactionTestCase`` subclass —
    but only for the items this invocation collected, which under CI sharding is
    one slice of the suite. The scan below is the half that covers all of it.
    """
    offenders = sorted(
        item.nodeid
        for item in request.session.items
        if reference_baseline.uses_real_transactions(item)
        and not reference_baseline.declares_serialized_rollback(item)
        and _normalised(item.nodeid) not in DOCUMENTED_EXCEPTIONS
    )

    assert not offenders, (
        "these tests flush the database at teardown without declaring "
        "serialized_rollback=True, so nothing asks Django to snapshot the "
        f"migrated state they destroy: {offenders}"
    )


def test_no_source_file_declares_a_transactional_test_outside_the_contract() -> None:
    """The whole-tree check, read from the source rather than from a run.

    A parser rather than a text search: ``django_db(True)`` is transactional and
    the same words inside a docstring are not, and only an AST tells the two
    apart. This one runs without a database, so it is also the version that
    holds in the format-and-lint gate.
    """
    offenders = sorted(
        declaration
        for declaration, transactional, serialized in _marker_declarations()
        if transactional
        and declaration not in DOCUMENTED_EXCEPTIONS
        and (not serialized or not declaration.startswith(f"{GUARDED_TREE}/"))
    )

    assert not offenders, (
        "a transactional test must live under tests/, where the teardown hook "
        "is, and declare serialized_rollback=True — or be listed in "
        f"DOCUMENTED_EXCEPTIONS with a reason: {offenders}"
    )


def test_the_only_route_into_a_transactional_test_is_the_marker() -> None:
    """The scan above reads markers, so nothing may arrive another way.

    pytest-django also makes a test transactional through the ``transactional_db``,
    ``live_server`` and ``django_db_reset_sequences`` fixtures, and through a
    ``TransactionTestCase`` subclass. None of those are used here. If one ever
    is, the marker scan would stop being a complete answer, and this is what
    says so — the live check above still covers it, but only for the items one
    invocation collected.
    """
    offenders = sorted(_non_marker_transactional_declarations())

    assert not offenders, (
        "these declare a transactional test without the django_db marker, so "
        "the source scan above no longer sees every one of them: "
        f"{offenders}"
    )


# ---------------------------------------------------------------------------
# Reading the source
# ---------------------------------------------------------------------------

#: The `django_db` marker's signature, in order, so a positional argument is
#: read the same way pytest-django reads it.
MARKER_PARAMETERS = (
    "transaction",
    "reset_sequences",
    "databases",
    "serialized_rollback",
    "available_apps",
)


def _normalised(nodeid: str) -> str:
    return nodeid.replace("\\", "/")


@cache
def _parsed_test_trees() -> tuple[tuple[str, ast.Module], ...]:
    """Every test module, parsed once, as (repository-relative path, tree)."""
    return tuple(
        (_normalised(str(path.relative_to(ROOT))), ast.parse(path.read_text(encoding="utf-8")))
        for tree in TEST_TREES
        for path in sorted((ROOT / tree).rglob("*.py"))
    )


def _marker_declarations() -> list[tuple[str, bool, bool]]:
    """Every `django_db` marker in the test trees.

    Each entry is (where it is, is it transactional, does it declare
    serialized_rollback). "Where" is a node id when the marker decorates a
    function and a file path when it is a module-level `pytestmark`.
    """
    declarations: list[tuple[str, bool, bool]] = []
    for where, module in _parsed_test_trees():
        for call, function in _django_db_markers(module):
            arguments = _marker_arguments(call)
            declarations.append(
                (
                    f"{where}::{function}" if function else where,
                    bool(arguments.get("transaction") or arguments.get("reset_sequences")),
                    bool(arguments.get("serialized_rollback")),
                )
            )
    return declarations


def _django_db_markers(module: ast.Module) -> list[tuple[ast.Call, str]]:
    """Each `django_db` marker call in the module, with the function it decorates.

    One pass per function rather than a search of the whole module per marker:
    the trees here are large enough for the difference to be felt in a gate that
    is meant to be free.
    """
    found: list[tuple[ast.Call, str]] = []
    claimed: set[int] = set()
    for node in ast.walk(module):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for descendant in ast.walk(node):
            if isinstance(descendant, ast.Call) and _is_django_db_marker(descendant.func):
                found.append((descendant, node.name))
                claimed.add(id(descendant))
    for node in ast.walk(module):
        if (
            isinstance(node, ast.Call)
            and _is_django_db_marker(node.func)
            and id(node) not in claimed
        ):
            found.append((node, ""))
    return found


def _non_marker_transactional_declarations() -> list[str]:
    """Places a test becomes transactional without saying `django_db(...)`."""
    found: list[str] = []
    for where, module in _parsed_test_trees():
        for node in ast.walk(module):
            if isinstance(node, ast.ClassDef) and any(
                _base_name(base) == "TransactionTestCase" for base in node.bases
            ):
                found.append(f"{where}::{node.name}")
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                requested = {argument.arg for argument in node.args.args}
                for fixture in sorted(requested & reference_baseline.TRANSACTIONAL_FIXTURES):
                    found.append(f"{where}::{node.name} requests {fixture}")
    return found


def _is_django_db_marker(func: ast.expr) -> bool:
    return isinstance(func, ast.Attribute) and func.attr == "django_db"


def _marker_arguments(call: ast.Call) -> dict[str, object]:
    """Bind the call's arguments to the marker's parameter names."""
    arguments: dict[str, object] = {}
    for name, argument in zip(MARKER_PARAMETERS, call.args, strict=False):
        arguments[name] = _literal(argument)
    for keyword in call.keywords:
        if keyword.arg is not None:
            arguments[keyword.arg] = _literal(keyword.value)
    return arguments


def _literal(node: ast.expr) -> object:
    """The node's value if it is a literal, and a truthy sentinel if it is not.

    A non-literal is treated as "might be True", because a marker whose
    transactionality this file cannot read is exactly the one worth reporting.
    """
    try:
        return ast.literal_eval(node)
    except ValueError:
        return "unreadable"


def _base_name(base: ast.expr) -> str:
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    return ""
