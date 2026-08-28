"""No file in this repository still carries a merge conflict marker.

Written because one got through. A rebase of the current-register refresh onto
the branch that had just landed beside it left ``<<<<<<< HEAD`` in an ADR's
title line, and every gate passed: the suites do not read prose, the ADR tests
check numbers and index rows rather than bodies, and ``ruff`` never sees
Markdown. The only thing standing between that and a merged repository was
somebody happening to look at the top of the file.

Two branches editing the same file is the ordinary condition here — parallel
work is how this project runs — so the failure is not exotic and will happen
again. This is the cheapest possible check that it fails loudly.

The markers are matched at the start of a line, with the exact shapes git
writes, so a document that discusses conflicts in prose is unaffected: this
file names them in its own docstring and does not trip itself.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from django.conf import settings

#: Through the setting, like `test_repository_data_safety` does, so the two
#: repository-wide checks agree about where the repository is.
ROOT = Path(settings.BASE_DIR)

#: Git's own markers, anchored to the start of a line. ``|||||||`` appears in
#: diff3 style, which this repository does not use by default but a developer's
#: own ``merge.conflictStyle`` may.
MARKERS: tuple[str, ...] = ("<<<<<<< ", "||||||| ", ">>>>>>> ")

#: The bare separator is deliberately **not** in that list. Seven equals signs
#: at the start of a line is also how reStructuredText underlines a heading and
#: how several docs here draw a rule, so matching it would fail on files that
#: are perfectly correct. The two bracketing markers are unambiguous and always
#: present in a real conflict.

#: Suffixes worth reading. Binary files cannot carry a marker git would have
#: written, and a lock file that somehow did is not something this test can
#: usefully say anything about.
TEXT_SUFFIXES: frozenset[str] = frozenset(
    {
        ".py",
        ".md",
        ".html",
        ".css",
        ".js",
        ".toml",
        ".yml",
        ".yaml",
        ".json",
        ".txt",
        ".cfg",
        ".ini",
        ".sh",
        ".sql",
        ".tsv",
        ".csv",
        ".env",
        ".example",
    }
)


def tracked_files() -> list[Path]:
    """Every file git tracks, so nothing untracked or ignored is read.

    Asking git rather than walking the tree keeps the check away from
    ``.venv``, ``node_modules``, build output and an operator's local scratch
    files — none of which this repository is responsible for.
    """
    result = subprocess.run(
        ["git", "ls-files", "-z"],  # noqa: S607 - git off PATH, with literal arguments
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    names = result.stdout.decode("utf-8").split("\0")
    return [ROOT / name for name in names if name]


def test_there_are_files_to_check() -> None:
    """Guards the guard: a `git ls-files` that returned nothing passes silently."""
    assert len(tracked_files()) > 100


def test_no_tracked_file_carries_a_conflict_marker() -> None:
    offenders: dict[str, list[int]] = {}
    for path in tracked_files():
        if path.suffix not in TEXT_SUFFIXES or not path.is_file():
            continue
        # This module names the markers in its own prose, and quoting them is
        # the clearest way to document what it looks for.
        if path.name == Path(__file__).name:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):  # pragma: no cover - not text after all
            continue
        lines = [
            number
            for number, line in enumerate(text.splitlines(), start=1)
            if line.startswith(MARKERS)
        ]
        if lines:
            offenders[str(path.relative_to(ROOT))] = lines

    assert not offenders, "unresolved conflict markers: " + "; ".join(
        f"{name} (line {', '.join(str(number) for number in numbers)})"
        for name, numbers in sorted(offenders.items())
    )


@pytest.mark.parametrize("marker", MARKERS)
def test_the_check_would_notice_each_marker(marker: str, tmp_path: Path) -> None:
    """The detection itself, on a file that really carries one.

    Without this the test above passes just as happily when its matching is
    broken as when the repository is clean, which is the failure mode it exists
    to prevent in the first place.
    """
    sample = tmp_path / "conflicted.md"
    sample.write_text(f"# Title\n{marker}HEAD\nboth sides\n", encoding="utf-8")

    hits = [
        number
        for number, line in enumerate(sample.read_text(encoding="utf-8").splitlines(), start=1)
        if line.startswith(MARKERS)
    ]
    assert hits == [2]
