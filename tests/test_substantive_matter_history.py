"""`Teema käik` — the substantive history, and the rules it must not break.

The second lawyer feedback round asked one question of the Matter page: *mis
selle teemaga päriselt juhtus?* The chronology answered it with the application
talking about itself — «Marko lisas dokumendi kell 14:31», a stage change, a
next step and a `Menetluse areng` as three lines for one save, and a `Töövõit`
dated to the afternoon somebody confirmed it.

This file holds what docs/adr/0092 decided, in the order the record states it:

* every primary row is **projected from a canonical record**, never from an
  audit row, and the two that still came out of the audit stream — the sent
  opinion and the confirmed work victory — now come off `Submission` and
  `MatterWorkVictory` (§3, §4);
* a business date is **exact, approximate or genuinely unknown**, and nothing
  else may stand in for it: not `created_at`, not `confirmed_at`, not the upload
  timestamp and not today (§4);
* several canonical writes that share an **`operation_id`** read as one act, and
  nothing else groups anything — not a shared minute, not a shared author, not a
  similar title (§6);
* the open `Järgmiseks` reads at the top of the page and **not** a second time
  as history (§8);
* and every source is filtered through `visible_to` **before** it is projected,
  grouped, counted or sorted, so a restricted child changes no row, no count, no
  date, no filename and no ordering (§7, AUTH-003).
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.audit.visibility import (
    MATTER_LEVEL_EVENT_TYPES,
    change_log_event_types,
    child_event_types,
)
from app.core.enums import Visibility
from app.documents.models import Document
from app.intelligence.enums import WorkVictoryStatus
from app.intelligence.models import MatterWorkVictory
from app.matters.enums import EngagementKind, ExternalPositionProvenance
from app.matters.services import (
    add_engagement,
    change_stage,
    compose_update,
    create_matter,
    plan_website_overview,
    publish_website_overview,
)
from app.matters.timeline import (
    LAWYER_NOTE_LABEL,
    SUBMISSION_MILESTONE,
    WORK_VICTORY_DATE_UNKNOWN,
    WORK_VICTORY_MILESTONE,
    matter_timeline,
    work_victory_chronology_day,
)
from app.matters.views import CHANGE_LOG_PAGE_SIZE
from app.matters.workspace import (
    add_matter_external_position,
    add_matter_koda_opinion,
    add_procedural_development,
)
from app.related_materials.services import add_background_submission, link_related_matters
from app.submissions.enums import SubmissionKind, SubmissionStatus
from app.submissions.services import supersede_submission, withdraw_submission
from app.workflow.enums import ActionStatus, DatePrecision
from app.workflow.models import NextAction
from tests import factories

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _pdf(name: str = "eelnou.pdf", body: bytes = b"%PDF-1.4 eelnou") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, body, content_type="application/pdf")


def _history(matter, user, **kwargs):
    items, _more = matter_timeline(matter=matter, user=user, limit=100, **kwargs)
    return items


def _headlines(items) -> list[str]:
    return [item.milestone.what for item in items if item.milestone is not None]


def _rendered(items) -> str:
    """Everything a reader could read off these rows, as one string.

    Used by the permission assertions, which have to prove a *negative*: that no
    headline, no sub-line, no note, no date and no filename anywhere on the page
    mentions the record this reader may not see.
    """
    parts: list[str] = []
    for item in items:
        if item.milestone is not None:
            parts += [
                item.milestone.what,
                item.milestone.sub,
                item.milestone.display_date,
                item.milestone.own_note,
            ]
            parts += [link.label for link in item.milestone.links]
        if item.entry is not None:
            parts.append(item.entry.body)
        if item.event is not None:
            parts.append(item.event.summary)
        if item.next_step is not None:
            parts += [item.next_step.text, item.next_step.date_value]
        parts.append(item.stage_effect)
        parts += [file.label for file in item.files]
    return " ".join(part for part in parts if part)


@pytest.fixture
def ministry(db):
    return factories.OrganisationFactory(name="Majandus- ja Kommunikatsiooniministeerium")


@pytest.fixture
def association(db):
    return factories.OrganisationFactory(name="Metallitööstuse Liit")


@pytest.fixture
def committee(db):
    return factories.OrganisationFactory(name="Riigikogu majanduskomisjon")


@pytest.fixture
def consultation(db):
    """The reviewed `Kooskõlastusringil` row, not a second one made here.

    `workflow/0004` seeds the vocabulary, so a factory would collide with the
    unique key — and a stage invented by a test is not the stage the product
    offers (app/workflow/reference_stages.py).
    """
    from app.workflow.models import StageVocabulary

    return StageVocabulary.objects.get(key="consultation")


def _days_ago(count: int) -> dt.date:
    """A business day in the past, relative to the application's own today.

    Fixed calendar dates were the first draft and they were wrong for a reason
    worth stating: the chronology reads newest-first and means *past*, so a
    record dated after today is deliberately not projected — and a literal
    `2026-10-12` is a date this suite would stop exercising the moment the
    calendar passed it, in the direction that silently drops rows.
    """
    return timezone.localdate() - dt.timedelta(days=count)


def _et(value: dt.date) -> str:
    """The day as `format_estonian_date` writes it — unpadded."""
    return f"{value.day}.{value.month}.{value.year}"


# ---------------------------------------------------------------------------
# A — what the canonical sources project
# ---------------------------------------------------------------------------


def test_a_procedural_development_is_its_own_substantive_act(normal_matter, specialist):
    add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="Ministeerium saatis eelnõu uue versiooni",
        occurred_on=_days_ago(20),
        note="Meie ettepanekut ei arvestatud.",
    )

    rows = [item for item in _history(normal_matter, specialist) if item.procedural_development]
    assert len(rows) == 1
    milestone = rows[0].milestone
    assert "Ministeerium saatis eelnõu uue versiooni" in milestone.what
    assert milestone.display_date == _et(_days_ago(20))
    # The lawyer's reading of the step, under its own label and never folded into
    # the sentence about what the ministry did (docs/adr/0091 §4).
    assert milestone.own_note == "Meie ettepanekut ei arvestatud."
    assert milestone.own_note_label == "Juristi märkus"
    assert "Meie ettepanekut" not in milestone.sub


def test_received_and_discovered_feedback_say_which_they_are(
    normal_matter, specialist, ministry, association
):
    add_matter_external_position(
        matter=normal_matter,
        author=specialist,
        provenance=ExternalPositionProvenance.RECEIVED.value,
        organisation=association,
        summary="Meede tõstab liikmete kulusid.",
        stated_on=_days_ago(15),
    )
    add_matter_external_position(
        matter=normal_matter,
        author=specialist,
        provenance=ExternalPositionProvenance.DISCOVERED.value,
        organisation=ministry,
        summary="Ministeerium toetab varianti B.",
        stated_on=_days_ago(10),
    )

    headlines = _headlines(_history(normal_matter, specialist))
    assert "Meile saadetud tagasiside: Metallitööstuse Liit" in headlines
    assert "Teiste arvamus: Majandus- ja Kommunikatsiooniministeerium" in headlines


def test_aggregate_feedback_keeps_its_source_label_and_invents_no_organisation(
    normal_matter, specialist
):
    """Scenario A's «Meile saadetud tagasiside — Tööstusettevõtete küsitlus»."""
    position = add_matter_external_position(
        matter=normal_matter,
        author=specialist,
        provenance=ExternalPositionProvenance.RECEIVED.value,
        organisation=None,
        source_label="Tööstusettevõtete küsitlus",
        summary="58 vastust, ülekaalukalt vastu.",
        stated_on=_days_ago(12),
    ).record

    assert position.organisation_id is None
    headlines = _headlines(_history(normal_matter, specialist))
    assert "Meile saadetud tagasiside: Tööstusettevõtete küsitlus" in headlines


