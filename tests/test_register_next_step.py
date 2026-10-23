"""The register's `Järgmiseks` cell tells the same truth as the next-step rule (RULE-01).

`app.matters.next_step` (docs/adr/0120) answers what happens next on a Matter:
a visible open `NextAction`; otherwise the nearest visible upcoming `Oluline
tähtaeg`; otherwise the register's own Excel instruction; otherwise nothing. The
Teema page, `Minu asjad` and every «järgmine tegevus puudub» population —
`?tegevus=puudub` among them — read that rule. The register row did not: it
went from the action straight to the Excel sentence and then to «Järgmine samm
puudub», so a Matter waiting on a milestone was kept out of `?tegevus=puudub`
while its own row said it had no next step.

The milestone is surfaced, never converted: it keeps its own wording («Oluline
tähtaeg») and its own record, and nothing here creates or completes a
`NextAction`. A milestone the reader may not see decides nothing.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.intelligence.enums import FactStatus
from app.intelligence.models import MatterImportantDate
from app.legacy_import.current_state import CurrentRegisterState, RegisterCurrency
from app.matters.next_step import upcoming_milestone
from app.workflow.dates import year_bounds
from app.workflow.enums import DatePrecision
from app.workflow.models import NextAction
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

REGISTER = reverse("matters:matter_list")
MISSING = "Järgmine samm puudub"
LABEL = "Oluline tähtaeg"
SNAPSHOT = "c" * 64


def _today() -> dt.date:
    return timezone.localdate()


def _milestone(matter, title: str, *, days: int = 20, **fields) -> MatterImportantDate:
    day = _today() + dt.timedelta(days=days)
    fields.setdefault("date_value", day)
    fields.setdefault("period_end", fields["date_value"])
    return factories.ImportantDateFactory(matter=matter, title=title, **fields)


def _source_instruction(matter, text: str) -> None:
    """An imported Excel `JÄRGMISEKS`, the way the cutover writes it."""
    reference = factories.MatterSourceReferenceFactory(
        matter=matter, source_sheet="2026", source_row_number=1, source_snapshot_sha256=SNAPSHOT
    )
    CurrentRegisterState.objects.create(
        matter=matter,
        source_reference=reference,
        source_snapshot_sha256=SNAPSHOT,
        source_sheet="2026",
        source_row_number=1,
        currency=RegisterCurrency.CURRENT,
        next_action_text=text,
        observed_at=timezone.now(),
    )


def _cell(client, matter, **params) -> str:
    """The `Järgmiseks` cell of this Matter's register row."""
    body = client.get(REGISTER, {"olek": "koik", **params}).content.decode()
    rows = [chunk for chunk in body.split("<tr") if f"/teemad/{matter.pk}/" in chunk]
    assert rows, "the Matter has no row on the register"
    row = rows[0]
    start = row.index('class="table__action"')
    return row[start : row.index("</td>", start)]


def _listed_without_next_step(client, matter) -> bool:
    body = client.get(REGISTER, {"olek": "koik", "tegevus": "puudub"}).content.decode()
    return f"/teemad/{matter.pk}/" in body


# -- A. the contradiction itself ----------------------------------------------


def test_a_matter_waiting_on_a_milestone_is_not_called_stepless(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist, title="Tähtajaga teema")
    _milestone(matter, "Ministeeriumi vastus")

    cell = _cell(signed_in, matter)

    assert LABEL in cell
    assert "Ministeeriumi vastus" in cell
    assert MISSING not in cell
    # The row and the filter now tell the same truth.
    assert not _listed_without_next_step(signed_in, matter)
    # Surfaced, never converted.
    assert not NextAction.objects.filter(matter=matter).exists()


def test_a_closed_matter_names_no_milestone_as_its_next_step(signed_in, specialist):
    """E-03: the row reads the way the Teema page does — «Puudub — teema on suletud»."""
    from app.matters.services import close_matter
    from app.workflow.enums import Disposition

    matter = factories.MatterFactory(owner=specialist, title="Suletud tähtajaga teema")
    _milestone(matter, "Riigikogu teine lugemine")
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=specialist, reason="QA")

    cell = _cell(signed_in, matter)

    assert "Riigikogu teine lugemine" not in cell
    assert LABEL not in cell
    assert "Puudub — teema on suletud" in cell
    assert MISSING not in cell
    # And `?tegevus=puudub` agrees with the row it lists.
    assert _listed_without_next_step(signed_in, matter)


# -- B. an explicit step wins ---------------------------------------------------


