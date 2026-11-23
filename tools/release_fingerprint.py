"""The fingerprint differences a release may show, derived from the release itself.

A release kit generator runs it as the release commit's own copy, against the commit that
production runs now:

    tmp="$(mktemp)"
    git -C <repo> show "$TARGET:tools/release_fingerprint.py" > "$tmp"
    FP_EXPECTED_LINES="$(python -I "$tmp" --repo <repo> --ref "$TARGET" \\
        --previous-release "$PREVIOUS")" || exit 1

and gets the lines, C-sorted, for the kit's ``FP_EXPECTED_LINES``: nothing for a release
with no migration; for a release with exactly one migration, the two migration-leaf lines
and ``None -> 0`` for each model it creates.

**Why (r89, 23.11.2026).** A release kit's d2 compares `recovery_fingerprint` before and
after the swap, and its ``fingerprint_verdict`` stops unless the differences are exactly
``FP_EXPECTED_LINES``. r89 created one new, empty table; the fingerprint reported
``canonical_counts.legacy_import.LovableOwnerAlias: None -> 0`` beside the two
migration-leaf lines, but the kit's hand-written list held only the leaves, so d2 stopped
after a correct swap. The verdict was right to stop.

**What it never does.** It never touches the verdict, which stays an exact comparison, and
every line it emits must be one of the two exact shapes (`check_lines`): it can make a kit
expect the approved empty tables, never accept a count that moved.

**What it refuses, before anything is deployed** (exit 3, ``REFUSED: …``). A release it
cannot vouch for needs its expected lines reviewed by hand:

* more than one migration in the release (in one app or across apps), a migration that is
  not its app's new leaf, or one whose dependency is not the leaf production has;
* a migration file it cannot read as plain ``makemigrations`` output: anything at module
  level but imports and the ``Migration`` class; any import from ``django.db.migrations``
  or an aliased ``migrations``; ``replaces``, ``run_before``, ``initial`` or ``atomic``;
  an ``operations`` value that is not a literal list of ``migrations.<Operation>(…)``
  calls; ``getattr``;
* any operation outside `SUPPORTED_OPERATIONS` (schema only), and any mention, wherever
  and however written, of a data, raw SQL, delete or rename operation;
* a ``CreateModel`` with ``proxy`` or ``managed`` in its options, or of a model the
  fingerprint does not count (``REBUILDABLE_MODELS`` / ``OPERATIONAL_MODELS`` in
  ``app/core/deployment.py`` at the release commit);
* an app whose ``AppConfig.label`` is not its directory name.

**Pinned to the release commit.** Every file it reads comes from ``git show <ref>:…``, and
it refuses to run unless its own bytes are ``<ref>``'s ``tools/release_fingerprint.py``
(a guard against running a stale local copy: the pinning itself is the ``git show`` above).
The kit records the helper's blob sha beside its output.
"""

from __future__ import annotations

import argparse
import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path

#: Operations that change only the schema and create nothing but empty tables.
SUPPORTED_OPERATIONS = frozenset(
    {
        "CreateModel",
        "AddField",
        "AlterField",
        "AddIndex",
        "RemoveIndex",
        "AddConstraint",
        "RemoveConstraint",
        "AlterModelOptions",
        "AlterModelManagers",
    }
)
#: Names refused wherever they appear in a migration's code (not its comments).
FORBIDDEN_NAMES = frozenset(
    {
        "RunPython",
        "RunSQL",
        "SeparateDatabaseAndState",
        "DeleteModel",
        "RenameModel",
        "AlterModelTable",
        "AlterModelTableComment",
        "RemoveField",
        "RenameField",
        "RenameIndex",
        "AlterUniqueTogether",
        "AlterIndexTogether",
        "AlterOrderWithRespectTo",
        "getattr",
    }
)
#: Attribute names refused (``migrations.operations.X``); the class's own ``operations``
#: assignment is a plain name and is checked on its own.
FORBIDDEN_ATTRIBUTES = frozenset({"operations"})
REFUSED_CLASS_ATTRIBUTES = frozenset({"replaces", "run_before", "initial", "atomic"})
REFUSED_MODEL_OPTIONS = frozenset({"proxy", "managed"})
HELPER_PATH = "tools/release_fingerprint.py"
DEPLOYMENT_PATH = "app/core/deployment.py"
EXIT_REFUSED = 3

