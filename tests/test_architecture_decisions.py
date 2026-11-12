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


#: A record's own status line, in either of the header styles the directory
#: uses: `- Status: Accepted …` and `**Status:** accepted — …`.
_STATUS_LINE = re.compile(r"^[-*\s]*\**Status:?\**:?\s*\**\s*([A-Za-z]+)", re.IGNORECASE)

#: The status cell of an index row, the last column of the table.
_INDEX_STATUS = re.compile(r"^\|\s*\[(\d{4})\]\(([^)]+)\)\s*\|.*\|\s*([^|]*)\|\s*$", re.MULTILINE)


def _own_status(path: Path) -> str | None:
    for line in path.read_text(encoding="utf-8").splitlines()[:20]:
        match = _STATUS_LINE.match(line)
        if match:
            return match.group(1).lower()
    return None


def test_a_record_and_the_index_agree_on_its_status() -> None:
    """JARCH-007 (2026-10-17 audit): seven records read «proposed» on their own
    page for weeks after the index had said Accepted, and a reviewer separating
    decided design from open design reads the page, not the index.

    Compared on the first word only, and only where the record states one: the
    index cell carries amendment notes, and older records have no status line.
    """
    text = (ADR / "README.md").read_text(encoding="utf-8")
    rows = _INDEX_STATUS.findall(text)
    assert len(rows) >= 30
    disagree = []
    for number, target, cell in rows:
        own = _own_status(ADR / target)
        word = re.match(r"[A-Za-z]+", cell.strip())
        if own and word and own != word.group(0).lower():
            disagree.append(f"{number}: record says {own!r}, index says {cell.strip()[:40]!r}")
    assert not disagree, "\n".join(disagree)


def test_every_gap_in_the_numbering_is_explained() -> None:
    """A missing number is either a record on another branch or a skipped one;
    the index says which, so nobody goes looking for a decision that is not there."""
    numbers = sorted(int(number) for number in _records())
    gaps = sorted(set(range(1, numbers[-1] + 1)) - set(numbers))
    text = (ADR / "README.md").read_text(encoding="utf-8")
    unexplained = [f"{gap:04d}" for gap in gaps if f"- {gap:04d} —" not in text]
    assert not unexplained, f"unexplained gaps: {', '.join(unexplained)}"


# ---------------------------------------------------------------------------
# Every «ADR NNNN §X» the code cites names a section that exists (LS-02)
# ---------------------------------------------------------------------------
#
# The post-audit round of 2026-11-12 found twenty-one places citing «0213 §10»
# while the record stopped at §9: the code documented a contract nobody could
# read. A citation is checked the way a reader follows it — the number names a
# record in this directory, and the section is one the record gives a number
# or a name to.

#: Where citations are read. Prose under `docs/` cites records in its own way.
_CITING_ROOTS = ("app", "scripts", "templates", "tests")
#: This file quotes citations as examples; they are checked by the self-tests.
_THIS_FILE = "tests/test_architecture_decisions.py"
_CITING_SUFFIXES = frozenset(
    {".py", ".html", ".txt", ".sh", ".toml", ".js", ".yml", ".ps1", ".sql"}
)

#: One cited section: `§10`, `§ 3.3`, `§A4`, `§Failure`, `(§10)`, and a range
#: `§10–§11` / `§10-11`, whose two ends are both checked.
_SECTION = r"\(?§\s?[A-Za-z0-9.]+(?:\s?[–-]\s?§?\s?[A-Za-z0-9.]+)?\)?"
#: `docs/adr/0213 §10`, `ADR 0050 §3`, `0192 §5`, `0213 (§10)`, `0061 §17, §18`,
#: `0143 §A4`, `0072 §Failure`. The number has a leading zero (records stop at
#: 0999), so a year or an amount is never read as one.
_CITATION = re.compile(
    r"(?<![0-9A-Za-z_.])(0\d{3})(?:-[a-z0-9-]+\.md)?"
    rf"((?:\s*(?:,|and|or|ja)?\s*{_SECTION})+)"
)
_CITED_SECTION = re.compile(r"§\s?([A-Za-z0-9.]+)(?:\s?[–-]\s?§?\s?([A-Za-z0-9.]+))?")
_SECTION_ID = r"[A-Z]?\d+(?:\.\d+)*|[A-Z]"
_HEADING = re.compile(r"^#{1,6}\s+(?:ADR\s+\d{4}\s+[—-]\s+)?(?:§\s*)?(.*)$")
#: A numbered section written as a list item with a bold lead — `1. **Any live
#: work stops a closure…**` — the form many records number their decisions in.
#: A plain numbered list item is an enumeration, not a section.
_NUMBERED_LEAD = re.compile(r"^(\d+)\.\s+\*\*")
#: A bold paragraph lead naming a section: `**9.1 Closure reasons…**`,
#: `**New topics.**`.
_BOLD_LEAD = re.compile(r"^(?:[-*]\s+)?\*\*(?:§\s*)?(.*)$")

