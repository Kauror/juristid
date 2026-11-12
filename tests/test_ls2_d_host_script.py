"""`scripts/deploy/juristid-lovable-sync.sh`, run for real against a synthetic host (LS2-D).

`tests/test_ls_b_host_script.py` pins the order of the script's gates as text.
These run the script itself, scheduled and on demand, independently, with a
fake `docker` (and `flock`, `stat`) on PATH that records every container the
script starts and answers the database's questions from small text files. The
host paths come from the script's test seams (`JURISTID_SYNC_APPDATA`,
`JURISTID_SYNC_CAPTURE_SOURCE`, `JURISTID_SYNC_LOCK`, `JURISTID_HOST_LOCK`);
cron sets none of them. Nothing reaches Docker, Lovable or a database.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from django.conf import settings

ROOT = Path(settings.BASE_DIR)
SCRIPT = ROOT / "scripts" / "deploy" / "juristid-lovable-sync.sh"
BASH = shutil.which("bash") or "bash"
SECRET = "sekreet-mitte-logisse-42"  # noqa: S105 - a marker, not a password

FAKE_DOCKER = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FAKE_DIR/calls"
case "$1" in
  inspect)
    if [[ "$*" == *Health* ]]; then cat "$FAKE_DIR/health"; exit 0; fi
    echo "sha256:0123456789abcdef0123"; exit 0 ;;
  run) ;;
  *) exit 97 ;;
esac
args=("$@"); cmd=""
for ((i = 0; i < ${#args[@]}; i++)); do
  if [ "${args[$i]}" = manage.py ]; then cmd="${args[*]:$((i + 1))}"; break; fi
done
case "$cmd" in
  "data_sync_requests poll --kind onenote"*) cat "$FAKE_DIR/poll-onenote" ;;
  "data_sync_requests poll --kind lovable"*) cat "$FAKE_DIR/poll-lovable" ;;
  "data_sync_requests run-next"*) echo '{"status": "DONE"}' ;;
  "lovable_sync prune-observations"*) exit 0 ;;
  "lovable_sync due"*) cat "$FAKE_DIR/due" ;;
  "lovable_sync run"*) exit 0 ;;
  "lovable_sync observe"*) exit 2 ;;
  *) exit 98 ;;
esac
"""
FAKE_FLOCK = "#!/usr/bin/env bash\nexit 0\n"
# The credentials file is root-owned, mode 600, unless the test says otherwise.
FAKE_STAT = (
    "#!/usr/bin/env bash\n"
    'if [ -e "$FAKE_DIR/cred-ok" ]; then echo "0 600"; else echo "1000 644"; fi\n'
)
# As the real one (scripts/deploy/lib.sh `take_host_lock`): it takes the host lock
# JURISTID_HOST_LOCK names and refuses while another operation holds it. The
# synchronisation holds the host lock itself, so a backup it asked for under the
# same name was refused on the host (2026-11-10).
FAKE_BACKUP = (
    "#!/usr/bin/env bash\n"
    'lock="${JURISTID_HOST_LOCK:-/tmp/juristid-host-lock}"\n'
    'mkdir "$lock" 2>/dev/null || { echo "ERROR: another operation holds $lock"; exit 1; }\n'
    'root=""; while [ $# -gt 0 ]; do [ "$1" = --backup-root ] && root="$2"; shift; done\n'
    'mkdir -p "$root/sets/20261109T000000Z"\n'
    'echo "Backup set: $root/sets/20261109T000000Z"\n'
    'rm -rf -- "$lock"\n'
)
# As the real one with --record (DR-05): the proof is written into the set only
# when every check passed; `verify-no-proof` makes it pass without writing one.
FAKE_VERIFY = (
    "#!/usr/bin/env bash\n"
    'set=""; record=0; while [ $# -gt 0 ]; do\n'
    '  case "$1" in --set) set="$2" ;; --record) record=1 ;; esac; shift; done\n'
    'if [ "$record" = 1 ] && [ ! -e "$FAKE_DIR/verify-no-proof" ]; then\n'
    '  printf "set=%s\\nlevel=2\\n" "$(basename "$set")" > "$set/VERIFIED-L2"; fi\n'
    "exit 0\n"
)


