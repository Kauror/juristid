"""A set names what the dump describes, and a failed restore keeps nothing.

ENG-114: Kustuta teema removes a Teema's objects right after its rows'
deletion commits. Landing inside `pg_dump`, that left the rows in the dump and
the objects out of a membership read only after it — a set whose restore
brought the Teema back without its bytes. Membership is now the listing taken
as the dump begins (those objects copied into the pool before it starts), less
what vanished before it began, plus the listing taken as it ends.

ENG-115: `pg_restore` ran without `--single-transaction`, so a failure part-way
left a schema and part of the rows committed, and the failure message spoke
only of the database while the rerun was refused over the evidence already
copied.

The race itself, against a real `pg_dump` and the real `delete_matter()`, is in
the CI recovery rehearsal (.github/workflows/ci.yml, "A Teema deleted while the
dump runs is restored with its bytes"): it needs Docker and rsync. What can run
anywhere runs here — the two list operations the membership is built from, and
the order of the script's steps.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from django.conf import settings

ROOT = Path(settings.BASE_DIR)
SCRIPTS = ROOT / "scripts" / "deploy"
LIB = SCRIPTS / "lib.sh"
BACKUP = SCRIPTS / "juristid-backup.sh"
RESTORE = SCRIPTS / "juristid-restore.sh"

#: Not skipped when absent, for the reason tests/test_deployment_scripts.py gives.
BASH = shutil.which("bash") or "bash"


def _lib(script: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - a fixed interpreter and a repository path
        [BASH, "-c", f'set -euo pipefail; . "{LIB.as_posix()}"; {script}'],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def _inventory(path: Path, *entries: str) -> None:
    path.write_bytes(b"".join(entry.encode() + b"\0" for entry in entries))


def _entries(path: Path) -> list[str]:
    return [entry for entry in path.read_bytes().decode().split("\0") if entry]


# -- the list operations ------------------------------------------------------


def test_only_listed_objects_the_pool_holds_are_kept(tmp_path: Path) -> None:
    """An object listed as the dump began and not in the pool vanished before
    the dump started — after its rows' deletion committed — so it is not a
    member. Everything the pool holds from that listing is."""
    pool = tmp_path / "pool"
    (pool / "aa").mkdir(parents=True)
    (pool / "aa" / "kept.bin").write_bytes(b"k")
    (pool / "aa" / "name with space.bin").write_bytes(b"s")
    _inventory(tmp_path / "before", "aa/gone.bin", "aa/kept.bin", "aa/name with space.bin")

    result = _lib("inventory_present_in pool before pinned", tmp_path)

    assert result.returncode == 0, result.stderr
    assert _entries(tmp_path / "pinned") == ["aa/kept.bin", "aa/name with space.bin"]


def test_an_empty_listing_keeps_nothing_and_writes_an_empty_inventory(tmp_path: Path) -> None:
    (tmp_path / "pool").mkdir()
    _inventory(tmp_path / "before")

    result = _lib("inventory_present_in pool before pinned", tmp_path)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "pinned").read_bytes() == b""


def test_the_union_names_each_object_once_in_capture_order(tmp_path: Path) -> None:
    """The case ENG-114 is about: an object in the beginning listing only (its
    Teema deleted during the dump) stays a member beside everything the ending
    listing names — once each, sorted as `capture_inventory` sorts, so the
    inventory checksums the same however it was assembled."""
    _inventory(tmp_path / "pinned", "aa/deleted-during-dump.bin", "bb/both.bin")
    _inventory(tmp_path / "after", "bb/both.bin", "cc/new-after-dump.bin")

    result = _lib("inventory_union pinned after members", tmp_path)

    assert result.returncode == 0, result.stderr
    assert _entries(tmp_path / "members") == [
        "aa/deleted-during-dump.bin",
        "bb/both.bin",
        "cc/new-after-dump.bin",
    ]
    counted = _lib("inventory_count members", tmp_path)
    assert counted.stdout.strip() == "3"
    duplicates = _lib("inventory_duplicate_count members", tmp_path)
    assert duplicates.stdout.strip() == "0"


def test_an_unreadable_listing_stops_rather_than_counting_as_empty(tmp_path: Path) -> None:
    _inventory(tmp_path / "after")

    result = _lib("inventory_union missing after members", tmp_path)

    assert result.returncode != 0
    assert "cannot be read" in result.stderr


# -- the order the backup runs in ------------------------------------------


def test_the_membership_is_read_at_both_ends_of_the_dump() -> None:
    """Order is the whole fix, so it is read from the script.

    The beginning listing and its copy into the pool come before `pg_dump`;
    the ending listing after it and before the second pass; the union, the
    in-pool check and the byte totals after that pass, when every member has
    had its chance to reach the pool.
    """
    text = BACKUP.read_text(encoding="utf-8")
    first_pass = text.index('step "Evidence and page XML, first pass"')
    pinned = text.index('pin_listing "evidence" "$EVIDENCE_SOURCE"')
    dump = text.index("juristid_compose exec -T db pg_dump")
    after = text.index('capture_inventory "$EVIDENCE_SOURCE" "$SCRATCH/evidence.after"')
    second_pass = text.index('step "Evidence and page XML, second pass"')
    kept = text.index('inventory_present_in "$EVIDENCE_MIRROR" "$SCRATCH/evidence.before"')
    union = text.index('inventory_union "$SCRATCH/evidence.pinned" "$SCRATCH/evidence.after"')
    in_pool = text.index(
        'inventory_first_missing "$EVIDENCE_MIRROR" "$PARTIAL_DIR/$EVIDENCE_INVENTORY"'
    )
    measured = text.index('inventory_selected_bytes "$EVIDENCE_MIRROR"')

    assert first_pass < pinned < dump < after < second_pass < kept < union < in_pool < measured


def test_the_beginning_copy_tolerates_only_a_vanished_file() -> None:
    """rsync's exit 24 means a listed file vanished, which here is an object
    removed before the dump began. Every other failure still stops the backup."""
    text = BACKUP.read_text(encoding="utf-8")
    body = text[text.index("pin_listing() {") : text.index('pin_listing "evidence"')]

    assert '--from0 --files-from="$listing"' in body
    assert "--delete" not in body
    assert "24)" in body
    assert "*) die" in body


def test_the_append_only_premise_is_no_longer_claimed_for_the_source_tree() -> None:
    text = BACKUP.read_text(encoding="utf-8")

    assert "That argument depends on evidence being append-only. It is" not in text
    assert "docs/adr/0096" in text


# -- the restore is one transaction ------------------------------------------


def test_the_database_is_restored_in_one_transaction() -> None:
    text = RESTORE.read_text(encoding="utf-8")
    command = text[text.index("juristid_compose exec -T db pg_restore") :]
    command = command[: command.index('"$CONTAINER_PATH"')]

    assert "--single-transaction" in command
    assert "--exit-on-error" in command


def test_a_failed_restore_names_the_storage_it_already_filled() -> None:
    """The rerun is refused over non-empty storage, so the first message has to
    say so — not leave the operator to meet that refusal second."""
    text = RESTORE.read_text(encoding="utf-8")
    body = text[
        text.index("restore_failed() {") : text.index("juristid_compose exec -T db pg_restore")
    ]

    assert "$DATA_ROOT/evidence" in body
    assert "$DATA_ROOT/legacy-source" in body
    assert "move those two trees aside" in body
    assert "drop it, recreate it" not in text


@pytest.mark.parametrize("claim", ["Restores nothing partially."])
def test_the_usage_no_longer_promises_what_the_restore_did_not_do(claim: str) -> None:
    assert claim not in RESTORE.read_text(encoding="utf-8")
