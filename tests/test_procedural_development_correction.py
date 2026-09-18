"""Correcting a `Menetluse areng` that is already on the chronology (QA-06).

The product rule this module asserts:

    A `Menetluse areng` already filed may be corrected — its `Sündmus`, its
    period and its `Juristi märkus` — by anybody who may author business
    content, **on an open Matter only**, from the chronology row it is on.

QA-06 is what this exists for. The canonical service has accepted a correction
since docs/adr/0091 §5 — `correct_procedural_development`, its conflict token,
its future-date invariant, its closed-Matter refusal and its audit event were all
already in place and are unchanged here. What there was no way to do was *reach*
it: the chronology rendered a development through the generic milestone block,
which carries no `Muuda`, so a lawyer who recorded «Eelnõu jõudis Riigikokku» and
meant «Komisjon arutas eelnõu» had nowhere to say so — on the one record whose
whole purpose is to let the file state what the procedure actually did.

**The correction's scope is this record and nothing beside it**, which is the
claim §P and §Q below exist to hold. A `+ Menetluse areng` save may write four
canonical things in one transaction: the development, its evidence, a
`Hetkeseis` change and a `Järgmiseks`. Correcting the development's sentence
afterwards does not rewind the stage, does not supersede the next action and does
not touch the files. Those are separately correctable facts with their own
surfaces; what ties the original writes together is the `operation_id` they
share, not a claim that this row owns them (docs/adr/0091 §5.4, docs/adr/0092 §6).

**A correction is not an undo.** «Correct what this recorded procedural
development says happened» is the whole of it. Making the original *operation*
reversible is a different product question and is not what this route is.
"""

from __future__ import annotations

import datetime as dt
import uuid as _uuid

import pytest
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.dates import format_estonian_date
from app.core.enums import Visibility
from app.matters.models import MatterProceduralDevelopment
from app.matters.services import (
    DEVELOPMENT_CANNOT_BE_FUTURE,
    DEVELOPMENT_EDIT_CONFLICT,
    DEVELOPMENT_NEEDS_TITLE,
    close_matter,
    record_procedural_development,
)
from app.matters.timeline import DEVELOPMENT_DATE_UNKNOWN, LAWYER_NOTE_LABEL, matter_timeline
from app.matters.workspace import add_procedural_development
from app.workflow.enums import DatePrecision, Disposition
from tests import factories

pytestmark = pytest.mark.django_db


# -- harness ----------------------------------------------------------------


def _days_ago(days: int) -> dt.date:
    """A day in the past, relative to the clock rather than written down.

    A chronology row only renders once its day has arrived, so a hard-coded date
    is a test that starts passing or failing depending on when it is run.
    """
    return timezone.localdate() - dt.timedelta(days=days)


#: What the ministry did, and when the lawyer says it did it.
HAPPENED = _days_ago(3)
HEADLINE = "Ministeerium saatis uue eelnõu versiooni"
NOTE = "Versioon ei arvesta meie varasemat ettepanekut."


def _url(matter, development) -> str:
    return reverse(
        "matters:update_development",
        kwargs={"pk": matter.pk, "development_id": development.pk},
    )


