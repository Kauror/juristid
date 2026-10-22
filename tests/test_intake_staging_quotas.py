"""`Uus teema` staging is bounded on the way in and swept on a timer.

csf_3942255b466a4e213b958b16 (docs/adr/0177): the twenty-file cap was per form,
a form is one render, and the sweep of expired forms was a command nobody
scheduled — so one person could fill the pending-upload volume with fresh
forms. These tests hold the four halves of the fix:

* every quota refuses **before a byte is written** — per person and for the
  deployment, by forms and by bytes, and by free space on the volume;
* the check and the write are **serialised**, so two requests at once cannot
  both pass a limit only one of them fits under, and a write that fails gives
  its reservation back;
* the sweep deletes **only expired or consumed** staging, is bounded and
  idempotent, and reports what it refused;
* the sweep **never touches evidence and never follows a symbolic link**.
"""

from __future__ import annotations

import hashlib
import os
import threading
from datetime import timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse
from django.utils import timezone

from app.matters import intake_staging
from app.matters.staging import MatterIntakeFile, MatterIntakeSession
from tests import synthetic_corpus as corpus

STAGE = reverse("matters:intake_stage")


def _pdf(name: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(
        name, corpus.text_pdf([f"Lisa {name}"]), content_type="application/pdf"
    )


def _accepted(name: str) -> Any:
    from app.documents.uploads import read_upload

    return read_upload(_pdf(name))


def _staging_root() -> Path:
    return Path(settings.PENDING_UPLOAD_ROOT) / intake_staging.STORAGE_PREFIX


def _staged_objects() -> set[Path]:
    root = _staging_root()
    return {path for path in root.rglob("*") if path.is_file()} if root.exists() else set()


def _evidence_digest() -> dict[str, str]:
    root = Path(settings.EVIDENCE_ROOT)
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _plant_evidence() -> Path:
    planted = Path(settings.EVIDENCE_ROOT) / "ab" / "cd" / "planted-evidence.pdf"
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_bytes(b"%PDF-1.4 immutable evidence")
    return planted


def _symlink(target: Path, link: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):  # pragma: no cover - Windows without privilege
        pytest.skip("this host cannot create symbolic links")


def _stage(owner: Any, *names: str, session: Any = None) -> intake_staging.StagingResult:
    return intake_staging.stage_uploads(
        owner=owner, uploads=[_accepted(name) for name in names], session=session
    )


def _expire(session: Any) -> None:
    MatterIntakeSession.objects.filter(pk=session.pk).update(
        expires_at=timezone.now() - timedelta(hours=2)
    )


def _size(name: str) -> int:
    return len(_accepted(name).content)


# ---------------------------------------------------------------------------
# Quotas refuse before a byte is written
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_person_at_their_form_limit_is_refused_before_anything_is_written(
    signed_in, specialist, settings
):
    settings.INTAKE_STAGING_MAX_ACTIVE_SESSIONS_PER_OWNER = 2
    _stage(specialist, "a.pdf")
    _stage(specialist, "b.pdf")
    before = _staged_objects()
    third = intake_staging.open_session(owner=specialist)

    response = signed_in.post(STAGE, {"intake": str(third.pk), "files": [_pdf("c.pdf")]})

    assert response.status_code == 200
    expected = intake_staging.OWNER_SESSIONS_FULL.format(limit=2)
    assert response.context["intake_errors"] == (expected,)
    assert _staged_objects() == before
    assert not MatterIntakeFile.objects.filter(session=third).exists()


@pytest.mark.django_db
def test_the_service_itself_refuses_with_the_bytes_that_arrived(specialist, settings):
    """The view's early answer is a courtesy; this is the answer that decides."""
    settings.INTAKE_STAGING_MAX_ACTIVE_SESSIONS_PER_OWNER = 1
    _stage(specialist, "a.pdf")
    before = _staged_objects()

    result = _stage(specialist, "b.pdf", session=intake_staging.open_session(owner=specialist))

    assert result.staged == ()
    assert result.refusal == intake_staging.OWNER_SESSIONS_FULL.format(limit=1)
    assert _staged_objects() == before


@pytest.mark.django_db
def test_empty_forms_are_not_active_and_a_form_already_holding_files_may_grow(specialist, settings):
    """A render is a row and no bytes; reloading the page must not use the quota up."""
    settings.INTAKE_STAGING_MAX_ACTIVE_SESSIONS_PER_OWNER = 1
    for _ in range(3):
        intake_staging.open_session(owner=specialist)
    first = _stage(specialist, "a.pdf").session

    more = _stage(specialist, "b.pdf", session=first)

    assert more.refusal == ""
    assert len(more.staged) == 1
    assert intake_staging.staging_usage(owner=specialist).owner_active_sessions == 1


@pytest.mark.django_db
def test_a_person_over_their_byte_quota_is_refused_and_removed_files_still_count(
    specialist, settings
):
    """Taking a file back off the form leaves its bytes until the sweep."""
    settings.INTAKE_STAGING_MAX_BYTES_PER_OWNER = _size("a.pdf") + _size("b.pdf") - 1
    first = _stage(specialist, "a.pdf")
    assert intake_staging.remove_file(session=first.session, file_id=first.staged[0].pk)
    before = _staged_objects()

    result = _stage(specialist, "b.pdf", session=first.session)

    assert result.refusal == intake_staging.OWNER_BYTES_FULL.format(
        limit_mb=settings.INTAKE_STAGING_MAX_BYTES_PER_OWNER // (1024 * 1024)
    )
    assert _staged_objects() == before


@pytest.mark.django_db
def test_a_consumed_form_gives_its_quota_back(specialist, settings):
    settings.INTAKE_STAGING_MAX_ACTIVE_SESSIONS_PER_OWNER = 1
    settings.INTAKE_STAGING_MAX_BYTES_PER_OWNER = _size("a.pdf")
    first = _stage(specialist, "a.pdf").session
    intake_staging.consume_session(first)

    result = _stage(specialist, "b.pdf")

    assert result.refusal == ""
    assert len(result.staged) == 1


@pytest.mark.django_db
def test_somebody_elses_staging_counts_against_the_deployment_not_against_me(
    specialist, other_specialist, settings
):
    settings.INTAKE_STAGING_MAX_ACTIVE_SESSIONS_PER_OWNER = 1
    settings.INTAKE_STAGING_MAX_ACTIVE_SESSIONS = 1
    _stage(other_specialist, "a.pdf")
    before = _staged_objects()

    result = _stage(specialist, "b.pdf")

    assert result.refusal == intake_staging.STAGING_FULL
    assert _staged_objects() == before


@pytest.mark.django_db
def test_the_deployment_byte_ceiling_refuses_everybody(specialist, other_specialist, settings):
    settings.INTAKE_STAGING_MAX_BYTES = _size("a.pdf") + _size("b.pdf") - 1
    _stage(other_specialist, "a.pdf")
    before = _staged_objects()

    result = _stage(specialist, "b.pdf")

    assert result.refusal == intake_staging.STAGING_FULL
    assert _staged_objects() == before


@pytest.mark.django_db
def test_too_little_free_space_on_the_volume_refuses_before_writing(
    signed_in, specialist, settings, monkeypatch
):
    settings.INTAKE_STAGING_MIN_FREE_BYTES = 10 * 1024 * 1024
    seen: list[str] = []

    def disk_usage(path: Any) -> SimpleNamespace:
        seen.append(str(path))
        return SimpleNamespace(total=1 << 40, used=1 << 40, free=10 * 1024 * 1024 + 100)

    monkeypatch.setattr(intake_staging.shutil, "disk_usage", disk_usage)
    session = intake_staging.open_session(owner=specialist)

    response = signed_in.post(STAGE, {"intake": str(session.pk), "files": [_pdf("a.pdf")]})

    assert response.status_code == 200
    assert response.context["intake_errors"] == (intake_staging.STAGING_FULL,)
    assert _staged_objects() == set()
    assert seen and all(Path(path) == Path(settings.PENDING_UPLOAD_ROOT) for path in seen)


@pytest.mark.django_db
def test_the_declared_size_is_refused_before_the_files_are_read(
    signed_in, specialist, settings, monkeypatch
):
    from app.documents import uploads

    settings.INTAKE_STAGING_MAX_BYTES_PER_OWNER = 10

    def must_not_read(handle: Any) -> Any:
        raise AssertionError("a request over quota was read")

    monkeypatch.setattr(uploads, "read_upload", must_not_read)
    session = intake_staging.open_session(owner=specialist)

    response = signed_in.post(STAGE, {"intake": str(session.pk), "files": [_pdf("a.pdf")]})

    assert response.status_code == 200
    assert response.context["intake_errors"] == (
        intake_staging.OWNER_BYTES_FULL.format(limit_mb=0),
    )
    assert _staged_objects() == set()


# ---------------------------------------------------------------------------
# A failed write gives its reservation back
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_write_that_fails_half_way_leaves_no_bytes_and_no_reservation(
    specialist, settings, monkeypatch
):
    settings.INTAKE_STAGING_MAX_BYTES_PER_OWNER = _size("a.pdf") + _size("b.pdf")
    real = intake_staging.staging_storage()
    calls = {"n": 0}

    class FailingOnTheSecondWrite:
        def __getattr__(self, name: str) -> Any:
            return getattr(real, name)

        def save(self, name: str, content: Any) -> str:
            calls["n"] += 1
            if calls["n"] == 2:
                partial = Path(real.path(name))
                partial.parent.mkdir(parents=True, exist_ok=True)
                partial.write_bytes(b"%PDF-1.4 half")
                raise OSError("disk full")
            return str(real.save(name, content))

    monkeypatch.setattr(intake_staging, "staging_storage", FailingOnTheSecondWrite)
    session = intake_staging.open_session(owner=specialist)
    with pytest.raises(OSError, match="disk full"):
        _stage(specialist, "a.pdf", "b.pdf", session=session)

    assert _staged_objects() == set()
    assert not MatterIntakeFile.objects.filter(session=session).exists()
    assert intake_staging.staging_usage(owner=specialist).owner_bytes == 0

    monkeypatch.setattr(intake_staging, "staging_storage", lambda: real)
    retried = _stage(specialist, "a.pdf", "b.pdf", session=session)
    assert retried.refusal == ""
    assert len(retried.staged) == 2


# ---------------------------------------------------------------------------
# Two requests at once cannot both pass
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
@pytest.mark.parametrize("limit", ["per_owner", "global"])
def test_two_simultaneous_uploads_cannot_both_pass_a_limit_one_fits_under(
    specialist, other_specialist, settings, monkeypatch, limit
):
    """The first holds the lock until the second is seen waiting on it.

    Two worker threads and the test's own connection: three in all, the most
    the emulated local cluster takes. Without the lock the second would not
    wait, `wait_until_blocked` would fail, and both would be staged.
    """
    from tests.test_evidence_concurrency import TIMEOUT, Runner, wait_until_blocked

    if limit == "per_owner":
        settings.INTAKE_STAGING_MAX_ACTIVE_SESSIONS_PER_OWNER = 1
        owners = (specialist, specialist)
        expected = intake_staging.OWNER_SESSIONS_FULL.format(limit=1)
    else:
        settings.INTAKE_STAGING_MAX_ACTIVE_SESSIONS = 1
        owners = (specialist, other_specialist)
        expected = intake_staging.STAGING_FULL
    sessions = [intake_staging.open_session(owner=owner) for owner in owners]
    uploads = [_accepted("esimene.pdf"), _accepted("teine.pdf")]

    real_each = intake_staging._stage_each
    holding = threading.Event()
    release = threading.Event()

    def stage_each(*args: Any, **kwargs: Any) -> Any:
        result = real_each(*args, **kwargs)
        if not holding.is_set():
            holding.set()
            assert release.wait(TIMEOUT)
        return result

    monkeypatch.setattr(intake_staging, "_stage_each", stage_each)
    results: dict[int, Any] = {}

    def run(index: int) -> None:
        results[index] = intake_staging.stage_uploads(
            owner=owners[index], uploads=[uploads[index]], session=sessions[index]
        )

    first = Runner(lambda: run(0)).start()
    assert holding.wait(TIMEOUT)
    second = Runner(lambda: run(1)).start()
    try:
        wait_until_blocked(backends=1)
    finally:
        release.set()
    assert first.join() is None and second.join() is None

    assert results[0].refusal == "" and len(results[0].staged) == 1
    assert results[1].refusal == expected and results[1].staged == ()
    assert MatterIntakeFile.objects.count() == 1
    assert len(_staged_objects()) == 1


# ---------------------------------------------------------------------------
# The sweep: what is eligible, bounded, idempotent
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_only_expired_or_consumed_staging_is_swept(specialist):
    live = _stage(specialist, "live.pdf").session
    empty_live = intake_staging.open_session(owner=specialist)
    expired = _stage(specialist, "expired.pdf").session
    _expire(expired)
    consumed = _stage(specialist, "consumed.pdf").session
    MatterIntakeSession.objects.filter(pk=consumed.pk).update(consumed_at=timezone.now())

    report = intake_staging.sweep_stale_sessions()

    assert report.sessions == 2 and report.errors == () and report.remaining == 0
    remaining = set(MatterIntakeSession.objects.values_list("pk", flat=True))
    assert remaining == {live.pk, empty_live.pk}
    assert len(list((_staging_root() / str(live.pk)).iterdir())) == 1
    assert not (_staging_root() / str(expired.pk)).exists()
    assert not (_staging_root() / str(consumed.pk)).exists()


@pytest.mark.django_db
def test_a_run_is_bounded_by_sessions_and_by_bytes_and_a_second_run_is_a_no_op(specialist):
    stale = [_stage(specialist, f"{index}.pdf").session for index in range(3)]
    for session in stale:
        _expire(session)

    by_count = intake_staging.sweep_stale_sessions(limit=2)
    assert (by_count.sessions, by_count.remaining) == (2, 1)

    # One session always goes, however small the byte bound, so a single large
    # form cannot stall the sweep for ever.
    by_bytes = intake_staging.sweep_stale_sessions(max_bytes=1)
    assert (by_bytes.sessions, by_bytes.remaining) == (1, 0)

    again = intake_staging.sweep_stale_sessions()
    assert again == intake_staging.SweepReport(sessions=0, files=0, objects=0)


@pytest.mark.django_db
def test_the_byte_bound_stops_before_the_next_session_would_pass_it(specialist):
    sessions = [_stage(specialist, f"{index}.pdf").session for index in range(3)]
    for session in sessions:
        _expire(session)
    two = sum(
        MatterIntakeFile.objects.filter(session__in=sessions[:2]).values_list(
            "size_bytes", flat=True
        )
    )

    report = intake_staging.sweep_stale_sessions(max_bytes=two)

    assert report.sessions == 2
    assert report.remaining == 1


@pytest.mark.django_db
def test_the_command_reports_what_it_did_and_a_dry_run_does_nothing(specialist):
    stale = _stage(specialist, "a.pdf").session
    _expire(stale)

    dry = StringIO()
    call_command("prune_intake_staging", "--dry-run", stdout=dry)
    assert "Kustutataks 1 ettevalmistust, 1 faili" in dry.getvalue()
    assert MatterIntakeSession.objects.filter(pk=stale.pk).exists()

    out = StringIO()
    call_command("prune_intake_staging", stdout=out)
    assert "Kustutatud 1 ettevalmistust, 1 faili, 1 objekti" in out.getvalue()
    assert "Vigu: 0." in out.getvalue()
    assert not MatterIntakeSession.objects.filter(pk=stale.pk).exists()


@pytest.mark.django_db
def test_the_loop_sweeps_on_its_first_turn(specialist, monkeypatch):
    stale = _stage(specialist, "a.pdf").session
    _expire(stale)
    real = intake_staging.sweep_stale_sessions
    turns = {"n": 0}

    class Stop(BaseException):
        pass

    def sweep(**kwargs: Any) -> Any:
        turns["n"] += 1
        if turns["n"] > 1:
            raise Stop
        return real(**kwargs)

    monkeypatch.setattr(intake_staging, "sweep_stale_sessions", sweep)
    out = StringIO()
    with pytest.raises(Stop):
        call_command("prune_intake_staging", "--loop", "--interval-seconds", "0", stdout=out)

    assert "Kustutatud 1 ettevalmistust" in out.getvalue()
    assert not MatterIntakeSession.objects.filter(pk=stale.pk).exists()


# ---------------------------------------------------------------------------
# The sweep never touches evidence and never follows a link
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_link_inside_a_stale_session_is_reported_not_followed(specialist):
    planted = _plant_evidence()
    evidence_before = _evidence_digest()
    stale = _stage(specialist, "a.pdf").session
    _expire(stale)
    link = _staging_root() / str(stale.pk) / "looks-like-a-staged-file"
    _symlink(planted, link)

    with pytest.raises(CommandError, match="jäeti puutumata"):
        call_command("prune_intake_staging", stdout=StringIO(), stderr=StringIO())

    assert _evidence_digest() == evidence_before
    assert link.is_symlink()
    assert MatterIntakeSession.objects.filter(pk=stale.pk).exists()
    assert len(list((_staging_root() / str(stale.pk)).iterdir())) == 2


@pytest.mark.django_db
def test_a_session_directory_that_is_a_link_to_evidence_is_refused(specialist):
    _plant_evidence()
    evidence_before = _evidence_digest()
    survivor = _stage(specialist, "survivor.pdf").session
    stale = intake_staging.open_session(owner=specialist)
    _expire(stale)
    _staging_root().mkdir(parents=True, exist_ok=True)
    _symlink(Path(settings.EVIDENCE_ROOT), _staging_root() / str(stale.pk))

    report = intake_staging.sweep_stale_sessions()

    assert report.sessions == 0
    assert len(report.errors) == 1 and "symbolic link" in report.errors[0]
    assert _evidence_digest() == evidence_before
    assert MatterIntakeSession.objects.filter(pk=stale.pk).exists()
    assert MatterIntakeSession.objects.filter(pk=survivor.pk).exists()


@pytest.mark.django_db
def test_a_row_naming_a_key_outside_its_session_is_refused(specialist):
    live = _stage(specialist, "live.pdf")
    stale = _stage(specialist, "stale.pdf")
    _expire(stale.session)
    # The stale row now climbs out of its own prefix into the live form's:
    # following it would delete bytes an unfinished form is still offering.
    live_name = Path(live.staged[0].storage_key).name
    MatterIntakeFile.objects.filter(pk=stale.staged[0].pk).update(
        storage_key=f"intake/{stale.session.pk}/../{live.session.pk}/{live_name}"
    )
    before = _staged_objects()

    report = intake_staging.sweep_stale_sessions()

    assert report.sessions == 0
    assert len(report.errors) == 1 and "outside" in report.errors[0]
    assert _staged_objects() == before


@pytest.mark.django_db
def test_an_ordinary_sweep_leaves_the_evidence_tree_byte_for_byte(specialist):
    _plant_evidence()
    evidence_before = _evidence_digest()
    for name in ("a.pdf", "b.pdf"):
        _expire(_stage(specialist, name).session)

    report = intake_staging.sweep_stale_sessions()

    assert report.sessions == 2 and report.errors == ()
    assert _evidence_digest() == evidence_before


# ---------------------------------------------------------------------------
# The probe
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_probe_is_green_while_nothing_stale_waits_too_long(specialist, settings):
    settings.INTAKE_STAGING_SWEEP_STALE_SECONDS = 3600
    _stage(specialist, "live.pdf")
    stale = _stage(specialist, "stale.pdf").session
    MatterIntakeSession.objects.filter(pk=stale.pk).update(
        expires_at=timezone.now() - timedelta(minutes=5)
    )

    out = StringIO()
    call_command("check_intake_staging", stdout=out)
    assert "Ettevalmistused korras" in out.getvalue()


@pytest.mark.django_db
def test_the_probe_is_red_when_a_stale_session_waits_past_the_threshold(specialist, settings):
    settings.INTAKE_STAGING_SWEEP_STALE_SECONDS = 3600
    _expire(_stage(specialist, "stale.pdf").session)

    err = StringIO()
    with pytest.raises(SystemExit) as exit_code:
        call_command("check_intake_staging", "--quiet", stderr=err)
    assert exit_code.value.code == 1
    assert "oodanud koristamist" in err.getvalue()


@pytest.mark.django_db
def test_the_probe_is_red_when_the_volume_is_short_of_headroom(settings, monkeypatch):
    settings.INTAKE_STAGING_MIN_FREE_BYTES = 1024
    monkeypatch.setattr(
        intake_staging.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(total=2048, used=2048, free=10),
    )

    err = StringIO()
    with pytest.raises(SystemExit) as exit_code:
        call_command("check_intake_staging", "--quiet", stderr=err)
    assert exit_code.value.code == 1
    assert "Hoidlas on vaba 10 baiti" in err.getvalue()
