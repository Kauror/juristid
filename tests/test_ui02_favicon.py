"""UI-02: the browser icon is the Chamber's round emblem, on every page.

The icon used to be `koda-logo-negative.png` — the white horizontal wordmark
(seal, flag and lettering, 1372x466) squeezed into a square tab slot, white on
transparent, so it was a smudge on a dark tab and nothing at all on a light
one, and it was not the mark the top bar shows. These hold the replacement:

* every document that opens a page carries one set of icon links, and nothing
  else in the templates declares an icon;
* each link resolves to an existing file cut from `koda-logo-round.png` — the
  bar's own artwork — at the size the link declares;
* the sign-in pages, an application page, an error page and `/favicon.ico`
  all lead to those files.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from PIL import Image

from tests import factories
from tests.admin_session import apply_admin_gate, choose, pass_gate
from tools import brand_favicons

ROOT = Path(settings.BASE_DIR)
TEMPLATES = ROOT / "templates"
BRAND = ROOT / "static" / "brand"
PARTIAL = "components/favicon_links.html"
LINKS = (TEMPLATES / PARTIAL).read_text(encoding="utf-8")

ICON_LINK = re.compile(r'<link\s[^>]*rel="(?:icon|apple-touch-icon|shortcut icon)"[^>]*>')
STATIC_HREF = re.compile(r"""href="\{% static '([^']+)' %\}\"""")


def _declared() -> list[tuple[str, str]]:
    """(static path, sizes attribute) for every icon link in the partial."""
    found = []
    for tag in ICON_LINK.findall(LINKS):
        href = STATIC_HREF.search(tag)
        assert href, f"icon link without a {{% static %}} href: {tag}"
        sizes = re.search(r'sizes="([^"]+)"', tag)
        found.append((href.group(1), sizes.group(1) if sizes else ""))
    return found


def _document_roots() -> set[str]:
    return {
        path.relative_to(TEMPLATES).as_posix()
        for path in TEMPLATES.rglob("*.html")
        if re.search(r"<head[\s>]", path.read_text(encoding="utf-8"))
    }


# -- the templates --------------------------------------------------------------------


def test_every_document_that_opens_a_page_includes_the_one_set_of_icons():
    roots = _document_roots()
    assert roots == {"base.html", "error_page.html"}, roots
    for name in roots:
        head = (TEMPLATES / name).read_text(encoding="utf-8").split("</head>")[0]
        assert head.count(f'{{% include "{PARTIAL}" %}}') == 1, name


def test_no_other_template_declares_an_icon_of_its_own():
    """One place, so a second, stale icon cannot win in some corner of the app."""
    elsewhere = [
        path.relative_to(TEMPLATES).as_posix()
        for path in TEMPLATES.rglob("*.html")
        if path.relative_to(TEMPLATES).as_posix() != PARTIAL
        and ICON_LINK.search(path.read_text(encoding="utf-8"))
    ]
    assert elsewhere == []


def test_every_sign_in_page_reaches_the_icons_through_base_html():
    """The door, the persona page, the development and personal sign-in pages."""
    extends = re.compile(r'{%\s*extends\s+"([^"]+)"')
    pages = 0
    for path in sorted((TEMPLATES / "accounts").rglob("*.html")):
        found = extends.search(path.read_text(encoding="utf-8"))
        if not found:
            continue  # partials and e-mail bodies open no page
        pages += 1
        chain = [found.group(1)]
        while chain[-1] not in {"base.html", "error_page.html"}:
            parent = extends.search((TEMPLATES / chain[-1]).read_text(encoding="utf-8"))
            assert parent, f"{path.name} extends {chain}, which opens no document of its own"
            chain.append(parent.group(1))
    assert pages >= 5  # the door, the persona page, dev and personal sign-in, …


# -- the files ------------------------------------------------------------------------


def test_every_icon_link_names_a_round_emblem_file_that_exists():
    declared = _declared()
    rels = re.findall(r'rel="([^"]+)"', LINKS)
    assert "icon" in rels and "apple-touch-icon" in rels
    for path, _sizes in declared:
        assert path.startswith("brand/koda-round-"), path
        assert (ROOT / "static" / path).is_file(), path
    # The wordmark is not an icon any more, anywhere.
    assert "koda-logo-negative" not in LINKS


