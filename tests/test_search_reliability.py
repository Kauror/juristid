"""The ways search goes quietly wrong, and the guards against each of them.

Every test here is a regression: it failed before the change it accompanies. The
common shape of the failures is worth stating, because it is what makes search
defects expensive. None of them raised anything. A submission stopped being
findable under the ministry it was sent to; a re-captured page kept answering
with the text of a capture that had been replaced; a rebuild made ordinary saves
fail; a hand-built query returned 500 where "no results" was the honest answer.
In each case the page looked exactly like a page that had searched correctly and
found nothing — which is the conclusion this system exists to prevent a lawyer
from reaching (docs/adr/0013).
"""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest
from django.db import connection, connections, transaction
from django.utils import timezone

from app.core.enums import Visibility
from app.documents.enums import DerivativeStatus
from app.legacy_import.source_pages import (
    LegacySourcePage,
    MatterSourcePage,
    SourceMatchClass,
    SourceMatchMethod,
    SourceRelationshipKind,
    SourceSystem,
)
from app.matters.models import Matter
from app.organisations.models import Organisation, OrganisationType
from app.search.indexing import (
    indexable_matters,
    indexing_is_suspended,
    rebuild_all,
    refresh_matters,
    suspend_indexing,
)
from app.search.models import INDEX_VERSION, SearchDocument, SearchSourceKind
from app.search.services import (
    MAX_QUERY_CHARACTERS,
    clean_query,
    result_count,
    search,
    search_documents,
)
from app.submissions.services import set_recipients
from tests import factories
from tests import synthetic_corpus as corpus

pytestmark = pytest.mark.django_db

PDF = "application/pdf"


# ---------------------------------------------------------------------------
# A sent opinion has to be findable under the ministry it was sent to
# ---------------------------------------------------------------------------


@pytest.fixture
def submission_with_recipient(specialist):
    ministry = Organisation.objects.create(
        name="Rahandusministeerium", organisation_type=OrganisationType.MINISTRY
    )
    ministry.aliases.create(alias="RaM", alias_type="ABBREVIATION")
    matter = factories.MatterFactory(
        owner=specialist, title="Riigilõivuseaduse muutmise eelnõu", reference_year=2026
    )
    submission = factories.SubmissionFactory(
        matter=matter, title="Koja arvamus riigilõivuseaduse eelnõule", reference="KODA-2026-9"
    )
    return submission, ministry


def test_a_sent_opinion_is_findable_under_its_recipient(
    submission_with_recipient, specialist
) -> None:
    """The headline question: where is the opinion we sent to that ministry?

    ``set_recipients`` deletes the old rows and ``bulk_create``s the new ones.
    Django sends ``post_delete`` per instance and sends nothing at all for a
    bulk insert, so the removal reindexed the submission with an empty recipient
    list and the addition never reindexed it again. The opinion stayed in the
    corpus, addressed to nobody.
    """
    submission, ministry = submission_with_recipient
    set_recipients(submission=submission, addressees=[ministry], audit=False)

    results = search(query="Rahandusministeerium", user=specialist)

    assert any(result.submission_id == submission.pk for result in results)


def test_a_recipients_alias_finds_the_opinion(submission_with_recipient, specialist) -> None:
    submission, ministry = submission_with_recipient
    set_recipients(submission=submission, addressees=[ministry], audit=False)

    results = search(query="RaM", user=specialist)

    assert any(result.submission_id == submission.pk for result in results)


def test_replacing_the_recipient_removes_the_old_one(submission_with_recipient, specialist) -> None:
    """The direction that was already right, pinned so the fix cannot break it."""
    submission, ministry = submission_with_recipient
    other = Organisation.objects.create(
        name="Kliimaministeerium", organisation_type=OrganisationType.MINISTRY
    )
    set_recipients(submission=submission, addressees=[ministry], audit=False)
    set_recipients(submission=submission, addressees=[other], audit=False)

    assert not [
        result
        for result in search(query="Rahandusministeerium", user=specialist)
        if result.submission_id == submission.pk
    ]
    assert [
        result
        for result in search(query="Kliimaministeerium", user=specialist)
        if result.submission_id == submission.pk
    ]


