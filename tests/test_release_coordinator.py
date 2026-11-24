"""`tools.release_coordinator`: the HR-05 coordinator in SHADOW mode (offline prototype).

No database, docker, ssh or network: it reads pinned files and reports. Synthetic inputs
shaped like a real kit's release.env and release manifest (made-up shas and ids).
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools import release_coordinator
from tools.release_coordinator import EXIT_NOT_READY, main, read_env, read_manifest, shadow
from tools.release_migrations import Expected, Migration, parse_plan

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "release_migrations"
R91 = (FIXTURES / "r91-plan.txt").read_text(encoding="utf-8")
R89 = (FIXTURES / "r89-plan.txt").read_text(encoding="utf-8")
TARGET, PREVIOUS = "b" * 40, "a" * 40
IMAGE = "sha256:" + "c" * 64
ARCHIVE_BYTES = b"not really an image archive\n"
ARCHIVE_SHA = hashlib.sha256(ARCHIVE_BYTES).hexdigest()
ARCHIVE_NAME = f"juristid-main-web-{TARGET[:12]}.tar.gz"


def _expected(plan_text: str) -> str:
    plan = parse_plan(plan_text)
    rows = tuple(
        Migration(
            lb, f"app/{lb.split('.')[0]}/migrations/{lb.split('.')[1]}.py", "1" * 40, "2" * 64
        )
        for lb in plan.labels
    )
    return Expected(PREVIOUS, TARGET, rows, (), plan).render()


def _manifest(**changes: str | None) -> str:
    values: dict[str, str | None] = {
        "revision": TARGET,
        "previous": PREVIOUS,
        "release_note": "note-present — docs/release-notes/uuendused.toml is in the payload",
        "image_tag": f"juristid-main-web:{TARGET[:12]}",
        "image_id": IMAGE,
        "scanned_image_id": IMAGE,
        "runtime_checked_image_id": IMAGE,
        "archive_image_id": IMAGE,
        "runtime_check": "PASS scripts/ci/assert_image_runtime.py, from inside the image",
        "image_scan": "PASS no fixable HIGH or CRITICAL",
        "image_scan_fixable_high_critical": "0",
        "archive": ARCHIVE_NAME,
        "archive_sha256": ARCHIVE_SHA,
        "loaded_check": f"PASS {IMAGE}",
    }
    values.update(changes)
    return "".join(f"{k}: {v}\n" for k, v in values.items() if v is not None)


@pytest.fixture
def release(tmp_path):
    d = tmp_path / "rel"
    d.mkdir()
    (d / "release.env").write_text(
        "# Sourced by the kit\n"
        f"TARGET={TARGET}\nPREVIOUS={PREVIOUS}\nARCHIVE_SHA={ARCHIVE_SHA}\n"
        f"SCANNED_IMAGE_ID={IMAGE}\nRELEASE_NOTE_KIND=note-present\n",
        encoding="utf-8",
    )
    (d / "manifest.txt").write_text(_manifest(), encoding="utf-8")
    (d / "archive.sha256").write_text(f"{ARCHIVE_SHA}  {ARCHIVE_NAME}\n", encoding="utf-8")
    (d / ARCHIVE_NAME).write_bytes(ARCHIVE_BYTES)
    (d / "expected-migrations.txt").write_text(_expected(R91), encoding="utf-8", newline="\n")
    (d / "plan.txt").write_text(R91, encoding="utf-8")
    return d


def _run(d: Path, **overrides):
    kwargs = {
        "release_env": d / "release.env",
        "manifest": d / "manifest.txt",
        "archive_sha_file": d / "archive.sha256",
        "expected_migrations": d / "expected-migrations.txt",
        "archive": d / ARCHIVE_NAME,
        "plan": d / "plan.txt",
    }
    kwargs.update(overrides)
    gates, inputs = shadow(**kwargs)
    return {g.name: g.status for g in gates}, inputs


def test_every_gate_passes_on_consistent_inputs(release):
    status, inputs = _run(release)
    not_passed = {k: v for k, v in status.items() if v != "PASS" and k != "migrations:derived"}
    assert not_passed == {}
    assert status["migrations:derived"] == "MISSING"  # no --repo here
    assert set(inputs) >= {"release.env", "manifest", "archive.sha256", "archive", "plan.txt"}


def test_a_manifest_without_loaded_check_is_missing_not_passed(release):
    (release / "manifest.txt").write_text(_manifest(loaded_check=None), encoding="utf-8")
    assert _run(release)[0]["manifest:loaded_check"] == "MISSING"


@pytest.mark.parametrize(
    ("changes", "gate"),
    [
        ({"revision": "d" * 40}, "manifest:revision"),
        ({"previous": "d" * 40}, "manifest:previous"),
        ({"scanned_image_id": "sha256:" + "d" * 64}, "manifest:image_ids"),
        ({"archive_sha256": "d" * 64}, "archive:sha256"),
        ({"image_scan_fixable_high_critical": "1"}, "manifest:image_scan"),
        ({"runtime_check": "FAIL"}, "manifest:runtime_check"),
        ({"release_note": "note-absent"}, "manifest:release_note"),
        ({"loaded_check": "PASS sha256:" + "d" * 64}, "manifest:loaded_check"),
    ],
)
def test_a_disagreeing_manifest_fails_its_gate(release, changes, gate):
    (release / "manifest.txt").write_text(_manifest(**changes), encoding="utf-8")
    assert _run(release)[0][gate] == "FAIL"


def test_archive_bytes_that_are_not_the_pinned_sha_fail(release):
    (release / ARCHIVE_NAME).write_bytes(ARCHIVE_BYTES + b"x")
    assert _run(release)[0]["archive:bytes"] == "FAIL"


def test_a_live_plan_with_another_count_fails(release):
    (release / "plan.txt").write_text(R89, encoding="utf-8")
    assert _run(release)[0]["migrations:live_plan"] == "FAIL"


def test_an_expected_file_for_another_release_fails(release):
    text = _expected(R91).replace(f"target: {TARGET}", f"target: {'e' * 40}")
    (release / "expected-migrations.txt").write_text(text, encoding="utf-8", newline="\n")
    assert _run(release)[0]["migrations:commits"] == "FAIL"


def test_missing_evidence_is_reported_missing(release):
    status, _ = _run(release, archive=None, plan=None)
    assert status["archive:bytes"] == status["migrations:live_plan"] == "MISSING"


def _cli_args(release: Path) -> list[str]:
    return [
        "--release-env", str(release / "release.env"),
        "--manifest", str(release / "manifest.txt"),
        "--archive-sha-file", str(release / "archive.sha256"),
        "--expected-migrations", str(release / "expected-migrations.txt"),
        "--archive", str(release / ARCHIVE_NAME),
        "--plan", str(release / "plan.txt"),
    ]  # fmt: skip


def test_the_cli_exits_3_unless_all_pass_and_never_overwrites(release, tmp_path, capsys):
    report = tmp_path / "report.txt"
    args = _cli_args(release)
    assert main([*args, "--report", str(report)]) == EXIT_NOT_READY  # derived: MISSING
    assert "VERDICT: NOT READY - 1 gate(s) not passed: migrations:derived" in report.read_text(
        encoding="utf-8"
    )
    assert main([*args, "--report", str(report)]) == 2
    capsys.readouterr()


def test_it_runs_no_process(release, monkeypatch):
    """Shadow mode reads files; without --repo it starts no process at all (no docker,
    ssh, git or manage.py)."""

    def refuse(*args, **kwargs):
        raise AssertionError(f"a process was started: {args!r}")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    _run(release)


def test_the_source_names_no_host_command():
    source = Path(release_coordinator.__file__).read_text(encoding="utf-8")
    code = source.split('"""', 2)[2]  # after the module docstring
    for word in ("docker", "ssh", "manage.py", "compose", "subprocess", "os.system"):
        assert word not in code, word


