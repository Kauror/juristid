"""«Arvamus koostamisel» has one definition, and native work is part of it (ENG-019).

Osakond's ARVAMUS KOOSTAMISEL column, the list behind each of its cells, and
the register's ``?arvamus=`` filter used to read only the imported register's
VÄLJA column. A Matter created here never has a register row, so a native
opinion being written in DRAFT counted nowhere — while /arvamused/ printed it —
and on the pilot database, which holds no register rows at all, the column was
structurally nought. The reverse held as well: a register-backed Matter whose
opinion was then sent here stayed «koostamisel» next to the very send that
finished it.

The definition is now one function, ``register_filters.opinion_state_q``:

* **koostamisel** — a DRAFT Submission this reader may see, **or** a CURRENT
  register row with a blank VÄLJA whose Matter has no SENT Submission this
  reader may see;
* **saadetud** — a SENT Submission this reader may see, **or** a CURRENT
  register row with something in VÄLJA.

The cell counts it, the cell's link opens it, and the register filter is it —
so every test below asserts on all three where it can, and compares row
identities rather than two integers that could agree by accident.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlparse

import pytest
from django.utils import timezone

from app.core.enums import Visibility
from app.legacy_import.current_state import CurrentRegisterState, RegisterCurrency
from app.legacy_import.register_semantics import OpinionSentState
from app.matters import dashboard
from app.matters.department_dashboard import TEAM_COLUMNS, team_rows
from app.matters.models import Matter
from app.matters.register_filters import register_population
from app.matters.services import close_matter
from app.submissions.enums import SubmissionStatus
from app.workflow.enums import Disposition
from tests import factories

pytestmark = pytest.mark.django_db

DRAFTING = {"olek": "avatud", "arvamus": "koostamisel"}
SENT = {"olek": "avatud", "arvamus": "saadetud"}


# ---------------------------------------------------------------------------
# Building the shapes
# ---------------------------------------------------------------------------


@pytest.fixture
def martin(db) -> Any:
    return factories.UserFactory(display_name="Martin Koostaja")


def register_row(matter: Matter, *, sent_recorded: bool) -> None:
    """A CURRENT register row, with VÄLJA blank or marked.

    Both derivations of the cell are written, because a check constraint makes
    the database refuse a row that says «recorded» and «blank» at once.
    """
    reference = factories.MatterSourceReferenceFactory(matter=matter)
    CurrentRegisterState.objects.create(
        matter=matter,
        source_reference=reference,
        source_snapshot_sha256="1" * 64,
        source_sheet="2026",
        source_row_number=reference.source_row_number,
        currency=RegisterCurrency.CURRENT,
        opinion_sent_recorded=sent_recorded,
        opinion_sent_state=(
            OpinionSentState.RECORDED_OTHER if sent_recorded else OpinionSentState.BLANK
        ),
        observed_at=timezone.now(),
    )


def draft_on(matter: Matter, **kwargs: Any) -> Any:
    return factories.SubmissionFactory(matter=matter, status=SubmissionStatus.DRAFT, **kwargs)


@pytest.fixture
def send_on(capture_evidence):
    """A SENT Submission with the immutable final evidence the database insists on."""

    def send(matter: Matter, *, visibility_override: str = "") -> Any:
        version = capture_evidence(
            matter,
            b"%PDF-1.4 synthetic sent opinion",
            "saadetud-arvamus.pdf",
            "application/pdf",
            visibility_override=visibility_override,
        )
        return factories.SubmissionFactory(
            matter=matter,
            status=SubmissionStatus.SENT,
            sent_at=timezone.now(),
            final_version=version,
            visibility_override=visibility_override,
        )

    return send


def ids(queryset) -> set:
    return set(queryset.values_list("pk", flat=True))


def listed(user: Any, params: dict[str, str]) -> set:
    """Exactly the rows ``/teemad/?<params>`` pages through for this reader."""
    return ids(register_population(user, params))


def drafting_cell(user: Any, person_key: str):
    index = next(i for i, (key, _l, _g, _s) in enumerate(TEAM_COLUMNS) if key == "drafting")
    row = next(row for row in team_rows(user) if row.key == person_key)
    return row.cells[index]


def cell_rows(user: Any, cell) -> set:
    """The rows the cell's own link opens, as the register would read them."""
    return listed(user, dict(parse_qsl(urlparse(cell.url).query)))