def _executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def host(tmp_path: Path) -> dict[str, Path]:
    appdata = tmp_path / "appdata"
    (appdata / "config").mkdir(parents=True)
    (appdata / "config" / "juristid.env").write_text("DJANGO_SECRET_KEY=x\n", encoding="utf-8")
    (appdata / "config" / "lovable-sync.env").write_text(
        f"LOVABLE_SYNC_PASSWORD={SECRET}\n", encoding="utf-8"
    )
    deploy = appdata / "repo" / "scripts" / "deploy"
    _executable(deploy / "juristid-backup.sh", FAKE_BACKUP)
    _executable(deploy / "juristid-verify-backup.sh", FAKE_VERIFY)
    capture = tmp_path / "capture"
    capture.mkdir()
    bin_dir = tmp_path / "bin"
    _executable(bin_dir / "docker", FAKE_DOCKER)
    _executable(bin_dir / "flock", FAKE_FLOCK)
    _executable(bin_dir / "stat", FAKE_STAT)
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "health").write_text("healthy\n")
    (fake / "poll-onenote").write_text("NOT_DUE NO_REQUEST\n")
    (fake / "poll-lovable").write_text("NOT_DUE NO_REQUEST\n")
    (fake / "due").write_text("NOT_DUE NOT_MIDNIGHT_IN_TALLINN\n")
    (fake / "cred-ok").write_text("")
    return {
        "tmp": tmp_path,
        "appdata": appdata,
        "bin": bin_dir,
        "fake": fake,
        "capture": capture,
        "logs": appdata / "l100" / "sync" / "logs",
        "lock": tmp_path / "host-lock",
    }


