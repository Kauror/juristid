"""The plan is primary; the obligation is stated beside it.

PR #205 separated two questions that had shared one predicate — *what should a
lawyer work on today* and *has Koda actually answered* — and deliberately
changed nothing a reader sees. This is the reading half: the second answer
reaches the two surfaces where the first one is already on screen.

    Plaanis 15.10.2026                 ← the plan. Unchanged, and still primary.
    Arvamuse tähtaeg 20.09 · 1 p üle   ← the obligation, stated second.

What this file is organised around
----------------------------------

**The primary reading may not move.** Not the register's Kuupäev cell, not its
sort key, not `work_items`, not `outstanding_response_deadlines`. So a good half
of what is asserted here is again what did *not* change, on the same Matters the
new line is asked to describe.

**A surface never states one date twice.** Where the Kuupäev cell has already
fallen back to `Arvamuse tähtaeg` — no step, or a step with no date of its own —
the secondary line is silent. The rule compares the two dates rather than
guessing from the shape of the row, and it lives in
`work_items.secondary_response_obligation` rather than in either template.

**The reader's scope decides, and a restricted child may not speak.** A `SENT`
Submission below a NORMAL Matter discharges the obligation for somebody who may
open it and for nobody else. The direction matters: a reader who cannot see it
is told the obligation is *outstanding*, which discloses nothing, while the
opposite would announce that restricted work happened on a named file
(AUTH-003, docs/adr/0038).

**A list may not pay per row.** The register annotates once
(`selectors.matter_list_queryset`) and the filter refuses to render without it,
so twenty rows cost what one row costs.

Every date is relative to today, for the reason `test_response_obligation_state`
gives: a test written around a production date passes for a fortnight and then
fails for reasons nobody can reproduce.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.core.dates import format_estonian_date, short_day_month
from app.core.enums import Visibility
from app.legacy_import.current_state import CurrentRegisterState, RegisterCurrency
from app.legacy_import.models import MatterSourceReference
from app.legacy_import.register_semantics import OpinionSentState
from app.matters import register_dates as rd
from app.matters import selectors
from app.matters import work_items as wi
from app.matters.models import Matter
from app.matters.services import close_matter, create_matter
from app.submissions.enums import SubmissionStatus
from app.submissions.services import (
    attach_final_evidence,
    create_submission,
    mark_submission_sent,
)
from app.workflow.enums import ActionKind, DateSemantics, Disposition
from app.workflow.services import set_next_action
from tests import factories
from tests import synthetic_corpus as corpus

pytestmark = pytest.mark.django_db

REGISTER = reverse("matters:matter_list")
PDF = b"%PDF-1.4 arvamus"

#: The label both surfaces print in front of the second date. The register's own
#: word for the column, read from the one place that owns it.
LABEL = rd.RESPONSE_DEADLINE_LABEL


# ---------------------------------------------------------------------------
# Building the world
# ---------------------------------------------------------------------------


@pytest.fixture
def today() -> date:
    return timezone.localdate()


def _matter(owner, *, deadline, title="Kohustusega teema", **kwargs):
    return create_matter(
        title=title,
        owner=owner,
        reference_year=2026,
        response_deadline=deadline,
        **kwargs,
    )


def _instruct(matter, actor, *, on=None, text="Jalgin menetlust"):
    return set_next_action(
        matter=matter,
        text=text,
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=on,
        actor=actor,
    )


def _mark(
    matter,
    *,
    state: str,
    sent_on: date | None = None,
    currency: str = RegisterCurrency.CURRENT,
) -> CurrentRegisterState:
    """One derived register row saying what ``VÄLJA`` holds for this Matter.

    The construction `test_response_obligation_state` uses, and for its reason:
    what is under test is how a read model treats a derived row, not how the row
    is derived.
    """
    digest = hashlib.sha256(b"response-obligation-display-snapshot").hexdigest()
    reference = MatterSourceReference.objects.create(
        matter=matter,
        source_system="EXCEL_REGISTER",
        source_file_name="Tood eelnoudega.xlsx",
        source_snapshot_sha256=digest,
        source_sheet="2026",
        source_row_number=matter.reference_number,
        source_row_raw={"VÄLJA": "" if state == OpinionSentState.BLANK else "margitud"},
        source_title=matter.title,
        source_era="2026",
    )
    return CurrentRegisterState.objects.create(
        matter=matter,
        source_reference=reference,
        source_snapshot_sha256=digest,
        source_sheet="2026",
        source_row_number=reference.source_row_number,
        currency=currency,
        status_label="Kooskolastusringil",
        opinion_sent_recorded=state != OpinionSentState.BLANK,
        opinion_sent_state=state,
        opinion_sent_date=sent_on,
        continues_under_reference=("2026_999" if currency == RegisterCurrency.SUPERSEDED else ""),
        observed_at=timezone.now(),
    )


def _send_opinion(matter, actor):
    submission = create_submission(matter=matter, title="Arvamus", actor=actor)
    attach_final_evidence(
        submission=submission,
        content=PDF,
        original_filename="arvamus.pdf",
        mime_type="application/pdf",
        actor=actor,
    )
    submission.refresh_from_db()
    return mark_submission_sent(submission=submission, actor=actor)


def _send_restricted_opinion(matter, capture_evidence, extract):
    """A SENT opinion restricted below an otherwise ordinary Matter."""
    version = capture_evidence(
        matter,
        corpus.text_pdf(["Salajane arvamus"]),
        "salajane.pdf",
        "application/pdf",
        title="Salajane arvamus",
        visibility_override=Visibility.RESTRICTED,
    )
    extract(version)
    return factories.SubmissionFactory(
        matter=matter,
        title="Salajane arvamus",
        status=SubmissionStatus.SENT,
        sent_at=timezone.now(),
        final_version=version,
        visibility_override=Visibility.RESTRICTED,
    )


# ---------------------------------------------------------------------------
# Reading the two surfaces the way a lawyer does
# ---------------------------------------------------------------------------


def _body(response) -> str:
    assert response.status_code == 200
    return response.content.decode()


#: The Kuupäev cell's **primary** line — the first `.dateline` in the cell, which
#: is the reading `register_date` and the ORDER BY share. The secondary line
#: carries `.dateline` too (it is masked by the same visual-suite rule), so the
#: pattern is non-greedy on purpose: what it must never do is let a second line
#: answer a question asked about the first.
_PRIMARY_CELL = re.compile(
    r'<td class="table__date">.*?(?:<span class="dateline[^"]*">\s*([^<\s][^<]*?)\s*</span>|'
    r'<span class="muted">(—)</span>)',
    re.DOTALL,
)

#: The Kuupäev cell's **secondary** line, whole: label and value together.
_SECONDARY_CELL = re.compile(r'<span class="dateowed">(.*?)</span>\s*</td>', re.DOTALL)


def _register_table(response) -> str:
    body = _body(response)
    return body[body.index('<table class="table table--register">') :]


def primary_dates(response) -> list[str]:
    """The Kuupäev column's first line, top to bottom."""
    return [found or dash for found, dash in _PRIMARY_CELL.findall(_register_table(response))]