# ---------------------------------------------------------------------------
# The cases the brief names
# ---------------------------------------------------------------------------


def test_a_native_draft_is_being_drafted(department_head, martin) -> None:
    """The reported defect: a real draft on a native Matter counted nowhere."""
    matter = factories.MatterFactory(owner=martin, title="Omaloodud teema")
    draft_on(matter)

    assert ids(dashboard.drafting_matters(department_head)) == {matter.pk}
    assert listed(department_head, DRAFTING) == {matter.pk}
    cell = drafting_cell(department_head, str(martin.pk))
    assert cell.value == 1
    assert cell_rows(department_head, cell) == {matter.pk}


def test_a_native_matter_whose_only_opinion_went_out_is_not_being_drafted(
    department_head, martin, send_on
) -> None:
    matter = factories.MatterFactory(owner=martin)
    send_on(matter)

    assert listed(department_head, DRAFTING) == set()
    assert listed(department_head, SENT) == {matter.pk}
    assert drafting_cell(department_head, str(martin.pk)).value == 0


def test_a_blank_valja_on_a_current_register_row_is_still_being_drafted(
    department_head, martin
) -> None:
    """The register half survives unchanged (ADR 0021)."""
    drafting = factories.MatterFactory(owner=martin)
    register_row(drafting, sent_recorded=False)
    marked = factories.MatterFactory(owner=martin)
    register_row(marked, sent_recorded=True)

    assert listed(department_head, DRAFTING) == {drafting.pk}
    assert listed(department_head, SENT) == {marked.pk}
    assert drafting_cell(department_head, str(martin.pk)).value == 1


def test_a_register_row_and_native_drafts_are_one_matter(department_head, martin) -> None:
    """Both halves true, two drafts on top: still one file, counted once."""
    matter = factories.MatterFactory(owner=martin)
    register_row(matter, sent_recorded=False)
    draft_on(matter)
    draft_on(matter)

    assert dashboard.drafting_matters(department_head).count() == 1
    assert listed(department_head, DRAFTING) == {matter.pk}
    cell = drafting_cell(department_head, str(martin.pk))
    assert cell.value == 1
    assert cell_rows(department_head, cell) == {matter.pk}


def test_a_native_send_retires_a_stale_blank_valja(department_head, martin, send_on) -> None:
    """The register said «not sent yet» when it was read; the Submission says it went.

    Where a canonical SENT Submission exists it is the outbound record and VÄLJA
    is source metadata beside it (ADR 0021). The audit's reproduction exactly:
    the row counted the send in ARVAMUSI VÄLJA and kept the file «koostamisel».
    """
    matter = factories.MatterFactory(owner=martin)
    register_row(matter, sent_recorded=False)
    assert listed(department_head, DRAFTING) == {matter.pk}

    send_on(matter)

    assert listed(department_head, DRAFTING) == set()
    assert listed(department_head, SENT) == {matter.pk}
    assert drafting_cell(department_head, str(martin.pk)).value == 0


def test_a_second_opinion_in_preparation_after_a_send_is_being_drafted(
    department_head, martin, send_on
) -> None:
    """The send retires the stale register cell, never a real draft beside it."""
    matter = factories.MatterFactory(owner=martin)
    register_row(matter, sent_recorded=False)
    send_on(matter)
    draft_on(matter, title="Täiendav arvamus")

    assert listed(department_head, DRAFTING) == {matter.pk}
    assert listed(department_head, SENT) == {matter.pk}


def test_without_any_register_row_native_work_is_still_counted(
    department_head, martin, send_on
) -> None:
    """The pilot database's shape: nought register rows, real native work."""
    drafting = factories.MatterFactory(owner=martin)
    draft_on(drafting)
    sent = factories.MatterFactory(owner=martin)
    send_on(sent)
    assert not CurrentRegisterState.objects.exists()

    assert listed(department_head, DRAFTING) == {drafting.pk}
    assert listed(department_head, SENT) == {sent.pk}
    assert drafting_cell(department_head, str(martin.pk)).value == 1


def test_a_draft_on_a_closed_matter_is_not_current_work(department_head, martin) -> None:
    matter = factories.MatterFactory(owner=martin)
    draft_on(matter)
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=department_head)

    assert listed(department_head, DRAFTING) == set()
    assert drafting_cell(department_head, str(martin.pk)).value == 0


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------