def _detail(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _row(client, matter, development) -> str:
    """The markup of this development's own chronology row, as the page renders it."""
    body = _detail(client, matter)
    start = body.index(f'id="menetluse-areng-{development.pk}-sisu"')
    return body[start : body.index("</article>", start)]


def _fields(development, **changes) -> dict[str, str]:
    """Everything the correction form posts, filled from the record.

    A real browser sends every box, so a test that sent three of them would be
    exercising a request the product never makes — and would prove nothing about
    the fields it left out.
    """
    payload: dict[str, object] = {
        "title": development.title,
        "note": development.note,
        "occurred_on": (
            format_estonian_date(development.occurred_on)
            if development.occurred_on and development.occurred_on_precision == DatePrecision.EXACT
            else ""
        ),
        "areng_precision": development.occurred_on_precision,
        "areng_month": "",
        "areng_quarter": "",
        "areng_year": "",
        "revision": development.revision_token,
    }
    if development.occurred_on and development.occurred_on_precision != DatePrecision.EXACT:
        payload["areng_year"] = str(development.occurred_on.year)
        if development.occurred_on_precision == DatePrecision.MONTH:
            payload["areng_month"] = str(development.occurred_on.month)
        if development.occurred_on_precision == DatePrecision.QUARTER:
            payload["areng_quarter"] = str((development.occurred_on.month - 1) // 3 + 1)
    payload.update(changes)
    return {key: ("" if value is None else str(value)) for key, value in payload.items()}


def _save(client, matter, development, **changes):
    """Save a correction the way the page does: with the token the row is at."""
    development.refresh_from_db()
    return client.post(_url(matter, development), _fields(development, **changes))


def _corrections(development=None):
    events = ChangeEvent.objects.filter(event_type=ChangeEventType.PROCEDURAL_DEVELOPMENT_CORRECTED)
    if development is not None:
        events = events.filter(object_id=str(development.pk))
    return events


def _revision_in(html: str) -> str:
    """The token the rendered form is holding.

    Read out of the markup rather than computed, because the whole point of the
    field is that the browser carries the server's value back unchanged.
    """
    marker = 'name="revision"'
    assert marker in html, "the correction form carries no revision token"
    field = html[html.index(marker) : html.index(marker) + 400]
    start = field.index('value="') + len('value="')
    return field[start : field.index('"', start)]


def _milestone(matter, user, development):
    """The chronology row this development draws, or ``None``."""
    items, _ = matter_timeline(matter=matter, user=user, limit=50)
    for item in items:
        if item.procedural_development is not None and item.record.pk == development.pk:
            return item.milestone
    return None


@pytest.fixture
def development(normal_matter, specialist):
    return record_procedural_development(
        matter=normal_matter,
        title=HEADLINE,
        occurred_on=HAPPENED,
        note=NOTE,
        actor=specialist,
    )


# -- §A. the affordance and the form ----------------------------------------


def test_the_chronology_offers_muuda_on_a_menetluse_areng(signed_in, normal_matter, development):
    """The defect QA-06 reported, stated as the behaviour that replaces it."""
    row = _row(signed_in, normal_matter, development)

    assert HEADLINE in row
    assert ">Muuda<" in row
    assert _url(normal_matter, development) in row
    # The swap target is the row's own text region and not the article: a
    # correction must not be able to move the row or add a line to the list.
    assert f'hx-target="#menetluse-areng-{development.pk}-sisu"' in row


def test_the_form_opens_on_what_the_record_says(signed_in, normal_matter, development):
    html = signed_in.get(_url(normal_matter, development)).content.decode()

    assert f'value="{HEADLINE}"' in html
    assert NOTE in html
    assert f'value="{format_estonian_date(HAPPENED)}"' in html
    assert _revision_in(html) == development.revision_token


def test_the_correction_form_asks_only_about_this_record(signed_in, normal_matter, development):
    """§4, §13. No `Hetkeseis`, no `Järgmiseks`, no attachments.

    An editor carrying those controls would let one press rewind a stage nobody
    asked to rewind, supersede an open next action, or capture evidence under an
    event that says «parandatud». They are separately correctable facts.
    """
    html = signed_in.get(_url(normal_matter, development)).content.decode()

    assert 'name="stage"' not in html
    assert 'name="next_text"' not in html
    assert 'name="next_date"' not in html
    assert 'type="file"' not in html


def test_cancelling_re_reads_the_record(signed_in, normal_matter, development):
    """`Tühista` leaves edit mode by fetching what the server actually holds."""
    html = signed_in.get(_url(normal_matter, development), {"vaade": "lugemine"}).content.decode()

    assert 'name="title"' not in html
    assert HEADLINE in html
    assert ">Muuda<" in html


# -- §B. the substantive fields ----------------------------------------------


def test_a_correct_title_only(signed_in, normal_matter, specialist, development):
    """§12.A."""
    response = _save(signed_in, normal_matter, development, title="Komisjon arutas eelnõu")

    assert response.status_code == 200
    development.refresh_from_db()
    assert development.title == "Komisjon arutas eelnõu"
    assert development.note == NOTE
    assert development.occurred_on == HAPPENED


def test_b_correct_lawyer_note_only(signed_in, normal_matter, development):
    """§12.B."""
    _save(
        signed_in, normal_matter, development, note="Uus versioon arvestab ettepanekut osaliselt."
    )

    development.refresh_from_db()
    assert development.note == "Uus versioon arvestab ettepanekut osaliselt."
    assert development.title == HEADLINE


def test_c_add_a_lawyer_note_to_a_blank_record(signed_in, normal_matter, specialist):
    """§12.C."""
    bare = record_procedural_development(
        matter=normal_matter, title=HEADLINE, occurred_on=HAPPENED, actor=specialist
    )
    assert bare.note == ""

    _save(signed_in, normal_matter, bare, note="Seda tuleb jälgida.")

    bare.refresh_from_db()
    assert bare.note == "Seda tuleb jälgida."


def test_d_clear_a_lawyer_note(signed_in, normal_matter, development):
    """§12.D. A step somebody no longer has an opinion about keeps no note."""
    _save(signed_in, normal_matter, development, note="")

    development.refresh_from_db()
    assert development.note == ""
    assert development.title == HEADLINE


def test_an_empty_title_is_refused(signed_in, normal_matter, development):
    """A development that does not say what happened is not a record of anything."""
    response = _save(signed_in, normal_matter, development, title="   ")

    assert response.status_code == 400
    assert DEVELOPMENT_NEEDS_TITLE in response.content.decode()
    development.refresh_from_db()
    assert development.title == HEADLINE


# -- §C. the period, at every precision --------------------------------------


def test_e_exact_to_another_exact(signed_in, normal_matter, development):
    """§12.E."""
    moved = _days_ago(20)
    _save(signed_in, normal_matter, development, occurred_on=format_estonian_date(moved))

    development.refresh_from_db()
    assert development.occurred_on == moved
    assert development.occurred_on_precision == DatePrecision.EXACT


def test_f_exact_to_unknown(signed_in, normal_matter, specialist, development):
    """§12.F. «Kuupäev teadmata» is a fact the file has to be able to hold."""
    _save(signed_in, normal_matter, development, occurred_on="")

    development.refresh_from_db()
    assert development.occurred_on is None
    # An unknown date has no precision, and the database says so too.
    assert development.occurred_on_precision == DatePrecision.EXACT
    assert _milestone(normal_matter, specialist, development).display_date == (
        DEVELOPMENT_DATE_UNKNOWN
    )


def test_g_unknown_to_exact(signed_in, normal_matter, specialist):
    """§12.G."""
    undated = record_procedural_development(matter=normal_matter, title=HEADLINE, actor=specialist)
    assert undated.occurred_on is None

    _save(signed_in, normal_matter, undated, occurred_on=format_estonian_date(HAPPENED))

    undated.refresh_from_db()
    assert undated.occurred_on == HAPPENED
    assert undated.occurred_on_precision == DatePrecision.EXACT


def test_an_undated_record_opens_on_an_empty_box_and_never_on_today(
    signed_in, normal_matter, specialist
):
    """§4. The one difference between this editor and the creating panel.

    `+ Menetluse areng` opens its day box on today, visibly and clearably, which
    docs/adr/0078 §2 allows for creation. An editor inheriting that default would
    reopen a stored «kuupäev teadmata» showing today — one `Salvesta` away from a
    day this application invented.
    """
    undated = record_procedural_development(matter=normal_matter, title=HEADLINE, actor=specialist)

    html = signed_in.get(_url(normal_matter, undated)).content.decode()

    assert format_estonian_date(timezone.localdate()) not in html


def test_h_exact_to_month(signed_in, normal_matter, specialist, development):
    """§12.H."""
    first = timezone.localdate().replace(day=1)
    _save(
        signed_in,
        normal_matter,
        development,
        occurred_on="",
        areng_precision=DatePrecision.MONTH.value,
        areng_year=str(first.year),
        areng_month=str(first.month),
    )

    development.refresh_from_db()
    assert development.occurred_on == first
    assert development.occurred_on_precision == DatePrecision.MONTH
    # The anchor is a place in a sort, never a day anybody named.
    assert "01." not in _milestone(normal_matter, specialist, development).display_date


def test_i_month_to_quarter(signed_in, normal_matter, specialist):
    """§12.I, where the quarter is one whose first day has already arrived."""
    today = timezone.localdate()
    first_of_month = today.replace(day=1)
    recorded = record_procedural_development(
        matter=normal_matter,
        title=HEADLINE,
        occurred_on=first_of_month,
        occurred_on_precision=DatePrecision.MONTH.value,
        actor=specialist,
    )
    quarter = (today.month - 1) // 3 + 1
    quarter_anchor = dt.date(today.year, (quarter - 1) * 3 + 1, 1)

    _save(
        signed_in,
        normal_matter,
        recorded,
        occurred_on="",
        areng_precision=DatePrecision.QUARTER.value,
        areng_year=str(today.year),
        areng_quarter=str(quarter),
        areng_month="",
    )

    recorded.refresh_from_db()
    assert recorded.occurred_on == quarter_anchor
    assert recorded.occurred_on_precision == DatePrecision.QUARTER


def test_an_approximate_record_reopens_on_its_period_and_not_on_its_anchor(
    signed_in, normal_matter, specialist
):
    """§4. Stored `2026-10-01` + `MONTH` opens as *oktoober*, never as `01.10.2026`."""
    first = timezone.localdate().replace(day=1)
    approximate = record_procedural_development(
        matter=normal_matter,
        title=HEADLINE,
        occurred_on=first,
        occurred_on_precision=DatePrecision.MONTH.value,
        actor=specialist,
    )

    html = signed_in.get(_url(normal_matter, approximate)).content.decode()

    assert format_estonian_date(first) not in html
    assert f'value="{first.month}" selected' in html or f'value="{first.month}"' in html


# -- §D. the future-date invariant (QA-07, preserved) ------------------------


@pytest.mark.parametrize("ahead", [7, 30, 400])
def test_j_a_correction_may_not_move_the_date_into_the_future(
    signed_in, normal_matter, development, ahead
):
    """§12.J. The invariant `record_procedural_development` states, unchanged."""
    future = timezone.localdate() + dt.timedelta(days=ahead)

    response = _save(
        signed_in, normal_matter, development, occurred_on=format_estonian_date(future)
    )

    assert response.status_code == 400
    assert DEVELOPMENT_CANNOT_BE_FUTURE in response.content.decode()
    development.refresh_from_db()
    assert development.occurred_on == HAPPENED


def test_j_a_wholly_future_period_is_refused_at_every_precision(
    signed_in, normal_matter, development
):
    """§8. A month, a quarter and a year that have not begun are all refused."""
    next_year = timezone.localdate().year + 1

    for extra in (
        {
            "areng_precision": DatePrecision.MONTH.value,
            "areng_year": str(next_year),
            "areng_month": "10",
        },
        {
            "areng_precision": DatePrecision.QUARTER.value,
            "areng_year": str(next_year),
            "areng_quarter": "4",
        },
        {"areng_precision": DatePrecision.YEAR.value, "areng_year": str(next_year)},
    ):
        response = _save(signed_in, normal_matter, development, occurred_on="", **extra)
        assert response.status_code == 400, extra
        assert DEVELOPMENT_CANNOT_BE_FUTURE in response.content.decode(), extra

    development.refresh_from_db()
    assert development.occurred_on == HAPPENED


def test_a_period_that_has_begun_is_allowed(signed_in, normal_matter, development):
    """§8. *september 2026* on 18 September is a step the lawyer is plainly describing
    as past, and refusing it would refuse the truth."""
    today = timezone.localdate()

    response = _save(
        signed_in,
        normal_matter,
        development,
        occurred_on="",
        areng_precision=DatePrecision.YEAR.value,
        areng_year=str(today.year),
    )

    assert response.status_code == 200
    development.refresh_from_db()
    assert development.occurred_on == dt.date(today.year, 1, 1)
    assert development.occurred_on_precision == DatePrecision.YEAR


def test_k_a_legacy_future_row_stays_correctable_in_its_headline(
    signed_in, normal_matter, development
):
    """§12.K, and the rule this form must not repeat the panel's check to keep.

    A row filed before the invariant existed still carries its future date. It is
    left exactly as it is and is not rewritten — so refusing every correction
    that merely *carries* that date would make its headline permanently
    uncorrectable, which is a second, quieter way of the file being unable to say
    what happened.
    """
    future = timezone.localdate() + dt.timedelta(days=40)
    MatterProceduralDevelopment.objects.filter(pk=development.pk).update(occurred_on=future)
    development.refresh_from_db()

    response = _save(signed_in, normal_matter, development, title="Komisjon arutas eelnõu")

    assert response.status_code == 200
    development.refresh_from_db()
    assert development.title == "Komisjon arutas eelnõu"
    assert development.occurred_on == future


# -- §E. optimistic concurrency ----------------------------------------------


def test_l_a_stale_revision_conflicts_and_writes_nothing(
    client, signed_in, normal_matter, specialist, other_specialist, development
):
    """§12.L. A opens the editor, B saves, A submits their stale version."""
    opened = signed_in.get(_url(normal_matter, development)).content.decode()
    stale = _revision_in(opened)

    other = client
    other.force_login(other_specialist)
    _save(other, normal_matter, development, title="Komisjon arutas eelnõu")
    development.refresh_from_db()
    assert development.title == "Komisjon arutas eelnõu"

    response = signed_in.post(
        _url(normal_matter, development),
        _fields(development, title="Minu vana versioon", revision=stale),
    )

    assert response.status_code == 409
    html = response.content.decode()
    assert DEVELOPMENT_EDIT_CONFLICT in html
    # B's change stands, and A's values are still in the boxes to reconcile.
    development.refresh_from_db()
    assert development.title == "Komisjon arutas eelnõu"
    assert "Praegune kirje:" in html
    assert "Komisjon arutas eelnõu" in html
    assert "Minu vana versioon" in html
    # The token is not advanced: adopting the newer one would be the view
    # deciding that the next submit may overwrite what the other writer saved.
    assert _revision_in(html) == stale


def test_a_conflict_shows_the_other_writers_lawyer_note_under_its_own_label(
    client, signed_in, normal_matter, other_specialist, development
):
    """§7, and QA-05 on the conflict side of the form."""
    opened = signed_in.get(_url(normal_matter, development)).content.decode()
    stale = _revision_in(opened)

    other = client
    other.force_login(other_specialist)
    _save(other, normal_matter, development, note="Tegelikult arvestab osaliselt.")

    response = signed_in.post(
        _url(normal_matter, development), _fields(development, note="Vana", revision=stale)
    )

    html = response.content.decode()
    assert response.status_code == 409
    assert "Tegelikult arvestab osaliselt." in html
    assert LAWYER_NOTE_LABEL in html


# -- §F. the closed Matter ---------------------------------------------------


def test_m_a_closed_matter_refuses_a_correction(signed_in, normal_matter, specialist, development):
    """§12.M. Reopening is the explicit route back to business work."""
    payload = _fields(development, title="Komisjon arutas eelnõu")
    close_matter(
        matter=normal_matter, disposition=Disposition.COMPLETED, actor=specialist, reason="QA"
    )

    response = signed_in.post(_url(normal_matter, development), payload)

    assert response.status_code == 400
    development.refresh_from_db()
    assert development.title == HEADLINE


def test_a_closed_matter_offers_no_muuda_control(signed_in, normal_matter, specialist, development):
    """The flag hides the button; the row lock is what actually refuses."""
    close_matter(
        matter=normal_matter, disposition=Disposition.COMPLETED, actor=specialist, reason="QA"
    )

    row = _row(signed_in, normal_matter, development)

    assert HEADLINE in row
    assert ">Muuda<" not in row


# -- §G. permissions ---------------------------------------------------------


def test_n_a_restricted_development_cannot_be_corrected_by_an_unauthorised_reader(
    client, normal_matter, reader, development
):
    """§12.N. Not the control, not the form, not a POST guessing the UUID.

    A restricted child inside a Matter somebody may see is indistinguishable here
    from one that does not exist — the same 404 to the GET and to the POST, so
    neither can be used to learn that the row is there (AUTH-003).
    """
    MatterProceduralDevelopment.objects.filter(pk=development.pk).update(
        visibility_override=Visibility.RESTRICTED
    )
    client.force_login(reader)

    body = _detail(client, normal_matter)
    assert HEADLINE not in body
    assert f"menetluse-areng-{development.pk}-muuda" not in body

    assert client.get(_url(normal_matter, development)).status_code in (403, 404)
    assert client.post(_url(normal_matter, development), _fields(development)).status_code in (
        403,
        404,
    )

    development.refresh_from_db()
    assert development.title == HEADLINE


def test_a_development_on_another_matter_is_not_reachable_through_this_one(
    signed_in, normal_matter, specialist, development
):
    """The Matter in the path is a proof of belonging, not decoration."""
    elsewhere = factories.MatterFactory(owner=specialist)

    url = reverse(
        "matters:update_development",
        kwargs={"pk": elsewhere.pk, "development_id": development.pk},
    )

    assert signed_in.get(url).status_code == 404


def test_a_reader_without_business_write_is_offered_no_correction(
    client, normal_matter, reader, development
):
    client.force_login(reader)

    body = _detail(client, normal_matter)
    assert f"menetluse-areng-{development.pk}-muuda" not in body

    assert client.post(
        _url(normal_matter, development), _fields(development, title="Ei tohi")
    ).status_code in (302, 403, 404)
    development.refresh_from_db()
    assert development.title == HEADLINE


def test_an_unknown_development_is_a_404(signed_in, normal_matter):
    url = reverse(
        "matters:update_development",
        kwargs={"pk": normal_matter.pk, "development_id": _uuid.uuid4()},
    )
    assert signed_in.get(url).status_code == 404


# -- §H. what a correction must leave alone ----------------------------------


def test_o_evidence_links_survive_a_correction(
    signed_in, normal_matter, specialist, evidence_root, pdf_bytes
):
    """§12.O, §3. Files are not re-posted here and cannot be detached by a correction."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from app.documents.models import DocumentLink

    result = add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title=HEADLINE,
        occurred_on=HAPPENED,
        note=NOTE,
        uploads=[SimpleUploadedFile("eelnou.pdf", pdf_bytes, content_type="application/pdf")],
    )
    made = result.record
    before = set(
        DocumentLink.objects.filter(procedural_development=made).values_list("document_id", "pk")
    )
    assert before

    _save(signed_in, normal_matter, made, title="Komisjon arutas eelnõu")

    after = set(
        DocumentLink.objects.filter(procedural_development=made).values_list("document_id", "pk")
    )
    assert after == before


def test_p_the_stage_the_original_operation_moved_is_not_rewound(
    signed_in, normal_matter, specialist
):
    """§12.P, §2, §13. A correction is not an undo of the operation that wrote the row.

    The original save moved `Hetkeseis` to `Riigikogus`. Correcting «Eelnõu
    jõudis Riigikokku» to «Komisjon arutas eelnõu» afterwards does not silently
    rewind the file — the stage is a separate canonical fact with its own
    correction surface.
    """
    riigikogus = factories.StageFactory(label_et="Riigikogus")
    result = add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="Eelnõu jõudis Riigikokku",
        occurred_on=HAPPENED,
        stage=riigikogus,
    )
    normal_matter.refresh_from_db()
    assert normal_matter.stage_id == riigikogus.pk

    _save(signed_in, normal_matter, result.record, title="Komisjon arutas eelnõu")

    normal_matter.refresh_from_db()
    assert normal_matter.stage_id == riigikogus.pk
    assert (
        ChangeEvent.objects.filter(
            matter=normal_matter, event_type=ChangeEventType.MATTER_STAGE_CHANGED
        ).count()
        == 1
    )


def test_q_the_next_action_the_original_operation_set_is_not_touched(
    signed_in, normal_matter, specialist
):
    """§12.Q. `Järgmiseks` is a separately correctable fact, not this row's property."""
    from app.workflow.enums import ActionStatus
    from app.workflow.models import NextAction

    result = add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title=HEADLINE,
        occurred_on=HAPPENED,
        next_text="Vaatan uue versiooni läbi",
        next_date=timezone.localdate() + dt.timedelta(days=5),
    )
    action = result.action
    assert action is not None

    _save(signed_in, normal_matter, result.record, title="Komisjon arutas eelnõu")

    action.refresh_from_db()
    open_actions = NextAction.objects.filter(matter=normal_matter, status=ActionStatus.OPEN)
    assert list(open_actions.values_list("pk", flat=True)) == [action.pk]
    assert action.text == "Vaatan uue versiooni läbi"


# -- §I. audit ---------------------------------------------------------------


def test_r_a_correction_writes_exactly_one_event_naming_the_fields_that_moved(
    signed_in, normal_matter, specialist, development
):
    """§12.R, §9. The existing event, and no full note in the payload.

    The audit row says *that* the lawyer's note changed and never what it now
    says: it is this office's judgement of what happened, and a payload holding
    it beside the event's own title is the one place the two could be read back
    as one statement (docs/adr/0091 §4, §5).
    """
    _save(
        signed_in,
        normal_matter,
        development,
        title="Komisjon arutas eelnõu",
        note="Uus hinnang.",
    )

    events = list(_corrections(development))
    assert len(events) == 1
    payload = events[0].payload
    assert payload["fields"] == ["note", "title"]
    assert "Uus hinnang." not in str(payload)
    assert NOTE not in str(payload)


def test_a_date_correction_records_both_sides_of_the_period(signed_in, normal_matter, development):
    """§9. The date and its precision together, never the anchor on its own."""
    moved = _days_ago(20)
    _save(signed_in, normal_matter, development, occurred_on=format_estonian_date(moved))

    payload = _corrections(development).get().payload
    assert payload["occurred_on_from"] == HAPPENED.isoformat()
    assert payload["occurred_on_to"] == moved.isoformat()


def test_a_save_that_changed_nothing_records_nothing(signed_in, normal_matter, development):
    """An audit row for a save that moved no value would be a history of button presses."""
    response = _save(signed_in, normal_matter, development)

    assert response.status_code == 200
    assert _corrections(development).count() == 0


def test_a_refused_correction_writes_no_audit_row(signed_in, normal_matter, development):
    future = timezone.localdate() + dt.timedelta(days=30)
    _save(signed_in, normal_matter, development, occurred_on=format_estonian_date(future))

    assert _corrections(development).count() == 0


# -- §J. no delete -----------------------------------------------------------


def test_s_there_is_no_delete_route(normal_matter, development):
    """§12.S, §5. A mistaken row is corrected; what the file recorded is part of the file."""
    for name in (
        "delete_development",
        "remove_development",
        "development_delete",
        "cancel_development",
    ):
        with pytest.raises(NoReverseMatch):
            reverse(
                f"matters:{name}",
                kwargs={"pk": normal_matter.pk, "development_id": development.pk},
            )


def test_the_correction_route_refuses_every_other_method(signed_in, normal_matter, development):
    for method in ("delete", "put", "patch"):
        response = getattr(signed_in, method)(_url(normal_matter, development))
        assert response.status_code == 405, method
    development.refresh_from_db()
    assert development.title == HEADLINE


# -- §K. the chronology after a correction (QA-05 preserved) -----------------


def test_the_corrected_row_comes_back_in_place_and_reads_as_the_page_will(
    signed_in, normal_matter, specialist, development
):
    """§6. One renderer for the row and for the answer, so the two cannot drift."""
    response = _save(
        signed_in, normal_matter, development, title="Komisjon arutas eelnõu", note="Uus hinnang."
    )
    answer = response.content.decode()

    assert "Komisjon arutas eelnõu" in answer
    assert ">Muuda<" in answer
    # The `<article>`, the dot and the spine are never in the answer: a
    # correction cannot move the row or add a second line to the chronology.
    assert "uxtl__item" not in answer
    assert 'class="uxtl__rail"' not in answer

    development.refresh_from_db()
    assert _milestone(normal_matter, specialist, development).what.endswith(
        "Komisjon arutas eelnõu"
    )


def test_qa05_the_lawyer_note_stays_separately_attributed_after_a_correction(
    signed_in, normal_matter, specialist, development
):
    """§6, and QA-05 held through the new surface.

    A paragraph of Koda's reading of a procedural step, printed unlabelled under
    a headline naming a ministry, is read as part of what the ministry did. The
    label survives the initial save, the correction's answer and the next page
    load alike.
    """
    before = _row(signed_in, normal_matter, development)
    assert LAWYER_NOTE_LABEL in before
    assert NOTE in before

    answer = _save(
        signed_in, normal_matter, development, note="Uus versioon arvestab osaliselt."
    ).content.decode()
    assert LAWYER_NOTE_LABEL in answer
    assert "Uus versioon arvestab osaliselt." in answer
    assert "uxtl__msnotelabel" in answer

    after = _row(signed_in, normal_matter, development)
    assert LAWYER_NOTE_LABEL in after
    assert "Uus versioon arvestab osaliselt." in after


def test_a_correction_does_not_add_a_row_to_the_chronology(
    signed_in, normal_matter, specialist, development
):
    items_before, _ = matter_timeline(matter=normal_matter, user=specialist, limit=50)

    _save(signed_in, normal_matter, development, title="Komisjon arutas eelnõu")

    items_after, _ = matter_timeline(matter=normal_matter, user=specialist, limit=50)
    assert len(items_after) == len(items_before)
