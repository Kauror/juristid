"""One restricted Matter, and every aggregate that must not know about it.

The failure this suite exists to catch is not a page that shows a forbidden
row. It is a page that shows the *right* rows above the *wrong* total — because
somebody counted first and filtered afterwards. Nothing on screen looks wrong,
and the number is the disclosure.

The fixture's restricted Matter is built to make that detectable: it is the only
Matter owned by Sandra with a RESTRICTED visibility, and it carries one of
almost everything — a policy area, a stage, a track, an owner, a source
organisation, an overdue action, an entry, a sent submission to an organisation
nothing else writes to, a source page with a file, and a document version. Every
aggregate below is therefore off by a number this file can name if the scoping
is wrong.

The matrix covers the five readers the product actually has:

* the **department scope** — past the shared gate, no persona chosen;
* a **reader**, who is authenticated and outside the legal team (docs/adr/0042);
* the **owner**, who does;
* the **department head**, who reads RESTRICTED content by role;
* the **technical administrator**, who does *not*. That is not an oversight: the
  specification separates technical administration from business access, and a
  test that let it slide would remove the only thing enforcing it (5.2).

Since docs/adr/0181 there are two answers, not three: the reader, the owner and
the head read the restricted Matter alike — every figure identical — and the
department scope and the administrator without a grant read nothing at all,
NORMAL work included.
"""

from __future__ import annotations

import pytest

from app.core.authorization import DEPARTMENT_VIEWER
from app.reporting import metric_catalogue as keys
from app.reporting.services import compute
from tests.synthetic_statistics import RESTRICTED_ONLY_WORD

pytestmark = pytest.mark.django_db


#: Metrics whose value changes by exactly one when the restricted Matter is
#: visible. Not an exhaustive list of metrics — an exhaustive list of the
#: *shapes* an aggregate takes: a total, a year bar, a composition, a coverage
#: denominator, a child-record count, a file count.
_SHIFTS_BY_ONE = (
    keys.MATTERS_TOTAL,
    keys.ACTIVE_FULL_MATTERS,
    keys.MATTERS_WITH_HISTORICAL_SOURCE,
    keys.SUBMISSIONS_SENT,
    keys.OVERDUE_DO_DEADLINE,
    keys.ENTRY_COUNT,
)


def _values(viewer, reporting_context) -> dict[str, int]:
    context = reporting_context(viewer)
    return {key: compute(key, context).value for key in _SHIFTS_BY_ONE}


# ---------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------


def test_a_reader_sees_all_of_the_restricted_matter(world, reporting_context):
    """The baseline this matrix is measured against.

    It used to be the figures *without* the restricted Matter, read by a
    READER. Since docs/adr/0181 a READER is an authorized internal user, so
    these are the department's full figures — one more of everything than the
    old baseline (11, 5, 3, 3, 1, 2).
    """
    assert _values(world.reader, reporting_context) == {
        keys.MATTERS_TOTAL: 12,
        keys.ACTIVE_FULL_MATTERS: 6,
        keys.MATTERS_WITH_HISTORICAL_SOURCE: 4,
        keys.SUBMISSIONS_SENT: 4,
        keys.OVERDUE_DO_DEADLINE: 2,
        keys.ENTRY_COUNT: 3,
    }


def test_the_owner_sees_exactly_what_a_reader_sees(world, reporting_context):
    """Neither participation nor role: every authorized reader (docs/adr/0181)."""
    assert _values(world.sandra, reporting_context) == _values(world.reader, reporting_context)


def test_the_department_head_reads_restricted_content_by_role(world, reporting_context):
    assert _values(world.head, reporting_context) == _values(world.sandra, reporting_context)


def test_the_technical_administrator_does_not(world, reporting_context):
    """Administering the system is not the same as reading the department's files.

    ADMINISTRATOR is a technical role and carries no business access. A
    time-bounded, audited break-glass grant is the route, and it is not this
    (master specification 5.2, app/core/authorization.py). Since docs/adr/0181
    that means no business content at all — not even NORMAL Matters.
    """
    for value in _values(world.admin, reporting_context).values():
        assert value == 0


def test_the_department_scope_without_a_persona_sees_nothing(world, reporting_context):
    """Somebody past the shared gate who has chosen nobody.

    It used to see the department's NORMAL figures. Since docs/adr/0181 the
    shared password alone reads nothing: a persona is required first.
    """
    for value in _values(DEPARTMENT_VIEWER, reporting_context).values():
        assert value == 0


