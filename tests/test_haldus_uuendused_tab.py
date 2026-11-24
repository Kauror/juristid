"""Uuendused is a Haldus page (owner, 2026-11-21), the sidebar's Rakendus item since UX-002."""

from __future__ import annotations

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def _sidebar(body: str) -> str:
    start = body.index('<nav class="haldusnav"')
    return body[start : body.index("</nav>", start)]


def _main(body: str) -> str:
    start = body.index('<div class="haldus__main">')
    return body[start : body.index("<footer", start)]


def test_the_release_notes_are_a_marked_haldus_item(client, specialist):
    client.force_login(specialist)

    body = client.get(reverse("core:release_notes")).content.decode()

    nav = _sidebar(body)
    current = nav[: nav.index(f'href="{reverse("core:release_notes")}">Uuendused')]
    assert current.rstrip().endswith('aria-current="page"')
    assert nav.count('aria-current="page"') == 1
    assert ">Ülevaade</a>" in nav
    # Juristid / Lovable stay the page's own two sections beside the sidebar.
    assert 'aria-label="Uuenduste osad"' in body


def test_the_lovable_section_keeps_the_uuendused_item_current(client, specialist):
    client.force_login(specialist)

    nav = _sidebar(client.get(reverse("core:release_notes_lovable")).content.decode())

    current = nav[: nav.index(f'href="{reverse("core:release_notes")}">Uuendused')]
    assert current.rstrip().endswith('aria-current="page"')


def test_the_overview_links_uuendused_once_in_the_sidebar(client, specialist):
    client.force_login(specialist)

    body = client.get(reverse("haldus:index")).content.decode()

    assert f'href="{reverse("core:release_notes")}">Uuendused</a>' in _sidebar(body)
    assert reverse("core:release_notes") not in _main(body)
