"""Capture the acceptance set for the Teema refinement preview. PREVIEW ONLY.

Not a test and not part of any suite. It drives a real browser against a local
development server holding the synthetic preview Matter, and writes PNGs into
`preview-artifacts/matter-refinement/`.

    uv run python preview-artifacts/capture.py --matter <uuid>

The production visual-regression baselines in `e2e/baselines/` are never touched
by this: it writes nowhere near them, and nothing here is compared to anything.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent / "matter-refinement"
BASE = "http://127.0.0.1:8077"
PERSONA = "Mari Näidisjurist"

#: The product owner's own viewport, from the supplied current-page screenshot.
PRIMARY = (1560, 991)

WIDE = [(1920, 1080), (1440, 900), (1280, 800)]
NARROW = [(1024, 768), (768, 1024), (390, 844)]
#: The two sides of the only breakpoint the design has.
BREAKPOINT = [(1101, 900), (1100, 900)]


def sign_in(page) -> None:
    page.goto(f"{BASE}/konto/arendus-sisselogimine/")
    page.get_by_label(PERSONA, exact=False).check()
    page.get_by_role("button", name="Logi sisse").click()
    page.wait_for_url(f"{BASE}/minu-asjad/")


def settle(page) -> None:
    page.wait_for_load_state("networkidle")
    # The shell's fonts are self-hosted; a capture taken before they swap in
    # measures the fallback's metrics and every column in it is wrong.
    page.evaluate("document.fonts.ready")
    page.wait_for_timeout(250)


def shot(page, url: str, name: str, size: tuple[int, int], *, full: bool = True) -> None:
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(url)
    settle(page)
    OUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=full)
    print(f"  {name}.png  {size[0]}x{size[1]}{' full' if full else ''}")


def overflow(page) -> int:
    return page.evaluate("Math.max(0, document.documentElement.scrollWidth - window.innerWidth)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matter", required=True)
    args = parser.parse_args()

    current = f"{BASE}/teemad/{args.matter}/"
    preview = f"{BASE}/disainisusteem/teema-refinement/{args.matter}/"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": PRIMARY[0], "height": PRIMARY[1]})
        sign_in(page)

        print("A — the product-owner comparison")
        shot(page, current, "A-current-1560x991", PRIMARY)
        shot(page, preview, "A-preview-1560x991", PRIMARY)
        shot(page, current, "A-current-1560x991-viewport", PRIMARY, full=False)
        shot(page, preview, "A-preview-1560x991-viewport", PRIMARY, full=False)

        print("B — wide")
        for size in WIDE:
            shot(page, preview, f"B-preview-{size[0]}x{size[1]}", size)

        print("C — stacked / narrow")
        for size in NARROW:
            shot(page, preview, f"C-preview-{size[0]}x{size[1]}", size)

        print("D — states")
        page.set_viewport_size({"width": PRIMARY[0], "height": PRIMARY[1]})

        page.goto(preview)
        settle(page)
        page.screenshot(path=str(OUT / "D-01-default.png"), full_page=True)
        print("  D-01-default.png")

        # Composer open, through the interaction the refinement adds (I01):
        # clicking the Järgmiseks row rather than the composer itself.
        page.goto(preview)
        settle(page)
        page.locator(".uxnext__text").click()
        page.wait_for_timeout(200)
        page.screenshot(path=str(OUT / "D-02-composer-open.png"), full_page=True)
        print("  D-02-composer-open.png")

        page.goto(preview)
        settle(page)
        page.locator(".metaline__item", has_text="Hetkeseis").locator(
            "summary.inlineedit__trigger"
        ).click()
        page.wait_for_timeout(200)
        page.screenshot(path=str(OUT / "D-03-header-inline-editor-open.png"), full_page=False)
        print("  D-03-header-inline-editor-open.png")

        page.goto(preview)
        settle(page)
        page.locator("summary.headmenu__trigger").click()
        page.wait_for_timeout(200)
        page.screenshot(path=str(OUT / "D-04-action-menu-open.png"), full_page=False)
        print("  D-04-action-menu-open.png")

        page.goto(preview)
        settle(page)
        page.locator("#ajajoon > summary").click()
        page.wait_for_timeout(200)
        page.screenshot(path=str(OUT / "D-05-timeline-collapsed.png"), full_page=True)
        print("  D-05-timeline-collapsed.png")

        page.goto(preview)
        settle(page)
        page.locator("#seotud-materjalid summary.disclosure__summary").click()
        page.wait_for_timeout(200)
        page.screenshot(path=str(OUT / "D-06-related-materials-open.png"), full_page=True)
        print("  D-06-related-materials-open.png")

        page.goto(preview)
        settle(page)
        page.locator(".factsections .factrow").first.hover()
        page.wait_for_timeout(250)
        page.screenshot(path=str(OUT / "D-07-factrow-actions-visible.png"), full_page=False)
        print("  D-07-factrow-actions-visible.png")

        print("E — responsive check")
        failures = []
        for width in [1920, 1560, 1440, 1280, 1101, 1100, 1024, 768, 390]:
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(preview)
            settle(page)
            columns = page.evaluate(
                "getComputedStyle(document.querySelector('.teemagrid'))"
                ".gridTemplateColumns.split(' ').length"
            )
            over = overflow(page)
            print(f"  {width:>5}px  columns={columns}  horizontal-overflow={over}px")
            if over:
                failures.append(f"{width}px overflows by {over}px")
            if width >= 1101 and columns != 2:
                failures.append(f"{width}px is not two columns")
            if width <= 1100 and columns != 1:
                failures.append(f"{width}px did not stack")

        browser.close()

    if failures:
        print("\nRESPONSIVE FAILURES:")
        for line in failures:
            print(f"  {line}")
        return 1
    print("\nresponsive: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