# ---------------------------------------------------------------------------
# A re-captured historical page
# ---------------------------------------------------------------------------


def _source_page(**overrides) -> LegacySourcePage:
    now = timezone.now()
    values = {
        "source_system": SourceSystem.ONENOTE_DESKTOP,
        "source_page_id": "page-0001",
        "page_key": "arhiiv/page-0001",
        "source_notebook": "Õigusosakond",
        "source_section": "Maksud",
        "title": "Aktsiisimäärade arutelu",
        "capture_id": "capture-1",
        "derived_text": "Esimene hõive: koosolekul osalesid ministeeriumi esindajad.",
        "reference_tokens": "2019_44",
        "first_imported_at": now,
        "latest_imported_at": now,
    }
    values.update(overrides)
    return LegacySourcePage.objects.create(**values)


@pytest.fixture
def linked_page(specialist):
    matter = factories.MatterFactory(owner=specialist, title="Aktsiisid 2019", reference_year=2019)
    page = _source_page()
    link = MatterSourcePage.objects.create(
        matter=matter,
        source_page=page,
        relationship_kind=SourceRelationshipKind.PRIMARY,
        match_method=SourceMatchMethod.EXCEL_EXACT_PAGE_ID,
        match_class=SourceMatchClass.EXACT,
    )
    return matter, page, link


def test_a_linked_page_is_searchable_by_its_text(linked_page, specialist) -> None:
    """The fixture creates the link and calls nothing else, on purpose.

    Five production call sites create these rows and all five remembered to
    call `index_source_link` — which is precisely the fragility: the projection
    was correct only until somebody wrote a sixth. A page attached to a Matter
    and absent from search looks exactly like a page that was never attached.
    """
    _, _, link = linked_page
    results = search(query="koosolekul", user=specialist)
    assert any(result.source_page_id == link.pk for result in results)


def test_re_capturing_a_page_replaces_what_finds_it(linked_page, specialist) -> None:
    """The archive is known to have produced stale page HTML, so re-capture is
    a normal operation rather than an exception.

    The Matter↔page rows were written when the *link* was created and never
    again. A second capture rewrote the page in place and the corpus kept
    answering with the first one — and kept *not* answering for anything the
    new capture had added.
    """
    _, page, link = linked_page
    page.derived_text = "Teine hõive: arutelu lükati edasi järgmisesse kvartalisse."
    page.save()

    assert not [r for r in search(query="koosolekul", user=specialist) if r.source_page_id]
    assert [r for r in search(query="kvartalisse", user=specialist) if r.source_page_id == link.pk]


def test_a_renamed_page_is_findable_under_its_new_title(linked_page, specialist) -> None:
    _, page, link = linked_page
    page.title = "Aktsiisimäärade lõplik kokkulepe"
    page.save()

    results = search(query="kokkulepe", user=specialist)
    assert any(result.source_page_id == link.pk for result in results)


def test_unlinking_one_matter_leaves_the_other_matters_page_searchable(
    linked_page, specialist
) -> None:
    """A page shared by two Matters is two rows, and one belongs to each.

    Deleting one relationship must take exactly one row with it — the whole
    reason the projection is keyed on the link rather than on the page
    (docs/adr/0013, Stage-2D brief 37).
    """
    _, page, first = linked_page
    second_matter = factories.MatterFactory(owner=specialist, title="Aktsiisid, teine teema")
    second = MatterSourcePage.objects.create(
        matter=second_matter,
        source_page=page,
        relationship_kind=SourceRelationshipKind.RELATED,
        match_method=SourceMatchMethod.MANUAL,
        match_class=SourceMatchClass.REVIEWED,
    )

    first.delete()

    locators = {r.source_page_id for r in search(query="koosolekul", user=specialist)}
    assert second.pk in locators
    assert first.pk not in locators


