"""`Saaja` means the addressee, on every surface that names one (ENG-061).

`SubmissionRecipient` keeps two facts apart: who Koda formally wrote to
(`ADDRESSEE`) and who was copied in (`FOR_INFORMATION`, «teadmiseks»). Only the
first answers the question a reporting count asks, and Statistika has always
filtered on it. The `/arvamused/` register did not: its `?saaja=` filter and the
option list behind it matched **any** recipient row, so an organisation that was
only copied in was offered as a `Saaja`, filtering by it listed a letter whose
`Adressaat` column did not name it, and the same filter gave one count on the
register and another in Statistika. The `Adressaat` cell printed only addressee
rows but tested `forloop.last` over every row, so a letter with a copy read
`Ministry,`.

One collection now answers all four — the filter, the options, the cell and the
Statistika filter (`app/submissions/models.py`, `addressed_to`,
`addressee_organisations`, `addressee_prefetch`). These tests hold them to the
example the owner's brief gives: ADDRESSEE Ministry, TEADMISEKS Association.

**Nothing here decides which statuses count as sent** (ENG-062). The register's
own default status and Statistika's `status=SENT` are asserted as they were.
"""

from __future__ import annotations

import datetime
from html.parser import HTMLParser

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.submissions.enums import RecipientRole, SentAtPrecision
from app.submissions.models import SubmissionRecipient
from app.submissions.services import (
    attach_final_evidence,
    create_submission,
    mark_submission_sent,
)
from app.submissions.workspace import recipient_options
from tests import factories

pytestmark = pytest.mark.django_db

SENT_URL = reverse("submissions:sent")
STATISTIKA_URL = reverse("reporting:submissions")

#: A day in the past, whatever today is, and the Statistika period that holds
#: it whatever the default period is. Nothing below changes its answer on
#: another day (the Round-7 time rule).
SENT_ON = timezone.make_aware(datetime.datetime(2026, 5, 4))
ALL_YEARS = {"periood": "koik"}


class _Rows(HTMLParser):
    """Every body row of the first table, as its cells' collapsed text.

    Only `<tbody>`: a filter's own option list and the page's flash messages
    both name organisations, and a test that searched the whole page for one
    would pass on the select rather than on the row.
    """

    def __init__(self) -> None:
        super().__init__()
        self.header: list[str] = []
        self.rows: list[list[str]] = []
        self._where = ""
        self._cell: list[str] | None = None
        self._row: list[str] | None = None
        self._done = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if self._done:
            return
        if tag in ("thead", "tbody"):
            self._where = tag
        elif tag == "tr" and self._where == "tbody":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._done:
            return
        if tag in ("td", "th") and self._cell is not None:
            text = " ".join("".join(self._cell).split())
            if self._where == "thead":
                self.header.append(text)
            elif self._row is not None:
                self._row.append(text)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
        elif tag == "table":
            self._done = True


def _table(response) -> _Rows:
    assert response.status_code == 200
    parser = _Rows()
    parser.feed(response.content.decode())
    return parser


def _column(response, heading: str) -> list[str]:
    """One column of the page's table, by its own heading."""
    table = _table(response)
    index = table.header.index(heading)
    return [row[index] for row in table.rows]


def _send(matter, *, title, actor, addressees=(), copied=(), sent_at=SENT_ON):
    """A SENT opinion through the canonical services, with a past business date."""
    submission = create_submission(
        matter=matter,
        title=title,
        actor=actor,
        recipients=list(addressees),
        for_information=list(copied),
    )
    attach_final_evidence(
        submission=submission,
        content=f"%PDF-1.4 {title}".encode(),
        original_filename=f"{title}.pdf",
        mime_type="application/pdf",
        actor=actor,
    )
    return mark_submission_sent(
        submission=submission,
        actor=actor,
        sent_at=sent_at,
        sent_at_precision=SentAtPrecision.DATE,
    )


@pytest.fixture
def ministry(db):
    return factories.OrganisationFactory(name="Ministry")


@pytest.fixture
def association(db):
    return factories.OrganisationFactory(name="Association")


@pytest.fixture
def copied_letter(specialist, ministry, association):
    """The brief's example: written to Ministry, Association copied in."""
    matter = factories.MatterFactory(owner=specialist)
    return _send(
        matter,
        title="Kiri ministeeriumile",
        actor=specialist,
        addressees=[ministry],
        copied=[association],
    )


# ---------------------------------------------------------------------------
# The register: filter, options and cell
# ---------------------------------------------------------------------------


def test_the_register_filter_includes_the_addressee(signed_in, copied_letter, ministry):
    titles = _column(signed_in.get(SENT_URL, {"saaja": str(ministry.pk)}), "Arvamus")

    assert len(titles) == 1
    assert titles[0].startswith("Kiri ministeeriumile")


def test_the_register_filter_excludes_an_organisation_only_copied_in(
    signed_in, copied_letter, association
):
    """Copying somebody in is not writing to them."""
    response = signed_in.get(SENT_URL, {"saaja": str(association.pk)})

    assert response.context["total"] == 0
    assert _table(response).rows == []