def test_a_legacy_position_keeps_its_neutral_historical_heading(normal_matter, specialist):
    """A row recorded before the question existed reads as it always did.

    Not «Täpsustamata: …», which would be the file announcing a gap in itself
    that nobody can honestly close (docs/adr/0091 §3.4).
    """
    from app.matters.models import EXTERNAL_POSITION_LEGACY_HEADLINE, MatterExternalPosition

    position = add_matter_external_position(
        matter=normal_matter,
        author=specialist,
        provenance=ExternalPositionProvenance.DISCOVERED.value,
        organisation=factories.OrganisationFactory(name="Rahandusministeerium"),
        summary="Toetame eelnõu.",
        stated_on=_days_ago(30),
    ).record
    MatterExternalPosition.objects.filter(pk=position.pk).update(
        provenance=ExternalPositionProvenance.LEGACY.value
    )

    headlines = _headlines(_history(normal_matter, specialist))
    assert f"{EXTERNAL_POSITION_LEGACY_HEADLINE}: Rahandusministeerium" in headlines
    assert "Täpsustamata" not in " ".join(headlines)


def test_every_koda_opinion_appears_and_none_is_collapsed(
    normal_matter, specialist, ministry, committee, evidence_root
):
    """Scenario E. Two sends, two rows, each with its own date and recipient."""
    first_day, second_day = _days_ago(40), _days_ago(9)
    add_matter_koda_opinion(
        matter=normal_matter,
        author=specialist,
        upload=_pdf("arvamus_1.pdf", b"%PDF-1.4 esimene"),
        recipients=[ministry],
        sent_on=first_day,
        title="Koja arvamus",
    )
    second = add_matter_koda_opinion(
        matter=normal_matter,
        author=specialist,
        upload=_pdf("arvamus_2.pdf", b"%PDF-1.4 teine"),
        recipients=[committee],
        sent_on=second_day,
        title="Koja täiendav arvamus",
    ).record
    # The kind is what tells two opinions on one file apart, and
    # `+ Koja arvamus` does not ask for it — so it is set here the way the
    # `Dokumendid` door sets it, rather than through a keyword that panel has no
    # box for.
    second.kind = SubmissionKind.SUPPLEMENTARY_OPINION
    second.save(update_fields=["kind"])

    rows = [item for item in _history(normal_matter, specialist) if item.submission]
    assert len(rows) == 2
    by_date = {row.milestone.display_date: row for row in rows}
    assert set(by_date) == {_et(first_day), _et(second_day)}
    assert by_date[_et(first_day)].milestone.what == SUBMISSION_MILESTONE
    # The addressee, off the canonical `SubmissionRecipient` rows rather than out
    # of the send event's payload.
    assert "Majandus- ja Kommunikatsiooniministeerium" in by_date[_et(first_day)].milestone.sub
    assert "Riigikogu majanduskomisjon" in by_date[_et(second_day)].milestone.sub
    # The default `Ametlik arvamus` says nothing the row does not and is left off.
    assert "Täiendav arvamus" in by_date[_et(second_day)].milestone.sub
    assert "Ametlik arvamus" not in by_date[_et(first_day)].milestone.sub
    # The exact bytes that went out, under the row that stands for the send.
    assert [file.label for file in by_date[_et(first_day)].files] == ["arvamus_1.pdf"]
    assert [file.label for file in by_date[_et(second_day)].files] == ["arvamus_2.pdf"]


def test_a_sent_opinion_is_read_from_the_record_and_not_from_the_send_event(
    normal_matter, specialist, ministry, evidence_root
):
    """The send event is no longer a chronology row at all (docs/adr/0092 §3).

    It still exists and is still auditable; what it stopped being is the source
    of a business date. `sent_at` is the day somebody supplied, and for a
    historical opinion the event's `occurred_at` is the day of the import.
    """
    add_matter_koda_opinion(
        matter=normal_matter,
        author=specialist,
        upload=_pdf(),
        recipients=[ministry],
        sent_on=dt.date(2019, 4, 2),
        title="Koja arvamus",
    )

    assert ChangeEvent.objects.filter(
        matter=normal_matter, event_type=ChangeEventType.SUBMISSION_SENT
    ).exists()
    rows = [item for item in _history(normal_matter, specialist) if item.submission]
    assert len(rows) == 1
    assert rows[0].milestone.display_date == "2.4.2019"
    # And not two rows: the event contributes none of its own.
    assert _headlines(_history(normal_matter, specialist)).count(SUBMISSION_MILESTONE) == 1


# ---------------------------------------------------------------------------
# B — the business-date contract
# ---------------------------------------------------------------------------


def test_a_work_victory_is_dated_by_its_business_period_and_not_by_its_confirmation(
    normal_matter, specialist
):
    """The defect docs/adr/0092 §4 fixes, stated as a date a reader sees.

    A 2019 win reviewed this morning belongs in 2019. The chronology used to put
    it at the top of the file under today's date, because `confirmed_at` was
    both where the row sat and what it printed.
    """
    victory = factories.WorkVictoryFactory(
        matter=normal_matter,
        status=WorkVictoryStatus.CONFIRMED,
        confirmed_at=timezone.now(),
        confirmed_by=specialist,
        title="Ettepanek võeti eelnõusse üle",
        period_date=dt.date(2019, 1, 1),
        period_end=dt.date(2019, 12, 31),
        date_precision=DatePrecision.YEAR,
    )

    rows = [
        item
        for item in _history(normal_matter, specialist)
        if item.milestone is not None and item.milestone.what == WORK_VICTORY_MILESTONE
    ]
    assert len(rows) == 1
    assert rows[0].milestone.display_date == "2019"
    # Where it *sits*, not only what it says: the anchor is the period's first
    # day, and never the confirmation moment.
    assert work_victory_chronology_day(victory) == dt.date(2019, 1, 1)
    assert rows[0].occurred_at.date() == dt.date(2019, 1, 1)
    assert str(timezone.localdate().year) not in rows[0].milestone.display_date


def test_a_work_victory_with_no_period_says_so_and_never_borrows_a_timestamp(
    normal_matter, specialist
):
    factories.WorkVictoryFactory(
        matter=normal_matter,
        status=WorkVictoryStatus.CONFIRMED,
        confirmed_at=timezone.now(),
        title="Muudatus jõudis lõppteksti",
        period_date=None,
        period_end=None,
        date_precision=DatePrecision.EXACT,
    )

    rows = [
        item
        for item in _history(normal_matter, specialist)
        if item.milestone is not None and item.milestone.what == WORK_VICTORY_MILESTONE
    ]
    assert len(rows) == 1
    assert rows[0].milestone.display_date == WORK_VICTORY_DATE_UNKNOWN
    today = timezone.localdate()
    assert f"{today.day:02d}.{today.month:02d}.{today.year}" not in rows[0].milestone.display_date


