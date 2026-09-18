"""Every `aria-describedby` on a rendered page points at an element that exists.

Django's `BoundField` puts `aria-describedby="<auto_id>_helptext"` on the widget
of **every** field that declares `help_text`, whether or not a template renders
the sentence — and these forms are rendered field by field, by hand, because
each surface has its own layout. So a field could declare help text that nothing
printed, and the control would announce a description that was not in the
document. A screen reader follows the pointer, finds nothing, and reads the box
with no description at all; the defect is invisible to everybody else, including
to a test that asserts the page returned 200.

`published_on` on the `Ülevaade / uudis` panel is where this was found, and it
is the case §B pins down in detail. The scan in §A is the general rule, because
six more fields across five templates had the same shape and none of them was
doing anything unusual:

    site_path · for_information · joint_submitters · prepare_by ·
    brief_summary · visibility

The static half of the contract — help text printed into an element that
carries no id — is in `tests/test_ui_contract.py`, which needs no database.
This module is the half that only a rendered response can answer: help text that
is not printed **anywhere**.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from app.matters.services import plan_website_overview

pytestmark = pytest.mark.django_db

#: Every id an element claims, and every id an `aria-describedby` asks for.
ID_ATTR = re.compile(r'\bid="([^"\s]+)"')
DESCRIBED_BY = re.compile(r'\baria-describedby="([^"]+)"')


def described_ids(html: str) -> list[str]:
    """Every whitespace-separated id any control on this page is described by."""
    return [
        value for match in DESCRIBED_BY.finditer(html) for value in match.group(1).split() if value
    ]


def assert_every_description_resolves(html: str, where: str) -> None:
    """The invariant, stated once.

    Exactly once rather than at least once: two elements sharing an id is the
    same defect read from the other end — the browser resolves one of them and
    nobody chose which.
    """
    ids = ID_ATTR.findall(html)
    for wanted in described_ids(html):
        found = ids.count(wanted)
        assert found == 1, (
            f"{where}: a control is aria-describedby={wanted!r}, and the page holds "
            f"{found} elements with that id. A description that is not in the "
            f"document is read as no description at all."
        )


# ===========================================================================
# A — the general rule, on the surfaces that render forms by hand
# ===========================================================================


def test_the_matter_page_describes_nothing_it_does_not_render(signed_in, normal_matter):
    """The `Ülevaade / uudis` panel's own page, and the defect's home."""
    response = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}))
    assert response.status_code == 200
    assert_every_description_resolves(response.content.decode(), "matter detail")


def test_the_documents_tab_describes_nothing_it_does_not_render(signed_in, normal_matter):
    """`site_path`, and the two opinion forms' `for_information` / `joint_submitters`."""
    response = signed_in.get(reverse("matters:matter_documents", kwargs={"pk": normal_matter.pk}))
    assert response.status_code == 200
    assert_every_description_resolves(response.content.decode(), "matter documents")


def test_the_create_form_describes_nothing_it_does_not_render(signed_in):
    """`prepare_by`, on the opinion-action panel `Uus teema` carries."""
    response = signed_in.get(reverse("matters:matter_create"))
    assert response.status_code == 200
    assert_every_description_resolves(response.content.decode(), "uus teema")


def test_the_edit_form_describes_nothing_it_does_not_render(signed_in, normal_matter):
    """`brief_summary`, whose help text no template printed at all."""
    response = signed_in.get(reverse("matters:matter_edit", kwargs={"pk": normal_matter.pk}))
    assert response.status_code == 200
    assert_every_description_resolves(response.content.decode(), "matter edit")


def test_the_intake_form_describes_nothing_it_does_not_render(signed_in):
    """`visibility`, where a restricted letter is first filed."""
    response = signed_in.get(reverse("matters:intake"))
    assert response.status_code == 200
    assert_every_description_resolves(response.content.decode(), "saabunud/lisa")


def test_the_planned_rows_publish_form_describes_nothing_it_does_not_render(
    signed_in, normal_matter, specialist
):
    """The second `published_on` box: a planned row's own publish form.

    Rendered only when the Matter has a planned `Ülevaade / uudis`, which is
    exactly why the first pass at this missed it — the panel above is on every
    Matter page and this one is on none of them until somebody plans a write-up.
    """
    plan_website_overview(matter=normal_matter, actor=specialist)
    response = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}))
    assert response.status_code == 200
    html = response.content.decode()
    assert "Lisa link ja avaldamiskuupäev" in html, "the planned row's publish form is not rendered"
    assert_every_description_resolves(html, "planned overview publish form")


# ===========================================================================
# B — `published_on`, named rather than only covered by the scan
# ===========================================================================


def test_the_publication_date_is_described_by_its_own_help_text(signed_in, normal_matter):
    """The reported defect, as the four facts that make it a defect.

    The control exists, it says it is described, the description is there, and
    it is there once. Asserted separately from the scan above so that a failure
    reads as what it is rather than as "some id on the Matter page".
    """
    response = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}))
    html = response.content.decode()

    control = re.search(r"<input[^>]*\bid=\"id_published_on\"[^>]*>", html)
    assert control, "the Ülevaade / uudis panel renders no #id_published_on"
    assert "id_published_on_helptext" in described_ids(control.group(0)), (
        "the publication date box no longer says it is described by its help text"
    )
    assert html.count('id="id_published_on_helptext"') == 1, (
        "the description the box points at is missing, or rendered twice"
    )


def test_the_description_says_an_unknown_date_may_be_left_empty(signed_in, normal_matter):
    """*What* it says, not only that something is there.

    The meaning is the rule docs/adr/0089 §8 settled — an empty box is a valid
    answer and means the day is unknown — and it is the one sentence somebody
    filling this in has to be able to read. Matched on the phrase rather than on
    the whole string, so rewording the sentence does not fail this while
    removing the meaning does.
    """
    response = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}))
    html = response.content.decode()
    described = re.search(r'id="id_published_on_helptext"[^>]*>(.*?)</span>', html, re.S)
    assert described, "no element carries id_published_on_helptext"
    assert "kuupäev ei ole teada" in described.group(1), (
        f"the accessible description no longer carries the meaning: {described.group(1)!r}"
    )


def test_the_panel_does_not_say_the_same_thing_twice(signed_in, normal_matter):
    """One instruction, one place.

    The sentence used to be prose beside the fieldset while the box pointed at
    nothing. Moving it to the field is only an improvement if the prose goes:
    otherwise a screen reader reads the instruction once as the box's
    description and again as the paragraph under it.
    """
    response = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}))
    html = response.content.decode()
    assert html.count("jäta tühjaks") == 1, (
        "the publication date's instruction is on the panel more than once"
    )
    assert "Avaldamise kuupäev on valikuline" not in html, (
        "the duplicated panel sentence is back beside the field-level help text"
    )


def test_the_publication_date_is_still_optional_and_still_empty(signed_in, normal_matter):
    """The fix is an association, and changes nothing about the control.

    Named because the two things most easily broken while attaching a
    description are the two docs/adr/0089 §8 settled: the box is not required,
    and nothing pre-fills it with today.
    """
    response = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}))
    control = re.search(r"<input[^>]*\bid=\"id_published_on\"[^>]*>", response.content.decode())
    assert control
    assert "required" not in control.group(0), "the publication date became required"
    assert not re.search(r'\bvalue="[^"]+"', control.group(0)), (
        "the publication date box opens with a value in it"
    )
