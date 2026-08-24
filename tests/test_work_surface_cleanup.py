"""What the work surfaces stopped showing, and what they must not have lost.

One removal and one substitution, each with a way of going wrong that no
screenshot would catch:

*The reference column is gone.* Deleting a `<th>` and leaving its `<td>` behind
shifts every cell in the row one column left, and the page still looks like a
table. So the check is structural — every row has exactly as many cells as the
header has columns — not a search for a missing word. What the reference is
actually for (identity, exact search, the Matter's own page) is asserted to
still work, because a cleanup that quietly removed a capability would be a much
worse trade than the column was.

*Colleagues appear by short name.* Only where a real account is resolved. The
matching rule for a name the register recorded — never shortened, because the
register names people who have no account here at all — belongs with the
cutover fixture that has real source rows, in `test_cutover_dashboard.py`.
"""

from __future__ import annotations

from html.parser import HTMLParser

import pytest
from django.urls import reverse

from app.matters.services import create_matter
from app.search.services import search_matters
from tests import factories

pytestmark = pytest.mark.django_db

#: Every ordinary work surface that renders a table of Matters.
#:
#: Minu töö and Ülevaade are deliberately absent. Neither renders a table any
#: more: the Minu töö / Ülevaade rebuild replaced both with rule-separated rows,
#: and Minu töö dropped its register table outright — it is where a lawyer sees
#: what to do next, not a place to browse. A route with no table cannot be
#: asserted to have a well-formed one, and adding a table back to satisfy this
#: file would be the test writing the design.
WORK_SURFACES = ("matters:matter_list", "matters:inbox")


class _Tables(HTMLParser):
    """The shape of every table on a page: its headers, and its rows' widths.

    Deliberately structural. Asserting that the word "Viide" is absent proves
    nothing about whether the cells beneath it went with it.
    """

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[dict] = []
        self._in_head = False
        self._header: list[str] = []
        self._widths: list[int] = []
        self._row: int | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "table":
            self._header, self._widths, self._in_head = [], [], False
        elif tag == "thead":
            self._in_head = True
        elif tag == "tr":
            self._row = 0
        elif tag in ("th", "td"):
            self._text = []
            if self._row is not None and not self._in_head:
                self._row += 1

    def handle_data(self, data: str) -> None:
        self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "th" and self._in_head:
            self._header.append("".join(self._text).strip())
        elif tag == "thead":
            self._in_head = False
        elif tag == "tr":
            if self._row:
                self._widths.append(self._row)
            self._row = None
        elif tag == "table":
            self.tables.append({"header": self._header, "widths": self._widths})


def _tables(body: str) -> list[dict]:
    parser = _Tables()
    parser.feed(body)
    return parser.tables


@pytest.fixture
def populated(specialist):
    """One Matter on every ordinary surface, owned and unowned."""
    ministry = factories.OrganisationFactory(name="Näidisministeerium")
    owned = create_matter(
        title="Pakendiseaduse muutmise eelnõu",
        owner=specialist,
        reference_year=2026,
        source_organisations=[ministry],
    )
    unowned = create_matter(
        title="Vastutajata saabunud materjal",
        reference_year=2026,
        source_organisations=[ministry],
    )
    return {"owned": owned, "unowned": unowned}


# -- the reference column ---------------------------------------------------


@pytest.mark.parametrize("route", WORK_SURFACES)
def test_no_work_surface_spends_a_column_on_the_reference(populated, client, specialist, route):
    client.force_login(specialist)
    body = client.get(reverse(route)).content.decode()

    for table in _tables(body):
        assert "Viide" not in table["header"], f"{route}: {table['header']}"


@pytest.mark.parametrize("route", WORK_SURFACES)
def test_every_row_still_has_exactly_as_many_cells_as_its_header(
    populated, client, specialist, route
):
    """The failure a missing-word assertion cannot see: a shifted row."""
    client.force_login(specialist)
    body = client.get(reverse(route)).content.decode()

    tables = [table for table in _tables(body) if table["header"] and table["widths"]]
    assert tables, f"{route} rendered no populated table"
    for table in tables:
        assert set(table["widths"]) == {len(table["header"])}, f"{route}: {table}"


def test_the_department_surface_dropped_it_too(populated, client, department_head):
    client.force_login(department_head)
    body = client.get(reverse("matters:department_work")).content.decode()

    for table in _tables(body):
        assert "Viide" not in table["header"]


def test_the_rows_still_carry_the_data_the_columns_promise(populated, client, specialist):
    """Removing a column must not have taken a neighbour's content with it."""
    client.force_login(specialist)
    body = client.get(reverse("matters:matter_list")).content.decode()

    assert "Pakendiseaduse muutmise eelnõu" in body
    assert "Vastutajata saabunud materjal" in body


# -- what the reference is still for ----------------------------------------


def test_the_matter_still_has_its_reference(populated):
    """A display concern only. The identity is untouched."""
    assert populated["owned"].display_reference


def test_an_exact_reference_still_finds_its_matter(populated, specialist):
    reference = populated["owned"].display_reference
    results = search_matters(user=specialist, query=reference)

    assert [result.matter.id for result in results] == [populated["owned"].id]
    assert results[0].match_kind == "reference"


def test_the_register_still_answers_a_reference_typed_into_the_search_box(
    populated, client, specialist
):
    """Through the page, not only through the selector."""
    client.force_login(specialist)
    reference = populated["owned"].display_reference
    response = client.get(reverse("matters:matter_list"), {"q": reference})

    assert response.status_code == 200
    assert "Pakendiseaduse muutmise eelnõu" in response.content.decode()


def test_the_matter_page_still_shows_the_reference(populated, client, specialist):
    """Where a reference is genuinely wanted: on the record it identifies."""
    client.force_login(specialist)
    body = client.get(
        reverse("matters:matter_detail", kwargs={"pk": populated["owned"].pk})
    ).content.decode()

    assert populated["owned"].display_reference in body


# -- short names ------------------------------------------------------------


@pytest.fixture
def marko(db):
    return factories.UserFactory(upn="marko@example.invalid", display_name="Marko Example")


def test_a_current_colleague_is_named_by_their_short_name(client, marko):
    create_matter(title="Marko teema", owner=marko, reference_year=2026)
    client.force_login(marko)
    body = client.get(reverse("matters:matter_list")).content.decode()

    assert marko.get_short_name() == "Marko"
    assert ">Marko<" in body


def test_the_owner_control_offers_short_names(client, marko):
    """The chips somebody picks from, and the filter chip that names the choice."""
    from app.matters.forms import MatterCreateForm

    labels = [str(label) for _, label in MatterCreateForm().fields["owner"].choices if label]
    assert "Marko" in labels
    assert "Marko Example" not in labels


def test_the_full_name_is_still_reachable_from_the_row(client, marko):
    """Short in the cell, complete in the tooltip: nothing is actually lost."""
    create_matter(title="Marko teema", owner=marko, reference_year=2026)
    client.force_login(marko)
    body = client.get(reverse("matters:matter_list")).content.decode()

    assert 'title="Marko Example"' in body


def test_the_stored_display_name_is_untouched(marko):
    marko.refresh_from_db()
    assert marko.display_name == "Marko Example"
