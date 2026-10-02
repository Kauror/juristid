"""`Hetkeseis` periods, stage-driven closure, reopening and the next-stage order (docs/adr/0131).

The brief's matrix, each item where it is decided:

* **periods** — no stage, no period; every move a new row; the same stage is no
  move; backward is a new row; an old row is never rewritten;
* **binding** — work is tied to the period it was done in, and an act saved
  together with a move belongs to the period before the move — except on a
  Matter with no stage yet, where it belongs to the first period;
* **closure** — «Jõustunud» and «Rohkem ei tegele» close an open Matter in the
  same transaction, «Jõustumise ootel» does not, and the open step goes the way
  every closure ends it; `Lõpeta teema` is gone;
* **reopening** — into a named stage, as a new period, the closing one kept;
* **the next-stage order** — likely forward first, backward last, the bridge
  from «ELi õiguse ülevõtmise ootel» to «Idee», history never impossible;
* **Teema käik** — grouped by period, the current one open, no boundary rows;
* **the rail** — the procedure and the opinions that went out, and no
  operational clutter;
* **terminology** — «Valdkond» on every surface that asks or shows it.
"""

from __future__ import annotations

import datetime as dt
import importlib
import re

import pytest
from django.apps import apps as django_apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.audit.operations import stage_episode_scope
from app.core.errors import DomainError
from app.core.invariants import check_domain_invariants
from app.intelligence.enums import ImportantDateKind
from app.intelligence.services import add_important_date
from app.matters.enums import StageEpisodeOrigin
from app.matters.episode_timeline import (
    EARLIER_ACTIVITY_LABEL,
    NO_STAGE_LABEL,
    matter_episode_timeline,
)
from app.matters.forms import MatterCreateForm, MatterEditForm, ReopenForm
from app.matters.models import MatterStageEpisode
from app.matters.process_timeline import SENT_LABEL, TRANSPOSITION_DEADLINE_LABEL, process_steps
from app.matters.services import (
    REOPEN_INTO_TERMINAL_STAGE,
    REOPEN_NEEDS_A_STAGE,
    add_engagement,
    change_stage,
    close_matter,
    create_matter,
    reopen_matter_into_stage,
    set_matter_title,
)
from app.matters.stage_episodes import offered_next_stages, offered_reopening_stages, period_label
from app.matters.workspace import (
    TERMINAL_STAGE_MAKES_NO_STEP,
    add_matter_koda_opinion,
    add_matter_note,
    add_procedural_development,
)
from app.submissions.enums import SubmissionStatus
from app.taxonomy.models import LegalInstrumentType
from app.workflow.enums import ActionStatus, Disposition
from app.workflow.models import NextAction, StageVocabulary, resolve_legacy_status
from app.workflow.services import set_next_action_for_new_work
from app.workflow.stage_flow import (
    COMMON_TAIL,
    flow_context,
    next_stage_keys,
    reopening_stage_keys,
)
from app.workflow.vocabulary import RAW_LABEL_TO_DISPOSITION, RAW_LABEL_TO_STAGE
from tests import factories

pytestmark = pytest.mark.django_db

#: Every active stage key, in the vocabulary's order — what `stage_flow` is given.
ALL_KEYS = [
    "idea",
    "consultation",
    "government",
    "parliament",
    "awaiting_entry",
    "in_force",
    "estonian_eu_position",
    "eu_procedure",
    "awaiting_transposition",
    "other",
    "monitoring_stopped",
]
DOMESTIC = {"idea", "consultation", "government", "parliament"}
EU = {"estonian_eu_position", "eu_procedure", "awaiting_transposition"}


def _stage(key: str) -> StageVocabulary:
    return StageVocabulary.objects.get(key=key)


def _matter(author, *, stage: str | None = None, instruments: tuple[str, ...] = ("seadus",)):
    """A natively created Matter — through `create_matter`, as `Uus teema` makes one."""
    matter = create_matter(
        title="Pakendiseaduse muudatus",
        actor=author,
        owner=author,
        stage=_stage(stage) if stage else None,
    )
    matter.legal_instruments.set([LegalInstrumentType.objects.get(key=key) for key in instruments])
    return matter


def _episodes(matter) -> list[tuple[int, str, bool]]:
    return [
        (episode.sequence, episode.stage.key, episode.is_current)
        for episode in MatterStageEpisode.objects.filter(matter=matter)
        .select_related("stage")
        .order_by("sequence")
    ]


def _current(matter) -> MatterStageEpisode:
    return MatterStageEpisode.objects.get(matter=matter, is_current=True)


