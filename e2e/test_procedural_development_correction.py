"""Correcting a filed `Menetluse areng`, in a real browser (QA-06).

The Python suite proves the rule at the service and at the route: every
substantive field round-trips, a period reopens at the precision it was stored
at, an emptied box stores nothing, a stale form is refused, a closed Teema takes
no correction and the stage and next step the original save wrote are left where
they are. What only a browser can show is the half that is htmx.

That `Muuda` on a chronology row turns *that row* into a form with the record
already in it — the difference between correcting a sentence and retyping a
procedural step. That saving puts the corrected row back where it was rather than
adding a second one underneath, and without a full-page navigation that loses
where the reader was. That `Juristi märkus` still arrives under its own label
afterwards, which is QA-05 held through a surface that did not exist when QA-05
shipped. And that the correction survives a reload, so what was shown is what
was stored.

Everything here is synthetic, and every test files its own Matter. The seeded
world is shared across a shard and never reset between files
(`e2e/conftest.py`), so a test that corrected a seeded development would be
rewriting a row `e2e/test_ui_regression.py` photographs.
"""

from __future__ import annotations

import datetime as dt

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    MARTIN,
    create_matter,
    open_add_panel,
    sign_in,
    unique_title,
)

pytestmark = pytest.mark.e2e

HEADLINE = "Ministeerium saatis uue eelnõu versiooni"
CORRECTED = "Komisjon arutas eelnõu"
NOTE = "Versioon ei arvesta meie varasemat ettepanekut."
CORRECTED_NOTE = "Uus versioon arvestab meie ettepanekut osaliselt."
NOTE_LABEL = "Juristi märkus"
DATE_UNKNOWN = "Kuupäev teadmata"

#: A day that has already gone by, computed against the clock: a chronology row
#: only renders once its day has arrived, and a `Menetluse areng` may not be
#: dated into the future at all (QA-07).
HAPPENED = (dt.date.today() - dt.timedelta(days=3)).strftime("%d.%m.%Y")
HAPPENED_READ = (dt.date.today() - dt.timedelta(days=3)).strftime("%-d.%-m.%Y")
MOVED = (dt.date.today() - dt.timedelta(days=20)).strftime("%d.%m.%Y")
MOVED_READ = (dt.date.today() - dt.timedelta(days=20)).strftime("%-d.%-m.%Y")


def _file_a_development(page, *, occurred_on: str = HAPPENED) -> None:
    """Record one procedural step through the real `+ Märge · Tavaline` panel.

    `+ Menetluse areng` filed these until docs/adr/0097 §6 and posted to
    `/lisa/menetluse-areng/`. The panel is retired and the record is not: the
    ordinary note writes the same `MatterProceduralDevelopment`, through
    `/lisa/marge/`, and this file is about correcting one.

    **No `Juristi märkus` here**, because the panel does not ask for one any
    more (§6.2). The editor still offers the box on a stored row — which is
    what the correction tests below use it for, and is the stronger shape of
    the same claim: a note the *correction* surface adds is a note the reader
    sees attributed to this office.
    """
    open_add_panel(page, "marge-tavaline")
    page.locator("#marge-tavaline input[name=title]").fill(HEADLINE)
    page.locator("#marge-tavaline input[name=occurred_on]").fill(occurred_on)
    with page.expect_response(
        lambda response: "/lisa/marge/" in response.url and response.request.method == "POST"
    ) as caught:
        page.locator("#marge-tavaline button[type=submit]").click()
    assert caught.value.status == 200, f"the development was refused: {caught.value.status}"
    page.wait_for_load_state("networkidle")


def _row(page):
    """The one `Menetluse areng` row on a freshly filed Matter."""
    row = page.locator(".uxtl__ms-body").first
    row.wait_for()
    return row


def _open_the_editor(page):
    _row(page).get_by_role("button", name="Muuda", exact=False).click()
    form = page.locator(".uxtl__editform")
    form.wait_for()
    return form


def _save(page):
    with page.expect_response(
        lambda response: (
            "/menetluse-areng/" in response.url
            and response.url.endswith("/muuda/")
            and response.request.method == "POST"
        )
    ) as caught:
        page.locator(".uxtl__editform button[type=submit]").click()
    return caught.value