def test_env_and_manifest_readers_refuse_duplicates():
    with pytest.raises(ValueError, match="unreadable"):
        read_env("TARGET=a\nTARGET=b\n")
    with pytest.raises(ValueError, match="unreadable"):
        read_manifest("revision: a\nrevision: b\n")


@pytest.mark.parametrize("text", ["", "\n  \n"], ids=["empty", "blank"])
def test_an_empty_manifest_fails_and_its_gates_are_missing(release, tmp_path, text):
    """Review of #701: an empty manifest parsed to {} and skipped every manifest gate."""
    (release / "manifest.txt").write_text(text, encoding="utf-8")
    status, _ = _run(release)
    assert status["manifest"] == "FAIL"
    for name in release_coordinator.ALL_GATES:
        if name.startswith("manifest:") or name == "archive:sha256":
            assert status[name] == "MISSING", name
    report = tmp_path / "empty-manifest-report.txt"
    assert main([*_cli_args(release), "--report", str(report)]) == EXIT_NOT_READY
    assert "every gate passes" not in report.read_text(encoding="utf-8")


def test_every_gate_is_always_reported(release):
    (release / "release.env").unlink()
    (release / "expected-migrations.txt").unlink()
    status, _ = _run(release)
    assert set(release_coordinator.ALL_GATES) <= set(status)
    assert status["input:release.env"] == "MISSING"
    assert status["migrations:live_plan"] == "MISSING"


def test_a_non_utf8_input_is_a_failed_gate_with_a_report(release, tmp_path):
    (release / "manifest.txt").write_bytes(b"revision: \xff\n")
    report = tmp_path / "bad-bytes-report.txt"
    assert main([*_cli_args(release), "--report", str(report)]) == EXIT_NOT_READY
    assert "FAIL    input:manifest" in report.read_text(encoding="utf-8")


def test_it_writes_no_bytecode_next_to_the_tools(release, tmp_path):
    """Under `python -I` (not -B) the sibling load must not create tools/__pycache__."""
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("release_coordinator.py", "release_migrations.py"):
        shutil.copyfile(Path(release_coordinator.__file__).with_name(name), tools / name)
    done = subprocess.run(  # noqa: S603 - this interpreter, copied files, fixed arguments
        [sys.executable, "-I", str(tools / "release_coordinator.py"), *_cli_args(release),
         "--report", str(tmp_path / "r.txt")],
        capture_output=True, text=True, check=False, cwd=tmp_path,
    )  # fmt: skip
    assert done.returncode == EXIT_NOT_READY, done.stderr  # migrations:derived MISSING
    assert not (tools / "__pycache__").exists()