def _event(matter, event_type: str) -> ChangeEvent:
    return ChangeEvent.objects.filter(matter=matter, event_type=event_type).latest("created_at")


def _pdf(name: str = "arvamus.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, f"%PDF-1.4 {name}".encode(), content_type="application/pdf")


# ---------------------------------------------------------------------------
# 1–5 — periods
# ---------------------------------------------------------------------------


def test_a_matter_created_with_no_stage_has_no_period(specialist):
    matter = _matter(specialist)

    assert _episodes(matter) == []
    assert check_domain_invariants().ok


def test_a_matter_created_in_idee_has_one_recorded_period(specialist):
    matter = _matter(specialist, stage="idea")

    episode = _current(matter)
    assert _episodes(matter) == [(1, "idea", True)]
    assert episode.origin == StageEpisodeOrigin.RECORDED
    assert episode.started_at is not None and episode.ended_at is None
    # `Teema loodud` is written in that period.
    assert _event(matter, ChangeEventType.MATTER_CREATED).stage_episode_id == episode.pk


def test_a_move_ends_one_period_and_begins_the_next(specialist):
    matter = _matter(specialist, stage="idea")
    idea = _current(matter)

    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)

    idea.refresh_from_db()
    assert _episodes(matter) == [(1, "idea", False), (2, "consultation", True)]
    assert idea.ended_at is not None and idea.ended_at >= idea.started_at
    consultation = _current(matter)
    assert consultation.started_at == idea.ended_at
    moved = _event(matter, ChangeEventType.MATTER_STAGE_CHANGED)
    assert moved.stage_episode_id == consultation.pk
    assert (moved.payload["from_key"], moved.payload["to_key"]) == ("idea", "consultation")


def test_going_back_opens_a_new_period_and_leaves_the_old_one_alone(specialist):
    matter = _matter(specialist, stage="idea")
    first = _current(matter)
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    first.refresh_from_db()
    frozen = (first.stage_id, first.started_at, first.ended_at, first.is_current, first.sequence)

    change_stage(matter=matter, stage=_stage("idea"), actor=specialist)

    assert _episodes(matter) == [(1, "idea", False), (2, "consultation", False), (3, "idea", True)]
    first.refresh_from_db()
    assert (
        first.stage_id,
        first.started_at,
        first.ended_at,
        first.is_current,
        first.sequence,
    ) == frozen
    assert _current(matter).pk != first.pk


def test_the_same_stage_again_is_not_a_move(specialist):
    matter = _matter(specialist, stage="idea")
    events = ChangeEvent.objects.filter(matter=matter).count()

    change_stage(matter=matter, stage=_stage("idea"), actor=specialist)

    assert _episodes(matter) == [(1, "idea", True)]
    assert ChangeEvent.objects.filter(matter=matter).count() == events


def test_clearing_the_stage_ends_the_period_and_opens_none(specialist):
    """`Määramata` is not a period, from `Muuda teemat` as from anywhere."""
    matter = _matter(specialist, stage="idea")

    change_stage(matter=matter, stage=None, actor=specialist)

    assert _episodes(matter) == [(1, "idea", False)]
    assert matter.stage is None
    assert check_domain_invariants().ok


def test_the_database_refuses_a_second_current_period(specialist):
    matter = _matter(specialist, stage="idea")

    with pytest.raises(IntegrityError), transaction.atomic():
        MatterStageEpisode.objects.create(
            matter=matter,
            stage=_stage("government"),
            sequence=2,
            origin=StageEpisodeOrigin.RECORDED,
            started_at=timezone.now(),
        )


def test_the_database_refuses_an_unknown_start_on_a_recorded_period(specialist):
    matter = _matter(specialist)

    with pytest.raises(IntegrityError), transaction.atomic():
        MatterStageEpisode.objects.create(
            matter=matter, stage=_stage("idea"), sequence=1, origin=StageEpisodeOrigin.RECORDED
        )


# ---------------------------------------------------------------------------
# 6–9 — which period an act belongs to
# ---------------------------------------------------------------------------


def test_work_in_the_current_period_is_tied_to_it(specialist):
    matter = _matter(specialist, stage="idea")

    note = add_matter_note(matter=matter, author=specialist, body="Helistasin ministeeriumisse.")

    added = ChangeEvent.objects.get(event_type=ChangeEventType.ENTRY_ADDED, object_id=note.entry.pk)
    assert added.stage_episode_id == _current(matter).pk


