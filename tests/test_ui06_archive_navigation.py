"""UI-06: «Arhiiv» leaves the status tabs; the archive filter keeps its meaning.

Archive is a record mode and closed is a lifecycle state — two facts. The tab
row is Avatud | Suletud | Kõik; the archive is asked for under Kirje liik
(`?liik=ARCHIVE`, any status). An address that still carries `?olek=arhiiv`
keeps exactly the rows it had (every archive record, open or closed — the
same as `?olek=koik&liik=ARCHIVE`) and says so with a removable chip. On a
row, «Arhiiv» is drawn only where «Suletud» is not: on an open archive record
it is the one thing saying this is a historical register row nobody has
activated as current work.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse
from django.utils import timezone

from app.matters.enums import RecordMode
from app.workflow.enums import Disposition
from tests import factories

pytestmark = pytest.mark.django_db

REGISTER = reverse("matters:matter_list")


@pytest.fixture
def world(db):
    """Archive ≠ closed: each of the four combinations once."""
    return {
        "open_full": factories.MatterFactory(title="Avatud täielik"),
        "closed_full": factories.MatterFactory(
            title="Suletud täielik",
            is_open=False,
            closed_at=timezone.now(),
            disposition=Disposition.COMPLETED,
        ),
        "open_archive": factories.ArchiveMatterFactory(title="Avatud arhiivirida"),
        "closed_archive": factories.ArchiveMatterFactory(
            title="Suletud arhiivirida", is_open=False
        ),
    }


@pytest.fixture
def signed_in(client, world):
    client.force_login(factories.DepartmentHeadFactory())
    return client


def titles(response) -> set[str]:
    return {matter.title for matter in response.context["page"].object_list}


def test_the_status_tabs_are_avatud_suletud_koik(signed_in):
    options = signed_in.get(REGISTER).context["status_options"]

    assert [(option["key"], option["label"]) for option in options] == [
        ("avatud", "Avatud"),
        ("suletud", "Suletud"),
        ("koik", "Kõik"),
    ]
    assert {option["key"]: option["count"] for option in options} == {
        "avatud": 2,
        "suletud": 2,
        "koik": 4,
    }
    body = signed_in.get(REGISTER).content.decode()
    nav = body.split('aria-label="Kirje seis"', 1)[1].split("</nav>", 1)[0]
    assert "olek=arhiiv" not in nav and "Arhiiv" not in nav


def test_an_old_arhiiv_link_keeps_its_archive_semantics(signed_in, world):
    """Every archive record, open or closed — never «suletud»."""
    old = titles(signed_in.get(REGISTER, {"olek": "arhiiv"}))

    assert old == {"Avatud arhiivirida", "Suletud arhiivirida"}
    assert old == titles(signed_in.get(REGISTER, {"olek": "koik", "liik": RecordMode.ARCHIVE}))
    assert old != titles(signed_in.get(REGISTER, {"olek": "suletud"}))
    # Combined with another dimension it still intersects, as it always did.
    assert titles(signed_in.get(REGISTER, {"olek": "arhiiv", "liik": RecordMode.FULL})) == set()


def test_an_old_arhiiv_link_says_what_is_filtering_it(signed_in):
    response = signed_in.get(REGISTER, {"olek": "arhiiv", "aasta": "2026"})

    chip = next(chip for chip in response.context["active_filters"] if chip["name"] == "olek")
    assert (chip["label"], chip["value"]) == ("Kirje liik", "Arhiiv (avatud ja suletud)")
    # The × removes the archive filter and nothing else: every status, the year kept.
    assert "olek=koik" in chip["remove_query"] and "aasta=2026" in chip["remove_query"]
    body = response.content.decode()
    assert "Arhiiv (avatud ja suletud)" in body
    # No tab claims to be active for it, and the panel keeps it on submit.
    assert (
        'aria-current="true"'
        not in body.split('aria-label="Kirje seis"', 1)[1].split("</nav>", 1)[0]
    )
    assert '<input type="hidden" name="olek" value="arhiiv">' in body


def test_the_live_search_fragment_keeps_the_chip(signed_in):
    response = signed_in.get(
        REGISTER, {"olek": "arhiiv", "q": "arhiivirida"}, HTTP_HX_REQUEST="true"
    )

    assert "Arhiiv (avatud ja suletud)" in response.content.decode()


def test_the_archive_badge_is_left_out_only_beside_suletud(signed_in):
    body = signed_in.get(REGISTER, {"olek": "koik"}).content.decode()

    def flags(title: str) -> str:
        row = body.split(f'title="{title}"', 1)[1].split("</td>", 1)[0]
        return " ".join(re.findall(r'class="badge badge--(\w+)"', row))

    assert flags("Avatud arhiivirida") == "archive"
    assert flags("Suletud arhiivirida") == "closed"
    assert flags("Suletud täielik") == "closed"
    assert flags("Avatud täielik") == ""


def test_the_record_mode_is_untouched(signed_in, world):
    signed_in.get(REGISTER, {"olek": "arhiiv"})

    world["closed_archive"].refresh_from_db()
    assert world["closed_archive"].record_mode == RecordMode.ARCHIVE
    assert world["closed_archive"].is_open is False