def secondary_lines(response) -> list[str]:
    """Every rendered `Arvamuse tähtaeg 20.09 · 1 p üle`, as one string each."""
    return [
        " ".join(re.sub(r"<[^>]+>", " ", block).split())
        for block in _SECONDARY_CELL.findall(_register_table(response))
    ]


def titles_on(response) -> list[str]:
    return [row.title for row in response.context["page"].object_list]


def teema_owed(response) -> str:
    """`PRAEGUNE TEGEVUS`'s obligation line, or an empty string when absent."""
    body = _body(response)
    found = re.search(r'<p class="curact__owed">(.*?)</p>', body, re.DOTALL)
    return " ".join(re.sub(r"<[^>]+>", " ", found.group(1)).split()) if found else ""


def teema_of(client, matter) -> Any:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))


# ---------------------------------------------------------------------------
# 1. The main scenario
# ---------------------------------------------------------------------------
#
#   deadline yesterday · nothing sent · no qualifying VÄLJA · open step at +24 d
#
# The exact shape the two questions answer differently, and the reason this
# reading exists.


@pytest.fixture
def instructed(specialist, today):
    matter = _matter(specialist, deadline=today - timedelta(days=1))
    _instruct(matter, specialist, on=today + timedelta(days=24))
    return matter


def test_teema_states_the_plan_first_and_the_obligation_second(signed_in, instructed, today):
    """Both facts on the page, in that order, and each said once."""
    response = teema_of(signed_in, instructed)
    body = _body(response)

    plan = body.index("Jalgin menetlust")
    owed = body.index('<p class="curact__owed">')
    assert plan < owed, "the obligation must read as a note under the task, not above it"

    assert teema_owed(response) == f"{LABEL} {short_day_month(today - timedelta(days=1))} · 1 p üle"