def test_an_opinion_sent_with_a_move_stays_in_the_period_it_was_written_in(specialist):
    """Rule 7 / Flow B: the opinion belongs to «Idee»; «Kooskõlastusringil» begins after it."""
    matter = _matter(specialist, stage="idea")
    idea = _current(matter)
    ministry = factories.OrganisationFactory()

    result = add_matter_koda_opinion(
        matter=matter,
        author=specialist,
        upload=_pdf(),
        recipients=[ministry],
        sent_on=timezone.localdate(),
        stage=_stage("consultation"),
    )

    sent = ChangeEvent.objects.get(
        event_type=ChangeEventType.SUBMISSION_SENT, object_id=result.record.pk
    )
    assert sent.stage_episode_id == idea.pk
    consultation = _current(matter)
    assert consultation.stage.key == "consultation"
    assert _event(matter, ChangeEventType.MATTER_STAGE_CHANGED).stage_episode_id == consultation.pk
    # Every row the save wrote names the outgoing period, before the move or after.
    written = ChangeEvent.objects.filter(operation_id=result.operation_id).exclude(
        event_type=ChangeEventType.MATTER_STAGE_CHANGED
    )
    assert written.exists()
    assert set(written.values_list("stage_episode_id", flat=True)) == {idea.pk}

    timeline = matter_episode_timeline(matter=matter, user=specialist)
    assert timeline is not None
    current, earlier = timeline.groups[0], timeline.groups[1]
    assert current.label == "Kooskõlastusringil" and current.is_open and current.items == []
    assert earlier.label == "Idee" and not earlier.is_open
    assert [item.submission for item in earlier.items if item.submission] == [result.record]


def test_a_marge_that_moves_the_stage_stays_in_the_outgoing_period(specialist):
    matter = _matter(specialist, stage="idea")
    idea = _current(matter)

    result = add_procedural_development(
        matter=matter,
        author=specialist,
        title="Eelnõu saadeti kooskõlastusringile",
        occurred_on=timezone.localdate(),
        stage=_stage("consultation"),
    )

    recorded = ChangeEvent.objects.get(
        event_type=ChangeEventType.PROCEDURAL_DEVELOPMENT_RECORDED, object_id=result.record.pk
    )
    assert recorded.stage_episode_id == idea.pk


def test_with_no_stage_yet_the_act_belongs_to_the_first_period(specialist):
    """Rule 8: there is no outgoing period, so the first one holds the act."""
    matter = _matter(specialist)

    result = add_procedural_development(
        matter=matter,
        author=specialist,
        title="Ministeerium saatis VTK",
        occurred_on=timezone.localdate(),
        stage=_stage("idea"),
    )

    first = _current(matter)
    recorded = ChangeEvent.objects.get(
        event_type=ChangeEventType.PROCEDURAL_DEVELOPMENT_RECORDED, object_id=result.record.pk
    )
    assert recorded.stage_episode_id == first.pk
    assert _episodes(matter) == [(1, "idea", True)]


def test_a_correction_saved_with_a_move_belongs_to_the_period_it_was_saved_in(specialist):
    """`Muuda teemat`: one save, one period — the move is made last."""
    matter = _matter(specialist, stage="idea")
    idea = _current(matter)

    with transaction.atomic(), stage_episode_scope():
        change_stage(matter=matter, stage=_stage("government"), actor=specialist)
        set_matter_title(matter=matter, value="Uus pealkiri", actor=specialist)

    renamed = _event(matter, ChangeEventType.MATTER_TITLE_CHANGED)
    assert renamed.stage_episode_id == idea.pk


def test_a_stage_only_change_draws_no_row(specialist):
    """Rule 9: the period boundary says it — no «Hetkeseis muutus» row."""
    matter = _matter(specialist, stage="idea")
    add_matter_note(matter=matter, author=specialist, body="Idee faasi märge.")
    add_procedural_development(
        matter=matter, author=specialist, title="", stage=_stage("consultation")
    )
    change_stage(matter=matter, stage=_stage("government"), actor=specialist)

    timeline = matter_episode_timeline(matter=matter, user=specialist)
    assert timeline is not None
    rows = [item for group in timeline.groups for item in group.items]
    assert not any(
        item.event is not None and item.event.event_type == ChangeEventType.MATTER_STAGE_CHANGED
        for item in rows
    )
    assert not any(item.procedural_development is not None for item in rows)
    assert [group.label for group in timeline.groups] == [
        "Valitsuses",
        "Kooskõlastusringil",
        "Idee",
    ]


# ---------------------------------------------------------------------------
# 10–13 — Teema käik, grouped
# ---------------------------------------------------------------------------