def test_an_unauthenticated_reader_sees_nothing_at_all(world, reporting_context):
    for value in _values(None, reporting_context).values():
        assert value == 0


# ---------------------------------------------------------------------------
# The shapes that are easy to forget — the same for every reader, empty for an
# outsider
# ---------------------------------------------------------------------------


def test_a_restricted_matter_appears_in_a_readers_year_bar_and_no_outsiders(
    world, reporting_context
):
    def year_bar(viewer) -> int:
        result = compute(keys.MATTERS_BY_REPORTING_YEAR, reporting_context(viewer))
        return next((s.value for s in result.segments if s.label == str(world.current_year)), 0)

    assert year_bar(world.reader) == 6
    assert year_bar(world.sandra) == 6
    assert year_bar(world.admin) == 0


def test_a_restricted_matter_appears_in_a_readers_owner_tally_and_no_outsiders(
    world, reporting_context
):
    def sandra_total(viewer) -> int:
        result = compute(keys.MATTERS_BY_OWNER, reporting_context(viewer))
        return next((s.value for s in result.segments if s.label == "Sandra"), 0)

    assert sandra_total(world.reader) == 3
    assert sandra_total(world.sandra) == 3
    assert sandra_total(world.admin) == 0


def test_a_restricted_matter_counts_in_a_readers_policy_area_and_no_outsiders(
    world, reporting_context
):
    def tax_total(viewer) -> int:
        result = compute(keys.MATTERS_BY_POLICY_AREA, reporting_context(viewer))
        return next((s.value for s in result.segments if s.label == "Maksud"), 0)

    assert tax_total(world.reader) == 2
    assert tax_total(world.sandra) == 2
    assert tax_total(world.admin) == 0


def test_a_restricted_matter_does_not_reveal_an_organisation_it_alone_names(
    world, reporting_context
):
    """`Näidisliit` sends only the restricted Matter and receives only its opinion.

    A top-list built after aggregation would print the organisation's name to a
    viewer with no access to the single record that names it. Since
    docs/adr/0181 that viewer is an administrator without a business role; a
    READER reads the record and its name like the head does.
    """
    for key in (keys.MATTERS_BY_SOURCE_ORGANISATION, keys.SUBMISSIONS_BY_RECIPIENT):
        labels = {s.label for s in compute(key, reporting_context(world.admin)).segments}
        assert world.partner.name not in labels
        for privileged_viewer in (world.reader, world.head):
            privileged = {
                s.label for s in compute(key, reporting_context(privileged_viewer)).segments
            }
            assert world.partner.name in privileged


def test_a_restricted_matter_moves_a_readers_coverage_denominator_as_the_owners(
    world, reporting_context
):
    reader = compute(keys.MATTERS_BY_POLICY_AREA, reporting_context(world.reader))
    owner = compute(keys.MATTERS_BY_POLICY_AREA, reporting_context(world.sandra))
    outsider = compute(keys.MATTERS_BY_POLICY_AREA, reporting_context(world.admin))
    assert reader.coverage_denominator == owner.coverage_denominator == 12
    assert reader.coverage_count == owner.coverage_count == 3
    assert outsider.coverage_denominator == 0
    assert outsider.coverage_count == 0


def test_a_restricted_matters_files_are_in_every_readers_material_totals(world, reporting_context):
    """One page and one file behind a restricted Matter, in three metrics."""
    for viewer, pages, occurrences, total_bytes in (
        (world.reader, 5, 9, 54),
        (world.head, 5, 9, 54),
        (world.admin, 0, 0, 0),
    ):
        context = reporting_context(viewer)
        assert compute(keys.LEGACY_SOURCE_PAGES, context).value == pages
        assert compute(keys.HISTORICAL_RESOURCE_OCCURRENCES, context).value == occurrences
        assert compute(keys.HISTORICAL_RESOURCE_BYTES, context).value == total_bytes


def test_a_restricted_matters_document_is_in_every_readers_extraction_totals(
    world, reporting_context
):
    reader = compute(keys.EXTRACTION_SUCCESS, reporting_context(world.reader)).value
    privileged = compute(keys.EXTRACTION_SUCCESS, reporting_context(world.head)).value
    outsider = compute(keys.EXTRACTION_SUCCESS, reporting_context(world.admin)).value
    assert reader == privileged
    assert outsider == 0


