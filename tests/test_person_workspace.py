"""Minu asjad and the manager's view of a colleague's desk.

One page, two modes, and the three rules that make the second mode safe: the
gate is the department head's existing one and answers 404, every count is taken
after `visible_to`, and a personal scratchpad is *absent* from a manager's
response rather than hidden in it.

Every fixture date is relative to today. A test written around a production date
passes for a fortnight and then starts failing for reasons nobody can reproduce.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.intelligence.services import add_important_date
from app.matters.models import PersonalScratchpad
from app.matters.my_work import build_my_work
from app.matters.services import create_matter
from app.workflow.dates import period_bounds
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

SCRATCHPAD_TEXT = "helista esmaspaeval ministeeriumi kantseleisse"


@pytest.fixture
def today():
    return timezone.localdate()


def _matter(owner, title="Näidisteema", **kwargs):
    return create_matter(title=title, owner=owner, **kwargs)


def _person_url(user):
    return reverse("matters:person_work", kwargs={"pk": user.pk})


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_person_workspace_gate(client, specialist, other_specialist, department_head):
    """Self and the department head, and 404 for everybody else.

    404 rather than 403, the convention every restricted surface in this
    application follows: a 403 confirms the page is there and that somebody else
    may read it (03-BACKEND §5).
    """
    client.force_login(specialist)
    assert client.get(_person_url(specialist)).status_code == 200

    client.force_login(department_head)
    assert client.get(_person_url(specialist)).status_code == 200

    client.force_login(other_specialist)
    assert client.get(_person_url(specialist)).status_code == 404


def test_an_id_that_names_nobody_answers_the_same_404(client, department_head):
    """The refusal must not double as a way to find out who exists."""
    client.force_login(department_head)
    missing = "00000000-0000-4000-8000-000000000000"
    assert client.get(f"/inimesed/{missing}/asjad/").status_code == 404


def test_the_old_minu_too_address_still_lands_on_the_page(client, specialist):
    """Every bookmark and every pasted link keeps working (03-BACKEND §4)."""
    client.force_login(specialist)
    response = client.get("/minu-too/?kuni=koik")
    assert response.status_code == 301
    assert response["Location"] == "/minu-asjad/?kuni=koik"


# ---------------------------------------------------------------------------
# The scratchpad
# ---------------------------------------------------------------------------


def test_scratchpad_privacy(client, specialist, department_head):
    """A manager's response does not contain the notes. Not hidden — absent.

    The block is inside `{% if is_self %}`, the view does not fetch the row for
    anybody but `request.user`, and the model is keyed on the person. Three
    layers, and this asserts the one a reader could otherwise defeat with
    view-source (01-EHITUSJUHIS §3.5).
    """
    PersonalScratchpad.objects.create(user=specialist, body=SCRATCHPAD_TEXT)

    client.force_login(specialist)
    mine = client.get(reverse("matters:my_work")).content.decode()
    assert SCRATCHPAD_TEXT in mine

    client.force_login(department_head)
    theirs = client.get(_person_url(specialist)).content.decode()
    assert SCRATCHPAD_TEXT not in theirs
    assert "pw-note" not in theirs
    # And the manager gets the block that replaces it.
    assert "Kiirvaade" in theirs
    assert "Isiklikke märkmeid näeb ainult inimene ise." in theirs


def test_the_scratchpad_endpoint_writes_only_the_signed_in_person(
    client, specialist, department_head
):
    """There is no `subject` to widen, and adding one would not help.

    The service takes the user, not an id, so a POST carrying somebody else's
    primary key writes the caller's own row — which is what this asserts,
    because a refusal that depends on remembering to check is a refusal that
    gets edited out (03-BACKEND §2).
    """
    client.force_login(department_head)
    response = client.post(
        reverse("matters:save_scratchpad"),
        {"body": "juhi märkus", "subject": str(specialist.pk), "user": str(specialist.pk)},
    )
    assert response.status_code == 200

    assert PersonalScratchpad.objects.get(user=department_head).body == "juhi märkus"
    assert not PersonalScratchpad.objects.filter(user=specialist).exists()


def test_the_scratchpad_refuses_a_get(client, specialist):
    client.force_login(specialist)
    assert client.get(reverse("matters:save_scratchpad")).status_code == 405


# ---------------------------------------------------------------------------
# The rules that survive the redesign
# ---------------------------------------------------------------------------


def test_wait_is_never_late_on_the_page(client, specialist, today):
    """A passed OOTAN review date is neutral, never red and never «üle».

    The band it sits in changed — reviews are ordinary dated work now and are
    merged into *Sel nädalal* — and the semantics did not (03-BACKEND §1).
    """
    matter = _matter(specialist, title="Käibemaksuseaduse muutmine")
    set_next_action(
        matter=matter,
        text="Ootan ministeeriumi vastust",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.EXPECTED_AROUND,
        target_date=today - timedelta(days=9),
        actor=specialist,
    )

    client.force_login(specialist)
    body = client.get(reverse("matters:my_work")).content.decode()

    assert "Ootan ministeeriumi vastust" in body
    assert "9 p üle" not in body
    assert "9 p" in body
    assert "Üle tähtaja" not in body

    work = build_my_work(specialist, today=today)
    assert work.overdue == 0
    item = next(item for band in work.bands for item in band.items)
    assert item.is_review_ripe and not item.is_overdue
    assert "workrow2--overdue" not in body


def test_month_precision_is_printed_verbatim(client, specialist, today):
    """«september 2026» stays «september 2026». No day is invented for it."""
    anchor = (today.replace(day=1) + timedelta(days=62)).replace(day=1)
    _, end = period_bounds(anchor, DatePrecision.MONTH)
    matter = _matter(specialist, title="Riigihangete seaduse muutmine")
    set_next_action(
        matter=matter,
        text="Ootan eelnõu jõudmist valitsusse",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.EXPECTED_AROUND,
        target_date=anchor,
        date_precision=DatePrecision.MONTH,
        actor=specialist,
    )

    client.force_login(specialist)
    body = client.get(reverse("matters:my_work"), {"kuni": "koik"}).content.decode()

    item = next(
        item for band in build_my_work(specialist, today=today).bands for item in band.items
    )
    assert item.display_date in body
    assert "." not in item.display_date
    assert item.is_approximate
    # A month-precise date never lands in a band headed by a number of days.
    assert item.period_end == end


def test_a_month_precise_date_is_not_in_jargmised_30_paeva(specialist, today):
    """Even when its anchor falls inside the window (01-EHITUSJUHIS §3.2)."""
    anchor = today + timedelta(days=12)
    matter = _matter(specialist)
    set_next_action(
        matter=matter,
        text="Ootan otsust",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.EXPECTED_AROUND,
        target_date=anchor.replace(day=1),
        date_precision=DatePrecision.MONTH,
        actor=specialist,
    )
    bands = {band.key: band for band in build_my_work(specialist, today=today).bands}
    assert "jargmised_30_paeva" not in bands


def test_count_matches_population(client, specialist, today):
    """Every figure on the strip opens a list of exactly that many rows.

    The promise the whole page rests on: a number is a count, and the link under
    it is the same query (01-EHITUSJUHIS §3.3).
    """
    for index in range(3):
        matter = _matter(specialist, title=f"Teema {index}")
        set_next_action(
            matter=matter,
            text=f"Tee midagi {index}",
            kind=ActionKind.DO,
            date_semantics=DateSemantics.DEADLINE,
            target_date=today - timedelta(days=index + 1),
            actor=specialist,
        )
    _matter(specialist, title="Vaikne teema")

    client.force_login(specialist)
    work = build_my_work(specialist, today=today)

    for figure in work.seis:
        listed = client.get(figure.url)
        assert listed.status_code == 200, figure.url
        assert listed.context["total"] == figure.value, figure.caption


def test_restricted_matter_not_counted(client, specialist, reader, today):
    """A reader without the entitlement sees a smaller number, not a lock.

    `reader` is the one role that is genuinely outside the department's
    business access — since docs/adr/0042 a second specialist is not an
    outsider. What it must see is a *shorter* list, and never the restricted
    Matter's own words (01-EHITUSJUHIS §3.4).
    """
    restricted_title = "Liikme konfidentsiaalne kaebus"
    matter = factories.MatterFactory(
        owner=specialist, title=restricted_title, visibility=Visibility.RESTRICTED
    )
    set_next_action(
        matter=matter,
        text="Vasta ametile",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today + timedelta(days=2),
        actor=specialist,
        responsible=specialist,
    )
    factories.MatterFactory(owner=specialist, title="Tavaline teema")

    entitled = build_my_work(specialist, today=today)
    refused = build_my_work(reader, today=today, subject=specialist)

    assert entitled.open_matters == 2
    assert refused.open_matters == 1
    assert restricted_title not in {row.matter.title for row in refused.portfolio.all_rows}
    assert refused.overdue == 0

    client.force_login(reader)
    assert restricted_title not in client.get(reverse("matters:matter_list")).content.decode()


def test_no_complete_button(client, specialist, today):
    """No one-click «tehtuks», and no keyboard hint for a control that is gone.

    Completing a step without setting the follow-up is half a transaction, so it
    goes through the ⋯ menu to the Matter page (01-EHITUSJUHIS §3.6).
    """
    matter = _matter(specialist)
    set_next_action(
        matter=matter,
        text="Saada arvamus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today,
        actor=specialist,
    )

    client.force_login(specialist)
    body = client.get(reverse("matters:my_work")).content.decode()

    assert "complete_work_item" not in body
    assert "/valmis/" not in body
    assert "uxdone" not in body
    assert "data-workdone" not in body
    assert "tehtud" not in body.lower().split("rowmenu")[0]
    # The ⋯ menu still offers it, as a link to where the follow-up is set.
    assert "Märgi tehtuks" in body


# ---------------------------------------------------------------------------
# The vocabulary the design settled
# ---------------------------------------------------------------------------


def test_the_page_uses_the_agreed_words(client, specialist, today):
    matter = _matter(specialist)
    set_next_action(
        matter=matter,
        text="Saada arvamus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today,
        actor=specialist,
    )
    add_important_date(
        matter=matter,
        title="Avaliku konsultatsiooni lõpp",
        date_value=today + timedelta(days=3),
        period_end=today + timedelta(days=3),
        actor=specialist,
    )

    client.force_login(specialist)
    body = client.get(reverse("matters:my_work")).content.decode()

    assert "Minu asjad" in body
    assert "Minu töö" not in body
    assert "Aktiivsed teemad" in body
    assert "Vajab sekkumist" in body
    assert "Vajab tähelepanu" not in body
    assert "Ülevaatamiseks küps" not in body
    assert "SEIS" not in body


def test_band_counters_are_bare_numbers(client, specialist, today):
    """«8», not «8 tegevust» (01-EHITUSJUHIS §4).

    The prototype still prints the longer form and the conflict history agreed
    with it; the final rule in `01` does not, and `01` wins
    (docs/design-v2-compatibility.md, DS-01).
    """
    matter = _matter(specialist)
    set_next_action(
        matter=matter,
        text="Saada arvamus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today,
        actor=specialist,
    )

    client.force_login(specialist)
    body = client.get(reverse("matters:my_work")).content.decode()

    assert re.search(r'class="workband__count">\s*1\s*</span>', body)
    assert "tegevust</span>" not in body


def test_the_manager_view_says_whose_desk_it_is(client, specialist, department_head):
    client.force_login(department_head)
    body = client.get(_person_url(specialist)).content.decode()

    assert f"{specialist.display_name} · asjad" in body
    assert "← Osakonna töö" in body
    # The heading names the colleague; «Minu asjad» is only the nav item.
    heading = body.split('workhead__title">', 1)[1].split("</h1>", 1)[0]
    assert "Minu asjad" not in heading
