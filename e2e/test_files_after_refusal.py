"""A file chosen before a refused save is never dropped in silence (JUX-001).

A refused `LISA TEEMALE` save re-renders `#teema-vaade`, and no browser lets a
page put a file back into a file input. The typed text comes back from the
server; the chosen files cannot. Before this, the drop box simply reset, nothing
said so, and the corrected save stored the record with no file at all.

Now the refused form says which files have to be chosen again, beside the drop
box and announced as a status; and a save pressed while that is still true is
stopped in the page with the same notice in focus, until the person either
chooses the files again or says to save without them (docs/adr/0107, amendment
of 2026-10-18).

Every door that takes files is covered by one script. These three are the ones
a refusal is most likely on: `Kohtumine` starts with an empty day (`Muu` a cleared one), and
`Lisa tagasiside` has a required provenance with no default.

State is read off the page only (e2e/conftest.py).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    SAADETI_KOJALE,
    SANDRA,
    chronology,
    create_matter,
    open_add_panel,
    sign_in,
    wait_for_htmx,
)
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

PDF = b"%PDF-1.4 synthetic evidence chosen before a refusal"
NOTICE = "[data-files-again]"


def _et(on: date) -> str:
    return f"{on.day}.{on.month}.{on.year}"


def _pdf(name: str) -> dict:
    return {"name": name, "mimeType": "application/pdf", "buffer": PDF + name.encode()}


@dataclass(frozen=True)
class Door:
    """One `LISA TEEMALE` form with files: how to refuse it, and how to fix it."""

    panel: str
    path: str
    refusal: str
    #: Fills everything but the one answer the server refuses the save for.
    fill_but_one: Callable
    #: Gives that answer.
    fix: Callable
    #: What the stored row is found by in `Teema käik`.
    row_text: str


def _muu_fill(form) -> None:
    form.locator('[name="description"]').fill("Muu materjal, mille fail pidi kaasa tulema.")
    # `Millal?` opens at today since docs/adr/0172 §4; the refused answer is a cleared day.
    form.locator('[name="occurred_on"]').fill("")


def _muu_fix(form) -> None:
    form.locator('[name="occurred_on"]').fill(_et(date.today() - timedelta(days=4)))


def _kohtumine_fill(form) -> None:
    form.locator('[name="summary"]').fill("Kohtumine, mille protokoll pidi kaasa tulema.")


def _kohtumine_fix(form) -> None:
    form.locator('[name="occurred_on"]').fill(_et(date.today() - timedelta(days=6)))


def _tagasiside_fill(form) -> None:
    form.locator('[name="summary"]').fill("Tagasiside, mille kiri pidi kaasa tulema.")


def _tagasiside_fix(form) -> None:
    form.locator(f'label.chip:has(input[name="provenance"][value="{SAADETI_KOJALE}"])').click()


MUU = Door(
    panel="marge-muu",
    path="/lisa/muu/",
    refusal="Sisesta, millal see toimus.",
    fill_but_one=_muu_fill,
    fix=_muu_fix,
    row_text="Muu materjal, mille fail pidi",
)
KOHTUMINE = Door(
    panel="arvamus-kohtumine",
    path="/lisa/kohtumine/",
    refusal="Sisesta kohtumise kuupäev.",
    fill_but_one=_kohtumine_fill,
    fix=_kohtumine_fix,
    row_text="Kohtumine, mille protokoll pidi",
)
LISA_TAGASISIDE = Door(
    panel="kaasamine-tagasiside",
    path="/lisa/kaasamise-tagasiside/",
    refusal="Vali, kas seisukoht saadeti Kojale või avaldati mujal.",
    fill_but_one=_tagasiside_fill,
    fix=_tagasiside_fix,
    row_text="Tagasiside, mille kiri pidi",
)


def _form(page, door: Door):
    return page.locator(f"#{door.panel} form")


def _save(page, door: Door) -> int:
    with page.expect_response(
        lambda r: r.url.endswith(door.path) and r.request.method == "POST"
    ) as answer:
        _form(page, door).get_by_role("button", name="Salvesta", exact=True).click()
    wait_for_htmx(page)
    return answer.value.status


def _refuse_with(page, door: Door, files: list[dict]):
    """Choose files, leave one answer out, save: the refused form and its notice."""
    open_add_panel(page, door.panel)
    form = _form(page, door)
    door.fill_but_one(form)
    form.locator('input[type="file"]').set_input_files(files)
    assert _save(page, door) == 400
    form = _form(page, door)
    expect(form).to_contain_text(door.refusal)
    # The browser has emptied the input: that is the defect being told.
    assert form.locator('input[type="file"]').evaluate("i => i.files.length") == 0
    return form, page.locator(f"#{door.panel} {NOTICE}")


def _press_save_and_count_posts(page, door: Door) -> int:
    posts: list[str] = []

    def watch(request) -> None:
        if request.method == "POST" and request.url.endswith(door.path):
            posts.append(request.url)

    page.on("request", watch)
    _form(page, door).get_by_role("button", name="Salvesta", exact=True).click()
    wait_for_htmx(page)
    page.remove_listener("request", watch)
    return len(posts)


def _stored_row(page, door: Door):
    page.reload()
    row = chronology(page).locator("article.uxtl__item", has_text=door.row_text)
    expect(row).to_have_count(1)
    return row


def _refused_then_chosen_again(page, base_url, door: Door) -> None:
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Fail pärast tagasilükkamist"))

    form, notice = _refuse_with(page, door, [_pdf("tagasi-lykatud-lisa.pdf")])

    # The refusal says which file is gone, beside the drop box, as a status.
    expect(notice).to_be_visible()
    expect(notice).to_have_attribute("role", "status")
    expect(notice).to_contain_text("Fail tuleb uuesti valida: tagasi-lykatud-lisa.pdf")
    expect(form.locator(f"[data-filedrop] ~ {NOTICE}")).to_have_count(1)

    # Fixing the refused answer alone is not enough: a save with the input still
    # empty is stopped in the page — nothing is posted — and the notice takes focus.
    door.fix(form)
    assert _press_save_and_count_posts(page, door) == 0
    expect(notice).to_be_focused()
    expect(notice).to_contain_text("tagasi-lykatud-lisa.pdf")
    expect(notice.get_by_role("button", name="Salvesta ilma failita")).to_be_visible()

    # Choosing the file again clears the notice, and the save keeps the file.
    form.locator('input[type="file"]').set_input_files([_pdf("tagasi-lykatud-lisa.pdf")])
    expect(notice).to_have_count(0)
    assert _save(page, door) == 200

    row = _stored_row(page, door)
    expect(row.locator("a.uxtl__file")).to_have_count(1)
    expect(row.locator("a.uxtl__file", has_text="tagasi-lykatud-lisa.pdf")).to_have_count(1)


def test_muu_names_the_file_to_choose_again_and_keeps_it_on_the_corrected_save(page, base_url):
    _refused_then_chosen_again(page, base_url, MUU)


@pytest.mark.parametrize("door", [KOHTUMINE, LISA_TAGASISIDE], ids=["kohtumine", "lisa-tagasiside"])
def test_every_door_with_files_names_the_file_to_choose_again(page, base_url, door):
    _refused_then_chosen_again(page, base_url, door)


def test_saving_without_the_files_is_the_persons_choice_not_the_pages(page, base_url):
    """`Salvesta ilma failita` is a choice, made in words, and then it saves.

    The server takes a record without files as it always has (the files are
    optional, and a page with no script has no notice at all); the page only
    asks before doing so after a refusal emptied the picker.
    """
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Salvesta ilma failita"))

    form, notice = _refuse_with(page, MUU, [_pdf("esimene.pdf"), _pdf("teine.pdf")])
    expect(notice).to_contain_text("Failid tuleb uuesti valida: esimene.pdf, teine.pdf")

    MUU.fix(form)
    assert _press_save_and_count_posts(page, MUU) == 0
    expect(notice).to_be_focused()

    with page.expect_response(
        lambda r: r.url.endswith(MUU.path) and r.request.method == "POST"
    ) as answer:
        notice.get_by_role("button", name="Salvesta ilma failita").click()
    wait_for_htmx(page)
    assert answer.value.status == 200

    row = _stored_row(page, MUU)
    expect(row.locator("a.uxtl__file")).to_have_count(0)