_APP = re.compile(r"[a-z_][a-z0-9_]*")
_MIGRATION = re.compile(r"[0-9]{4}_[a-z0-9_]+")
_MIGRATION_PATH = re.compile(r"app/([a-z_][a-z0-9_]*)/migrations/([0-9]{4}_[a-z0-9_]+)\.py")
_SHA = re.compile(r"[0-9a-f]{40}")
#: The only two shapes an expected line may take.
_LINE = re.compile(
    r"canonical_counts\.[a-z_][a-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*: None -> 0"
    r"|migration_leaves: (newly present|no longer present): "
    r"[a-z_][a-z0-9_]*\.[0-9]{4}_[a-z0-9_]+"
)


class Refused(ValueError):
    """A release this helper cannot vouch for: its expected lines need a person."""


# ---------------------------------------------------------------------------------------
# One migration file
# ---------------------------------------------------------------------------------------


def _is_migrations_attr(node: ast.AST, attr: str | None = None) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "migrations"
        and (attr is None or node.attr == attr)
    )


def _check_imports(tree: ast.Module) -> None:
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith("django.db.migrations"):
                raise Refused(f"imports from {module}")
            for alias in node.names:
                if alias.name == "migrations" and (module != "django.db" or alias.asname):
                    raise Refused("an aliased or unusual import of migrations")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("django.db.migrations"):
                    raise Refused(f"imports {alias.name}")
        elif isinstance(node, ast.ClassDef) or (
            isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        ):
            continue
        else:
            raise Refused(f"module-level code that is not an import (line {node.lineno})")


def _forbidden_name(tree: ast.Module) -> str:
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            return node.id
        if isinstance(node, ast.Attribute) and (
            node.attr in FORBIDDEN_NAMES or node.attr in FORBIDDEN_ATTRIBUTES
        ):
            return node.attr
    return ""


def _migration_class(tree: ast.Module) -> ast.ClassDef:
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    if len(classes) != 1 or classes[0].name != "Migration":
        raise Refused("not exactly one class Migration")
    cls = classes[0]
    if len(cls.bases) != 1 or not _is_migrations_attr(cls.bases[0], "Migration"):
        raise Refused("class Migration is not a migrations.Migration")
    return cls


def _class_values(cls: ast.ClassDef) -> dict[str, ast.expr]:
    values: dict[str, ast.expr] = {}
    for node in cls.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        if not (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            raise Refused(f"class Migration holds code that is not an assignment ({node.lineno})")
        name = node.targets[0].id
        if name in REFUSED_CLASS_ATTRIBUTES:
            raise Refused(f"class Migration sets {name}")
        if name not in {"dependencies", "operations"}:
            raise Refused(f"class Migration sets {name}")
        values[name] = node.value
    if "operations" not in values or "dependencies" not in values:
        raise Refused("class Migration lacks dependencies or operations")
    return values


def _dependencies(node: ast.expr) -> set[tuple[str, str]]:
    if not isinstance(node, ast.List):
        raise Refused("dependencies is not a literal list")
    found: set[tuple[str, str]] = set()
    for item in node.elts:
        if isinstance(item, ast.Tuple) and all(
            isinstance(e, ast.Constant) and isinstance(e.value, str) for e in item.elts
        ):
            values = [e.value for e in item.elts if isinstance(e, ast.Constant)]
            if len(values) == 2:
                found.add((str(values[0]), str(values[1])))
                continue
        if (
            isinstance(item, ast.Call)
            and _is_migrations_attr(item.func, "swappable_dependency")
            and not item.keywords
        ):
            continue
        raise Refused("a dependency that is neither (app, migration) nor swappable_dependency")
    return found


def _created_model(call: ast.Call) -> str:
    keywords = {k.arg: k.value for k in call.keywords}
    name = keywords.get("name")
    if call.args or not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
        raise Refused("a CreateModel without a literal name keyword")
    options = keywords.get("options")
    if options is not None:
        if not isinstance(options, ast.Dict) or not all(
            isinstance(k, ast.Constant) for k in options.keys
        ):
            raise Refused(f"CreateModel {name.value}: options are not a literal dict")
        keys = {k.value for k in options.keys if isinstance(k, ast.Constant)}
        refused = sorted(str(k) for k in keys & REFUSED_MODEL_OPTIONS)
        if refused:
            raise Refused(f"CreateModel {name.value} sets {', '.join(refused)}")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name.value):
        raise Refused(f"not a model name: {name.value!r}")
    return name.value


