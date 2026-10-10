"""Every piece of text on the rendered pages, measured, in both themes.

tests/test_text_contrast.py holds the palettes: every role against every
surface, as numbers in a stylesheet. This holds what the browser actually
paints — a role on the surface a component really puts it on, with every
translucent background composited and every fade applied — across the pages a
lawyer works in and the menus they open (docs/adr/0147 §8).

Both themes meet WCAG AA (4.5:1, 3:1 for large text) for every piece of text
here, the dimmed `Hetkeseis` chips included. The dark theme carried one pair
until 2026-10-09 — `--text-muted` on `--surface-elevated`, 4.49:1, on nine
popover labels — and the owner closed it by lifting the role (docs/adr/0147,
amendment). Nothing may fail now, so a new rule that puts quiet text on a
surface too close to it fails here in either theme, on the run that introduces
it.

Nothing here writes: every page is only read.
"""

from __future__ import annotations

import pytest

from app.core.management.commands.seed_e2e_data import OPEN_TITLE
from e2e.conftest import SANDRA, sign_in

pytestmark = pytest.mark.e2e

#: The WCAG ratio of each visible text element against the background it is
#: painted on: ancestors' backgrounds composited until an opaque one, opacity
#: multiplied down the chain. Elements that paint nothing — hidden, clipped to
#: a pixel, faded to nothing, or disabled (which WCAG exempts) — are skipped.
AUDIT = r"""
() => {
  const parse = (c) => {
    const m = c && c.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(/[\s,\/]+/).filter(Boolean).map(Number);
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const over = (top, bottom) => ({
    r: top.r * top.a + bottom.r * (1 - top.a),
    g: top.g * top.a + bottom.g * (1 - top.a),
    b: top.b * top.a + bottom.b * (1 - top.a),
    a: 1,
  });
  const lum = (c) => {
    const f = (v) => {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  };
  const ratio = (a, b) => {
    const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
    return (x + 0.05) / (y + 0.05);
  };
  const hex = (c) =>
    "#" + [c.r, c.g, c.b].map((v) => Math.round(v).toString(16).padStart(2, "0")).join("");

  const background = (element) => {
    const stack = [];
    let opacity = 1;
    for (let node = element; node && node.nodeType === 1; node = node.parentElement) {
      const style = getComputedStyle(node);
      opacity *= parseFloat(style.opacity);
      if (style.backgroundImage !== "none" && !/gradient/.test(style.backgroundImage)) {
        return null;
      }
      const colour = parse(style.backgroundColor);
      if (colour && colour.a > 0) {
        stack.push(colour);
        if (colour.a >= 1) break;
      }
    }
    let colour = { r: 255, g: 255, b: 255, a: 1 };
    for (let i = stack.length - 1; i >= 0; i--) colour = over(stack[i], colour);
    return { colour, opacity };
  };

  const paintsNothing = (element) => {
    for (let node = element; node && node.nodeType === 1; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.display === "none" || style.visibility === "hidden") return true;
      if (style.position === "absolute" && node.offsetWidth <= 1 && node.offsetHeight <= 1) {
        return true;
      }
    }
    const box = element.getBoundingClientRect();
    return box.width === 0 || box.height === 0;
  };

  const elements = new Set();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    if (walker.currentNode.textContent.trim() && walker.currentNode.parentElement) {
      elements.add(walker.currentNode.parentElement);
    }
  }
  const fields = "input:not([type=hidden]):not([type=checkbox]):not([type=radio])"
    + ":not([type=file]), select, textarea";
  for (const control of document.querySelectorAll(fields)) {
    if (control.value) elements.add(control);
  }

  const failures = [];
  for (const element of elements) {
    if (["SCRIPT", "STYLE", "NOSCRIPT", "OPTION", "TEMPLATE"].includes(element.tagName)) continue;
    if (element.closest(":disabled, [aria-disabled=true], .is-disabled, svg")) continue;
    if (paintsNothing(element)) continue;
    const style = getComputedStyle(element);
    const ink = parse(style.color);
    const ground = background(element);
    if (!ink || !ground || ground.opacity < 0.05) continue;
    let painted = ink.a < 1 ? over(ink, ground.colour) : ink;
    if (ground.opacity < 1) painted = over({ ...painted, a: ground.opacity }, ground.colour);
    const size = parseFloat(style.fontSize);
    const large = size >= 24 || (size >= 18.66 && parseInt(style.fontWeight, 10) >= 700);
    const value = ratio(painted, ground.colour);
    if (value < (large ? 3 : 4.5)) {
      failures.push({
        ratio: Math.round(value * 100) / 100,
        ink: hex(painted),
        ground: hex(ground.colour),
        text: (element.value || element.textContent).trim().replace(/\s+/g, " ").slice(0, 60),
        element: element.tagName.toLowerCase() + "." + [...element.classList].join("."),
      });
    }
  }
  return failures;
}
"""