#: Citations that resolved to nothing when this test arrived, by where they are
#: written, so a *new* bad citation of the same section still fails. Each is to
#: be corrected by whoever knows what it meant (most cite a paragraph by a
#: number the record never gave it). The list only shrinks:
#: `test_the_known_unresolved_citations_are_still_unresolved` fails once one is
#: fixed, so its entry is removed with the fix. Never add to it — fix the
#: citation, or the record.
KNOWN_UNRESOLVED: frozenset[tuple[str, str, str]] = frozenset(
    {
        # 0157 is a record on another branch (the README names the gap).
        ("app/core/admin.py", "0041", "6"),
        ("app/legacy_import/lovable_pilot.py", "0157", "3"),
        ("app/legacy_import/lovable_reading.py", "0157", "3"),
        ("app/matters/intake_extraction.py", "0072", "Failure"),
        ("app/matters/management/commands/run_intake_reader.py", "0072", "Failure"),
        ("app/matters/models.py", "0086", "9"),
        ("app/matters/services.py", "0051", "26"),
        ("app/matters/urls.py", "0096", "6"),
        ("app/matters/urls.py", "0096", "7"),
        ("app/matters/views.py", "0061", "17"),
        ("app/matters/workspace.py", "0061", "17"),
        ("app/notifications/management/commands/generate_commencement_reminders.py", "0198", "8"),
        ("app/submissions/opinions.py", "0061", "14"),
        ("app/submissions/opinions.py", "0061", "5"),
        ("app/submissions/services.py", "0061", "17"),
        ("app/workflow/plan.py", "0133", "11"),
        ("templates/matters/matter_documents.html", "0061", "22"),
        ("templates/matters/matter_documents.html", "0061", "27"),
        ("templates/matters/partials/opinion_manage.html", "0061", "13"),
        ("templates/matters/partials/rail.html", "0052", "14"),
        ("templates/submissions/metadata.html", "0061", "13"),
        ("tests/test_admin_is_not_a_back_door.py", "0041", "6"),
        ("tests/test_arvamused_workspace_retirement.py", "0061", "14"),
        ("tests/test_arvamused_workspace_retirement.py", "0061", "16"),
        ("tests/test_arvamused_workspace_retirement.py", "0061", "6"),
        ("tests/test_child_projection_visibility.py", "0061", "25"),
        ("tests/test_opinions_under_documents.py", "0061", "10"),
        ("tests/test_opinions_under_documents.py", "0061", "26"),
        ("tests/test_opinions_under_documents.py", "0061", "29"),
        ("tests/test_opinions_under_documents.py", "0061", "40"),
        ("tests/test_opinions_under_documents.py", "0061", "9"),
        ("tests/test_opinions_workspace.py", "0061", "22"),
        ("tests/test_qa_round_wording_and_navigation.py", "0061", "15"),
        ("tests/test_qa_round_wording_and_navigation.py", "0061", "16"),
        ("tests/test_simplified_next_action.py", "0052", "13"),
        ("tests/test_teema_human_qa.py", "0061", "6"),
        ("tests/test_teema_rail.py", "0052", "14"),
        ("tests/test_teema_rail.py", "0061", "10"),
        ("tests/test_teema_rail.py", "0061", "18"),
        ("tests/test_teema_rail.py", "0061", "19"),
        ("tests/test_teema_rail.py", "0061", "28"),
        ("tests/test_teema_rail.py", "0061", "6"),
        ("tests/test_teema_redesign.py", "0061", "45"),
        ("tests/test_teema_redesign.py", "0061", "7"),
    }
)


def _section_ids(path: Path) -> set[str]:
    """What a citation of this record may name: heading numbers and letters
    (`## 10.`, `### 4.2`, `## A.`), bold-led numbered items alone and under
    their heading (`1.2`, `A4`), bold paragraph leads (`**9.1 …**`), and the
    first word of a heading or bold lead (`§Amendment`, `§new topics`),
    lower-cased."""
    found: set[str] = set()
    current = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        heading = _HEADING.match(line)
        item = None if heading else _NUMBERED_LEAD.match(line)
        if item:
            found.add(item.group(1))
            if current:
                found.add(f"{current}.{item.group(1)}")
                found.add(f"{current}{item.group(1)}")
            continue
        lead = None if heading else _BOLD_LEAD.match(line)
        text = heading.group(1) if heading else (lead.group(1) if lead else None)
        if text is None:
            continue
        token = re.match(rf"({_SECTION_ID})(?=[.)\s:—]|$)", text)
        if token:
            found.add(token.group(1))
            if heading:
                current = token.group(1)
        word = re.match(r"[A-Za-zÕÄÖÜõäöü]+", text)
        if word:
            found.add(word.group(0).lower())
    return found


