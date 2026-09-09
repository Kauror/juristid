"""Measure the preview against `02-layout-spec.md`. PREVIEW ONLY.

Not a test. It reads computed style off the live page and prints what the
handoff says beside what the browser did, so the design QA in the report is
measured rather than eyeballed.

    uv run python preview-artifacts/measure.py --matter <uuid>
"""

from __future__ import annotations

import argparse
import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8077"
PERSONA = "Mari Näidisjurist"

CHECKS: list[tuple[str, str, str, str]] = [
    # (what, selector, javascript expression on `el`, expected)
    ("grid columns", ".teemagrid", "getComputedStyle(el).gridTemplateColumns", "… 300px"),
    ("Järgmiseks padding", ".uxnext", "getComputedStyle(el).padding", "14px 32px"),
    ("Järgmiseks radius", ".uxnext", "getComputedStyle(el).borderRadius", "0px"),
    ("Järgmiseks ground", ".uxnext", "getComputedStyle(el).backgroundColor", "#131b21"),
    ("Järgmiseks accent", ".uxnext", "getComputedStyle(el).boxShadow", "inset 3px #17506a"),
    ("composer accent", "details.composer", "getComputedStyle(el).boxShadow", "inset 3px #17506a"),
    ("composer radius", "details.composer", "getComputedStyle(el).borderRadius", "0px"),
    ("next-step text", ".uxnext__text", "getComputedStyle(el).fontSize", "15px"),
    ("facts panel ground", ".factsections", "getComputedStyle(el).backgroundColor", "#14191f"),
    (
        "facts section pad",
        ".factsections .factsection",
        "getComputedStyle(el).padding",
        "16px 32px 14px",
    ),
    ("fact row padding", ".factlist--compact .factrow", "getComputedStyle(el).padding", "4px 0px"),
    ("fact actions rest", ".factsections .factrow__actions", "getComputedStyle(el).opacity", "0"),
    ("section label", ".factsection__head .sectionlabel", "getComputedStyle(el).fontSize", "12px"),
    (
        "section weight",
        ".factsection__head .sectionlabel",
        "getComputedStyle(el).fontWeight",
        "700",
    ),
    (
        "section tracking",
        ".factsection__head .sectionlabel",
        "getComputedStyle(el).letterSpacing",
        "0.96px",
    ),
    (
        "section case",
        ".factsection__head .sectionlabel",
        "getComputedStyle(el).textTransform",
        "uppercase",
    ),
    ("section colour", ".factsection__head .sectionlabel", "getComputedStyle(el).color", "#9aa7b4"),
    ("rail label", ".railcard__label", "getComputedStyle(el).fontSize", "12px"),
    (
        "timeline label",
        ".accordion--timeline .accordion__title",
        "getComputedStyle(el).fontSize",
        "12px",
    ),
    (
        "timeline head pad",
        ".accordion--timeline .accordion__head",
        "getComputedStyle(el).padding",
        "18px 32px 10px",
    ),
    (
        "timeline caret",
        ".accordion--timeline .accordion__caret",
        "getComputedStyle(el).display",
        "none",
    ),
    ("timeline ground", ".accordion--timeline", "getComputedStyle(el).backgroundColor", "#101418"),
    ("timeline body", ".uxtl .richtext", "getComputedStyle(el).fontSize", "14px"),
    ("timeline body colour", ".uxtl .richtext", "getComputedStyle(el).color", "#e8edf2"),
    ("timeline meta", ".uxtl__meta", "getComputedStyle(el).fontSize", "12px"),
    ("timeline meta colour", ".uxtl__meta", "getComputedStyle(el).color", "#7d8b99"),
    ("timeline entry gap", ".uxtl__body", "getComputedStyle(el).paddingBottom", "6px"),
    ("rail width", "aside.rail", "el.getBoundingClientRect().width", "300"),
    (
        "inline underline",
        ".inlineedit__trigger .metaline__value",
        "getComputedStyle(el).borderBottomColor",
        "transparent",
    ),
    ("page title", ".matterhead__title", "getComputedStyle(el).fontSize", "21px"),
    ("summary text", ".summary__text", "getComputedStyle(el).fontSize", "15px"),
]

LEFT_EDGES = [
    (".uxnext", "Järgmiseks"),
    (".uxcomp__collapsed", "composer prompt row"),
    ("#olulised-tahtajad .sectionlabel", "Olulised tähtajad label"),
    ("#olulised-tahtajad .factrow__date", "first deadline date"),
    ("#olulised-tahtajad .addrow", "+ Lisa tähtaeg"),
    ("#kaasamine .sectionlabel", "Kaasamine label"),
    (".accordion--timeline .accordion__title", "Ajajoon label"),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matter", required=True)
    parser.add_argument("--width", type=int, default=1560)
    args = parser.parse_args()

    url = f"{BASE}/disainisusteem/teema-refinement/{args.matter}/"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": args.width, "height": 991})
        page.goto(f"{BASE}/konto/arendus-sisselogimine/")
        page.get_by_label(PERSONA, exact=False).check()
        page.get_by_role("button", name="Logi sisse").click()
        page.wait_for_url(f"{BASE}/minu-asjad/")
        page.goto(url)
        page.wait_for_load_state("networkidle")

        print(f"measured at {args.width}px\n")
        print(f"{'what':<24} {'measured':<34} expected")
        print("-" * 96)
        for what, selector, expression, expected in CHECKS:
            value = page.evaluate(
                "([sel, expr]) => { const el = document.querySelector(sel);"
                " if (!el) return 'MISSING';"
                " return String(eval(expr)); }",
                [selector, expression],
            )
            print(f"{what:<24} {value:<34} {expected}")

        print(f"\n{'left edge of':<32} x")
        print("-" * 96)
        for selector, label in LEFT_EDGES:
            x = page.evaluate(
                "(sel) => { const el = document.querySelector(sel);"
                " if (!el) return 'MISSING';"
                " const r = el.getBoundingClientRect();"
                " const s = getComputedStyle(el);"
                " return String(Math.round(r.left + parseFloat(s.paddingLeft))); }",
                selector,
            )
            print(f"{label:<32} {x}")

        # The design's own claim: the main column's content edge is 32 px from
        # the frame. `.teemamain` starts at the viewport edge, so the numbers
        # above are absolute and 32 is the answer for every one of them.
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
