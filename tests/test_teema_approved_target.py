"""The approved Teema target, rule by rule.

The design handoff (`TEEMA_TARGET.html`, `TEEMA_TARGET_SPEC.md` and the three
screenshots) is the oracle for this page. What is asserted here is what that
target requires and what the old page did instead — every one of these is a
*deliberate* supersession of an earlier presentation decision, recorded in
`docs/adr/0074`.

The domain underneath is almost entirely unchanged. Where a canonical rule is
asserted, it is asserted because the target moved the surface that reaches it,
not because the rule moved.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.dates import format_estonian_date
from app.core.enums import Visibility
from app.documents.enums import DocumentRole
from app.documents.models import Document
from app.intelligence.enums import FactStatus, WorkVictoryStatus
from app.intelligence.models import MatterEffectiveDate, MatterImportantDate, MatterWorkVictory
from app.matters.enums import COMPOSER_ENGAGEMENT_KINDS, EngagementKind, MatterOrigin
from app.matters.forms import ComposerForm
from app.matters.models import Entry, MatterEngagement
from app.matters.process_timeline import process_steps
from app.matters.services import add_engagement, change_stage, close_matter, reopen_matter
from app.matters.timeline import matter_timeline
from app.submissions.enums import SentAtPrecision
from app.submissions.services import register_sent_opinion
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics, Disposition
from app.workflow.models import StageVocabulary
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db


def _detail(client, matter) -> str:
    url = reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    return client.get(url).content.decode()


def _compose(client, matter, **fields):
    payload = {"body": "", "next_text": "", "next_date": "", "deadline_title": ""}
    payload.update(fields)
    return client.post(
        reverse("matters:compose", kwargs={"pk": matter.pk}),
        payload,
        headers={"HX-Request": "true"},
    )


def _action(matter, actor, *, days: int = 7):
    return set_next_action(
        matter=matter,
        text="Koosta koja arvamus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() + timedelta(days=days),
        actor=actor,
    )


# ===========================================================================
# HEADER — §9, §10, §11
# ===========================================================================


def test_the_metaline_holds_the_five_target_items_in_order(signed_in, normal_matter, stage):
    """`Vastutaja · Valdkond · Hetkeseis · Saabus · Tähtaeg`.

    `Saabus` is position 4 and `Tähtaeg` is immediately after it. Arrival and
    response deadline are read as a pair — together they say how much time is
    left — so they sit next to each other rather than one in the title band and
    one three hundred pixels to the right (TEEMA_TARGET_SPEC §B).
    """
    normal_matter.stage = stage
    normal_matter.received_date = date(2026, 8, 24)
    normal_matter.response_deadline = timezone.localdate() + timedelta(days=21)
    normal_matter.save(update_fields=["stage", "received_date", "response_deadline"])

    body = _detail(signed_in, normal_matter)
    metaline = body[body.index('class="metaline"') :]
    metaline = metaline[: metaline.index('class="summary"')]

    order = [
        label
        for label in ("Vastutaja", "Valdkond", "Hetkeseis", "Saabus", "Tähtaeg")
        if label in metaline
    ]
    assert order == ["Vastutaja", "Valdkond", "Hetkeseis", "Saabus", "Tähtaeg"]
    assert metaline.index("Saabus") < metaline.index("Tähtaeg")


def test_saabus_moved_out_of_the_rail_and_kept_its_write_path(signed_in, normal_matter):
    """Moved, not rebuilt. One control, one endpoint, one audit row — a second
    `Saabus` editor would be two ways to write one column."""
    body = _detail(signed_in, normal_matter)
    rail = body[body.index('id="teema-andmed"') :]
    rail = rail[: rail.index("</aside>")]
    header = body[body.index('id="teema-pais"') : body.index('id="teema-vaade"')]

    assert "Saabus" not in rail
    assert "Saabus" in header
    assert body.count('name="received_date"') == 1
    assert (
        reverse("matters:update_field", kwargs={"pk": normal_matter.pk, "field": "received_date"})
        in header
    )


def test_the_saabus_editor_re_renders_the_surface_it_lives_on(signed_in, normal_matter):
    """**The surface a field re-renders has to move with the field.**

    The editor swaps `#teema-pais`, so the response has to be the header band.
    While `Saabus` was a rail fact `update_field` answered with the rail, and a
    control that swapped the header with a rail replaced the band with a rail —
    the value it had just written vanished, and so did the title, the metaline
    and every other inline editor. A browser found it; nothing in the markup
    could (docs/adr/0074 §2).
    """
    url = reverse("matters:update_field", kwargs={"pk": normal_matter.pk, "field": "received_date"})

    body = signed_in.post(url, {"received_date": "14.8.2026"}).content.decode()

    assert 'id="teema-pais"' in body
    assert 'id="teema-andmed"' not in body
    assert "14.8.2026" in body
    normal_matter.refresh_from_db()
    assert normal_matter.received_date == date(2026, 8, 14)


def test_the_header_deadline_is_the_response_deadline_not_the_nearest_milestone(
    signed_in, normal_matter, specialist
):
    """**§10.** A Riigikogu reading in three weeks is not a day Koda owes an
    opinion on, and the slot's own editor is headed `Arvamuse tähtaeg`.

    `selectors.active_deadline` still answers the broader "what is the next
    dated thing on this file" for the work lists that want it.
    """
    normal_matter.response_deadline = timezone.localdate() + timedelta(days=40)
    normal_matter.save(update_fields=["response_deadline"])
    nearer = timezone.localdate() + timedelta(days=3)
    MatterImportantDate.objects.create(
        matter=normal_matter,
        title="Riigikogu I lugemine",
        date_value=nearer,
        period_end=nearer,
        date_precision=DatePrecision.EXACT,
        status=FactStatus.ACTIVE,
        created_by=specialist,
    )

    body = _detail(signed_in, normal_matter)
    slot = body[body.index("metaline__item--deadline") :]
    slot = slot[: slot.index("</span>\n  </div>") if "</span>\n  </div>" in slot else 2000]

    assert "Arvamuse tähtaeg" in slot
    assert "Riigikogu I lugemine" not in slot
    assert "40 p" in slot


def test_a_matter_with_no_arrival_date_offers_one(signed_in, normal_matter):
    """A register row imported without a date, which is an ordinary state.

    A quiet invitation in the slot rather than a label over an em dash — the
    same treatment `Tähtaeg` gets, and for the same reason (Teema redesign §24).
    """
    normal_matter.received_date = None
    normal_matter.save(update_fields=["received_date"])

    body = _detail(signed_in, normal_matter)
    header = body[body.index('class="metaline"') : body.index('class="summary"')]

    assert "+ Saabus" in header
    assert 'name="received_date"' in header


def test_a_matter_with_no_response_deadline_offers_one(signed_in, normal_matter):
    normal_matter.response_deadline = None
    normal_matter.save(update_fields=["response_deadline"])

    body = _detail(signed_in, normal_matter)

    assert "+ Tähtaeg" in body
    assert "metaline__item--deadline" not in body


def test_every_metaline_value_is_an_inline_editor(signed_in, normal_matter, stage):
    """No modal, no navigation away: the value is the trigger and the editor has
    one `Salvesta` (TEEMA_TARGET_SPEC §B)."""
    normal_matter.stage = stage
    normal_matter.received_date = timezone.localdate()
    normal_matter.response_deadline = timezone.localdate() + timedelta(days=10)
    normal_matter.save(update_fields=["stage", "received_date", "response_deadline"])

    body = _detail(signed_in, normal_matter)
    header = body[body.index('class="metaline"') : body.index('class="summary"')]

    assert header.count('class="inlineedit"') == 5
    assert header.count("inlineedit__form") == 5
    # And `Muuda teemat` is still the whole-record action.
    assert reverse("matters:matter_edit", kwargs={"pk": normal_matter.pk}) in body


# ===========================================================================
# THE WORKSPACE — §15, §16, §17, §18, §27 as docs/adr/0075 supersedes them
# ===========================================================================
#
# The approved target's composer clauses are superseded by ADR 0075: the open
# five-panel form over one `Salvesta` is replaced by `PRAEGUNE TEGEVUS` and
# `LISA TEEMALE`. What these tests still guard is everything the target decided
# that ADR 0075 did *not* touch — the file affordance being immediately
# available, the retired classification vocabulary staying retired, the reader
# and the closed Matter getting no write controls, and every panel's questions.


def test_the_current_action_is_answerable_on_an_initial_get(signed_in, normal_matter, specialist):
    """The box that finishes the current task is on the page, not behind a
    click. Recording work is the reason this product exists and it must not
    begin with opening something (docs/adr/0075 §3)."""
    _action(normal_matter, specialist)
    body = _detail(signed_in, normal_matter)
    zone = body[body.index('id="praegune-tegevus"') : body.index('id="lisa-teemale"')]

    assert "Koosta koja arvamus" in zone
    assert "Mida tegid?" in zone
    assert 'name="action_id"' in zone
    assert "<textarea" in zone


def test_a_refused_save_comes_back_with_what_was_typed(signed_in, normal_matter, specialist):
    action = _action(normal_matter, specialist)
    response = signed_in.post(
        reverse("matters:add_note", kwargs={"pk": normal_matter.pk}),
        {"body": ""},
        headers={"HX-Request": "true"},
    )
    html = response.content.decode()

    assert response.status_code == 400
    # Its own panel, open, with its own refusal beside its own field.
    assert 'id="lisa-marge"' in html
    assert "Kirjelda, mis juhtus." in html
    # And the current action is untouched by a refused note.
    action.refresh_from_db()
    assert action.status == ActionStatus.OPEN


def test_a_reader_gets_no_write_controls(client, normal_matter, reader, specialist):
    _action(normal_matter, specialist)
    client.force_login(reader)
    body = _detail(client, normal_matter)

    # The task reads; nothing offers to change it.
    assert "Koosta koja arvamus" in body
    assert "Mida tegid?" not in body
    assert 'id="lisa-teemale"' not in body
    assert "Salvesta" not in body.split('id="teema-vaade"')[1].split("</div>")[0]


def test_a_closed_matter_gets_no_workspace(signed_in, normal_matter, specialist):
    from app.matters.services import close_matter

    close_matter(matter=normal_matter, disposition=Disposition.COMPLETED, actor=specialist)
    body = _detail(signed_in, normal_matter)

    assert "Mida tegid?" not in body
    assert 'id="lisa-teemale"' not in body
    # …and the chronology is still readable.
    assert 'id="ajalugu-loend"' in body


def test_the_next_step_is_asked_for_in_its_own_words(signed_in, normal_matter):
    """`Mida on vaja teha?` and `Millal?`, and no classification behind them.

    The target's «three primary questions in one form» is superseded: *what
    happened* and *what happens next* are two intentions and two saves now. What
    survives unchanged is the vocabulary — a next step is a sentence and a day
    (docs/adr/0075 §2, ADR 0052)."""
    body = _detail(signed_in, normal_matter)
    panel = body[body.index('id="lisa-jargmine"') : body.index('id="lisa-kaasamine"')]

    assert panel.index("Mida on vaja teha?") < panel.index("Millal?")
    for chip in ("Täna", "Homme", "+1 nädal", "+2 nädalat", "Kuupäev…"):
        assert chip in panel
    # The retired vocabulary is not back.
    for gone in ("TEEN", "OOTAN", "JÄLGIN", "Täpsemalt…"):
        assert gone not in panel


def test_the_file_control_is_immediately_available_and_there_is_no_manus_panel(
    signed_in, normal_matter, specialist
):
    """**§17.** Attaching files must not begin with opening a second panel.

    True of the current-action box, which is open on arrival, and of every
    `LISA TEEMALE` form once its own panel is chosen: the drop area is part of
    the form rather than a disclosure inside it."""
    _action(normal_matter, specialist)
    body = _detail(signed_in, normal_matter)
    zone = body[body.index('id="praegune-tegevus"') : body.index('id="lisa-teemale"')]

    assert "+ Manus" not in body
    assert "cx-drop" in zone
    assert "Lohista failid siia või" in zone
    assert "vali arvutist" in zone
    assert 'type="file"' in zone
    assert "multiple" in zone
    # And the four questions the target does not ask about a file.
    for gone in ("Sissekande liik", "Roll", "Toimus"):
        assert gone not in body


def test_lisa_teemale_offers_seven_choices_and_opens_none_of_them(signed_in, normal_matter):
    """**§18, as ADR 0075 restates it.** Seven operations, each its own form,
    and the zone is a choice until one is picked."""
    body = _detail(signed_in, normal_matter)
    panels = body[body.index('id="lisa-teemale"') : body.index('id="ajajoon"')]

    expected = [
        "+ Märge",
        "+ Järgmine tegevus",
        "+ Kaasamine",
        "+ Oluline tähtaeg",
        "+ Jõustumine",
        "+ Töövõit",
        "+ Lõpeta teema",
    ]
    assert [chip for chip in expected if chip in panels] == expected
    assert panels.count('class="cx-panel"') + panels.count("cx-panel cx-panel--last") == 7
    # All closed on arrival: nothing in this zone is a form until it is chosen.
    assert "data-addpanel\n             open" not in panels
    assert 'cx-panel" open' not in panels


def test_the_panels_offered_do_not_depend_on_what_the_matter_already_holds(
    signed_in, normal_matter, specialist
):
    """A Matter that already carries a commencement can gain another. The chips
    used to disappear once the facts section owned the second one, and that
    section is gone (docs/adr/0074 §7)."""
    MatterEffectiveDate.objects.create(
        matter=normal_matter,
        date_value=date(2027, 1, 1),
        period_end=date(2027, 1, 1),
        date_precision=DatePrecision.EXACT,
        status=FactStatus.ACTIVE,
        created_by=specialist,
    )
    MatterWorkVictory.objects.create(
        matter=normal_matter,
        title="Varasem võit",
        status=WorkVictoryStatus.CONFIRMED,
        confirmed_by=specialist,
        confirmed_at=timezone.now(),
        created_by=specialist,
    )

    body = _detail(signed_in, normal_matter)

    assert "+ Jõustumine" in body
    assert "+ Töövõit" in body


def test_each_operation_carries_its_own_save_and_there_is_no_global_one(
    signed_in, normal_matter, specialist
):
    """**§27 reversed by docs/adr/0075 §2.** One `Salvesta` per intention.

    The target's single save was the whole claim that a professional update is
    one act. It is not: one button that could mean *note* and *deadline* and
    *win* and *closure* at once is a button whose meaning has to be
    reconstructed from which boxes were filled in."""
    _action(normal_matter, specialist)
    body = _detail(signed_in, normal_matter)
    workspace = body[body.index('id="praegune-tegevus"') : body.index('id="ajajoon"')]

    # The current action's own save, and one only. `Muuda` is beside it with a
    # save of its own — a different operation, which is the distinction this
    # round exists to make (brief §9).
    zone = body[body.index('id="praegune-tegevus"') : body.index('id="lisa-teemale"')]
    completion = zone[zone.index('class="curact__form"') :]
    assert completion.count('type="submit"') == 1
    assert zone.count('type="submit"') == 2

    # Seven choices under LISA TEEMALE, minus the one hidden while a step is
    # open, each with exactly one save of its own.
    panels = body[body.index('id="lisa-teemale"') : body.index('id="ajajoon"')]
    assert panels.count('type="submit"') == 6
    # And the composer's single global save is gone from the page entirely.
    assert "composer__actions" not in workspace


# ===========================================================================
# ONE SAVE, MANY RECORDS — §27
# ===========================================================================


def test_one_post_records_every_panel_atomically(signed_in, normal_matter, specialist):
    """Entry, next step, attachment, deadline, commencement, victory and
    engagement — one POST, one transaction, one operation id."""
    response = _compose(
        signed_in,
        normal_matter,
        body="<p>Käisin ministeeriumis.</p>",
        next_text="Koosta arvamus",
        next_date="20.10.2026",
        attachment=SimpleUploadedFile("koond.pdf", b"%PDF-1.4 x", content_type="application/pdf"),
        deadline_title="Kooskõlastusringi lõpp",
        deadline_date="30.09.2026",
        deadline_precision=DatePrecision.EXACT,
        effective_title="Pakendiseaduse muudatused",
        effective_on="01.01.2027",
        victory_change="Üleminekuaeg pikenes",
        engagement_kind=EngagementKind.SURVEY,
        engagement_audience="liikmed",
        engagement_responses="14",
    )
    assert response.status_code == 200, response.content.decode()[:3000]

    assert Entry.objects.filter(matter=normal_matter).count() == 1
    assert Document.objects.filter(matter=normal_matter).count() == 1
    assert MatterImportantDate.objects.filter(matter=normal_matter).count() == 1
    assert MatterEffectiveDate.objects.filter(matter=normal_matter).count() == 1
    assert MatterWorkVictory.objects.filter(matter=normal_matter).count() == 1
    assert MatterEngagement.objects.filter(matter=normal_matter).count() == 1

    # One professional action, one operation id across every audit row.
    operations = set(
        ChangeEvent.objects.filter(matter=normal_matter)
        .exclude(operation_id=None)
        .values_list("operation_id", flat=True)
    )
    assert len(operations) == 1


def test_two_filled_panels_are_all_or_nothing(signed_in, normal_matter):
    """A refusal in one panel writes none of the others."""
    response = _compose(
        signed_in,
        normal_matter,
        body="<p>Midagi juhtus.</p>",
        effective_title="Pakendiseaduse muudatused",
        effective_on="",
        victory_change="Üleminekuaeg pikenes",
    )

    assert response.status_code == 400
    assert not Entry.objects.filter(matter=normal_matter).exists()
    assert not MatterEffectiveDate.objects.filter(matter=normal_matter).exists()
    assert not MatterWorkVictory.objects.filter(matter=normal_matter).exists()


def test_an_attachment_goes_through_the_canonical_evidence_service(signed_in, normal_matter):
    """Immutable bytes, a checksum, and the ordinary role — the target stopped
    asking a person to classify a file and did not stop classifying it."""
    response = _compose(
        signed_in,
        normal_matter,
        body="<p>Sain faili.</p>",
        attachment=SimpleUploadedFile("eelnou.pdf", b"%PDF-1.4 y", content_type="application/pdf"),
    )
    assert response.status_code == 200, response.content.decode()[:2000]

    document = Document.objects.get(matter=normal_matter)
    assert document.role == DocumentRole.OTHER
    version = document.versions.get()
    assert version.original_filename == "eelnou.pdf"
    assert version.sha256


# ===========================================================================
# + OLULINE TÄHTAEG — §19
# ===========================================================================


@pytest.mark.parametrize(
    ("precision", "anchor", "end"),
    [
        (DatePrecision.EXACT, date(2026, 9, 30), date(2026, 9, 30)),
        (DatePrecision.MONTH, date(2026, 9, 1), date(2026, 9, 30)),
        (DatePrecision.QUARTER, date(2026, 7, 1), date(2026, 9, 30)),
    ],
)
def test_the_compact_precision_derives_its_period_from_the_day(
    signed_in, normal_matter, precision, anchor, end
):
    """One `Kuupäev` box and three chips. `Kuu` means the month containing the
    day that was picked, and `bounds_for` normalises it to the same stored anchor
    the full period form produces (docs/adr/0074 §11)."""
    response = _compose(
        signed_in,
        normal_matter,
        deadline_title="Kooskõlastusringi lõpp",
        deadline_date="30.09.2026",
        deadline_precision=precision,
    )
    assert response.status_code == 200, response.content.decode()[:2000]

    record = MatterImportantDate.objects.get(matter=normal_matter)
    assert record.date_precision == precision
    assert record.date_value == anchor
    assert record.period_end == end


def test_the_panel_offers_three_precisions_and_the_domain_keeps_five(signed_in, normal_matter):
    chips = ComposerForm().precision_chips

    assert [chip["label"] for chip in chips] == ["Täpne päev", "Kuu", "Kvartal"]
    assert chips[0]["selected"]
    body = _detail(signed_in, normal_matter)
    assert "Poolaasta" not in body
    # The stored vocabulary is untouched; old records still read.
    assert DatePrecision.HALF_YEAR in DatePrecision.values


# ===========================================================================
# + JÕUSTUMINE and + TÖÖVÕIT — §20, §21
# ===========================================================================


def test_a_commencement_is_a_matter_effective_date_not_an_entry(signed_in, normal_matter):
    response = _compose(
        signed_in,
        normal_matter,
        effective_title="Pakendiseaduse muudatused",
        effective_on="01.01.2027",
    )
    assert response.status_code == 200, response.content.decode()[:2000]

    record = MatterEffectiveDate.objects.get(matter=normal_matter)
    assert record.description == "Pakendiseaduse muudatused"
    assert record.date_value == date(2027, 1, 1)
    assert record.date_precision == DatePrecision.EXACT
    assert record.status == FactStatus.ACTIVE
    # No Entry was manufactured to make it appear in the chronology.
    assert not Entry.objects.filter(matter=normal_matter).exists()


def test_a_victory_does_not_require_closing_the_matter(signed_in, normal_matter):
    """**§21.** A win is recorded when it happens, which is usually while the
    file is still open."""
    response = _compose(signed_in, normal_matter, victory_change="Üleminekuaeg pikenes 2028-ni")
    assert response.status_code == 200, response.content.decode()[:2000]

    normal_matter.refresh_from_db()
    assert normal_matter.is_open

    victory = MatterWorkVictory.objects.get(matter=normal_matter)
    assert victory.title == "Üleminekuaeg pikenes 2028-ni"
    assert victory.status == WorkVictoryStatus.CONFIRMED
    assert victory.confirmed_by is not None
    # No reporting period was borrowed because the panel happened to be open.
    assert victory.period_date is None
    assert not Entry.objects.filter(matter=normal_matter).exists()


# ===========================================================================
# + KAASAMINE — §22, §23, §24, §25
# ===========================================================================


def test_the_engagement_panel_offers_the_three_target_kinds(signed_in, normal_matter):
    body = _detail(signed_in, normal_matter)
    panel = body[body.index('id="lisa-kaasamine"') :]
    panel = panel[: panel.index('id="lisa-tahtaeg"')]

    assert [label for _value, label in COMPOSER_ENGAGEMENT_KINDS] == [
        "Küsitlus",
        "Koosolek",
        "Kirjade voor",
    ]
    for label in ("Küsitlus", "Koosolek", "Kirjade voor"):
        assert f">{label}<" in panel
    assert "Keda kaasati" in panel
    assert "Vastuseid" in panel
    # And none of the old five-field form.
    for gone in ("Pealkiri", "Märkus"):
        assert gone not in panel


@pytest.mark.parametrize(
    ("value", "stored"),
    [
        (EngagementKind.SURVEY, EngagementKind.SURVEY),
        (EngagementKind.MEETING, EngagementKind.MEETING),
        (EngagementKind.EMAIL_CAMPAIGN, EngagementKind.EMAIL_CAMPAIGN),
    ],
)
def test_each_offered_kind_saves(signed_in, normal_matter, value, stored):
    response = _compose(
        signed_in,
        normal_matter,
        engagement_kind=value,
        engagement_audience="liikmed",
    )
    assert response.status_code == 200, response.content.decode()[:2000]

    record = MatterEngagement.objects.get(matter=normal_matter)
    assert record.kind == stored
    assert record.title == "liikmed"
    # The target does not ask for a date; the work is being recorded now.
    assert record.occurred_on == timezone.localdate()


def test_the_response_count_is_real_stored_data(signed_in, normal_matter):
    """**§23.** Not note text, not title text, not browser-only state."""
    response = _compose(
        signed_in,
        normal_matter,
        engagement_kind=EngagementKind.SURVEY,
        engagement_audience="liikmed",
        engagement_responses="14",
    )
    assert response.status_code == 200, response.content.decode()[:2000]

    record = MatterEngagement.objects.get(matter=normal_matter)
    assert record.response_count == 14
    record.refresh_from_db()
    assert record.response_count == 14
    # Nothing is derived from it.
    assert not hasattr(record, "response_rate")


def test_an_uncounted_engagement_stores_null_not_zero(signed_in, normal_matter):
    """«Nobody answered» and «nobody counted» are different facts about a
    consultation, and a column that cannot tell them apart reports the second as
    the first (docs/adr/0074 §5)."""
    _compose(
        signed_in,
        normal_matter,
        engagement_kind=EngagementKind.MEETING,
        engagement_audience="kaubandusvaldkonna töögrupp",
    )

    assert MatterEngagement.objects.get(matter=normal_matter).response_count is None


def test_existing_engagements_keep_their_kind_and_gain_a_null_count(normal_matter, specialist):
    """No backfill, no rewrite: a `WEB_CALL` row the composer never offers is
    still valid, still reads and still edits."""
    record = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.WEB_CALL,
        title="Kaasamiskutse koda.ee-s",
        actor=specialist,
    )

    record.refresh_from_db()
    assert record.kind == EngagementKind.WEB_CALL
    assert record.response_count is None

    from app.matters.services import update_engagement

    update_engagement(engagement=record, title="Kaasamiskutse", actor=specialist)
    record.refresh_from_db()
    assert record.kind == EngagementKind.WEB_CALL


def test_an_engagement_needs_to_say_who_was_engaged(normal_matter):
    form = ComposerForm(
        {"engagement_kind": EngagementKind.SURVEY, "engagement_responses": "14"},
        matter=normal_matter,
    )

    assert not form.is_valid()
    assert "engagement_audience" in form.errors


# ===========================================================================
# THE STANDING SECTIONS ARE GONE — §28
# ===========================================================================


def test_no_facts_panel_and_no_standalone_kaasamine(signed_in, normal_matter, specialist):
    """Removed, not hidden. A Matter carrying every structured fact renders
    neither surface, and both records are still there."""
    MatterImportantDate.objects.create(
        matter=normal_matter,
        title="Kooskõlastusringi lõpp",
        date_value=date(2026, 9, 30),
        period_end=date(2026, 9, 30),
        date_precision=DatePrecision.EXACT,
        status=FactStatus.ACTIVE,
        created_by=specialist,
    )
    add_engagement(
        matter=normal_matter, kind=EngagementKind.SURVEY, title="liikmed", actor=specialist
    )

    body = _detail(signed_in, normal_matter)

    assert "factspanel" not in body
    assert 'id="teema-faktid"' not in body
    assert 'id="kaasamine"' not in body
    assert "hidden" not in body.split('id="teema-vaade"')[1][:200]
    # The records are untouched.
    assert MatterImportantDate.objects.filter(matter=normal_matter).exists()
    assert MatterEngagement.objects.filter(matter=normal_matter).exists()


# ===========================================================================
# PROCESS STRIP — §29 – §32
# ===========================================================================


def _sent(matter, capture_evidence, organisation, *, when, title="Koja arvamus", visibility=""):
    """A sent opinion, recorded through the door a person actually uses.

    `register_sent_opinion` rather than a factory row, for two reasons.
    `submissions_sent_requires_timestamp_and_evidence` is a check constraint and
    not a convention, so a fixture that set the status directly would be testing
    a row the product cannot produce — and the send event this service writes is
    what puts `Arvamus välja` in the chronology, which is half of what the
    rename below has to leave alone.
    """
    version = capture_evidence(
        matter,
        b"%PDF-1.4 synthetic evidence",
        "koja-arvamus.pdf",
        "application/pdf",
        title=title,
        role=DocumentRole.KODA_SUBMISSION_FINAL,
        visibility_override=visibility,
    )
    return register_sent_opinion(
        document=version.document,
        version=version,
        title=title,
        recipients=[organisation],
        sent_at=when,
        sent_at_precision=SentAtPrecision.DATE,
    )


def _started(matter, *, days_ago: int):
    """Backdate the Matter's creation, since `created_at` is `auto_now_add`.

    Without this every fixture below would have a Matter created *today* and an
    opinion sent in the past, which is a shape the product does not produce and
    which sorts `Koja arvamus` ahead of `Alustatud`.
    """
    from app.matters.models import Matter

    Matter.objects.filter(pk=matter.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
    matter.refresh_from_db()
    return matter


# -- A, B, C: Alustatud ------------------------------------------------------


def test_a_native_matter_starts_with_alustatud(signed_in, specialist):
    """**A.** The Matter this system created says when the work started."""
    matter = factories.MatterFactory(owner=specialist, origin=MatterOrigin.NATIVE, stage=None)

    steps = process_steps(matter=matter, user=specialist)
    assert [step.label for step in steps] == ["Alustatud"]
    assert steps[0].display == format_estonian_date(timezone.localdate())

    body = _detail(signed_in, matter)
    assert body.count('class="tl-step"') == 1
    # `Loodud` was the old wording, and it named the database row rather than
    # the act. It is gone from the strip entirely.
    assert "Loodud" not in body[body.index("tl-strip") : body.index('id="ajalugu-loend"')]


def test_an_imported_matter_takes_no_alustatud_from_created_at(signed_in, specialist):
    """**B.** `created_at` on a register-archive row is the moment the importer
    wrote it, which for a 2019 file is a fact about a migration. A bare imported
    Matter therefore draws no strip at all rather than one lonely dot."""
    matter = factories.MatterFactory(
        owner=specialist, origin=MatterOrigin.LEGACY_IMPORT, stage=None
    )

    assert process_steps(matter=matter, user=specialist) == []
    assert "tl-strip" not in _detail(signed_in, matter)


def test_a_promoted_archive_row_takes_no_alustatud_either(specialist):
    """The origin test is the exact value, not «not LEGACY_IMPORT».

    `PROMOTED_LEGACY` is an archive row somebody activated, and its `created_at`
    is the same import timestamp — so a rule written as an exclusion would have
    fabricated an `Alustatud` for every promoted file.
    """
    matter = factories.MatterFactory(
        owner=specialist, origin=MatterOrigin.PROMOTED_LEGACY, stage=None
    )

    assert process_steps(matter=matter, user=specialist) == []


def test_saabus_is_not_alustatud(signed_in, specialist):
    """**C.** `Saabus` is when Koda received something; `Alustatud` is when the
    work started. They are different facts, and an imported Matter carrying only
    the first still gets no milestone from it."""
    matter = factories.MatterFactory(
        owner=specialist,
        origin=MatterOrigin.LEGACY_IMPORT,
        stage=None,
        received_date=date(2019, 3, 14),
    )

    assert process_steps(matter=matter, user=specialist) == []
    body = _detail(signed_in, matter)
    # The fact itself is untouched: it reads in the header, where it belongs.
    assert "14.3.2019" in body
    assert "tl-strip" not in body


# -- D, E: Hetkeseis ---------------------------------------------------------


@pytest.mark.parametrize("key", ["idea", "parliament"])
def test_a_stage_draws_no_milestone_whatever_it_is(signed_in, specialist, key):
    """**D, E.** No stage is whitelisted, and the two ends of the vocabulary
    are the parametrised cases: `Idee` is the earliest and `Riigikogus` the one
    a closed Matter used to be left reading as `Riigikogus · praegu`.

    `Hetkeseis` is where the file stands *now*, it is stated in the header, and
    a strip column repeating it is what made that sentence possible
    (docs/adr/0074 §12).
    """
    stage = StageVocabulary.objects.get(key=key)
    matter = factories.MatterFactory(owner=specialist, origin=MatterOrigin.NATIVE, stage=stage)
    change_stage(matter=matter, stage=stage, actor=specialist)

    assert [step.label for step in process_steps(matter=matter, user=specialist)] == ["Alustatud"]

    body = _detail(signed_in, matter)
    strip = body[body.index("tl-strip") : body.index('id="ajalugu-loend"')]
    assert stage.label_et not in strip
    # And it is still in the header, which is the one place it is stated.
    assert stage.label_et in body[: body.index("tl-strip")]


def test_the_strip_never_says_praegu(signed_in, specialist, stage):
    """**§10.** Every milestone is historical, so no column is the current one
    and none carries the `praegu` suffix."""
    matter = factories.MatterFactory(owner=specialist, origin=MatterOrigin.NATIVE, stage=stage)

    assert all(step.date_line != "praegu" for step in process_steps(matter=matter, user=specialist))

    body = _detail(signed_in, matter)
    strip = body[body.index("tl-strip") : body.index('id="ajalugu-loend"')]
    assert "praegu" not in strip
    for gone in ("is-current", "is-todo"):
        assert gone not in body


# -- F, G: the structured facts ---------------------------------------------


def test_an_important_date_draws_no_milestone(signed_in, normal_matter, specialist):
    """**F.** `Oluline tähtaeg` stays canonical and keeps its fact section, its
    chronology row once it has passed, and every work surface — it is not a
    major procedural act and no longer draws a column, in either direction."""
    ahead = timezone.localdate() + timedelta(days=21)
    behind = timezone.localdate() - timedelta(days=21)
    for title, when in (("Tulevane tähtaeg", ahead), ("Möödunud tähtaeg", behind)):
        MatterImportantDate.objects.create(
            matter=normal_matter,
            title=title,
            date_value=when,
            period_end=when,
            date_precision=DatePrecision.EXACT,
            status=FactStatus.ACTIVE,
            created_by=specialist,
        )

    assert [step.label for step in process_steps(matter=normal_matter, user=specialist)] == [
        "Alustatud"
    ]

    body = _detail(signed_in, normal_matter)
    strip = body[body.index("tl-strip") : body.index('id="ajalugu-loend"')]
    assert "Tulevane tähtaeg" not in strip
    assert "Möödunud tähtaeg" not in strip
    # Not deleted: both rows are still there, and the one that has happened
    # still reads in the chronology.
    assert MatterImportantDate.objects.filter(matter=normal_matter).count() == 2
    assert "Möödunud tähtaeg" in body[body.index('id="ajalugu-loend"') :]


def test_an_effective_date_draws_no_milestone(signed_in, normal_matter, specialist):
    """**G.** `Jõustumine` is the same: canonical, readable, and not a step."""
    ahead = timezone.localdate() + timedelta(days=120)
    MatterEffectiveDate.objects.create(
        matter=normal_matter,
        date_value=ahead,
        period_end=ahead,
        date_precision=DatePrecision.EXACT,
        description="Pakendiseadus",
        status=FactStatus.ACTIVE,
        created_by=specialist,
    )

    assert [step.label for step in process_steps(matter=normal_matter, user=specialist)] == [
        "Alustatud"
    ]
    assert MatterEffectiveDate.objects.filter(matter=normal_matter).count() == 1

    body = _detail(signed_in, normal_matter)
    strip = body[body.index("tl-strip") : body.index('id="ajalugu-loend"')]
    for gone in ("Pakendiseadus", "Jõustub"):
        assert gone not in strip


def test_the_strip_has_no_future_grammar_left(signed_in, normal_matter, specialist):
    """**§11.** With both dated facts gone the strip has no future source, so
    the day count and the horizon go with them.

    Asserted on a date inside the old sixty-day horizon, which is exactly where
    the retired `N p` suffix used to appear. The countdown itself is not
    retired — it still reads on the header deadline and on every work surface.
    """
    ahead = timezone.localdate() + timedelta(days=21)
    MatterImportantDate.objects.create(
        matter=normal_matter,
        title="Riigikogu I lugemine",
        date_value=ahead,
        period_end=ahead,
        date_precision=DatePrecision.EXACT,
        status=FactStatus.ACTIVE,
        created_by=specialist,
    )

    strip_module = __import__("app.matters.process_timeline", fromlist=["x"])
    for dead in ("STATE_CURRENT", "STATE_TODO", "CURRENT_SUFFIX", "DAYS_HORIZON", "DAYS_UNIT"):
        assert not hasattr(strip_module, dead), dead

    body = _detail(signed_in, normal_matter)
    strip = body[body.index("tl-strip") : body.index('id="ajalugu-loend"')]
    assert "21 p" not in strip


# -- H: generic engagement ---------------------------------------------------


@pytest.mark.parametrize(
    "kind",
    [
        EngagementKind.SURVEY,
        EngagementKind.MEETING,
        EngagementKind.EMAIL_CAMPAIGN,
        EngagementKind.WEB_CALL,
        EngagementKind.OTHER,
    ],
)
def test_a_generic_engagement_draws_no_milestone(signed_in, normal_matter, specialist, kind):
    """**H.** A `Kaasamine` is a canonical record, a chronology row and detail
    information — it is not automatically a procedural milestone.

    A future `Arvamuste kogumine` round will be a specifically identifiable
    thing with a start and received batches, and *that* will earn a column.
    Guessing today that every consultation is one would draw a milestone the
    domain cannot yet distinguish.
    """
    add_engagement(
        matter=normal_matter,
        kind=kind,
        title="liikmed",
        occurred_on=timezone.localdate(),
        actor=specialist,
    )

    assert [step.label for step in process_steps(matter=normal_matter, user=specialist)] == [
        "Alustatud"
    ]

    body = _detail(signed_in, normal_matter)
    strip = body[body.index("tl-strip") : body.index('id="ajalugu-loend"')]
    assert "liikmed" not in strip
    # Still a record, and still a chronology row.
    assert MatterEngagement.objects.filter(matter=normal_matter).count() == 1
    assert "Kaasamine: liikmed" in body[body.index('id="ajalugu-loend"') :]


# -- I, J, K: Koja arvamus ---------------------------------------------------


def test_a_sent_submission_is_koja_arvamus(
    signed_in, normal_matter, specialist, organisation, capture_evidence
):
    """**I.** The one milestone that names an act rather than a stored title —
    and it is `Koja arvamus`, not `Arvamus välja`."""
    _started(normal_matter, days_ago=30)
    _sent(normal_matter, capture_evidence, organisation, when=timezone.now() - timedelta(days=3))

    steps = process_steps(matter=normal_matter, user=specialist)
    assert [step.label for step in steps] == ["Alustatud", "Koja arvamus"]

    body = _detail(signed_in, normal_matter)
    strip = body[body.index("tl-strip") : body.index('id="ajalugu-loend"')]
    assert "Koja arvamus" in strip
    assert "Arvamus välja" not in strip
    # The chronology keeps its own wording, which is a sentence about an event.
    assert "Arvamus välja" in body[body.index('id="ajalugu-loend"') :]


def test_two_sent_opinions_draw_two_milestones(
    normal_matter, specialist, organisation, capture_evidence
):
    """**J.** A supplementary opinion months after the first is a second real
    procedural act, and collapsing the two would lose it."""
    _started(normal_matter, days_ago=200)
    first = timezone.now() - timedelta(days=120)
    second = timezone.now() - timedelta(days=20)
    _sent(normal_matter, capture_evidence, organisation, when=first, title="Koja arvamus")
    _sent(normal_matter, capture_evidence, organisation, when=second, title="Koja täiendav arvamus")

    steps = process_steps(matter=normal_matter, user=specialist)

    assert [step.label for step in steps] == ["Alustatud", "Koja arvamus", "Koja arvamus"]
    assert [step.display for step in steps[1:]] == [
        format_estonian_date(timezone.localtime(first).date()),
        format_estonian_date(timezone.localtime(second).date()),
    ]


def test_a_draft_submission_is_not_a_milestone(normal_matter, specialist):
    """**K.** A draft is work in progress, not a send. Neither is a file: the
    canonical record of a send is a SENT `Submission` (post-QA R2-01)."""
    factories.SubmissionFactory(matter=normal_matter, title="Koostamisel")

    assert [step.label for step in process_steps(matter=normal_matter, user=specialist)] == [
        "Alustatud"
    ]


# -- L, M, N: Lõpetatud ------------------------------------------------------


def test_a_closed_matter_ends_with_lopetatud(signed_in, normal_matter, specialist):
    """**L.** The milestone is named for the step, not for its outcome."""
    close_matter(
        matter=normal_matter,
        disposition=Disposition.RESPONSE_COMPLETE,
        actor=specialist,
        reason="Arvamus esitatud.",
    )
    normal_matter.refresh_from_db()

    steps = process_steps(matter=normal_matter, user=specialist)
    assert [step.label for step in steps] == ["Alustatud", "Lõpetatud"]
    assert steps[-1].display == format_estonian_date(timezone.localdate())
    # The disposition is secondary information and reads as the column's title.
    assert steps[-1].detail == "Vastus esitatud ja järeltegevus tehtud"

    body = _detail(signed_in, normal_matter)
    strip = body[body.index("tl-strip") : body.index('id="ajalugu-loend"')]
    assert "Lõpetatud" in strip
    assert 'title="Vastus esitatud ja järeltegevus tehtud"' in strip
    # And the outcome has not replaced the name of the step.
    for gone in (">Menetlus lõppes<", ">Jõustus<", ">Loobuti<"):
        assert gone not in strip


def test_an_open_matter_has_no_lopetatud(normal_matter, specialist):
    """**M.**"""
    assert [step.label for step in process_steps(matter=normal_matter, user=specialist)] == [
        "Alustatud"
    ]


def test_a_reopened_matter_is_not_shown_as_finished(normal_matter, specialist):
    """**N.** The `MATTER_CLOSED` event survives forever and is true history —
    a strip that went looking for one would call a currently-open file finished.

    Read off the Matter's current state instead: `reopen_matter` clears
    `closed_at`, and `matters_closure_fields_consistent` makes the database
    refuse `is_open=True` beside a `closed_at`.
    """
    close_matter(
        matter=normal_matter,
        disposition=Disposition.INITIATIVE_WITHDRAWN,
        actor=specialist,
        reason="",
    )
    normal_matter.refresh_from_db()
    reopen_matter(matter=normal_matter, actor=specialist, reason="Menetlus jätkub.")
    normal_matter.refresh_from_db()

    assert ChangeEvent.objects.filter(
        matter=normal_matter, event_type=ChangeEventType.MATTER_CLOSED
    ).exists(), "the closure is still history"
    assert [step.label for step in process_steps(matter=normal_matter, user=specialist)] == [
        "Alustatud"
    ]


def test_a_closed_archive_row_with_no_closure_day_draws_no_column(specialist):
    """`matters_closure_fields_consistent` exempts an ARCHIVE row from carrying
    a `closed_at`, because an imported register row is not made to invent a day
    it never had. A milestone with no date is not a position in a process."""
    matter = factories.ArchiveMatterFactory(is_open=False)

    assert process_steps(matter=matter, user=specialist) == []


def test_the_three_milestones_read_in_procedural_order(
    normal_matter, specialist, organisation, capture_evidence
):
    """The whole vocabulary on one Matter, oldest first — and a send on the day
    of closure keeps the order it was read in rather than reshuffling."""
    _sent(normal_matter, capture_evidence, organisation, when=timezone.now())
    close_matter(
        matter=normal_matter,
        disposition=Disposition.COMPLETED,
        actor=specialist,
        reason="",
    )
    normal_matter.refresh_from_db()

    steps = process_steps(matter=normal_matter, user=specialist)

    assert [step.label for step in steps] == ["Alustatud", "Koja arvamus", "Lõpetatud"]
    assert [step.sort_on for step in steps] == sorted(step.sort_on for step in steps)


def test_the_strip_is_ordered_by_date_even_when_a_send_predates_its_matter(
    normal_matter, specialist, organisation, capture_evidence
):
    """A back-filled send sorts before `Alustatud`, and that is deliberate.

    A native Matter created today can register an opinion that went out in June
    — `Registreeri saatmine` exists precisely to record a send that already
    happened. Its `created_at` is then a fact about when somebody wrote the file
    down, not about when the work started, and the two dates disagree.

    The strip sorts by date, which is the one rule that does not misstate a day
    it is showing. Pinning `Alustatud` leftmost instead would print the dates out
    of order, and suppressing it would invent a credibility rule this round did
    not decide. **Flagged rather than solved**: whether `Alustatud` is the right
    source for a back-filled Matter at all is the kind of question the reserved
    `Arvamuste kogumine` and `Pöördumine` sources are also waiting on
    (docs/adr/0074 §12.1).
    """
    _sent(
        normal_matter,
        capture_evidence,
        organisation,
        when=timezone.now() - timedelta(days=100),
    )

    steps = process_steps(matter=normal_matter, user=specialist)

    assert [step.label for step in steps] == ["Koja arvamus", "Alustatud"]
    assert [step.sort_on for step in steps] == sorted(step.sort_on for step in steps)


# -- O: Töövõit --------------------------------------------------------------


def test_a_work_victory_is_not_a_procedural_step(specialist):
    """**O, §31.** A win is a chronology milestone, not a stage of the
    proceeding. Kept from the previous round, and still true."""
    matter = factories.MatterFactory(owner=specialist, origin=MatterOrigin.NATIVE, stage=None)
    MatterWorkVictory.objects.create(
        matter=matter,
        title="Üleminekuaeg pikenes",
        status=WorkVictoryStatus.CONFIRMED,
        confirmed_by=specialist,
        confirmed_at=timezone.now(),
        created_by=specialist,
    )

    labels = [step.label for step in process_steps(matter=matter, user=specialist)]

    assert labels == ["Alustatud"]


# -- P: authorization --------------------------------------------------------


def test_a_restricted_opinion_changes_no_geometry(
    client, normal_matter, specialist, reader, organisation, capture_evidence
):
    """**P, §18.** Scoped before it is derived, not filtered afterwards.

    Sent opinions are the only source this strip still reads through a
    `visible_to`, so they are the only source that can leak — and a reader who
    may not see one must not learn it exists from the dot count, the connector
    count, the labels, the dates or the strip's width.

    A READER, not a second lawyer. Both lawyer roles read RESTRICTED content by
    design, so a specialist is the wrong oracle for «cannot see it»
    (`ROLES_WITH_RESTRICTED_ACCESS`, docs/adr/0042).
    """
    _started(normal_matter, days_ago=30)
    client.force_login(reader)
    url = reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk})

    def strip_of() -> str:
        body = client.get(url).content.decode()
        if "tl-strip" not in body:
            return ""
        return body[body.index("tl-strip") : body.index('id="ajalugu-loend"')]

    before = strip_of()
    _sent(
        normal_matter,
        capture_evidence,
        organisation,
        when=timezone.now() - timedelta(days=2),
        title="Salajane arvamus",
        visibility=Visibility.RESTRICTED,
    )
    after = strip_of()

    assert before == after
    assert "Koja arvamus" not in after
    # And the owner does see it, so the fixture is not vacuous.
    assert [step.label for step in process_steps(matter=normal_matter, user=specialist)] == [
        "Alustatud",
        "Koja arvamus",
    ]


# ===========================================================================
# CHRONOLOGY — §33 – §38
# ===========================================================================


def test_the_ajajoon_head_carries_only_the_label_and_the_count(signed_in, normal_matter):
    body = _detail(signed_in, normal_matter)
    head = body[body.index('id="ajajoon"') :]
    head = head[: head.index("</summary>")]

    assert "Ajajoon" in head
    assert "kirjet" in head
    assert "uxtl__preview" not in head
    assert "timelinefilter" not in body
    for gone in (">Kõik<", ">Sissekanded<", ">Sündmused<"):
        assert gone not in body


def test_the_chronology_has_two_row_kinds_and_no_third(signed_in, normal_matter, specialist):
    from app.matters.services import add_entry

    add_entry(matter=normal_matter, author=specialist, body="<p>Helistasin ministeeriumisse.</p>")
    add_engagement(
        matter=normal_matter, kind=EngagementKind.SURVEY, title="liikmed", actor=specialist
    )

    body = _detail(signed_in, normal_matter)
    chronology = body[body.index('id="ajalugu-loend"') :]

    assert "uxtl__dot--ms" in chronology, "milestone rows draw the 12px accent dot"
    assert "uxtl__dot" in chronology, "work rows draw the 6px muted dot"
    assert "uxtl__sysrow" not in chronology, "no folded run"
    assert "groupfacts" not in chronology


def test_a_structured_fact_is_shown_once(signed_in, normal_matter, specialist):
    """**§36.** Not «Kaasamine lisatud» *and* «Kaasamine: liikmed»."""
    add_engagement(
        matter=normal_matter, kind=EngagementKind.SURVEY, title="liikmed", actor=specialist
    )

    body = _detail(signed_in, normal_matter)
    chronology = body[body.index('id="ajalugu-loend"') :]

    assert chronology.count("Kaasamine: liikmed") == 1
    assert "Kaasamine lisatud" not in chronology
    assert "lisas kaasamise" not in chronology


def test_a_work_entry_carries_its_time_inline_and_its_file(signed_in, normal_matter):
    _compose(
        signed_in,
        normal_matter,
        body="<p>Uus eelnõu versioon ministeeriumilt.</p>",
        attachment=SimpleUploadedFile(
            "eelnou_v3.pdf", b"%PDF-1.4 z", content_type="application/pdf"
        ),
    )

    body = _detail(signed_in, normal_matter)
    chronology = body[body.index('id="ajalugu-loend"') :]

    assert "lisas dokumendi" in chronology
    assert 'class="uxtl__time"' in chronology
    # The time is inside the meta line, not in a column of its own.
    meta = chronology[chronology.index('class="uxtl__meta"') :]
    meta = meta[: meta.index("</p>")]
    assert "uxtl__time" in meta
    # The file is a link to the exact bytes.
    version = Document.objects.get(matter=normal_matter).versions.get()
    assert reverse("documents:download", kwargs={"pk": version.pk}) in chronology
    assert "eelnou_v3.pdf" in chronology


def test_a_save_that_set_a_next_step_shows_the_pill(signed_in, normal_matter):
    _compose(
        signed_in,
        normal_matter,
        body="<p>Rääkisin ministeeriumiga.</p>",
        next_text="Ootan ministeeriumi vastust",
        next_date="20.10.2026",
    )

    body = _detail(signed_in, normal_matter)

    assert "uxtl__next" in body
    assert "Ootan ministeeriumi vastust" in body


def test_the_chronology_is_newest_first(signed_in, normal_matter, specialist):
    from app.matters.services import add_entry

    add_entry(matter=normal_matter, author=specialist, body="<p>Esimene.</p>")
    add_entry(matter=normal_matter, author=specialist, body="<p>Teine.</p>")

    body = _detail(signed_in, normal_matter)
    chronology = body[body.index('id="ajalugu-loend"') :]

    assert chronology.index("Teine.") < chronology.index("Esimene.")


def test_a_dated_fact_reaches_the_chronology_only_once_it_has_happened(
    signed_in, normal_matter, specialist
):
    """The chronology answers what has *happened*, and that rule is unchanged.

    What changed around it is that the strip no longer answers «where is this
    going»: it is a sparse list of major procedural acts, all of them
    historical. So an `Oluline tähtaeg` still ahead of us is on neither surface
    of the Teema tab — it reads in its own fact section and on the work
    surfaces, which is where a date somebody is waiting for belongs
    (docs/adr/0074 §12, §15).
    """
    ahead = timezone.localdate() + timedelta(days=30)
    behind = timezone.localdate() - timedelta(days=30)
    for title, when in (("Riigikogu I lugemine", ahead), ("Kooskõlastusring", behind)):
        MatterImportantDate.objects.create(
            matter=normal_matter,
            title=title,
            date_value=when,
            period_end=when,
            date_precision=DatePrecision.EXACT,
            status=FactStatus.ACTIVE,
            created_by=specialist,
        )

    body = _detail(signed_in, normal_matter)
    chronology = body[body.index('id="ajalugu-loend"') :]
    strip = body[body.index("tl-strip") : body.index('id="ajalugu-loend"')]

    assert "Riigikogu I lugemine" not in strip
    assert "Riigikogu I lugemine" not in chronology
    assert "Kooskõlastusring" not in strip
    assert "Kooskõlastusring" in chronology


# ===========================================================================
# AUTHORIZATION — §32, §56
# ===========================================================================


#: The one thing on this page that legitimately differs between two identical
#: renders. Django rotates the CSRF token per request, so comparing two raw
#: pages byte-for-byte compares the token and nothing else — which would make
#: the strong oracle below fail on every run and prove nothing.
_CSRF = re.compile(r'name="csrfmiddlewaretoken" value="[^"]*"')


def _page_regions(client, matter) -> tuple[str, str, str]:
    """The page and its two derived regions, with the rotating token masked."""
    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()
    body = _CSRF.sub('name="csrfmiddlewaretoken" value="X"', body)
    grid = body[body.index('id="teema-vaade"') :]
    strip = grid[: grid.index('id="ajalugu-loend"')]
    chronology = grid[grid.index('id="ajalugu-loend"') :]
    return grid, strip, chronology


@pytest.mark.parametrize(
    "restricted",
    ["engagement", "important_date", "effective_date", "work_victory", "submission"],
)
def test_a_restricted_child_changes_nothing_for_an_unauthorized_reader(
    client, normal_matter, reader, specialist, organisation, capture_evidence, restricted
):
    """**§56, the strong oracle.** The page before adding a RESTRICTED child and
    the page after it must be identical for a reader who may not see it — text,
    row count, strip steps, connector positions, ordering and empty states.

    Scoped *before* the presentation is derived, never filtered afterwards: a
    strip built from every child and then pruned would leave the column count and
    the connector spacing behind (AUTH-003, docs/adr/0074 §13).
    """
    # A READER, not a second lawyer. Both lawyer roles read RESTRICTED content
    # by design, so a specialist is the wrong oracle for "cannot see it"
    # (`ROLES_WITH_RESTRICTED_ACCESS`, docs/adr/0042).
    client.force_login(reader)
    normal_matter.visibility = Visibility.NORMAL
    normal_matter.save(update_fields=["visibility"])
    before = _page_regions(client, normal_matter)

    today = timezone.localdate()
    if restricted == "engagement":
        record = add_engagement(
            matter=normal_matter,
            kind=EngagementKind.SURVEY,
            title="Piiratud küsitlus",
            occurred_on=today - timedelta(days=1),
            actor=specialist,
        )
    elif restricted == "important_date":
        record = MatterImportantDate.objects.create(
            matter=normal_matter,
            title="Piiratud tähtaeg",
            date_value=today - timedelta(days=1),
            period_end=today - timedelta(days=1),
            date_precision=DatePrecision.EXACT,
            status=FactStatus.ACTIVE,
            created_by=specialist,
        )
    elif restricted == "effective_date":
        record = MatterEffectiveDate.objects.create(
            matter=normal_matter,
            date_value=today - timedelta(days=1),
            period_end=today - timedelta(days=1),
            date_precision=DatePrecision.EXACT,
            description="Piiratud jõustumine",
            status=FactStatus.ACTIVE,
            created_by=specialist,
        )
    elif restricted == "work_victory":
        record = MatterWorkVictory.objects.create(
            matter=normal_matter,
            title="Piiratud võit",
            status=WorkVictoryStatus.CONFIRMED,
            confirmed_by=specialist,
            confirmed_at=timezone.now(),
            created_by=specialist,
        )
    else:
        # The one source the strip still reads through a `visible_to`, and
        # therefore the one that can still leak. Its restriction is inherited
        # from the file it was sent as, which is how `register_sent_opinion`
        # arranges it — a submission may not be less restricted than its own
        # evidence.
        record = _sent(
            normal_matter,
            capture_evidence,
            organisation,
            when=timezone.now() - timedelta(days=1),
            title="Piiratud arvamus",
            visibility=Visibility.RESTRICTED,
        )
    record.visibility_override = Visibility.RESTRICTED
    record.save(update_fields=["visibility_override"])

    after = _page_regions(client, normal_matter)

    assert after == before, "a restricted child changed the page for a reader who may not see it"


def test_the_count_matches_what_the_reader_can_actually_see(
    client, normal_matter, reader, specialist
):
    """**§38.** «14 kirjet» when the reader can see thirteen is a disclosure."""
    client.force_login(reader)
    add_engagement(
        matter=normal_matter,
        kind=EngagementKind.SURVEY,
        title="Avalik küsitlus",
        occurred_on=timezone.localdate(),
        actor=specialist,
    )
    hidden = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.MEETING,
        title="Piiratud koosolek",
        occurred_on=timezone.localdate(),
        actor=specialist,
    )
    hidden.visibility_override = Visibility.RESTRICTED
    hidden.save(update_fields=["visibility_override"])

    items, _more = matter_timeline(matter=normal_matter, user=reader, limit=50)
    body = client.get(
        reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk})
    ).content.decode()

    assert f"{len(items)} kirjet" in body
    assert "Piiratud koosolek" not in body


# ===========================================================================
# RAIL — §39 – §45
# ===========================================================================


def test_the_rail_holds_the_four_target_blocks_in_order(signed_in, normal_matter):
    body = _detail(signed_in, normal_matter)
    rail = body[body.index('id="teema-andmed"') :]
    rail = rail[: rail.index("</aside>")]

    positions = [
        rail.index(label)
        for label in ("Teema andmed", "Koja arvamus", "Seotud materjalid", "Märkmed")
    ]
    assert positions == sorted(positions)
    assert "Arvamust ei ole lisatud." in rail
    assert "Seotud teemasid ega taustmaterjali ei ole valitud." in rail


def test_teema_andmed_holds_the_four_target_rows(signed_in, normal_matter):
    body = _detail(signed_in, normal_matter)
    card = body[body.index('id="teema-andmed"') :]
    card = card[: card.index('id="koja-arvamus"')]

    for row in ("Teemaviide", "Menetlusliik", "Kellelt", "Kellele"):
        assert row in card
    for gone in ("Saabus", "Muu valdkond", "Andmeklass", "Märgi testandmeteks"):
        assert gone not in card


def test_the_notes_block_has_no_save_button(signed_in, normal_matter):
    body = _detail(signed_in, normal_matter)
    note = body[body.index("railcard--note") :]
    note = note[: note.index("</aside>")]

    assert "Märkmed" in note
    assert "Vabad märkmed…" in note
    assert 'type="submit"' not in note
    assert "Salvestub automaatselt" not in note
    assert "ainult sulle nähtav mustand" not in note


def test_the_notes_autosave_reports_when_it_landed(signed_in, normal_matter, specialist):
    url = reverse("matters:save_note", kwargs={"pk": normal_matter.pk})

    response = signed_in.post(
        url, {"markmed-body": "RaM kontakt: Liina."}, headers={"HX-Request": "true"}
    )
    assert response.status_code == 200
    assert "Salvestatud" in response.content.decode()

    # It survives a reload, and there is exactly one row.
    from app.matters.models import MatterPersonalNote

    assert MatterPersonalNote.objects.filter(matter=normal_matter, author=specialist).count() == 1
    body = _detail(signed_in, normal_matter)
    assert "RaM kontakt: Liina." in body
    assert "Salvestatud" in body

    # A second save does not create a second row.
    signed_in.post(url, {"markmed-body": "Muudetud."}, headers={"HX-Request": "true"})
    assert MatterPersonalNote.objects.filter(matter=normal_matter, author=specialist).count() == 1


def test_an_unsaved_note_claims_no_save_time(signed_in, normal_matter):
    """The hint renders nothing at all until there is something true to say.

    And it is its own swap target, so a refusal — htmx swaps on 2xx only —
    leaves whatever was there rather than announcing a save that did not happen.
    That is the whole mechanism: there is no state in which the box says
    `Salvestatud` without a stored `updated_at` behind it."""
    body = _detail(signed_in, normal_matter)
    hint = body[body.index('id="teema-markme-seis"') :]
    hint = hint[: hint.index("</span>")]

    assert "Salvestatud" not in hint
    # The form swaps only the hint, never the textarea somebody is typing into.
    note = body[body.index("railcard--note") : body.index("</aside>")]
    assert 'hx-target="#teema-markme-seis"' in note


def test_a_refused_note_save_is_not_reported_as_saved(normal_matter, specialist):
    """`save_personal_note` refuses an unauthenticated author, and the view
    answers 400 rather than rendering a hint for a write that did not happen."""
    from django.contrib.auth.models import AnonymousUser

    from app.core.errors import DomainError
    from app.matters.services import save_personal_note

    with pytest.raises(DomainError):
        save_personal_note(matter=normal_matter, author=AnonymousUser(), body="x")


def test_a_note_is_private_to_its_author(client, normal_matter, other_specialist, specialist):
    from app.matters.services import save_personal_note

    save_personal_note(matter=normal_matter, author=specialist, body="Ainult minu silmadele.")
    client.force_login(other_specialist)

    assert "Ainult minu silmadele." not in _detail(client, normal_matter)


# ===========================================================================
# SPECIAL STATES — §12, §51, §52
# ===========================================================================


def test_a_restricted_matter_still_says_so(signed_in, restricted_matter):
    body = _detail(signed_in, restricted_matter)

    assert "Piiratud" in body
    # And the target hierarchy is what it is shown in.
    assert 'id="teema-vaade-wrap"' in body


def test_a_test_record_still_carries_its_badge(signed_in, normal_matter):
    from app.matters.enums import MatterDataClass

    normal_matter.data_class = MatterDataClass.TEST
    normal_matter.save(update_fields=["data_class"])

    body = _detail(signed_in, normal_matter)

    assert "badge--test" in body
    # …and the way to change it is not on this page any more.
    assert "Märgi pärisandmeteks" not in body
    normal_matter.refresh_from_db()
    assert normal_matter.is_test_data


def test_a_matter_with_nothing_on_it_renders_a_short_deliberate_page(signed_in, specialist):
    matter = factories.MatterFactory(
        owner=specialist, response_deadline=None, brief_summary="", stage=None
    )

    body = _detail(signed_in, matter)

    assert 'id="ajajoon"' in body, "the section still exists"
    # One dot and no connector: `Alustatud`, and nothing else has happened yet.
    assert body.count('class="tl-step"') == 1
    assert "factspanel" not in body
    assert 'id="kaasamine"' not in body


def test_a_reader_sees_the_same_reading_hierarchy_without_the_write_controls(
    client, normal_matter, reader, specialist
):
    _action(normal_matter, specialist)
    client.force_login(reader)

    body = _detail(client, normal_matter)

    assert 'id="praegune-tegevus"' in body
    assert 'id="ajajoon"' in body
    assert 'id="teema-andmed"' in body
    assert "✓ Tehtud" not in body
    assert "Mida tegid?" not in body
    assert 'id="lisa-teemale"' not in body


# ===========================================================================
# PRAEGUNE TEGEVUS — §14 as docs/adr/0075 §3 supersedes it
# ===========================================================================


def test_the_current_action_zone_holds_the_task_and_exactly_one_way_to_finish_it(
    signed_in, normal_matter, specialist
):
    _action(normal_matter, specialist)

    body = _detail(signed_in, normal_matter)
    zone = body[body.index('id="praegune-tegevus"') : body.index('id="lisa-teemale"')]

    assert "Praegune tegevus" in zone
    assert "Koosta koja arvamus" in zone
    assert "Mida tegid?" in zone
    assert "Muuda" in zone
    # **The whole point of the round.** Completion is the result being saved,
    # so there is no second control that completes without one.
    for gone in ("✓ Tehtud", "Märgi tehtuks", "Tehtud</button>"):
        assert gone not in zone
    # And not the retired controls or vocabulary.
    assert "Lükka edasi" not in body
    for gone in ("TEEN", "OOTAN", "JÄLGIN"):
        assert gone not in zone
