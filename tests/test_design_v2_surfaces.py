"""What the v2 rebuild moved, and what it must not have broken while moving it.

Three things this file exists to hold, and each of them shipped as a defect at
least once in a redesign somewhere:

* an address that stopped resolving because a page was renamed;
* a control the design added whose count and whose list disagreed;
* a page whose words drifted back to the ones the design replaced.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.core.development_status import ITEMS
from tests import factories

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Nothing that resolved before stopped resolving
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("/olulised-tahtajad/", "/jalgimine/tahtajad/"),
        ("/joustuvad-aktid/", "/jalgimine/joustumised/"),
        ("/toovoidud/", "/jalgimine/toovoidud/"),
    ],
)
def test_the_jalgimine_pages_kept_their_old_addresses(client, specialist, old, new):
    """Grouped under one prefix, and every existing link still lands (03-BACKEND §4)."""
    client.force_login(specialist)
    response = client.get(f"{old}?aasta=2026")
    assert response.status_code == 301
    assert response["Location"] == f"{new}?aasta=2026"
    assert client.get(new).status_code == 200


def test_the_arvamused_workspace_is_still_a_full_destination(client, specialist):
    """Not redirected into the Teemad section.

    The handoff asks for a redirect and the merged architecture deliberately
    keeps these as destinations with their own filters and their own pager. The
    brief forbids changing that without separate confirmation, so this asserts
    the behaviour that is in force (docs/design-v2-compatibility.md, DS-03).
    """
    client.force_login(specialist)
    assert client.get("/arvamused/").status_code == 200
    # Both tabs are full destinations for a specialist since ADR 0056: the two
    # lawyer roles read the corpus, because these are the department's own
    # outgoing letters. Access is still its own boundary and still asked by the
    # route — a READER gets 403 here, asserted in
    # `tests/test_opinion_archive_search.py` where that rule lives.
    assert client.get("/arvamused/arhiiv/").status_code == 200


def test_arvamused_is_not_back_on_the_navigation_bar(client, specialist):
    client.force_login(specialist)
    body = client.get(reverse("matters:matter_list")).content.decode()
    bar = body.split('<nav class="topnav"', 1)[1].split("</nav>", 1)[0]
    assert "Arvamused" not in bar


def test_the_development_status_page_is_not_for_lawyers(
    client, specialist, department_head, administrator
):
    """Internal tooling under /haldus/, 404 for everybody else."""
    url = reverse("core:development_status")

    client.force_login(specialist)
    assert client.get(url).status_code == 404

    client.force_login(department_head)
    assert client.get(url).status_code == 200

    client.force_login(administrator)
    assert client.get(url).status_code == 200


def test_the_development_status_page_is_on_no_navigation_bar(client, department_head):
    client.force_login(department_head)
    body = client.get(reverse("matters:matter_list")).content.decode()
    assert reverse("core:development_status") not in body


def test_every_status_item_says_what_happens_next():
    """A row with no next step is a note, not a worklist entry."""
    assert ITEMS
    keys = [item.key for item in ITEMS]
    assert len(keys) == len(set(keys))
    for item in ITEMS:
        assert item.issue.strip()
        assert item.why.strip()
        assert item.next_step.strip()


# ---------------------------------------------------------------------------
# The register's «näita korraga»
# ---------------------------------------------------------------------------


def test_the_register_shows_twelve_rows_by_default(client, specialist):
    """Twelve, because the Arvamused section is under the register now and
    twenty-five rows made a reader scroll past the whole thing to find it
    (02-EKRAANID §C)."""
    for index in range(20):
        factories.MatterFactory(owner=specialist, title=f"Teema {index:02d}")

    client.force_login(specialist)
    response = client.get(reverse("matters:matter_list"))

    assert len(response.context["page"].object_list) == 12
    assert response.context["total"] == 20


@pytest.mark.parametrize(("choice", "expected"), [("30", 20), ("50", 20), ("koik", 20)])
def test_the_page_size_control_shows_what_it_says(client, specialist, choice, expected):
    for index in range(20):
        factories.MatterFactory(owner=specialist, title=f"Teema {index:02d}")

    client.force_login(specialist)
    response = client.get(reverse("matters:matter_list"), {"kaupa": choice})

    assert len(response.context["page"].object_list) == expected


def test_an_unreadable_page_size_falls_back_rather_than_emptying_the_list(client, specialist):
    """A hand-edited URL should show the register, not an argument about it."""
    factories.MatterFactory(owner=specialist)
    client.force_login(specialist)
    response = client.get(reverse("matters:matter_list"), {"kaupa": "kolmteist"})
    assert response.status_code == 200
    assert response.context["page_size"] == "12"


def test_the_page_size_options_keep_every_other_filter(client, specialist):
    client.force_login(specialist)
    response = client.get(reverse("matters:matter_list"), {"olek": "avatud", "q": "pakend"})
    for option in response.context["page_size_options"]:
        assert "olek=avatud" in option["query"]
        assert "q=pakend" in option["query"]
        assert "leht=" not in option["query"]


# ---------------------------------------------------------------------------
# The words
# ---------------------------------------------------------------------------


def test_the_department_page_uses_the_agreed_words(client, department_head):
    client.force_login(department_head)
    body = client.get(reverse("matters:department")).content.decode()

    assert "Vajab sekkumist" in body
    assert "Vajab tähelepanu" not in body
    assert "läbi vaatamata" in body
    assert "triaaž" not in body
    assert ">SEIS<" not in body


def test_the_department_table_opens_a_persons_desk_not_the_register(
    client, department_head, specialist
):
    """A register row answers "what is this Matter"; the question a head clicks
    a name to ask is "what is on this person's desk" (design handoff, §A)."""
    factories.MatterFactory(owner=specialist)
    client.force_login(department_head)
    response = client.get(reverse("matters:department"))

    rows = {row.key: row for row in response.context["page"].team}
    assert rows[str(specialist.pk)].url == reverse(
        "matters:person_work", kwargs={"pk": specialist.pk}
    )
    # One's own row keeps the short address for one's own desk.
    assert rows[str(department_head.pk)].url == reverse("matters:my_work")


def test_the_department_page_has_no_seis_label(client, specialist):
    client.force_login(specialist)
    body = client.get(reverse("matters:department")).content.decode()
    assert "seis__label" not in body