def test_a_draft_the_reader_may_not_see_does_not_count_for_them(
    department_head, reader, martin
) -> None:
    """A restricted draft on an ordinary Matter: the head counts it, a reader does not.

    The Matter itself is readable to both. What differs is the child, which
    carries its own stricter visibility — and counting it for somebody who may
    not open it would disclose that a draft exists.
    """
    matter = factories.MatterFactory(owner=martin)
    draft_on(matter, visibility_override=Visibility.RESTRICTED)

    assert listed(department_head, DRAFTING) == {matter.pk}
    assert listed(reader, DRAFTING) == set()
    assert matter.pk in ids(Matter.objects.visible_to(reader))


def test_a_restricted_matter_is_absent_for_a_reader(department_head, reader, martin) -> None:
    matter = factories.MatterFactory(owner=martin, visibility=Visibility.RESTRICTED)
    draft_on(matter)

    assert listed(department_head, DRAFTING) == {matter.pk}
    assert listed(reader, DRAFTING) == set()
    assert dashboard.drafting_matters(reader).count() == 0


def test_a_send_the_reader_may_not_see_does_not_retire_the_register_cell_for_them(
    department_head, reader, martin, send_on
) -> None:
    """No oracle. A hidden send must not make a readable file change state.

    For the head the restricted send is the outbound record, so the blank VÄLJA
    is stale. For a reader who may not see that send, the file reads exactly as
    it would if the send did not exist — which is the same rule
    `work_items._discharge_exists` applies to the response obligation.
    """
    matter = factories.MatterFactory(owner=martin)
    register_row(matter, sent_recorded=False)
    send_on(matter, visibility_override=Visibility.RESTRICTED)

    assert listed(department_head, DRAFTING) == set()
    assert listed(department_head, SENT) == {matter.pk}
    assert listed(reader, DRAFTING) == {matter.pk}
    assert listed(reader, SENT) == set()


# ---------------------------------------------------------------------------
# The count is the list
# ---------------------------------------------------------------------------


def test_every_drafting_cell_opens_exactly_the_matters_it_counted(
    client, department_head, martin, send_on
) -> None:
    """Every shape at once, every row of the table, and the real view.

    Compared as row identities, not as two integers, and once more through the
    register view itself so the parameters are read the way a browser sends
    them.
    """
    sandra = factories.UserFactory(display_name="Sandra Teine")
    native = factories.MatterFactory(owner=martin, title="Omaloodud mustand")
    draft_on(native)
    legacy = factories.MatterFactory(owner=martin, title="Registri tühi VÄLJA")
    register_row(legacy, sent_recorded=False)
    both = factories.MatterFactory(owner=sandra, title="Mõlemad pooled")
    register_row(both, sent_recorded=False)
    draft_on(both)
    stale = factories.MatterFactory(owner=sandra, title="Saadetud siin")
    register_row(stale, sent_recorded=False)
    send_on(stale)
    nobody = factories.MatterFactory(owner=None, title="Vastutajata mustand")
    draft_on(nobody)

    expected = {
        str(martin.pk): {native.pk, legacy.pk},
        str(sandra.pk): {both.pk},
        "vastutajata": {nobody.pk},
    }
    client.force_login(department_head)
    for key, matters in expected.items():
        cell = drafting_cell(department_head, key)
        assert cell.value == len(matters), key
        assert cell_rows(department_head, cell) == matters, key
        response = client.get(cell.url)
        assert response.status_code == 200
        assert response.context["page"].paginator.count == cell.value, key
        assert {matter.pk for matter in response.context["page"].object_list} == matters, key

    total = drafting_cell(department_head, "kokku")
    assert total.value == dashboard.drafting_matters(department_head).count() == 4


def test_the_register_control_offers_the_values_the_definition_answers(
    client, department_head, martin
) -> None:
    """The panel can reproduce what a cell opens, native work included."""
    matter = factories.MatterFactory(owner=martin, title="Paneelist leitav mustand")
    draft_on(matter)

    client.force_login(department_head)
    response = client.get("/teemad/", {"olek": "avatud", "arvamus": "koostamisel"})

    assert response.status_code == 200
    assert {row.pk for row in response.context["page"].object_list} == {matter.pk}
    assert 'value="koostamisel" selected' in response.content.decode()