def test_the_page_draws_one_accordion_per_period_current_open(signed_in, specialist):
    matter = _matter(specialist, stage="idea")
    add_matter_note(matter=matter, author=specialist, body="Esimene idee märge.")
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    add_matter_note(matter=matter, author=specialist, body="Ringi märge.")
    change_stage(matter=matter, stage=_stage("government"), actor=specialist)
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()

    opened = re.findall(
        r'<details class="kaikstage[^"]*"\s+id="(etapp-[^"]+)"[^>]*?( open)?>', body
    )
    assert len(opened) == 4
    # Newest first, and only the current one drawn open.
    assert [bool(flag) for _id, flag in opened] == [True, False, False, False]
    # Two consultation rounds, two independently addressable accordions.
    assert len({anchor for anchor, _flag in opened}) == 4
    assert body.count('class="kaikstage__stage">Kooskõlastusringil<') == 2
    assert "2. kord" in body
    assert "kaikstage--current" in body
    # The rows are in their periods, newest period first.
    assert body.index("Ringi märge.") < body.index("Esimene idee märge.")


def test_a_period_heading_prints_only_what_is_known(specialist):
    matter = _matter(specialist, stage="idea")
    tallinn = timezone.get_current_timezone()
    episode = _current(matter)

    episode.started_at = dt.datetime(2026, 5, 4, 12, tzinfo=tallinn)
    assert period_label(episode) == "alates 5.26"
    episode.is_current = False
    episode.ended_at = dt.datetime(2026, 10, 2, 12, tzinfo=tallinn)
    assert period_label(episode) == "5.26–10.26"
    episode.ended_at = dt.datetime(2026, 5, 30, 12, tzinfo=tallinn)
    assert period_label(episode) == "5.26"
    episode.started_at = None
    episode.ended_at = dt.datetime(2026, 10, 2, 12, tzinfo=tallinn)
    assert period_label(episode) == "–10.26"
    episode.is_current = True
    episode.ended_at = None
    assert period_label(episode) == ""


def test_work_from_before_any_period_reads_as_earlier_activity(specialist):
    """No row is moved into today's stage; unbound work keeps no period."""
    matter = factories.MatterFactory(owner=specialist, stage=None)
    add_matter_note(matter=matter, author=specialist, body="Enne hetkeseisu.")
    change_stage(matter=matter, stage=_stage("idea"), actor=specialist)
    add_matter_note(matter=matter, author=specialist, body="Idee ajal.")

    timeline = matter_episode_timeline(matter=matter, user=specialist)
    assert timeline is not None
    labels = [group.label for group in timeline.groups]
    assert labels == ["Idee", EARLIER_ACTIVITY_LABEL]
    earlier = timeline.groups[1]
    assert earlier.episode is None and not earlier.is_open
    assert [item.entry.body for item in earlier.items if item.entry] == ["Enne hetkeseisu."]


def test_work_after_the_stage_was_cleared_reads_under_no_stage(specialist):
    matter = _matter(specialist, stage="idea")
    change_stage(matter=matter, stage=None, actor=specialist)
    add_matter_note(matter=matter, author=specialist, body="Hetkeseisuta.")

    timeline = matter_episode_timeline(matter=matter, user=specialist)
    assert timeline is not None
    top = timeline.groups[0]
    assert top.label == NO_STAGE_LABEL and top.is_open
    assert [item.entry.body for item in top.items if item.entry] == ["Hetkeseisuta."]


def test_a_matter_with_no_period_keeps_the_flat_chronology(specialist):
    matter = _matter(specialist)
    add_matter_note(matter=matter, author=specialist, body="Lihtne märge.")

    assert matter_episode_timeline(matter=matter, user=specialist) is None


# ---------------------------------------------------------------------------
# 14–20 — closure through the stage, and reopening
# ---------------------------------------------------------------------------


def test_joustumise_ootel_does_not_close(specialist):
    matter = _matter(specialist, stage="parliament")

    change_stage(matter=matter, stage=_stage("awaiting_entry"), actor=specialist)

    matter.refresh_from_db()
    assert matter.is_open and matter.disposition == ""


def test_joustunud_closes_as_completed_and_ends_the_open_step(specialist):
    matter = _matter(specialist, stage="awaiting_entry")
    step = set_next_action_for_new_work(
        matter=matter, text="Jälgin jõustumist", target_date=None, actor=specialist
    )

    add_procedural_development(
        matter=matter, author=specialist, title="Seadus jõustus", stage=_stage("in_force")
    )

    matter.refresh_from_db()
    assert not matter.is_open
    assert matter.disposition == Disposition.COMPLETED
    assert matter.closed_at is not None and matter.closed_by == specialist
    terminal = _current(matter)
    assert terminal.stage.key == "in_force"
    assert _event(matter, ChangeEventType.MATTER_CLOSED).stage_episode_id == terminal.pk
    step.refresh_from_db()
    assert step.status == ActionStatus.CANCELLED
    assert not NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).exists()
    assert check_domain_invariants().ok