def test_every_file_is_the_size_its_link_declares():
    for path, sizes in _declared():
        declared = {tuple(int(n) for n in size.split("x")) for size in sizes.split()}
        with Image.open(ROOT / "static" / path) as image:
            if image.format == "ICO":
                actual = set(image.info["sizes"])
            else:
                actual = {image.size}
        assert actual == declared, (path, actual, declared)


def test_the_icons_are_cut_from_the_bars_own_emblem_not_redrawn():
    """Each file matches what the generator makes from `koda-logo-round.png`.

    Compared as pixels, with a tolerance, so a Pillow upgrade that changes a
    byte of PNG compression does not fail it — but a different picture does.
    """
    expected = brand_favicons.render()
    assert set(expected) == {path.removeprefix("brand/") for path, _ in _declared()}
    for name, data in expected.items():
        with Image.open(io.BytesIO(data)) as want, Image.open(BRAND / name) as have:
            for size in sorted(want.info.get("sizes", {want.size})):
                if want.format == "ICO":
                    want.size = size
                    have.size = size
                a = want.convert("RGBA").tobytes()
                b = have.convert("RGBA").tobytes()
                assert len(a) == len(b), name
                drift = sum(abs(x - y) for x, y in zip(a, b, strict=True)) / len(a)
                assert drift < 2.0, f"{name} {size}: mean drift {drift:.2f}/255"


def test_the_generator_reads_only_the_round_emblem():
    assert brand_favicons.SOURCE == BRAND / "koda-logo-round.png"
    source = Path(brand_favicons.__file__).read_text(encoding="utf-8")
    assert "koda-logo-negative" not in source


def test_the_icon_is_the_emblem_on_its_paper_with_transparent_corners():
    """Legible on a light and a dark tab: ink on white inside the ring, nothing outside it."""
    with Image.open(BRAND / brand_favicons.png_name(48)) as image:
        rgba = image.convert("RGBA")
    corners = [rgba.getpixel(xy) for xy in ((0, 0), (47, 0), (0, 47), (47, 47))]
    assert all(pixel[3] == 0 for pixel in corners), corners
    assert rgba.getpixel((24, 24))[3] == 255
    with Image.open(BRAND / brand_favicons.TOUCH_NAME) as touch:
        assert touch.mode == "RGB" and touch.size == (180, 180)


# -- the rendered pages ---------------------------------------------------------------


def _icons_in(body: str) -> set[str]:
    return set(re.findall(r'<link\s[^>]*rel="(?:icon|apple-touch-icon)"[^>]*href="([^"]+)"', body))


def _assert_round_icons(body: str) -> None:
    hrefs = _icons_in(body)
    assert len(hrefs) == len(_declared()), hrefs
    for href in hrefs:
        assert "/brand/koda-round-" in href, href
        assert "koda-logo-negative" not in href


@pytest.mark.django_db
def test_the_sign_in_pages_carry_the_round_icons(settings):
    apply_admin_gate(settings)
    client = Client()
    for name in ("accounts:shared_gate", "accounts:choose_persona"):
        response = client.get(reverse(name))
        if response.status_code == 302:
            response = client.get(response["Location"])
        assert response.status_code == 200, name
        _assert_round_icons(response.content.decode())


@pytest.mark.django_db
def test_the_development_sign_in_page_carries_the_round_icons(settings):
    settings.DEV_LOGIN_ENABLED = True
    settings.AUTH_MODE = "none"
    response = Client().get(reverse("accounts:dev_login"))
    assert response.status_code == 200
    _assert_round_icons(response.content.decode())


@pytest.mark.django_db
def test_an_application_page_carries_the_round_icons(settings):
    apply_admin_gate(settings)
    client = pass_gate(Client())
    choose(client, factories.UserFactory())
    response = client.get(reverse("matters:my_work"))
    assert response.status_code == 200
    _assert_round_icons(response.content.decode())


@pytest.mark.django_db
def test_an_error_page_carries_the_round_icons(client, specialist):
    client.force_login(specialist)
    response = client.get("/ei-ole-sellist-lehte/")
    assert response.status_code == 404
    _assert_round_icons(response.content.decode())


@pytest.mark.django_db
def test_favicon_ico_leads_to_the_round_emblem(client):
    response = client.get("/favicon.ico")
    assert response.status_code == 302  # temporary, so a cached old target is asked again
    assert "/brand/koda-round-favicon" in response["Location"]
    assert response["Location"].endswith(".ico")
