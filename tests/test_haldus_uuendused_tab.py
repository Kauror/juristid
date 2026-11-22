"""Uuendused is a Haldus tab (owner, 2026-11-21), and keeps its link under *Rakendus*."""

from __future__ import annotations

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def _strip(body: str) -> str:
    start = body.index('aria-label="Halduse osad"')
    return body[start : body.index("</nav>", start)]


def test_the_release_notes_are_a_marked_haldus_tab(client, specialist):
    client.force_login(specialist)

    body = client.get(reverse("core:release_notes")).content.decode()

    strip = _strip(body)
    assert (
        f'aria-current="page"\n           href="{reverse("core:release_notes")}">Uuendused' in strip
    )
    assert "Ülevaade" in strip
    # Juristid / Lovable stay the page's own two sections under the strip.
    assert 'aria-label="Uuenduste osad"' in body


def test_the_overview_offers_the_tab_and_keeps_the_rakendus_link_once(client, specialist):
    client.force_login(specialist)

    body = client.get(reverse("haldus:index")).content.decode()

    assert ">Uuendused</a>" in _strip(body)
    others = body[body.index('id="halduse-osad"') : body.index('id="rakendus"')]
    assert "Uuendused" not in others
    rakendus = body[body.index('id="rakendus"') :]
    assert f'href="{reverse("core:release_notes")}">Uuendused' in rakendus
