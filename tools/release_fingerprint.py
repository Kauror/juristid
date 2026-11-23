"""The fingerprint differences a migration release may show, from its reviewed migration.

    python -m tools.release_fingerprint --app legacy_import \\
        --migration 0033_lovable_owner_alias --previous-leaf 0032_postipoiss_letters \\
        --migration-file app/legacy_import/migrations/0033_lovable_owner_alias.py

prints the lines, C-sorted, ready for a release kit's ``FP_EXPECTED_LINES``.

**Why (r89, 23.11.2026).** A release kit's d2 compares `recovery_fingerprint` before and
after the swap, and its ``fingerprint_verdict`` stops unless the differences are exactly
``FP_EXPECTED_LINES``. r89 created one new, empty table; the fingerprint reported
``canonical_counts.legacy_import.LovableOwnerAlias: None -> 0`` beside the two
migration-leaf lines, but the kit's hand-written list held only the leaves, so d2 stopped
after a correct swap. r84 had listed its new models by hand; r89's generator forgot. The
verdict was right to stop.

**What this does, and what it never does.** It writes the expected list from the reviewed
migration file itself: the two leaf lines of a one-migration release, and ``None -> 0`` for
each model that file's ``CreateModel`` operations create — nothing else. It never touches
the verdict, which stays an exact comparison: a new model that already holds rows
(``None -> 1``), a model the migration does not create, any other count that moved, a
missing expected line or any other leaf still stops the release. Every line it emits is
checked against those two exact shapes (`check_lines`), and anything it cannot read — a
``CreateModel`` without a literal name, a model created twice, a malformed label — is
refused rather than skipped.

**Pinned to reviewed source.** A kit generator reads this file at the reviewed release
commit (``git show <TARGET>:tools/release_fingerprint.py``) and the migration from the same
commit, so the list comes from code CI tested (tests/test_release_fingerprint.py) and the
blob the kit already pins.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_CREATE = re.compile(r"migrations\.CreateModel\(\s*name\s*=\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]")
_OPERATION = re.compile(r"migrations\.([A-Z][A-Za-z]*)\(")
_APP = re.compile(r"[a-z_][a-z0-9_]*")
_MIGRATION = re.compile(r"[0-9]{4}_[a-z0-9_]+")
#: The only two shapes an expected line may take.
_LINE = re.compile(
    r"canonical_counts\.[a-z_][a-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*: None -> 0"
    r"|migration_leaves: (newly present|no longer present): "
    r"[a-z_][a-z0-9_]*\.[0-9]{4}_[a-z0-9_]+"
)


def created_models(text: str) -> list[str]:
    """The model names a migration's ``CreateModel`` operations create, in file order.

    Refuses one it cannot name (it would otherwise be left out silently, and the release
    would stop after the swap again) and a model created twice.
    """
    names = _CREATE.findall(text)
    creates = [op for op in _OPERATION.findall(text) if op == "CreateModel"]
    if len(names) != len(creates):
        raise ValueError(
            f"{len(creates)} CreateModel operation(s) but {len(names)} readable name(s)"
        )
    if len(set(names)) != len(names):
        raise ValueError(f"a model is created twice: {names}")
    return names


def new_model_lines(app: str, text: str) -> list[str]:
    """``canonical_counts.<app>.<Model>: None -> 0`` for each model the migration creates.

    That is how ``recovery_fingerprint --compare`` words a model absent before and empty
    after.
    """
    if not _APP.fullmatch(app):
        raise ValueError(f"not an app label: {app!r}")
    return [f"canonical_counts.{app}.{name}: None -> 0" for name in created_models(text)]


def leaf_lines(app: str, migration: str, previous_leaf: str) -> list[str]:
    """The two migration-leaf lines of a release that adds one migration to ``app``."""
    if not _APP.fullmatch(app):
        raise ValueError(f"not an app label: {app!r}")
    for value in (migration, previous_leaf):
        if not _MIGRATION.fullmatch(value):
            raise ValueError(f"not a migration name: {value!r}")
    if migration == previous_leaf:
        raise ValueError("the new migration cannot be its own previous leaf")
    return [
        f"migration_leaves: newly present: {app}.{migration}",
        f"migration_leaves: no longer present: {app}.{previous_leaf}",
    ]


def check_lines(lines: list[str]) -> list[str]:
    """Refuse any line that is not one of the two exact shapes, and any duplicate."""
    for line in lines:
        if not _LINE.fullmatch(line):
            raise ValueError(f"not an allowed expected line: {line!r}")
    if len(set(lines)) != len(lines):
        raise ValueError("a line is listed twice")
    return lines


def expected_lines(*, app: str, migration: str, previous_leaf: str, text: str) -> list[str]:
    """Every line the release's fingerprint must show, C-sorted (``LC_ALL=C sort``)."""
    lines = leaf_lines(app, migration, previous_leaf) + new_model_lines(app, text)
    return sorted(check_lines(lines), key=lambda s: s.encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A release kit's expected fingerprint lines.")
    parser.add_argument("--app", required=True)
    parser.add_argument("--migration", required=True, help="e.g. 0033_lovable_owner_alias")
    parser.add_argument("--previous-leaf", required=True, help="e.g. 0032_postipoiss_letters")
    parser.add_argument("--migration-file", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.migration_file.stem != args.migration:
        parser.error(f"--migration-file is {args.migration_file.name}, not {args.migration}.py")
    lines = expected_lines(
        app=args.app,
        migration=args.migration,
        previous_leaf=args.previous_leaf,
        text=args.migration_file.read_text(encoding="utf-8"),
    )
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