def test_a_historical_page_on_a_restricted_matter_is_invisible(
    linked_page, specialist, reader
) -> None:
    """A source page has no restriction of its own, so its Matter's is the whole
    answer — and that answer is read live, from the Matter, on every query."""
    matter, _, link = linked_page
    assert [r for r in search(query="koosolekul", user=reader) if r.source_page_id]

    Matter.objects.filter(pk=matter.pk).update(visibility=Visibility.RESTRICTED)

    assert search(query="koosolekul", user=reader) == []
    assert result_count(query="koosolekul", user=reader) == 0
    assert [r for r in search(query="koosolekul", user=specialist) if r.source_page_id == link.pk]


# ---------------------------------------------------------------------------
# Malformed and hostile queries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        'unmatched "quote',
        "-",
        "!!!???",
        "or and or",
        '"" OR ""',
        "\x00",
        "kala\x00mees",
        "\x01\x02\x03",
        " " * 4000,
        "a" * 5000,
        "õ" * 5000,
        "2026_99999999999999999999",
        "2026_-5",
        "2026_",
        "_184",
    ],
)
def test_a_malformed_query_never_becomes_a_server_error(signed_in, query) -> None:
    """Anything that reaches the query string has to produce a page.

    NUL is the one that actually crashed: psycopg refuses to send it, so a
    hand-built ``?q=%00`` was an unhandled exception where "no results" was the
    truthful answer. The rest are here because a search box is the most
    poked-at input in the product and a 500 is never the right reply to a
    person typing.
    """
    response = signed_in.get("/otsing/", {"q": query})
    assert response.status_code == 200


def test_an_over_long_query_says_it_was_not_run(signed_in) -> None:
    """Refused, not truncated, and not reported as "vasteid ei leitud".

    A cut query answers a question nobody asked, and an empty-results page for
    a query that never ran is exactly the false negative this suite exists to
    prevent.
    """
    response = signed_in.get("/otsing/", {"q": "a" * (MAX_QUERY_CHARACTERS + 1)})

    assert response.status_code == 200
    assert response.context["query_was_refused"] is True
    assert response.context["result_count"] == 0
    body = response.content.decode()
    assert "Päringut ei tehtud" in body
    assert "Vasteid ei leitud" not in body


def test_a_query_at_the_limit_is_still_run(signed_in) -> None:
    response = signed_in.get("/otsing/", {"q": "a" * MAX_QUERY_CHARACTERS})
    assert response.status_code == 200
    assert response.context["query_was_refused"] is False


def test_whitespace_between_the_two_halves_of_a_reference_does_not_matter() -> None:
    assert clean_query("2026    184") == "2026 184"
    assert clean_query("  2026\t184\n") == "2026 184"


def test_a_refused_query_is_not_echoed_back_into_the_search_box(signed_in) -> None:
    """Otherwise a hundred thousand characters get rendered into the page, and
    the box shows something that looks like it was searched for."""
    response = signed_in.get("/otsing/", {"q": "z" * 5000})
    assert response.context["query"] == ""
    assert "z" * 600 not in response.content.decode()


# ---------------------------------------------------------------------------
# Deterministic ordering
# ---------------------------------------------------------------------------


def test_equally_ranked_results_come_back_in_the_same_order_every_time(specialist) -> None:
    """Two archive Matters have no reference to break the tie, and two fragments
    of two files can share a locator. Without a unique final sort key
    PostgreSQL is free to reorder them between requests — which moves rows
    across the fifty-result boundary and makes a result disappear on reload.
    """
    for _ in range(12):
        factories.ArchiveMatterFactory(
            owner=specialist, title="Ehitusseadustiku muutmine", reference_year=None
        )
    rebuild_all()

    orders = [
        [document.pk for document in search_documents(query="Ehitusseadustiku", user=specialist)]
        for _ in range(5)
    ]
    assert len({tuple(order) for order in orders}) == 1
    assert len(orders[0]) == 12


# ---------------------------------------------------------------------------
# Idempotence: every targeted refresh converges
# ---------------------------------------------------------------------------