def test_an_organisation_only_copied_in_is_not_offered_as_a_saaja(
    signed_in, copied_letter, ministry, association
):
    offered = list(signed_in.get(SENT_URL).context["recipients"])

    assert ministry in offered
    assert association not in offered


def test_the_adressaat_cell_reads_exactly_the_addressee(signed_in, copied_letter):
    """`Ministry`, not `Ministry,` — the copy row used to leave the comma."""
    assert _column(signed_in.get(SENT_URL), "Adressaat") == ["Ministry"]


def test_two_addressees_and_a_copy_read_as_one_list_with_one_comma(
    signed_in, specialist, ministry, association
):
    committee = factories.OrganisationFactory(name="Committee")
    _send(
        factories.MatterFactory(owner=specialist),
        title="Kiri kahele",
        actor=specialist,
        addressees=[ministry, committee],
        copied=[association],
    )

    assert _column(signed_in.get(SENT_URL), "Adressaat") == ["Committee, Ministry"]


def test_a_send_with_only_a_copy_reads_as_having_no_addressee(signed_in, specialist, association):
    """An empty cell and a dash are different claims; the column states the second."""
    _send(
        factories.MatterFactory(owner=specialist),
        title="Ainult teadmiseks",
        actor=specialist,
        copied=[association],
    )

    assert _column(signed_in.get(SENT_URL), "Adressaat") == ["—"]


def test_the_options_never_name_an_addressee_of_a_submission_the_reader_cannot_see(
    client, reader, specialist, ministry
):
    """An option is a statement that a visible letter went there (AUTHORIZATION)."""
    hidden_body = factories.OrganisationFactory(name="Varjatud amet")
    _send(
        factories.MatterFactory(owner=specialist),
        title="Nähtav kiri",
        actor=specialist,
        addressees=[ministry],
    )
    _send(
        factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED),
        title="Piiratud kiri",
        actor=specialist,
        addressees=[hidden_body],
    )

    offered = list(recipient_options(reader))
    assert ministry in offered
    assert hidden_body not in offered

    client.force_login(reader)
    body = client.get(SENT_URL).content.decode()
    assert "Varjatud amet" not in body
    # And the filter itself does not become an oracle for it.
    response = client.get(SENT_URL, {"saaja": str(hidden_body.pk)})
    assert response.context["total"] == 0


def test_a_page_of_the_register_does_not_cost_a_query_per_row(
    signed_in, specialist, ministry, association
):
    """The addressee cell reads one prefetch for the page, however long it is."""

    def cost() -> int:
        with CaptureQueriesContext(connection) as captured:
            response = signed_in.get(SENT_URL)
        assert response.status_code == 200
        return len(captured)

    matter = factories.MatterFactory(owner=specialist)
    for index in range(2):
        _send(
            matter,
            title=f"Esimene {index}",
            actor=specialist,
            addressees=[ministry],
            copied=[association],
        )
    short = cost()
    for index in range(8):
        _send(
            matter,
            title=f"Teine {index}",
            actor=specialist,
            addressees=[factories.OrganisationFactory()],
            copied=[association],
        )

    assert cost() == short


# ---------------------------------------------------------------------------
# Statistika agrees
# ---------------------------------------------------------------------------


def test_statistika_filters_the_same_way(signed_in, copied_letter, ministry, association):
    to_ministry = signed_in.get(STATISTIKA_URL, {**ALL_YEARS, "saaja": str(ministry.pk)})
    to_association = signed_in.get(STATISTIKA_URL, {**ALL_YEARS, "saaja": str(association.pk)})

    assert to_ministry.context["total"] == 1
    assert to_association.context["total"] == 0
    # And its own addressee cell reads the same as the register's.
    assert _column(to_ministry, "Adressaadid") == ["Ministry"]


def test_the_register_and_statistika_agree_for_every_offered_saaja(
    signed_in, specialist, ministry, association, copied_letter
):
    """One definition, so there is no organisation on which the two can differ."""
    _send(
        factories.MatterFactory(owner=specialist),
        title="Kiri liidule",
        actor=specialist,
        addressees=[association],
        copied=[ministry],
    )
    _send(
        factories.MatterFactory(owner=specialist),
        title="Kiri mõlemale",
        actor=specialist,
        addressees=[ministry],
    )

    offered = list(signed_in.get(SENT_URL).context["recipients"])
    assert {organisation.name for organisation in offered} == {"Ministry", "Association"}
    for organisation in offered:
        register = signed_in.get(SENT_URL, {"saaja": str(organisation.pk)}).context["total"]
        statistika = signed_in.get(
            STATISTIKA_URL, {**ALL_YEARS, "saaja": str(organisation.pk)}
        ).context["total"]
        assert register == statistika, organisation.name
    assert signed_in.get(SENT_URL, {"saaja": str(ministry.pk)}).context["total"] == 2
    assert signed_in.get(SENT_URL, {"saaja": str(association.pk)}).context["total"] == 1


def test_the_shared_collection_is_the_addressee_role_and_nothing_else(copied_letter):
    """The definition itself, so a future caller cannot quietly widen it."""
    rows = SubmissionRecipient.objects.addressees().filter(submission=copied_letter)

    assert {row.role for row in rows} == {RecipientRole.ADDRESSEE}
    assert [row.organisation.name for row in rows] == ["Ministry"]
