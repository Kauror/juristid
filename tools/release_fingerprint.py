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
* a release that changes the models the fingerprint does not count, a migrations
  package's ``__init__.py``, or sets ``MIGRATION_MODULES``;
* a migration file it cannot read as plain ``makemigrations`` output: anything at module
  level but allowlisted imports and the ``Migration`` class; a decorator or class keyword;
  ``replaces``, ``run_before``, ``initial``, ``atomic``, or a name assigned twice; a
  dependency other than ``(app, migration)`` or ``swappable_dependency(settings.
  AUTH_USER_MODEL)``; an ``operations`` value that is not a literal list of
  ``migrations.<Operation>(…)`` calls; any dunder, ``getattr`` or code-running builtin;
* any operation outside `SUPPORTED_OPERATIONS` (schema only), and any mention, wherever
  and however written, of a data, raw SQL, delete or rename operation;
* an operation argument that is not inert data for its role: calls only of
  `ALLOWED_CALLS`, references only to `ALLOWED_REFERENCES` (whole dotted names), sizes
  only whole numbers, identifiers only plain names, operator classes only from a short
  list, no ``template``/``function``/``sql`` keyword, and no ``"`` or ``;`` outside free
  text such as ``help_text``;
* ``proxy``, ``managed`` or ``db_table`` in a model's options, or a created model the
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

#: Operations that change only the schema: no row is added or removed, so row counts
#: stay as they were (a default or a type change may still rewrite values in place).
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
#: Builtins that run or reach code, refused anywhere in a migration file.
CODE_BUILTINS = frozenset(
    {"eval", "exec", "compile", "open", "globals", "locals", "vars", "breakpoint", "input"}
)
#: Attribute names refused (``migrations.operations.X``); the class's own ``operations``
#: assignment is a plain name and is checked on its own.
FORBIDDEN_ATTRIBUTES = frozenset({"operations"})
REFUSED_CLASS_ATTRIBUTES = frozenset({"replaces", "run_before", "initial", "atomic"})
REFUSED_MODEL_OPTIONS = frozenset({"proxy", "managed", "db_table"})
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


#: The only imports a plain `makemigrations` file in this repository uses (surveyed
#: 23.11.2026); any other import, a star import or an alias is refused.
ALLOWED_FROM_IMPORTS = {"django.db": {"migrations", "models"}, "django.conf": {"settings"}}
ALLOWED_IMPORTS = frozenset(
    {
        "app.core.ids",
        "django.contrib.postgres.fields",
        "django.contrib.postgres.indexes",
        "django.contrib.postgres.search",
        "django.core.validators",
        "django.db.models.deletion",
        "django.db.models.manager",
        "django.utils.timezone",
        "uuid",
    }
)
_FIELDS = (
    "AutoField BigAutoField BigIntegerField BooleanField CharField DateField DateTimeField "
    "DecimalField DurationField EmailField FloatField ForeignKey GenericIPAddressField "
    "IntegerField JSONField ManyToManyField OneToOneField PositiveBigIntegerField "
    "PositiveIntegerField PositiveSmallIntegerField SlugField SmallIntegerField TextField "
    "TimeField URLField UUIDField"
).split()
#: The exact callables an operation's arguments may call (surveyed from this repository's
#: schema-only migrations, 23.11.2026): field, index and constraint classes and the inert
#: query objects. Never ``RawSQL``, ``Func``, ``ExpressionWrapper`` or anything reached
#: through a module (``uuid.os.system`` …): a dotted name is matched whole.
ALLOWED_CALLS = frozenset(
    [f"models.{field}" for field in _FIELDS]
    + [
        "models.Q",
        "models.F",
        "models.Value",
        "models.OrderBy",
        "models.Index",
        "models.UniqueConstraint",
        "models.CheckConstraint",
        "django.db.models.manager.Manager",
        "django.contrib.postgres.fields.ArrayField",
        "django.contrib.postgres.indexes.GinIndex",
        "django.contrib.postgres.search.SearchVectorField",
    ]
)
#: The exact names an operation's arguments may refer to without calling them.
ALLOWED_REFERENCES = frozenset(
    {
        "app.core.ids.uuid7",
        "dict",
        "list",
        "django.utils.timezone.now",
        "settings.AUTH_USER_MODEL",
        "uuid.uuid4",
        *(
            f"django.db.models.deletion.{rule}"
            for rule in ("CASCADE", "PROTECT", "SET_NULL", "SET_DEFAULT", "RESTRICT", "DO_NOTHING")
        ),
    }
)
#: Keywords that put SQL text into DDL, refused on any call.
REFUSED_KEYWORDS = frozenset({"template", "function", "sql", "arg_joiner"})


