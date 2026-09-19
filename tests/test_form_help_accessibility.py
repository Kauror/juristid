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
from django.utils import timezone

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


def test_the_composer_date_box_claims_no_description_it_does_not_have(signed_in, normal_matter):
    """The reported defect's control, after the sentence it pointed at was retired.

    The defect was a box saying `aria-describedby="id_published_on_helptext"`
    about an element that did not exist: a screen reader followed the pointer,
    found nothing, and read the control with no description at all while a
    sighted reader got the sentence.

    docs/adr/0095 §5 removes the sentence, because the panel no longer has a
    conditional to explain. That closes the defect the other way round — there is
    nothing to point at and nothing claiming to — and this is the assertion that
    says so, on the one control the defect was reported on. The page-wide
    invariant above covers every other control on it.
    """
    response = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}))
    html = response.content.decode()

    control = re.search(r"<input[^>]*\bid=\"id_published_on\"[^>]*>", html)
    assert control, "the Ülevaade / uudis panel renders no #id_published_on"
    assert "id_published_on_helptext" not in described_ids(control.group(0)), (
        "the composer's date box points at a help text the panel no longer renders"
    )
    assert 'id="id_published_on_helptext"' not in html, "the retired help text is back on the page"


def test_the_composer_panel_carries_no_date_instruction_at_all(signed_in, normal_matter):
    """One instruction, one place — and here, no instruction.

    The sentence used to be prose beside the fieldset while the box pointed at
    nothing; the fix moved it onto the field; docs/adr/0095 §5 retires it along
    with the conditional it explained. What must not come back is either copy.

    The *publish* form on a stored planned row keeps its own help text and its
    own empty box: publishing a plan is a different act and docs/adr/0089 §8
    still governs it. That form is asserted separately above, which is why this
    test reads a Matter with nothing planned.
    """
    response = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}))
    html = response.content.decode()

    assert "jäta tühjaks" not in html, (
        "the retired publication-date instruction is back on the composer panel"
    )
    assert "Avaldamise kuupäev on valikuline" not in html, (
        "the duplicated panel sentence is back beside the field"
    )


def test_the_composer_date_box_is_optional_and_opens_on_today(signed_in, normal_matter):
    """The two properties most easily broken while changing a description.

    docs/adr/0089 §8 settled that the box is not required and that nothing
    *reacts* to a paste by filling it. docs/adr/0095 §5 adds a server-side
    `initial` and changes neither: the day is in the box before anything is
    typed, where it can be read and cleared, and an emptied box still stores
    `NULL`.
    """
    today = timezone.localdate()
    response = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}))
    html = response.content.decode()
    control = re.search(r"<input[^>]*\bid=\"id_published_on\"[^>]*>", html)

    assert control
    assert "required" not in control.group(0), "the publication date became required"
    assert f'value="{today.day}.{today.month}.{today.year}"' in control.group(0), (
        f"the composer's date box does not open on today: {control.group(0)!r}"
    )
    assert "data-publication-default" not in html, "the withdrawn paste-triggered island is back"
