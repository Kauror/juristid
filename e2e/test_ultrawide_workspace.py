"""The workspace is bounded, centred and whole — at any width a monitor has.

Why this is its own file
------------------------
`test_ui_shell.py` asserts the shell's rules on the desktop ladder the product
supports: 1440 down to 1024, the machines the department actually has. This one
asserts the rule above that ladder, and it is a different rule. The shell may
span the viewport. The workspace may not, because past about 1600px more pixels
stop making a work surface better and start making a list row a distance the eye
has to travel: on a 3440px monitor an Ülevaade row put its title at the left
bezel and the stage and owner that qualify it 2800px away, with the facts rail
off against the other bezel (docs/adr/0035).

What a screenshot cannot say
----------------------------
The visual suite locks how the bounded workspace *looks* at 3440. It cannot say
"materially narrower than the viewport", "centred", or "the rail is inside the
workspace" — those are relationships between boxes, and a baseline approves a
broken one as readily as a correct one. They are asserted here.

The other half matters just as much: the bound must not reach down into the
widths people actually work at. `test_the_workspace_does_not_narrow_ordinary_desktop`
is the guard on that, and it is the test that fails if somebody later lowers the
maximum to a number that feels tidy in a stylesheet.

Why the widths are a loop and not a parametrisation
---------------------------------------------------
Every test here signs in, and signing in is a page load — so parametrising over
(surface × width) meant fifty-four sign-ins for assertions that are all about
one page's geometry. On a loaded runner that is where this file starts timing
out, in the fixture, for a reason that has nothing to do with layout. One
sign-in per surface and a loop over the widths costs a third of that, and every
assertion below names the width it failed at, so nothing is lost from the
report. `test_ui_shell.py` already does this where it has two widths to check.
"""

from __future__ import annotations

import pytest

from e2e.conftest import SANDRA, sign_in

pytestmark = pytest.mark.e2e

#: Above the supported ladder: a 1080p monitor, a 1440p one, and the 3440px
#: ultrawide the QA round photographed.
WIDE = [1920, 2560, 3440]

#: The widths the department works at. The bound must be invisible here.
ORDINARY = [1280, 1366, 1440]

#: Every shared-shell surface the brief named, plus the two that use the same
#: primitive and would have drifted unnoticed. `None` for the Matter page: its
#: URL is not fixed, so it is resolved from the register.
SURFACES: list[tuple[str, str | None, str, str | None]] = [
    ("Osakond", "/osakond/", ".page--overview", ".ovbody__rail"),
    ("Minu töö", "/minu-asjad/", ".page--work", ".worklayout2__rail"),
    ("Teemad", "/teemad/", ".page", None),
    ("Statistika", "/statistika/", ".page", None),
    ("Teema", None, ".teema", ".teema .rail"),
    ("Arvamused", "/arvamused/", ".page", None),
    ("Jälgimine", "/jalgimine/tahtajad/", ".page", None),
]

#: Named for the test id, so a failure says which page rather than which index.
SURFACE_IDS = [surface[0] for surface in SURFACES]

#: How far off centre the workspace may sit. A scrollbar is not in the layout
#: viewport Chromium reports, so in practice this absorbs nothing but a
#: sub-pixel rounding of an odd leftover width.
CENTRING_TOLERANCE = 2

#: The workspace has to be *materially* narrower than a wide viewport, not
#: merely narrower. 1920 is the tightest case: a 1600px workspace is 83% of it,
#: so the bound has to bite by then or it is not bounding anything a person
#: would notice.
MAX_SHARE_OF_VIEWPORT = 0.85

#: How much bare monitor has to be left over before "there is an outer margin"
#: is a fair description of what somebody sees.
MINIMUM_OUTER_MARGIN = 100


def document_overflows(page) -> bool:
    return page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )


def resolve(page, base_url: str, path: str | None) -> str:
    """The URL for a surface, resolving the Matter page through the register."""
    if path is not None:
        return f"{base_url}{path}"
    page.goto(f"{base_url}/teemad/?olek=koik")
    page.wait_for_load_state("networkidle")
    link = page.locator(".table__titlelink").first
    assert link.count(), "the register is empty, so there is no Matter page to measure"
    return f"{base_url}{link.get_attribute('href')}"


def open_at(page, url: str, width: int) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(url)
    page.wait_for_load_state("networkidle")