def test_a_filed_menetluse_areng_can_be_corrected_in_place(page, base_url):
    """§12.T. The whole feature, end to end, on an open Teema.

    The chronology offers `Muuda`, the row becomes the form, saving puts the
    corrected substantive record back in the same row, and nothing about the page
    around it moves.
    """
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Menetluse arengu paranduse brauserikatse"))
    _file_a_development(page)

    expect(page.locator(".uxtl__ms-body")).to_have_count(1)
    expect(_row(page)).to_contain_text(f"Märge: {HEADLINE}")
    # QA-05, before the correction: no `Juristi märkus` line, because the panel
    # that filed this one does not ask for one (docs/adr/0097 §6.2).
    expect(_row(page)).not_to_contain_text(NOTE_LABEL)

    form = _open_the_editor(page)

    # Prefilled, every box of it. Retyping a procedural step to fix its sentence
    # is the thing this avoids — and the note box is offered, empty, because
    # this is the surface that decides per record.
    assert form.locator("input[name=title]").input_value() == HEADLINE
    assert form.locator("input[name=occurred_on]").input_value() == HAPPENED_READ
    assert form.locator("textarea[name=note]").input_value() == ""
    # And it is holding the version it was filled from.
    assert form.locator("input[name=revision]").input_value()
    # The editor asks about this record and nothing beside it: no `Uus
    # hetkeseis`, no `Järgmiseks`, no attachment control.
    expect(form.locator("select[name=stage]")).to_have_count(0)
    expect(form.locator("input[name=next_text]")).to_have_count(0)
    expect(form.locator("input[type=file]")).to_have_count(0)

    form.locator("input[name=title]").fill(CORRECTED)
    form.locator("textarea[name=note]").fill(CORRECTED_NOTE)
    form.locator("input[name=occurred_on]").fill(MOVED)
    saved = _save(page)
    assert saved.status == 200, f"the correction was refused: {saved.status}"
    page.wait_for_load_state("networkidle")

    # Same row, corrected, and no second line underneath it. No full-page
    # navigation either — the reader is still where they were.
    row = _row(page)
    expect(row).to_contain_text(f"Märge: {CORRECTED}")
    expect(row).to_contain_text(MOVED_READ)
    expect(page.locator(".uxtl__ms-body")).to_have_count(1)
    expect(page.locator(".uxtl__editform")).to_have_count(0)
    # QA-05 after the correction: its own labelled line, attributed to this
    # office and not to the ministry named in the headline.
    expect(row).to_contain_text(NOTE_LABEL)
    expect(row).to_contain_text(CORRECTED_NOTE)

    # What was shown is what was stored.
    page.reload()
    page.wait_for_load_state("networkidle")
    expect(_row(page)).to_contain_text(f"Märge: {CORRECTED}")
    expect(_row(page)).to_contain_text(MOVED_READ)
    expect(_row(page)).to_contain_text(CORRECTED_NOTE)


def test_an_emptied_date_reads_as_kuupaev_teadmata(page, base_url):
    """A day the panel's default stamped can be told the truth.

    «Kuupäev teadmata» rather than the day the row happens to sit on: the
    fallback places the row and never describes it.
    """
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Menetluse arengu katse: tühi kuupäev"))
    _file_a_development(page)

    form = _open_the_editor(page)
    form.locator("input[name=occurred_on]").fill("")
    saved = _save(page)
    assert saved.status == 200, f"an emptied date was refused: {saved.status}"
    page.wait_for_load_state("networkidle")

    expect(_row(page)).to_contain_text(DATE_UNKNOWN)

    page.reload()
    page.wait_for_load_state("networkidle")
    expect(_row(page)).to_contain_text(DATE_UNKNOWN)

    # And it reopens on an empty box, never on today.
    form = _open_the_editor(page)
    assert form.locator("input[name=occurred_on]").input_value() == ""


def test_cancelling_leaves_the_row_exactly_as_it_was(page, base_url):
    """`Tühista` is a re-read, so nothing typed into an abandoned form survives."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Menetluse arengu katse: tühistamine"))
    _file_a_development(page)

    form = _open_the_editor(page)
    form.locator("input[name=title]").fill("Seda ei salvestata")
    form.get_by_role("button", name="Tühista", exact=True).click()
    page.wait_for_load_state("networkidle")

    row = _row(page)
    expect(page.locator(".uxtl__editform")).to_have_count(0)
    expect(row).to_contain_text(f"Märge: {HEADLINE}")
    expect(row).not_to_contain_text("Seda ei salvestata")


def test_a_future_date_is_refused_beside_the_control_it_was_typed_into(page, base_url):
    """QA-07, preserved through the correction surface.

    A `Menetluse areng` records something that has happened. The refusal arrives
    in the form that is still open, holding what the person typed, rather than as
    a page-level banner they have to go and find.
    """
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Menetluse arengu katse: tulevik"))
    _file_a_development(page)

    ahead = (dt.date.today() + dt.timedelta(days=30)).strftime("%d.%m.%Y")
    form = _open_the_editor(page)
    form.locator("input[name=occurred_on]").fill(ahead)
    refused = _save(page)
    assert refused.status == 400, f"a future date was accepted: {refused.status}"
    page.wait_for_load_state("networkidle")

    expect(page.locator(".uxtl__editform")).to_have_count(1)
    expect(page.locator(".uxtl__editform")).to_contain_text("ei saa olla tulevikus")
    # Nothing was written, and the form still holds what the person typed.
    assert page.locator(".uxtl__editform input[name=occurred_on]").input_value() == ahead