def test_the_register_keeps_its_primary_date_and_adds_the_second(
    signed_in, instructed, specialist, today
):
    """The cell, the annotation the ORDER BY reads, and the new line — together.

    Asserted in one test because the whole risk of this change is that the
    secondary line arrives and the primary quietly moves with it.
    """
    response = signed_in.get(REGISTER)

    assert primary_dates(response) == [format_estonian_date(today + timedelta(days=24))]
    assert secondary_lines(response) == [
        f"{LABEL} {short_day_month(today - timedelta(days=1))} · 1 p üle"
    ]

    sort_key = (
        rd.annotate_display_date(Matter.objects.visible_to(specialist), specialist)
        .values_list(rd.DISPLAY_DATE, flat=True)
        .get(pk=instructed.pk)
    )
    assert sort_key == today + timedelta(days=24)


def test_the_operational_readings_are_untouched(instructed, specialist, today):
    """No work population learns anything from this round."""
    items = [
        item
        for item in wi.work_items(specialist, today=today, responsible=specialist)
        if item.matter_id == instructed.pk
    ]

    (item,) = items
    assert item.source_type == wi.SOURCE_NEXT_ACTION
    assert item.when == today + timedelta(days=24)
    assert instructed.pk not in set(
        wi.outstanding_response_deadlines(specialist).values_list("pk", flat=True)
    )


def test_the_stored_deadline_is_never_written(instructed, signed_in, today):
    """Rendering the second fact may not express itself by moving the column."""
    teema_of(signed_in, instructed)
    signed_in.get(REGISTER)

    instructed.refresh_from_db()
    assert instructed.response_deadline == today - timedelta(days=1)


# ---------------------------------------------------------------------------
# 2. Discharge — the line follows the canonical state and nothing else
# ---------------------------------------------------------------------------


def _case(specialist, today, build):
    """Thirty days late, with an instruction six weeks out, plus one fact."""
    matter = _matter(specialist, deadline=today - timedelta(days=30))
    _instruct(matter, specialist, on=today + timedelta(days=40))
    build(matter, specialist, today)
    return matter


DISCHARGING = [
    pytest.param(lambda m, u, t: _send_opinion(m, u), id="visible-sent"),
    pytest.param(
        lambda m, u, t: _mark(m, state=OpinionSentState.DATE, sent_on=t - timedelta(days=28)),
        id="current-date",
    ),
    pytest.param(lambda m, u, t: _mark(m, state=OpinionSentState.NOT_SENT), id="current-not-sent"),
]

LEAVING_IT_OPEN = [
    pytest.param(lambda m, u, t: _mark(m, state=OpinionSentState.RECORDED_OTHER), id="other"),
    pytest.param(
        lambda m, u, t: _mark(
            m,
            state=OpinionSentState.DATE,
            sent_on=t - timedelta(days=28),
            currency=RegisterCurrency.RETIRED,
        ),
        id="retired-date",
    ),
    pytest.param(
        lambda m, u, t: _mark(
            m,
            state=OpinionSentState.DATE,
            sent_on=t - timedelta(days=28),
            currency=RegisterCurrency.SUPERSEDED,
        ),
        id="superseded-date",
    ),
]


