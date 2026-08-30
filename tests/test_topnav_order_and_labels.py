"""The order of the main navigation, and the word on its deadline item.

Two changes, both to the shell and neither to the product behind it:

* «Minu asjad» is the first destination on the bar. A lawyer's own queue is
  where the day starts, so it is where the bar starts; Osakond, which used to
  hold the first slot, is second.
* The reading destination labelled «Jälgimine» reads «Tähtajad». The route, its
  namespace, its ``nav_active`` key and every bookmark under ``/jalgimine/`` are
  untouched — this is the word, not the domain, which is what the assertions
  below are written to hold apart.

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

#: The visible destinations, in the order a reader must find them. The last two
#: are inside the "Veel" disclosure below 1560px and inline above it — one
#: include rendered in two branches, so one order for both.
EXPECTED_ORDER = ["Minu asjad", "Osakond", "Teemad", "Tähtajad", "Statistika"]

#: What the deadline item used to say. It may not survive anywhere in the shell.
RETIRED_LABEL = "Jälgimine"

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
# B, C. the label, and the destination under it
# ---------------------------------------------------------------------------


def test_jalgimine_is_no_longer_a_visible_navigation_label(signed_in):
    """Gone from the bar at every width.

    The whole ``<nav>`` is searched, so a label merely pushed into the "Veel"
    disclosure would still fail this.
    """
    navigation = navigation_of(signed_in.get(reverse("matters:department")))

    assert RETIRED_LABEL not in navigation


def test_tahtajad_leads_where_jalgimine_led(signed_in):
    """Same view, same route name, same address. Only the word changed."""
    navigation = navigation_of(signed_in.get(reverse("matters:department")))
    anchor = anchor_for(navigation, "Tähtajad")

    destination = reverse("intelligence:important_dates")
    assert destination == "/jalgimine/tahtajad/"
    assert f'href="{destination}"' in anchor


def test_the_route_and_its_namespace_are_untouched(client, specialist):
    """The half of the change that must *not* have happened.

    A label change that quietly took the URL with it would break every
    bookmark, every internal link and the legacy redirects that still point at
    these views. So the addresses are resolved and then actually opened.
    """
    client.force_login(specialist)

    assert reverse("intelligence:important_dates") == "/jalgimine/tahtajad/"
    assert reverse("intelligence:effective_dates") == "/jalgimine/joustumised/"
    assert reverse("intelligence:work_victories") == "/jalgimine/toovoidud/"

    assert client.get("/jalgimine/tahtajad/").status_code == 200
    assert client.get(reverse("intelligence:important_dates_legacy")).status_code == 301


# ---------------------------------------------------------------------------
# D. the active state
# ---------------------------------------------------------------------------


def test_tahtajad_is_marked_current_on_its_own_page(client, specialist):
    """The internal key stayed ``jalgimine``.

    So this is the assertion that the renamed item still lights up — the one
    thing a word change can silently break.
    """
    client.force_login(specialist)
    navigation = navigation_of(client.get(reverse("intelligence:important_dates")))
    anchor = anchor_for(navigation, "Tähtajad")

    assert "is-active" in anchor
    assert 'aria-current="page"' in anchor


@pytest.mark.parametrize(
    "route,label",
    [
        ("matters:my_work", "Minu asjad"),
        ("matters:department", "Osakond"),
        ("matters:matter_list", "Teemad"),
        ("intelligence:important_dates", "Tähtajad"),
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


def test_the_veel_trigger_still_marks_the_renamed_destination(client, specialist):
    """Below 1560px the item is inside the disclosure and the closed trigger is
    all a reader can see. It carries the same signal, off the same key."""
    client.force_login(specialist)
    body = client.get(reverse("intelligence:important_dates")).content.decode()

    trigger = re.search(r'<summary class="topnav__trigger[^"]*"', body)
    assert trigger, "the disclosure trigger is not on the page"
    assert "is-active" in trigger.group(0)


# ---------------------------------------------------------------------------
# E. the shared gate with no persona selected
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
    (Vali kasutaja brief 23). The remaining four keep their relative order.
    """
    navigation = navigation_of(behind_the_gate.get(reverse("matters:department")))

    assert ">Minu asjad</a>" not in navigation
    assert labels_of(navigation) == ["Osakond", "Teemad", "Tähtajad", "Statistika"]


def test_the_renamed_item_is_offered_behind_the_gate_too(behind_the_gate):
    """The label is not conditional on being signed in as somebody."""
    navigation = navigation_of(behind_the_gate.get(reverse("matters:department")))

    assert RETIRED_LABEL not in navigation
    assert f'href="{reverse("intelligence:important_dates")}"' in anchor_for(navigation, "Tähtajad")


# ---------------------------------------------------------------------------
# G, H. the two branches
# ---------------------------------------------------------------------------


def test_the_secondary_pair_reads_the_same_in_both_branches(signed_in):
    """The wide row and the disclosure hold the same two links in the same
    order, because they are the same include.

    The contract the shell depends on — exactly one branch displayed at a time,
    so each destination reaches the accessibility tree once — is asserted in a
    real browser (e2e/test_ui_shell.py). What is asserted here is the ordering
    inside each branch, which no screenshot would show.
    """
    navigation = navigation_of(signed_in.get(reverse("matters:department")))

    wide = navigation.index('<span class="topnav__wide">')
    menu = navigation.index('<div class="topnav__menu">')

    for branch in (navigation[wide:menu], navigation[menu:]):
        assert labels_of(branch) == ["Tähtajad", "Statistika"]
