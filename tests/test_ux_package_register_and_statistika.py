"""Two presentation changes and one remembered-block rollout (UX package 2026-10-28).

* **Requirement 7** — Statistika → Tegevus → *Saadetud arvamused* lists three
  columns, `Saadetud`, `Teema`, `Adressaadid`. `Liik` is gone from that table
  only: the CSV still carries it, the `?arvamus=` narrowing still works, and the
  Arvamused workspace — a different screen — is untouched.
* **Requirement 8** — the Teemad register's Kuupäev cell prints a step's date
  alone: `12.11.2026`, not `PLAANIS 12.11.2026`. The word stays in the date's
  tooltip and accessible text; a fallback `Arvamuse tähtaeg` keeps its visible
  word; the outstanding-deadline annotation under it, the overdue styling and
  words, the approximate precision and the sort are all as they were.
* **Requirement 9** — Statistika's stable informational foldouts carry the
  remembered-block attributes (app/accounts/disclosures.py), closed by default.

Every title and organisation here is invented.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.accounts import disclosures
from app.matters import views
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics
from app.workflow.services import set_next_action
from tests import factories
from tests.test_register_interactive_columns import dates_rendered, estonian, titles_on
from tests.test_saaja_means_addressee import ALL_YEARS, _send, _table

pytestmark = pytest.mark.django_db

REGISTER = reverse("matters:matter_list")
STATISTIKA = reverse("reporting:submissions")
TODAY = timezone.localdate()


# ===========================================================================
# Requirement 7 — no `Liik` in Statistika's sent-opinions table
# ===========================================================================


@pytest.fixture
def letter(specialist):
    ministry = factories.OrganisationFactory(name="Sünteetiline ministeerium")
    matter = factories.MatterFactory(owner=specialist, title="Sünteetiline eelnõu")
    return _send(matter, title="Sünteetiline arvamus", actor=specialist, addressees=[ministry])


def test_the_sent_opinions_table_has_three_columns_and_no_liik(signed_in, letter):
    table = _table(signed_in.get(STATISTIKA, ALL_YEARS))

    assert table.header == ["Saadetud", "Teema", "Adressaadid"]
    assert len(table.rows) == 1
    assert len(table.rows[0]) == 3
    assert "Sünteetiline arvamus" in table.rows[0][1]
    assert table.rows[0][2] == "Sünteetiline ministeerium"
    assert letter.get_kind_display() not in " ".join(table.rows[0])


def test_the_csv_still_carries_the_kind(signed_in, letter):
    response = signed_in.get(reverse("reporting:export", kwargs={"slug": "arvamused"}), ALL_YEARS)

    assert response.status_code == 200
    content = b"".join(response.streaming_content).decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(content), delimiter=";"))
    header = rows[0]
    assert "liik" in header
    assert rows[1][header.index("liik")] == letter.get_kind_display()


def test_the_kind_filter_still_narrows_the_table(signed_in, letter):
    kept = signed_in.get(STATISTIKA, {**ALL_YEARS, "arvamus": letter.kind})
    other = "OTHER" if letter.kind != "OTHER" else "FORMAL_OPINION"
    dropped = signed_in.get(STATISTIKA, {**ALL_YEARS, "arvamus": other})

    assert kept.context["total"] == 1
    assert dropped.context["total"] == 0


def test_the_arvamused_workspace_is_a_different_screen_and_keeps_its_columns(signed_in, letter):
    """Only Statistika's table lost the column; the workspace is not this page."""
    table = _table(signed_in.get(reverse("submissions:sent")))

    assert table.header != ["Saadetud", "Teema", "Adressaadid"]
    assert len(table.rows) == 1


# ===========================================================================
# Requirement 8 — the Kuupäev cell prints the step's date alone
# ===========================================================================


def stepped(owner, title: str, on: date, **kwargs):
    matter = factories.MatterFactory(owner=owner, title=title, **kwargs.pop("matter", {}))
    set_next_action(
        matter=matter,
        text=f"Samm: {title}",
        target_date=on,
        actor=owner,
        responsible=owner,
        **kwargs,
    )
    return matter


def date_cells(body: str) -> list[str]:
    table = body[body.index('<table class="table table--register">') :]
    return re.findall(r'<td class="table__date">(.*?)</td>', table, re.DOTALL)


def test_a_planned_step_prints_its_date_without_plaanis(signed_in, specialist):
    on = TODAY + timedelta(days=15)
    stepped(
        specialist,
        "Plaaniga teema",
        on,
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
    )

    body = signed_in.get(REGISTER).content.decode()
    (cell,) = date_cells(body)

    # The word is the date's tooltip and its accessible text, not a printed label.
    assert f'title="Plaanis: {estonian(on)}"' in cell
    assert '<span class="visually-hidden">Plaanis </span>' in cell
    printed = cell.replace(f'title="Plaanis: {estonian(on)}"', "").replace(
        '<span class="visually-hidden">Plaanis </span>', ""
    )
    assert "datemeaning" not in printed
    assert "plaanis" not in printed.casefold()
    assert dates_rendered(signed_in.get(REGISTER)) == [estonian(on)]


def test_a_review_date_prints_without_vaatan_ule(signed_in, specialist):
    on = TODAY + timedelta(days=3)
    stepped(
        specialist,
        "Jälgitav teema",
        on,
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.REVIEW_ON,
    )

    (cell,) = date_cells(signed_in.get(REGISTER).content.decode())

    assert "datemeaning" not in cell
    assert f'title="Vaatan üle: {estonian(on)}"' in cell
    assert '<span class="visually-hidden">Vaatan üle </span>' in cell


