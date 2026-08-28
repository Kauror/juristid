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


def test_a_reader_sees_none_of_the_restricted_matter(world, reporting_context):
    """The baseline this matrix is measured against.

    It used to be an unrelated specialist. Since docs/adr/0042 that person reads
    the department, so the viewer who may *not* see the restricted Matter is
    somebody outside the legal team — here a reader, who is authenticated, real
    and deliberately not widened by that decision.
    """
    assert _values(world.reader, reporting_context) == {
        keys.MATTERS_TOTAL: 11,
        keys.ACTIVE_FULL_MATTERS: 5,
        keys.MATTERS_WITH_HISTORICAL_SOURCE: 3,
        keys.SUBMISSIONS_SENT: 3,
        keys.OVERDUE_DO_DEADLINE: 1,
        keys.ENTRY_COUNT: 2,
    }


def test_the_owner_sees_exactly_one_more_of_everything(world, reporting_context):
    """Participation, not role. Sandra owns it, so it is hers to read."""
    unrelated = _values(world.reader, reporting_context)
    owner = _values(world.sandra, reporting_context)
    assert owner == {key: unrelated[key] + 1 for key in unrelated}


def test_the_department_head_reads_restricted_content_by_role(world, reporting_context):
    assert _values(world.head, reporting_context) == _values(world.sandra, reporting_context)


def test_the_technical_administrator_does_not(world, reporting_context):
    """Administering the system is not the same as reading the department's files.

    ADMINISTRATOR is a technical role and carries no business access. A
    time-bounded, audited break-glass grant is the route, and it is not this
    (master specification 5.2, app/core/authorization.py).
    """
    assert _values(world.admin, reporting_context) == _values(world.reader, reporting_context)


def test_the_department_scope_works_without_a_persona_and_stays_normal(world, reporting_context):
    """Somebody past the shared gate who has chosen nobody.

    The page has to be worth looking at — so the numbers are the department's —
    and it must not become useful by borrowing an arbitrary person's identity
    (Stage-2D auth brief 6).
    """
    department = _values(DEPARTMENT_VIEWER, reporting_context)
    assert department == _values(world.reader, reporting_context)
    assert department[keys.MATTERS_TOTAL] == 11


def test_an_unauthenticated_reader_sees_nothing_at_all(world, reporting_context):
    for value in _values(None, reporting_context).values():
        assert value == 0


# ---------------------------------------------------------------------------
# The shapes that are easy to forget
# ---------------------------------------------------------------------------


def test_a_restricted_matter_does_not_appear_in_a_year_bar(world, reporting_context):
    def year_bar(viewer) -> int:
        result = compute(keys.MATTERS_BY_REPORTING_YEAR, reporting_context(viewer))
        return next((s.value for s in result.segments if s.label == str(world.current_year)), 0)

    assert year_bar(world.reader) == 5
    assert year_bar(world.sandra) == 6


def test_a_restricted_matter_does_not_appear_in_an_owner_tally(world, reporting_context):
    def sandra_total(viewer) -> int:
        result = compute(keys.MATTERS_BY_OWNER, reporting_context(viewer))
        return next((s.value for s in result.segments if s.label == "Sandra Testjurist"), 0)

    assert sandra_total(world.reader) == 2
    assert sandra_total(world.sandra) == 3


def test_a_restricted_matter_does_not_inflate_a_policy_area(world, reporting_context):
    def tax_total(viewer) -> int:
        result = compute(keys.MATTERS_BY_POLICY_AREA, reporting_context(viewer))
        return next((s.value for s in result.segments if s.label == "Maksud"), 0)

    assert tax_total(world.reader) == 1
    assert tax_total(world.sandra) == 2


def test_a_restricted_matter_does_not_reveal_an_organisation_it_alone_names(
    world, reporting_context
):
    """`Näidisliit` sends only the restricted Matter and receives only its opinion.

    A top-list built after aggregation would print the organisation's name to a
    reader with no access to the single record that names it.
    """
    for key in (keys.MATTERS_BY_SOURCE_ORGANISATION, keys.SUBMISSIONS_BY_RECIPIENT):
        labels = {s.label for s in compute(key, reporting_context(world.reader)).segments}
        assert world.partner.name not in labels
        privileged = {s.label for s in compute(key, reporting_context(world.head)).segments}
        assert world.partner.name in privileged


