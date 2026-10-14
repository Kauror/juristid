"""QA remediation 2026-10-14, agent I: what the QA probes proved only in part.

* **Digits** (QA-SUBDOCS-01): the shared reader itself, and the one call site
  the QA probes did not reach (the Statistika matrix list's year).
* **Header fragments** (QA-BROWSER-01, -02, -07): the tab a header answer marks
  when it is sent from `Dokumendid`, the shape of the header stage answer, and
  the ⋯ menu a column save now sends.
* **Accessible names** (QA-BROWSER-06): Minu asjad's row menus.
* **First names** (owner decision «Marko»): a colleague named in passing reads
  by first name — `Marko`, not `Marko Udras` beside `Ireen` and `Sandra` — and
  a first name two colleagues share is still told apart.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.accounts.naming import disambiguated_names
from app.core.request_params import bounded_int, digits_or_none
from app.matters.services import create_matter, matter_field_revision
from app.workflow.enums import ActionKind, DateSemantics
from app.workflow.models import StageVocabulary
from tests import factories
from tests.factories import NextActionFactory

pytestmark = pytest.mark.django_db


def stage(key: str) -> StageVocabulary:
    return StageVocabulary.objects.get(key=key)


# ---------------------------------------------------------------------------
# Digits
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12", 12),
        (" 12 ", 12),
        ("0", 0),
        ("²", None),  # superscript two: `isdigit()` says yes, `int()` raises
        ("١٢", None),  # Arabic-Indic twelve: `int()` would read it
        ("1_0", None),  # `int()` reads underscores
        ("+5", None),
        ("-1", None),
        ("1e3", None),
        ("", None),
        (None, None),
        ("1\x002", None),
        ("9" * 3000, None),
    ],
)
def test_digits_or_none_reads_ascii_digits_only(raw, expected):
    assert digits_or_none(raw) == expected


def test_digits_or_none_keeps_to_its_range():
    assert digits_or_none("12", minimum=1, maximum=12) == 12
    assert digits_or_none("13", minimum=1, maximum=12) is None
    assert digits_or_none("0", minimum=1, maximum=12) is None


def test_bounded_int_answers_its_default_for_a_non_ascii_digit():
    assert bounded_int("²", default=7) == 7
    assert bounded_int("١٢", default=7) == 7
    # A caller with a negative floor still gets its negative numbers.
    assert bounded_int("-3", default=0, minimum=-5, maximum=5) == -3
    assert bounded_int("-6", default=0, minimum=-5, maximum=5) == 0


def test_the_statistics_matrix_list_refuses_a_superscript_year_with_a_404(client, specialist):
    client.force_login(specialist)
    client.raise_request_exception = False

    response = client.get(
        reverse("reporting:metric_rows"),
        {"naitaja": "EU_RELATED_MATTERS_BY_YEAR", "ruhm": "el", "aasta": "²"},
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Header fragments
# ---------------------------------------------------------------------------


def _tabs(body: str) -> str:
    return body.split('id="teema-vaated"', 1)[1].split("</nav>", 1)[0]


def _stage_post(client, matter, key: str, **headers):
    return client.post(
        reverse("matters:update_field", kwargs={"pk": matter.pk, "field": "stage"}),
        {"stage": str(stage(key).pk), "revision": matter_field_revision(matter, "stage")},
        HTTP_HX_REQUEST="true",
        **headers,
    )


def test_a_header_edit_sent_from_dokumendid_marks_dokumendid(signed_in, specialist):
    matter = create_matter(title="Päis dokumentidest", actor=specialist, owner=specialist)
    documents = reverse("matters:matter_documents", kwargs={"pk": matter.pk})

    response = _stage_post(
        signed_in, matter, "consultation", HTTP_HX_CURRENT_URL=f"http://testserver{documents}"
    )

    assert response.status_code == 200
    body = response.content.decode()
    # The header alone: `Dokumendid` has no column to answer into.
    assert "HX-Retarget" not in response
    assert 'id="teema-vaade"' not in body
    tabs = _tabs(body)
    dokumendid = tabs.split("Dokumendid", 1)[0].rsplit("<a", 1)[1]
    assert 'aria-current="page"' in dokumendid
    assert tabs.count('aria-current="page"') == 1


def test_a_header_stage_change_on_teema_answers_with_the_column_and_the_header(
    signed_in, specialist
):
    matter = create_matter(title="Päis ja veerg", actor=specialist, owner=specialist)

    response = _stage_post(signed_in, matter, "consultation")

    assert response.status_code == 200
    assert response["HX-Retarget"] == "#teema-vaade"
    assert response["HX-Reswap"] == "outerHTML"
    body = response.content.decode()
    assert 'id="teema-vaade"' in body.split('id="teema-pais"', 1)[0]
    assert re.search(r'<header class="matterhead" id="teema-pais" hx-swap-oob="true"', body)
    assert _tabs(body).count('aria-current="page"') == 1


def test_a_column_save_sends_the_header_menu_once_the_matter_has_a_file(signed_in, specialist):
    """QA-BROWSER-07: the ⋯ menu arrives with the save that brought the first file."""
    matter = create_matter(title="Esimene fail", actor=specialist, owner=specialist)
    factories.DocumentFactory(matter=matter, created_by=specialist)

    response = signed_in.post(
        reverse("matters:add_note", kwargs={"pk": matter.pk}),
        {"stage": str(stage("consultation").pk)},
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    body = response.content.decode()
    menu = body.split('id="teema-menuu" hx-swap-oob="true"', 1)
    assert len(menu) == 2, "the column save did not send the ⋯ menu"
    assert "Rohkem tegevusi" in menu[1].split("</details>", 1)[0]


def test_the_header_menu_slot_is_drawn_empty_before_the_first_file(signed_in, specialist):
    matter = create_matter(title="Failita", actor=specialist, owner=specialist)

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()

    slot = body.split('id="teema-menuu"', 1)[1].split("</span>", 1)[0]
    assert "Rohkem tegevusi" not in slot


# ---------------------------------------------------------------------------
# Accessible names
# ---------------------------------------------------------------------------


def test_minu_asjad_row_menus_are_named_for_their_teema(signed_in, specialist):
    for title in ("Esimene rida", "Teine rida"):
        matter = create_matter(title=title, actor=specialist, owner=specialist)
        NextActionFactory(
            matter=matter,
            kind=ActionKind.DO,
            date_semantics=DateSemantics.DEADLINE,
            target_date=timezone.localdate() + timedelta(days=3),
            responsible=specialist,
        )

    body = signed_in.get(reverse("matters:my_work")).content.decode()
    names = re.findall(r'class="rowmenu__trigger" aria-label="([^"]+)"', body)

    assert "Rea tegevused: Esimene rida" in names
    assert "Rea tegevused: Teine rida" in names
    assert "Rea tegevused" not in names


# ---------------------------------------------------------------------------
# First names
# ---------------------------------------------------------------------------


@pytest.fixture
def department(db):
    """The production department's shape: two one-word names and one full one."""
    return {
        "marko": factories.DepartmentHeadFactory(
            upn="marko.rem-i@example.invalid", display_name="Marko Udras"
        ),
        "ireen": factories.UserFactory(upn="ireen.rem-i@example.invalid", display_name="Ireen"),
        "sandra": factories.UserFactory(upn="sandra.rem-i@example.invalid", display_name="Sandra"),
    }