def test_an_other_bucket_cannot_smuggle_a_restricted_row_into_a_total(world, reporting_context):
    """The grouped tail is a sum of segments, so it inherits their scoping.

    Worth asserting rather than assuming: a "Muud" bucket computed as
    `total - shown` from an unscoped total is exactly how a restricted row
    reappears as an anonymous number. Since docs/adr/0181 a READER's total is
    the department's 12, and an outsider's is nought.
    """
    result = compute(keys.MATTERS_BY_ORIGIN, reporting_context(world.reader))
    assert result.segment_total == 12
    result = compute(keys.MATTERS_BY_ORIGIN, reporting_context(world.admin))
    assert result.segment_total == 0


# ---------------------------------------------------------------------------
# Exports and lists
# ---------------------------------------------------------------------------


def _csv(client, url: str) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return b"".join(response.streaming_content).decode("utf-8-sig")


def test_a_csv_export_carries_the_restricted_row_for_a_reader_and_none_for_an_outsider(
    client, world
):
    for viewer in (world.reader, world.head):
        client.force_login(viewer)
        body = _csv(client, "/statistika/eksport/teemad.csv?periood=koik")
        assert world.restricted.title in body
        assert world.native_open.title in body

    client.force_login(world.admin)
    outsider = _csv(client, "/statistika/eksport/teemad.csv?periood=koik")
    assert world.restricted.title not in outsider
    assert world.native_open.title not in outsider


def test_a_csv_export_never_says_how_much_it_withheld(client, world):
    """A file that reports the size of its own omission has not omitted it.

    Read by an administrator without a business role, from whom since
    docs/adr/0181 everything is withheld.
    """
    client.force_login(world.admin)
    body = _csv(client, "/statistika/eksport/teemad.csv?periood=koik").lower()
    for word in ("piiratud", "peidetud", "varjatud", "restricted"):
        assert word not in body


def test_restricted_material_reaches_a_readers_material_export_and_no_outsiders(client, world):
    client.force_login(world.reader)
    body = _csv(client, "/statistika/eksport/materjalid.csv?periood=koik")
    assert "liige.pdf" in body
    assert "eelnou.pdf" in body

    client.force_login(world.admin)
    body = _csv(client, "/statistika/eksport/materjalid.csv?periood=koik")
    assert "liige.pdf" not in body
    assert "eelnou.pdf" not in body


def test_restricted_narrative_never_reaches_any_statistika_page(client, world):
    """One word lives only inside the restricted Matter's own content.

    Asserting on the word rather than on a count covers labels, chips, chart
    titles and table cells in one go. Read by an administrator without a
    business role, the viewer who may not read it since docs/adr/0181.
    """
    client.force_login(world.admin)
    for path in (
        "/statistika/",
        "/statistika/teemad/",
        "/statistika/tegevus/",
        "/statistika/ajalooline/",
        "/statistika/andmekvaliteet/",
        "/statistika/materjalid/",
        "/statistika/arvamused/",
    ):
        response = client.get(path, {"periood": "koik"})
        assert response.status_code == 200, path
        assert RESTRICTED_ONLY_WORD not in response.content.decode(), path


def test_a_filter_naming_an_unreadable_owner_empties_the_population(world, reporting_context):
    """Not "ignore the filter and show everything" under a chip saying one name."""
    context = reporting_context(world.reader, owner_unreadable=True)
    assert compute(keys.MATTERS_TOTAL, context).value == 0


def test_a_section_picker_only_offers_sections_the_reader_can_reach(world, reporting_context):
    """One OneNote section exists only behind the restricted Matter.

    A picker built from the whole corpus would print its name — which is itself
    a fact about confidential material — to a viewer who cannot open a single
    page in it. Since docs/adr/0181 a READER opens it; an administrator without
    a business role is offered no section at all.
    """
    from app.reporting.filters import source_sections
    from tests.synthetic_statistics import SECTION_RESTRICTED

    assert source_sections(reporting_context(world.reader)) == source_sections(
        reporting_context(world.head)
    )
    assert SECTION_RESTRICTED in source_sections(reporting_context(world.reader))
    assert source_sections(reporting_context(world.admin)) == []