# ---------------------------------------------------------------------------
# Ultrawide: bounded, centred, and nothing hanging outside it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,path,container,rail", SURFACES, ids=SURFACE_IDS)
def test_the_workspace_is_bounded_and_centred(page, base_url, name, path, container, rail):
    """One workspace, materially narrower than the monitor, in the middle of it.

    Both halves are the point. Bounded and left-aligned would leave the same
    dead ocean the QA screenshot showed, just on one side; centred and unbounded
    is what the page did before this branch.
    """
    sign_in(page, base_url, SANDRA)
    url = resolve(page, base_url, path)

    for width in WIDE:
        open_at(page, url, width)

        workspace = page.locator(".app__main").bounding_box()
        assert workspace["width"] <= width * MAX_SHARE_OF_VIEWPORT, (
            f"{name} at {width}px: the workspace is {round(workspace['width'])}px — "
            f"it is still taking the monitor"
        )

        left = workspace["x"]
        right = width - (workspace["x"] + workspace["width"])
        assert abs(left - right) <= CENTRING_TOLERANCE, (
            f"{name} at {width}px: the workspace sits {round(left)}px from the left "
            f"and {round(right)}px from the right"
        )

        # The page's own container, whether it is the centred `.page` or one of
        # the full-bleed work surfaces, has to be inside that box. A negative
        # margin measured against a padding that is no longer there is exactly
        # how a full-bleed page escapes its container, and it is invisible until
        # somebody photographs it.
        content = page.locator(container).first.bounding_box()
        assert content["x"] >= workspace["x"] - 1, (
            f"{name} at {width}px: the content starts outside the workspace"
        )
        assert content["x"] + content["width"] <= workspace["x"] + workspace["width"] + 1, (
            f"{name} at {width}px: the content ends outside the workspace"
        )

        assert not document_overflows(page), f"{name} at {width}px: the document scrolls sideways"


@pytest.mark.parametrize(
    "name,path,container,rail",
    [surface for surface in SURFACES if surface[3]],
    ids=[surface[0] for surface in SURFACES if surface[3]],
)
def test_the_rail_stays_attached_to_the_main_content(page, base_url, name, path, container, rail):
    """The rail reads as part of the workspace, not as furniture by the bezel.

    Asserted as "its right edge is the workspace's right edge", because that is
    what attachment means for a rail that is a bordered panel rather than a
    floating column: it is flush with the content it belongs to, and the empty
    monitor is outside both.
    """
    sign_in(page, base_url, SANDRA)
    url = resolve(page, base_url, path)

    for width in WIDE:
        open_at(page, url, width)

        workspace = page.locator(".app__main").bounding_box()
        panel = page.locator(rail).first.bounding_box()
        workspace_right = workspace["x"] + workspace["width"]

        assert abs((panel["x"] + panel["width"]) - workspace_right) <= 1, (
            f"{name} at {width}px: the rail ends at {round(panel['x'] + panel['width'])}px, "
            f"the workspace ends at {round(workspace_right)}px"
        )
        assert width - workspace_right > MINIMUM_OUTER_MARGIN, (
            f"{name} at {width}px: there is no outer margin, so nothing was bounded"
        )


# ---------------------------------------------------------------------------
# Ordinary desktop: the bound must not be felt here
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,path,container,rail", SURFACES, ids=SURFACE_IDS)
def test_the_workspace_does_not_narrow_ordinary_desktop(
    page, base_url, name, path, container, rail
):
    """1280–1440 keeps every pixel it had.

    The failure this exists to catch is a well-meant correction going too far:
    a maximum lowered to something that reads tidily in a stylesheet takes the
    density out of the machines the department actually has, and no ultrawide
    assertion above would notice.
    """
    sign_in(page, base_url, SANDRA)
    url = resolve(page, base_url, path)

    for width in ORDINARY:
        open_at(page, url, width)

        workspace = page.locator(".app__main").bounding_box()
        assert round(workspace["width"]) == width, (
            f"{name} at {width}px: the workspace narrowed to {round(workspace['width'])}px"
        )
        assert not document_overflows(page), f"{name} at {width}px: the document scrolls sideways"


# ---------------------------------------------------------------------------
# The shell is not the workspace
# ---------------------------------------------------------------------------


def test_the_shell_still_spans_the_viewport(page, base_url):
    """Bounding the work surface must not turn the application into a card.

    The bar and the footer band are shell: they span the monitor, and a hairline
    that stops in mid-air on a 3440px screen reads as a rendering fault rather
    than as a decision.
    """
    sign_in(page, base_url, SANDRA)

    for width in WIDE:
        open_at(page, f"{base_url}/osakond/", width)
        for selector in (".topbar", ".app__footer"):
            box = page.locator(selector).bounding_box()
            assert round(box["width"]) == width, (
                f"{selector} at {width}px is {round(box['width'])}px — "
                f"the shell was constrained too"
            )
            assert round(box["x"]) == 0, (
                f"{selector} at {width}px does not start at the viewport edge"
            )
