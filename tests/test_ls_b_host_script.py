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


def test_on_demand_passes_every_gate_before_it_asks():
    text = _text()
    ask = _at(text, "one_off due --trigger manual --on-demand")
    for gate in (
        "flock -n 9",
        "mkdir /tmp/juristid-host-lock",
        '"$HEALTH" != healthy',
        '-e "$SYNC_DIR/PAUSED"',
        '"0 600"',
    ):
        assert _at(text, gate) < ask, gate


def test_on_demand_runs_only_the_request_runner_after_the_backup():
    text = _text()
    ask = _at(text, "one_off due --trigger manual --on-demand")
    backup = _at(text, 'if [ "$MODE" = apply ]; then')
    request = _at(text, "one_off_manage data_sync_requests run-next --kind lovable")
    nightly = _at(text, 'one_off run --trigger "$TRIGGER"')
    assert ask < backup < request < nightly
    # The request branch exits before the nightly run and the observation.
    assert 'exit "$rc"\nfi\n\nsay "run"' in text
    # Retention is the night's: an on-demand firing skips it.
    assert _at(text, 'if [ "$ON_DEMAND" = 0 ]; then') < _at(text, "PRUNE_ARGS=(prune-observations)")
    # The set taken for an APPLY request reaches the run.
    assert '-e LOVABLE_SYNC_BACKUP_SET="$SET"' in text


def test_a_firing_with_nothing_to_do_leaves_no_log_and_writes_nothing():
    text = _text()
    assert 'if [ "$DUE" = "NOT_DUE NO_REQUEST" ]; then exit 0; fi' in text
    assert 'if [ "$KEEP_LOG" = 0 ]; then rm -f "$LOG"; fi' in text
    assert "--on-demand) TRIGGER=manual; ON_DEMAND=1 ;;" in text


def test_the_script_never_sends_a_write_to_lovable_and_installs_no_cron():
    text = _text()
    for forbidden in ("crontab", "curl ", "wget ", "/boot/config/plugins/dynamix"):
        assert forbidden not in text, forbidden
