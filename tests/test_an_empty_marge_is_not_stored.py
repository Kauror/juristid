"""A `Märge` that records nothing is refused — by what it would write (ENG-060).

docs/adr/0105 §4: any one of a sentence, a file, a new `Hetkeseis` or a next step
is a whole save, and a press carrying none of them is refused. The rule read the
**request**: a posted stage counted as content, although `change_stage` writes
nothing when the stage is the one the file already has. So «Uus hetkeseis:
<the current one>» stored a dated row saying nothing, with no stage event.

Two paths, both fixed here:

* **Capture.** The stage counts only when it *moves* the file, and that is
  decided on the Matter row as it stands under `lock_open_matter_for_business_write`
  — not on the instance the view fetched before the lock, which a concurrent
  save may already have made stale.
* **Correction.** `Muuda` never asked the question at all, so a titled `Märge`
  could be corrected into a row with no title, no note and no file. It may not
  now. What counts is what the *row* says — its title, its note, its files — and
  never the stage or step the original save moved: those are separate facts,
  and whether removing a `Märge` takes them with it is ENG-020's open question,
  not this one.

A refused save writes no row and no `ChangeEvent`. Today is frozen.
"""

from __future__ import annotations

import datetime
import threading

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, connections, transaction
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.dates import format_estonian_date
from app.core.errors import DomainError
from app.matters import services as matter_services
from app.matters.models import Matter, MatterProceduralDevelopment
from app.matters.services import DEVELOPMENT_NEEDS_SOMETHING, change_stage
from app.matters.workspace import add_procedural_development
from app.workflow.enums import ActionStatus, DatePrecision
from app.workflow.models import NextAction
from tests import factories

pytestmark = pytest.mark.django_db

TODAY = datetime.date(2026, 9, 24)
HAPPENED = datetime.date(2026, 9, 21)

_REAL_LOCALDATE = timezone.localdate

#: The same bound `tests/test_concurrency.py` waits for PostgreSQL within.
LOCK_WAIT_TIMEOUT = 15


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    def frozen(value=None, timezone=None):
        return TODAY if value is None else _REAL_LOCALDATE(value, timezone)

    monkeypatch.setattr("django.utils.timezone.localdate", frozen)
    return TODAY