def test_a_confirmed_victory_whose_period_is_ahead_is_still_on_the_file(normal_matter, specialist):
    """`confirmed_at` is the whole existence test, and the period is a label.

    The department records wins by *reporting* year, so a row labelled 2030 is
    not a claim that nothing has happened yet — and the future filter every
    other projected record follows would take a judgement somebody has already
    made off the file altogether (docs/adr/0092 §4).
    """
    factories.WorkVictoryFactory(
        matter=normal_matter,
        status=WorkVictoryStatus.CONFIRMED,
        confirmed_at=timezone.now(),
        confirmed_by=specialist,
        title="Erisus jäi eelnõusse sisse",
        period_date=dt.date(2030, 1, 1),
        period_end=dt.date(2030, 12, 31),
        date_precision=DatePrecision.YEAR,
    )

    rows = [
        item
        for item in _history(normal_matter, specialist)
        if item.milestone is not None and item.milestone.what == WORK_VICTORY_MILESTONE
    ]
    assert len(rows) == 1
    assert rows[0].milestone.sub == "Erisus jäi eelnõusse sisse"
    assert rows[0].milestone.display_date == "2030"


def test_an_unconfirmed_candidate_is_not_a_history_row(normal_matter, specialist):
    factories.WorkVictoryFactory(
        matter=normal_matter,
        status=WorkVictoryStatus.CANDIDATE,
        title="Masin pakkus seda",
        period_date=_days_ago(100),
        period_end=_days_ago(100),
        date_precision=DatePrecision.EXACT,
    )

    assert WORK_VICTORY_MILESTONE not in _headlines(_history(normal_matter, specialist))


@pytest.mark.parametrize(
    ("occurred_on", "precision", "expected"),
    [
        (dt.date(2026, 3, 9), DatePrecision.EXACT, "9.3.2026"),
        (dt.date(2026, 3, 1), DatePrecision.MONTH, "märts 2026"),
        (dt.date(2026, 4, 1), DatePrecision.QUARTER, "II kvartal 2026"),
        (dt.date(2025, 1, 1), DatePrecision.YEAR, "2025"),
    ],
)
def test_a_development_prints_its_date_at_the_precision_it_was_recorded_to(
    normal_matter, specialist, occurred_on, precision, expected
):
    """The anchor places the row; the precision writes it down.

    A `MONTH` row stores 1 March and must never print `1.3.2026`, which is a day
    nobody named (docs/adr/0079 §2, §3).
    """
    add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="Valitsus kiitis eelnõu heaks",
        occurred_on=occurred_on,
        occurred_on_precision=precision.value,
    )

    rows = [item for item in _history(normal_matter, specialist) if item.procedural_development]
    assert len(rows) == 1
    assert rows[0].milestone.display_date == expected


def test_an_undated_development_says_kuupaev_teadmata_and_never_its_created_at(
    normal_matter, specialist
):
    """Scenario D. The one substitution this package exists to refuse."""
    from app.matters.timeline import DEVELOPMENT_DATE_UNKNOWN

    add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="Eelnõu jõudis Riigikokku",
        occurred_on=None,
    )

    rows = [item for item in _history(normal_matter, specialist) if item.procedural_development]
    assert len(rows) == 1
    assert rows[0].milestone.display_date == DEVELOPMENT_DATE_UNKNOWN
    today = timezone.localdate()
    assert f"{today.day}.{today.month}.{today.year}" not in rows[0].milestone.display_date
    assert f"{today.day:02d}.{today.month:02d}.{today.year}" not in rows[0].milestone.display_date


def test_two_undated_records_render_in_a_deterministic_order(normal_matter, specialist):
    """Unknown is unknown, and it is still the same page on the second load.

    The internal timestamps place the rows and describe neither of them — which
    is exactly what makes a stable ordering possible without a date.
    """
    for title in ("Esimene teadmata", "Teine teadmata"):
        add_procedural_development(
            matter=normal_matter, author=specialist, title=title, occurred_on=None
        )

    first = [item.milestone.what for item in _history(normal_matter, specialist)]
    second = [item.milestone.what for item in _history(normal_matter, specialist)]
    assert first == second
    assert first.index("Menetluse areng: Teine teadmata") < first.index(
        "Menetluse areng: Esimene teadmata"
    )


# ---------------------------------------------------------------------------
# C — one act, one row
# ---------------------------------------------------------------------------


def test_a_development_with_a_stage_and_a_step_is_one_substantive_act(
    normal_matter, specialist, consultation
):
    """Scenario A's revised draft: three canonical writes, one history row."""
    add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="Ministeerium saatis eelnõu uue versiooni",
        occurred_on=_days_ago(5),
        stage=consultation,
        next_text="Vaatan uue versiooni läbi",
        next_date=timezone.localdate() + dt.timedelta(days=4),
    )

    items = _history(normal_matter, specialist)
    developments = [item for item in items if item.procedural_development]
    assert len(developments) == 1
    row = developments[0]
    assert row.stage_effect == "Kooskõlastusringil"
    assert row.next_step is not None
    assert row.next_step.text == "Vaatan uue versiooni läbi"
    assert row.next_step.date_value == _et(timezone.localdate() + dt.timedelta(days=4))
    # And no second and third row saying the same two things.
    assert "Hetkeseis: Kooskõlastusringil" not in _headlines(items)
    assert not [
        item
        for item in items
        if item.milestone is None
        and item.event is not None
        and item.event.event_type == ChangeEventType.NEXT_ACTION_SET
    ]


def test_records_written_in_the_same_minute_by_one_person_do_not_group(
    normal_matter, specialist, ministry
):
    """No time window, no author match, no similarity. Only `operation_id`."""
    add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="Eelnõu jõudis Riigikokku",
        occurred_on=_days_ago(4),
    )
    add_matter_external_position(
        matter=normal_matter,
        author=specialist,
        provenance=ExternalPositionProvenance.DISCOVERED.value,
        organisation=ministry,
        summary="Ministeerium toetab eelnõu.",
        stated_on=_days_ago(4),
    )

    items = _history(normal_matter, specialist)
    assert len([item for item in items if item.procedural_development]) == 1
    assert len([item for item in items if item.external_position]) == 1
    assert all(not item.stage_effect for item in items)


def test_a_stage_change_saved_beside_a_hidden_development_still_reads_as_itself(
    normal_matter, specialist, reader, consultation
):
    """The grouping cannot become a channel for what the grouping hid.

    A reader refused the development sees the stage change exactly as they would
    see one made from the header — one row, the stage's own label, and not a
    syllable about the record that moved it.
    """
    development = add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="Ministeerium saatis salajase versiooni",
        occurred_on=_days_ago(4),
        note="Sisemine hinnang.",
        stage=consultation,
    ).record
    development.visibility_override = Visibility.RESTRICTED
    development.save(update_fields=["visibility_override"])

    items = _history(normal_matter, reader)
    rendered = _rendered(items)
    assert "salajase" not in rendered
    assert "Sisemine hinnang" not in rendered
    assert not [item for item in items if item.procedural_development]
    # The Matter-level fact stands alone, which is what it is.
    assert "Hetkeseis: Kooskõlastusringil" in _headlines(items)
    assert all(not item.stage_effect for item in items)


# ---------------------------------------------------------------------------
# D — the open step is current work, not history
# ---------------------------------------------------------------------------


