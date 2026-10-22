"""Statistika asks the archive's own question before it counts the archive.

The opinion archive has one reader rule, `may_read_archive`
(app/legacy_import/opinion_access.py), and the browse page, the detail page, the
download, the Arvamused workspace tab and the related-material suggestions all
ask it. Statistika did not: its selectors queried `OpinionArchiveItem` directly,
so a reader who may not open a single archive letter could still read how many
there are, how many distinct files, how many reached a Matter and how they fall
by year (csf_a21a2d1c9c6d746327ed27e9, docs/adr/0175). The module docstring of
`opinion_access` already says why that is not a lesser disclosure: «a reader who
may see the coverage figures can infer the corpus».

So every selector that reads the archive is registered here with
:func:`reads_opinion_archive`, and the registration *is* the guard: the wrapped
selector refuses before it runs a query for anybody `may_read_archive` refuses.
`services.compute_many` asks :func:`withheld` first and leaves such a metric off
the page altogether — a section that is not there, never a zero that reads as a
measurement — and a surface that asked `services.compute` for one directly gets
:class:`ArchiveMetricWithheld`, a ``PermissionDenied``, rather than a number.

One decision, asked in one place. The archive metrics are not narrowed per row:
the archive has no Matter to inherit a restriction from, so the only honest
answer is the all-or-nothing one the archive pages already give.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import TYPE_CHECKING

from django.core.exceptions import PermissionDenied

from app.legacy_import.opinion_access import may_read_archive

if TYPE_CHECKING:
    from app.reporting.context import ReportingContext
    from app.reporting.metric_types import MetricResult

#: What a refused direct call says. Never rendered to a reader: the pages leave
#: the metric out instead of asking for it.
ARCHIVE_WITHHELD = "Arvamuste arhiivi näitajad on ainult arhiivi lugejale."

Selector = Callable[["ReportingContext"], "MetricResult"]

#: Every selector that reads the opinion archive, registered by the decorator.
_ARCHIVE_SELECTORS: set[Selector] = set()


class ArchiveMetricWithheld(PermissionDenied):
    """An archive metric was asked for by somebody who may not read the archive."""


def may_report_archive(context: ReportingContext) -> bool:
    """The archive's own reader rule, asked of this report's viewer."""
    return may_read_archive(context.viewer)


def reads_opinion_archive(selector: Selector) -> Selector:
    """Mark a selector as reading the archive, and refuse it to non-readers.

    The refusal comes before the selector body, so nothing about the corpus is
    even queried on behalf of somebody who may not read it.
    """

    @wraps(selector)
    def guarded(context: ReportingContext) -> MetricResult:
        if not may_report_archive(context):
            raise ArchiveMetricWithheld(ARCHIVE_WITHHELD)
        return selector(context)

    _ARCHIVE_SELECTORS.add(guarded)
    return guarded


def reads_archive(selector: Selector) -> bool:
    """Whether this selector was registered as reading the archive."""
    return selector in _ARCHIVE_SELECTORS