def _pdf(name: str = "ministeeriumi_kiri.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"%PDF-1.4 synthetic evidence", content_type="application/pdf")


def _matter_events(matter) -> int:
    return ChangeEvent.objects.filter(matter=matter).count()


def _developments(matter):
    return MatterProceduralDevelopment.objects.filter(matter=matter)


@pytest.fixture
def staged_matter(specialist, stage):
    return factories.MatterFactory(owner=specialist, stage=stage)


# ---------------------------------------------------------------------------
# Capture: the stage counts only when it moves the file
# ---------------------------------------------------------------------------


def test_the_current_stage_alone_is_refused(staged_matter, specialist, stage):
    """The auditor's M3, at the service."""
    events = _matter_events(staged_matter)

    with pytest.raises(DomainError) as refusal:
        add_procedural_development(
            matter=staged_matter,
            author=specialist,
            title="",
            occurred_on=HAPPENED,
            stage=stage,
        )

    assert str(refusal.value) == DEVELOPMENT_NEEDS_SOMETHING
    assert not _developments(staged_matter).exists()
    assert _matter_events(staged_matter) == events


def test_a_real_stage_change_alone_is_a_whole_save(staged_matter, specialist):
    moved_to = factories.StageFactory()

    result = add_procedural_development(
        matter=staged_matter, author=specialist, title="", occurred_on=HAPPENED, stage=moved_to
    )

    staged_matter.refresh_from_db()
    assert staged_matter.stage_id == moved_to.pk
    assert result.record.title == ""
    assert (
        ChangeEvent.objects.filter(
            matter=staged_matter, event_type=ChangeEventType.MATTER_STAGE_CHANGED
        ).count()
        == 1
    )


def test_the_current_stage_beside_real_content_saves_without_a_stage_event(
    staged_matter, specialist, stage
):
    """Choosing the current stage is not an error; it just is not content."""
    add_procedural_development(
        matter=staged_matter,
        author=specialist,
        title="Ministeerium helistas",
        occurred_on=HAPPENED,
        stage=stage,
    )

    assert _developments(staged_matter).count() == 1
    assert not ChangeEvent.objects.filter(
        matter=staged_matter, event_type=ChangeEventType.MATTER_STAGE_CHANGED
    ).exists()


@pytest.mark.parametrize(
    "content",
    [
        {"title": "Rääkisin ministeeriumiga"},
        # The panel has no note box, but the operation takes one and a note is
        # something the row says (docs/adr/0097 §6.2 kept the column).
        {"note": "Uus versioon ei arvesta meie ettepanekut."},
        {"uploads": "file"},
        # docs/adr/0106: a step may have no day yet.
        {"next_text": "Vaatan uue versiooni üle"},
    ],
    ids=["title", "note", "file", "next-step"],
)
def test_each_kind_of_content_is_a_whole_save(normal_matter, specialist, evidence_root, content):
    kwargs = dict(content)
    if kwargs.get("uploads") == "file":
        kwargs["uploads"] = [_pdf()]

    add_procedural_development(
        matter=normal_matter,
        author=specialist,
        occurred_on=HAPPENED,
        title=kwargs.pop("title", ""),
        **kwargs,
    )

    assert _developments(normal_matter).count() == 1


def test_the_stage_is_read_from_the_locked_row_not_the_callers_instance(
    staged_matter, specialist, stage
):
    """The view fetched its `Matter` before the lock. A save that moved the file
    in between leaves that instance stale — and the stale value must not decide."""
    moved_to = factories.StageFactory()
    stale = Matter.objects.get(pk=staged_matter.pk)
    Matter.objects.filter(pk=staged_matter.pk).update(stage=moved_to)

    # Against the stale copy this *looks* like a change; on the row it is not.
    with pytest.raises(DomainError):
        add_procedural_development(
            matter=stale, author=specialist, title="", occurred_on=HAPPENED, stage=moved_to
        )
    assert not _developments(staged_matter).exists()

    # And the other way round: the stale copy says «current», the row says the
    # file has moved on — so choosing the old stage is a real change back.
    add_procedural_development(
        matter=stale, author=specialist, title="", occurred_on=HAPPENED, stage=stage
    )
    staged_matter.refresh_from_db()
    assert staged_matter.stage_id == stage.pk
    assert _developments(staged_matter).count() == 1


# -- through the panel --------------------------------------------------------


def _add_note(matter) -> str:
    return reverse("matters:add_note", kwargs={"pk": matter.pk})


def test_the_panel_refuses_the_current_stage_alone(signed_in, staged_matter, stage):
    """The auditor's M3 as a person does it: 400, the sentence, nothing stored."""
    events = _matter_events(staged_matter)

    response = signed_in.post(
        _add_note(staged_matter),
        {"occurred_on": format_estonian_date(HAPPENED), "stage": str(stage.pk)},
    )

    assert response.status_code == 400
    assert DEVELOPMENT_NEEDS_SOMETHING in response.content.decode()
    assert not _developments(staged_matter).exists()
    assert _matter_events(staged_matter) == events


def test_the_panel_still_takes_a_real_stage_change(signed_in, staged_matter):
    moved_to = factories.StageFactory()

    response = signed_in.post(_add_note(staged_matter), {"stage": str(moved_to.pk)})

    assert response.status_code == 200
    staged_matter.refresh_from_db()
    assert staged_matter.stage_id == moved_to.pk
    assert _developments(staged_matter).count() == 1


def test_the_panel_pre_checks_against_the_stage_it_was_drawn_with(stage):
    """The form repeats the operation's rule early, from the stage the page drew;
    with no drawn stage to compare it leaves the question to the operation."""
    from app.matters.forms import MatterProgressForm
    from app.matters.legal_process import PhaseContext

    drawn_here = MatterProgressForm(
        {"stage": str(stage.pk)}, phases=PhaseContext(stage_key=stage.key)
    )
    assert not drawn_here.is_valid()
    assert drawn_here.non_field_errors() == [DEVELOPMENT_NEEDS_SOMETHING]

    drawn_elsewhere = MatterProgressForm(
        {"stage": str(stage.pk)}, phases=PhaseContext(stage_key="muu-etapp")
    )
    assert drawn_elsewhere.is_valid(), drawn_elsewhere.errors

    undrawn = MatterProgressForm({"stage": str(stage.pk)})
    assert undrawn.is_valid(), undrawn.errors


# ---------------------------------------------------------------------------
# Correction: `Muuda` may not take the last words off a file-less row
# ---------------------------------------------------------------------------


def _record(matter, actor, **kwargs):
    return matter_services.record_procedural_development(
        matter=matter,
        title=kwargs.pop("title", ""),
        occurred_on=kwargs.pop("occurred_on", HAPPENED),
        note=kwargs.pop("note", ""),
        actor=actor,
        **kwargs,
    )


def _correct(development, actor, **changes):
    development.refresh_from_db()
    values = {
        "title": development.title,
        "occurred_on": development.occurred_on,
        "occurred_on_precision": development.occurred_on_precision,
        "note": development.note,
    }
    values.update(changes)
    return matter_services.correct_procedural_development(
        development=development, actor=actor, **values
    )


def _corrections(development) -> int:
    return ChangeEvent.objects.filter(
        event_type=ChangeEventType.PROCEDURAL_DEVELOPMENT_CORRECTED, object_id=str(development.pk)
    ).count()


def test_clearing_every_field_of_a_file_less_marge_is_refused(normal_matter, specialist):
    """The auditor's M7: a title-only `Märge` corrected to no title, no note, no date."""
    development = _record(normal_matter, specialist, title="Ministeerium saatis eelnõu")

    with pytest.raises(DomainError) as refusal:
        _correct(development, specialist, title="  ", note="", occurred_on=None)

    assert str(refusal.value) == matter_services.DEVELOPMENT_CORRECTION_LEAVES_NOTHING
    development.refresh_from_db()
    assert development.title == "Ministeerium saatis eelnõu"
    assert development.occurred_on == HAPPENED
    assert _corrections(development) == 0


def test_clearing_the_note_of_a_note_only_marge_is_refused(normal_matter, specialist):
    development = _record(normal_matter, specialist, note="Uus versioon ei arvesta meid.")

    with pytest.raises(DomainError):
        _correct(development, specialist, note="")

    development.refresh_from_db()
    assert development.note == "Uus versioon ei arvesta meid."
    assert _corrections(development) == 0


def test_clearing_the_title_while_a_note_remains_is_accepted(normal_matter, specialist):
    development = _record(
        normal_matter, specialist, title="Ministeerium saatis eelnõu", note="Vaatan üle."
    )

    _correct(development, specialist, title="")

    development.refresh_from_db()
    assert development.title == ""
    assert development.note == "Vaatan üle."
    assert _corrections(development) == 1


def test_a_marge_whose_content_is_its_file_may_lose_its_words(
    normal_matter, specialist, evidence_root
):
    result = add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="Ministeerium saatis eelnõu",
        occurred_on=HAPPENED,
        uploads=[_pdf()],
    )

    _correct(result.record, specialist, title="")

    result.record.refresh_from_db()
    assert result.record.title == ""


