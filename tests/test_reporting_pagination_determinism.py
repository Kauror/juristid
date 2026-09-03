"""The two paginated drill-throughs must serve every row exactly once.

Django's ``Paginator`` slices, and a slice is ``LIMIT``/``OFFSET``. PostgreSQL
only promises to honour the ``ORDER BY`` it was given: rows that tie on every
ordering key may come back in a different relative order on the query for page 2
than they did on the query for page 1. When that happens a reader loses a row
off one page and sees another twice, with nothing on the screen to say so.

Both lists here order on business keys that genuinely repeat. Sent opinions
share a ``sent_at`` — the archive import writes every letter of one day at the
same anchor time — and two historical occurrences can share a section, a page
order and a filename while being different files. So the fix is a final
ordering key that cannot repeat, and these tests are about the served rows
rather than the ``order_by`` tuple: a structural assertion alone would pass
against a queryset that never actually paginates correctly.

Both fixtures are built so that the order the rows are *stored* in disagrees
with the order the selector is supposed to serve them in. That is not decoration.
Both models take a time-sortable ``uuid7`` default, so rows created in sequence
land in the heap in key order, and a fixture that ignored this would pass with
no tie-break at all — the sequential scan would happen to return exactly what
the assertion wanted.

The submissions list needs one more turn of the screw, because ``.distinct()``
hides the defect. ``SELECT DISTINCT ... ORDER BY sent_at DESC`` has to sort by
the rest of the select list too, and ``id`` is the first column in it, so today
the tied rows come back in primary-key order by accident. Nothing promises that:
it is a consequence of a ``DISTINCT`` the audit found for unrelated reasons, and
it would evaporate the day that ``DISTINCT`` moves. So the fixture pairs the
rows on ``created_at`` — the second key the model itself declares — which makes
the order the selector must serve differ from the one the accident produces.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone

from app.core.ids import uuid7
from app.reporting.views import PAGE_SIZE

pytestmark = pytest.mark.django_db

#: Enough rows to cross a page boundary and leave a short second page.
TIED_ROWS = PAGE_SIZE + 10

SUBMISSIONS_URL = "/statistika/arvamused/"
MATERIALS_URL = "/statistika/materjalid/"


def _ascending_keys(count: int) -> list[Any]:
    """``count`` uuid7 primary keys, in ascending order."""
    return sorted(uuid7() for _ in range(count))


def walk_pages(client, url: str, query: dict[str, str]) -> tuple[list[list[Any]], Any]:
    """Every page of a drill-through, as the row identities it actually served."""
    pages: list[list[Any]] = []
    number = 1
    while True:
        response = client.get(url, {**query, "leht": str(number)})
        assert response.status_code == 200, f"{url}?leht={number} -> {response.status_code}"
        page = response.context["page"]
        pages.append([row.pk for row in page.object_list])
        if not page.has_next():
            return pages, response.context["paginator"]
        number += 1
        assert number < 50, "pagination did not terminate"


def assert_pages_are_sound(pages, paginator, expected_order: list[Any]) -> None:
    """The four properties OFFSET pagination is supposed to have.

    ``expected_order`` is only the tied rows this test built. The lists also
    carry the shared world's own rows, which is deliberate — the population must
    stay the one the statistic counted — so the served sequence is filtered down
    to the rows under test before its order is compared.
    """
    served = [pk for page in pages for pk in page]

    assert len(served) == len(set(served)), "a row was served on more than one page"
    assert paginator.count == len(served), "the paginator counted rows it never served"

    missing = set(expected_order) - set(served)
    assert not missing, f"{len(missing)} rows never appeared on any page"

    under_test = set(expected_order)
    assert [pk for pk in served if pk in under_test] == expected_order, (
        "tied rows were not served in the order the selector asks for"
    )


# ---------------------------------------------------------------------------
# Sent submissions
# ---------------------------------------------------------------------------


@pytest.fixture
def tied_submissions(world):
    """``TIED_ROWS`` sent opinions that exhaust every business ordering key.

    All of them share one ``sent_at``: the archive import writes a whole day's
    letters at a fixed anchor time, so this is the corpus's normal shape rather
    than a contrived one. They are then paired on ``created_at`` — two rows per
    value — which leaves the model's declared second key deciding between the
    pairs and row identity deciding inside each pair.

    Returns the identities in the order the list is supposed to serve them:
    newest ``created_at`` first, ascending by key within a pair. Storage order
    is the opposite on both counts.

    One evidence version is shared by all of them. The check constraint asks a
    sent submission to carry its final text, not to carry a unique one, and
    nothing in this selector reads it.
    """
    from app.submissions.enums import SubmissionKind, SubmissionStatus
    from app.submissions.models import Submission
    from tests.synthetic_statistics import _document, _version

    document = _document(world.native_open, "Seotud arvamuste tõend")
    version = _version(document, filename="seotud.pdf", payload="seotud")

    sent_at = timezone.now().replace(microsecond=0)
    keys = _ascending_keys(TIED_ROWS)

    for index, key in enumerate(keys):
        Submission.objects.create(
            id=key,
            matter=world.native_open,
            title=f"Seotud arvamus {index:02d}",
            kind=SubmissionKind.FORMAL_OPINION,
            status=SubmissionStatus.SENT,
            sent_at=sent_at,
            final_version=version,
        )

    # `created_at` is auto_now_add, so each row carries the microsecond its
    # INSERT happened at. Rewriting it in pairs is what makes the required order
    # differ from ascending key order — see the module docstring.
    expected: list[Any] = []
    pairs = [keys[position : position + 2] for position in range(0, len(keys), 2)]
    for index, pair in enumerate(pairs):
        Submission.objects.filter(pk__in=pair).update(created_at=sent_at + timedelta(seconds=index))
    for pair in reversed(pairs):
        expected.extend(pair)
    return expected


def test_every_tied_submission_is_served_exactly_once(client, world, tied_submissions):
    """Sixty opinions sent at the same moment, walked page by page."""
    client.force_login(world.martin)
    pages, paginator = walk_pages(client, SUBMISSIONS_URL, {"periood": "koik"})

    assert len(pages) > 1, "the fixture did not cross a page boundary"
    assert_pages_are_sound(pages, paginator, tied_submissions)


def test_the_same_submission_page_returns_the_same_rows_every_time(client, world, tied_submissions):
    """A reader who reloads page 2 must not be shown a different page 2."""
    client.force_login(world.martin)
    first, _ = walk_pages(client, SUBMISSIONS_URL, {"periood": "koik"})
    second, _ = walk_pages(client, SUBMISSIONS_URL, {"periood": "koik"})
    assert first == second


def test_newest_sent_opinions_still_come_first(client, world, tied_submissions):
    """The business sort is unchanged; the tie-breaks sit underneath it."""
    client.force_login(world.martin)
    response = client.get(SUBMISSIONS_URL, {"periood": "koik"})
    served = list(response.context["page"].object_list)
    sent_at = [row.sent_at for row in served]
    assert sent_at == sorted(sent_at, reverse=True), "newest-first was lost"


def test_a_sent_at_tie_is_broken_by_the_newer_record(client, world):
    """`Submission.Meta.ordering` says `-created_at` after `-sent_at`.

    The drill-through's own `order_by` replaces the model default outright, so
    before this it dropped that second key and left the tie to the planner.
    Restoring it keeps one answer to "which of two opinions sent at the same
    moment comes first" rather than two.
    """
    from app.submissions.enums import SubmissionKind, SubmissionStatus
    from app.submissions.models import Submission
    from tests.synthetic_statistics import _document, _version

    document = _document(world.native_open, "Samaaegsete arvamuste tõend")
    version = _version(document, filename="samaaegne.pdf", payload="samaaegne")
    sent_at = timezone.now().replace(microsecond=0)

    older, newer = (
        Submission.objects.create(
            matter=world.native_open,
            title=title,
            kind=SubmissionKind.FORMAL_OPINION,
            status=SubmissionStatus.SENT,
            sent_at=sent_at,
            final_version=version,
        )
        for title in ("Varem loodud", "Hiljem loodud")
    )
    assert older.created_at < newer.created_at

    client.force_login(world.martin)
    response = client.get(SUBMISSIONS_URL, {"periood": "koik"})
    served = [row.pk for row in response.context["page"].object_list]
    assert served.index(newer.pk) < served.index(older.pk)


# ---------------------------------------------------------------------------
# Historical materials
# ---------------------------------------------------------------------------


@pytest.fixture
def tied_resources(world):
    """``TIED_ROWS`` occurrences that tie on all three of the list's sort keys.

    Same page — so the same section and the same page order — and the same
    filename. A OneNote page that carries thirty pasted ``image001.png`` blocks
    produces exactly this. They are still distinct rows: ``resource_key`` is
    what the page's own catalogue identifies them by, and it is unique per page.
    """
    from app.legacy_import.models import LegacySourceResource
    from tests.synthetic_statistics import _sha

    keys = list(reversed(_ascending_keys(TIED_ROWS)))
    rows = [
        LegacySourceResource.objects.create(
            id=key,
            source_page=world.page_primary,
            resource_key=f"r-tied-{index:03d}",
            original_filename="image001.png",
            source_block_ordinal=8,
            sha256=_sha(f"tied-{index}"),
            size_bytes=64,
            archive_relative_path=f"resources/r-tied-{index:03d}/original/image001.png",
        )
        for index, key in enumerate(keys)
    ]
    return sorted(row.pk for row in rows)


def test_every_tied_resource_is_served_exactly_once(client, world, tied_resources):
    """Sixty occurrences of one filename on one page, walked page by page."""
    client.force_login(world.martin)
    pages, paginator = walk_pages(client, MATERIALS_URL, {"periood": "koik"})

    assert len(pages) > 1, "the fixture did not cross a page boundary"
    assert_pages_are_sound(pages, paginator, tied_resources)


def test_the_same_material_page_returns_the_same_rows_every_time(client, world, tied_resources):
    client.force_login(world.martin)
    first, _ = walk_pages(client, MATERIALS_URL, {"periood": "koik"})
    second, _ = walk_pages(client, MATERIALS_URL, {"periood": "koik"})
    assert first == second


def test_materials_still_read_in_filing_order(client, world, tied_resources):
    """Section, then page order, then filename — the filing structure is intact."""
    client.force_login(world.martin)
    pages, _ = walk_pages(client, MATERIALS_URL, {"periood": "koik"})
    served: list[Any] = []
    for page in pages:
        served.extend(page)

    from app.legacy_import.models import LegacySourceResource

    rows = LegacySourceResource.objects.filter(pk__in=served).select_related("source_page")
    by_pk = {row.pk: row for row in rows}
    filing = [
        (
            by_pk[pk].source_page.source_section,
            by_pk[pk].source_page.page_order,
            by_pk[pk].original_filename,
        )
        for pk in served
    ]
    assert filing == sorted(filing), "the filing order was lost"


# ---------------------------------------------------------------------------
# The final key itself
# ---------------------------------------------------------------------------


def test_both_drill_throughs_end_on_a_unique_ordering_key(world, reporting_context):
    """Guard the tie-break against being tidied away later.

    The behavioural tests above are the real proof. This one names the invariant
    so that removing the last key fails with the reason rather than with a page
    of unequal identity lists.
    """
    from app.reporting.selectors import historical
    from app.reporting.selectors import submissions as submission_selectors

    context = reporting_context(world.martin)
    for label, queryset in (
        ("arvamused", submission_selectors.list_rows(context)),
        ("materjalid", historical.list_rows(context)),
    ):
        order_by = [str(term) for term in queryset.query.order_by]
        assert order_by[-1] in {"pk", "id"}, (
            f"the {label} drill-through no longer ends on row identity: {order_by}"
        )


def test_the_tie_break_adds_no_join(world, reporting_context):
    """Row identity is the model's own column; it must not reach through a relation."""
    from app.reporting.selectors import historical
    from app.reporting.selectors import submissions as submission_selectors

    context = reporting_context(world.martin)
    for label, queryset in (
        ("arvamused", submission_selectors.list_rows(context)),
        ("materjalid", historical.list_rows(context)),
    ):
        final = str(queryset.query.order_by[-1])
        assert "__" not in final, f"the {label} tie-break orders through a relation: {final}"
