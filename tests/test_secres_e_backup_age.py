"""Security and resilience review 2026-10-29, assessor E (workstream 2: DR).

`scripts/deploy/juristid-check-backup-age.sh` is the one tool that can say
whether a Juristid backup set was taken lately. On the production host it is
scheduled by nothing and read by nobody (coordinator's read-only host survey,
2026-10-29: no juristid cron entry besides the Lovable sync; the 15-minute
host watchdog watches the USB snapshot's age, not the sets'). These pin the
exit-code contract an eventual cron entry or monitor would rely on, and record
two edge cases found while reading it:

* **E-07 (P3)** a set whose stamp lies in the future is reported fresh for
  ever. Its age is clamped to zero, so one mis-stamped directory (a host whose
  clock was wrong, a set copied in from a machine with a wrong clock) masks
  every later staleness. The check should refuse or warn on a stamp ahead of
  `now`, not clamp it. Marked xfail(strict) so the marker is removed with the
  fix.
* a set whose stamp cannot be parsed is warned about and not counted; when it
  is the only directory the verdict is "no complete set" (exit 2), which is
  the right exit for a monitor even though the wording is about completeness.

Everything else here is the behaviour a scheduled run depends on: a `.partial`
beside a fresh set is reported and does not change the verdict; a `sets` path
that is a file is "never taken" (exit 2); the three non-zero codes are
distinct and stable.

Runs the real script against a temporary backup root; needs bash and nothing
else, like tests/test_backup_verification_health.py.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from django.conf import settings

ROOT = Path(settings.BASE_DIR)
AGE = ROOT / "scripts" / "deploy" / "juristid-check-backup-age.sh"
BASH = shutil.which("bash") or "bash"

EXIT_FRESH, EXIT_ARGUMENTS, EXIT_NONE, EXIT_STALE = 0, 1, 2, 3


def _age(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - a fixed interpreter and a repository path
        [BASH, str(AGE), *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _stamp(hours_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).strftime("%Y%m%dT%H%M%SZ")


def _complete_set(root: Path, stamp: str) -> Path:
    set_dir = root / "sets" / stamp
    set_dir.mkdir(parents=True)
    for name in ("database.dump", "manifest.json", "SHA256SUMS"):
        (set_dir / name).write_text("x", encoding="utf-8")
    return set_dir


# -- the exit-code contract a monitor would wire to -------------------------


def test_the_four_exit_codes_are_distinct_and_stable(tmp_path: Path) -> None:
    """A cron wrapper maps these to alerts; a change here is a silent monitor."""
    missing_root = _age("--backup-root", str(tmp_path / "nowhere"), "--max-age-hours", "24")
    assert missing_root.returncode == EXIT_ARGUMENTS

    (tmp_path / "sets").mkdir()
    never = _age("--backup-root", str(tmp_path), "--max-age-hours", "24")
    assert never.returncode == EXIT_NONE

    _complete_set(tmp_path, _stamp(30))
    stale = _age("--backup-root", str(tmp_path), "--max-age-hours", "24")
    assert stale.returncode == EXIT_STALE
    assert stale.stdout.startswith("CRITICAL:")

    _complete_set(tmp_path, _stamp(1))
    fresh = _age("--backup-root", str(tmp_path), "--max-age-hours", "24")
    assert fresh.returncode == EXIT_FRESH
    assert fresh.stdout.startswith("OK:")


def test_a_partial_and_an_incomplete_set_beside_a_fresh_one_are_reported_not_counted(
    tmp_path: Path,
) -> None:
    """What an unattended regime accumulates: failed runs leave `.partial`
    directories (juristid-backup.sh never removes another run's work), and the
    check must keep saying so while still answering the freshness question."""
    _complete_set(tmp_path, _stamp(2))
    (tmp_path / "sets" / f"{_stamp(5)}.partial").mkdir()
    (tmp_path / "sets" / f"{_stamp(6)}.partial").mkdir()
    incomplete = tmp_path / "sets" / _stamp(7)
    incomplete.mkdir()
    (incomplete / "database.dump").write_text("x", encoding="utf-8")

    result = _age("--backup-root", str(tmp_path), "--max-age-hours", "24")

    assert result.returncode == EXIT_FRESH
    assert "NOTE: 2 unfinished .partial" in result.stdout
    assert "WARNING: 1 directory/directories named like a set are missing" in result.stdout
    assert "1 complete set(s)" in result.stdout


def test_a_sets_path_that_is_a_file_is_never_taken(tmp_path: Path) -> None:
    (tmp_path / "sets").write_text("not a directory", encoding="utf-8")

    result = _age("--backup-root", str(tmp_path), "--max-age-hours", "24")

    assert result.returncode == EXIT_NONE
    assert "No Juristid backup has ever been taken here" in result.stdout


def test_an_unparsable_stamp_is_warned_about_and_not_counted(tmp_path: Path) -> None:
    """Month 13: matches the glob, fails `date`. Reported, never silently
    treated as the epoch (which would read as "infinitely old")."""
    _complete_set(tmp_path, "20261399T000000Z")

    result = _age("--backup-root", str(tmp_path), "--max-age-hours", "24")

    assert result.returncode == EXIT_NONE
    assert "WARNING: could not read a date from: 20261399T000000Z" in result.stdout


def test_an_unparsable_stamp_does_not_hide_a_real_fresh_set(tmp_path: Path) -> None:
    _complete_set(tmp_path, "20261399T000000Z")
    _complete_set(tmp_path, _stamp(1))

    result = _age("--backup-root", str(tmp_path), "--max-age-hours", "24")

    assert result.returncode == EXIT_FRESH
    assert "1 complete set(s)" in result.stdout


# -- E-07: a stamp in the future ------------------------------------------------


def test_a_set_stamped_in_the_future_is_not_called_fresh(tmp_path: Path) -> None:
    """One directory named `20991231T000000Z` — a wrong host clock, a copied-in
    set — makes the check answer OK for the next 73 years whatever happens to
    the real backups. It should warn and ignore the stamp, or exit non-zero."""
    _complete_set(tmp_path, "20991231T000000Z")
    _complete_set(tmp_path, _stamp(100))

    result = _age("--backup-root", str(tmp_path), "--max-age-hours", "24")

    assert result.returncode != EXIT_FRESH, result.stdout