def test_rohkem_ei_tegele_closes_as_monitoring_stopped(specialist):
    matter = _matter(specialist, stage="consultation")

    change_stage(matter=matter, stage=_stage("monitoring_stopped"), actor=specialist)

    matter.refresh_from_db()
    assert not matter.is_open
    assert matter.disposition == Disposition.MONITORING_STOPPED
    assert _current(matter).stage.key == "monitoring_stopped"


def test_a_terminal_stage_saved_with_a_step_is_refused_whole(specialist):
    matter = _matter(specialist, stage="parliament")

    with pytest.raises(DomainError, match=re.escape(TERMINAL_STAGE_MAKES_NO_STEP)):
        add_procedural_development(
            matter=matter,
            author=specialist,
            title="Seadus jõustus",
            stage=_stage("in_force"),
            next_text="Jälgin rakendamist",
        )

    matter.refresh_from_db()
    assert matter.is_open and matter.stage.key == "parliament"
    assert _episodes(matter) == [(1, "parliament", True)]


def test_a_future_marge_with_a_terminal_stage_makes_no_step(specialist):
    matter = _matter(specialist, stage="parliament")

    add_procedural_development(
        matter=matter,
        author=specialist,
        title="Seadus jõustub",
        occurred_on=timezone.localdate() + dt.timedelta(days=10),
        stage=_stage("in_force"),
        as_next_step=True,
    )

    matter.refresh_from_db()
    assert not matter.is_open
    assert not NextAction.objects.filter(matter=matter).exists()


def test_reopening_after_joustunud_opens_a_new_period_and_keeps_the_old(specialist):
    matter = _matter(specialist, stage="awaiting_entry")
    change_stage(matter=matter, stage=_stage("in_force"), actor=specialist)
    terminal = _current(matter)

    reopen_matter_into_stage(matter=matter, stage=_stage("idea"), actor=specialist)

    matter.refresh_from_db()
    assert matter.is_open and matter.disposition == "" and matter.closed_at is None
    assert matter.stage.key == "idea"
    terminal.refresh_from_db()
    assert terminal.stage.key == "in_force" and not terminal.is_current
    assert terminal.ended_at is not None
    assert _episodes(matter) == [
        (1, "awaiting_entry", False),
        (2, "in_force", False),
        (3, "idea", True),
    ]
    reopened = _event(matter, ChangeEventType.MATTER_REOPENED)
    assert reopened.stage_episode_id == _current(matter).pk
    assert check_domain_invariants().ok


def test_reopening_after_rohkem_ei_tegele_into_consultation(specialist):
    matter = _matter(specialist, stage="idea")
    change_stage(matter=matter, stage=_stage("monitoring_stopped"), actor=specialist)

    reopen_matter_into_stage(matter=matter, stage=_stage("consultation"), actor=specialist)

    matter.refresh_from_db()
    assert matter.is_open and matter.stage.key == "consultation"
    assert [key for _seq, key, _cur in _episodes(matter)] == [
        "idea",
        "monitoring_stopped",
        "consultation",
    ]


def test_reopening_needs_a_real_stage_that_does_not_close_again(specialist):
    matter = _matter(specialist, stage="idea")
    change_stage(matter=matter, stage=_stage("in_force"), actor=specialist)

    with pytest.raises(DomainError, match=REOPEN_NEEDS_A_STAGE):
        reopen_matter_into_stage(matter=matter, stage=None, actor=specialist)
    with pytest.raises(DomainError, match=re.escape(REOPEN_INTO_TERMINAL_STAGE)):
        reopen_matter_into_stage(
            matter=matter, stage=_stage("monitoring_stopped"), actor=specialist
        )

    matter.refresh_from_db()
    assert not matter.is_open
    offered = [stage.key for stage in offered_reopening_stages(matter)]
    assert "in_force" not in offered and "monitoring_stopped" not in offered
    assert offered[0] == "idea"
    form = ReopenForm(offered=offered_reopening_stages(matter))
    labels = [str(label) for value, label in form.fields["stage"].choices if value]
    assert labels[0] == "Idee"
    assert not any("lõpetab teema" in label for label in labels)
    # The form refuses a terminal stage even when it is posted by hand.
    posted = ReopenForm({"stage": str(_stage("in_force").pk)}, offered=[])
    assert not posted.is_valid()