def test_what_the_original_save_moved_is_not_row_content(normal_matter, specialist):
    """A `Märge` that said something *and* moved the stage cannot be corrected
    into «only the stage»: the stage is the Matter's fact, not this row's, and
    reading it as row content would be deciding ENG-020 by the back door."""
    moved_to = factories.StageFactory()
    result = add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="Eelnõu jõudis Riigikokku",
        occurred_on=HAPPENED,
        stage=moved_to,
        next_text="Jälgin komisjoni arutelu",
    )

    with pytest.raises(DomainError):
        _correct(result.record, specialist, title="")

    # And the refused correction rewound nothing the original save wrote.
    normal_matter.refresh_from_db()
    assert normal_matter.stage_id == moved_to.pk
    assert NextAction.objects.filter(matter=normal_matter, status=ActionStatus.OPEN).exists()


def test_a_stage_only_marge_stays_correctable_in_its_date(normal_matter, specialist):
    """The row a whole-save stage change writes has no words and no file, and it
    was a legitimate save. Correcting its date takes nothing off it, so it is not
    refused for being what it always was."""
    result = add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="",
        occurred_on=HAPPENED,
        stage=factories.StageFactory(),
    )

    _correct(
        result.record,
        specialist,
        occurred_on=datetime.date(2026, 9, 1),
        occurred_on_precision=DatePrecision.MONTH,
    )

    result.record.refresh_from_db()
    assert result.record.occurred_on_precision == DatePrecision.MONTH