def analyse_migration(text: str, *, app: str, previous_leaf: str) -> list[str]:
    """The models one migration creates, in file order — or `Refused` (see the module)."""
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        raise Refused(f"not Python: {error.msg}") from error
    _check_imports(tree)
    forbidden = _forbidden_name(tree)
    if forbidden:
        raise Refused(f"unsupported: {forbidden}")
    values = _class_values(_migration_class(tree))
    if (app, previous_leaf) not in _dependencies(values["dependencies"]):
        raise Refused(f"its dependencies do not name ({app}, {previous_leaf})")
    operations = values["operations"]
    if not isinstance(operations, ast.List):
        raise Refused("operations is not a literal list")
    created: list[str] = []
    for item in operations.elts:
        if not (isinstance(item, ast.Call) and _is_migrations_attr(item.func)):
            raise Refused("an operation that is not a migrations.<Operation>(…) call")
        name = item.func.attr if isinstance(item.func, ast.Attribute) else ""
        if name not in SUPPORTED_OPERATIONS:
            raise Refused(f"unsupported operation: {name}")
        if name == "CreateModel":
            created.append(_created_model(item))
    if len(set(created)) != len(created):
        raise Refused(f"a model is created twice: {created}")
    return created


# ---------------------------------------------------------------------------------------
# The expected lines
# ---------------------------------------------------------------------------------------


def leaf_lines(app: str, migration: str, previous_leaf: str) -> list[str]:
    """The two migration-leaf lines of a release that adds one migration to ``app``."""
    if not _APP.fullmatch(app):
        raise Refused(f"not an app label: {app!r}")
    for value in (migration, previous_leaf):
        if not _MIGRATION.fullmatch(value):
            raise Refused(f"not a migration name: {value!r}")
    if migration == previous_leaf:
        raise Refused("the new migration cannot be its own previous leaf")
    return [
        f"migration_leaves: newly present: {app}.{migration}",
        f"migration_leaves: no longer present: {app}.{previous_leaf}",
    ]


def check_lines(lines: list[str]) -> list[str]:
    """Refuse any line that is not one of the two exact shapes, and any duplicate."""
    for line in lines:
        if not _LINE.fullmatch(line):
            raise Refused(f"not an allowed expected line: {line!r}")
    if len(set(lines)) != len(lines):
        raise Refused("a line is listed twice")
    return lines


def expected_lines(
    *,
    app: str,
    migration: str,
    previous_leaf: str,
    text: str,
    uncounted: frozenset[str] = frozenset(),
) -> list[str]:
    """Every line the release's fingerprint must show, C-sorted (``LC_ALL=C sort``)."""
    leaves = leaf_lines(app, migration, previous_leaf)
    created = analyse_migration(text, app=app, previous_leaf=previous_leaf)
    not_counted = sorted(f"{app}.{m}" for m in created if f"{app}.{m}" in uncounted)
    if not_counted:
        raise Refused(f"the fingerprint does not count {', '.join(not_counted)}")
    lines = leaves + [f"canonical_counts.{app}.{model}: None -> 0" for model in created]
    return sorted(check_lines(lines), key=lambda s: s.encode("utf-8"))


# ---------------------------------------------------------------------------------------
# The release, read from git
# ---------------------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> bytes:
    git = shutil.which("git")
    if git is None:
        raise Refused("git is not available")
    # A fixed program; every ref is a checked 40-character sha and every path is built
    # from checked labels.
    done = subprocess.run([git, "-C", str(repo), *args], capture_output=True, check=False)  # noqa: S603
    if done.returncode != 0:
        raise Refused(f"git {' '.join(args)} failed")
    return done.stdout


def _show(repo: Path, ref: str, path: str) -> str:
    return _git(repo, "show", f"{ref}:{path}").decode("utf-8")


