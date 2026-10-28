"""Every remembered block in a template names a registered key (docs/adr/0194).

The registry is the boundary the save endpoint enforces, so a template asking
for a key that is not in it would draw a block that can never be saved — and
would render closed, whatever its intended default. Read from the template
source, so it needs no database and catches a typo before a page is rendered.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.accounts.disclosures import DISCLOSURES

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
LITERAL = re.compile(r"""\{%\s*disclosure\s+["']([^"']+)["']\s*%\}""")
KEY_SHAPE = re.compile(r"^[a-z_]+(\.[a-z_]+)+$")


def _literal_keys() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in TEMPLATES.rglob("*.html"):
        for key in LITERAL.findall(path.read_text(encoding="utf-8")):
            found.setdefault(key, []).append(str(path.relative_to(TEMPLATES)))
    return found


def test_templates_use_remembered_blocks() -> None:
    """Guards the guard: a regex that stopped matching would pass in silence."""
    assert len(_literal_keys()) >= 3


def test_every_literal_key_is_registered() -> None:
    unknown = {key: where for key, where in _literal_keys().items() if key not in DISCLOSURES}
    assert not unknown, f"unregistered disclosure keys: {unknown}"


def test_keys_are_semantic_and_bounded() -> None:
    """Dotted lowercase words: never a position, a number or an id."""
    for key, disclosure in DISCLOSURES.items():
        assert key == disclosure.key
        assert KEY_SHAPE.match(key), key
        assert len(key) <= 80


def test_a_remembered_block_does_not_also_hard_code_open() -> None:
    """`open` comes from the tag; a literal one would override the reader's choice."""
    offenders = []
    for path in TEMPLATES.rglob("*.html"):
        for tag in re.findall(
            r"<details[^>]*\{%\s*disclosure[^>]*>", path.read_text(encoding="utf-8")
        ):
            without_tag = re.sub(r"\{%.*?%\}", "", tag)
            if re.search(r"\sopen(\s|>|$)", without_tag):
                offenders.append(f"{path.relative_to(TEMPLATES)}: {tag}")
    assert not offenders, offenders
