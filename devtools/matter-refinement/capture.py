"""Capture the real Matter page and check its responsive behaviour.

Development tool, not a test and not part of any suite. The committed visual
baselines come from the CI container, where the fonts rasterise differently;
these captures are for looking at, and for the side-by-side against the
approved preview.

    uv run python devtools/matter-refinement/capture.py --matter <uuid>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent / "shots"
BASE = "http://127.0.0.1:8078"
PERSONA = "Mari Näidisjurist"

#: The viewport the approved comparison was taken at.
PRIMARY = (1560, 991)
WIDTHS = [1920, 1560, 1440, 1280, 1101, 1100, 1024, 768, 390]


def sign_in(page) -> None:
    page.goto(f"{BASE}/konto/arendus-sisselogimine/")
    page.get_by_label(PERSONA, exact=False).check()
    page.get_by_role("button", name="Logi sisse").click()
    page.wait_for_url(f"{BASE}/minu-asjad/")


def settle(page) -> None:
    page.wait_for_load_state("networkidle")
    page.evaluate("document.fonts.ready")
    page.wait_for_timeout(250)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matter", required=True)
    args = parser.parse_args()
    url = f"{BASE}/teemad/{args.matter}/"
    OUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": PRIMARY[0], "height": PRIMARY[1]})
        sign_in(page)

        page.goto(url)
        settle(page)
        page.screenshot(path=str(OUT / "real-1560x991.png"))
        page.screenshot(path=str(OUT / "real-1560-full.png"), full_page=True)
        print("  real-1560x991.png / real-1560-full.png")

        page.locator(".uxnext__text").click()
        page.wait_for_timeout(250)
        page.screenshot(path=str(OUT / "real-composer-open.png"), full_page=True)
        print("  real-composer-open.png")

        page.goto(url)
        settle(page)
        page.locator("#seotud-materjalid .disclosure__summary").first.click()
        page.wait_for_timeout(250)
        page.screenshot(path=str(OUT / "real-related-open.png"), full_page=True)
        print("  real-related-open.png")

        page.goto(url)
        settle(page)
        page.locator(".factspanel .factrow").first.hover()
        page.wait_for_timeout(300)
        page.screenshot(path=str(OUT / "real-factrow-hover.png"))
        print("  real-factrow-hover.png")

        print()
        failures = []
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(url)
            settle(page)
            columns = page.evaluate(
                "getComputedStyle(document.querySelector('.teemagrid'))"
                ".gridTemplateColumns.split(' ').length"
            )
            over = page.evaluate(
                "Math.max(0, document.documentElement.scrollWidth - window.innerWidth)"
            )
            rail = page.evaluate(
                "(() => { const r = document.querySelector('.teema .rail');"
                " return r ? Math.round(r.getBoundingClientRect().width) : 0; })()"
            )
            # Measured from the main column, not the viewport: above
            # `--layout-workspace-max` the whole shell is centred, so a
            # viewport-relative number says how wide the screen is rather than
            # whether the design's 32px edge holds (ultrawide workspace pass).
            edge = page.evaluate(
                "(() => { const e = document.querySelector('.factspanel .sectionlabel');"
                " const m = document.querySelector('.teemamain');"
                " if (!e || !m) return -1;"
                " return Math.round(e.getBoundingClientRect().left"
                " - m.getBoundingClientRect().left); })()"
            )
            print(
                f"  {width:>5}px  columns={columns}  rail={rail}px  "
                f"label-x={edge}  overflow={over}px"
            )
            if over:
                failures.append(f"{width}px overflows by {over}px")
            if width >= 1101 and columns != 2:
                failures.append(f"{width}px is not two columns")
            if width >= 1101 and rail != 300:
                failures.append(f"{width}px rail is {rail}px, not 300")
            if width <= 1100 and columns != 1:
                failures.append(f"{width}px did not stack")
            if width >= 1101 and edge not in (-1, 32):
                failures.append(f"{width}px section label at {edge}px, not 32")
            page.screenshot(path=str(OUT / f"real-{width}.png"), full_page=(width <= 1100))

        browser.close()

    print()
    if failures:
        print("FAILURES:")
        for line in failures:
            print(f"  {line}")
        return 1
    print("responsive: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
