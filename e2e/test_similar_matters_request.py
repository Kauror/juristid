"""What `Sarnased teemad` sends while `Uus teema` is being filled in, measured on the wire.

Two defects in one request (ENG-026, ENG-090):

* it was a GET of the whole form, so the private `Märkmed` and the CSRF token
  rode in the address — into every access log and proxy on the way — and a
  long `Lühikokkuvõte` made the address longer than the server accepts;
* three of its five triggers named ids no element carries, so picking a
  Valdkond, an Õigusakt or a Saatja asked nothing and the section went on
  answering the form as it was before the pick.

So this watches the requests themselves: the method, the address, the body,
how many, and after which action. What the engine answers is
`tests/test_similar_matters.py`'s business.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from e2e.conftest import MARTIN, sign_in

pytestmark = pytest.mark.e2e

CREATE_PATH = "/teemad/uus/"
DRAFT_PATH = "/teemad/uus/sarnased/"
ALLOWED = {
    "title",
    "brief_summary",
    "policy_areas",
    "legal_instruments",
    "source_organisations",
    "csrfmiddlewaretoken",
}
PRIVATE = "PRIVAATNE MÄRKUS ÄRA SAADA"
MINISTRY = "Näidisministeerium"


def _requests(page) -> list:
    seen: list = []
    page.on("request", lambda request: seen.append(request) if DRAFT_PATH in request.url else None)
    return seen


def _settle(page, ms: int = 900) -> None:
    """Past the longest debounce, then let the request land."""
    page.wait_for_timeout(ms)
    page.wait_for_load_state("networkidle")


def _body(request) -> dict[str, list[str]]:
    return parse_qs(request.post_data or "", keep_blank_values=True)


def _create_form(page, base_url) -> None:
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}{CREATE_PATH}")
    page.wait_for_load_state("networkidle")
    # The private note is filled first, so every request below is sent with
    # it on the page and has to leave it there.
    page.fill("#id_notes", PRIVATE)
    _settle(page)


def test_the_request_is_a_post_carrying_only_the_deciding_fields(page, base_url):
    _create_form(page, base_url)
    seen = _requests(page)

    page.fill("#id_title", "Pakendiseaduse muutmise eelnõu")
    _settle(page)

    assert len(seen) == 1, [request.url for request in seen]
    request = seen[0]
    assert request.method == "POST"
    assert urlsplit(request.url).query == ""
    body = _body(request)
    assert set(body) <= ALLOWED, set(body) - ALLOWED
    assert body["title"] == ["Pakendiseaduse muutmise eelnõu"]
    assert "notes" not in body
    assert "PRIVAATNE" not in (request.post_data or "")
    assert "csrfmiddlewaretoken" not in request.url


def test_typing_a_private_note_asks_nothing(page, base_url):
    _create_form(page, base_url)
    seen = _requests(page)

    page.fill("#id_notes", "")
    page.locator("#id_notes").type("Veel üks privaatne mõte", delay=10)
    page.locator("#id_title").focus()  # a `change` on the note, too
    _settle(page)

    assert seen == []


def test_the_summary_asks(page, base_url):
    _create_form(page, base_url)
    seen = _requests(page)

    page.fill("#id_brief_summary", "Pakendiettevõtjate aruandlus muutub")
    _settle(page)

    assert len(seen) == 1
    assert _body(seen[0])["brief_summary"] == ["Pakendiettevõtjate aruandlus muutub"]


@pytest.mark.parametrize("name", ["policy_areas", "legal_instruments"])
def test_a_chip_asks_and_sends_what_it_chose(page, base_url, name):
    _create_form(page, base_url)
    seen = _requests(page)

    chip = page.locator(f"label.chip:has(input[name='{name}'])").first
    value = chip.locator("input").get_attribute("value")
    chip.click()
    _settle(page)

    assert len(seen) == 1, f"picking a {name} chip asked {len(seen)} times"
    assert _body(seen[0]).get(name) == [value]


def test_a_sender_asks_and_sends_what_it_chose(page, base_url):
    _create_form(page, base_url)
    seen = _requests(page)

    box = page.locator("#saatja-otsi")
    box.click()
    box.fill("Näidismin")
    page.locator("#saatja-tulemused").get_by_role("option", name=MINISTRY, exact=True).click()
    _settle(page)

    assert seen, "choosing a Saatja asked nothing"
    assert _body(seen[-1]).get("source_organisations"), _body(seen[-1])


def test_a_burst_of_ticks_asks_once(page, base_url):
    _create_form(page, base_url)
    seen = _requests(page)

    chips = page.locator("label.chip:has(input[name='policy_areas'])")
    for index in range(3):
        chips.nth(index).click()
    _settle(page)

    assert len(seen) == 1, f"three quick ticks asked {len(seen)} times"
    assert len(_body(seen[0])["policy_areas"]) == 3


def test_a_summary_longer_than_any_address_is_answered(page, base_url):
    _create_form(page, base_url)
    statuses: list[int] = []
    page.on(
        "response",
        lambda response: statuses.append(response.status) if DRAFT_PATH in response.url else None,
    )

    page.locator("#id_brief_summary").fill("Pakendiettevõtjate aruandlus. " * 300)
    _settle(page)

    assert statuses == [200]
