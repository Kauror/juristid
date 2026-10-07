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


@pytest.mark.parametrize("label", ["Vastutaja", "Hetkeseis"])
def test_a_business_select_does_not_submit_itself(signed_in, normal_matter, label):
    html = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk})
    ).content.decode()

    select = _select(html, f'aria-label="{label}"')
    assert "data-autosubmit" not in select
    # And the decision it waits for is on the page.
    assert "Salvesta vastutaja muudatus" in html
    assert "Salvesta hetkeseisu muudatus" in html


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