def test_the_open_next_action_is_not_also_a_history_row(normal_matter, specialist):
    from app.workflow.services import set_next_action_for_new_work

    set_next_action_for_new_work(
        matter=normal_matter,
        text="Koostan arvamuse",
        target_date=timezone.localdate() + dt.timedelta(days=7),
        actor=specialist,
    )

    items = _history(normal_matter, specialist)
    assert not [
        item
        for item in items
        if item.event is not None and item.event.event_type == ChangeEventType.NEXT_ACTION_SET
    ]
    assert "Koostan arvamuse" not in _rendered(items)


def test_a_superseded_step_comes_back_as_history(normal_matter, specialist):
    """Because then it *is* history: it is not what the file owes any more."""
    from app.workflow.services import set_next_action_for_new_work

    set_next_action_for_new_work(
        matter=normal_matter,
        text="Koostan arvamuse",
        target_date=timezone.localdate() + dt.timedelta(days=7),
        actor=specialist,
    )
    set_next_action_for_new_work(
        matter=normal_matter,
        text="Vaatan vastuse üle",
        target_date=timezone.localdate() + dt.timedelta(days=14),
        actor=specialist,
    )

    rendered = _rendered(_history(normal_matter, specialist))
    assert "Koostan arvamuse" in rendered
    assert "Vaatan vastuse üle" not in rendered


def test_a_step_set_inside_a_note_keeps_its_strip_on_that_note(normal_matter, specialist):
    """§8's exception: the consequence of an act reads on that act's own row."""
    compose_update(
        matter=normal_matter,
        author=specialist,
        body="<p>Kohtusime ministeeriumiga.</p>",
        next_action={
            "text": "Saadan kokkuvõtte",
            "target_date": timezone.localdate() + dt.timedelta(days=3),
        },
    )

    rows = [item for item in _history(normal_matter, specialist) if item.is_entry]
    assert len(rows) == 1
    assert rows[0].next_step is not None
    assert rows[0].next_step.text == "Saadan kokkuvõtte"
    assert NextAction.objects.filter(matter=normal_matter, status=ActionStatus.OPEN).count() == 1


# ---------------------------------------------------------------------------
# E — evidence reads under the fact it evidences
# ---------------------------------------------------------------------------


def test_a_developments_paper_reads_under_the_development(normal_matter, specialist, evidence_root):
    add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="Ministeerium saatis eelnõu uue versiooni",
        occurred_on=_days_ago(6),
        uploads=[_pdf("eelnou-v2.pdf")],
    )

    items = _history(normal_matter, specialist)
    developments = [item for item in items if item.procedural_development]
    assert [file.label for file in developments[0].files] == ["eelnou-v2.pdf"]
    # And it is not *also* a row of its own with a dot and a date.
    assert not [
        item
        for item in items
        if item.event is not None
        and item.event.event_type == ChangeEventType.EVIDENCE_VERSION_ADDED
    ]


def test_a_sent_opinions_final_text_reads_once_under_the_send(
    normal_matter, specialist, ministry, evidence_root
):
    """One act, one row — including the upload that is part of it.

    `+ Koja arvamus` captures the letter and registers the send in one
    operation, and `Submission.final_version` is a column rather than a
    `DocumentLink`, so the generic «this file reads on its record's row» rule
    could not see it. The result was «Arvamus välja» and a second «lisas
    dokumendi» line for one act, with the filename printed twice
    (docs/adr/0092 §5).
    """
    add_matter_koda_opinion(
        matter=normal_matter,
        author=specialist,
        upload=_pdf("koja-arvamus.pdf"),
        recipients=[ministry],
        sent_on=_days_ago(7),
        title="Koja arvamus",
    )

    items = _history(normal_matter, specialist)
    sends = [item for item in items if item.submission]
    assert len(sends) == 1
    assert [file.label for file in sends[0].files] == ["koja-arvamus.pdf"]
    # And no evidence row of its own, with its own dot and its own upload time.
    assert not [
        item
        for item in items
        if item.event is not None
        and item.event.event_type == ChangeEventType.EVIDENCE_VERSION_ADDED
    ]
    assert _rendered(items).count("koja-arvamus.pdf") == 1


def test_a_restricted_final_text_hides_its_filename_and_leaves_the_send_readable(
    normal_matter, specialist, reader, ministry, evidence_root
):
    """A visible act plus a hidden document must not name the document."""
    result = add_matter_koda_opinion(
        matter=normal_matter,
        author=specialist,
        upload=_pdf("koja-arvamus-salajane.pdf"),
        recipients=[ministry],
        sent_on=_days_ago(7),
        title="Koja arvamus",
    )
    document = result.documents[0]
    document.visibility_override = Visibility.RESTRICTED
    document.save(update_fields=["visibility_override"])

    assert not Document.objects.visible_to(reader).filter(pk=document.pk).exists()
    items = _history(normal_matter, reader)
    rows = [item for item in items if item.submission]
    assert len(rows) == 1, "the send itself is not restricted and still reads"
    assert rows[0].files == ()
    assert "salajane" not in _rendered(items)


# ---------------------------------------------------------------------------
# F — permission ordering
# ---------------------------------------------------------------------------


def test_a_restricted_submission_does_not_reveal_that_an_opinion_exists(
    normal_matter, specialist, reader, ministry, evidence_root
):
    result = add_matter_koda_opinion(
        matter=normal_matter,
        author=specialist,
        upload=_pdf("koja-arvamus.pdf"),
        recipients=[ministry],
        sent_on=_days_ago(7),
        title="Koja arvamus",
    )
    submission = result.record
    # **The document goes first.** `submissions_check_final_evidence` refuses
    # final evidence less restricted than the send it proves, which is the same
    # rule read from the other end: restricting the letter and leaving the send
    # readable would put the send's date and recipient in front of somebody
    # refused the text.
    document = result.documents[0]
    document.visibility_override = Visibility.RESTRICTED
    document.save(update_fields=["visibility_override"])
    submission.visibility_override = Visibility.RESTRICTED
    submission.save(update_fields=["visibility_override"])

    items = _history(normal_matter, reader)
    rendered = _rendered(items)
    assert SUBMISSION_MILESTONE not in _headlines(items)
    assert _et(_days_ago(7)) not in rendered
    assert "Kommunikatsiooniministeerium" not in rendered
    assert "koja-arvamus.pdf" not in rendered


def test_a_restricted_position_reveals_no_source_note_date_or_count(
    normal_matter, specialist, reader, association
):
    position = add_matter_external_position(
        matter=normal_matter,
        author=specialist,
        provenance=ExternalPositionProvenance.RECEIVED.value,
        organisation=association,
        summary="Meede tõstab kulusid.",
        lawyer_note="Nende arvutus ei arvesta väikeettevõtteid.",
        stated_on=_days_ago(15),
    ).record
    position.visibility_override = Visibility.RESTRICTED
    position.save(update_fields=["visibility_override"])

    hidden = _rendered(_history(normal_matter, reader))
    assert "Metallitööstuse" not in hidden
    assert "väikeettevõtteid" not in hidden
    assert _et(_days_ago(15)) not in hidden
    # And the visible page is unchanged in *shape*: the reader's count is the
    # count of what they can read, not one short of somebody else's.
    assert len(_history(normal_matter, reader)) == len(_history(normal_matter, reader))