def test_an_overdue_step_keeps_its_colour_and_its_words(signed_in, specialist):
    on = TODAY - timedelta(days=2)
    stepped(
        specialist,
        "Hilinenud teema",
        on,
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
    )

    (cell,) = date_cells(signed_in.get(REGISTER).content.decode())

    assert "dateline--overdue" in cell
    assert '<span class="dateline__late"> · üle tähtaja</span>' in cell
    assert "datemeaning" not in cell
    assert re.search(rf">\s*{re.escape(estonian(on))}</span>", cell)


def test_an_approximate_date_stays_approximate(signed_in, specialist):
    first = (TODAY.replace(day=1) + timedelta(days=95)).replace(day=1)
    stepped(
        specialist,
        "Kuu täpsusega teema",
        first,
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.EXPECTED_AROUND,
        date_precision=DatePrecision.MONTH,
    )

    (cell,) = date_cells(signed_in.get(REGISTER).content.decode())

    assert "dateline--approximate" in cell
    assert estonian(first) not in cell, "a month must not print as its first day"
    assert 'title="Oodatav: ' in cell


def test_a_fallback_response_deadline_keeps_its_visible_word(signed_in, specialist):
    factories.MatterFactory(
        owner=specialist,
        title="Ainult tähtajaga teema",
        response_deadline=TODAY + timedelta(days=9),
    )

    (cell,) = date_cells(signed_in.get(REGISTER).content.decode())

    assert '<span class="datemeaning">Arvamuse tähtaeg</span>' in cell


def test_the_outstanding_deadline_annotation_is_kept(signed_in, specialist):
    """The plan on the first line, the owed `Arvamuse tähtaeg` under it."""
    stepped(
        specialist,
        "Plaan ja võlg",
        TODAY + timedelta(days=40),
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.REVIEW_ON,
        matter={"response_deadline": TODAY + timedelta(days=5)},
    )

    (cell,) = date_cells(signed_in.get(REGISTER).content.decode())

    assert '<span class="dateowed__label">Arvamuse tähtaeg</span>' in cell
    assert "datemeaning" not in cell


def test_a_missing_date_is_still_a_dash(signed_in, specialist):
    factories.MatterFactory(owner=specialist, title="Kuupäevata teema")

    assert dates_rendered(signed_in.get(REGISTER)) == ["—"]


def test_the_sort_is_unchanged(signed_in, specialist):
    late = stepped(
        specialist,
        "Hiline",
        TODAY + timedelta(days=30),
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
    )
    early = stepped(
        specialist,
        "Varane",
        TODAY + timedelta(days=1),
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.REVIEW_ON,
    )
    undated = factories.MatterFactory(owner=specialist, title="Kuupäevata")

    response = signed_in.get(REGISTER, {"jarjestus": views.DATE_SORT_ASC})

    assert titles_on(response) == [early.title, late.title, undated.title]
    assert dates_rendered(response) == [
        estonian(TODAY + timedelta(days=1)),
        estonian(TODAY + timedelta(days=30)),
        "—",
    ]


# ===========================================================================
# Requirement 9 — Statistika's informational foldouts are remembered
# ===========================================================================


def test_the_overview_definitions_foldout_is_remembered_and_closed_by_default(
    client, department_head
):
    client.force_login(department_head)

    body = client.get(reverse("reporting:overview")).content.decode()

    tag = re.search(r'<details class="foldout"[^>]*>', body)
    assert tag, "the definitions foldout is on the page"
    assert 'data-disclosure="reporting.overview.definitions"' in tag.group(0)
    assert 'data-disclosure-saved="0"' in tag.group(0)
    assert not re.search(r"\sopen(\s|>|$)", tag.group(0))


def test_every_reporting_definition_and_chart_table_carries_its_key():
    """Read off the templates: every one of them, not one page's sample."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "templates" / "reporting"
    keys = {"definition": "reporting.definition", "chart__fallback": "reporting.chart_table"}
    seen = dict.fromkeys(keys, 0)
    for template in root.glob("*.html"):
        text = template.read_text(encoding="utf-8")
        for cls, key in keys.items():
            for tag in re.findall(rf'<details class="{cls}"[^>]*>', text):
                seen[cls] += 1
                assert f'{{% disclosure "{key}" %}}' in tag, (template.name, tag)
                assert " open" not in tag.replace("{% disclosure", ""), (template.name, tag)
                assert "{% load disclosures %}" in text, template.name
    assert seen["definition"] >= 7
    assert seen["chart__fallback"] >= 3
    for key in ("reporting.overview.definitions", "reporting.definition", "reporting.chart_table"):
        assert disclosures.DISCLOSURES[key].default_open is False


def test_a_remembered_open_definition_is_drawn_open(client, department_head):
    client.force_login(department_head)
    response = client.post(
        reverse("accounts:save_disclosure"),
        {"key": "reporting.overview.definitions", "open": "1"},
    )
    assert response.status_code in (200, 204)

    body = client.get(reverse("reporting:overview")).content.decode()
    tag = re.search(r'<details class="foldout"[^>]*>', body)
    assert tag and 'data-disclosure-saved="1"' in tag.group(0) and " open" in tag.group(0)