def test_the_reopen_route_takes_the_stage(signed_in, specialist):
    matter = _matter(specialist, stage="idea")
    change_stage(matter=matter, stage=_stage("in_force"), actor=specialist)

    signed_in.post(reverse("matters:reopen", kwargs={"pk": matter.pk}), {})
    matter.refresh_from_db()
    assert not matter.is_open

    signed_in.post(
        reverse("matters:reopen", kwargs={"pk": matter.pk}),
        {"stage": str(_stage("consultation").pk)},
    )
    matter.refresh_from_db()
    assert matter.is_open and matter.stage.key == "consultation"


def test_a_closed_matter_is_offered_reopening_into_a_stage(signed_in, specialist):
    matter = _matter(specialist, stage="idea")
    change_stage(matter=matter, stage=_stage("in_force"), actor=specialist)

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()
    banner = re.search(r'<div class="banner banner--closed">.*?</form>', body, re.S).group(0)
    assert 'name="stage"' in banner and "Ava uuesti" in banner
    assert "Määramata" not in banner and "lõpetab teema" not in banner


def test_lopeta_teema_is_gone_from_every_ordinary_surface(signed_in, specialist):
    matter = _matter(specialist, stage="idea")

    with pytest.raises(NoReverseMatch):
        reverse("matters:close_from_workspace", kwargs={"pk": matter.pk})
    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()
    edit = signed_in.get(reverse("matters:matter_edit", kwargs={"pk": matter.pk})).content.decode()
    for page in (body, edit):
        assert "+ Lõpeta teema" not in page
        assert 'id="teema-lopeta"' not in page


def test_historical_rohkem_pole_tegevusi_plaanis_is_still_a_disposition():
    """Rule 20: the new stage is going forward only; history is not reread."""
    mapping = resolve_legacy_status("rohkem pole tegevusi plaanis")
    assert mapping is not None
    assert mapping.stage is None
    assert mapping.disposition == Disposition.MONITORING_STOPPED
    assert RAW_LABEL_TO_DISPOSITION == {"rohkem pole tegevusi plaanis": "MONITORING_STOPPED"}
    assert "monitoring_stopped" not in RAW_LABEL_TO_STAGE.values()


def test_uus_teema_in_a_terminal_stage_files_the_teema_closed(signed_in, specialist):
    response = signed_in.post(
        reverse("matters:matter_create"),
        {
            "title": "Juba jõustunud seadus",
            "stage": str(_stage("in_force").pk),
            "response_deadline": (timezone.localdate() + dt.timedelta(days=5)).strftime("%d.%m.%Y"),
        },
    )

    assert response.status_code == 302, response.content.decode()[:2000]
    from app.matters.models import Matter

    matter = Matter.objects.get(title="Juba jõustunud seadus")
    assert not matter.is_open and matter.disposition == Disposition.COMPLETED
    assert not NextAction.objects.filter(matter=matter).exists()
    assert _current(matter).stage.key == "in_force"


# ---------------------------------------------------------------------------
# Legacy: what the migration seeds, and what it does not invent
# ---------------------------------------------------------------------------


def test_the_seed_gives_active_staged_matters_a_period_with_no_start(specialist):
    seeded = importlib.import_module("app.matters.migrations.0043_seed_current_stage_episodes")
    active = factories.MatterFactory(owner=specialist, stage=_stage("government"))
    unstaged = factories.MatterFactory(owner=specialist, stage=None)
    closed = factories.MatterFactory(owner=specialist, stage=_stage("parliament"))
    close_matter(matter=closed, disposition=Disposition.OTHER, actor=specialist)
    note = add_matter_note(matter=active, author=specialist, body="Enne seemet.")
    assert check_domain_invariants().by_kind().get("stage-episode-missing")

    seeded.seed(django_apps, None)
    seeded.seed(django_apps, None)  # idempotent

    episode = MatterStageEpisode.objects.get(matter=active)
    assert episode.origin == StageEpisodeOrigin.CARRIED_OVER
    assert episode.started_at is None and episode.is_current
    assert not MatterStageEpisode.objects.filter(matter__in=[unstaged, closed]).exists()
    # Earlier work is not moved into today's stage.
    added = ChangeEvent.objects.get(event_type=ChangeEventType.ENTRY_ADDED, object_id=note.entry.pk)
    assert added.stage_episode_id is None
    assert check_domain_invariants().ok


def test_a_carried_over_joustunud_on_an_open_matter_is_not_a_finding(specialist):
    """No new invariant is made retroactively false for truthful legacy data."""
    matter = factories.MatterFactory(owner=specialist, stage=_stage("in_force"))
    MatterStageEpisode.objects.create(
        matter=matter, stage=matter.stage, sequence=1, origin=StageEpisodeOrigin.CARRIED_OVER
    )

    assert check_domain_invariants().ok


