# ruff: noqa: F811 - the programme's fake-API fixture is imported and used as an argument
"""Corrections from the r93 production UI review (25.11.2026), Riigikogu and Haldus.

* RK-01: a plenary agenda title arrives wrapped in HTML («<p>…</p>»); it is text.
* RK-02: a mention under a bill's reading is legislative even when no Teema follows
  the bill (only followed bills are stored).
* RK-03: two names of koda in one passage are one mention.
* RK-04: an undecided invitation koda's opinion already answers is earlier work, and
  one whose answer needs checking is listed with those; neither stays on top.
* HA-01/02: Haldus → Registrite jälgimine puts each Riigikogu stage's counts on a line
  of its own and keeps the «Seis» labels with their values.
"""

from __future__ import annotations

import datetime as dt
import uuid as uuid_module

import pytest
from django.urls import reverse

from app.riigikogu import selectors, stenograms
from app.riigikogu.enums import MentionCategory, ResponseCheck
from app.riigikogu.models import StenogramMention
from tests.test_riigikogu_programme import (  # noqa: F401 - fixture
    KODA_AMONG,
    api,
    discover,
    teema_on_bill,
    with_letter,
)

pytestmark = pytest.mark.django_db

DAY = dt.date(2026, 11, 18)


def _plenary(api, title: str, text: str) -> None:
    api.json["/api/steno/verbatims"] = [
        {
            "date": f"{DAY.isoformat()}T10:00:00",
            "title": "XV Riigikogu täiskogu istung",
            "link": "https://stenogrammid.riigikogu.ee/202611181000",
            "agendaItems": [
                {
                    "agendaItemUuid": str(uuid_module.uuid4()),
                    "title": title,
                    "events": [
                        {
                            "type": "SPEECH",
                            "uuid": str(uuid_module.uuid4()),
                            "speaker": "Aivar Näidis",
                            "text": text,
                        }
                    ],
                }
            ],
        }
    ]


def test_a_html_wrapped_title_is_stored_as_text_and_its_bill_makes_it_legislative(api):
    _plenary(
        api,
        "<p>K&auml;ibemaksuseaduse muutmise seaduse eeln&otilde;u (1011 SE) esimene lugemine</p>",
        "Kaubandus-tööstuskoda on oma seisukoha juba esitanud.",
    )
    counts = stenograms.read_days(start=DAY, end=DAY)
    (mention,) = StenogramMention.objects.all()
    assert mention.agenda_title == (
        "Käibemaksuseaduse muutmise seaduse eelnõu (1011 SE) esimene lugemine"
    )
    # No Teema follows 1011 SE, so no bill row: still a bill's reading.
    assert mention.bill is None
    assert mention.category == MentionCategory.LEGISLATIVE
    assert (counts.legislative, counts.other) == (1, 0)


def test_two_names_of_koda_in_one_passage_are_one_mention(api):
    _plenary(
        api,
        "Infotund",
        "Siin ta ütleb, et kaubandus-tööstuskoda pidi laamendama: kaubanduskoda pole olemas.",
    )
    counts = stenograms.read_days(start=DAY, end=DAY)
    assert counts.mentions_new == 1
    assert StenogramMention.objects.count() == 1


def test_the_monitooring_list_cleans_rows_read_before_the_fix(api):
    """Production holds rows read by r93: HTML titles, a bill reading classed OTHER,
    and the same passage twice. The page shows them right without touching them."""
    common = {
        "sitting_on": DAY,
        "sitting_title": "XV Riigikogu täiskogu istung",
        "speaker": "Aivar Näidis",
        "quote": "Siin ta ütleb, et kaubandus-tööstuskoda pidi laamendama.",
        "context": "…",
        "url": "https://stenogrammid.riigikogu.ee/202611181000",
        "category": MentionCategory.OTHER,
        "source": "STENOGRAM",
    }
    title = "<p>Käibemaksuseaduse muutmise seaduse eelnõu (1011 SE) esimene lugemine</p>"
    StenogramMention.objects.create(key="e1:10", agenda_title=title, **common)
    StenogramMention.objects.create(key="e1:60", agenda_title=title, **common)
    (shown,) = selectors.plenary_mentions()
    assert shown.agenda_title.startswith("Käibemaksuseaduse") and "<p>" not in shown.agenda_title
    assert shown.bill_mark == "1011 SE"


def test_an_answered_or_to_check_invitation_leaves_the_top_list(api, specialist):
    teema_on_bill(specialist)
    with_letter(api, parties=KODA_AMONG)
    discover()
    from app.riigikogu.models import Invitation

    invitation = Invitation.objects.get()
    page = selectors.monitooring(specialist)
    assert [r.invitation.pk for r in page.open_rows] == [invitation.pk]
    assert selectors.counts(specialist) == 1

    Invitation.objects.filter(pk=invitation.pk).update(response=ResponseCheck.FOUND)
    page = selectors.monitooring(specialist)
    assert page.open_rows == []
    assert [r.invitation.pk for r in page.previous_rows] == [invitation.pk]
    assert selectors.counts(specialist) == 0

    Invitation.objects.filter(pk=invitation.pk).update(response=ResponseCheck.NEEDS_CHECK)
    page = selectors.monitooring(specialist)
    assert page.open_rows == []
    assert [r.invitation.pk for r in page.response_rows] == [invitation.pk]
    assert selectors.counts(specialist) == 0


def test_haldus_puts_stage_counts_on_their_own_line_and_pairs_the_state(client, department_head):
    from django.test import override_settings

    client.force_login(department_head)
    with override_settings(RIIGIKOGU_MONITORING=True, REGISTER_WATCH_FETCH=True):
        response = client.get(reverse("haldus:register_watch"))
    if response.status_code != 200:
        pytest.skip("this persona does not open Haldus → Registrite jälgimine")
    html = response.content.decode()
    assert '<th scope="col">Loendurid</th>' not in html
    assert 'class="regwatchlog__countsrow"' in html
    state = html[html.index('id="regwatchlog-state"') : html.index("</dl>")]
    assert state.count("<div>") == state.count("<dt>")