def _string_set(source: str, name: str) -> set[str]:
    """The string literals of a module-level ``NAME = frozenset({...})``."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return {
                n.value
                for n in ast.walk(node.value)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            }
    raise Refused(f"{name} not found in {DEPLOYMENT_PATH}")


def _leaf(repo: Path, ref: str, app: str) -> str:
    listing = _git(repo, "ls-tree", "--name-only", f"{ref}:app/{app}/migrations").decode()
    names = sorted(
        n[:-3] for n in listing.split() if n.endswith(".py") and _MIGRATION.fullmatch(n[:-3])
    )
    if not names:
        raise Refused(f"app {app} has no migrations at {ref}")
    return names[-1]


def release_lines(*, repo: Path, ref: str, previous_release: str, helper: Path) -> list[str]:
    """The expected lines of the release ``previous_release`` → ``ref``, read from git."""
    for value in (ref, previous_release):
        if not _SHA.fullmatch(value):
            raise Refused(f"a commit must be a full 40-character sha, not {value!r}")
    pinned = _show(repo, ref, HELPER_PATH).replace("\r\n", "\n").encode("utf-8")
    if helper.read_bytes().replace(b"\r\n", b"\n") != pinned:
        raise Refused(f"this helper is not {HELPER_PATH} at {ref}")
    changed = _git(
        repo, "diff", "--name-status", "--no-renames", previous_release, ref, "--", "app"
    ).decode()
    migrations = []
    for line in changed.splitlines():
        status, _, path = line.partition("\t")
        if "/migrations/" not in path or path.endswith("__init__.py"):
            continue
        match = _MIGRATION_PATH.fullmatch(path)
        if status != "A" or match is None:
            raise Refused(f"a migration is changed, not added: {status} {path}")
        migrations.append(match.groups())
    if not migrations:
        return []
    if len(migrations) > 1:
        names = ", ".join(f"{a}.{m}" for a, m in migrations)
        raise Refused(f"more than one migration in the release ({names})")
    app, migration = migrations[0]
    if f'label = "{app}"' not in _show(repo, ref, f"app/{app}/apps.py"):
        raise Refused(f"app/{app}/apps.py does not label the app {app!r}")
    if _leaf(repo, ref, app) != migration:
        raise Refused(f"{app}.{migration} is not the app's leaf at {ref}")
    previous_leaf = _leaf(repo, previous_release, app)
    deployment = _show(repo, ref, DEPLOYMENT_PATH)
    uncounted = frozenset(
        _string_set(deployment, "REBUILDABLE_MODELS")
        | _string_set(deployment, "OPERATIONAL_MODELS")
    )
    return expected_lines(
        app=app,
        migration=migration,
        previous_leaf=previous_leaf,
        text=_show(repo, ref, f"app/{app}/migrations/{migration}.py"),
        uncounted=uncounted,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A release kit's expected fingerprint lines.")
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--ref", help="the release commit (full sha)")
    parser.add_argument("--previous-release", help="the commit production runs (full sha)")
    parser.add_argument("--migration-file", type=Path, help="review mode: one local file")
    parser.add_argument("--app")
    parser.add_argument("--previous-leaf")
    args = parser.parse_args(argv)
    try:
        if args.ref or args.previous_release:
            if not (args.ref and args.previous_release) or args.migration_file:
                raise Refused("--ref and --previous-release go together, without --migration-file")
            lines = release_lines(
                repo=args.repo,
                ref=args.ref,
                previous_release=args.previous_release,
                helper=Path(__file__),
            )
        else:
            if not (args.migration_file and args.app and args.previous_leaf):
                raise Refused("review mode needs --migration-file, --app and --previous-leaf")
            if not args.migration_file.is_file():
                raise Refused(f"no such file: {args.migration_file}")
            lines = expected_lines(
                app=args.app,
                migration=args.migration_file.stem,
                previous_leaf=args.previous_leaf,
                text=args.migration_file.read_text(encoding="utf-8"),
            )
    except Refused as error:
        sys.stderr.write(f"release_fingerprint: REFUSED: {error}\n")
        return EXIT_REFUSED
    sys.stdout.write("".join(f"{line}\n" for line in lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
