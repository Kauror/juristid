"""No browser file may name a Matter after a word another file counts rows for.

The browser world is one seeded database per shard, shared by every file in it
and never reset between them (`ci_sharding.py`). A Matter one file creates is
therefore in the register when the next file runs — and
`e2e/test_register_search.py` types `Tavaline` and asserts **exactly one** row,
four times over. `e2e/test_lawyer_classification_round.py` filed «Tavaline
saabunud eelnõu …» and made that two, in any partition that put the two files
in one shard. It never did, so CI stayed green and the dependency stayed
latent; the files sort `l` before `r`, so wherever they do meet, the polluting
one goes first.

Pure — no database, no browser — so it runs in every shard of the fast suite
and fails before the browser job has to be lucky. Two checks, deliberately
small:

* the helper refuses a reserved prefix at the moment a test asks for a title;
* and no `unique_title("…")` literal anywhere in `e2e/` carries one, which is
  the same rule asked of the source rather than of the run.

Neither sees a title filled straight into `#id_title` as a literal. That is a
known limit and not worth a parser: the rule is restated as prose at the two
places where a browser file does that (`e2e/test_next_step_arrival.py`,
`e2e/test_work_surfaces_review.py`).
"""

from __future__ import annotations

import pathlib
import re

import pytest

from e2e.titles import RESERVED_REGISTER_WORDS, unique_title

E2E = pathlib.Path(__file__).resolve().parent.parent / "e2e"

#: `unique_title("…")` with a plain literal argument. A computed prefix is not
#: matched here and is caught by the helper itself instead.
CALL = re.compile(r"""unique_title\(\s*(["'])(?P<prefix>[^"']*)\1\s*\)""")


def test_the_helper_refuses_a_reserved_prefix():
    for word in RESERVED_REGISTER_WORDS:
        with pytest.raises(ValueError, match=word):
            unique_title(f"{word} lause")


def test_the_helper_still_builds_an_ordinary_unique_title():
    first = unique_title("Liigituse saabunud eelnõu")
    second = unique_title("Liigituse saabunud eelnõu")
    assert first.startswith("Liigituse saabunud eelnõu ")
    assert first != second


def test_no_browser_file_asks_for_a_reserved_prefix():
    offenders = [
        f"{path.name}: {match.group('prefix')!r}"
        for path in sorted(E2E.glob("test_*.py"))
        for match in CALL.finditer(path.read_text(encoding="utf-8"))
        for word in RESERVED_REGISTER_WORDS
        if word.casefold() in match.group("prefix").casefold()
    ]
    assert not offenders, (
        "these browser files would file a Matter answering to a word "
        f"e2e/test_register_search.py counts rows for: {offenders}"
    )
