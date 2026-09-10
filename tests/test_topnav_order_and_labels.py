"""The order of the main navigation, and which item is marked current.

The bar reads: **Minu asjad, Osakond, Teemad, Statistika**. A lawyer's own queue
is where the day starts, so it is where the bar starts.

There used to be a fifth item between Teemad and Statistika. It was called
«Jälgimine», then «Tähtajad», and it opened three generated department-wide
reading pages over the structured Matter facts. It is gone, and so are they:
`Töövõit` and `Jõustumine` are structured filters on Teemad and an `Oluline
tähtaeg` is its owner's own upcoming work, so there is one register to search
and one personal queue rather than four parallel lists of the same Matters
(docs/adr/0067).

That product decision, its redirects and its filters belong to
`tests/test_teemad_consolidation.py`. What this file keeps is the shell
contract: the order, the active state, and the two branches of the include.

The order is asserted by position in the rendered ``<nav>`` rather than by mere
presence: a test that only asks "is Osakond on the bar" passes on every possible
ordering, including the one this replaces.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from app.accounts.enums import AuthMode

pytestmark = pytest.mark.django_db

#: The visible destinations, in the order a reader must find them. The last one
#: is inside the "Veel" disclosure below 1560px and inline above it — one
#: include rendered in two branches, so one order for both.
EXPECTED_ORDER = ["Minu asjad", "Osakond", "Teemad", "Statistika"]

#: What the fifth item said, in both of the words it ever used. Neither may
#: survive anywhere in the shell.
RETIRED_LABELS = ("Jälgimine", "Tähtajad")

LINK = re.compile(r"<a\b[^>]*>([^<]+)</a>")


def navigation_of(response) -> str:
    """The main navigation's markup, and nothing else on the page.

    Sliced rather than searched for, because every assertion here is about what
    is on the bar. «Tähtajad» is ordinary Estonian and appears in headings,
    tables and filters all over this application; a bare ``in body`` would pass
    on any of them.
    """
    assert response.status_code == 200
    body = response.content.decode()
    start = body.index('<nav class="topnav"')
    return body[start : body.index("</nav>", start)]


def labels_of(navigation: str) -> list[str]:
    """Every link the bar renders, in document order.

    Both branches — the inline row and the disclosure — come from the same
    include and only one of them is ever displayed, so reading the markup sees
    each secondary destination twice. The list is de-duplicated here, keeping
    first-seen order; that the two branches agree is asserted separately.
    """
    seen: list[str] = []
    for label in LINK.findall(navigation):
        text = label.strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def anchor_for(navigation: str, label: str) -> str:
    """The whole anchor carrying ``label``, attributes and all.

    Matched rather than sliced at by offset: the attribute order in the
    template is not what any of these tests is about.
    """
    match = re.search(rf"<a\b[^>]*>{re.escape(label)}</a>", navigation)
    assert match, f"{label!r} is not on the bar:\n{navigation}"
    return match.group(0)


# ---------------------------------------------------------------------------
# A. the order
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "route",
    ["matters:department", "matters:my_work", "matters:matter_list", "reporting:overview"],
)
def test_the_bar_reads_in_the_approved_order(signed_in, route):
    """Asserted on four surfaces, because the bar is one template on all of them.

    A single-page check would still hold if some page shadowed the include with
    a copy of its own, which is the shape of defect an ordering change would
    otherwise hide.
    """
    assert labels_of(navigation_of(signed_in.get(reverse(route)))) == EXPECTED_ORDER


def test_minu_asjad_comes_before_osakond(signed_in):
    """The change stated as the reader meets it, not as a list equality.

    Kept separate from the test above so that a later, legitimate addition to
    the bar cannot quietly take this specific guarantee with it when somebody
    updates the expected list.
    """
    navigation = navigation_of(signed_in.get(reverse("matters:department")))

    assert navigation.index(">Minu asjad</a>") < navigation.index(">Osakond</a>")


def test_the_head_reads_the_same_order_as_a_specialist(client, department_head):
    """The order is not conditional on a role.

    Osakond was head-only navigation once (docs/adr/0049); it is not any more,
    and neither is anything else on this bar.
    """
    client.force_login(department_head)

    assert labels_of(navigation_of(client.get(reverse("matters:department")))) == EXPECTED_ORDER


# ---------------------------------------------------------------------------
# B. the retired fifth item
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("retired", RETIRED_LABELS)
def test_the_retired_deadline_item_is_on_no_branch_of_the_bar(signed_in, retired):
    """Gone at every width.

    The whole ``<nav>`` is searched, so a label merely pushed into the "Veel"
    disclosure would still fail this.
    """
    assert retired not in navigation_of(signed_in.get(reverse("matters:department")))


def test_the_veel_trigger_is_not_marked_by_a_destination_that_no_longer_exists(
    client, specialist
):
    """The trigger lit up for `nav_active == 'jalgimine'` as well as Statistika.

    Nothing sets that key any more, and a condition kept for a page that is gone
    is a condition nobody would notice had stopped meaning anything.
    """
    client.force_login(specialist)
    body = client.get(reverse("matters:matter_list")).content.decode()

    trigger = re.search(r'<summary class="topnav__trigger[^"]*"', body)
    assert trigger, "the disclosure trigger is not on the page"
    assert "is-active" not in trigger.group(0)


def test_the_veel_trigger_still_marks_statistika(client, specialist):
    """Below 1560px Statistika is inside the disclosure and the closed trigger is
    all a reader can see. It carries the signal, off its own key."""
    client.force_login(specialist)
    body = client.get(reverse("reporting:overview")).content.decode()

    trigger = re.search(r'<summary class="topnav__trigger[^"]*"', body)
    assert trigger, "the disclosure trigger is not on the page"
    assert "is-active" in trigger.group(0)


# ---------------------------------------------------------------------------
# C. the active state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "route,label",
    [
        ("matters:my_work", "Minu asjad"),
        ("matters:department", "Osakond"),
        ("matters:matter_list", "Teemad"),
        ("reporting:overview", "Statistika"),
    ],
)
def test_each_destination_marks_only_itself(client, specialist, route, label):
    """Exactly one current item per page, and it is the right one.

    Moving an anchor between its siblings is the change most likely to leave
    two items underlined, so both halves are asserted.
    """
    client.force_login(specialist)
    navigation = navigation_of(client.get(reverse(route)))

    current = {
        LINK.search(anchor).group(1).strip()
        for anchor in re.findall(r'<a\b[^>]*aria-current="page"[^>]*>[^<]*</a>', navigation)
    }
    assert current == {label}


# ---------------------------------------------------------------------------
# D. the shared gate with no persona selected
# ---------------------------------------------------------------------------


@pytest.fixture
def behind_the_gate(client, settings):
    """A client that has typed the department password and selected nobody."""
    password = "seda-parooli-ei-ole-kusagil-mujal"  # noqa: S105
    settings.AUTH_MODE = AuthMode.SHARED_GATE
    settings.SHARED_GATE_PASSWORD = password
    settings.DEV_LOGIN_ENABLED = False
    settings.LOGIN_URL = "accounts:choose_persona"

    response = client.post(reverse("accounts:shared_gate"), {"password": password})
    assert response.status_code == 302
    return client


def test_a_reader_with_no_persona_is_not_offered_minu_asjad(behind_the_gate):
    """Preserved deliberately, and not weakened to make the order literal.

    There is no "minu" behind the shared door until somebody is selected, and
    ``login_required`` on that surface would bounce the reader straight back to
    the persona page. An item that can only fail is worse than no item
    (Vali kasutaja brief 23). The remaining three keep their relative order.
    """
    navigation = navigation_of(behind_the_gate.get(reverse("matters:department")))

    assert ">Minu asjad</a>" not in navigation
    assert labels_of(navigation) == ["Osakond", "Teemad", "Statistika"]


@pytest.mark.parametrize("retired", RETIRED_LABELS)
def test_the_retired_item_is_absent_behind_the_gate_too(behind_the_gate, retired):
    """Its removal is not conditional on being signed in as somebody."""
    assert retired not in navigation_of(behind_the_gate.get(reverse("matters:department")))


# ---------------------------------------------------------------------------
# E. the two branches
# ---------------------------------------------------------------------------


def test_the_secondary_items_read_the_same_in_both_branches(signed_in):
    """The wide row and the disclosure hold the same links in the same order,
    because they are the same include.

    The contract the shell depends on — exactly one branch displayed at a time,
    so each destination reaches the accessibility tree once — is asserted in a
    real browser (e2e/test_ui_shell.py). What is asserted here is the ordering
    inside each branch, which no screenshot would show.
    """
    navigation = navigation_of(signed_in.get(reverse("matters:department")))

    wide = navigation.index('<span class="topnav__wide">')
    menu = navigation.index('<div class="topnav__menu">')

    for branch in (navigation[wide:menu], navigation[menu:]):
        assert labels_of(branch) == ["Statistika"]
