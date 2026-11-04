"""`scripts/maintenance/juristid-git-inventory.sh` on a synthetic clone.

The November 2026 housekeeping round found 157 remote branches, 557 local
branches and 192 worktrees, and classified them by hand. This script makes that
classification repeatable. What matters is that SAFE is narrow: a branch with
commits not in main, a tagged or archive branch, a branch in use by a worktree,
a dirty worktree or one holding unpushed commits must never come out SAFE —
and the script must not change the repository while it looks.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from django.conf import settings

ROOT = Path(settings.BASE_DIR)
SCRIPT = ROOT / "scripts" / "maintenance" / "juristid-git-inventory.sh"
BASH = shutil.which("bash") or "bash"
GIT = shutil.which("git") or "git"

ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def git(cwd: Path, *arguments: str) -> str:
    result = subprocess.run(  # noqa: S603 - a fixed executable
        [GIT, "-c", "init.defaultBranch=main", "-c", "core.autocrlf=false", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=ENV,
        check=True,
    )
    return result.stdout.strip()


def commit(cwd: Path, name: str) -> str:
    (cwd / name).write_text(name)
    git(cwd, "add", name)
    git(cwd, "commit", "-q", "-m", name)
    return git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", str(origin))
    work = tmp_path / "work"
    git(tmp_path, "clone", "-q", str(origin), str(work))
    commit(work, "base")
    git(work, "push", "-q", "origin", "HEAD:main")
    # merged: branched, then main fast-forwarded over it
    git(work, "switch", "-q", "-c", "merged")
    commit(work, "merged-change")
    git(work, "push", "-q", "origin", "merged")
    git(work, "switch", "-q", "main")
    git(work, "merge", "-q", "--ff-only", "merged")
    git(work, "push", "-q", "origin", "main")
    # unmerged, archive, tagged
    for name in ("unmerged", "archive/old"):
        git(work, "switch", "-q", "-c", name, "main")
        commit(work, f"{name.replace('/', '-')}-change")
        git(work, "push", "-q", "origin", name)
    git(work, "switch", "-q", "-c", "tagged", "main~1")
    git(work, "tag", "keep-me")
    git(work, "push", "-q", "origin", "tagged")
    git(work, "switch", "-q", "main")
    git(work, "fetch", "-q", "origin")
    # worktrees: clean+merged, dirty, unpushed commit, in-use branch
    git(work, "worktree", "add", "-q", str(tmp_path / "wt-clean"), "origin/merged")
    git(work, "worktree", "add", "-q", str(tmp_path / "wt-dirty"), "origin/main")
    (tmp_path / "wt-dirty" / "scratch.txt").write_text("untracked")
    git(work, "worktree", "add", "-q", "-b", "local-only", str(tmp_path / "wt-unpushed"), "main")
    commit(tmp_path / "wt-unpushed", "unpushed")
    git(work, "branch", "-q", "inuse-local", "main")
    git(work, "push", "-q", "origin", "inuse-local")
    git(work, "fetch", "-q", "origin")
    git(work, "worktree", "add", "-q", str(tmp_path / "wt-inuse"), "inuse-local")
    return work


def inventory(clone: Path) -> dict[tuple[str, str], str]:
    result = subprocess.run(  # noqa: S603 - a fixed interpreter and a repository path
        [BASH, str(SCRIPT), "--repo", str(clone)],
        capture_output=True,
        text=True,
        timeout=60,
        env=ENV,
    )
    assert result.returncode == 0, result.stderr
    rows: dict[tuple[str, str], str] = {}
    for line in result.stdout.splitlines()[1:]:
        kind, klass, name = line.split("\t")[:3]
        rows[(kind, Path(name).name if kind == "worktree" else name)] = klass
    return rows


def test_classification_is_narrow(clone: Path) -> None:
    rows = inventory(clone)
    assert rows[("remote-branch", "merged")] == "SAFE"
    assert rows[("remote-branch", "main")] == "KEEP"
    assert rows[("remote-branch", "unmerged")] == "REVIEW"
    assert rows[("remote-branch", "archive/old")] == "KEEP"
    assert rows[("remote-branch", "tagged")] == "KEEP"
    assert rows[("remote-branch", "inuse-local")] == "REVIEW"  # checked out in a worktree
    assert rows[("local-branch", "local-only")] == "KEEP"  # checked out
    assert rows[("local-branch", "unmerged")] == "REVIEW"
    assert rows[("worktree", "wt-clean")] == "SAFE"
    assert rows[("worktree", "wt-dirty")] == "REVIEW"
    assert rows[("worktree", "wt-unpushed")] == "REVIEW"
    assert rows[("worktree", "work")] == "KEEP"


def test_inventory_changes_nothing(clone: Path) -> None:
    def state() -> tuple[str, str, str]:
        return (
            git(clone, "for-each-ref"),
            git(clone, "worktree", "list", "--porcelain"),
            git(clone.parent / "wt-dirty", "status", "--porcelain"),
        )

    before = state()
    inventory(clone)
    assert state() == before
