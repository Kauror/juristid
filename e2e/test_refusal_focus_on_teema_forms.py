"""A refused `LISA TEEMALE` save puts the cursor on the control that was refused.

The 2026-10-17 audit (JUX-003) found the rule in `e2e/test_refusal_focus.py`
holding on `Uus teema` and failing on the Teema page. There the refusals are
printed as direct children of `form.addform`, so the container lookup in
`static/js/ux.js` `controlForError` climbed to the form itself and handed back
its *first* control: an empty Lisa tagasiside put the cursor in the
organisation search, a Muu with no day put it in a perfectly good `Kirjeldus`,
and a Kohtumine with no summary put it on the valid date. A screen-reader user
heard the name of a field that had nothing wrong with it, and nothing announced
the refusal at all.

The rule now: the refused control is found through the error's own id relation
— the control (or fieldset) whose `aria-describedby` names the message, or the
control whose id the message's `<id>_error` spells — before any container is
consulted. A refused radio group puts the cursor on its first radio. The
refusal is also announced: each message of the refused form is put back into
the page as `role="alert"`, which a screen reader reads when it is inserted.

JUX-006 is the organisation picker's half: it has a visible label, the label
says the field is required while «Avaldatud mujal» is chosen, and on that
refusal the search box — the only control a person can reach — carries
`aria-describedby` to the message and `aria-invalid`.

Every test writes to a Matter it creates.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    AVALDATUD_MUJAL,
    FEEDBACK_PICKER,
    SAADETI_KOJALE,
    SANDRA,
    create_matter,
    open_add_panel,
    open_feedback_form,
    sign_in,
    unique_title,
    wait_for_htmx,
)

pytestmark = pytest.mark.e2e


def _et(on: date) -> str:
    return f"{on.day}.{on.month}.{on.year}"


@pytest.fixture
def matter(page, base_url):
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Fookus pärast tagasilükkamist"))


def _save(page, form, path: str) -> None:
    with page.expect_response(
        lambda r: r.url.endswith(path) and r.request.method == "POST"
    ) as answer:
        form.get_by_role("button", name="Salvesta", exact=True).click()
    wait_for_htmx(page)
    assert answer.value.status == 400


def _announced(form, text: str) -> None:
    """The message is an alert: one element, read when it is put in the page."""
    alert = form.locator(".field__error", has_text=text)
    expect(alert).to_have_count(1)
    expect(alert).to_have_attribute("role", "alert")


# ---- Lisa tagasiside ---------------------------------------------------------

TAGASISIDE = "/lisa/kaasamise-tagasiside/"


def _tagasiside(page):
    return page.locator("#kaasamine-tagasiside form")


def test_tagasiside_without_a_provenance_focuses_its_first_radio(page, matter):
    form = open_feedback_form(page)
    form.locator("[name=summary]").fill("Seisukoht, millel puudub päritolu.")
    _save(page, form, TAGASISIDE)

    form = _tagasiside(page)
    message = "Vali, kas seisukoht saadeti Kojale või avaldati mujal."
    expect(form).to_contain_text(message)
    expect(form.locator('input[name="provenance"]').first).to_be_focused()
    _announced(form, message)


def test_an_empty_tagasiside_focuses_the_provenance_not_the_organisation(page, matter):
    form = open_feedback_form(page)
    _save(page, form, TAGASISIDE)

    form = _tagasiside(page)
    expect(form.locator('input[name="provenance"]').first).to_be_focused()
    expect(page.locator(f"#{FEEDBACK_PICKER}-otsi")).not_to_be_focused()


def test_a_bad_tagasiside_link_focuses_the_link(page, matter):
    form = open_feedback_form(page, SAADETI_KOJALE)
    form.locator("[name=summary]").fill("Seisukoht, mille link on vigane.")
    form.locator("[name=url]").fill("see ei ole aadress")
    _save(page, form, TAGASISIDE)

    form = _tagasiside(page)
    expect(form.locator(".field__error")).to_have_count(1)
    expect(form.locator("[name=url]")).to_be_focused()


def test_avaldatud_mujal_without_an_organisation_focuses_and_describes_the_search(page, matter):
    form = open_feedback_form(page, AVALDATUD_MUJAL)
    form.locator("[name=summary]").fill("Avaldatud seisukoht, kelle oma, ei ole öeldud.")
    _save(page, form, TAGASISIDE)

    form = _tagasiside(page)
    search = page.locator(f"#{FEEDBACK_PICKER}-otsi")
    error = form.locator(f"#{FEEDBACK_PICKER}-valik .field__error")
    expect(error).to_have_count(1)
    error_id = error.get_attribute("id")
    assert error_id

    expect(search).to_be_focused()
    described = (search.get_attribute("aria-describedby") or "").split()
    assert error_id in described, described
    expect(search).to_have_attribute("aria-invalid", "true")
    _announced(form, error.inner_text().strip())


def test_the_organisation_search_has_a_visible_label_required_only_when_published(page, matter):
    form = open_feedback_form(page)
    label = form.locator(f'label[for="{FEEDBACK_PICKER}-otsi"]')
    expect(label).to_be_visible()
    expect(label).to_contain_text("Organisatsioon")
    marker = label.locator(".req-mark")
    expect(marker).to_be_hidden()

    form.locator(f'label.chip:has(input[name="provenance"][value="{AVALDATUD_MUJAL}"])').click()
    expect(marker).to_be_visible()
    expect(page.locator(f"#{FEEDBACK_PICKER}-otsi")).to_have_accessible_name(
        re.compile(r"^Organisatsioon \(kohustuslik\)")
    )

    form.locator(f'label.chip:has(input[name="provenance"][value="{SAADETI_KOJALE}"])').click()
    expect(marker).to_be_hidden()


# ---- Muu -----------------------------------------------------------------------

MUU = "/lisa/muu/"


def _muu(page):
    return page.locator("#marge-muu form")


def test_muu_without_a_day_focuses_the_day_not_the_description(page, matter):
    open_add_panel(page, "marge-muu")
    form = _muu(page)
    form.locator("[name=description]").fill("Materjal, mille päev on puudu.")
    # The box opens at today (docs/adr/0172 §4); a person who clears it is refused.
    form.locator("[name=occurred_on]").fill("")
    _save(page, form, MUU)

    form = _muu(page)
    expect(form).to_contain_text("Sisesta, millal see toimus.")
    expect(form.locator("[name=occurred_on]")).to_be_focused()
    _announced(form, "Sisesta, millal see toimus.")


def test_a_bad_muu_link_focuses_the_link(page, matter):
    open_add_panel(page, "marge-muu")
    form = _muu(page)
    form.locator("[name=description]").fill("Materjal, mille link on vigane.")
    form.locator("[name=occurred_on]").fill(_et(date.today() - timedelta(days=3)))
    form.locator("[name=url]").fill("see ei ole aadress")
    _save(page, form, MUU)

    form = _muu(page)
    expect(form.locator(".field__error")).to_have_count(1)
    expect(form.locator("[name=url]")).to_be_focused()


# ---- Kohtumine -----------------------------------------------------------------

KOHTUMINE = "/lisa/kohtumine/"


def test_kohtumine_without_a_summary_focuses_the_summary_not_the_date(page, matter):
    open_add_panel(page, "arvamus-kohtumine")
    form = page.locator("#arvamus-kohtumine form")
    form.locator("[name=occurred_on]").fill(_et(date.today() - timedelta(days=2)))
    _save(page, form, KOHTUMINE)

    form = page.locator("#arvamus-kohtumine form")
    expect(form.locator(".field__error")).to_have_count(1)
    expect(form.locator("[name=summary]")).to_be_focused()