def _section_key(section: str) -> str:
    section = section.rstrip(".")
    if section[0].isdigit() or re.fullmatch(r"[A-Z]\d*", section):
        return section
    return section.lower()


def _cited_sections(text: str) -> list[str]:
    """Every section one citation names, both ends of a range included."""
    found: list[str] = []
    for start, end in _CITED_SECTION.findall(text):
        found.append(start.rstrip("."))
        if end:
            found.append(end.rstrip("."))
    return found


def _citations_in(text: str) -> list[tuple[int, str, str]]:
    """(line, record, section as written) for every citation in ``text``."""
    found: list[tuple[int, str, str]] = []
    for number, line in enumerate(text.splitlines(), 1):
        for match in _CITATION.finditer(line):
            for section in _cited_sections(match.group(2)):
                found.append((number, match.group(1), section))
    return found


def _citations() -> list[tuple[str, int, str, str]]:
    """(file, line, record, cited section) for every citation in the code."""
    from django.conf import settings

    base = Path(settings.BASE_DIR)
    found: list[tuple[str, int, str, str]] = []
    for root in _CITING_ROOTS:
        for path in sorted((base / root).rglob("*")):
            if path.suffix not in _CITING_SUFFIXES or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "§" not in text:
                continue
            where = path.relative_to(base).as_posix()
            if where == _THIS_FILE:
                continue  # its own examples, `test_the_citation_reader_…`
            found.extend((where, *citation) for citation in _citations_in(text))
    return found


def _unresolved() -> dict[tuple[str, str, str], list[int]]:
    """(file, record, section) → the lines that cite a section that is not there."""
    paths = {number: ADR / names[0] for number, names in _records().items()}
    sections = {number: _section_ids(path) for number, path in paths.items()}
    missing: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for where, line, record, section in _citations():
        if _section_key(section) not in sections.get(record, set()):
            missing[(where, record, section)].append(line)
    return dict(missing)


def test_the_section_reader_finds_the_forms_the_records_use() -> None:
    """Guards the guard, on records whose shapes differ: a numbered heading, a
    lettered one with bold-led numbered items, a bold amendment lead, a word
    heading; and a plain numbered list is not a section."""
    ids = _section_ids(next(ADR.glob("0213-*.md")))
    assert {"1", "9", "9.1", "9.9", "10", "10.4", "new"} <= ids
    # §4's plain enumeration («1. Deploying the candidate.» under §8) gives no ids.
    assert "99" not in ids and "8.1" not in ids
    assert {"A", "A4", "B"} <= _section_ids(next(ADR.glob("0143-*.md")))
    assert {"1", "7"} <= _section_ids(next(ADR.glob("0152-*.md")))
    assert "amendment" in _section_ids(next(ADR.glob("0054-*.md")))
    assert len(_citations()) > 2000


def test_the_citation_reader_finds_the_forms_the_code_uses() -> None:
    sample = "\n".join(
        [
            "x (docs/adr/0213 §10) y",
            "ADR 0050 §3 and ADR 0198 §2",
            "0192 §5",
            "per 0213 (§10)",
            "docs/adr/0061 §17, §18",
            "0213 §10–§11",
            "0213 §9-10",
            "ADR 0143 §A4, 0072 §Failure",
            "2026 §3 is a year, 2099_7 §1 a reference",
        ]
    )
    assert [(record, section) for _, record, section in _citations_in(sample)] == [
        ("0213", "10"),
        ("0050", "3"),
        ("0198", "2"),
        ("0192", "5"),
        ("0213", "10"),
        ("0061", "17"),
        ("0061", "18"),
        ("0213", "10"),
        ("0213", "11"),
        ("0213", "9"),
        ("0213", "10"),
        ("0143", "A4"),
        ("0072", "Failure"),
    ]


def test_every_cited_adr_section_exists() -> None:
    """Regression: LS-02. The source-authority contract was cited as «0213 §10»
    from twenty-one places in `app/` before the section was written."""
    unresolved = {key: lines for key, lines in _unresolved().items() if key not in KNOWN_UNRESOLVED}
    assert not unresolved, "\n".join(
        f"{where}:{lines[0]}: ADR {record} §{section} does not exist"
        for (where, record, section), lines in sorted(unresolved.items())
    )


def test_the_known_unresolved_citations_are_still_unresolved() -> None:
    """The allowance only shrinks: a fixed citation leaves `KNOWN_UNRESOLVED`."""
    fixed = sorted(KNOWN_UNRESOLVED - set(_unresolved()))
    assert not fixed, f"resolved now, remove from KNOWN_UNRESOLVED: {fixed}"