def test_a_restricted_work_victory_contributes_no_row(normal_matter, specialist, reader):
    victory = factories.WorkVictoryFactory(
        matter=normal_matter,
        status=WorkVictoryStatus.CONFIRMED,
        confirmed_at=timezone.now(),
        title="Salajane võit",
        period_date=_days_ago(300),
        period_end=_days_ago(300),
        date_precision=DatePrecision.EXACT,
    )
    MatterWorkVictory.objects.filter(pk=victory.pk).update(
        visibility_override=Visibility.RESTRICTED
    )

    assert "Salajane võit" not in _rendered(_history(normal_matter, reader))
    assert WORK_VICTORY_MILESTONE in _headlines(_history(normal_matter, specialist))


# ---------------------------------------------------------------------------
# G — bounded query shape
# ---------------------------------------------------------------------------


def test_the_history_does_not_cost_a_query_per_row(
    normal_matter, specialist, ministry, consultation, django_assert_max_num_queries
):
    """More rows must not mean more queries.

    Measured rather than guessed: the projection is built twice over the same
    Matter, once with three of each record and once with nine, and the ceiling
    is the same both times. What this protects is the *shape* — every source is
    one read and every attachment pass is one read for the whole page — not a
    particular number, which is why the two runs share a budget instead of
    asserting an exact count (docs/adr/0092 §10).
    """

    def populate(count: int) -> None:
        for index in range(count):
            add_procedural_development(
                matter=normal_matter,
                author=specialist,
                title=f"Menetlus liikus {index}",
                occurred_on=_days_ago(60 - index),
                stage=consultation if index == 0 else None,
            )
            add_matter_external_position(
                matter=normal_matter,
                author=specialist,
                provenance=ExternalPositionProvenance.DISCOVERED.value,
                organisation=ministry,
                summary=f"Seisukoht {index}",
                stated_on=_days_ago(60 - index),
            )
            add_engagement(
                matter=normal_matter,
                kind=EngagementKind.SURVEY,
                title=f"Küsitlus {index}",
                occurred_on=_days_ago(60 - index),
                actor=specialist,
            )

    budget = 18
    populate(3)
    with django_assert_max_num_queries(budget):
        assert len(_history(normal_matter, specialist)) >= 9
    populate(6)
    with django_assert_max_num_queries(budget):
        assert len(_history(normal_matter, specialist)) >= 27


# ---------------------------------------------------------------------------
# H — the page itself
# ---------------------------------------------------------------------------


