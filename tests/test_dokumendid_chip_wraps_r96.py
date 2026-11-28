"""The import chip in Dokumendid wraps instead of widening the page (r96 final UI check).

A page with three import states reads «1 fail imporditud · 1 fail on veel
importimata · 1 fail kopeerimine ebaõnnestus». `.statechip` is `nowrap`, so on a
375 px screen the chip was 367 px wide inside a 335 px column and the whole page
scrolled sideways (browser-measured, every text size). The row's chip now wraps.
"""

from __future__ import annotations

import re
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent / "static" / "css" / "app.css").read_text(
    encoding="utf-8"
)


def _rule(selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert match, f"no rule for {selector}"
    return match.group(1)


def test_the_chip_in_a_file_list_may_wrap_and_never_exceeds_its_row():
    body = _rule(".filelist .statechip")
    assert "white-space: normal" in body
    assert "max-width: 100%" in body


def test_the_chip_elsewhere_is_unchanged():
    assert "white-space: nowrap" in _rule(".statechip")
