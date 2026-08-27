"""The ADR directory is an ordered record, so its ordering is checked.

Two small tests, and they exist because of a failure mode Git cannot see. Two
branches each add `docs/adr/00NN-*.md` with the same number and a different slug;
the files do not overlap, so there is no conflict, both merge cleanly, and `main`
ends up with two decisions claiming to be number 39. Every cross-reference
elsewhere in the repository — and there are dozens, in code comments as often as
in prose — then names a number that identifies two documents.

It happened on this branch. SEARCH-001 wrote `0039-search-index-freshness.md`
while `0039-retiring-minu-tiim.md` was in flight on another, and nothing but a
human reading two pull requests side by side would have caught it. That is what
a test is for.

The same round found the quieter half: a record can be written, referenced from
code, and never appear in the index a reader actually consults. So the index is
checked for completeness too.

Only `docs/adr/README.md`'s **table** is read. The "Stage coverage" list below it
groups records into ranges and deliberately names some of them twice; it is
prose about the records rather than the index of them.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from django.conf import settings

ADR = Path(settings.BASE_DIR) / "docs" / "adr"

#: `0041-search-index-freshness.md` → `0041`. Anything in the directory that is
#: not numbered — `README.md` — is not an ADR and is not this test's business.
_NUMBERED = re.compile(r"^(\d{4})-.+\.md$")

#: A row of the index table: `| [0041](0041-search-index-freshness.md) | … |`.
_INDEX_ROW = re.compile(r"^\|\s*\[(\d{4})\]\(([^)]+)\)", re.MULTILINE)


def _records() -> dict[str, list[str]]:
    """Every numbered file in the directory, grouped by the number it claims."""
    found: dict[str, list[str]] = defaultdict(list)
    for path in sorted(ADR.glob("*.md")):
        match = _NUMBERED.match(path.name)
        if match:
            found[match.group(1)].append(path.name)
    return dict(found)


def _index() -> list[tuple[str, str]]:
    text = (ADR / "README.md").read_text(encoding="utf-8")
    return _INDEX_ROW.findall(text)


def test_there_are_decision_records_to_check() -> None:
    """Guards the guards: a glob or a regex that stopped matching passes in silence."""
    assert len(_records()) >= 30
    assert len(_index()) >= 30


def test_no_two_decision_records_claim_the_same_number() -> None:
    """Parsed generically, so the check outlives every ADR it was written for."""
    collisions = {number: names for number, names in _records().items() if len(names) > 1}
    assert not collisions, "\n".join(
        f"ADR {number} is claimed by {', '.join(sorted(names))}"
        for number, names in sorted(collisions.items())
    )


def test_the_index_names_every_record_exactly_once() -> None:
    """Both directions, because they fail differently.

    A record missing from the table is one a reader cannot find from the place
    they look decisions up — which is how SEARCH-001's own ADR was left, listed
    under stage coverage and absent from the index. A row naming a file that is
    not there is a link that 404s in the repository browser.
    """
    listed = [number for number, _ in _index()]
    duplicates = sorted({number for number in listed if listed.count(number) > 1})
    assert not duplicates, f"listed more than once: {', '.join(duplicates)}"

    written = set(_records())
    missing = sorted(written - set(listed))
    assert not missing, f"written but not in the index table: {', '.join(missing)}"

    for number, target in _index():
        assert (ADR / target).exists(), f"the index links ADR {number} to a missing {target}"
