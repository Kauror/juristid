"""The Teema page cleanup, in a real browser, at the widths it is read at.

`tests/test_teema_page_cleanup.py` proves what the server sends. This proves
what only a layout engine can say about it:

* the date under `+ Lisa` and under `+ Arvamus / tagasiside` is the width of a
  date, its calendar button stays on the same row, and nothing pushes the page
  sideways at 1440, 768 or 375;
* `+ Lõpeta teema` opens straight onto its chips, with no heading row above them;
* `Menetluse kulg` on a file that sent three opinions draws one solid run ending
  at the current phase, and the browser resolves it that way;
* `Teema käik` draws «Arvamus välja» semibold and a `Märge` regular, and every
  row's dot agrees with its headline — the 12 px accent dot beside a primary
  row, the 6 px muted one beside every other, on one aligned spine at 1440 and
  768;
* at 768 the same file with a future `Arvamuse tähtaeg` draws nine columns and
  no label on the rail prints over its neighbour's;
* neither section's heading takes any room, and both are still headings.

Everything here is synthetic.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from itertools import pairwise

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    SANDRA,
    choose_organisation,
    open_add_panel,
    open_hetkeseis,
    open_kaik_row,
    sign_in,
    unique_title,
)
from e2e.conftest import record_marge as post_marge

pytestmark = pytest.mark.e2e

#: Three opinions a season apart, all behind us on any day this suite runs, so
#: the rail they draw does not depend on the calendar.
OPINION_DAYS = ("10.02.2025", "15.05.2025", "01.09.2025")

WIDTHS = (1440, 768, 375)


def a_procedure_matter(
    page,
    base_url: str,
    label: str,
    *,
    deadline: str = "",
    instruments: tuple[str, ...] = ("Seadus",),
    hetkeseis: str = "Kooskõlastusringil",
) -> str:
    """A `Seadus` on `Kooskõlastusringil`, filed through the real form.

    The shape the removed `Etapp` select rendered on, and the one whose rail has
    a current phase with phases ahead of it. ``deadline`` fills `Arvamuse
    tähtaeg`, the one date `Uus teema` asks for, as a lawyer types it.
    """
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")
    page.fill("#id_title", unique_title(label))
    for instrument in instruments:
        page.get_by_role("checkbox", name=instrument, exact=True).check()
    open_hetkeseis(page)
    page.get_by_role("radio", name=hetkeseis, exact=True).check()
    if deadline:
        page.fill("#id_response_deadline", deadline)
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    page.wait_for_load_state("networkidle")
    return page.url


def record_koja_arvamus(page, *, sent_on: str) -> None:
    """One sent `Koja arvamus`, through `+ Arvamus / tagasiside`."""
    open_add_panel(page, "arvamus-koja")
    form = page.locator("#arvamus-koja")
    form.locator("input[name=upload]").set_input_files(
        {"name": f"arvamus-{sent_on}.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4"}
    )
    form.locator("[name=sent_on]").fill(sent_on)
    choose_organisation(page, "koja-adressaat")
    form.get_by_role("button", name="Registreeri arvamus").click()
    page.wait_for_load_state("networkidle")
    expect(page.locator("#ajalugu-loend")).to_contain_text(sent_on.lstrip("0").replace(".0", "."))


def a_matter_with_three_opinions(page, base_url: str) -> str:
    url = a_procedure_matter(page, base_url, "Kolm arvamust")
    for day in OPINION_DAYS:
        record_koja_arvamus(page, sent_on=day)
    return url


def assert_no_sideways_scroll(page) -> None:
    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    assert overflow <= 0, f"the page scrolls sideways by {overflow}px"


def date_geometry(label) -> dict:
    """The label's width, and whether the calendar button shares the box's row."""
    return label.evaluate(
        """el => {
          const box = el.querySelector('input').getBoundingClientRect();
          const trigger = el.querySelector('.datepicker__trigger').getBoundingClientRect();
          const own = el.getBoundingClientRect();
          return {
            width: own.width,
            sameRow: Math.abs(trigger.top + trigger.height / 2 - (box.top + box.height / 2)) <= 4,
            inside: trigger.right <= own.right + 1,
            panel: el.closest('.cx-panel__body').getBoundingClientRect().width,
          };
        }"""
    )


# ---------------------------------------------------------------------------
# The panels: `+ Lisa`, `+ Arvamus / tagasiside`, `+ Lõpeta teema`
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", WIDTHS)
def test_the_panels_are_compact_and_fit(page, base_url, width):
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": width, "height": 900})
    a_procedure_matter(page, base_url, f"Paneelid {width}")

    # `+ Lisa`: no `Etapp` anywhere in it, and its default child's one date —
    # `Arvamuse tähtaeg` since `Tavaline` and its `Kuupäev` left on 2026-10-07
    # — the width of a date.
    open_add_panel(page, "marge-arvamuse-tahtaeg")
    family = page.locator("#lisa-marge")
    expect(family.locator("[name=process_phase]")).to_have_count(0)
    expect(family).not_to_contain_text("Etapp")
    marge = page.locator("#marge-arvamuse-tahtaeg")
    geometry = date_geometry(marge.locator("label.cx-f--solo"))
    assert geometry["width"] <= 161, geometry
    assert geometry["sameRow"] and geometry["inside"], geometry
    assert_no_sideways_scroll(page)

    # `+ Arvamus / tagasiside` opens on `Meile saadetud tagasiside`.
    open_add_panel(page, "arvamus-tagasiside")
    feedback = page.locator("#arvamus-tagasiside")
    geometry = date_geometry(feedback.locator("label.cx-f--solo"))
    assert geometry["width"] <= 161, geometry
    if width > 375:
        # It used to run nearly the whole panel.
        assert geometry["width"] < geometry["panel"] / 2, geometry
    assert geometry["sameRow"] and geometry["inside"], geometry
    assert_no_sideways_scroll(page)

    # `+ Lõpeta teema` is retired (docs/adr/0131 §11): no third panel to fit.
    expect(page.locator("#teema-lopeta")).to_have_count(0)


# ---------------------------------------------------------------------------
# `Menetluse kulg`, `Teema käik` and the two headings, on the owner's case
# ---------------------------------------------------------------------------


def reaches(page) -> list[float]:
    values = page.locator(".lprail .tl-step").evaluate_all(
        "nodes => nodes.map(n => getComputedStyle(n).getPropertyValue('--tl-reach').trim())"
    )
    return [float(value.rstrip("%")) / 100 for value in values]


def test_three_opinions_draw_one_run_and_the_page_reads_quietly(page, base_url):
    sign_in(page, base_url, SANDRA)
    a_matter_with_three_opinions(page, base_url)
    page.locator("#ajalugu-loend").get_by_text("Arvamus välja").first.wait_for()

    rail = page.locator(".lprail")
    expect(rail.locator(".tl-step--current")).to_have_count(1)
    expect(rail.locator(".tl-step--milestone").filter(has_text="Koja arvamus")).to_have_count(3)

    # One run: muted, solid, then muted — resolved by the browser, not read off
    # the markup.
    shape = "".join("0" if r == 0 else "1" if r == 1 else "p" for r in reaches(page))
    assert re.fullmatch(r"0*1+p?0*", shape), shape
    kinds = rail.locator(".tl-step").evaluate_all(
        "nodes => nodes.map(n => n.classList.contains('tl-step--current') ? 'C' :"
        " n.classList.contains('tl-step--milestone') ? 'M' : 'P')"
    )
    current = kinds.index("C")
    assert shape[current:].strip("0") == "", (kinds, shape)
    assert set(shape[kinds.index("M") : current]) == {"1"}, (kinds, shape)

    # `Teema käik`: the opinion heavier than a note — a `Märge`, which every
    # older file holds, written through `add_note` since its panel left.
    record_marge(page, "Rääkisin ministeeriumiga")
    history = page.locator("#ajalugu-loend")
    note = history.locator("article.uxtl__item").filter(has_text="Rääkisin ministeeriumiga")
    expect(note).to_have_count(1)

    def weight(row) -> int:
        return int(
            row.locator(".uxtl__mswhat").first.evaluate("el => getComputedStyle(el).fontWeight")
        )

    opinion = history.locator("article.uxtl__item--primary").filter(has_text="Arvamus välja").first
    assert weight(opinion) == 600
    assert weight(note) == 400
    expect(note).to_have_class(re.compile(r"\buxtl__item--secondary\b"))
    open_kaik_row(note)
    expect(note.get_by_role("button", name=re.compile("Muuda"))).to_be_visible()

    # `Menetluse kulg`'s heading takes no room and is still a level-two heading;
    # `Tegevused` is a visible one, with its count beside it (docs/adr/0140 §7).
    heading = page.get_by_role("heading", name="Menetluse kulg", level=2)
    expect(heading).to_have_count(1)
    box = heading.bounding_box()
    assert box is not None and box["width"] <= 1 and box["height"] <= 1, box
    expect(page.get_by_role("heading", name="Tegevused", level=2)).to_be_visible()
    expect(page.locator("#ajajoon .uxtl__count")).to_be_visible()

    for width in (768, 375):
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(100)
        assert_no_sideways_scroll(page)
        expect(rail.locator(".tl-step--current")).to_have_count(1)


# ---------------------------------------------------------------------------
# `Teema käik`: one hierarchy — the dot and the headline weight are one decision
# ---------------------------------------------------------------------------

#: What the browser resolves each row's dot, headline and spine to. Read off
#: the laid-out page, not off class names, so a rule that stopped matching —
#: a renamed modifier, a lost `#teema-vaade-wrap` scope — fails here.
ROW_GEOMETRY = """rows => {
  const scope = document.querySelector('#teema-vaade-wrap');
  const resolve = (token) => {
    const probe = document.createElement('span');
    probe.style.background = `var(${token})`;
    scope.appendChild(probe);
    const colour = getComputedStyle(probe).backgroundColor;
    probe.remove();
    return colour;
  };
  const accent = resolve('--accent-link');
  const muted = resolve('--text-muted');
  return rows.map(row => {
    const dot = row.querySelector('.uxtl__dot');
    const d = dot.getBoundingClientRect();
    const line = row.querySelector('.uxtl__line').getBoundingClientRect();
    const rail = row.querySelector('.uxtl__rail').getBoundingClientRect();
    const body = row.querySelector('.uxtl__body').getBoundingClientRect();
    const head = row.querySelector('.uxtl__mswhat') || row.querySelector('.uxtl__author');
    return {
      text: row.textContent.replace(/\\s+/g, ' ').trim().slice(0, 90),
      primary: row.classList.contains('uxtl__item--primary'),
      secondary: row.classList.contains('uxtl__item--secondary'),
      width: d.width, height: d.height,
      dotX: d.left + d.width / 2,
      background: getComputedStyle(dot).backgroundColor,
      accent, muted,
      lineX: line.left + line.width / 2,
      lineBottom: line.bottom, railTop: rail.top,
      bodyLeft: body.left,
      weight: head ? Number(getComputedStyle(head).fontWeight) : null,
    };
  });
}"""


def record_marge(page, title: str) -> None:
    """A `Märge`, through `add_note` — `+ Lisa · Tavaline` left on 2026-10-07,
    and every `Märge` already on a file still draws its row."""
    assert post_marge(page, title) == 200
    expect(page.locator("#ajalugu-loend")).to_contain_text(title)


def record_feedback(page, summary: str) -> None:
    """`Meile saadetud tagasiside`, as `e2e/test_correction_round_surfaces.py` does."""
    open_add_panel(page, "arvamus-tagasiside")
    page.fill("#id_tagasiside_summary", summary)
    page.get_by_role("button", name="Salvesta tagasiside").click()
    page.wait_for_load_state("networkidle")
    expect(page.locator("#ajalugu-loend")).to_contain_text(summary)


def record_other_position(page, summary: str) -> None:
    """`Teiste arvamus`, as `e2e/test_external_position.py` does."""
    open_add_panel(page, "arvamus-teiste")
    choose_organisation(page, "valine-seisukoht")
    page.locator("#arvamus-teiste [name=summary]").fill(summary)
    page.locator("#arvamus-teiste").get_by_role("button", name="Salvesta").click()
    page.wait_for_load_state("networkidle")
    expect(page.locator("#ajalugu-loend")).to_contain_text(summary)


def record_publication(page) -> None:
    """A published `Ülevaade / uudis`: both boxes filled files it as published."""
    open_add_panel(page, "lisa-koduleht")
    panel = page.locator("#lisa-koduleht")
    panel.locator("[name=url]").fill("https://koda.ee/uudised/e2e-uks-hierarhia")
    panel.locator("[name=published_on]").fill("14.03.2026")
    panel.get_by_role("button", name="Lisa ülevaade / uudis").click()
    page.wait_for_load_state("networkidle")
    expect(page.locator("#ajalugu-loend")).to_contain_text("koda.ee/uudised/e2e-uks-hierarhia")


def test_teema_kaik_draws_one_hierarchy_in_the_dot_and_the_headline(page, base_url):
    """PRIMARY = 12 px accent dot + semibold; SECONDARY = 6 px muted dot + regular.

    The owner's screenshot of 2026-09-27, rebuilt through the real forms: two
    outcomes — «Arvamus välja» and a published `Ülevaade / uudis` — among a
    `Märge`, «Meile saadetud tagasiside», «Teiste arvamus» and «Teema loodud»,
    each of which drew the big accent dot beside a regular headline
    (docs/adr/0074 §14, amended 2026-09-27).
    """
    sign_in(page, base_url, SANDRA)
    a_procedure_matter(page, base_url, "Üks hierarhia")
    record_koja_arvamus(page, sent_on="15.05.2025")
    record_publication(page)
    record_marge(page, "Rääkisin ministeeriumiga")
    record_feedback(page, "Liige toetab eelnõu")
    record_other_position(page, "Ministeeriumi seisukoht")

    history = page.locator("#ajalugu-loend")
    rows = history.locator("article.uxtl__item")

    for width in (1440, 768):
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(150)
        geometry = rows.evaluate_all(ROW_GEOMETRY)
        assert geometry, "the chronology drew no rows"
        accent, muted = geometry[0]["accent"], geometry[0]["muted"]
        assert accent != muted, (accent, muted)

        # The screenshot's rows, by what they say.
        primary = [row["text"] for row in geometry if row["primary"]]
        secondary = [row["text"] for row in geometry if row["secondary"]]
        assert len(primary) == 2, primary
        assert any("Arvamus välja" in text for text in primary), primary
        # A koda.ee news address reads `Uudis` since docs/adr/0142 §C.
        # The row's text opens with its toggle's hidden name, then the headline.
        headlines = [text.replace("Kirje üksikasjad", "", 1).strip() for text in primary]
        assert any(text.startswith("Uudis") for text in headlines), primary
        for words in (
            "Rääkisin ministeeriumiga",
            "Meile saadetud tagasiside",
            "Teiste arvamus",
            "Teema loodud",
        ):
            assert any(words in text for text in secondary), (width, words, secondary)
        assert len(primary) + len(secondary) == len(geometry)

        # One hierarchy: if primary, the big accent dot and a semibold
        # headline; if not, the small muted dot and a regular one.
        for row in geometry:
            if row["primary"]:
                assert abs(row["width"] - 12) <= 0.5 and abs(row["height"] - 12) <= 0.5, row
                assert row["background"] == accent, row
                assert row["weight"] == 600, row
            else:
                assert abs(row["width"] - 6) <= 0.5 and abs(row["height"] - 6) <= 0.5, row
                assert row["background"] == muted, row
                assert row["weight"] in (None, 400), row

        # One spine: every dot centred on one vertical line, each row's line
        # running on into the next row, and no row's text moved sideways by its
        # dot changing size.
        spine = geometry[0]["lineX"]
        for row in geometry:
            assert abs(row["lineX"] - spine) <= 0.5, (width, row)
            assert abs(row["dotX"] - spine) <= 0.5, (width, row)
            assert abs(row["bodyLeft"] - geometry[0]["bodyLeft"]) <= 0.5, (width, row)
        for above, below in pairwise(geometry):
            assert above["lineBottom"] >= below["railTop"] - 1, (width, above, below)

        assert_no_sideways_scroll(page)

        # The quieter rows keep every control, and each one still takes a click.
        for words in ("Rääkisin ministeeriumiga", "Liige toetab eelnõu", "Ministeeriumi seisukoht"):
            row = rows.filter(has_text=words)
            expect(row).to_have_count(1)
            open_kaik_row(row)
            muuda = row.get_by_role("button", name=re.compile("Muuda"))
            expect(muuda).to_be_visible()
            muuda.click(trial=True)
            kustuta = row.locator("summary", has_text="Kustuta")
            expect(kustuta).to_be_visible()
            kustuta.click(trial=True)


# ---------------------------------------------------------------------------
# `Menetluse kulg` at 768: nine columns, and no label over its neighbour's
# ---------------------------------------------------------------------------


def label_boxes(page) -> list[dict]:
    """Every rail label's own box, and the column it belongs to.

    The label's box and not the column's: a flex item in the column is as wide
    as its longest word whatever the column is, so a word that does not fit is
    a box running on past the column edge — which is exactly what overprinted.
    """
    return page.locator(".lprail .tl-step").evaluate_all(
        """nodes => nodes.map(n => {
          const what = n.querySelector('.tl-step__what').getBoundingClientRect();
          const column = n.getBoundingClientRect();
          return {
            label: n.querySelector('.tl-step__what').textContent.trim(),
            left: what.left, right: what.right, top: what.top, bottom: what.bottom,
            columnLeft: column.left, columnRight: column.right,
          };
        })"""
    )


def overprinted(boxes: list[dict]) -> list[tuple[str, str]]:
    return [
        (a["label"], b["label"])
        for i, a in enumerate(boxes)
        for b in boxes[i + 1 :]
        if a["left"] < b["right"] - 0.5
        and b["left"] < a["right"] - 0.5
        and a["top"] < b["bottom"] - 0.5
        and b["top"] < a["bottom"] - 0.5
    ]


def test_nine_columns_at_768_never_print_one_label_over_another(page, base_url):
    """The owner's shape plus a deadline still ahead: nine columns on the rail.

    With equal shares at 768 each column was 78px, and the current phase's
    semibold «Kooskõlastusring» — one word, about 95px — ran on into the next
    column and printed over «Arvamuse tähtaeg». A column is never narrower than
    its own longest word now (`static/css/app.css`, `.tl-strip`), so at 768 the
    long one is wider and the rest share what is left; the page itself never
    scrolls sideways, and on a phone the rail scrolls itself as it always did.
    """
    sign_in(page, base_url, SANDRA)
    ahead = date.today() + timedelta(days=30)
    a_procedure_matter(
        page, base_url, "Üheksa veergu", deadline=f"{ahead.day}.{ahead.month}.{ahead.year}"
    )
    for day in OPINION_DAYS:
        record_koja_arvamus(page, sent_on=day)

    rail = page.locator(".lprail .tl-strip")
    expect(rail.locator(".tl-step--current")).to_have_text(re.compile("Kooskõlastusring"))
    expect(rail.locator(".tl-step--milestone").filter(has_text="Koja arvamus")).to_have_count(3)
    expect(rail.locator(".tl-step--milestone").filter(has_text="Arvamuse tähtaeg")).to_have_count(1)

    for width in (768, 1440, 375):
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(150)
        boxes = label_boxes(page)
        assert len(boxes) == 9, [box["label"] for box in boxes]
        assert overprinted(boxes) == [], (width, overprinted(boxes))
        for box in boxes:
            assert box["right"] <= box["columnRight"] + 0.5, (width, box)
        # Contiguous columns, so the one solid run is one line with no gap.
        for before, after in pairwise(boxes):
            assert abs(after["columnLeft"] - before["columnRight"]) <= 0.5, (width, before, after)
        assert_no_sideways_scroll(page)

        geometry = rail.evaluate(
            "node => ({ clientWidth: node.clientWidth, scrollWidth: node.scrollWidth })"
        )
        if width >= 768:
            # Nine floors fit at 768, so the rail does not scroll there.
            assert geometry["scrollWidth"] <= geometry["clientWidth"] + 1, (width, geometry)
        else:
            assert geometry["scrollWidth"] > geometry["clientWidth"], (width, geometry)


# ---------------------------------------------------------------------------
# `Menetluse kulg` starts at `Algus` on a file still standing on it
# ---------------------------------------------------------------------------


def typed(day: date) -> str:
    return f"{day.day:02d}.{day.month:02d}.{day.year}"


def drawn_left_to_right(page) -> list[tuple[str, str, str]]:
    """Every rail column as the browser lays it out: label, date, kind.

    Ordered by each column's own left edge rather than by the markup, so the
    assertion is about what a reader sees. The DOM order is asserted to agree,
    which is what makes the arrow on the rail read the same way to a screen
    reader.
    """
    columns = page.locator(".lprail .tl-step").evaluate_all(
        """nodes => nodes.map((n, i) => ({
          dom: i,
          left: n.getBoundingClientRect().left,
          label: n.querySelector('.tl-step__what').textContent.trim(),
          date: (n.querySelector('.tl-step__date') || {textContent: ''}).textContent.trim(),
          kind: n.classList.contains('tl-step--current') ? 'C'
            : n.classList.contains('tl-step--milestone') ? 'M' : 'P',
        }))"""
    )
    by_left = sorted(columns, key=lambda column: column["left"])
    assert [column["dom"] for column in by_left] == list(range(len(columns))), by_left
    lefts = [column["left"] for column in by_left]
    assert all(b > a for a, b in pairwise(lefts)), lefts
    return [(column["label"], column["date"], column["kind"]) for column in by_left]


@pytest.mark.parametrize("width", (1440, 768))
def test_opinions_sent_after_the_file_opened_read_after_algus(page, base_url, width):
    """The production defect, in the browser that found it.

    A native VTK-and-bill file still on `Idee`, with two opinions sent after it
    opened and an answer due later, drew

        Koja arvamus → Koja arvamus → Algus → Arvamuse tähtaeg → VTK → …

    and must draw

        Algus → Koja arvamus (earlier) → Koja arvamus (later) → Arvamuse tähtaeg → VTK → …

    Dates relative to the day the suite runs, so the rail's reading of today —
    both sends behind us, the deadline ahead — holds on any date.
    """
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": width, "height": 900})
    today = date.today()
    first, second, due = (
        today - timedelta(days=5),
        today - timedelta(days=4),
        today + timedelta(days=3),
    )
    a_procedure_matter(
        page,
        base_url,
        f"Algusest {width}",
        deadline=typed(due),
        instruments=("VTK", "Seadus"),
        hetkeseis="Idee",
    )
    record_koja_arvamus(page, sent_on=typed(first))
    record_koja_arvamus(page, sent_on=typed(second))
    page.reload()
    page.wait_for_load_state("networkidle")

    rail = page.locator(".lprail .tl-strip")
    expect(rail.locator(".tl-step--current")).to_have_text(re.compile("Algus"))
    columns = drawn_left_to_right(page)

    def short(day: date) -> str:
        return f"{day.day}.{day.month}.{day.year}"

    assert [(label, when) for label, when, _kind in columns] == [
        ("Algus", ""),
        ("Koja arvamus", short(first)),
        ("Koja arvamus", short(second)),
        ("Arvamuse tähtaeg", short(due)),
        ("VTK", ""),
        ("Kooskõlastusring", ""),
        ("Valitsuses", ""),
        ("Riigikogus", ""),
        ("Jõustumine", ""),
    ], columns
    assert columns[0][2] == "C", columns

    # One continuous reached run from `Algus` through both sends, today's place
    # in the segment towards the deadline, and nothing reached after it.
    shape = "".join("0" if r == 0 else "1" if r == 1 else "p" for r in reaches(page))
    assert re.fullmatch(r"11p0{6}", shape), shape

    # The geometry the 768 round fixed, on this file too.
    boxes = label_boxes(page)
    assert overprinted(boxes) == [], (width, overprinted(boxes))
    for box in boxes:
        assert box["right"] <= box["columnRight"] + 0.5, (width, box)
    for before, after in pairwise(boxes):
        assert abs(after["columnLeft"] - before["columnRight"]) <= 0.5, (width, before, after)
    assert_no_sideways_scroll(page)
    geometry = rail.evaluate(
        "node => ({ clientWidth: node.clientWidth, scrollWidth: node.scrollWidth })"
    )
    # Nine columns fit at 768 and above, so the rail itself does not scroll.
    assert geometry["scrollWidth"] <= geometry["clientWidth"] + 1, (width, geometry)