def test_a_restricted_matter_does_not_move_a_coverage_denominator(world, reporting_context):
    outsider = compute(keys.MATTERS_BY_POLICY_AREA, reporting_context(world.reader))
    owner = compute(keys.MATTERS_BY_POLICY_AREA, reporting_context(world.sandra))
    assert outsider.coverage_denominator == 11
    assert owner.coverage_denominator == 12
    assert outsider.coverage_count == 2
    assert owner.coverage_count == 3


def test_a_restricted_matters_files_are_not_in_the_material_totals(world, reporting_context):
    """One page and one file behind a restricted Matter, in three metrics."""
    for viewer, pages, occurrences, byte_gap in (
        (world.reader, 4, 8, 0),
        (world.head, 5, 9, 4),
    ):
        context = reporting_context(viewer)
        assert compute(keys.LEGACY_SOURCE_PAGES, context).value == pages
        assert compute(keys.HISTORICAL_RESOURCE_OCCURRENCES, context).value == occurrences
        assert compute(keys.HISTORICAL_RESOURCE_BYTES, context).value == 50 + byte_gap


def test_a_restricted_matters_document_is_not_in_the_extraction_totals(world, reporting_context):
    outsider = compute(keys.EXTRACTION_SUCCESS, reporting_context(world.reader)).value
    privileged = compute(keys.EXTRACTION_SUCCESS, reporting_context(world.head)).value
    assert privileged == outsider + 2  # its submission evidence and its material


def test_an_other_bucket_cannot_smuggle_a_restricted_row_into_a_total(world, reporting_context):
    """The grouped tail is a sum of segments, so it inherits their scoping.

    Worth asserting rather than assuming: a "Muud" bucket computed as
    `total - shown` from an unscoped total is exactly how a restricted row
    reappears as an anonymous number.
    """
    result = compute(keys.MATTERS_BY_ORIGIN, reporting_context(world.reader))
    assert result.segment_total == 11


# ---------------------------------------------------------------------------
# Exports and lists
# ---------------------------------------------------------------------------


def _csv(client, url: str) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return b"".join(response.streaming_content).decode("utf-8-sig")


def test_a_csv_export_carries_no_restricted_row(client, world):
    client.force_login(world.reader)
    body = _csv(client, "/statistika/eksport/teemad.csv?periood=koik")
    assert world.restricted.title not in body
    assert world.native_open.title in body

    client.force_login(world.head)
    privileged = _csv(client, "/statistika/eksport/teemad.csv?periood=koik")
    assert world.restricted.title in privileged


def test_a_csv_export_never_says_how_much_it_withheld(client, world):
    """A file that reports the size of its own omission has not omitted it."""
    client.force_login(world.reader)
    body = _csv(client, "/statistika/eksport/teemad.csv?periood=koik").lower()
    for word in ("piiratud", "peidetud", "varjatud", "restricted"):
        assert word not in body


def test_restricted_material_never_reaches_the_material_export(client, world):
    client.force_login(world.reader)
    body = _csv(client, "/statistika/eksport/materjalid.csv?periood=koik")
    assert "liige.pdf" not in body
    assert "eelnou.pdf" in body


def test_restricted_narrative_never_reaches_any_statistika_page(client, world):
    """One word lives only inside the restricted Matter's own content.

    Asserting on the word rather than on a count covers labels, chips, chart
    titles and table cells in one go.
    """
    client.force_login(world.reader)
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
    a fact about confidential material — to a reader who cannot open a single
    page in it.
    """
    from app.reporting.filters import source_sections
    from tests.synthetic_statistics import SECTION_RESTRICTED

    assert source_sections(reporting_context(world.reader)) == [
        "ARHIIV keskkond",
        "ARHIIV maksud ja toll",
    ]
    assert SECTION_RESTRICTED in source_sections(reporting_context(world.head))