@pytest.mark.parametrize("build", DISCHARGING)
def test_a_discharged_obligation_says_nothing(signed_in, specialist, today, build):
    """Koda answered. There is no second fact left to state."""
    matter = _case(specialist, today, build)

    assert teema_owed(teema_of(signed_in, matter)) == ""
    assert secondary_lines(signed_in.get(REGISTER)) == []


@pytest.mark.parametrize("build", LEAVING_IT_OPEN)
def test_what_does_not_discharge_it_still_shows(signed_in, specialist, today, build):
    """An unread cell, and a row that speaks for a different file (ADR 0059)."""
    _case(specialist, today, build)
    expected = f"{LABEL} {short_day_month(today - timedelta(days=30))} · 30 p üle"
    matter = Matter.objects.get(response_deadline=today - timedelta(days=30))

    assert teema_owed(teema_of(signed_in, matter)) == expected
    assert secondary_lines(signed_in.get(REGISTER)) == [expected]


# ---------------------------------------------------------------------------
# 3. One date, said once
# ---------------------------------------------------------------------------


def test_without_a_step_the_deadline_is_the_primary_date_and_is_not_repeated(
    signed_in, specialist, today
):
    """The duplicate this rule exists to prevent.

    With no `Järgmiseks` the Kuupäev cell *is* `Arvamuse tähtaeg`, and the Teema
    header's own `Tähtaeg` slot states the same day in full. A secondary line
    here would be the first fact stuttering rather than a second one.
    """
    matter = _matter(specialist, deadline=today - timedelta(days=5))
    response = signed_in.get(REGISTER)

    assert primary_dates(response) == [format_estonian_date(today - timedelta(days=5))]
    assert secondary_lines(response) == []

    assert teema_owed(teema_of(signed_in, matter)) == ""
    assert matter.pk in set(wi.response_obligations(specialist).values_list("pk", flat=True))


def test_an_undated_step_leaves_the_deadline_primary_on_the_register(signed_in, specialist, today):
    """A step with no date of its own falls through to the deadline in both
    readings — `Coalesce` in the ORDER BY and the fallback in `register_date` —
    so the register is already showing this date and does not show it twice."""
    matter = _matter(specialist, deadline=today - timedelta(days=5))
    _instruct(matter, specialist, on=None)

    response = signed_in.get(REGISTER)
    assert primary_dates(response) == [format_estonian_date(today - timedelta(days=5))]
    assert secondary_lines(response) == []


# ---------------------------------------------------------------------------
# 4. A deadline still ahead of us
# ---------------------------------------------------------------------------


def test_a_future_deadline_is_stated_without_a_lateness(signed_in, specialist, today):
    """The date, and nothing about days. Nothing is owed *late* yet."""
    matter = _matter(specialist, deadline=today + timedelta(days=6))
    _instruct(matter, specialist, on=today + timedelta(days=20))

    expected = f"{LABEL} {short_day_month(today + timedelta(days=6))}"
    assert teema_owed(teema_of(signed_in, matter)) == expected

    line = secondary_lines(signed_in.get(REGISTER))
    assert line == [expected]
    assert "üle" not in line[0]


def test_a_deadline_that_is_today_is_stated_without_a_lateness(signed_in, specialist, today):
    """The boundary. `is_past` is `deadline < today`, so today is not late."""
    matter = _matter(specialist, deadline=today)
    _instruct(matter, specialist, on=today + timedelta(days=20))

    assert teema_owed(teema_of(signed_in, matter)) == f"{LABEL} {short_day_month(today)}"


# ---------------------------------------------------------------------------
# 5. Authorization — a restricted child may not speak for the Matter
# ---------------------------------------------------------------------------