def test_an_open_next_action_outranks_the_milestone(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _milestone(matter, "Ministeeriumi vastus")
    set_next_action(matter=matter, text="Helistan ministeeriumi", actor=specialist)

    cell = _cell(signed_in, matter)

    assert "Helistan ministeeriumi" in cell
    assert "Ministeeriumi vastus" not in cell
    assert LABEL not in cell


# -- C. the milestone the canonical rule picks ---------------------------------


def test_the_register_names_the_milestone_the_rule_chooses(signed_in, specialist):
    """A year anchored on 1 January is due after a day in February of that year.

    The expectation is asked of `next_step.upcoming_milestone`, not restated:
    the register must agree with the rule, whatever the rule is.
    """
    matter = factories.MatterFactory(owner=specialist)
    start, end = year_bounds(_today().year + 1)
    _milestone(
        matter,
        "Aasta jooksul",
        date_value=start,
        period_end=end,
        date_precision=DatePrecision.YEAR,
    )
    _milestone(matter, "Veebruaris", date_value=start.replace(month=2, day=1))
    _milestone(matter, "Hiljem täpselt", date_value=start.replace(month=3, day=1))

    chosen = upcoming_milestone(MatterImportantDate.objects.filter(matter=matter))
    assert chosen is not None and chosen.title == "Veebruaris"

    cell = _cell(signed_in, matter)
    assert chosen.title in cell
    assert "Aasta jooksul" not in cell
    assert "Hiljem täpselt" not in cell


# -- D. what is not a next step ------------------------------------------------


@pytest.mark.parametrize("shape", ["past", "cancelled", "removed"])
def test_a_milestone_that_is_not_ahead_is_not_the_next_step(signed_in, specialist, shape):
    matter = factories.MatterFactory(owner=specialist)
    if shape == "past":
        _milestone(matter, "Möödunud tähtaeg", days=-3)
    elif shape == "cancelled":
        _milestone(matter, "Tühistatud tähtaeg", status=FactStatus.CANCELLED)
    else:
        record = _milestone(matter, "Eemaldatud tähtaeg")
        MatterImportantDate.objects.filter(pk=record.pk).update(removed_at=timezone.now())

    cell = _cell(signed_in, matter)

    assert MISSING in cell
    assert LABEL not in cell
    assert _listed_without_next_step(signed_in, matter)


# -- E. authorization ---------------------------------------------------------


def test_a_restricted_milestone_decides_alike_for_a_reader_and_nothing_for_an_outsider(
    client, reader, administrator, specialist
):
    """It used to be hidden from a READER, who then saw `MISSING`; since
    docs/adr/0181 the READER's row is the specialist's, and an administrator
    without a grant sees neither the Matter nor the milestone."""
    matter = factories.MatterFactory(owner=specialist, title="Nähtav teema")
    _milestone(
        matter,
        "Salajane tähtaeg",
        days=11,
        visibility_override=Visibility.RESTRICTED,
    )

    client.force_login(specialist)
    as_owner = _cell(client, matter)
    assert "Salajane tähtaeg" in as_owner
    assert LABEL in as_owner

    client.force_login(reader)
    cell = _cell(client, matter)
    assert cell == as_owner
    assert MISSING not in cell
    assert not _listed_without_next_step(client, matter)

    client.force_login(administrator)
    body = client.get(REGISTER, {"olek": "koik"}).content.decode()
    assert "Nähtav teema" not in body
    assert "Salajane tähtaeg" not in body
    assert f"/teemad/{matter.pk}/" not in body


# -- F. the Excel instruction comes after the milestone -------------------------


def test_the_milestone_outranks_the_excel_instruction(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _source_instruction(matter, "Uurida ministeeriumilt")
    _milestone(matter, "Ministeeriumi vastus")

    cell = _cell(signed_in, matter)
    assert "Ministeeriumi vastus" in cell
    assert "Uurida ministeeriumilt" not in cell
    # The imported sentence is untouched; it is simply not what happens next.
    assert CurrentRegisterState.objects.get(matter=matter).next_action_text == (
        "Uurida ministeeriumilt"
    )


def test_without_a_milestone_the_excel_instruction_still_reads(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _source_instruction(matter, "Uurida ministeeriumilt")

    cell = _cell(signed_in, matter)
    assert "Excelist" in cell
    assert "Uurida ministeeriumilt" in cell
    assert LABEL not in cell


# -- G. one read for the page, not one per row -----------------------------------


def _milestone_reads(client) -> int:
    with CaptureQueriesContext(connection) as captured:
        assert client.get(REGISTER, {"olek": "koik"}).status_code == 200
    return sum('FROM "intelligence_matterimportantdate"' in q["sql"] for q in captured)


def test_the_page_reads_milestones_once_whatever_its_length(signed_in, specialist):
    for number in range(2):
        _milestone(factories.MatterFactory(owner=specialist), f"Tähtaeg {number}")
    few = _milestone_reads(signed_in)

    for number in range(2, 8):
        _milestone(factories.MatterFactory(owner=specialist), f"Tähtaeg {number}")
    many = _milestone_reads(signed_in)

    assert many == few
    assert many <= 1