#: The dark theme's known text gaps, as painted colours, because that is what
#: the page reports. `--text-muted` (#7d8b99) on `--surface-elevated` (#1e242b),
#: 4.49:1, was here until docs/adr/0147's amendment of 2026-10-09; empty since,
#: like `DARK_GAPS` in tests/test_text_contrast.py.
DARK_GAPS: set[tuple[str, str]] = set()


def _open_matter(page, base_url: str, title: str, tab: str = "") -> None:
    page.goto(f"{base_url}/teemad/?olek=koik&q={title.split()[0]}")
    link = page.get_by_role("link", name=title, exact=False).first
    page.goto(f"{base_url}{link.get_attribute('href')}")
    page.wait_for_load_state("networkidle")
    if tab:
        page.get_by_role("link", name=tab).first.click()
        page.wait_for_load_state("networkidle")


def _go(path: str):
    def visit(page, base_url):
        page.goto(f"{base_url}{path}")
        page.wait_for_load_state("networkidle")

    return visit


def _then(first, *actions):
    def visit(page, base_url):
        first(page, base_url)
        for action in actions:
            action(page)
            page.wait_for_timeout(250)

    return visit


#: The surfaces of the working day, and the overlays opened on them.
SURFACES = {
    "minu-asjad": _go("/minu-asjad/"),
    "minu-asjad-rida": _then(
        _go("/minu-asjad/"), lambda p: p.locator(".rowmenu__trigger").first.click()
    ),
    "veel": _then(_go("/minu-asjad/"), lambda p: p.locator(".topnav__trigger").click()),
    "otsingu-soovitused": _then(
        _go("/minu-asjad/"),
        lambda p: p.locator("#global-search").press_sequentially("sünteetiline", delay=20),
        lambda p: p.wait_for_selector("#global-search-results:not([hidden])"),
    ),
    "osakond": _go("/osakond/"),
    "teemad": _go("/teemad/?olek=koik"),
    "teemad-filter": _then(_go("/teemad/"), lambda p: p.locator(".filterpanel__trigger").click()),
    "teema": lambda page, base_url: _open_matter(page, base_url, OPEN_TITLE),
    "teema-dokumendid": lambda page, base_url: _open_matter(
        page, base_url, OPEN_TITLE, "Dokumendid"
    ),
    "teema-suletud": lambda page, base_url: _open_matter(
        page, base_url, "Lõpetatud sünteetiline teema"
    ),
    # A ticked `Õigusakt` dims the `Hetkeseis` chips it does not normally take
    # (docs/adr/0130 §4): the quietest text the application draws.
    "uus-teema-hetkeseis": _then(
        _go("/teemad/uus/"),
        lambda p: p.get_by_role("radio", name="Seadus", exact=True).check(),
        lambda p: p.wait_for_selector(".chip--atypical"),
    ),
    "uus-teema-viga": _then(
        _go("/teemad/uus/"),
        lambda p: p.locator("form.createform").evaluate("form => form.noValidate = true"),
        lambda p: p.get_by_role("button", name="Salvesta", exact=True).click(),
        lambda p: p.wait_for_load_state("networkidle"),
    ),
    "statistika": _go("/statistika/"),
    "otsing": _go("/otsing/?q=s%C3%BCnteetiline"),
    "uuendused": _go("/uuendused/"),
}


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_every_piece_of_text_meets_aa(page, base_url, theme):
    page.add_init_script(
        f"try {{ window.localStorage.setItem('juristid-theme', '{theme}') }} catch (e) {{}}"
    )
    page.goto(f"{base_url}/konto/arendus-sisselogimine/")
    found: dict[str, list[dict]] = {"sisselogimine": page.evaluate(AUDIT)}
    sign_in(page, base_url, SANDRA)

    for name, visit in SURFACES.items():
        visit(page, base_url)
        assert page.evaluate("() => document.documentElement.dataset.theme") == theme, name
        found[name] = page.evaluate(AUDIT)

    allowed = DARK_GAPS if theme == "dark" else set()
    failing = {
        name: [f for f in failures if (f["ink"], f["ground"]) not in allowed]
        for name, failures in found.items()
    }
    failing = {name: failures for name, failures in failing.items() if failures}
    assert not failing, f"{theme}: text under AA:\n" + "\n".join(
        f"  {name}: {f['ratio']}:1 {f['ink']} on {f['ground']} {f['element']} «{f['text']}»"
        for name, failures in failing.items()
        for f in failures
    )