def test_a_restricted_opinion_does_not_discharge_it_for_a_reader_who_cannot_see_it(
    client, specialist, reader, today, capture_evidence, extract
):
    """AUTH-003 applied to the new line.

    The reader is told the obligation is outstanding, which is what they would
    have been told before the restricted opinion existed. Silence here would be
    the disclosure: a line that disappeared when a colleague sent something
    announces that a colleague sent something.
    """
    matter = _matter(specialist, deadline=today - timedelta(days=10))
    _instruct(matter, specialist, on=today + timedelta(days=30))
    _send_restricted_opinion(matter, capture_evidence, extract)

    client.force_login(reader)
    expected = f"{LABEL} {short_day_month(today - timedelta(days=10))} · 10 p üle"

    assert teema_owed(teema_of(client, matter)) == expected
    assert secondary_lines(client.get(REGISTER)) == [expected]


def test_a_participant_who_may_see_the_opinion_sees_it_discharged(
    client, specialist, today, capture_evidence, extract
):
    """The same Matter, the same moment, read by somebody with the access."""
    matter = _matter(specialist, deadline=today - timedelta(days=10))
    _instruct(matter, specialist, on=today + timedelta(days=30))
    _send_restricted_opinion(matter, capture_evidence, extract)

    client.force_login(specialist)

    assert teema_owed(teema_of(client, matter)) == ""
    assert secondary_lines(client.get(REGISTER)) == []


def test_the_restricted_opinion_changes_nothing_else_on_the_readers_page(
    client, specialist, reader, today, capture_evidence, extract
):
    """No hidden-child leak: the rows, their order and their primary dates are
    the ones the reader saw before the restricted Submission existed."""
    matter = _matter(specialist, deadline=today - timedelta(days=10))
    _instruct(matter, specialist, on=today + timedelta(days=30))
    _matter(specialist, deadline=today - timedelta(days=2), title="Teine teema")

    client.force_login(reader)
    before = client.get(REGISTER)
    was = (titles_on(before), primary_dates(before), secondary_lines(before))

    _send_restricted_opinion(matter, capture_evidence, extract)

    after = client.get(REGISTER)
    assert (titles_on(after), primary_dates(after), secondary_lines(after)) == was


def test_a_restricted_step_does_not_reveal_itself_through_the_second_line(
    client, specialist, reader, today
):
    """The other child that can be restricted.

    A `Järgmiseks` a reader may not open is invisible to `register_date`, so for
    that reader the Kuupäev cell falls back to `Arvamuse tähtaeg` — and the
    secondary line goes quiet with it. A line that appeared only for readers who
    cannot see the step would announce that the step exists.
    """
    matter = _matter(specialist, deadline=today - timedelta(days=10))
    action = _instruct(matter, specialist, on=today + timedelta(days=30))
    action.visibility_override = Visibility.RESTRICTED
    action.save(update_fields=["visibility_override"])

    client.force_login(reader)
    response = client.get(REGISTER)

    assert primary_dates(response) == [format_estonian_date(today - timedelta(days=10))]
    assert secondary_lines(response) == []


# ---------------------------------------------------------------------------
# 6. A closed file is not a queue
# ---------------------------------------------------------------------------


def test_a_closed_matter_states_no_outstanding_obligation(signed_in, specialist, today):
    """`Praegune tegevus` on a closed Teema says the file is closed, and that is
    the whole of it. An overdue warning there is a work queue in everything but
    name, and the header's `Tähtaeg` already states the date it carries."""
    matter = _matter(specialist, deadline=today - timedelta(days=10))
    _instruct(matter, specialist, on=today + timedelta(days=30))
    close_matter(
        matter=matter,
        actor=specialist,
        disposition=Disposition.MONITORING_STOPPED,
        reason="Menetlus loppes",
    )

    assert teema_owed(teema_of(signed_in, matter)) == ""
    assert secondary_lines(signed_in.get(REGISTER, {"olek": "koik"})) == []
    assert matter.pk not in set(wi.response_obligations(specialist).values_list("pk", flat=True))


# ---------------------------------------------------------------------------
# 7. The population this reading describes
# ---------------------------------------------------------------------------


