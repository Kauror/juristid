"""`Ülevaade / uudis`, `Oluline tähtaeg` and `Töövõit` are findable (F-008).

All three were stored text that search never held: a lawyer who recorded a
deadline and searched for its words got «vasteid ei leitud», which reads as
«not recorded» rather than «not indexed». Each now has a source kind of its own,
joined live to its record so that the record's *current* `visibility_override`
decides who may read the row — the AUTH-003 rule every child kind follows.

What is held here, for each of the three:

* a record written **before** the release that added the kind is findable once
  the one-time full rebuild has run — the production path (`rebuild_search_index`);
* a record written **after** is findable when its own transaction commits;
* a corrected title moves what search finds, and a removed record leaves it;
* a RESTRICTED record on a NORMAL Matter gives a reader who may not see it no
  title, no excerpt and no change in the result count;

and, for the overview alone, that its address is never made into its title.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app.core.enums import Visibility
from app.intelligence.services import (
    add_important_date,
    add_work_victory_candidate,
    update_important_date,
    update_work_victory,
)
from app.matters.models import MatterWebsiteOverview
from app.matters.removal import remove_matter_record
from app.matters.services import (
    correct_website_overview_link,
    plan_website_overview,
    publish_website_overview,
)
from app.search.indexing import rebuild_all, suspend_indexing
from app.search.models import INDEX_VERSION, SearchDocument, SearchSourceKind
from app.search.services import result_count, search_page
from app.workflow.enums import DatePrecision
from tests import factories

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# The three kinds, described once
# ---------------------------------------------------------------------------


def _overview(matter: Any, actor: Any, title: str, body: str) -> MatterWebsiteOverview:
    """A published overview carrying `title`. It has no prose besides it."""
    del body
    plan = plan_website_overview(matter=matter, actor=actor)
    return publish_website_overview(
        overview=plan,
        url=f"https://www.koda.ee/uudised/{plan.pk}",
        published_on=None,
        actor=actor,
        title=title,
    )


def _overview_retitle(record: Any, actor: Any, title: str) -> Any:
    return correct_website_overview_link(
        overview=record, url=record.url, published_on=record.published_on, actor=actor, title=title
    )


def _deadline(matter: Any, actor: Any, title: str, body: str) -> Any:
    return add_important_date(
        matter=matter,
        title=title,
        date_value=date(2027, 3, 1),
        period_end=date(2027, 3, 1),
        date_precision=DatePrecision.EXACT,
        note=body,
        actor=actor,
    )


def _deadline_retitle(record: Any, actor: Any, title: str) -> Any:
    return update_important_date(
        record=record,
        title=title,
        date_value=record.date_value,
        period_end=record.period_end,
        date_precision=record.date_precision,
        note=record.note,
        actor=actor,
    )


def _victory(matter: Any, actor: Any, title: str, body: str) -> Any:
    return add_work_victory_candidate(
        matter=matter,
        title=title,
        detail=body,
        note="Märkus kinnitajale.",
        actor=actor,
    )


def _victory_retitle(record: Any, actor: Any, title: str) -> Any:
    return update_work_victory(
        record=record,
        title=title,
        detail=record.detail,
        period_date=record.period_date,
        period_end=record.period_end,
        date_precision=record.date_precision,
        source_url=record.source_url,
        note=record.note,
        actor=actor,
    )


@dataclass(frozen=True)
class Kind:
    kind: str
    label: str
    removal_key: str
    link: str
    create: Callable[[Any, Any, str, str], Any]
    retitle: Callable[[Any, Any, str], Any]
    #: Whether the record has prose besides its title. An overview does not.
    has_body: bool

    def __str__(self) -> str:
        return self.kind


KINDS = [
    Kind(
        SearchSourceKind.WEBSITE_OVERVIEW,
        "Ülevaade / uudis",
        "ulevaade",
        "website_overview_id",
        _overview,
        _overview_retitle,
        has_body=False,
    ),
    Kind(
        SearchSourceKind.IMPORTANT_DATE,
        "Oluline tähtaeg",
        "tahtaeg",
        "important_date_id",
        _deadline,
        _deadline_retitle,
        has_body=True,
    ),
    Kind(
        SearchSourceKind.WORK_VICTORY,
        "Töövõit",
        "toovoit",
        "work_victory_id",
        _victory,
        _victory_retitle,
        has_body=True,
    ),
]

#: Synthetic words no seed or factory produces, so a hit can only be the record.
TITLE_WORD = "Kvarkovitšlik"
BODY_WORD = "Zebrakoridoriline"


def _results(user: Any, query: str) -> list[Any]:
    """What `/otsing/` would list: the service the page renders."""
    return search_page(query=query, user=user).results


def _of_kind(user: Any, query: str, kind: str) -> list[Any]:
    return [result for result in _results(user, query) if result.source_kind == kind]


@pytest.fixture
def matter(specialist):
    return factories.MatterFactory(owner=specialist, title="Tavaline teema F-008")


# ---------------------------------------------------------------------------
# Written after the release: findable when the write commits
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", KINDS, ids=str)
def test_a_written_record_is_findable_by_its_title(spec, matter, specialist):
    record = spec.create(matter, specialist, f"{TITLE_WORD} pealkiri", f"{BODY_WORD} selgitus")

    found = _of_kind(specialist, TITLE_WORD, spec.kind)

    assert len(found) == 1
    assert found[0].matter == matter
    assert found[0].source_title == f"{TITLE_WORD} pealkiri"
    assert found[0].source_label == spec.label
    row = SearchDocument.objects.get(source_kind=spec.kind)
    assert getattr(row, spec.link) == record.pk
    assert row.source_object_id == record.pk
    assert row.index_version == INDEX_VERSION


@pytest.mark.parametrize("spec", [k for k in KINDS if k.has_body], ids=str)
def test_a_written_record_is_findable_by_its_explanation_and_quoted(spec, matter, specialist):
    spec.create(matter, specialist, "Pealkiri", f"Siin on {BODY_WORD} lause.")

    found = _of_kind(specialist, BODY_WORD, spec.kind)

    assert len(found) == 1
    assert any(run.highlight and BODY_WORD in run.text for run in found[0].snippet)


def test_a_work_victorys_note_is_searchable_too(matter, specialist):
    add_work_victory_candidate(
        matter=matter, title="Pealkiri", note=f"{BODY_WORD} märkus", actor=specialist
    )

    assert len(_of_kind(specialist, BODY_WORD, SearchSourceKind.WORK_VICTORY)) == 1


# ---------------------------------------------------------------------------
# Written before the release: findable after the one-time rebuild
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", KINDS, ids=str)
def test_a_record_from_before_the_release_is_found_after_a_full_rebuild(spec, matter, specialist):
    """The production path: records exist, the corpus holds none of them.

    Written with indexing suspended, which is exactly the state a deployment
    starts in — the previous release never projected these kinds — and then
    the documented repair, `rebuild_search_index`, run as the operator runs it.
    """
    with suspend_indexing():
        spec.create(matter, specialist, f"{TITLE_WORD} vana", f"{BODY_WORD} vana")
    assert not SearchDocument.objects.filter(source_kind=spec.kind).exists()
    assert _of_kind(specialist, TITLE_WORD, spec.kind) == []

    out = io.StringIO()
    call_command("rebuild_search_index", stdout=out)

    found = _of_kind(specialist, TITLE_WORD, spec.kind)
    assert [result.source_title for result in found] == [f"{TITLE_WORD} vana"]
    if spec.has_body:
        assert len(_of_kind(specialist, BODY_WORD, spec.kind)) == 1


def test_a_rebuild_reports_each_new_kind(matter, specialist):
    for spec in KINDS:
        spec.create(matter, specialist, f"{TITLE_WORD} {spec.removal_key}", "")

    result = rebuild_all()

    assert (result.website_overviews, result.important_dates, result.work_victories) == (1, 1, 1)


def test_the_integrity_check_counts_and_recomputes_the_new_kinds(matter, specialist):
    """A kind projected and not watched is a kind whose absence nobody notices."""
    from app.search.management.commands.check_search_integrity import build_report

    for spec in KINDS:
        spec.create(matter, specialist, f"{TITLE_WORD} {spec.removal_key}", f"{BODY_WORD}")
    # An untitled plan is a record that projects nothing, on purpose; the check
    # must not call it missing.
    plan_website_overview(matter=matter, actor=specialist)
    rebuild_all()

    report = build_report(full=True)

    counted = {label: (expected, actual) for label, expected, actual in report.counts}
    assert counted["Ülevaated / uudised"] == (1, 1)
    assert counted["Olulised tähtajad"] == (1, 1)
    assert counted["Töövõidud"] == (1, 1)
    assert report.ok, report.findings

    # And it notices one going missing.
    SearchDocument.objects.filter(source_kind=SearchSourceKind.IMPORTANT_DATE).delete()
    report = build_report(full=True)
    assert any(finding.label == "Olulised tähtajad" for finding in report.findings)


# ---------------------------------------------------------------------------
# Edits and removals keep the index right
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", KINDS, ids=str)
def test_correcting_the_title_moves_what_search_finds(spec, matter, specialist):
    record = spec.create(matter, specialist, f"{TITLE_WORD} esimene", "")

    spec.retitle(record, specialist, "Täiesti teine sõnastus Ortograafikum")

    assert _of_kind(specialist, TITLE_WORD, spec.kind) == []
    found = _of_kind(specialist, "Ortograafikum", spec.kind)
    assert [result.source_title for result in found] == ["Täiesti teine sõnastus Ortograafikum"]
    assert SearchDocument.objects.filter(source_kind=spec.kind).count() == 1


@pytest.mark.parametrize("spec", KINDS, ids=str)
def test_removing_the_record_removes_it_from_search(spec, matter, specialist):
    record = spec.create(matter, specialist, f"{TITLE_WORD} eemaldatav", f"{BODY_WORD}")
    assert _of_kind(specialist, TITLE_WORD, spec.kind)

    remove_matter_record(
        matter_id=matter.pk, kind_key=spec.removal_key, record_id=record.pk, actor=specialist
    )

    assert _of_kind(specialist, TITLE_WORD, spec.kind) == []
    assert _of_kind(specialist, BODY_WORD, spec.kind) == []
    assert not SearchDocument.objects.filter(source_kind=spec.kind).exists()
    # And a rebuild does not bring it back.
    rebuild_all()
    assert not SearchDocument.objects.filter(source_kind=spec.kind).exists()


@pytest.mark.parametrize("spec", KINDS, ids=str)
def test_deleting_the_record_cascades_to_its_row(spec, matter, specialist):
    record = spec.create(matter, specialist, f"{TITLE_WORD} kustutatav", "")

    type(record).objects.filter(pk=record.pk).delete()

    assert not SearchDocument.objects.filter(source_kind=spec.kind).exists()


def test_a_cancelled_deadline_stays_findable(matter, specialist):
    """Cancelled is not removed: the expectation was real and the file says so."""
    from app.intelligence.services import cancel_important_date

    record = _deadline(matter, specialist, f"{TITLE_WORD} tühistatud", "")
    cancel_important_date(record=record, actor=specialist)

    assert len(_of_kind(specialist, TITLE_WORD, SearchSourceKind.IMPORTANT_DATE)) == 1


# ---------------------------------------------------------------------------
# A restricted record is invisible to a reader who may not see it
# ---------------------------------------------------------------------------

#: A word the Matter's own title and the restricted record's title share, so a
#: leak would show up as a count, not only as a row.
SHARED = "Ühisnimetaja"


def _restrict(record: Any) -> None:
    record.visibility_override = Visibility.RESTRICTED
    record.save(update_fields=["visibility_override", "updated_at"])


@pytest.mark.parametrize("spec", KINDS, ids=str)
def test_a_restricted_record_on_a_normal_matter_does_not_reach_a_reader(
    spec, specialist, reader, client
):
    matter = factories.MatterFactory(owner=specialist, title=f"{SHARED} avalik teema")
    before = result_count(query=SHARED, user=reader)
    assert before == 1

    record = spec.create(matter, specialist, f"{SHARED} {TITLE_WORD}", f"{BODY_WORD} salajane")
    _restrict(record)

    # No row, no title, no excerpt — and the count is what it was.
    assert result_count(query=SHARED, user=reader) == before
    assert result_count(query=TITLE_WORD, user=reader) == 0
    assert result_count(query=BODY_WORD, user=reader) == 0
    assert [result.source_kind for result in _results(reader, SHARED)] == [SearchSourceKind.MATTER]
    client.force_login(reader)
    page = client.get(reverse("search:search"), {"q": SHARED}).content.decode()
    assert TITLE_WORD not in page
    assert "salajane" not in page
    assert f'<span class="badge badge--source">{spec.label}</span>' not in page
    for query in (TITLE_WORD, BODY_WORD):
        page = client.get(reverse("search:search"), {"q": query}).content.decode()
        assert "salajane" not in page
        assert 'class="result"' not in page

    # The row exists and the people entitled to it still find it.
    assert result_count(query=SHARED, user=specialist) == before + 1
    assert len(_of_kind(specialist, TITLE_WORD, spec.kind)) == 1


@pytest.mark.parametrize("spec", KINDS, ids=str)
def test_restricting_takes_effect_on_the_next_query_with_no_reindex(spec, specialist, reader):
    """Visibility is joined live: an override written behind the indexer's back
    (a `QuerySet.update()`, which fires no signal) still hides the row."""
    matter = factories.MatterFactory(owner=specialist, title="Teine avalik teema")
    record = spec.create(matter, specialist, f"{TITLE_WORD} ajutine", "")
    assert len(_of_kind(reader, TITLE_WORD, spec.kind)) == 1
    indexed_at = SearchDocument.objects.get(source_kind=spec.kind).indexed_at

    type(record).objects.filter(pk=record.pk).update(visibility_override=Visibility.RESTRICTED)

    assert SearchDocument.objects.get(source_kind=spec.kind).indexed_at == indexed_at
    assert _of_kind(reader, TITLE_WORD, spec.kind) == []

    type(record).objects.filter(pk=record.pk).update(visibility_override="")
    assert len(_of_kind(reader, TITLE_WORD, spec.kind)) == 1


@pytest.mark.parametrize("spec", KINDS, ids=str)
def test_a_record_on_a_restricted_matter_does_not_reach_a_reader(
    spec, restricted_matter, specialist, reader
):
    spec.create(restricted_matter, specialist, f"{TITLE_WORD} piiratud", "")

    assert result_count(query=TITLE_WORD, user=reader) == 0
    assert len(_of_kind(specialist, TITLE_WORD, spec.kind)) == 1


# ---------------------------------------------------------------------------
# An overview's address is never its title
# ---------------------------------------------------------------------------


def test_an_untitled_overview_projects_nothing_and_its_address_is_not_searchable(
    matter, specialist
):
    plan = plan_website_overview(matter=matter, actor=specialist)
    assert not SearchDocument.objects.filter(source_kind=SearchSourceKind.WEBSITE_OVERVIEW).exists()

    publish_website_overview(
        overview=plan,
        url="https://www.koda.ee/uudised/aadressisona-kaasajastamine",
        published_on=None,
        actor=specialist,
    )
    rebuild_all()

    assert not SearchDocument.objects.filter(source_kind=SearchSourceKind.WEBSITE_OVERVIEW).exists()
    assert result_count(query="aadressisona", user=specialist) == 0
    assert result_count(query="kaasajastamine", user=specialist) == 0


def test_a_titled_overview_carries_its_title_and_not_its_address(matter, specialist):
    overview = _overview(matter, specialist, f"{TITLE_WORD} uudis", "")
    correct_website_overview_link(
        overview=overview,
        url="https://www.koda.ee/uudised/aadressisona-kaasajastamine",
        published_on=None,
        actor=specialist,
    )

    row = SearchDocument.objects.get(source_kind=SearchSourceKind.WEBSITE_OVERVIEW)
    assert row.title == f"{TITLE_WORD} uudis"
    for column in ("title", "identifiers", "alias_text", "people_text", "body_text"):
        assert "aadressisona" not in getattr(row, column)
        assert "koda.ee" not in getattr(row, column)
    assert result_count(query="aadressisona", user=specialist) == 0


def test_clearing_an_overviews_title_withdraws_its_row(matter, specialist):
    overview = _overview(matter, specialist, f"{TITLE_WORD} uudis", "")

    _overview_retitle(overview, specialist, "")

    assert not SearchDocument.objects.filter(source_kind=SearchSourceKind.WEBSITE_OVERVIEW).exists()


# ---------------------------------------------------------------------------
# The results page, and what it costs
# ---------------------------------------------------------------------------


def test_the_results_page_labels_and_links_each_new_kind(matter, specialist, client):
    for spec in KINDS:
        spec.create(matter, specialist, f"{TITLE_WORD} {spec.label}", "")
    client.force_login(specialist)

    page = client.get(reverse("search:search"), {"q": TITLE_WORD}).content.decode()

    matter_url = reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    for spec in KINDS:
        assert f'<span class="badge badge--source">{spec.label}</span>' in page
        assert f"{TITLE_WORD} {spec.label}" in page
    assert page.count(f'href="{matter_url}"') >= len(KINDS)


def test_the_header_suggestions_say_there_is_more_when_only_a_new_kind_matches(
    matter, specialist, client
):
    """The dropdown lists Matters only; «Vaata kõiki tulemusi» must not hide these."""
    _deadline(matter, specialist, f"{TITLE_WORD} tähtaeg", "")
    client.force_login(specialist)

    payload = client.get(reverse("search:suggestions"), {"q": TITLE_WORD}).json()

    assert payload["results"] == []
    assert payload["has_more"] is True


def _queries_for_search(client: Any, query: str) -> int:
    with CaptureQueriesContext(connection) as captured:
        response = client.get(reverse("search:search"), {"q": query})
    assert response.status_code == 200
    return len(captured)


def test_a_results_page_costs_the_same_however_many_new_kind_rows_it_shows(specialist, client):
    """No query per result: the rows render from the page's own statements."""
    client.force_login(specialist)
    first = factories.MatterFactory(owner=specialist, title="Esimene")
    for spec in KINDS:
        spec.create(first, specialist, f"{TITLE_WORD} üks", f"{BODY_WORD}")
    one_each = _queries_for_search(client, TITLE_WORD)

    for number in range(4):
        other = factories.MatterFactory(owner=specialist, title=f"Teema {number}")
        for spec in KINDS:
            spec.create(other, specialist, f"{TITLE_WORD} mitu {number}", f"{BODY_WORD}")
    five_each = _queries_for_search(client, TITLE_WORD)

    assert five_each == one_each


def test_a_rebuild_costs_the_same_however_many_new_kind_records_there_are(specialist):
    """No query per record in a full rebuild: two statements per batch per kind."""
    first = factories.MatterFactory(owner=specialist, title="Esimene")
    for spec in KINDS:
        spec.create(first, specialist, f"{TITLE_WORD} üks", "")
    with CaptureQueriesContext(connection) as small:
        rebuild_all()

    for number in range(4):
        other = factories.MatterFactory(owner=specialist, title=f"Teema {number}")
        for spec in KINDS:
            spec.create(other, specialist, f"{TITLE_WORD} mitu {number}", "")
    with CaptureQueriesContext(connection) as large:
        rebuild_all()

    def new_kind_statements(captured: Any) -> int:
        tables = (
            "intelligence_matterimportantdate",
            "intelligence_matterworkvictory",
            "matters_matterwebsiteoverview",
        )
        return sum(
            1 for query in captured.captured_queries if any(t in query["sql"] for t in tables)
        )

    assert new_kind_statements(large) == new_kind_statements(small)
