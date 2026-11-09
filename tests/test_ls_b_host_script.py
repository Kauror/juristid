"""The host script's on-demand mode (docs/adr/0213), read as text.

The script runs only on the host (it needs Docker and the host's paths), so the
order of its gates is pinned here: an on-demand firing passes every gate the
night passes, asks the database read-only whether a request waits, takes and
verifies a backup only for APPLY, and runs the request runner and nothing else.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from django.conf import settings

SCRIPT = Path(settings.BASE_DIR) / "scripts" / "deploy" / "juristid-lovable-sync.sh"


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _at(text: str, needle: str) -> int:
    position = text.find(needle)
    assert position >= 0, needle
    return position


def test_the_script_parses():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)  # noqa: S603, S607 - a fixed script


LOVABLE_POLL = "plain_manage data_sync_requests poll --kind lovable"
ONENOTE_POLL = "plain_manage data_sync_requests poll --kind onenote"


def test_on_demand_passes_every_gate_before_it_asks():
    text = _text()
    ask = _at(text, LOVABLE_POLL)
    for gate in (
        "flock -n 9",
        'mkdir "$HOST_LOCK"',
        '"$HEALTH" != healthy',
        '-e "$SYNC_DIR/PAUSED"',
        '"0 600"',
    ):
        assert _at(text, gate) < ask, gate
    # LS2-D: the pause file and the credentials become the poll's problem code,
    # so a waiting request is refused by name instead of waiting to expire.
    assert '${LOVABLE_PROBLEM:+--problem "$LOVABLE_PROBLEM"}' in text


def test_on_demand_runs_only_the_request_runner_after_the_backup():
    text = _text()
    ask = _at(text, LOVABLE_POLL)
    backup = _at(text, 'if [ "$MODE" = apply ]; then')
    request = _at(text, "one_off_manage data_sync_requests run-next --kind lovable")
    nightly = _at(text, 'one_off run --trigger "$TRIGGER"')
    assert ask < backup < request < nightly
    # The request branch exits before the night's gates, run and observation.
    assert 'exit "$rc"\nfi\n\nif [ -e "$SYNC_DIR/PAUSED" ]' in text
    # Retention is the night's: an on-demand firing skips it.
    assert _at(text, 'if [ "$ON_DEMAND" = 0 ]; then') < _at(text, "PRUNE_ARGS=(prune-observations)")
    # The set taken for an APPLY request reaches the run.
    assert '-e LOVABLE_SYNC_BACKUP_SET="$SET"' in text


def test_a_firing_with_nothing_to_do_leaves_no_log_and_writes_nothing():
    text = _text()
    assert '"NOT_DUE "*) exit "$orc_onenote" ;;' in text
    assert 'if [ "$KEEP_LOG" = 0 ]; then rm -f "$LOG"; fi' in text
    assert "--on-demand) TRIGGER=manual; ON_DEMAND=1 ;;" in text


def test_the_script_never_sends_a_write_to_lovable_and_installs_no_cron():
    text = _text()
    for forbidden in ("crontab", "curl ", "wget ", "/boot/config/plugins/dynamix"):
        assert forbidden not in text, forbidden


def _onenote_container(text: str) -> str:
    start = _at(text, 'docker run --rm --name "juristid-main-onenote-import-$$"')
    return text[start : text.index("</dev/null", start)]


def test_the_onenote_lane_imports_with_a_verified_set_and_no_lovable_credentials():
    text = _text()
    ask = _at(text, ONENOTE_POLL)
    for gate in ("flock -n 9", 'mkdir "$HOST_LOCK"', '"$HEALTH" != healthy'):
        assert _at(text, gate) < ask, gate
    run = _at(text, 'docker run --rm --name "juristid-main-onenote-import-$$"')
    assert ask < _at(text[ask:], "take_backup") + ask < run
    container = _onenote_container(text)
    for needed in (
        "-e JURISTID_ONENOTE_CAPTURE_IMPORT=1",
        "-e REAL_DATA_ALLOWED=1",
        "-e JURISTID_ONENOTE_CAPTURE_ROOT=/capture",
        '-e JURISTID_ONENOTE_BACKUP_SET="$BACKUP_SET"',
        '-v "$CAPTURE_SOURCE:/capture:ro"',
        "data_sync_requests run-next --kind onenote",
    ):
        assert needed in container, needed
    assert "CREDENTIALS" not in container and "LOVABLE_SYNC" not in container
    assert "CAPTURE_SOURCE=${JURISTID_SYNC_CAPTURE_SOURCE:-/mnt/user/juristid-main/source}" in text
    # The OneNote lane comes before the Lovable lane's own gates and ask.
    assert run < _at(text, LOVABLE_POLL)