@pytest.fixture
def one_of_everything(specialist, capture_evidence, extract):
    matter = factories.MatterFactory(
        owner=specialist,
        title="Pakendiseaduse muutmise eelnõu",
        reference_year=2026,
        reference_number=77,
    )
    factories.EntryFactory(matter=matter, author=specialist, body="<p>Helistasin nõunikule.</p>")
    factories.SubmissionFactory(matter=matter, title="Koja arvamus", reference="KODA-2026-77")
    version = capture_evidence(
        matter, corpus.text_pdf(["Sisu, mis on ainult failis."]), "lisa.pdf", PDF, title="Lisa"
    )
    extract(version)
    page = _source_page()
    MatterSourcePage.objects.create(
        matter=matter,
        source_page=page,
        relationship_kind=SourceRelationshipKind.PRIMARY,
        match_method=SourceMatchMethod.MANUAL,
        match_class=SourceMatchClass.REVIEWED,
    )
    rebuild_all()
    return matter, version


def _fingerprint() -> list[tuple]:
    return sorted(
        SearchDocument.objects.values_list("source_kind", "source_object_id", "title", "alias_text")
    )


def test_every_targeted_refresh_is_idempotent(one_of_everything) -> None:
    """Run each of them twice; the projection has to be the same table after."""
    from app.search.indexing import (
        refresh_document_version,
        refresh_entry,
        refresh_matter,
        refresh_source_link,
        refresh_submission,
    )

    matter, version = one_of_everything
    before = _fingerprint()
    total = SearchDocument.objects.count()

    for _ in range(2):
        refresh_matter(matter)
        refresh_entry(matter.entries.get())
        refresh_submission(matter.submissions.get())
        refresh_source_link(matter.source_pages.get())
        refresh_document_version(version)

    assert SearchDocument.objects.count() == total
    assert _fingerprint() == before


def test_a_rebuild_that_keeps_existing_rows_does_not_collide_with_them(one_of_everything) -> None:
    """``--keep-existing`` used to raise on the first already-indexed fragment.

    The fragment pass was the one function in the projection that inserted
    without deleting first, on the reasoning that the table had just been
    emptied — true of the *other* mode. On any corpus that had ever been
    indexed, the flag was one ``IntegrityError`` and a rolled-back rebuild.
    """
    before = _fingerprint()
    total = SearchDocument.objects.count()

    result = rebuild_all(clear=False)

    assert result.documents == total
    assert _fingerprint() == before


def test_keeping_existing_rows_still_converges_on_changed_content(
    one_of_everything, specialist
) -> None:
    matter, _ = one_of_everything
    Matter.objects.filter(pk=matter.pk).update(title="Pakendiseaduse uus pealkiri")

    rebuild_all(clear=False)

    assert [r.matter.pk for r in search(query="uus pealkiri", user=specialist)][:1] == [matter.pk]


# ---------------------------------------------------------------------------
# Document fragments: only the live derivative, and never a surplus page
# ---------------------------------------------------------------------------


def test_a_reparse_that_yields_fewer_pages_leaves_no_surplus_rows(
    specialist, capture_evidence, extract
) -> None:
    """The projection for a version is deleted *by version*, not by fragment id.

    Deleting by fragment id only removes rows whose fragment the caller still
    knows about, so a reparse that produced fewer pages than the last one would
    leave the extra rows behind — results pointing at pages the current
    derivative does not have. The surplus row here stands in for exactly that:
    a row for this version whose fragment is no longer among the live ones.
    """
    import uuid

    matter = factories.MatterFactory(owner=specialist, title="Mitmeleheline lisa")
    version = capture_evidence(
        matter,
        corpus.text_pdf(["Esimene lehekülg.", "Teine lehekülg."]),
        "pikk.pdf",
        PDF,
        title="Pikk lisa",
    )
    extract(version)
    live = SearchDocument.objects.filter(
        source_kind=SearchSourceKind.DOCUMENT_FRAGMENT, document_version=version
    ).count()
    assert live >= 2

    SearchDocument.objects.create(
        matter=matter,
        source_kind=SearchSourceKind.DOCUMENT_FRAGMENT,
        source_object_id=uuid.uuid4(),
        document=version.document,
        document_version=version,
        title="Pikk lisa",
        body_text="Kolmas lehekülg, mida enam ei ole.",
        source_locator="lk 3",
        indexed_at=timezone.now(),
    )

    from app.search.indexing import refresh_document_version

    refresh_document_version(version)

    assert (
        SearchDocument.objects.filter(
            source_kind=SearchSourceKind.DOCUMENT_FRAGMENT, document_version=version
        ).count()
        == live
    )
    assert not SearchDocument.objects.filter(source_locator="lk 3", document_version=version)
    assert search(query="Kolmas", user=specialist) == []


