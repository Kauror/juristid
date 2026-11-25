"""The four text sizes reach the text that matters, and Tavaline is unchanged (docs/adr/0221).

B2 moved the type scale through tokens; ~110 rules still named a fixed pixel
size and so ignored the reader's choice — chips, status labels, timeline
labels, the deadline list, menus. The corrections round (25.11.2026) turned
the user-facing ones into the same tokens, each written so that at Tavaline it
computes to exactly the pixels it had (icons and glyphs drawn inside a fixed
circle keep theirs; the inventory is in the workstream's report).

For each surface the coordinator named — buttons, form labels, filter chips,
status labels, the stage popover, timeline labels, navigation, table text —
one representative element is measured at Väike, Tavaline, Suur and Väga suur,
on a desktop (1440) and a phone (375):

* at Tavaline it is exactly its frozen size (no default page moved);
* the four sizes grow strictly, smallest to largest;
* on the phone, no size makes the page scroll sideways.
"""

from __future__ import annotations

import pytest

from e2e.conftest import HEAD, SANDRA, Persona, document_overflows, sign_in

pytestmark = pytest.mark.e2e

KEY = "juristid-text-size"
#: Smallest to largest; ``None`` is Tavaline (no key at all).
SIZES = ("vaike", None, "suur", "vaga-suur")

#: The first Teema in the register, whichever the seed made first.
TEEMA = "teema"

#: (persona, page, [(surface, selector, Tavaline size)]). The Tavaline sizes are
#: what these elements computed before the conversion, read on the seeded data.
SURFACES: list[tuple[Persona, str, list[tuple[str, str, str]]]] = [
    (
        SANDRA,
        "/teemad/",
        [
            ("header button", ".topbar__cta", "12.5px"),
            ("filter chip", ".uxchip", "12px"),
            ("filter trigger", ".filterpanel__trigger", "12px"),
            ("table text", "td.table__title", "13px"),
            ("table header", "th.table__title", "10px"),
            ("navigation", ".topnav__link", "13px"),
        ],
    ),
    (
        SANDRA,
        "/haldus/",
        [
            ("Haldus navigation", ".haldusnav__link", "13px"),
            ("navigation group", ".haldusnav__grouplabel", "11px"),
            ("status label", ".statechip", "11px"),
        ],
    ),
    (
        SANDRA,
        "/teemad/uus/",
        [
            ("form label", "label.field__label", "11px"),
            ("choice chip", ".chip__name", "12px"),
        ],
    ),
    (
        SANDRA,
        TEEMA,
        [
            ("tab", ".tabs__tab", "13px"),
            ("rail label", ".railcard__label", "12px"),
            ("meta label", ".metaline__label", "12.5px"),
            ("stage popover option", ".stagepick__option", "13.5px"),
            ("timeline date", ".uxtl__msdate", "12px"),
            ("timeline text", ".uxtl__mswhat", "13px"),
            ("timeline count", ".uxtl__count", "12px"),
            ("small button", "button.button--tiny", "11px"),
        ],
    ),
    (
        HEAD,
        "/osakond/",
        [
            ("timeline kind label", ".uxtl__kind", "10px"),
            ("row status meaning", ".interrow__meaning", "10.5px"),
            ("section tab", ".ovtabs__tab", "13px"),
            ("feed filter", ".feedfilter__item", "12px"),
        ],
    ),
]


def _choose(page, size: str | None) -> None:
    if size is None:
        page.evaluate(f"() => window.localStorage.removeItem('{KEY}')")
    else:
        page.evaluate(f"() => window.localStorage.setItem('{KEY}', '{size}')")


def _teema_path(page, base_url: str) -> str:
    page.goto(f"{base_url}/teemad/")
    href = page.locator("a.table__titlelink").first.get_attribute("href")
    assert href, "the seeded register lists a Teema"
    return href


def _px(page, selector: str) -> float:
    element = page.locator(selector).first
    assert element.count(), f"{selector} is on the page"
    value = element.evaluate("e => getComputedStyle(e).fontSize")
    assert value.endswith("px"), value
    return float(value[:-2])


@pytest.mark.parametrize("width", [1440, 375])
@pytest.mark.parametrize(
    "persona, path, surfaces", SURFACES, ids=[f"{p.display_name}{path}" for p, path, _ in SURFACES]
)
def test_each_surface_scales_with_the_text_size(page, base_url, width, persona, path, surfaces):
    page.set_viewport_size({"width": width, "height": 900})
    sign_in(page, base_url, persona)
    address = _teema_path(page, base_url) if path == TEEMA else path

    measured: dict[str, list[float]] = {name: [] for name, _selector, _px_value in surfaces}
    for size in SIZES:
        _choose(page, size)
        page.goto(f"{base_url}{address}")
        page.wait_for_load_state("networkidle")
        assert page.evaluate("() => document.documentElement.getAttribute('data-text-size')") == (
            size
        )
        for name, selector, tavaline in surfaces:
            value = _px(page, selector)
            measured[name].append(value)
            if size is None:
                assert value == float(tavaline[:-2]), (name, selector, value, tavaline)
        if width < 900:
            assert not document_overflows(page), (address, size)
    _choose(page, None)

    for name, values in measured.items():
        assert values == sorted(values) and len(set(values)) == len(values), (name, values)
