"""Which selects commit themselves, and which wait for `Salvesta` (ENG-035).

A business write — reassigning the Teema, moving its stage — is committed by its
own `Salvesta` only, because a keyboard walk through a closed select fires
`change` at every option it passes on Chromium/Windows. A read-only filter keeps
`data-autosubmit`, whose listener in `static/js/app.js` commits a pointer choice
at once and a keyboard walk on Enter or on leaving the control. The browser half
is `e2e/test_select_commit.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse

pytestmark = pytest.mark.django_db


def _select(html: str, attribute: str) -> str:
    match = re.search(rf"<select[^>]*{attribute}[^>]*>", html)
    assert match, f"no select carrying {attribute}"
    return match.group(0)


def test_a_business_select_does_not_submit_itself(signed_in, normal_matter):
    html = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk})
    ).content.decode()

    select = _select(html, 'aria-label="Vastutaja"')
    assert "data-autosubmit" not in select
    # And the decision it waits for is on the page.
    assert "Salvesta vastutaja muudatus" in html


def test_hetkeseis_is_a_menu_of_buttons_not_a_select(signed_in, normal_matter):
    """Since 2026-10-12 the header's `Hetkeseis` is a list of submit buttons
    (docs/adr/0156): a keyboard moves between them without firing anything, and
    only a pressed button saves — ENG-035's rule kept by a different control
    (superseding the `Hetkeseis` case this test used to parametrize)."""
    html = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk})
    ).content.decode()
    slot = html.split('id="teema-hetkeseis"', 1)[1].split("</details>", 1)[0]

    assert "<select" not in slot
    assert 'type="submit" name="stage"' in slot
    assert "data-autosubmit" not in slot


def test_the_document_filters_still_submit_themselves(signed_in, normal_matter):
    html = signed_in.get(
        reverse("matters:matter_documents", kwargs={"pk": normal_matter.pk})
    ).content.decode()

    # `Roll — kõik` left the toolbar on 2026-10-07; `Aasta` is the filter left.
    assert 'name="roll"' not in html
    assert "data-autosubmit" in _select(html, 'name="aasta"')


def test_the_listener_tells_a_keyboard_walk_from_a_choice():
    """The one listener every `data-autosubmit` control shares, read as text.

    Not a behaviour test — `e2e/test_select_commit.py` is — but the pin that the
    listener still distinguishes the two, so a later «simplify it back to
    `change`» is a failing test rather than a quiet regression.
    """
    script = (Path(settings.BASE_DIR) / "static" / "js" / "app.js").read_text(encoding="utf-8")
    start = script.index('querySelectorAll("[data-autosubmit]")')
    block = script[start : start + 2500]

    assert '"keydown"' in block
    assert '"pointerdown"' in block
    assert '"blur"' in block
    assert 'event.key === "Enter"' in block