def test_a_superseded_derivative_stops_being_searchable(
    specialist, capture_evidence, extract
) -> None:
    """Only the ACTIVE derivative is content; a superseded parse is history.

    Indexing both would return the same page twice with slightly different text
    and no way for a reader to tell which one the system currently believes.
    """
    matter = factories.MatterFactory(owner=specialist, title="Ülekirjutatud lisa")
    version = capture_evidence(
        matter, corpus.text_pdf(["Vana parseri arvamus."]), "lisa.pdf", PDF, title="Lisa"
    )
    extract(version)
    assert search(query="parseri", user=specialist)

    from app.documents.models import DocumentDerivative

    DocumentDerivative.objects.filter(version=version).update(status=DerivativeStatus.SUPERSEDED)
    rebuild_all()

    assert (
        SearchDocument.objects.filter(
            source_kind=SearchSourceKind.DOCUMENT_FRAGMENT, document_version=version
        ).count()
        == 0
    )
    assert search(query="parseri", user=specialist) == []


# ---------------------------------------------------------------------------
# Bulk suspension is per-caller, not per-process
# ---------------------------------------------------------------------------


def test_suspending_indexing_does_not_suppress_it_for_another_thread(specialist) -> None:
    """A module-level flag would have made this fail, and the failure is silent.

    The write that loses its refresh belongs to whoever else was working at the
    time. They suspended nothing, they owe nothing, and their Matter saves
    successfully and cannot be found — with no error anywhere to explain it.
    """
    observed: dict[str, bool] = {}
    inside = threading.Event()
    checked = threading.Event()

    def elsewhere() -> None:
        inside.wait(timeout=10)
        observed["other_thread"] = indexing_is_suspended()
        checked.set()

    other = threading.Thread(target=elsewhere)
    other.start()
    try:
        with suspend_indexing():
            assert indexing_is_suspended() is True
            inside.set()
            checked.wait(timeout=10)
    finally:
        other.join(timeout=10)

    assert observed["other_thread"] is False
    assert indexing_is_suspended() is False


def test_suspension_is_restored_when_the_body_raises() -> None:
    with pytest.raises(RuntimeError):
        with suspend_indexing():
            raise RuntimeError("boom")
    assert indexing_is_suspended() is False


def test_nested_suspension_leaves_the_outer_one_in_force() -> None:
    with suspend_indexing():
        with suspend_indexing():
            assert indexing_is_suspended() is True
        assert indexing_is_suspended() is True
    assert indexing_is_suspended() is False


# ---------------------------------------------------------------------------
# The projection is never canonical, and never carries an authorization value
# ---------------------------------------------------------------------------