@pytest.mark.parametrize(
    "display_name", ["Marko Udras", "Marko Udras", " Marko Udras", "Marko  Udras"]
)
def test_the_short_name_is_the_first_word_however_the_name_is_spaced(display_name):
    person = factories.UserFactory.build(upn="marko@example.invalid", display_name=display_name)
    assert person.get_short_name() == "Marko"


def test_a_first_name_two_colleagues_share_is_still_told_apart():
    people = [
        factories.UserFactory.build(pk=1, upn="a@example.invalid", display_name="Marko Udras"),
        factories.UserFactory.build(pk=2, upn="b@example.invalid", display_name="Marko Tamm"),
        factories.UserFactory.build(pk=3, upn="c@example.invalid", display_name="Ireen"),
    ]

    labels = disambiguated_names(people)

    assert labels == {1: "Marko Udras", 2: "Marko Tamm", 3: "Ireen"}


def test_osakond_names_the_team_by_first_name(client, department):
    client.force_login(department["marko"])

    body = client.get(reverse("matters:department")).content.decode()
    names = [
        re.sub(r"<[^>]+>.*", "", name).strip()
        for name in re.findall(r'class="uxteam__name[^"]*"[^>]*>\s*(.*?)</span>', body, re.S)
    ]

    assert "Marko" in names
    assert "Marko Udras" not in names
    # The whole name is still reachable from the row.
    assert 'title="Marko Udras"' in body


def test_osakond_keeps_a_shared_first_name_apart(client, department):
    factories.UserFactory(upn="marko.tamm.rem-i@example.invalid", display_name="Marko Tamm")
    client.force_login(department["marko"])

    body = client.get(reverse("matters:department")).content.decode()

    assert re.search(r'class="uxteam__name"[^>]*>\s*Marko Udras', body)
    assert re.search(r'class="uxteam__name"[^>]*>\s*Marko Tamm', body)


def test_tegevused_names_the_author_by_first_name(client, department):
    marko = department["marko"]
    matter = create_matter(title="Marko kirjutab", actor=marko, owner=marko)
    factories.EntryFactory(matter=matter, author=marko, body="<p>Kohtusime ministeeriumiga.</p>")
    client.force_login(department["ireen"])

    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()
    authors = re.findall(r'class="uxtl__author[^"]*" title="([^"]*)">([^<]*)<', body)

    assert ("Marko Udras", "Marko") in authors
    assert all(shown != "Marko Udras" for _, shown in authors)


def test_the_identity_is_untouched(department):
    marko = department["marko"]
    marko.refresh_from_db()
    assert marko.display_name == "Marko Udras"
    assert marko.upn == "marko.rem-i@example.invalid"