def test_the_line_describes_exactly_the_canonical_obligation_population(
    specialist, today, capture_evidence, extract
):
    """The identity, over the whole matrix rather than case by case.

    Every Matter the secondary reading speaks about is in
    `response_obligations`, and every Matter it stays silent about is either
    outside that population or one whose primary date is already the deadline.
    Stated as set algebra so a future clause added to one side and not the other
    fails here rather than on a page.
    """
    built: list[Matter] = []
    for index, build in enumerate(
        [
            lambda m, u, t: None,
            lambda m, u, t: _instruct(m, u, on=t + timedelta(days=40)),
            lambda m, u, t: _instruct(m, u, on=None),
            lambda m, u, t: _send_opinion(m, u),
            lambda m, u, t: _mark(m, state=OpinionSentState.NOT_SENT),
            lambda m, u, t: _mark(m, state=OpinionSentState.RECORDED_OTHER),
        ]
    ):
        matter = _matter(specialist, deadline=today - timedelta(days=index + 1), title=f"T{index}")
        build(matter, specialist, today)
        built.append(matter)

    rows = {
        row.pk: row
        for row in selectors.matter_list_queryset(specialist).filter(
            pk__in=[matter.pk for matter in built]
        )
    }
    readings = {pk: rd.register_date(row) for pk, row in rows.items()}
    speaks_about = {
        pk
        for pk, row in rows.items()
        if wi.secondary_response_obligation(
            row,
            specialist,
            primary_date=readings[pk].value if readings[pk] is not None else None,
        )
        is not None
    }
    obliged = set(wi.response_obligations(specialist).values_list("pk", flat=True))
    primary_is_the_deadline = {
        pk
        for pk, row in rows.items()
        if readings[pk] is not None and readings[pk].value == row.response_deadline
    }

    assert speaks_about == obliged - primary_is_the_deadline
    assert speaks_about <= obliged


# ---------------------------------------------------------------------------
# 8. A list may not pay per row
# ---------------------------------------------------------------------------


def test_the_second_line_costs_no_query_per_row(signed_in, specialist, today):
    """One row against twenty-one, and the register's cost may not grow.

    Asserted as *constant* rather than as a number: the register is a shared page
    and a fixed budget here would be somebody else's unrelated red build. What a
    row may never cost is a query of its own.
    """

    def cost(expected_rows: int) -> int:
        with CaptureQueriesContext(connection) as captured:
            response = signed_in.get(REGISTER, {"kaupa": "30"})
            assert len(titles_on(response)) == expected_rows
            assert len(secondary_lines(response)) == expected_rows
        return len(captured.captured_queries)

    _instruct(
        _matter(specialist, deadline=today - timedelta(days=3), title="Rida 00"),
        specialist,
        on=today + timedelta(days=30),
    )
    one = cost(1)

    for index in range(1, 21):
        _instruct(
            _matter(specialist, deadline=today - timedelta(days=3), title=f"Rida {index:02d}"),
            specialist,
            on=today + timedelta(days=30),
        )
    many = cost(21)

    assert many == one


def test_the_row_filter_refuses_to_answer_without_the_annotation(specialist, today):
    """The seam, protected the way `register_date`'s prefetch is.

    An unannotated row would still render — through one `Exists` pair per Matter,
    which on a full register is a page nobody would notice getting slower.
    """
    from app.matters.templatetags.matter_activity import secondary_obligation

    matter = _matter(specialist, deadline=today - timedelta(days=3))
    _instruct(matter, specialist, on=today + timedelta(days=30))
    unannotated = (
        Matter.objects.visible_to(specialist)
        .prefetch_related(selectors.open_action_prefetch(specialist))
        .get(pk=matter.pk)
    )

    with pytest.raises(ValueError, match="annotate_response_obligation"):
        secondary_obligation(unannotated, specialist)


def test_no_migration_is_owed() -> None:
    """This round adds a reading. It adds no column."""
    from io import StringIO

    from django.core.management import call_command

    call_command("makemigrations", "--check", "--dry-run", stdout=StringIO())
