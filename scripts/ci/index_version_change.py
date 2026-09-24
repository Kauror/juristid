"""Does this release move the search index contract?

Run by `.github/workflows/release-image.yml` beside the release-note gate, over
the same two commits: the one production runs and the one being built.

    index_version_change.py --previous <sha> --target <sha>
                            [--git-dir DIR] [--env-file "$GITHUB_ENV"]

A release that changes `app.search.models.INDEX_VERSION` starts serving on rows
its own query refuses to read: search returns nothing until the operator runs
`rebuild_search_index` (runbook step 11). That step used to be conditional on
somebody remembering that this particular release moved the version, while the
readiness check and the search healthcheck stayed green (ENG-142). This makes
it a fact of the release artifact instead — derived from the source, not from
memory — so the manifest the operator deploys from says, in one line, whether
the rebuild is owed.

**Fails closed.** The target must declare a readable `INDEX_VERSION`; a build
that does not is refused. A previous commit that cannot be read (no such file,
no such constant) is reported as a change, so the rebuild is required: a rebuild
nobody needed costs a few seconds, a rebuild nobody ran costs every search.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

#: Where the constant lives. One file, one line; a move is a change to this
#: script, which is the point — nothing can relocate it silently.
MODELS_PATH = "app/search/models.py"

_CONSTANT = re.compile(r'^INDEX_VERSION\s*=\s*"([^"\n]+)"\s*$', re.MULTILINE)


class IndexVersionUnreadable(Exception):
    """The commit being released does not say which index contract it expects."""


@dataclass(frozen=True)
class IndexVersionChange:
    previous: str
    target: str

    @property
    def changed(self) -> bool:
        return self.previous != self.target

    def lines(self) -> list[str]:
        answer = "YES" if self.changed else "NO"
        return [
            f"index_version:            {self.previous or '(unreadable)'} -> {self.target}",
            f"index_version_changed:    {answer}",
            f"search_rebuild_required:  {answer}",
        ]


def declared_version(source: str) -> str:
    """The `INDEX_VERSION` a models file declares, or ``""`` when it declares none."""
    found = _CONSTANT.findall(source)
    return found[0] if len(found) == 1 else ""


def _show(git_dir: Path, commit: str) -> str:
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "-C", str(git_dir), "show", f"{commit}:{MODELS_PATH}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout if completed.returncode == 0 else ""


def compare(*, git_dir: Path, previous: str, target: str) -> IndexVersionChange:
    target_version = declared_version(_show(git_dir, target))
    if not target_version:
        raise IndexVersionUnreadable(
            f"{target} does not declare exactly one INDEX_VERSION in {MODELS_PATH}; "
            "refusing to build a release whose search contract is unknown."
        )
    return IndexVersionChange(
        previous=declared_version(_show(git_dir, previous)), target=target_version
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--previous", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--git-dir", type=Path, default=Path())
    parser.add_argument("--env-file", type=Path)
    options = parser.parse_args(argv)

    try:
        change = compare(git_dir=options.git_dir, previous=options.previous, target=options.target)
    except IndexVersionUnreadable as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    for line in change.lines():
        print(line)
    if change.changed:
        print(
            "This release moves the search index contract. After it is deployed, "
            "runbook step 11 is mandatory: rebuild_search_index, then the final "
            "deployment_readiness and check_search_integrity --full."
        )
    if options.env_file:
        with options.env_file.open("a", encoding="utf-8") as env:
            env.write(f"INDEX_VERSION_PREVIOUS={change.previous}\n")
            env.write(f"INDEX_VERSION_TARGET={change.target}\n")
            env.write(f"INDEX_VERSION_CHANGED={'YES' if change.changed else 'NO'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
