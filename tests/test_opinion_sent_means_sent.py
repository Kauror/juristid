"""`?arvamus=saadetud` means the opinion was recorded as sent (RULE-04).

`register_filters.opinion_state_q` read the register half of «saadetud» as
``opinion_sent_recorded`` — *something is written in VÄLJA* — so a file whose
register says **ei saatnud** was listed under «Arvamus saadetud», as was one
whose cell nobody can read. The register already says which of four things the
cell means (``CurrentRegisterState.opinion_sent_state``); only ``DATE`` says the
opinion went out.

Three questions, kept apart:

* **was an opinion sent** — this filter: a SENT Submission this reader may see,
  or a CURRENT register row reading ``DATE``;
* **is the opinion work finished** — ``OPINION_WORK_COMPLETE_STATES`` (ADR 0059):
  ``DATE`` and ``NOT_SENT`` both finish it, so «ei saatnud» still discharges the
  deadline while not being «saadetud»;
* **is an opinion being drafted** — ``koostamisel``, unchanged: a ``NOT_SENT``
  file is a decision taken, not a draft.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.legacy_import.register_semantics import OpinionSentState
from tests.test_arvamus_koostamisel_one_definition import (
    DRAFTING,
    SENT,
    draft_on,
    listed,
    send_on,  # noqa: F401 — the fixture `send` below asks for by name
)
from tests.test_valja_completion_semantics import _is_outstanding, _mark, _matter

pytestmark = pytest.mark.django_db


@pytest.fixture
def today() -> date:
    return timezone.localdate()


@pytest.fixture
def send(request):
    """`send_on` from the ENG-019 suite: a SENT Submission with its final evidence."""
    return request.getfixturevalue("send_on")


@pytest.fixture
def file(specialist, today):
    """An open Teema whose opinion deadline has passed — so «finished» is visible too."""
    return _matter(specialist, deadline=today - timedelta(days=30), title="Pakendiseadus")


# ---------------------------------------------------------------------------
# A–D — the four register readings, with no Submission at all
# ---------------------------------------------------------------------------


def test_a_register_date_is_sent(file, department_head, reader, today):
    _mark(file, state=OpinionSentState.DATE, sent_on=today - timedelta(days=5))

    assert listed(department_head, SENT) == {file.pk}
    assert listed(reader, SENT) == {file.pk}


def test_b_ei_saatnud_is_not_sent_and_still_finishes_the_work(file, department_head, specialist):
    """The defect. «ei saatnud» is a decision: the work is over, nothing went out."""
    row = _mark(file, state=OpinionSentState.NOT_SENT)
    assert row.opinion_sent_recorded is True  # something is written in VÄLJA

    assert listed(department_head, SENT) == set()
    # Separate questions, unchanged: the deadline is discharged (ADR 0059) and
    # the file is not an opinion being drafted.
    assert _is_outstanding(file, specialist) is False
    assert listed(department_head, DRAFTING) == set()


def test_c_an_unreadable_valja_mark_is_not_sent(file, department_head, specialist):
    row = _mark(file, state=OpinionSentState.RECORDED_OTHER)
    assert row.opinion_sent_recorded is True

    assert listed(department_head, SENT) == set()
    # Nor is it an accepted completion — unchanged (ADR 0059 §2).
    assert _is_outstanding(file, specialist) is True


def test_d_a_blank_valja_is_not_sent(file, department_head):
    row = _mark(file, state=OpinionSentState.BLANK)
    assert row.opinion_sent_recorded is False

    assert listed(department_head, SENT) == set()
    assert listed(department_head, DRAFTING) == {file.pk}


# ---------------------------------------------------------------------------
# E, F — the canonical record is enough on its own
# ---------------------------------------------------------------------------


def test_e_a_sent_submission_outranks_a_register_ei_saatnud(file, department_head, send):
    _mark(file, state=OpinionSentState.NOT_SENT)
    send(file)

    assert listed(department_head, SENT) == {file.pk}


@pytest.mark.parametrize("register", ["none", OpinionSentState.BLANK])
def test_f_a_sent_submission_is_sent_with_or_without_a_register_row(
    file, department_head, send, register
):
    if register != "none":
        _mark(file, state=register)
    send(file)

    assert listed(department_head, SENT) == {file.pk}


# ---------------------------------------------------------------------------
# G, H — a send the reader may not see
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state",
    [OpinionSentState.BLANK, OpinionSentState.NOT_SENT, OpinionSentState.RECORDED_OTHER],
)
def test_g_a_hidden_send_is_sent_only_for_who_may_see_it(
    file, department_head, reader, send, state
):
    _mark(file, state=state)
    send(file, visibility_override=Visibility.RESTRICTED)

    assert listed(department_head, SENT) == {file.pk}
    assert listed(reader, SENT) == set()


def test_h_a_register_date_is_sent_whatever_the_reader_may_see(
    file, department_head, reader, send, today
):
    """The register's own date is readable on its own, so nothing is disclosed."""
    _mark(file, state=OpinionSentState.DATE, sent_on=today - timedelta(days=5))
    send(file, visibility_override=Visibility.RESTRICTED)

    assert listed(department_head, SENT) == {file.pk}
    assert listed(reader, SENT) == {file.pk}


# ---------------------------------------------------------------------------
# I, J — «saadetud» and «koostamisel» are not each other's complement
# ---------------------------------------------------------------------------


def test_i_sent_once_and_drafting_again_is_both(file, department_head, send):
    send(file)
    draft_on(file)

    assert listed(department_head, SENT) == {file.pk}
    assert listed(department_head, DRAFTING) == {file.pk}


def test_j_ei_saatnud_leaving_saadetud_does_not_make_it_a_draft(file, department_head):
    _mark(file, state=OpinionSentState.NOT_SENT)

    assert listed(department_head, SENT) == set()
    assert listed(department_head, DRAFTING) == set()


# ---------------------------------------------------------------------------
# The page is the filter
# ---------------------------------------------------------------------------


def test_the_register_page_lists_exactly_the_sent_population(
    client, department_head, specialist, today
):
    """`/teemad/?arvamus=saadetud` — the only surface that asks this question."""
    shapes = {
        OpinionSentState.DATE: "Kuupäevaga saadetud",
        OpinionSentState.NOT_SENT: "Otsustati mitte saata",
        OpinionSentState.RECORDED_OTHER: "Loetamatu märge",
        OpinionSentState.BLANK: "Märkimata",
    }
    for state, title in shapes.items():
        _mark(
            _matter(specialist, deadline=today, title=title),
            state=state,
            sent_on=today if state == OpinionSentState.DATE else None,
        )
    client.force_login(department_head)

    body = client.get(f"{reverse('matters:matter_list')}?olek=avatud&arvamus=saadetud")

    page = body.content.decode()
    assert shapes[OpinionSentState.DATE] in page
    for state in (
        OpinionSentState.NOT_SENT,
        OpinionSentState.RECORDED_OTHER,
        OpinionSentState.BLANK,
    ):
        assert shapes[state] not in page, state