# ---------------------------------------------------------------------------
# 21–32 — the next-stage order
# ---------------------------------------------------------------------------


def _next(current, *, history=(), instruments=()):
    return next_stage_keys(
        current_key=current,
        history_keys=list(history),
        instrument_keys=list(instruments),
        available_keys=ALL_KEYS,
    )


def test_uus_teema_keeps_every_stage_and_the_soft_guidance():
    """21: `Uus teema` is not the next-stage picker; every stage, ADR 0130's dimming."""
    form = MatterCreateForm()
    keys = [stage.key for stage in form.fields["stage"].queryset]
    assert keys == ALL_KEYS


def test_muuda_teemat_keeps_the_general_vocabulary(specialist):
    """22: the correction surface offers every stage, in the vocabulary's order."""
    matter = _matter(specialist, stage="parliament")
    form = MatterEditForm(matter=matter, viewer=specialist)
    assert [stage.key for stage in form.fields["stage"].queryset] == ALL_KEYS


def test_a_domestic_file_is_not_offered_european_stages():
    offered = _next("consultation", history=["consultation", "idea"], instruments=["seadus"])

    assert offered[:3] == ["government", "parliament", "awaiting_entry"]
    assert not set(offered) & EU


def test_a_european_file_is_not_offered_domestic_stages():
    offered = _next("eu_procedure", history=["eu_procedure", "estonian_eu_position"])

    assert offered[:2] == ["awaiting_entry", "awaiting_transposition"]
    assert not set(offered) & DOMESTIC


def test_awaiting_transposition_offers_the_domestic_bridge_first():
    offered = _next(
        "awaiting_transposition",
        history=["awaiting_transposition", "awaiting_entry", "eu_procedure"],
        instruments=["direktiiv"],
    )

    assert offered[:2] == ["idea", "consultation"]


def test_crossing_the_bridge_makes_the_file_domestic(specialist):
    """26: «Idee» from «ELi õiguse ülevõtmise ootel», no domestic Õigusakt needed."""
    matter = _matter(specialist, stage="estonian_eu_position", instruments=("direktiiv",))
    for key in ("eu_procedure", "awaiting_entry", "awaiting_transposition"):
        change_stage(matter=matter, stage=_stage(key), actor=specialist)
    track_before = matter.track

    assert [stage.key for stage in offered_next_stages(matter)][:2] == ["idea", "consultation"]
    change_stage(matter=matter, stage=_stage("idea"), actor=specialist)

    offered = [stage.key for stage in offered_next_stages(matter)]
    assert offered[:3] == ["consultation", "government", "parliament"]
    matter.refresh_from_db()
    assert matter.track == track_before
    assert list(matter.legal_instruments.values_list("key", flat=True)) == ["direktiiv"]


def test_backward_comes_last_and_still_opens_a_new_period(specialist):
    offered = _next("parliament", history=["parliament", "government", "consultation", "idea"])
    assert offered.index("consultation") > offered.index("monitoring_stopped")

    matter = _matter(specialist, stage="parliament")
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    assert _episodes(matter) == [(1, "parliament", False), (2, "consultation", True)]


def test_a_stage_held_before_in_the_other_procedure_stays_reachable():
    offered = _next(
        "consultation",
        history=["consultation", "idea", "awaiting_transposition", "eu_procedure"],
    )

    assert "awaiting_transposition" in offered and "eu_procedure" in offered
    assert offered.index("eu_procedure") > offered.index("idea")


def test_the_current_stage_and_maaramata_are_never_offered():
    for current in ALL_KEYS:
        offered = _next(current, history=[current])
        assert current not in offered
        assert "" not in offered and None not in offered


def test_muu_and_rohkem_ei_tegele_are_always_offered():
    for current in ("idea", "eu_procedure", "awaiting_transposition", None):
        offered = _next(current, history=[current] if current else [])
        for key in COMMON_TAIL:
            assert key in offered


def test_with_nothing_to_go_on_every_stage_is_offered():
    assert flow_context(current_key=None, history_keys=[], instrument_keys=[]) is None
    assert _next(None) == ALL_KEYS
    assert flow_context(current_key="other", history_keys=[], instrument_keys=["seadus"]) == (
        "domestic"
    )


def test_reopening_never_offers_a_terminal_stage():
    offered = reopening_stage_keys(
        current_key="in_force",
        history_keys=["in_force", "awaiting_entry", "parliament"],
        instrument_keys=["seadus"],
        available_keys=ALL_KEYS,
    )

    assert offered[0] == "idea"
    assert "in_force" not in offered and "monitoring_stopped" not in offered