def _check_imports(tree: ast.Module) -> None:
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            allowed = ALLOWED_FROM_IMPORTS.get(node.module or "", set())
            for alias in node.names:
                if node.level or alias.asname or alias.name not in allowed:
                    raise Refused(f"an import this helper does not allow: from {node.module}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname or alias.name not in ALLOWED_IMPORTS:
                    raise Refused(f"an import this helper does not allow: {alias.name}")
        elif isinstance(node, ast.ClassDef) or (
            isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        ):
            continue
        else:
            raise Refused(f"module-level code that is not an import (line {node.lineno})")


def _dotted(node: ast.expr) -> str:
    """``a.b.c`` for a chain of attributes on a name, else ``""``."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return ""
    parts.append(node.id)
    return ".".join(reversed(parts))


#: What a string or number means, by the keyword (or options key) it sits under. Django
#: writes some of these into DDL unquoted (``opclasses``, the sizes) or passes an already
#: double-quoted identifier through (``quote_name``), so each is checked for its role.
NUMERIC_KEYWORDS = frozenset({"max_length", "max_digits", "decimal_places", "size"})
IDENTIFIER_KEYWORDS = frozenset(
    {"name", "model_name", "db_table", "db_column", "db_tablespace", "db_collation"}
)
FIELD_LIST_KEYWORDS = frozenset({"fields", "include"})
ALLOWED_OPCLASSES = frozenset({"gin_trgm_ops", "text_pattern_ops", "varchar_pattern_ops"})
#: Free text Django sends as a parameter or quotes as a value, never as SQL.
TEXT_KEYWORDS = frozenset(
    {
        "verbose_name",
        "verbose_name_plural",
        "help_text",
        "db_comment",
        "violation_error_message",
        "default",
        "db_default",
        "choices",
    }
)
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}")
_FIELD_REFERENCE = re.compile(r"-?[A-Za-z_][A-Za-z0-9_]{0,62}")


def _check_constant(node: ast.Constant, key: str | None) -> None:
    value = node.value
    if key in NUMERIC_KEYWORDS:
        if value is not None and (type(value) is not int or value < 0):
            raise Refused(f"{key}= is not a whole number")
        return
    if not isinstance(value, str):
        return
    if key == "opclasses":
        if value not in ALLOWED_OPCLASSES:
            raise Refused(f"an operator class this helper does not allow: {value!r}")
    elif key in IDENTIFIER_KEYWORDS:
        if not _IDENTIFIER.fullmatch(value):
            raise Refused(f"{key}= is not a plain identifier: {value!r}")
    elif key in FIELD_LIST_KEYWORDS:
        if not _FIELD_REFERENCE.fullmatch(value):
            raise Refused(f"{key}= names something that is not a plain field: {value!r}")
    elif key not in TEXT_KEYWORDS and ('"' in value or ";" in value):
        raise Refused(f"a string under {key or 'a positional argument'} holds a quote or ;")


def _check_value(node: ast.expr, key: str | None = None) -> None:
    """One operation argument is inert data for its role: a literal checked for the
    keyword it sits under (`_check_constant`), a container of such, an allowed reference,
    a call of an allowed class with inert arguments, or ``|``, ``&``, ``~``, ``-`` over
    inert data. Everything else is refused."""
    if key in NUMERIC_KEYWORDS and not isinstance(node, ast.Constant):
        raise Refused(f"{key}= is not a literal number")
    if isinstance(node, ast.Constant):
        _check_constant(node, key)
        return
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        for item in node.elts:
            _check_value(item, key)
        return
    if isinstance(node, ast.Dict):
        for dict_key, value in zip(node.keys, node.values, strict=True):
            if dict_key is None:
                raise Refused("an operation argument is unpacked with **")
            if not (isinstance(dict_key, ast.Constant) and isinstance(dict_key.value, str)):
                raise Refused("a dict key that is not a literal string")
            _check_constant(dict_key, None)
            _check_value(value, dict_key.value)
        return
    if isinstance(node, ast.Attribute | ast.Name):
        if _dotted(node) not in ALLOWED_REFERENCES:
            raise Refused(f"an operation argument refers to {_dotted(node) or 'an expression'}")
        return
    if isinstance(node, ast.Call):
        name = _dotted(node.func)
        if name not in ALLOWED_CALLS:
            raise Refused(f"an operation argument calls {name or 'an expression'}")
        positional = "size" if name == "django.contrib.postgres.fields.ArrayField" else None
        for index, item in enumerate(node.args):
            _check_value(item, positional if index == 1 else None)
        for keyword in node.keywords:
            if keyword.arg is None:
                raise Refused("an operation argument is unpacked with **")
            if keyword.arg in REFUSED_KEYWORDS:
                raise Refused(f"an operation argument passes {keyword.arg}=")
            _check_value(keyword.value, keyword.arg)
        return
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr | ast.BitAnd):
        _check_value(node.left, key)
        _check_value(node.right, key)
        return
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Invert | ast.USub):
        _check_value(node.operand, key)
        return
    raise Refused(f"an operation argument holds {type(node).__name__}")


def _check_arguments(call: ast.Call) -> None:
    """Every argument of an operation is inert data for its role (`_check_value`)."""
    for argument in call.args:
        _check_value(argument)
    for keyword in call.keywords:
        if keyword.arg is None:
            raise Refused("an operation argument is unpacked with **")
        _check_value(keyword.value, keyword.arg)


def _forbidden_name(tree: ast.Module) -> str:
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and (
            node.id in FORBIDDEN_NAMES or node.id in CODE_BUILTINS or node.id.startswith("__")
        ):
            return node.id
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return node.attr
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
    if cls.decorator_list or cls.keywords:
        raise Refused("class Migration has a decorator or a class keyword")
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
        if name in values:
            raise Refused(f"class Migration assigns {name} twice")
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
            and len(item.args) == 1
            and _dotted(item.args[0]) == "settings.AUTH_USER_MODEL"
        ):
            continue
        raise Refused("a dependency that is neither (app, migration) nor swappable_dependency")
    return found


def _refuse_model_options(call: ast.Call, operation: str) -> None:
    """``options`` may not make a model proxy, unmanaged or point it at another table."""
    for keyword in call.keywords:
        if keyword.arg == "options" and isinstance(keyword.value, ast.Dict):
            keys = {k.value for k in keyword.value.keys if isinstance(k, ast.Constant)}
            refused = sorted(str(k) for k in keys & REFUSED_MODEL_OPTIONS)
            if refused:
                raise Refused(f"{operation} sets {', '.join(refused)}")


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
        _check_arguments(item)
        if name == "AlterModelOptions":
            _refuse_model_options(item, "AlterModelOptions")
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


def _uncounted(repo: Path, ref: str) -> frozenset[str]:
    deployment = _show(repo, ref, DEPLOYMENT_PATH)
    return frozenset(
        _string_set(deployment, "REBUILDABLE_MODELS")
        | _string_set(deployment, "OPERATIONAL_MODELS")
    )


def _app_label(source: str) -> str:
    """The ``label = "…"`` an apps.py's AppConfig class sets, or ``""``."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if (
                    isinstance(item, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "label" for t in item.targets)
                    and isinstance(item.value, ast.Constant)
                    and isinstance(item.value.value, str)
                ):
                    return item.value.value
    return ""