def run(host: dict[str, Path], *arguments: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PATH"] = f"{host['bin']}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_DIR"] = str(host["fake"])
    env["JURISTID_SYNC_APPDATA"] = str(host["appdata"])
    env["JURISTID_SYNC_BACKUP_ROOT"] = str(host["tmp"] / "backups")
    env["JURISTID_SYNC_CAPTURE_SOURCE"] = str(host["capture"])
    env["JURISTID_SYNC_LOCK"] = str(host["tmp"] / "sync.lock")
    env["JURISTID_HOST_LOCK"] = str(host["lock"])
    return subprocess.run(  # noqa: S603 - a fixed interpreter and a repository script
        [BASH, str(SCRIPT), *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def calls(host: dict[str, Path]) -> list[str]:
    path = host["fake"] / "calls"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def containers(host: dict[str, Path]) -> list[str]:
    """The `manage.py …` part of every container the script started."""
    return [line.split(" manage.py ", 1)[1] for line in calls(host) if " manage.py " in line]


def logs(host: dict[str, Path]) -> str:
    if not host["logs"].exists():
        return ""
    return "\n".join(
        p.read_text(encoding="utf-8", errors="replace") for p in sorted(host["logs"].iterdir())
    )


def _with_credentials(line: str) -> bool:
    return "lovable-sync.env" in line


# -- on demand ----------------------------------------------------------------------


def test_on_demand_with_nothing_waiting_asks_twice_and_leaves_no_trace(host):
    result = run(host, "--on-demand")
    assert result.returncode == 0, result.stdout + result.stderr
    assert containers(host) == [
        "data_sync_requests poll --kind onenote",
        "data_sync_requests poll --kind lovable",
    ]
    # Asking needs no credentials: neither poll's container receives them.
    assert not any(_with_credentials(c) for c in calls(host))
    assert logs(host) == "", "a firing with nothing to do keeps no log"
    assert not host["lock"].exists(), "the release lock is released"


def test_on_demand_runs_only_the_request_runner_in_compare(host):
    (host["fake"] / "poll-lovable").write_text("DUE COMPARE\n")
    result = run(host, "--on-demand")
    assert result.returncode == 0, result.stdout + result.stderr
    assert containers(host) == [
        "data_sync_requests poll --kind onenote",
        "data_sync_requests poll --kind lovable",
        "data_sync_requests run-next --kind lovable",
    ]
    runner = next(c for c in calls(host) if "run-next --kind lovable" in c)
    assert _with_credentials(runner) and "-e JURISTID_LOVABLE_SYNC=1" in runner
    assert "LOVABLE_SYNC_BACKUP_SET= " in runner, "COMPARE takes no backup"
    text = logs(host)
    assert "DUE COMPARE" in text and "END rc=0" in text and "backup" not in text
    # Never the night's work: no retention, no scheduled run, no observation.
    assert not any("prune-observations" in c or "lovable_sync" in c for c in containers(host))


def test_on_demand_in_apply_takes_and_hands_over_a_verified_backup(host):
    (host["fake"] / "poll-lovable").write_text("DUE APPLY\n")
    result = run(host, "--on-demand")
    assert result.returncode == 0, result.stdout + result.stderr
    runner = next(c for c in calls(host) if "run-next --kind lovable" in c)
    assert "LOVABLE_SYNC_BACKUP_SET=sets/20261109T000000Z" in runner
    assert "verified (level 2)" in logs(host)
    # DR-05: the set travels with its proof, read-only.
    set_dir = host["tmp"] / "backups" / "sets" / "20261109T000000Z"
    assert "20261109T000000Z:/backup-set:ro -e LOVABLE_SYNC_BACKUP_DIR=/backup-set" in runner
    assert (set_dir / "VERIFIED-L2").exists()


@pytest.mark.parametrize("arguments", [(), ("--on-demand",)])
def test_a_backup_without_its_proof_hands_nothing_to_an_apply_run(host, arguments):
    """Regression: DR-05 — a verification that left no proof is no verification:
    the run gets no set and no mount, and so writes nothing (REVIEW_ONLY)."""
    (host["fake"] / "verify-no-proof").write_text("")
    (host["fake"] / "poll-lovable").write_text("DUE APPLY\n")
    (host["fake"] / "due").write_text("DUE APPLY\n")
    result = run(host, *arguments)
    assert result.returncode == 0, result.stdout + result.stderr
    engine = next(c for c in calls(host) if "run-next --kind lovable" in c or "sync run" in c)
    assert "LOVABLE_SYNC_BACKUP_SET= " in engine and "/backup-set" not in engine
    assert "--backup-set" not in engine
    assert "nothing is written" in logs(host)


def test_a_backup_the_host_lock_kept_out_writes_nothing(host):
    """Regression: DR-05 — 10.11: another job held the lock the backup takes, so
    no set was taken; the APPLY night gets no set and writes nothing, never an
    older set."""
    (host["tmp"] / "host-lock.sync-backup").mkdir()
    (host["fake"] / "due").write_text("DUE APPLY\n")
    result = run(host)
    assert result.returncode == 0, result.stdout + result.stderr
    engine = next(c for c in calls(host) if "lovable_sync run" in c)
    assert "--backup-set" not in engine and "/backup-set" not in engine
    assert "WARN backup failed: nothing is written" in logs(host)


def test_the_scheduled_apply_night_runs_with_the_set_and_its_proof(host):
    (host["fake"] / "due").write_text("DUE APPLY\n")
    result = run(host)
    assert result.returncode == 0, result.stdout + result.stderr
    engine = next(c for c in calls(host) if "lovable_sync run" in c)
    assert "--backup-set sets/20261109T000000Z" in engine
    assert "sets/20261109T000000Z:/backup-set:ro" in engine


def _pause(host: dict[str, Path]) -> None:
    sync = host["appdata"] / "l100" / "sync"
    sync.mkdir(parents=True, exist_ok=True)
    (sync / "PAUSED").write_text("")


@pytest.mark.parametrize(
    ("arrange", "problem"),
    [
        (_pause, "HOST_PAUSED"),
        (lambda h: (h["appdata"] / "config" / "lovable-sync.env").unlink(), "NO_CREDENTIALS"),
        (lambda h: (h["fake"] / "cred-ok").unlink(), "CREDENTIALS_MODE"),
    ],
)
def test_a_host_gate_becomes_the_polls_refusal_and_nothing_runs(host, arrange, problem):
    arrange(host)
    (host["fake"] / "poll-lovable").write_text(f"REFUSED {problem}\n")
    result = run(host, "--on-demand")
    assert result.returncode == 1
    assert containers(host)[-1] == f"data_sync_requests poll --kind lovable --problem {problem}"
    assert not any("run-next" in c for c in containers(host))
    assert f"lovable request REFUSED {problem} (host)" in logs(host)


def test_a_host_gate_with_nothing_waiting_stays_quiet(host):
    (host["fake"] / "cred-ok").unlink()
    result = run(host, "--on-demand")
    assert result.returncode == 0
    assert "--problem CREDENTIALS_MODE" in containers(host)[-1]
    assert logs(host) == ""


def test_the_onenote_lane_imports_read_only_and_never_sees_the_credentials(host):
    (host["fake"] / "poll-onenote").write_text("DUE ONENOTE\n")
    result = run(host, "--on-demand")
    assert result.returncode == 0, result.stdout + result.stderr
    importer = next(c for c in calls(host) if "run-next --kind onenote" in c)
    assert f"-v {host['capture']}:/capture:ro" in importer
    # The import writes Documents and page XML: the container must see the same
    # storage the web serves (compose.yml), or the bytes die with the container
    # while the rows survive (production, OneNote batch 1, 2026-11-10).
    appdata = host["appdata"]
    for store in ("evidence", "legacy-source", "derivatives"):
        assert f"-v {appdata}/{store}:/app/{store} " in importer, store
    assert "JURISTID_ONENOTE_BACKUP_SET=sets/20261109T000000Z" in importer
    assert not _with_credentials(importer) and "JURISTID_LOVABLE_SYNC" not in importer
    assert containers(host)[-1] == "data_sync_requests poll --kind lovable"


def test_a_missing_capture_corpus_refuses_the_onenote_request(host):
    host["capture"].rmdir()
    (host["fake"] / "poll-onenote").write_text("REFUSED NO_CAPTURE_SOURCE\n")
    result = run(host, "--on-demand")
    assert result.returncode == 1
    assert containers(host)[0] == (
        "data_sync_requests poll --kind onenote --problem NO_CAPTURE_SOURCE"
    )
    assert not any("run-next" in c for c in containers(host))


def test_an_unanswered_poll_fails_loudly(host):
    (host["fake"] / "poll-lovable").write_text("")
    result = run(host, "--on-demand")
    assert result.returncode == 1
    assert "FAIL the Lovable poll gave no answer" in logs(host)


# -- the night (scheduled) ----------------------------------------------------------


def test_the_scheduled_firing_that_is_not_due_runs_retention_only(host):
    result = run(host)
    assert result.returncode == 0, result.stdout + result.stderr
    assert containers(host) == [
        "lovable_sync prune-observations",
        "lovable_sync due --trigger scheduled",
    ]
    assert not any("data_sync_requests" in c for c in containers(host))


def test_the_scheduled_night_runs_the_engine_and_the_observation(host):
    (host["fake"] / "due").write_text("DUE COMPARE\n")
    result = run(host)
    assert result.returncode == 0, result.stdout + result.stderr
    assert containers(host) == [
        "lovable_sync prune-observations",
        "lovable_sync due --trigger scheduled",
        "lovable_sync run --trigger scheduled",
        "lovable_sync observe",
    ]
    assert not any("data_sync_requests" in c for c in containers(host))


def test_a_paused_host_skips_the_night_before_any_credentials(host):
    _pause(host)
    result = run(host)
    assert result.returncode == 0
    assert containers(host) == ["lovable_sync prune-observations"]
    assert "SKIP paused on the host" in logs(host)


# -- both -------------------------------------------------------------------------------


@pytest.mark.parametrize("arguments", [(), ("--on-demand",)])
def test_a_held_release_lock_starts_nothing_and_is_not_released(host, arguments):
    host["lock"].mkdir()
    (host["lock"] / "owner").write_text("owner=release pid=1\n")
    result = run(host, *arguments)
    assert result.returncode == 0
    assert calls(host) == []
    assert host["lock"].is_dir()


@pytest.mark.parametrize("arguments", [(), ("--on-demand",)])
def test_an_unhealthy_web_stops_both_modes(host, arguments):
    (host["fake"] / "health").write_text("unhealthy\n")
    result = run(host, *arguments)
    assert result.returncode == 1
    assert containers(host) == []
    assert "FAIL web is not healthy (unhealthy)" in logs(host)


@pytest.mark.parametrize(
    ("arguments", "poll", "due"),
    [
        ((), "NOT_DUE NO_REQUEST", "DUE APPLY"),
        (("--on-demand",), "DUE APPLY", "NOT_DUE NO_REQUEST"),
    ],
)
def test_the_credentials_never_reach_a_log_or_a_command_line(host, arguments, poll, due):
    (host["fake"] / "poll-lovable").write_text(f"{poll}\n")
    (host["fake"] / "due").write_text(f"{due}\n")
    result = run(host, *arguments)
    assert result.returncode == 0, result.stdout + result.stderr
    everything = "\n".join([result.stdout, result.stderr, logs(host), *calls(host)])
    assert SECRET not in everything
    for leftover in host["logs"].glob("*"):
        assert SECRET not in leftover.read_text(encoding="utf-8", errors="replace")


def test_an_unknown_argument_is_refused():
    result = subprocess.run(  # noqa: S603 - a fixed interpreter and a repository script
        [BASH, str(SCRIPT), "--apply"], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 64 and "usage" in result.stderr