def test_the_marge_panel_offers_the_curated_order(signed_in, specialist):
    matter = _matter(specialist, stage="consultation")

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()
    select = re.search(r'<select name="stage"[^>]*id="id_marge_stage".*?</select>', body, re.S)
    assert select is not None
    labels = re.findall(r"<option[^>]*>([^<]+)</option>", select.group(0))
    assert labels[0] == "Jätan muutmata"
    assert labels[1:4] == ["Valitsuses", "Riigikogus", "Jõustumise ootel"]
    assert "Jõustunud — lõpetab teema" in labels
    assert "Rohkem ei tegele — lõpetab teema" in labels
    assert "Kooskõlastusringil" not in labels
    assert "ELi menetluses" not in labels


# ---------------------------------------------------------------------------
# 38–43 — the rail: procedure and the opinions that went out
# ---------------------------------------------------------------------------


def _labels(matter, user):
    return [step.label for step in process_steps(matter=matter, user=user)]


def test_a_sent_opinion_is_a_point_and_a_withdrawn_one_stays_one(specialist, capture_evidence):
    matter = _matter(specialist, stage="idea")
    version = capture_evidence(matter, b"%PDF-1.4 synthetic", "arvamus.pdf", "application/pdf")
    sent_at = timezone.now() - dt.timedelta(days=2)
    factories.SubmissionFactory(
        matter=matter, status=SubmissionStatus.SENT, sent_at=sent_at, final_version=version
    )
    withdrawn = factories.SubmissionFactory(
        matter=matter, status=SubmissionStatus.WITHDRAWN, sent_at=sent_at, final_version=version
    )
    factories.SubmissionFactory(matter=matter, status=SubmissionStatus.DRAFT)

    steps = process_steps(matter=matter, user=specialist)
    sent = [step for step in steps if step.label == SENT_LABEL]
    assert len(sent) == 2
    assert sorted(step.detail for step in sent) == ["", withdrawn.get_status_display()]


def test_operational_records_draw_no_point(specialist):
    matter = _matter(specialist, stage="idea")
    add_matter_note(matter=matter, author=specialist, body="Tavaline märge.")
    add_engagement(
        matter=matter,
        kind="OTHER",
        title="Liikmed",
        occurred_on=timezone.localdate(),
        feedback_deadline=timezone.localdate() + dt.timedelta(days=7),
        actor=specialist,
    )
    ahead = timezone.localdate() + dt.timedelta(days=30)
    add_important_date(
        matter=matter,
        actor=specialist,
        title="Riigikogu arutelu",
        date_value=ahead,
        period_end=ahead,
    )

    assert _labels(matter, specialist) == []


def test_the_procedures_own_points_stay(specialist):
    matter = _matter(specialist, stage="idea")
    matter.response_deadline = timezone.localdate() + dt.timedelta(days=10)
    matter.save(update_fields=["response_deadline", "updated_at"])
    ahead = timezone.localdate() + dt.timedelta(days=200)
    add_important_date(
        matter=matter,
        actor=specialist,
        title="Ülevõtmise tähtaeg",
        date_value=ahead,
        period_end=ahead,
        kind=ImportantDateKind.TRANSPOSITION_DEADLINE,
    )

    labels = _labels(matter, specialist)
    assert "Arvamuse tähtaeg" in labels
    assert TRANSPOSITION_DEADLINE_LABEL in labels


# ---------------------------------------------------------------------------
# 44–47 — terminology
# ---------------------------------------------------------------------------


def test_valdkond_reads_the_same_on_both_forms_and_stays_a_multi_select(specialist):
    matter = _matter(specialist)
    for form in (MatterCreateForm(), MatterEditForm(matter=matter, viewer=specialist)):
        field = form.fields["policy_areas"]
        assert field.label == "Valdkond"
        assert field.widget.allow_multiple_selected
    assert "policy_areas" in MatterEditForm(matter=matter, viewer=specialist).fields


def test_valdkond_and_arvamuse_tahtaeg_read_the_same_on_the_teema_page(signed_in, specialist):
    matter = _matter(specialist, stage="idea")
    matter.response_deadline = timezone.localdate() + dt.timedelta(days=10)
    matter.save(update_fields=["response_deadline", "updated_at"])

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()
    edit = signed_in.get(reverse("matters:matter_edit", kwargs={"pk": matter.pk})).content.decode()

    assert '<span class="metaline__label">Valdkond</span>' in body
    assert '<span class="metaline__label">Arvamuse tähtaeg</span>' in body
    assert ">Valdkonnad<" not in body and ">Valdkonnad<" not in edit