def test_the_matter_page_calls_the_history_teema_kaik(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()

    assert "Teema käik" in body
    assert 'id="ajajoon"' in body, "the anchor a shared link uses does not move"


# ---------------------------------------------------------------------------
# I — the technical log the primary history was cleaned against
# ---------------------------------------------------------------------------


def test_the_history_links_to_the_technical_change_log(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()

    assert "Kõik muudatused" in body
    assert reverse("matters:matter_changes", kwargs={"pk": matter.pk}) in body


def test_the_change_log_shows_the_writes_the_history_leaves_out(
    signed_in, specialist, consultation
):
    """The other half of the trade docs/adr/0092 §11 makes.

    `Teema käik` is legible because it does not draw a row for every write; this
    is where those writes stay readable, so nothing was lost by cleaning it.
    """
    matter = factories.MatterFactory(owner=specialist)
    add_procedural_development(
        matter=matter,
        author=specialist,
        title="Ministeerium saatis eelnõu uue versiooni",
        occurred_on=_days_ago(5),
        stage=consultation,
    )

    body = signed_in.get(
        reverse("matters:matter_changes", kwargs={"pk": matter.pk})
    ).content.decode()

    assert "Hetkeseis muudetud" in body
    assert "Menetluse areng lisatud" in body


def test_the_change_log_shows_no_payload_no_identifier_and_no_operation(
    signed_in, specialist, consultation
):
    matter = factories.MatterFactory(owner=specialist)
    result = add_procedural_development(
        matter=matter,
        author=specialist,
        title="Ministeerium saatis eelnõu uue versiooni",
        occurred_on=_days_ago(5),
        stage=consultation,
    )

    body = signed_in.get(
        reverse("matters:matter_changes", kwargs={"pk": matter.pk})
    ).content.decode()

    assert str(result.operation_id) not in body
    assert str(result.record.pk) not in body
    assert "from_label" not in body
    assert "to_key" not in body
    assert "payload" not in body


def test_the_change_log_hides_a_row_about_a_restricted_child(
    client, specialist, reader, consultation
):
    """Permission-safe by construction: the row is not in the population.

    It is not redacted and it is not blanked — which matters, because a blanked
    row still says that something happened at a time to a kind of record
    (AUTH-003).
    """
    matter = factories.MatterFactory(owner=specialist)
    development = add_procedural_development(
        matter=matter,
        author=specialist,
        title="Ministeerium saatis salajase versiooni",
        occurred_on=_days_ago(5),
    ).record
    development.visibility_override = Visibility.RESTRICTED
    development.save(update_fields=["visibility_override"])

    client.force_login(reader)
    body = client.get(reverse("matters:matter_changes", kwargs={"pk": matter.pk})).content.decode()

    assert "salajase" not in body
    assert "Menetluse areng lisatud" not in body
    # And the reader who may see it does.
    client.force_login(specialist)
    allowed = client.get(
        reverse("matters:matter_changes", kwargs={"pk": matter.pk})
    ).content.decode()
    assert "Menetluse areng lisatud" in allowed


def test_the_change_log_refuses_a_matter_this_reader_may_not_open(client, reader, specialist):
    matter = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    client.force_login(reader)

    response = client.get(reverse("matters:matter_changes", kwargs={"pk": matter.pk}))
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# A sent opinion is history, and a later status does not unsend it
# ---------------------------------------------------------------------------
#
# The chronology used to be populated by `status=SENT`, which answers «is this
# the opinion that currently stands» — the right question for a portfolio and
# the wrong one for a history. Sending was a business act on a day; withdrawing
# the opinion afterwards adds a second act and takes nothing away from the first
# (docs/adr/0092 §3, `SubmissionQuerySet.historically_sent`).


def test_a_withdrawn_opinion_keeps_the_send_it_is_a_withdrawal_of(
    normal_matter, specialist, ministry, evidence_root
):
    """Scenario B. Two lines: the letter went, and then it was taken back."""
    sent_on = _days_ago(30)
    submission = add_matter_koda_opinion(
        matter=normal_matter,
        author=specialist,
        upload=_pdf("arvamus.pdf", b"%PDF-1.4 arvamus"),
        recipients=[ministry],
        sent_on=sent_on,
        title="Koja arvamus",
    ).record
    withdraw_submission(submission=submission, actor=specialist, reason="Uus info")

    items = _history(normal_matter, specialist)
    sends = [item for item in items if item.submission]

    assert len(sends) == 1
    assert sends[0].milestone.what == SUBMISSION_MILESTONE
    # The send keeps its own business date, its recipients and its final text.
    assert sends[0].milestone.display_date == _et(sent_on)
    assert "Majandus- ja Kommunikatsiooniministeerium" in sends[0].milestone.sub
    assert [file.label for file in sends[0].files] == ["arvamus.pdf"]
    # And the withdrawal reads as its own line, from the audit vocabulary.
    assert "Arvamus tagasi võetud" in _rendered(items)


def test_a_superseded_opinion_is_still_the_act_it_was(
    normal_matter, specialist, ministry, committee, evidence_root
):
    """Scenario C. A later opinion is another send, not a correction of this one."""
    first_day, second_day = _days_ago(60), _days_ago(12)
    first = add_matter_koda_opinion(
        matter=normal_matter,
        author=specialist,
        upload=_pdf("arvamus_1.pdf", b"%PDF-1.4 esimene"),
        recipients=[ministry],
        sent_on=first_day,
        title="Koja arvamus",
    ).record
    add_matter_koda_opinion(
        matter=normal_matter,
        author=specialist,
        upload=_pdf("arvamus_2.pdf", b"%PDF-1.4 teine"),
        recipients=[committee],
        sent_on=second_day,
        title="Koja täiendav arvamus",
    )
    supersede_submission(submission=first, actor=specialist)

    rows = [item for item in _history(normal_matter, specialist) if item.submission]

    # Two sends, two rows. Nothing elects a final opinion.
    assert len(rows) == 2
    assert {row.milestone.display_date for row in rows} == {_et(first_day), _et(second_day)}
    superseded = next(row for row in rows if row.milestone.display_date == _et(first_day))
    # Its final text is still reachable under the row that stands for the send.
    assert [file.label for file in superseded.files] == ["arvamus_1.pdf"]


def test_a_draft_carrying_a_stray_timestamp_is_not_a_send(
    normal_matter, specialist, ministry, evidence_root
):
    """The population is «was sent», not «has a timestamp».

    The CHECK constraint binds `sent_at` to the SENT status and says nothing
    about a draft, so a row carrying both `DRAFT` and a timestamp is malformed
    data. A history that materialised a send out of one would be inventing an
    act nobody performed.
    """
    submission = add_matter_koda_opinion(
        matter=normal_matter,
        author=specialist,
        upload=_pdf(),
        recipients=[ministry],
        sent_on=_days_ago(5),
        title="Koja arvamus",
    ).record
    # The constraint refuses `SENT` without evidence, never `DRAFT` with a date.
    submission.status = SubmissionStatus.DRAFT
    submission.save(update_fields=["status"])

    assert submission.sent_at is not None
    assert [item for item in _history(normal_matter, specialist) if item.submission] == []


def test_the_send_date_is_the_records_own_and_not_the_day_it_was_withdrawn(
    normal_matter, specialist, ministry, evidence_root
):
    """`sent_at` stays canonical, and no audit timestamp is revived as a date."""
    sent_on = _days_ago(45)
    submission = add_matter_koda_opinion(
        matter=normal_matter,
        author=specialist,
        upload=_pdf(),
        recipients=[ministry],
        sent_on=sent_on,
        title="Koja arvamus",
    ).record
    withdraw_submission(submission=submission, actor=specialist)

    rows = [item for item in _history(normal_matter, specialist) if item.submission]
    assert rows[0].milestone.display_date == _et(sent_on)
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.WITHDRAWN


def test_a_restricted_withdrawn_opinion_stays_hidden(
    normal_matter, specialist, reader, ministry, evidence_root
):
    """Widening the population must not widen visibility with it."""
    result = add_matter_koda_opinion(
        matter=normal_matter,
        author=specialist,
        upload=_pdf("salajane.pdf", b"%PDF-1.4 salajane"),
        recipients=[ministry],
        sent_on=_days_ago(20),
        title="Koja salajane arvamus",
    )
    submission = result.record
    withdraw_submission(submission=submission, actor=specialist)
    # The document first: `submissions_check_final_evidence` refuses final
    # evidence less restricted than the send it proves.
    document = result.documents[0]
    document.visibility_override = Visibility.RESTRICTED
    document.save(update_fields=["visibility_override"])
    submission.visibility_override = Visibility.RESTRICTED
    submission.save(update_fields=["visibility_override"])

    body = _rendered(_history(normal_matter, reader))
    assert "salajane" not in body
    assert SUBMISSION_MILESTONE not in body
    assert "Arvamus tagasi võetud" not in body


# ---------------------------------------------------------------------------
# `Kõik muudatused` renders a vocabulary somebody chose
# ---------------------------------------------------------------------------
#
# `scope_change_events` lets an *unclassified* event family through as
# Matter-level, which is right for `MATTER_CREATED` and wrong for a family whose
# summary names a child. Every other surface named its own vocabulary; this page
# asked for «everything», and «everything» plus «unknown means Matter-level» is
# «unknown means allowed» (AUTH-003, docs/adr/0092 §11).


def test_the_change_log_vocabulary_is_a_subset_of_what_somebody_classified():
    """The structural guard. A new event family cannot become visible silently.

    If this fails, an event type reached `Kõik muudatused` without being either
    declared safe at Matter visibility or given a visibility classifier in
    `app.audit.visibility._child_families`. Add it to whichever of the two is
    true; do not widen this assertion.
    """
    assert change_log_event_types() <= (MATTER_LEVEL_EVENT_TYPES | child_event_types())
    # And the two halves are disjoint: a type is Matter-level or it is a child's,
    # never both, or one of the two answers is wrong about it.
    assert not (MATTER_LEVEL_EVENT_TYPES & child_event_types())


@pytest.mark.parametrize(
    "event_type",
    [
        ChangeEventType.MATTER_RELATION_ADDED,
        ChangeEventType.MATTER_RELATION_REMOVED,
        ChangeEventType.BACKGROUND_MATERIAL_ADDED,
        ChangeEventType.BACKGROUND_MATERIAL_REMOVED,
        ChangeEventType.WEBSITE_OVERVIEW_PLANNED,
        ChangeEventType.WEBSITE_OVERVIEW_PUBLISHED,
        ChangeEventType.WEBSITE_OVERVIEW_CANCELLED,
        ChangeEventType.WEBSITE_OVERVIEW_LINK_CORRECTED,
    ],
)
def test_an_unclassified_family_is_absent_rather_than_allowed(event_type):
    """The families the review proved leak, named one by one.

    Each summary or event label names an object carrying its own
    `visibility_override`, and none of them has a classifier in
    `_child_families` yet. Classifying them is the right fix and belongs in that
    map; until somebody makes it, they are off this page.
    """
    assert event_type not in change_log_event_types()


def test_a_restricted_related_matters_title_is_not_in_the_change_log(client, specialist, reader):
    """The review's own reproduction.

    A reader who receives 404 for a RESTRICTED Matter could read its title out of
    `Kõik muudatused` on a Matter they may open.
    """
    matter = factories.MatterFactory(owner=specialist)
    secret = factories.MatterFactory(
        owner=specialist,
        visibility=Visibility.RESTRICTED,
        title="Riigisaladuse seaduse muudatused",
    )
    link_related_matters(matter=matter, other=secret, actor=specialist)

    client.force_login(reader)
    # The 404 that makes the disclosure a disclosure.
    assert client.get(reverse("matters:matter_detail", kwargs={"pk": secret.pk})).status_code == 404

    body = client.get(reverse("matters:matter_changes", kwargs={"pk": matter.pk})).content.decode()
    assert "Riigisaladuse" not in body
    assert "Teema seotud teise teemaga" not in body


def test_a_foreign_submissions_title_is_not_in_the_change_log(
    client, specialist, reader, ministry, evidence_root
):
    """Background material names a `Submission` that lives on another Matter."""
    matter = factories.MatterFactory(owner=specialist)
    source = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    submission = add_matter_koda_opinion(
        matter=source,
        author=specialist,
        upload=_pdf("taust.pdf", b"%PDF-1.4 taust"),
        recipients=[ministry],
        sent_on=_days_ago(80),
        title="Koja salajane taustarvamus",
    ).record
    add_background_submission(matter=matter, submission=submission, actor=specialist)

    client.force_login(reader)
    body = client.get(reverse("matters:matter_changes", kwargs={"pk": matter.pk})).content.decode()

    assert "salajane" not in body
    assert "Taustmaterjal lisatud" not in body


def test_a_restricted_website_overview_is_not_named_in_the_change_log(client, specialist, reader):
    """Not even its existence: the event label alone says one was published."""
    matter = factories.MatterFactory(owner=specialist)
    overview = plan_website_overview(matter=matter, actor=specialist)
    publish_website_overview(
        overview=overview,
        url="https://koda.ee/uudised/salajane-ulevaade",
        published_on=_days_ago(3),
        actor=specialist,
    )
    overview.refresh_from_db()
    overview.visibility_override = Visibility.RESTRICTED
    overview.save(update_fields=["visibility_override"])

    client.force_login(reader)
    body = client.get(reverse("matters:matter_changes", kwargs={"pk": matter.pk})).content.decode()

    assert "Ülevaade / uudis avaldatud" not in body
    assert "Ülevaade / uudis plaanis" not in body
    assert "salajane-ulevaade" not in body


def test_an_already_classified_restricted_development_is_still_hidden(client, specialist, reader):
    """The classified half of the union keeps working exactly as it did."""
    matter = factories.MatterFactory(owner=specialist)
    development = add_procedural_development(
        matter=matter,
        author=specialist,
        title="Ministeerium saatis salajase versiooni",
        occurred_on=_days_ago(5),
    ).record
    development.visibility_override = Visibility.RESTRICTED
    development.save(update_fields=["visibility_override"])

    client.force_login(reader)
    body = client.get(reverse("matters:matter_changes", kwargs={"pk": matter.pk})).content.decode()
    assert "salajase" not in body
    assert "Menetluse areng lisatud" not in body


def test_the_matter_level_writes_the_page_exists_for_are_all_still_there(
    signed_in, specialist, consultation
):
    """Fail-closed must not mean fail-empty: the audit page still audits."""
    matter = create_matter(title="Algne pealkiri", actor=specialist, owner=specialist)
    compose_update(matter=matter, author=specialist, body="Märkus")
    change_stage(matter=matter, stage=consultation, actor=specialist)

    body = signed_in.get(
        reverse("matters:matter_changes", kwargs={"pk": matter.pk})
    ).content.decode()

    assert "Teema loodud" in body
    assert "Hetkeseis muudetud" in body
    assert "Sissekanne lisatud" in body


# ---------------------------------------------------------------------------
# `?nihe=` is a number off a URL, and the page treats it as one
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["-1", "abc", "", "1e9", "9" * 40, str(2**63), str(10**30)])
def test_a_pathological_offset_is_a_page_and_never_a_500(signed_in, specialist, raw):
    """A malformed query string is a bad request, not a server fault.

    `OFFSET` is a 64-bit signed integer in PostgreSQL, so `2**63` reached the
    driver as a `DataError` and left a read-only audit page returning 500.
    """
    matter = factories.MatterFactory(owner=specialist)
    response = signed_in.get(
        reverse("matters:matter_changes", kwargs={"pk": matter.pk}), {"nihe": raw}
    )
    assert response.status_code == 200


def test_an_ordinary_second_page_reads_the_rows_after_the_first(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    for index in range(CHANGE_LOG_PAGE_SIZE + 5):
        ChangeEvent.objects.create(
            matter=matter,
            event_type=ChangeEventType.MATTER_TITLE_CHANGED,
            summary=f"Pealkiri {index}",
            occurred_at=timezone.now() - dt.timedelta(minutes=index + 1),
        )

    first = signed_in.get(reverse("matters:matter_changes", kwargs={"pk": matter.pk}))
    second = signed_in.get(
        reverse("matters:matter_changes", kwargs={"pk": matter.pk}),
        {"nihe": CHANGE_LOG_PAGE_SIZE},
    )

    assert len(first.context["changes"]) == CHANGE_LOG_PAGE_SIZE
    assert first.context["has_more"] is True
    assert second.context["has_more"] is False
    # Disjoint pages: nothing is shown twice and nothing is skipped.
    assert not {row.pk for row in first.context["changes"]} & {
        row.pk for row in second.context["changes"]
    }
    assert len(first.context["changes"]) + len(second.context["changes"]) == (
        ChangeEvent.objects.filter(matter=matter).count()
    )


def test_the_query_does_not_materialise_the_pages_before_it(signed_in, specialist):
    """LOW 7. The queryset is sliced; previous pages never reach Python.

    The old spelling asked for `offset + 101` rows and threw the first `offset`
    of them away, so page four cost four times page one for the same hundred
    lines on screen. What that defect *was* is the `LIMIT` the database was
    given, so that is what this measures.
    """
    matter = factories.MatterFactory(owner=specialist)
    for index in range(CHANGE_LOG_PAGE_SIZE * 3):
        ChangeEvent.objects.create(
            matter=matter,
            event_type=ChangeEventType.MATTER_TITLE_CHANGED,
            summary=f"Pealkiri {index}",
            occurred_at=timezone.now() - dt.timedelta(minutes=index + 1),
        )

    url = reverse("matters:matter_changes", kwargs={"pk": matter.pk})
    with CaptureQueriesContext(connection) as shallow:
        signed_in.get(url)
    with CaptureQueriesContext(connection) as deep:
        signed_in.get(url, {"nihe": CHANGE_LOG_PAGE_SIZE * 2})

    def _changelog_sql(captured) -> str:
        return next(
            query["sql"]
            for query in captured.captured_queries
            if "audit_changeevent" in query["sql"] and "LIMIT" in query["sql"]
        )

    shallow_sql, deep_sql = _changelog_sql(shallow), _changelog_sql(deep)
    assert f"LIMIT {CHANGE_LOG_PAGE_SIZE + 1}" in shallow_sql
    # The same bounded window however deep the page is, and an OFFSET the
    # database honours rather than a slice Python takes afterwards.
    assert f"LIMIT {CHANGE_LOG_PAGE_SIZE + 1}" in deep_sql
    assert f"OFFSET {CHANGE_LOG_PAGE_SIZE * 2}" in deep_sql
    # And no N+1: a deeper page is the same number of queries.
    assert len(shallow.captured_queries) == len(deep.captured_queries)


def test_the_empty_history_is_named_after_the_section_it_is_in(signed_in, specialist):
    """LOW 8. The heading says `Teema käik`; so does the empty state.

    `Ajajoon` survives as the id and the `?ajajoon=` filter, where renaming it
    would break links people have already sent, and nowhere a reader can see it.
    A section whose heading and whose empty state name it differently reads as
    two components (docs/adr/0092 §9).
    """
    matter = factories.MatterFactory(owner=specialist)
    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()

    assert "Teema käik on tühi. Esimene sissekanne ilmub siia." in body
    assert "Ajajoon on tühi" not in body
    # The compatibility surface is untouched.
    assert 'id="ajajoon"' in body
    assert (
        signed_in.get(
            reverse("matters:matter_detail", kwargs={"pk": matter.pk}),
            {"ajajoon": "sissekanded"},
        ).status_code
        == 200
    )


# ---------------------------------------------------------------------------
# The lawyer's own note, on the row and under its own label
# ---------------------------------------------------------------------------
#
# `development_milestone` has set `own_note` and `own_note_label` since
# docs/adr/0091 §5, and the generic chronology row rendered neither: the label,
# the value and the whole separation existed in exactly one template, the
# `Väline seisukoht` partial. So a `Menetluse areng` saved with a
# `Juristi märkus` printed the headline, the date and the files, and this
# office's own assessment of the step reached no reading surface at all.
#
# These assert the rendered page rather than the read model, because the read
# model was right the whole time.


def _development_row_html(client, matter, title: str) -> str:
    """The chronology article this development draws, as the page renders it."""
    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()
    start = body.index(title)
    return body[body.rindex("<article", 0, start) : body.index("</article>", start)]


def test_a_development_renders_the_step_and_the_lawyers_note_separately(signed_in, specialist):
    """What the procedure did, and what this office makes of it, are two lines.

    Folding the assessment into the headline would make one line say two things
    with two authors, which is the whole reason `note` is a column of its own
    (docs/adr/0091 §4, §5).
    """
    matter = factories.MatterFactory(owner=specialist)
    add_procedural_development(
        matter=matter,
        author=specialist,
        title="Ministeerium saatis parandatud eelnõu",
        occurred_on=_days_ago(4),
        note="Muudatused ei arvesta Koja ettepanekut.",
    )

    row = _development_row_html(signed_in, matter, "Ministeerium saatis parandatud eelnõu")

    assert "Ministeerium saatis parandatud eelnõu" in row
    assert "Muudatused ei arvesta Koja ettepanekut." in row
    headline = row.index("Ministeerium saatis parandatud eelnõu")
    note = row.index("Muudatused ei arvesta Koja ettepanekut.")
    assert headline < note, "the note reads under the step, never inside it"
    assert "eelnõu Muudatused" not in row, "the two sentences are never one string"


def test_the_developments_note_reads_under_the_juristi_markus_label(signed_in, specialist):
    """The label is what does the work.

    A paragraph of Koda's assessment printed unlabelled under a headline naming a
    ministry is the attribution defect with better line spacing — a colleague
    scanning the chronology reads it as part of what the ministry said. Same
    label, same markup and same stylesheet as a `Väline seisukoht`'s, because it
    is the same fact about the same author.
    """
    matter = factories.MatterFactory(owner=specialist)
    add_procedural_development(
        matter=matter,
        author=specialist,
        title="Valitsus kiitis eelnõu heaks",
        occurred_on=_days_ago(6),
        note="Meie kaks ettepanekut jäid arvestamata.",
    )

    row = _development_row_html(signed_in, matter, "Valitsus kiitis eelnõu heaks")

    assert LAWYER_NOTE_LABEL in row
    assert "uxtl__msnotelabel" in row
    label = row.index(LAWYER_NOTE_LABEL)
    assert label < row.index("Meie kaks ettepanekut jäid arvestamata.")


def test_a_note_carrying_markup_is_escaped(signed_in, specialist):
    """The note is a plain sentence somebody typed, rendered as one.

    `Menetluse areng` has no rich-text control and `note` is not sanitised
    markup, so the row must escape it rather than trust it — the ordinary Django
    autoescape, asserted because this is the first surface that prints the
    column.
    """
    matter = factories.MatterFactory(owner=specialist)
    add_procedural_development(
        matter=matter,
        author=specialist,
        title="Komisjon arutas eelnõu",
        occurred_on=_days_ago(2),
        note="<script>alert(1)</script> & muud",
    )

    row = _development_row_html(signed_in, matter, "Komisjon arutas eelnõu")

    assert "<script>alert(1)</script>" not in row
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in row
    assert "&amp; muud" in row


def test_a_development_with_no_note_renders_no_note_block(signed_in, specialist):
    """An ordinary step gains no empty label and no empty box.

    Most developments carry no assessment — the ministry sent a draft and there
    is nothing yet to say about it — and a bordered empty block under every one
    of them would be the row announcing a gap in itself.
    """
    matter = factories.MatterFactory(owner=specialist)
    add_procedural_development(
        matter=matter,
        author=specialist,
        title="Eelnou joudis Riigikokku",
        occurred_on=_days_ago(3),
    )

    row = _development_row_html(signed_in, matter, "Eelnou joudis Riigikokku")

    assert "uxtl__msnote" not in row
    assert LAWYER_NOTE_LABEL not in row


def test_an_external_positions_note_still_renders_exactly_once(signed_in, specialist, ministry):
    """The partial that already rendered this did not gain a second copy.

    A `Väline seisukoht` renders its own row, including its own note, precisely
    so that a correction swaps the record and not the spine around it. The
    generic branch must not reach it — two labelled notes on one row is the same
    attribution failure read twice.

    Counted by the note element's own class rather than by the words
    `Juristi märkus`, which are also the label of the `note` control on two
    composer panels standing open on the same page: a page-wide count of the
    phrase reads four and says nothing about the chronology.
    """
    matter = factories.MatterFactory(owner=specialist)
    add_matter_external_position(
        matter=matter,
        author=specialist,
        provenance=ExternalPositionProvenance.RECEIVED.value,
        organisation=ministry,
        summary="Ministeerium toetab varianti B.",
        lawyer_note="Nende pohjendus ei arvesta kulumojuga.",
        stated_on=_days_ago(9),
    )

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()

    assert body.count("Nende pohjendus ei arvesta kulumojuga.") == 1
    assert body.count("uxtl__msnotelabel") == 1


def test_a_restricted_developments_note_is_invisible_to_a_reader(client, specialist, reader):
    """§7. Permission before projection, and the note is inside that boundary.

    A development restricted below its Matter contributes no row, so it
    contributes no note either — the rendering fix must not turn `own_note` into
    a value that escapes a record nobody may read.
    """
    matter = factories.MatterFactory(owner=specialist)
    development = add_procedural_development(
        matter=matter,
        author=specialist,
        title="Ministeerium saatis salajase versiooni",
        occurred_on=_days_ago(5),
        note="Salajane hinnang eelnoule.",
    ).record
    development.visibility_override = Visibility.RESTRICTED
    development.save(update_fields=["visibility_override"])

    client.force_login(reader)
    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()

    assert "Salajane hinnang" not in body
    assert "salajase versiooni" not in body
    assert LAWYER_NOTE_LABEL not in body


def test_rendering_the_note_costs_no_extra_query(
    signed_in, specialist, django_assert_max_num_queries
):
    """§10. The column is already loaded, so printing it reads nothing new.

    Asserted against a doubling population under one budget, the same shape
    `test_the_history_does_not_cost_a_query_per_row` uses: a note that cost a
    query per row would be the read model reaching back into the database from
    inside the template.
    """
    matter = factories.MatterFactory(owner=specialist)

    def populate(count: int) -> None:
        for index in range(count):
            add_procedural_development(
                matter=matter,
                author=specialist,
                title=f"Menetlus liikus {index}",
                occurred_on=_days_ago(index + 1),
                note=f"Juristi hinnang {index}.",
            )

    budget = 18
    populate(3)
    with django_assert_max_num_queries(budget):
        assert len(_history(matter, specialist)) >= 3
    populate(6)
    with django_assert_max_num_queries(budget):
        assert len(_history(matter, specialist)) >= 9
