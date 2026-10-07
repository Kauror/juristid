"""The shared upload queue in a real browser (owner's round, 2026-10-07).

- a file dropped on a dropzone joins the same queue a picked file does, the
  browser's own navigation prevented;
- several files, each listed with an editable display title — the filename by
  default — and a `×` that takes it back off;
- a file let go of anywhere else on the page is refused rather than opened.

`DataTransfer` and `DragEvent` are built in the page, which is what a real drop
hands the document: the handlers cannot tell the two apart.
"""

from __future__ import annotations

from playwright.sync_api import expect

from e2e.conftest import MARTIN, create_matter, open_add_panel, sign_in, unique_title

DROP = """([selector, names]) => {
  const target = selector ? document.querySelector(selector) : document.body;
  const transfer = new DataTransfer();
  names.forEach((name) =>
    transfer.items.add(new File(["%PDF-1.4\\n" + name], name, { type: "application/pdf" }))
  );
  const init = { bubbles: true, cancelable: true, dataTransfer: transfer };
  const over = new DragEvent("dragover", init);
  target.dispatchEvent(over);
  const drop = new DragEvent("drop", init);
  target.dispatchEvent(drop);
  return [over.defaultPrevented, drop.defaultPrevented];
}"""

#: Any panel that takes files shares the queue; `+ Lisa · Töövõit` since
#: `Tavaline`, which this used, left `+ Lisa` on 2026-10-07.
PANEL = "marge-toovoit"
ZONE = f"#{PANEL} [data-filedrop]"


def test_dropped_files_join_the_queue_with_editable_titles(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Lohistamine"))
    open_add_panel(page, PANEL)

    prevented = page.evaluate(DROP, [ZONE, ["esimene.pdf", "teine.pdf"]])

    assert prevented == [True, True]
    rows = page.locator(f"#{PANEL} [data-upload-queue] .uploadqueue__row")
    expect(rows).to_have_count(2)
    expect(rows.nth(0).locator(".uploadqueue__title")).to_have_value("esimene.pdf")
    expect(rows.nth(1).locator(".uploadqueue__title")).to_have_value("teine.pdf")

    # A second drop adds to the queue rather than replacing it.
    page.evaluate(DROP, [ZONE, ["kolmas.pdf"]])
    expect(rows).to_have_count(3)

    # A typed title survives the queue being redrawn by a removal.
    rows.nth(0).locator(".uploadqueue__title").fill("Ministeeriumi kiri")
    rows.nth(1).locator(".uploadqueue__remove").click()
    expect(rows).to_have_count(2)
    expect(rows.nth(0).locator(".uploadqueue__title")).to_have_value("Ministeeriumi kiri")
    expect(rows.nth(1).locator(".uploadqueue__title")).to_have_value("kolmas.pdf")
    count = page.evaluate(f"document.querySelector('{ZONE} input[type=file]').files.length")
    assert count == 2


def test_a_file_dropped_off_a_zone_never_navigates(page, base_url):
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, unique_title("Möödalohistamine"))

    prevented = page.evaluate(DROP, [None, ["eksitus.pdf"]])

    assert prevented == [True, True]
    assert page.url.rstrip("/") == url.rstrip("/")
