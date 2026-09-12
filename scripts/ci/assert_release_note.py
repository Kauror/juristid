"""Does this release carry its release note?

Run by `.github/workflows/release-image.yml` after the requested commit is
checked out and before anything is built. The workflow is handed two commits —
the one being released and the one production currently runs — and lists the
paths that changed between them; this decides whether that payload needed an
entry in `docs/release-notes/uuendused.toml`, and whether it has one.

    assert_release_note.py --changed-files changed.txt [--waiver "why"] [--env-file "$GITHUB_ENV"]

**A release-time rule, deliberately not a per-PR one.** `docs/release-notes/
README.md` says there is no CI rule that a pull request must edit the notes,
and gives the reason: blocking a one-line internal fix on a sentence nobody
would read is how the file fills with noise. That rule stands. What this adds
is the other half. A release is the moment the sum of those pull requests
reaches a lawyer, and it is the one moment every change passes through, so it
is where «does /uuendused/ describe what they will meet» can be asked without
asking it of every branch.

Three answers, and a fourth for the operator:

* ``note-present`` — the notes file is in the payload. Nothing more is
  checked here; whether the entry parses and names a real day is the
  application's own concern (`app/core/release_notes.py`,
  `tests/test_release_notes.py`).
* ``internal-only`` — nothing in the payload is a path a lawyer can meet in
  the running application: tests, browser tests, documentation, workflows,
  scripts, deployment files. No note is owed. A rebuild of the revision
  production already runs is this case with an empty payload.
* ``missing`` — the payload touches the application and the notes did not
  change. The build stops and says which paths made it a release somebody
  will notice.
* ``waived`` — the same payload, released anyway because the operator said
  why. The reason is written into the release manifest, beside the digest,
  so a release that skipped its note says so in the artifact that outlives
  the run.

No git of its own: it reads a list of paths. The workflow produces that list
and proves the two commits are related before calling this, and the decision
is a pure function of the list so `tests/test_release_note_gate.py` can hold
every case without a repository.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

#: The one file whose change is the release note. Its README beside it is
#: maintainer guidance and counts for nothing here.
RELEASE_NOTES = "docs/release-notes/uuendused.toml"

#: Where a change reaches the running application. `config/` is on the list on
#: purpose: a settings change can turn a feature on for every reader, and the
#: cost of being wrong in that direction is a waiver, not a missed note.
USER_FACING_PREFIXES: tuple[str, ...] = ("app/", "templates/", "static/", "config/")

#: Files at the repository root that ship inside the image and can change what
#: it does. Everything else at the root — README, AGENTS.md, the lock file, the
#: compose file used only for development — is not a change a lawyer meets.
USER_FACING_ROOT_FILES: frozenset[str] = frozenset({"manage.py"})


@dataclass(frozen=True)
class Verdict:
    ok: bool
    #: ``note-present`` | ``internal-only`` | ``waived`` | ``missing``
    kind: str
    detail: str


def is_user_facing(path: str) -> bool:
    """Can a lawyer meet this change in the running application?"""
    normalised = path.replace("\\", "/").lstrip("./")
    if normalised in USER_FACING_ROOT_FILES:
        return True
    return normalised.startswith(USER_FACING_PREFIXES)


def decide(changed: Iterable[str], *, waiver: str = "") -> Verdict:
    """The whole rule, over the list of paths a release changes."""
    paths = sorted({line.strip().replace("\\", "/") for line in changed if line.strip()})
    if RELEASE_NOTES in paths:
        return Verdict(True, "note-present", f"{RELEASE_NOTES} is in the payload")

    facing = [path for path in paths if is_user_facing(path)]
    if not facing:
        return Verdict(
            True,
            "internal-only",
            "no path a reader can meet changed; no release note is owed",
        )

    reason = waiver.strip()
    if reason:
        return Verdict(True, "waived", reason)

    shown = facing[:20]
    more = f" … and {len(facing) - len(shown)} more" if len(facing) > len(shown) else ""
    return Verdict(
        False,
        "missing",
        f"{len(facing)} user-facing path(s) changed and {RELEASE_NOTES} did not: "
        + ", ".join(shown)
        + more,
    )


def _read_paths(source: str) -> list[str]:
    if source == "-":
        return sys.stdin.read().splitlines()
    return Path(source).read_text(encoding="utf-8").splitlines()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--changed-files",
        required=True,
        help="a file with one changed path per line (git diff --name-only), or - for stdin",
    )
    parser.add_argument(
        "--waiver",
        default="",
        help="release without a note anyway, for this stated reason (recorded in the manifest)",
    )
    parser.add_argument(
        "--env-file",
        default="",
        help="append RELEASE_NOTE=<kind> and RELEASE_NOTE_DETAIL=<detail> here ($GITHUB_ENV)",
    )
    args = parser.parse_args(argv)

    verdict = decide(_read_paths(args.changed_files), waiver=args.waiver)

    print(f"release note: {verdict.kind} — {verdict.detail}")
    if args.env_file:
        with Path(args.env_file).open("a", encoding="utf-8") as handle:
            handle.write(f"RELEASE_NOTE={verdict.kind}\n")
            handle.write(f"RELEASE_NOTE_DETAIL={verdict.detail.replace(chr(10), ' ')}\n")
    if not verdict.ok:
        print(
            "Add a [[day]] entry to docs/release-notes/uuendused.toml for the day this "
            "reaches production (docs/release-notes/README.md), or re-run with "
            "release_note_waiver set to the reason no note is owed.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
