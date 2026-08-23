"""The responsibility dimension: one precedence rule, read from one place.

Every statistic that groups Matters by a lawyer asks this module for the label,
rather than each metric restating how a name is chosen. The rule has three
steps and the order is load-bearing:

1. **The register's own ``VASTUTAJA`` text**, when the Matter carries a
   ``CurrentRegisterState``. This is the source fact and it outranks the
   resolved account on purpose. Colleagues named in the register have no login
   here, and grouping by ``Matter.owner`` would file them under *Määramata* —
   discarding the one thing the register is certain about. Inventing an account
   to hold them would be worse (Stage-2F owner resolver, and the same reasoning
   as ``app.matters.dashboard.source_responsibility``).
2. **The canonical ``Matter.owner``**, for genuinely native work that has no
   register row behind it. Current staff appear under their display name.
3. **``Määramata``**, only when neither exists. A source that names somebody
   never lands here.

Two things this module deliberately does not do.

**It never writes.** ``Matter.owner`` stays canonical; nothing here overwrites
it, proposes a mapping, or creates a User.

**It never links.** The register filters on the *resolved* owner and these
counts are grouped by the source name, so a drill-through would open a list
that disagrees with the number above it. An absent link is better than a link
that lies, which is the rule every other segment on these pages keeps by
linking exactly (brief 58).

Counting is inventory. A count of Matters is not a measure of effort, output or
standing, and nothing here sorts people by it (master specification 18.8).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from django.db.models import QuerySet

from app.matters.models import Matter
from app.reporting.metric_types import Matrix, MatrixCell, MatrixRow, Segment
from app.reporting.selectors.base import grouped_count

#: What a Matter with neither a source name nor an owner is called. One
#: constant, because three metrics and two tests have to agree on the string.
UNASSIGNED_LABEL = "Määramata"

#: Named columns a matrix shows before the tail is folded. Twelve is what the
#: register's own history needs and what a laptop can read; beyond that the
#: fold is labelled and counted rather than silent, and every name still
#: appears in full in the responsibility composition beside the matrix.
MATRIX_COLUMN_LIMIT = 12

#: The database paths the label is derived from. Named once so the ``values()``
#: call, the folding and the tests cannot drift apart.
SOURCE_PATH = "current_register_state__owner_raw"
OWNER_PATH = "owner__display_name"


def label_for(source_name: str | None, owner_name: str | None) -> str:
    """The precedence, as one expression. See the module docstring."""
    source = (source_name or "").strip()
    if source:
        return source
    owner = (owner_name or "").strip()
    return owner or UNASSIGNED_LABEL


#: The folded tail's own column in a wide matrix. Counted and labelled, never
#: dropped: a table that quietly stopped at twelve columns would read as the
#: whole department.
OTHER_COLUMN_LABEL = "Muud vastutajad"

#: Labels that are not somebody's name and always sit at the end, in this
#: order. Sorting them alphabetically among the people would bury *Määramata*
#: in the middle of a row of names.
_TRAILING = (OTHER_COLUMN_LABEL, UNASSIGNED_LABEL)


def _order(labels: set[str]) -> list[str]:
    """Alphabetical, with the grouped and unassigned buckets last.

    Not by count. An ordering by size is a league table however it is
    captioned, and these are inventory counts of files rather than measures of
    anybody's work (brief 51).
    """
    named = sorted(label for label in labels if label not in _TRAILING)
    return [*named, *(label for label in _TRAILING if label in labels)]


# ---------------------------------------------------------------------------
# One dimension
# ---------------------------------------------------------------------------


def tally(queryset: QuerySet[Matter]) -> dict[str, int]:
    """Matters per responsibility label, in one grouped query.

    ``CurrentRegisterState`` is a one-to-one on Matter, so joining it cannot
    duplicate a row — and the count is ``distinct`` anyway, because the
    queryset has already been through the visibility predicate and its
    collaborator join (brief 47, ``app/core/authorization.py``).
    """
    rows = queryset.order_by().values(SOURCE_PATH, OWNER_PATH).annotate(total=grouped_count())
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[label_for(row[SOURCE_PATH], row[OWNER_PATH])] += row["total"]
    return dict(counts)


def segments(queryset: QuerySet[Matter]) -> tuple[Segment, ...]:
    """The tally as chart segments, alphabetical, unassigned last, no links."""
    counts = tally(queryset)
    return tuple(
        Segment(
            label=label,
            value=counts[label],
            # No URL. See the module docstring: the register filters on the
            # resolved owner, and this counts the source name.
            url="",
            is_unknown=label == UNASSIGNED_LABEL,
        )
        for label in _order(set(counts))
    )


# ---------------------------------------------------------------------------
# Two dimensions
# ---------------------------------------------------------------------------


def _fold(columns: list[str], counts: dict[Any, dict[str, int]]) -> tuple[list[str], str]:
    """Keep the widest axis readable without dropping anything silently.

    When more names appear than a table can show, the tail becomes one labelled
    column that says how many names it holds. Which names are folded is decided
    by total records rather than alphabetically, so the column a reader would
    otherwise have to scroll furthest to reach is the one that survives. That is
    a decision about width: the surviving columns are still *displayed*
    alphabetically rather than ranked, and every folded name still appears in
    full in the responsibility composition beside the matrix.
    """
    named = [label for label in columns if label not in _TRAILING]
    if len(named) <= MATRIX_COLUMN_LIMIT:
        return columns, ""

    totals = {label: sum(row.get(label, 0) for row in counts.values()) for label in named}
    kept = set(sorted(named, key=lambda label: (-totals[label], label))[:MATRIX_COLUMN_LIMIT])
    folded = [label for label in named if label not in kept]

    for row in counts.values():
        moved = sum(row.pop(label, 0) for label in folded)
        if moved:
            row[OTHER_COLUMN_LABEL] = row.get(OTHER_COLUMN_LABEL, 0) + moved

    remaining = {*kept, OTHER_COLUMN_LABEL, *(set(columns) & {UNASSIGNED_LABEL})}
    note = (
        f"{len(folded)} vastutaja nime on koondatud veergu „{OTHER_COLUMN_LABEL}“, et "
        "tabel jääks loetavaks. Kõik nimed on eraldi näidatud vastutuse jaotuse graafikul."
    )
    return _order(remaining), note


def matrix(
    *,
    row_header: str,
    rows: list[tuple[Any, str]],
    counts: dict[Any, dict[str, int]],
    unknown_rows: frozenset[Any] = frozenset(),
) -> Matrix:
    """Assemble a two-dimensional count from an already-grouped mapping.

    ``rows`` is an ordered list of ``(row key, row label)``; ``counts`` maps a
    row key to ``{responsibility label: count}``. Everything arithmetic — the
    row totals, the column totals, the grand total — happens here rather than
    in a template, so no two renderings of the same matrix can add up
    differently (brief 54).
    """
    labels: set[str] = set()
    for row in counts.values():
        labels.update(row)
    # `_fold` may move counts between columns, so it both narrows the axis and
    # returns the order the narrowed axis is rendered in.
    columns, folded_note = _fold(_order(labels), counts)

    matrix_rows: list[MatrixRow] = []
    column_totals = [0] * len(columns)

    for key, label in rows:
        row_counts = counts.get(key, {})
        cells = tuple(MatrixCell(value=row_counts.get(column, 0)) for column in columns)
        for index, cell in enumerate(cells):
            column_totals[index] += cell.value
        matrix_rows.append(
            MatrixRow(
                label=label,
                cells=cells,
                total=sum(cell.value for cell in cells),
                is_unknown=key in unknown_rows,
            )
        )

    return Matrix(
        row_header=row_header,
        columns=tuple(columns),
        rows=tuple(matrix_rows),
        column_totals=tuple(column_totals),
        grand_total=sum(column_totals),
        folded_note=folded_note,
    )
