"""A relative link in the repository's Markdown names a file that exists.

ADRs, runbooks and the README cross-reference each other by relative path, and
a renamed file leaves every link to it pointing nowhere without any check
failing: Markdown is never imported. One such link survived in ADR 0076 for
seven weeks after ADR 0061 was given its final file name (found in the
maintenance round of 2026-11-04).

Only the path is checked, not a ``#fragment``: heading anchors are computed
differently by every renderer, and a wrong anchor still lands on the right
document. Links inside fenced or inline code are examples, not links, and are
skipped. Nothing here needs a database.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

from django.conf import settings

ROOT = Path(settings.BASE_DIR)

#: Where the repository's own Markdown lives. Walked from the filesystem rather
#: than from `git ls-files`, so the check also runs outside a checkout.
MARKDOWN_ROOTS = ("docs", "deploy", "design", "devtools", "tools", "prompts")
TOP_LEVEL = ("README.md", "AGENTS.md")

_FENCE = re.compile(r"^(```|~~~).*?^\1", re.MULTILINE | re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
#: `[text](target)` and `![alt](target)`, with an optional `"title"`.
_LINK = re.compile(r"\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def _markdown_files() -> list[Path]:
    found = [ROOT / name for name in TOP_LEVEL if (ROOT / name).exists()]
    for directory in MARKDOWN_ROOTS:
        found.extend(sorted((ROOT / directory).rglob("*.md")))
    return found


def _relative_targets(text: str) -> list[str]:
    prose = _INLINE_CODE.sub("", _FENCE.sub("", text))
    targets = []
    for match in _LINK.finditer(prose):
        target = match.group(1)
        if _SCHEME.match(target) or target.startswith("#"):
            continue
        path = unquote(target.split("#", 1)[0])
        if path:
            targets.append(path)
    return targets


def test_there_is_markdown_to_check() -> None:
    """Guards the guard: an empty walk would make the next test vacuous."""
    files = _markdown_files()
    assert len(files) > 100
    assert ROOT / "docs" / "adr" / "README.md" in files


def test_every_relative_markdown_link_names_an_existing_file() -> None:
    broken = []
    for source in _markdown_files():
        for target in _relative_targets(source.read_text(encoding="utf-8")):
            if not (source.parent / target).exists():
                broken.append(f"{source.relative_to(ROOT).as_posix()} -> {target}")
    assert broken == [], "broken relative links:\n  " + "\n  ".join(broken)


def test_the_link_reader_skips_code_and_external_links() -> None:
    text = (
        "[a](present.md) [b](https://example.invalid/x.md) [c](#anchor)\n"
        "`[d](inline.md)`\n"
        "```\n[e](fenced.md)\n```\n"
        '![f](images/g%20h.png "title") [i](other.md#section)\n'
    )
    assert _relative_targets(text) == ["present.md", "images/g h.png", "other.md"]