def _leaf(repo: Path, ref: str, app: str) -> str:
    """The app's highest-numbered migration at ``ref`` (by file name; the dependency check
    and the release's single-migration rule make this the graph's leaf)."""
    try:
        listing = _git(repo, "ls-tree", "--name-only", f"{ref}:app/{app}/migrations").decode()
    except Refused as error:
        raise Refused(f"app {app} has no migrations package at {ref} (a new app?)") from error
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
    top = _git(repo, "rev-parse", "--show-toplevel").decode().strip()
    if Path(top).resolve() != repo.resolve():
        raise Refused(f"--repo must be the repository's top level ({top})")
    pinned = _show(repo, ref, HELPER_PATH).replace("\r\n", "\n").encode("utf-8")
    if helper.read_bytes().replace(b"\r\n", b"\n") != pinned:
        raise Refused(f"this helper is not {HELPER_PATH} at {ref}")
    # The fingerprint's own exclusions may not move in the same release: a model moved
    # into them would drop out of both sides of the comparison unseen.
    uncounted = _uncounted(repo, ref)
    if uncounted != _uncounted(repo, previous_release):
        raise Refused(
            f"the release changes the models the fingerprint does not count ({DEPLOYMENT_PATH})"
        )
    if "MIGRATION_MODULES" in _show(repo, ref, "config/settings.py"):
        raise Refused("config/settings.py sets MIGRATION_MODULES: migrations may live outside app/")
    changed = _git(
        repo, "diff", "--name-status", "--no-renames", previous_release, ref, "--", ":(top)app"
    ).decode()
    migrations = []
    for line in changed.splitlines():
        status, _, path = line.partition("\t")
        if "/migrations/" not in path:
            continue
        if path.endswith("/migrations/__init__.py"):
            if status != "A" or _show(repo, ref, path).strip():
                raise Refused(f"a migrations package's __init__.py changes: {status} {path}")
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
    if _app_label(_show(repo, ref, f"app/{app}/apps.py")) != app:
        raise Refused(f"app/{app}/apps.py does not label the app {app!r}")
    if _leaf(repo, ref, app) != migration:
        raise Refused(f"{app}.{migration} is not the app's highest-numbered migration at {ref}")
    previous_leaf = _leaf(repo, previous_release, app)
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
    parser.add_argument(
        "--migration-file",
        type=Path,
        help="review mode: one local file; never for a kit (no pinning, no release checks)",
    )
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
