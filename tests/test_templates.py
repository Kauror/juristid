"""Properties of the template sources themselves.

These need no database and no rendering. They catch the class of mistake that
survives every rendering test, because a rendering test asserts that the text
it wants is present and never that some other text is absent.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings

TEMPLATE_ROOTS = [Path(directory) for directory in settings.TEMPLATES[0]["DIRS"]]


def _templates() -> list[Path]:
    found = [path for root in TEMPLATE_ROOTS for path in sorted(root.rglob("*.html"))]
    assert found, "No templates were discovered; the fixture is looking in the wrong place."
    return found


@pytest.mark.parametrize("template", _templates(), ids=lambda p: p.name)
def test_a_comment_never_reaches_the_page(template: Path) -> None:
    """`{# #}` is single-line only, so a wrapped one renders as page text.

    Django's lexer matches `{#` and `#}` within one line. A comment that wraps
    is therefore not a comment at all: the reader of the deployed site sees the
    explanation the author was writing for the next developer. It happened on
    the sign-in page, where a note about the shared PIN appeared above the PIN
    field, and it survived CI because nothing asserts what a page does *not*
    say.

    `{% comment %}` has no such limit, so multi-line notes must use it.
    """
    for number, line in enumerate(template.read_text(encoding="utf-8").splitlines(), 1):
        if "{#" in line and "#}" not in line:
            pytest.fail(
                f"{template}:{number} opens a `{{# #}}` comment that does not close on the "
                f"same line, so it will render to the page. Use "
                f"{{% comment %}}...{{% endcomment %}} instead.\n  {line.strip()}"
            )


@pytest.mark.parametrize("template", _templates(), ids=lambda p: p.name)
def test_every_comment_tag_is_closed(template: Path) -> None:
    """An unclosed `{% comment %}` swallows the rest of the template silently."""
    source = template.read_text(encoding="utf-8")
    opened = len(re.findall(r"{%\s*comment\b", source))
    closed = len(re.findall(r"{%\s*endcomment\s*%}", source))
    assert opened == closed, (
        f"{template} has {opened} `{{% comment %}}` and {closed} `{{% endcomment %}}`."
    )