def test_no_module_outside_search_reads_the_projection() -> None:
    """Business state must not depend on derived data.

    A domain rule that asked "does a SearchDocument exist" would make a
    rebuild — which empties the table — a business-state change, and the whole
    argument for the projection being disposable would collapse
    (master specification 11.3).

    Parsed rather than grepped, because the two are different assertions and
    only one of them is true. `app/legacy_import/current_state.py` names
    `SearchDocument` three times in its module docstring, to say that
    `CurrentRegisterState` is derived data in exactly the same sense — which is
    the architecture being documented, not violated. What must not exist is a
    *reference*: an import, a name, an attribute. So this walks the syntax tree
    and ignores everything that is only prose.
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "app"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        if relative.startswith("search/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.ImportFrom | ast.Import)
                else [getattr(node, "id", None) or getattr(node, "attr", None)]
            )
            if "SearchDocument" in names:
                offenders.append(relative)
                break
    assert offenders == []


# ---------------------------------------------------------------------------
# The integrity report
# ---------------------------------------------------------------------------


def test_every_searchable_kind_is_counted_by_the_integrity_check() -> None:
    """The registry that says what «healthy» means must know every kind there is.

    `_expected_populations` is a hand-written list, and everything the integrity
    report concludes is scoped to it: a kind absent from it is never counted, so
    its rows can be missing in every one and the command still prints a clean
    bill of health. That is not a hypothetical. AUTH-003 made `Kaasamine` a
    source kind and did not extend this list, and for that whole release a
    consultation could be recorded, never projected into the corpus, and never
    reported — the check said the index was fine while content sat outside it.

    So the two vocabularies are compared rather than counted. A `== 6` here
    would pass the moment somebody added a seventh kind and removed a sixth,
    which is the same defect wearing the right total.

    `_expected_populations` is private and imported anyway: this test *is* the
    completeness contract for that registry, and a contract asserted from
    outside the module it constrains is the only kind that catches a change made
    inside it.
    """
    from app.search.management.commands.check_search_integrity import _expected_populations

    declared = [kind for _, kind, _ in _expected_populations()]

    assert set(declared) == set(SearchSourceKind.values), (
        "the integrity check and the searchable vocabulary disagree. Not counted: "
        f"{sorted(set(SearchSourceKind.values) - set(declared))}; counted but not a "
        f"source kind: {sorted(set(declared) - set(SearchSourceKind.values))}"
    )


def test_no_source_kind_is_counted_twice_by_the_integrity_check() -> None:
    """Set equality alone would not notice a kind listed twice.

    Two entries for one kind is not merely untidy: `build_report` compares each
    line's expected total against the *same* projected count, so a duplicate
    either reports one shortfall as two findings or — if the second line counts
    a different canonical population — makes the report contradict itself about
    the same rows. Uniqueness is asserted separately because it fails for its
    own reason.
    """
    from app.search.management.commands.check_search_integrity import _expected_populations

    declared = [kind for _, kind, _ in _expected_populations()]
    duplicates = sorted({kind for kind in declared if declared.count(kind) > 1})

    assert not duplicates, f"counted more than once by the integrity check: {duplicates}"


def test_a_healthy_index_reports_healthy(one_of_everything) -> None:
    from app.search.management.commands.check_search_integrity import build_report

    report = build_report()
    assert report.ok, [(f.label, f.detail) for f in report.findings]
    assert report.index_versions == {INDEX_VERSION: report.total_rows}


def test_a_missing_matter_row_is_reported(one_of_everything) -> None:
    from app.search.management.commands.check_search_integrity import build_report

    SearchDocument.objects.filter(source_kind=SearchSourceKind.MATTER).delete()

    report = build_report()
    assert not report.ok
    assert any("Teemad" == finding.label for finding in report.findings)


def test_an_older_index_version_is_reported(one_of_everything) -> None:
    from app.search.management.commands.check_search_integrity import build_report

    SearchDocument.objects.filter(source_kind=SearchSourceKind.MATTER).update(index_version="1.0")

    report = build_report()
    assert not report.ok
    assert any(finding.label == "Indeksi versioon" for finding in report.findings)


def test_a_row_without_a_vector_is_reported(one_of_everything) -> None:
    from app.search.management.commands.check_search_integrity import build_report

    SearchDocument.objects.filter(source_kind=SearchSourceKind.ENTRY).update(search_estonian=None)

    report = build_report()
    assert not report.ok
    assert any(finding.label == "Otsinguvektorid" for finding in report.findings)


def test_a_row_pointing_at_another_matter_is_reported(one_of_everything, specialist) -> None:
    """The one projection defect that is a disclosure rather than a nuisance:
    authorization runs through ``SearchDocument.matter``, so a row that names
    the wrong one shows its content to the wrong readers."""
    from app.search.management.commands.check_search_integrity import build_report

    elsewhere = factories.MatterFactory(owner=specialist, title="Hoopis teine teema")
    SearchDocument.objects.filter(source_kind=SearchSourceKind.DOCUMENT_FRAGMENT).update(
        matter=elsewhere
    )

    report = build_report()
    assert not report.ok
    assert any("teemaviide" in finding.label for finding in report.findings)


def test_the_command_exits_non_zero_when_something_is_wrong(one_of_everything) -> None:
    from django.core.management import call_command

    SearchDocument.objects.filter(source_kind=SearchSourceKind.MATTER).delete()
    with pytest.raises(SystemExit):
        call_command("check_search_integrity")


def test_the_command_writes_nothing(one_of_everything) -> None:
    from django.core.management import call_command

    before = _fingerprint()
    call_command("check_search_integrity")
    assert _fingerprint() == before


# ---------------------------------------------------------------------------
# Failure of a targeted refresh must not delete a source out of the index
# ---------------------------------------------------------------------------


def test_a_refresh_that_fails_after_deleting_restores_the_previous_rows(
    one_of_everything, monkeypatch
) -> None:
    """Delete-then-insert is only safe if the pair is atomic.

    Without the transaction, a failure in between leaves the source's every row
    deleted and nothing put back: the document exists, the extraction succeeded,
    and search has silently forgotten it.
    """
    matter, _ = one_of_everything
    before = _fingerprint()

    def explode(*args, **kwargs):
        raise RuntimeError("the insert failed")

    monkeypatch.setattr(SearchDocument.objects, "bulk_create", explode)

    with pytest.raises(RuntimeError), transaction.atomic():
        refresh_matters(indexable_matters().filter(pk=matter.pk))

    monkeypatch.undo()
    assert _fingerprint() == before


def test_a_rolled_back_business_write_takes_its_index_row_with_it(specialist) -> None:
    """Canonical and derived must not disagree in the direction that lies.

    The refresh runs inside the caller's transaction, so a business write that
    aborts leaves the projection where it was. The failure this rules out is
    canonical-old plus search-new: a title in the index that no record anywhere
    ever had, which is worse than staleness because there is nothing to compare
    it against.
    """
    matter = factories.MatterFactory(owner=specialist, title="Kinnitatud pealkiri")
    rebuild_all()

    with pytest.raises(RuntimeError), transaction.atomic():
        matter.title = "Pealkiri, mida ei salvestatud"
        matter.save()
        raise RuntimeError("the rest of the business write failed")

    matter.refresh_from_db()
    assert matter.title == "Kinnitatud pealkiri"
    row = SearchDocument.objects.get(matter=matter, source_kind=SearchSourceKind.MATTER)
    assert row.title == "Kinnitatud pealkiri"
    assert search(query="mida ei salvestatud", user=specialist) == []


# ---------------------------------------------------------------------------
# Rendering the result page must not cost a query per row
# ---------------------------------------------------------------------------


def test_the_result_page_does_not_query_once_per_matter(signed_in, specialist) -> None:
    """The page shows each Matter's open instruction, and `current_action_of`
    falls back to a query per Matter when nothing prefetched it.

    Fifty results is the page cap, so the regression is fifty extra
    round-trips on the busiest query in the product — invisible in a test
    corpus of four and obvious on the real one. Twice the rows, the same
    number of queries.
    """
    from django.db import connection as default_connection
    from django.test.utils import CaptureQueriesContext

    from app.workflow.services import set_next_action

    def add(count: int, offset: int) -> None:
        for index in range(count):
            matter = factories.MatterFactory(
                owner=specialist,
                title=f"Ehitusseadustiku muutmise eelnõu {offset + index}",
            )
            set_next_action(
                matter=matter,
                text=f"Vastata {offset + index}",
                actor=specialist,
                target_date=timezone.localdate(),
            )

    add(10, 0)
    with CaptureQueriesContext(default_connection) as first:
        response = signed_in.get("/otsing/", {"q": "Ehitusseadustiku"})
    assert response.status_code == 200
    assert len(response.context["rows"]) == 10

    add(10, 10)
    with CaptureQueriesContext(default_connection) as second:
        response = signed_in.get("/otsing/", {"q": "Ehitusseadustiku"})
    assert len(response.context["rows"]) == 20

    assert len(second) == len(first)


# ---------------------------------------------------------------------------
# A rebuild must not make ordinary work fail
# ---------------------------------------------------------------------------


#: A note on references in this file.
#:
#: `MatterFactory.reference_number` is a `factory.Sequence`, and a sequence is
#: counted per *process*, not per test. Three Matters below used to pin a
#: literal — 501, 808, 900+ — against that counter, which holds only while no
#: shard ever creates that many Matters before reaching this file. CI partitions
#: by live test count, so adding a test anywhere re-shards, and on 2026-08-31 a
#: shard reached 501 and `matters_unique_human_reference` failed a concurrency
#: test that has nothing to do with references. None of the three ever asserted
#: on the reference; they now take the sequence like every other Matter here.

LOCK_WAIT_TIMEOUT = 20


def _wait_for_a_blocked_backend() -> None:
    """Block until PostgreSQL reports a backend queued on a lock.

    Lines the two transactions up so the interleaving under test actually
    happens, rather than hoping a sleep is long enough. Deliberately not
    asserted on: whether the wait was *observed* is a timing detail, while the
    invariant the test exists for is what the two transactions leave behind
    (tests/test_concurrency.py).
    """
    deadline = timezone.now() + timedelta(seconds=LOCK_WAIT_TIMEOUT)
    while timezone.now() < deadline:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE cardinality(pg_blocking_pids(pid)) > 0"
            )
            if cursor.fetchone()[0] >= 1:
                return


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_a_matter_save_during_a_full_rebuild_still_succeeds(specialist, monkeypatch) -> None:
    """The race, with both halves in real transactions on real connections.

    A full rebuild empties the table and refills it in one transaction. A
    Matter save that lands in the middle deletes the row it is about to
    replace, blocks on the rebuild, and then finds its delete matched nothing —
    the row it could see is gone and the row the rebuild inserted is not in its
    statement's snapshot. The insert that follows violates
    ``search_one_document_per_source_object``, and because the refresh runs
    inside the *business* transaction, what rolls back is the user's save.

    The interleaving is forced rather than hoped for: the rebuild is held open
    after it has emptied and refilled the Matter rows, the save is started and
    allowed to queue on a lock, and only then is the rebuild released. Without
    the gate this raises ``IntegrityError`` in the save thread every time.
    """
    matter = factories.MatterFactory(owner=specialist, title="Algne pealkiri")
    for index in range(20):
        factories.MatterFactory(owner=specialist, title=f"Taustateema {index}")
    rebuild_all()

    from app.search import indexing

    original_children = indexing._rebuild_children
    refilled = threading.Event()
    release = threading.Event()
    failures: list[BaseException] = []

    def paused_children(*args, **kwargs):
        refilled.set()
        release.wait(timeout=LOCK_WAIT_TIMEOUT * 2)
        return original_children(*args, **kwargs)

    monkeypatch.setattr(indexing, "_rebuild_children", paused_children)

    def rebuild() -> None:
        try:
            rebuild_all()
        except BaseException as error:
            failures.append(error)
        finally:
            connections.close_all()

    def save() -> None:
        try:
            with transaction.atomic():
                saved = Matter.objects.get(pk=matter.pk)
                saved.title = "Uus pealkiri"
                saved.save()
        except BaseException as error:
            failures.append(error)
        finally:
            connections.close_all()

    rebuilder = threading.Thread(target=rebuild)
    saver = threading.Thread(target=save)
    rebuilder.start()
    try:
        assert refilled.wait(timeout=LOCK_WAIT_TIMEOUT), "the rebuild never reached its child pass"
        saver.start()
        _wait_for_a_blocked_backend()
    finally:
        release.set()
    rebuilder.join(timeout=LOCK_WAIT_TIMEOUT * 3)
    saver.join(timeout=LOCK_WAIT_TIMEOUT * 3)

    assert failures == [], failures
    matter.refresh_from_db()
    assert matter.title == "Uus pealkiri"
    rows = SearchDocument.objects.filter(matter=matter, source_kind=SearchSourceKind.MATTER)
    assert rows.count() == 1
    # And the committed change is what the index holds, not the title the
    # rebuild read before the save landed.
    assert rows.get().title == "Uus pealkiri"