def test_the_correction_route_says_why(signed_in, normal_matter, specialist):
    development = _record(normal_matter, specialist, title="Ministeerium saatis eelnõu")
    url = reverse(
        "matters:update_development",
        kwargs={"pk": normal_matter.pk, "development_id": development.pk},
    )

    response = signed_in.post(
        url,
        {
            "title": "",
            "note": "",
            "occurred_on": format_estonian_date(HAPPENED),
            "areng_precision": DatePrecision.EXACT,
            "revision": development.revision_token,
        },
    )

    assert response.status_code == 400
    assert matter_services.DEVELOPMENT_CORRECTION_LEAVES_NOTHING in response.content.decode()
    development.refresh_from_db()
    assert development.title == "Ministeerium saatis eelnõu"
    assert _corrections(development) == 0


# ---------------------------------------------------------------------------
# Concurrency: the comparison waits for, and reads, the committed stage
# ---------------------------------------------------------------------------


def wait_for_a_blocked_backend() -> bool:
    """Until PostgreSQL reports a backend queued on a lock — never a sleep.

    `tests/test_concurrency.py`'s helper, restated rather than imported so this
    file does not load another module's world to borrow six lines.
    """
    deadline = timezone.now() + datetime.timedelta(seconds=LOCK_WAIT_TIMEOUT)
    while timezone.now() < deadline:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE cardinality(pg_blocking_pids(pid)) > 0"
            )
            if cursor.fetchone()[0] >= 1:
                return True
    return False


def run_in_thread(target) -> threading.Thread:
    def wrapped() -> None:
        try:
            target()
        finally:
            connections.close_all()

    thread = threading.Thread(target=wrapped)
    thread.start()
    return thread


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_a_stage_change_committed_while_the_marge_waited_decides_it(specialist):
    """Three connections: this thread, the holder and the `Märge`.

    The page was rendered at stage A and the person chose B. Before their save
    reaches the Matter row, a colleague moves the file to B and commits. The
    `Märge` queued behind that lock must read B from the row it then holds — so
    «B, and nothing else» is a press with nothing in it, refused, and no row is
    written. Deciding on the instance the view arrived with (still A) would store
    the empty row the audit found.
    """
    first, second = factories.StageFactory(), factories.StageFactory()
    matter = factories.MatterFactory(owner=specialist, stage=first)
    stale = Matter.objects.get(pk=matter.pk)
    holding = threading.Event()
    marge_started = threading.Event()
    outcomes: list[str] = []
    failures: list[BaseException] = []

    def colleague_moves_the_file() -> None:
        try:
            with transaction.atomic():
                locked = Matter.objects.select_for_update(no_key=True).get(pk=matter.pk)
                holding.set()
                marge_started.wait(timeout=LOCK_WAIT_TIMEOUT)
                wait_for_a_blocked_backend()
                change_stage(matter=locked, stage=second, actor=specialist)
        except BaseException as error:  # pragma: no cover - surfaced below
            failures.append(error)

    def the_marge() -> None:
        holding.wait(timeout=LOCK_WAIT_TIMEOUT)
        marge_started.set()
        try:
            add_procedural_development(
                matter=stale, author=specialist, title="", occurred_on=HAPPENED, stage=second
            )
            outcomes.append("saved")
        except DomainError as error:
            outcomes.append(str(error))
        except BaseException as error:  # pragma: no cover - surfaced below
            failures.append(error)

    holder = run_in_thread(colleague_moves_the_file)
    marge = run_in_thread(the_marge)
    holder.join(timeout=LOCK_WAIT_TIMEOUT * 2)
    marge.join(timeout=LOCK_WAIT_TIMEOUT * 2)

    assert not failures, failures
    assert outcomes == [DEVELOPMENT_NEEDS_SOMETHING]
    assert not MatterProceduralDevelopment.objects.filter(matter=matter).exists()
    assert Matter.objects.get(pk=matter.pk).stage_id == second.pk
    assert (
        ChangeEvent.objects.filter(
            matter=matter, event_type=ChangeEventType.MATTER_STAGE_CHANGED
        ).count()
        == 1
    )
