"""Import and search meeting: the point of doing both in one stage.

Import and search are deliberately not coupled — search is a derived layer built
after the fact, and either can be repaired without the other. These tests check
the seam between them: that an imported matter becomes findable, that an archive
record is not quietly excluded from search, and that importing a matter does not
give it a second, weaker route past authorization.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.enums import Visibility
from app.legacy_import.apply import apply_plan
from app.legacy_import.planner import build_plan
from app.matters.enums import RecordMode
from app.matters.models import Matter
from app.matters.services import set_matter_visibility
from app.organisations.models import Organisation, OrganisationType
from app.search.indexing import rebuild_all
from app.search.models import SearchDocument
from app.search.services import result_count, search_matters
from tests import factories
from tests.synthetic_register import Row, Sheet, write_workbook

pytestmark = pytest.mark.django_db

ARCHIVE_TITLE = "Sünteetiline arhiivne jäätmeseaduse eelnõu"
RECENT_TITLE = "Sünteetiline hiljutine käibemaksuseaduse eelnõu"


@pytest.fixture
def imported(db, tmp_path: Path, specialist):
    factories.UserFactory(display_name="Kadri", upn="kadri@example.invalid")
    Organisation.objects.create(
        name="Näidisministeerium", organisation_type=OrganisationType.MINISTRY
    )
    path = write_workbook(
        tmp_path / "synthetic.xlsx",
        [
            Sheet(
                2017,
                [
                    Row(
                        reference="2017_42",
                        title=ARCHIVE_TITLE,
                        counterparty="Näidisministeerium",
                        owner="Kadri",
                    )
                ],
            ),
            Sheet(
                2026,
                [
                    Row(
                        reference="2026_7",
                        title=RECENT_TITLE,
                        counterparty="Näidisministeerium",
                        owner="Kadri",
                        status="kooskõlastusringil",
                    )
                ],
            ),
        ],
    )
    apply_plan(build_plan(path))
    rebuild_all()
    return {
        "archive": Matter.objects.get(reference_year=2017, reference_number=42),
        "recent": Matter.objects.get(reference_year=2026, reference_number=7),
    }


def _titles(results) -> list[str]:
    return [result.matter.title for result in results]


def test_a_rebuild_finds_every_imported_matter(imported, specialist) -> None:
    assert SearchDocument.objects.count() == Matter.objects.count()


def test_an_imported_matter_is_found_by_its_original_reference(imported, specialist) -> None:
    """The number the department has used for years still opens the file."""
    results = search_matters(query="2017_42", user=specialist)
    assert len(results) == 1
    assert results[0].matter == imported["archive"]


def test_an_imported_matter_is_found_by_its_title(imported, specialist) -> None:
    assert ARCHIVE_TITLE in _titles(search_matters(query="jäätmeseaduse", user=specialist))


def test_an_archive_record_is_searchable_like_any_other(imported, specialist) -> None:
    """Archive is a record mode, not a reason to be invisible."""
    assert imported["archive"].record_mode == RecordMode.ARCHIVE
    assert ARCHIVE_TITLE in _titles(search_matters(query="arhiivne", user=specialist))


def test_an_imported_matter_is_found_through_its_resolved_organisation(
    imported, specialist
) -> None:
    assert RECENT_TITLE in _titles(search_matters(query="Näidisministeerium", user=specialist))


def test_the_raw_workbook_row_does_not_become_searchable_text(imported, specialist) -> None:
    """Provenance is evidence, not an index. It can hold anything."""
    reference = imported["archive"].source_references.get()
    assert reference.source_row_raw, "the row really was preserved"
    for document in SearchDocument.objects.all():
        assert "2011-2017" not in document.alias_text
        assert document.body_text == ""


def test_source_provenance_survives_a_full_reindex(imported) -> None:
    """Search is derived. Rebuilding it must not disturb anything canonical."""
    before = imported["archive"].source_references.get().source_row_raw
    rebuild_all()
    assert imported["archive"].source_references.get().source_row_raw == before


def test_search_can_be_rebuilt_without_re_importing(imported, specialist) -> None:
    SearchDocument.objects.all().delete()
    assert search_matters(query="2017_42", user=specialist) == []

    rebuild_all()
    assert search_matters(query="2017_42", user=specialist)[0].matter == imported["archive"]


# -- Scenario H: a restricted imported Matter ------------------------------


def test_a_restricted_imported_matter_is_visible_only_to_its_owner(
    imported, specialist, reader, administrator, department_head
) -> None:
    matter = imported["recent"]
    matter.owner = specialist
    matter.save(update_fields=["owner", "updated_at"])
    set_matter_visibility(matter=matter, visibility=Visibility.RESTRICTED)

    assert RECENT_TITLE in _titles(search_matters(query="käibemaksuseaduse", user=specialist))
    assert RECENT_TITLE in _titles(search_matters(query="käibemaksuseaduse", user=department_head))
    assert RECENT_TITLE not in _titles(search_matters(query="käibemaksuseaduse", user=reader))
    # Technical administration is not business access.
    assert RECENT_TITLE not in _titles(
        search_matters(query="käibemaksuseaduse", user=administrator)
    )


def test_a_hidden_imported_matter_does_not_change_the_visible_count(
    imported, specialist, reader
) -> None:
    matter = imported["recent"]
    matter.owner = specialist
    matter.save(update_fields=["owner", "updated_at"])

    assert result_count(query="Sünteetiline", user=reader) == 2
    set_matter_visibility(matter=matter, visibility=Visibility.RESTRICTED)
    assert result_count(query="Sünteetiline", user=reader) == 1
    assert result_count(query="Sünteetiline", user=specialist) == 2


def test_importing_a_matter_gives_it_no_second_route_past_authorization(imported, reader) -> None:
    """An imported record is a Matter like any other, including its permissions."""
    for matter in Matter.objects.all():
        set_matter_visibility(matter=matter, visibility=Visibility.RESTRICTED)

    assert search_matters(query="Sünteetiline", user=reader) == []
    assert search_matters(query="2017_42", user=reader) == []
    assert search_matters(query="Näidisministeerium", user=reader) == []
    assert result_count(query="Sünteetiline", user=reader) == 0


# -- performance -----------------------------------------------------------


def test_a_search_is_a_bounded_number_of_queries(
    imported, specialist, django_assert_max_num_queries
) -> None:
    """The tiers are one statement, so result count must not grow with them."""
    with django_assert_max_num_queries(4):
        search_matters(query="Sünteetiline", user=specialist)


def test_counting_results_is_a_bounded_number_of_queries(
    imported, specialist, django_assert_max_num_queries
) -> None:
    with django_assert_max_num_queries(3):
        result_count(query="Sünteetiline", user=specialist)


def test_planning_an_import_does_not_query_once_per_row(
    db, tmp_path: Path, django_assert_max_num_queries
) -> None:
    """Planning 2,500 rows must not mean 2,500 round trips per lookup."""
    factories.UserFactory(display_name="Kadri", upn="kadri@example.invalid")
    Organisation.objects.create(
        name="Näidisministeerium", organisation_type=OrganisationType.MINISTRY
    )
    rows = [
        Row(
            reference=f"2026_{number}",
            title=f"Sünteetiline teema {number}",
            counterparty="Näidisministeerium",
            owner="Kadri",
        )
        for number in range(1, 41)
    ]
    path = write_workbook(tmp_path / "many.xlsx", [Sheet(2026, rows)])

    # Four lookups per row today: source reference, owner, organisation and
    # existing matter. This asserts the shape rather than a target — if it ever
    # needs to be faster, the fix is batching, and this test is what would
    # notice a regression into something worse.
    with django_assert_max_num_queries(len(rows) * 5):
        build_plan(path)
