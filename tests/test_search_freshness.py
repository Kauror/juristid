"""SEARCH-001: does the projection converge without a human noticing?

Every test here failed on `adce39e`, and each one is a way a lawyer experiences
the same thing: they type a word that is true of the record and the system says
nothing was found. The record is fine. The canonical write committed. Nobody
gets an error, and on current main nothing anywhere says the corpus went stale —
which is what separates a freshness defect from a bug.

The file is organised the way the architecture is:

* **bounded fanout** — a `Kaasamine` is one row, so it is refreshed inside the
  business transaction and is findable the instant it is saved;
* **high fanout** — a rename can invalidate the whole corpus, so it records a
  durable obligation instead, and a consumer converges it;
* **failure injection** — the obligation has to survive a rollback removing it,
  a crash keeping it, and a failed rebuild leaving both it and the previous
  complete index intact;
* **authorization** — freshness must never make an unsafe row fresher.

The public search service is what most of these assert against, not the
`SearchDocument` rows. A row can be present, correct and unmatchable — a null
tsvector does exactly that — so "the row is there" is not the claim being
tested. "The lawyer finds it" is.
"""

from __future__ import annotations

import threading

import pytest
from django.core.management import call_command
from django.db import connections, transaction
from django.utils import timezone

from app.core.enums import Visibility
from app.matters.models import MatterEngagement, MatterSourceOrganisation
from app.matters.services import add_engagement, create_matter, update_engagement
from app.organisations.models import OrganisationAlias
from app.search import freshness
from app.search.indexing import rebuild_all, suspend_indexing
from app.search.models import (
    SearchDocument,
    SearchRebuildDebt,
    SearchRebuildReason,
    SearchSourceKind,
)
from app.search.services import search
from app.taxonomy.models import TagAlias
from tests import factories

pytestmark = pytest.mark.django_db


def found(term: str, user: object) -> list[tuple[str, str]]:
    """What the public search service returns, as (kind, matter)."""
    return sorted(
        (result.source_kind, str(result.matter.pk)) for result in search(query=term, user=user)
    )


def consume() -> freshness.ConsumeResult:
    return freshness.consume_once()


# ---------------------------------------------------------------------------
# Bounded fanout: a `Kaasamine` is findable the moment it is recorded
# ---------------------------------------------------------------------------
#
# AUTH-003 gave engagements their own search row and no way for one to arrive.
# Between that release and this one, every consultation recorded through the
# product was outside the corpus, and `check_search_integrity` did not count the
# kind — so nothing reported it either.


def test_a_recorded_engagement_is_searchable_without_a_rebuild(specialist):
    matter = factories.MatterFactory(owner=specialist, title="Pakendiseaduse muutmine")
    rebuild_all()

    add_engagement(
        matter=matter,
        kind="SURVEY",
        title="Liikmete küsitlus pakendiaktsiisist",
        note="Vastuseid kogutakse septembri lõpuni",
        actor=specialist,
    )

    assert found("pakendiaktsiisist", specialist) == [
        (SearchSourceKind.ENGAGEMENT.value, str(matter.pk))
    ]


def test_an_engagement_row_carries_a_vector(specialist):
    """A row without one exists, counts as indexed and can never match."""
    matter = factories.MatterFactory(owner=specialist)
    add_engagement(matter=matter, kind="SURVEY", title="Küsitlus", actor=specialist)

    row = SearchDocument.objects.get(source_kind=SearchSourceKind.ENGAGEMENT)
    assert row.search_estonian is not None
    assert row.search_simple is not None


def test_editing_an_engagement_replaces_what_it_says(specialist):
    matter = factories.MatterFactory(owner=specialist)
    engagement = add_engagement(
        matter=matter, kind="SURVEY", title="Küsitlus kalandusest", actor=specialist
    )

    update_engagement(engagement=engagement, title="Küsitlus metsandusest", actor=specialist)

    assert found("metsandusest", specialist) == [
        (SearchSourceKind.ENGAGEMENT.value, str(matter.pk))
    ]
    assert found("kalandusest", specialist) == []


def test_deleting_an_engagement_removes_its_row_through_the_cascade(specialist):
    """Asserted on the schema rather than on a handler, because the schema is
    what guarantees it: `SearchDocument.engagement` is a real FK with CASCADE."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = add_engagement(
        matter=matter, kind="SURVEY", title="Küsitlus tuulikutest", actor=specialist
    )
    assert found("tuulikutest", specialist)

    engagement.delete()

    assert SearchDocument.objects.filter(source_kind=SearchSourceKind.ENGAGEMENT).count() == 0
    assert found("tuulikutest", specialist) == []


def test_the_integrity_check_counts_engagements(specialist):
    from app.search.management.commands.check_search_integrity import build_report

    matter = factories.MatterFactory(owner=specialist)
    # Under suspension, so the row is canonical and unprojected — which is what
    # a bulk writer that forgot its refresh leaves behind, and what this check
    # existed to notice and did not.
    with suspend_indexing():
        MatterEngagement.objects.create(matter=matter, kind="SURVEY", title="Otse baasi")

    report = build_report()

    labels = {label: (expected, actual) for label, expected, actual in report.counts}
    assert labels["Kaasamised"] == (1, 0)
    assert any(finding.label == "Kaasamised" for finding in report.findings)


def test_a_bulk_writer_can_still_suspend_the_engagement_refresh(specialist):
    matter = factories.MatterFactory(owner=specialist)
    with suspend_indexing():
        add_engagement(matter=matter, kind="SURVEY", title="Vaikne kaasamine", actor=specialist)

    assert SearchDocument.objects.filter(source_kind=SearchSourceKind.ENGAGEMENT).count() == 0
    rebuild_all()
    assert SearchDocument.objects.filter(source_kind=SearchSourceKind.ENGAGEMENT).count() == 1


# ---------------------------------------------------------------------------
# Bounded fanout: a Matter is findable by whoever sent it
# ---------------------------------------------------------------------------
#
# `_alias_text_for` indexes every sender, so that a Matter which arrived from a
# ministry and an association is findable through either (ADR 0025). Nothing
# refreshed the row when that list changed, and the create path indexed the
# Matter before the senders were attached — so a Matter created through the
# product with its sender filled in was not findable by that sender at all.


def test_a_new_matter_is_findable_by_the_sender_it_was_created_with(specialist):
    organisation = factories.OrganisationFactory(name="Loomeliitude Keskliit")

    matter = create_matter(
        title="Autoriõiguse seaduse muutmine",
        owner=specialist,
        actor=specialist,
        source_organisations=[organisation],
    )

    assert found("Loomeliitude", specialist) == [(SearchSourceKind.MATTER.value, str(matter.pk))]


def test_attaching_a_sender_afterwards_makes_it_findable(specialist):
    organisation = factories.OrganisationFactory(name="Loomeliitude Keskliit")
    matter = factories.MatterFactory(owner=specialist, title="Teema ilma saatjata")
    rebuild_all()
    assert found("Loomeliitude", specialist) == []

    matter.source_organisations.add(organisation)

    assert found("Loomeliitude", specialist) == [(SearchSourceKind.MATTER.value, str(matter.pk))]


def test_a_sender_row_created_directly_makes_it_findable(specialist):
    """The through model has two write paths and each misses the other's signal."""
    organisation = factories.OrganisationFactory(name="Loomeliitude Keskliit")
    matter = factories.MatterFactory(owner=specialist, title="Teema ilma saatjata")
    rebuild_all()

    MatterSourceOrganisation.objects.create(matter=matter, organisation=organisation)

    assert found("Loomeliitude", specialist) == [(SearchSourceKind.MATTER.value, str(matter.pk))]


def test_removing_a_sender_stops_it_being_findable_by_that_name(specialist):
    organisation = factories.OrganisationFactory(name="Loomeliitude Keskliit")
    matter = factories.MatterFactory(owner=specialist, title="Teema saatjaga")
    matter.source_organisations.add(organisation)
    assert found("Loomeliitude", specialist)

    matter.source_organisations.remove(organisation)

    assert found("Loomeliitude", specialist) == []


def test_the_second_sender_is_indexed_too(specialist):
    """The failure ADR 0025 named: the search returns results, just not that one."""
    ministry = factories.OrganisationFactory(name="Kultuuriministeerium")
    association = factories.OrganisationFactory(name="Loomeliitude Keskliit")
    matter = factories.MatterFactory(owner=specialist, title="Autoriõiguse teema")

    matter.source_organisations.set([ministry, association])

    assert found("Kultuuriministeerium", specialist) == [
        (SearchSourceKind.MATTER.value, str(matter.pk))
    ]
    assert found("Loomeliitude", specialist) == [(SearchSourceKind.MATTER.value, str(matter.pk))]


# ---------------------------------------------------------------------------
# High fanout: the rename owes a rebuild, and something pays it
# ---------------------------------------------------------------------------


def test_renaming_an_organisation_converges_without_anybody_running_a_rebuild(specialist):
    organisation = factories.OrganisationFactory(name="Rahandusministeerium")
    matter = factories.MatterFactory(owner=specialist, title="Aktsiisimäärade muutmine")
    matter.source_organisations.add(organisation)
    rebuild_all()
    assert found("Rahandusministeerium", specialist)

    organisation.name = "Riigirahanduse amet"
    organisation.save()

    # The rename alone is not enough, and it is not supposed to be: the work is
    # deferred, and what SEARCH-001 adds is that the deferral is recorded.
    assert freshness.status().owed == 1
    assert freshness.status().reasons == {SearchRebuildReason.ORGANISATION_RENAMED.value: 1}

    consume()

    assert found("Riigirahanduse", specialist) == [(SearchSourceKind.MATTER.value, str(matter.pk))]
    assert found("Rahandusministeerium", specialist) == []
    assert freshness.status().is_clear


def test_a_recipient_only_organisation_rename_converges_too(specialist):
    """The case the drift detector could never see.

    `_stale_matter_text` recomputes MATTER rows. An organisation that is only
    ever a submission recipient appears in no MATTER row's text, so renaming it
    left the SUBMISSION rows stale with the integrity check reporting nothing at
    all — a canonical write that committed, a projection that did not, and no
    detector anywhere.
    """
    from app.submissions.services import set_recipients

    ministry = factories.OrganisationFactory(name="Vesiviljelusamet")
    matter = factories.MatterFactory(owner=specialist, title="Rannikuvete teema")
    submission = factories.SubmissionFactory(matter=matter, title="Koja arvamus")
    set_recipients(submission=submission, addressees=[ministry], actor=specialist)
    rebuild_all()
    assert found("Vesiviljelusamet", specialist)

    ministry.name = "Turbatootmisamet"
    ministry.save()
    consume()

    assert found("Turbatootmisamet", specialist) == [
        (SearchSourceKind.SUBMISSION.value, str(matter.pk))
    ]
    assert found("Vesiviljelusamet", specialist) == []


def test_renaming_a_tag_converges(specialist):
    tag = factories.TagFactory(name_et="Kastikaubandus")
    matter = factories.MatterFactory(owner=specialist, title="Sildistatud teema")
    matter.tags.add(tag)
    rebuild_all()

    tag.name_et = "Pudelikaubandus"
    tag.save()
    assert freshness.status().reasons == {SearchRebuildReason.TAG_RENAMED.value: 1}
    consume()

    assert found("Pudelikaubandus", specialist) == [(SearchSourceKind.MATTER.value, str(matter.pk))]


def test_renaming_a_policy_area_converges(specialist):
    area = factories.PolicyAreaFactory(name_et="Merendusvaldkond")
    matter = factories.MatterFactory(owner=specialist, title="Valdkonnaga teema")
    matter.policy_areas.add(area)
    rebuild_all()

    area.name_et = "Lennundusvaldkond"
    area.save()
    assert freshness.status().reasons == {SearchRebuildReason.POLICY_AREA_RENAMED.value: 1}
    consume()

    assert found("Lennundusvaldkond", specialist) == [
        (SearchSourceKind.MATTER.value, str(matter.pk))
    ]


def test_adding_an_organisation_alias_converges(specialist):
    organisation = factories.OrganisationFactory(name="Majandusministeerium")
    matter = factories.MatterFactory(owner=specialist, title="Majanduse teema")
    matter.source_organisations.add(organisation)
    rebuild_all()

    OrganisationAlias.objects.create(organisation=organisation, alias="MKMLYHEND")
    assert freshness.status().reasons == {SearchRebuildReason.ORGANISATION_ALIAS_CHANGED.value: 1}
    consume()

    assert found("MKMLYHEND", specialist) == [(SearchSourceKind.MATTER.value, str(matter.pk))]


def test_removing_a_tag_alias_converges(specialist):
    tag = factories.TagFactory(name_et="Ringmajandus")
    alias = TagAlias.objects.create(tag=tag, alias="ZULUKOOD")
    matter = factories.MatterFactory(owner=specialist, title="Ringmajanduse teema")
    matter.tags.add(tag)
    # Creating the alias already owed a rebuild; discharge it so this test is
    # about the delete alone.
    consume()
    assert found("ZULUKOOD", specialist)

    alias.delete()
    assert freshness.status().reasons == {SearchRebuildReason.TAG_ALIAS_CHANGED.value: 1}
    consume()

    assert found("ZULUKOOD", specialist) == []


def test_renaming_a_person_converges_their_entries(specialist):
    matter = factories.MatterFactory(owner=specialist, title="Sissekandega teema")
    factories.EntryFactory(matter=matter, author=specialist, body="<p>koosoleku märkmed</p>")
    rebuild_all()

    specialist.display_name = "Nimemuutus Testkasutaja"
    specialist.save()
    assert freshness.status().reasons == {SearchRebuildReason.PERSON_RENAMED.value: 1}
    consume()

    assert found("Nimemuutus", specialist) == [(SearchSourceKind.ENTRY.value, str(matter.pk))]


# ---------------------------------------------------------------------------
# What must *not* owe a rebuild
# ---------------------------------------------------------------------------


def test_creating_reference_data_owes_no_rebuild(specialist):
    """Nothing is stale before anything points at it."""
    factories.OrganisationFactory(name="Uus asutus")
    factories.TagFactory(name_et="Uus silt")
    factories.PolicyAreaFactory(name_et="Uus valdkond")

    assert freshness.status().is_clear


def test_saving_an_organisation_without_renaming_it_owes_no_rebuild(specialist):
    organisation = factories.OrganisationFactory(name="Sama nimi")
    organisation.save()
    organisation.organisation_type = organisation.organisation_type
    organisation.save()

    assert freshness.status().is_clear


def test_a_save_that_cannot_touch_a_name_is_never_even_read(specialist):
    """`update_fields` is a promise, and the handler takes it.

    A user row is saved on every sign-in. If the freshness handler read the
    stored row each time to compare a display name that the save cannot
    possibly have changed, this subsystem would put a query on the login path
    forever — so the assertion is about the queries it does *not* issue rather
    than a total, which belongs to whatever else is on that path.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    specialist.last_login = timezone.now()
    with CaptureQueriesContext(connection) as queries:
        specialist.save(update_fields=["last_login"])

    statements = [query["sql"] for query in queries.captured_queries]
    assert not any("searchrebuilddebt" in sql for sql in statements)
    # One SELECT is `accounts`' own pre-save hook, which predates this and reads
    # `entra_object_id`. What must not be here is a read of the *name* — that
    # would be this subsystem comparing a field the save promised not to touch.
    assert not any(
        sql.lstrip().upper().startswith('SELECT "ACCOUNTS_USER"."DISPLAY_NAME"')
        for sql in (statement.upper() for statement in statements)
    )
    assert freshness.status().is_clear


def test_an_ordinary_matter_save_owes_no_rebuild(specialist):
    matter = factories.MatterFactory(owner=specialist, title="Tavaline teema")
    matter.title = "Muudetud pealkiri"
    matter.save()

    # Bounded fanout is refreshed, not deferred.
    assert freshness.status().is_clear
    assert found("Muudetud", specialist) == [(SearchSourceKind.MATTER.value, str(matter.pk))]


# ---------------------------------------------------------------------------
# Coalescing
# ---------------------------------------------------------------------------


def test_many_invalidations_become_one_rebuild(specialist):
    matter = factories.MatterFactory(owner=specialist, title="Palju muudatusi")
    organisations = [factories.OrganisationFactory(name=f"Asutus {i}") for i in range(5)]
    for organisation in organisations:
        matter.source_organisations.add(organisation)
    rebuild_all()

    for index, organisation in enumerate(organisations):
        organisation.name = f"Ümbernimetatud {index}"
        organisation.save()

    assert freshness.status().owed == 5
    outcome = consume()

    # One rebuild, five obligations discharged.
    assert outcome.rebuilt is True
    assert outcome.cleared == 5
    assert freshness.status().is_clear
    assert found("Ümbernimetatud", specialist) == [(SearchSourceKind.MATTER.value, str(matter.pk))]


def test_consuming_with_nothing_owed_does_not_rebuild(specialist):
    factories.MatterFactory(owner=specialist)
    rebuild_all()
    stamps = set(SearchDocument.objects.values_list("indexed_at", flat=True))

    outcome = consume()

    assert outcome.rebuilt is False
    assert outcome.cleared == 0
    assert set(SearchDocument.objects.values_list("indexed_at", flat=True)) == stamps


# ---------------------------------------------------------------------------
# Failure injection
# ---------------------------------------------------------------------------


def test_a_rolled_back_transaction_leaves_no_debt(specialist):
    """The obligation lives in the mutation's transaction, so it shares its fate."""
    organisation = factories.OrganisationFactory(name="Enne")

    class Rollback(Exception):
        pass

    with pytest.raises(Rollback), transaction.atomic():
        organisation.name = "Pärast"
        organisation.save()
        assert SearchRebuildDebt.objects.count() == 1
        raise Rollback

    organisation.refresh_from_db()
    assert organisation.name == "Enne"
    assert freshness.status().is_clear


def test_a_committed_transaction_leaves_debt_that_survives_the_process(specialist):
    """Not an on-commit callback: a row, readable by a process that was not there."""
    organisation = factories.OrganisationFactory(name="Enne")
    with transaction.atomic():
        organisation.name = "Pärast"
        organisation.save()

    # Read through a fresh queryset, exactly as a worker started afterwards
    # would: nothing about this depends on the marking process still existing.
    assert SearchRebuildDebt.objects.filter(
        reason=SearchRebuildReason.ORGANISATION_RENAMED
    ).exists()


def test_a_failed_rebuild_keeps_the_previous_index_and_the_debt(monkeypatch, specialist):
    organisation = factories.OrganisationFactory(name="Rahandusministeerium")
    matter = factories.MatterFactory(owner=specialist, title="Aktsiisiteema")
    matter.source_organisations.add(organisation)
    rebuild_all()
    before = set(SearchDocument.objects.values_list("pk", flat=True))

    organisation.name = "Riigirahanduse amet"
    organisation.save()

    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("indeksi ehitamine katkes")

    monkeypatch.setattr(freshness, "rebuild_all", explode)
    with pytest.raises(RuntimeError):
        consume()

    # The old index is still complete and still serving.
    assert set(SearchDocument.objects.values_list("pk", flat=True)) == before
    assert found("Rahandusministeerium", specialist) == [
        (SearchSourceKind.MATTER.value, str(matter.pk))
    ]
    # The debt is still owed, and now says it has been tried.
    state = freshness.status()
    assert state.owed == 1
    assert state.failed_attempts == 1
    assert "katkes" in state.last_error


def test_a_retry_after_a_failed_rebuild_converges(monkeypatch, specialist):
    organisation = factories.OrganisationFactory(name="Rahandusministeerium")
    matter = factories.MatterFactory(owner=specialist, title="Aktsiisiteema")
    matter.source_organisations.add(organisation)
    rebuild_all()

    organisation.name = "Riigirahanduse amet"
    organisation.save()

    calls = {"n": 0}
    real = freshness.rebuild_all

    def flaky(*args: object, **kwargs: object) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("esimene katse ebaõnnestus")
        return real(*args, **kwargs)

    monkeypatch.setattr(freshness, "rebuild_all", flaky)
    with pytest.raises(RuntimeError):
        consume()
    consume()

    assert freshness.status().is_clear
    assert found("Riigirahanduse", specialist) == [(SearchSourceKind.MATTER.value, str(matter.pk))]


def test_a_mark_arriving_during_a_rebuild_is_not_cleared_by_it(specialist):
    """The claim is by primary key, taken before the rebuild starts.

    A mark that lands mid-rebuild may or may not be inside that rebuild's
    snapshot, and nothing outside PostgreSQL can tell which. So it must not be
    cleared by it — one redundant rebuild is the correct price for never
    silently discarding a change.
    """
    organisation = factories.OrganisationFactory(name="Esimene")
    matter = factories.MatterFactory(owner=specialist, title="Teema")
    matter.source_organisations.add(organisation)
    rebuild_all()

    organisation.name = "Teine"
    organisation.save()
    assert freshness.status().owed == 1

    later = factories.TagFactory(name_et="Silt")

    real = freshness.rebuild_all

    def rebuild_then_mark(*args: object, **kwargs: object) -> object:
        result = real(*args, **kwargs)
        # Committed after the claim was taken, standing in for a rename that
        # commits while the rebuild is in flight.
        later.name_et = "Uus silt"
        later.save()
        return result

    freshness.rebuild_all = rebuild_then_mark  # type: ignore[assignment]
    try:
        outcome = consume()
    finally:
        freshness.rebuild_all = real  # type: ignore[assignment]

    assert outcome.cleared == 1
    state = freshness.status()
    assert state.owed == 1
    assert state.reasons == {SearchRebuildReason.TAG_RENAMED.value: 1}


def test_debt_is_never_cleared_before_the_rebuild_commits(specialist):
    """The delete is issued after `rebuild_all` returns, never inside it."""
    organisation = factories.OrganisationFactory(name="Enne")
    factories.MatterFactory(owner=specialist)
    organisation.name = "Pärast"
    organisation.save()

    seen: list[int] = []
    real = freshness.rebuild_all

    def observe(*args: object, **kwargs: object) -> object:
        seen.append(SearchRebuildDebt.objects.count())
        return real(*args, **kwargs)

    freshness.rebuild_all = observe  # type: ignore[assignment]
    try:
        consume()
    finally:
        freshness.rebuild_all = real  # type: ignore[assignment]

    assert seen == [1]
    assert SearchRebuildDebt.objects.count() == 0


# ---------------------------------------------------------------------------
# The operator's view
# ---------------------------------------------------------------------------


def test_the_freshness_probe_passes_when_nothing_is_owed(specialist):
    call_command("check_search_freshness", "--quiet")


def test_the_freshness_probe_tolerates_work_in_progress(specialist):
    organisation = factories.OrganisationFactory(name="Enne")
    organisation.name = "Pärast"
    organisation.save()

    # Pending is not a fault: the worker has not had its idle period yet.
    call_command("check_search_freshness", "--quiet")


def test_the_freshness_probe_fails_once_the_debt_is_stale(specialist):
    organisation = factories.OrganisationFactory(name="Enne")
    organisation.name = "Pärast"
    organisation.save()

    with pytest.raises(SystemExit):
        call_command("check_search_freshness", "--quiet", "--max-seconds", "0")


def test_the_freshness_probe_fails_on_a_failed_rebuild(specialist):
    organisation = factories.OrganisationFactory(name="Enne")
    organisation.name = "Pärast"
    organisation.save()
    SearchRebuildDebt.objects.update(
        attempts=2, last_attempt_at=timezone.now(), last_error="ei õnnestunud"
    )

    with pytest.raises(SystemExit):
        call_command("check_search_freshness", "--quiet")


def test_the_integrity_check_reports_debt_without_consuming_it(specialist):
    from app.search.management.commands.check_search_integrity import build_report

    factories.MatterFactory(owner=specialist)
    rebuild_all()
    organisation = factories.OrganisationFactory(name="Enne")
    organisation.name = "Pärast"
    organisation.save()

    report = build_report(sample=0)

    assert report.freshness is not None
    assert report.freshness.owed == 1
    # Pending work is a fact, not a fault.
    assert report.ok
    # And the report read it rather than draining it.
    assert SearchRebuildDebt.objects.count() == 1


def test_the_integrity_check_reports_a_debt_nothing_is_converging(settings, specialist):
    from app.search.management.commands.check_search_integrity import build_report

    settings.SEARCH_REBUILD_DEBT_STALE_SECONDS = 0
    factories.MatterFactory(owner=specialist)
    rebuild_all()
    organisation = factories.OrganisationFactory(name="Enne")
    organisation.name = "Pärast"
    organisation.save()

    report = build_report(sample=0)

    assert not report.ok
    assert any(finding.label == "Indeksi võlg" for finding in report.findings)
    assert SearchRebuildDebt.objects.count() == 1


def test_the_worker_pays_off_what_is_owed_and_exits(specialist):
    organisation = factories.OrganisationFactory(name="Rahandusministeerium")
    matter = factories.MatterFactory(owner=specialist, title="Aktsiisiteema")
    matter.source_organisations.add(organisation)
    rebuild_all()
    organisation.name = "Riigirahanduse amet"
    organisation.save()

    call_command("run_search_refresh_worker", "--once")

    assert freshness.status().is_clear
    assert found("Riigirahanduse", specialist) == [(SearchSourceKind.MATTER.value, str(matter.pk))]


def test_the_worker_is_a_no_op_when_nothing_is_owed(specialist):
    factories.MatterFactory(owner=specialist)
    rebuild_all()
    stamps = set(SearchDocument.objects.values_list("indexed_at", flat=True))

    call_command("run_search_refresh_worker", "--once")

    assert set(SearchDocument.objects.values_list("indexed_at", flat=True)) == stamps


# ---------------------------------------------------------------------------
# Authorization: freshness must never make an unsafe row fresher
# ---------------------------------------------------------------------------


def test_a_restricted_engagement_refreshed_automatically_stays_invisible(specialist):
    matter = factories.MatterFactory(owner=specialist, title="Avalik teema")
    engagement = add_engagement(
        matter=matter, kind="SURVEY", title="SALAJANE-KAASAMINE", actor=specialist
    )
    engagement.visibility_override = Visibility.RESTRICTED
    engagement.save(update_fields=["visibility_override"])

    stranger = factories.UserFactory()
    assert found("SALAJANE-KAASAMINE", stranger) == []
    assert found("SALAJANE-KAASAMINE", specialist) == [
        (SearchSourceKind.ENGAGEMENT.value, str(matter.pk))
    ]


def test_restricting_an_engagement_takes_effect_without_any_refresh(specialist):
    """The projection stores no visibility, and a freshness path must not change that."""
    matter = factories.MatterFactory(owner=specialist, title="Avalik teema")
    engagement = add_engagement(
        matter=matter, kind="SURVEY", title="KAASAMINE-NÄHTAV", actor=specialist
    )
    stranger = factories.UserFactory()
    assert found("KAASAMINE-NÄHTAV", stranger)

    engagement.visibility_override = Visibility.RESTRICTED
    engagement.save(update_fields=["visibility_override"])

    assert found("KAASAMINE-NÄHTAV", stranger) == []


def test_a_rebuild_paying_off_debt_preserves_child_restrictions(specialist):
    matter = factories.MatterFactory(owner=specialist, title="Avalik teema")
    organisation = factories.OrganisationFactory(name="Ministeerium")
    matter.source_organisations.add(organisation)
    engagement = add_engagement(
        matter=matter, kind="SURVEY", title="SALAJANE-KAASAMINE", actor=specialist
    )
    engagement.visibility_override = Visibility.RESTRICTED
    engagement.save(update_fields=["visibility_override"])

    organisation.name = "Uus ministeerium"
    organisation.save()
    consume()

    stranger = factories.UserFactory()
    assert found("SALAJANE-KAASAMINE", stranger) == []
    assert found("Uus ministeerium", stranger) == [(SearchSourceKind.MATTER.value, str(matter.pk))]


def test_no_engagement_text_leaks_into_the_matter_row(specialist):
    """AUTH-003's central invariant, re-asserted on the new arrival path.

    The row a signal writes has to be the row a rebuild writes. If the bounded
    refresh composed searchable text of its own, this is where it would show.
    """
    matter = factories.MatterFactory(owner=specialist, title="Avalik teema")
    add_engagement(matter=matter, kind="SURVEY", title="SALAJANE-KAASAMINE", actor=specialist)

    matter_row = SearchDocument.objects.get(source_kind=SearchSourceKind.MATTER, matter=matter)
    assert "SALAJANE" not in matter_row.body_text
    assert "SALAJANE" not in matter_row.title
    assert "SALAJANE" not in matter_row.alias_text


def test_the_signal_and_the_rebuild_write_the_same_row(specialist):
    """One projection contract, two triggers.

    The rebuild is the reference implementation; a refresh path that produced
    anything different would be a second definition of what a `Kaasamine` says.
    """
    matter = factories.MatterFactory(owner=specialist)
    add_engagement(
        matter=matter,
        kind="SURVEY",
        title="Küsitlus",
        note="Märkus",
        url="https://kysitlus.example/abc?utm_source=x",
        actor=specialist,
    )
    columns = ("title", "identifiers", "alias_text", "body_text", "source_locator")
    by_signal = SearchDocument.objects.filter(source_kind=SearchSourceKind.ENGAGEMENT).values(
        *columns
    )[0]

    rebuild_all()

    by_rebuild = SearchDocument.objects.filter(source_kind=SearchSourceKind.ENGAGEMENT).values(
        *columns
    )[0]
    assert by_signal == by_rebuild


# ---------------------------------------------------------------------------
# Two consumers
# ---------------------------------------------------------------------------

LOCK_WAIT_TIMEOUT = 20


@pytest.mark.django_db(transaction=True)
def test_two_consumers_do_not_corrupt_each_other(specialist) -> None:
    """Nothing here assumes it is the only worker.

    Both claim the same obligations, because claiming is a read. The advisory
    lock in `app/search/indexing.py` keeps their rebuilds from overlapping, and
    the second clear is a delete that matches nothing — which is the correct
    outcome and not an error. What must not happen is a half-built index, a
    surviving obligation nobody owes, or an exception in either thread.
    """
    organisation = factories.OrganisationFactory(name="Rahandusministeerium")
    matter = factories.MatterFactory(owner=specialist, title="Aktsiisiteema")
    matter.source_organisations.add(organisation)
    rebuild_all()
    organisation.name = "Riigirahanduse amet"
    organisation.save()

    failures: list[BaseException] = []
    outcomes: list[freshness.ConsumeResult] = []

    def run() -> None:
        try:
            outcomes.append(consume())
        except BaseException as error:  # pragma: no cover - reported by the assert
            failures.append(error)
        finally:
            connections.close_all()

    workers = [threading.Thread(target=run) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=LOCK_WAIT_TIMEOUT)

    assert failures == [], failures
    assert len(outcomes) == 2
    assert sum(outcome.cleared for outcome in outcomes) == 1
    assert freshness.status().is_clear
    assert found("Riigirahanduse", specialist) == [(SearchSourceKind.MATTER.value, str(matter.pk))]
    assert SearchDocument.objects.filter(source_kind=SearchSourceKind.MATTER).count() == 1


@pytest.mark.django_db(transaction=True)
def test_a_matter_save_during_a_debt_driven_rebuild_still_succeeds(specialist) -> None:
    """The consumer inherits the rebuild gate rather than reinventing one.

    `consume_once` performs its repair through `rebuild_all`, so the shared and
    exclusive advisory locks that keep an operator's rebuild from failing a
    lawyer's save apply unchanged to a rebuild nobody asked for. That property
    matters more now than it did: before SEARCH-001 a rebuild happened when
    somebody chose to run one, and now it can happen at any moment.
    """
    organisation = factories.OrganisationFactory(name="Enne")
    matter = factories.MatterFactory(owner=specialist, title="Algne pealkiri")
    matter.source_organisations.add(organisation)
    for index in range(20):
        factories.MatterFactory(owner=specialist, title=f"Taustateema {index}")
    rebuild_all()
    organisation.name = "Pärast"
    organisation.save()

    failures: list[BaseException] = []

    def rebuild() -> None:
        try:
            consume()
        except BaseException as error:  # pragma: no cover - reported by the assert
            failures.append(error)
        finally:
            connections.close_all()

    def save() -> None:
        try:
            with transaction.atomic():
                saved = matter.__class__.objects.get(pk=matter.pk)
                saved.title = "Uus pealkiri"
                saved.save()
        except BaseException as error:  # pragma: no cover - reported by the assert
            failures.append(error)
        finally:
            connections.close_all()

    rebuilder = threading.Thread(target=rebuild)
    saver = threading.Thread(target=save)
    rebuilder.start()
    saver.start()
    rebuilder.join(timeout=LOCK_WAIT_TIMEOUT)
    saver.join(timeout=LOCK_WAIT_TIMEOUT)

    assert failures == [], failures
    matter.refresh_from_db()
    assert matter.title == "Uus pealkiri"
    rows = SearchDocument.objects.filter(matter=matter, source_kind=SearchSourceKind.MATTER)
    assert rows.count() == 1


def test_every_debt_receiver_survives_garbage_collection(specialist):
    """The trap that made this whole mechanism silently stop working once.

    `Signal.connect` holds its receiver weakly by default. Every handler that
    records freshness debt is a closure, so without `weak=False` it has no other
    reference, is collected, and quietly stops being a receiver — the file still
    reads correctly, the `dispatch_uid` is still registered, nothing raises, and
    renames simply stop owing a rebuild.

    A collected receiver is indistinguishable from the defect this module fixes,
    so it gets a guard rather than a comment.
    """
    import gc

    from django.db.models.signals import post_delete, post_save, pre_save

    gc.collect()
    connected = {
        entry[0][0]
        for signal in (pre_save, post_save, post_delete)
        for entry in signal.receivers
        if isinstance(entry[0][0], str)
    }
    for uid in (
        "search_debt_org_rename",
        "search_debt_tag_rename",
        "search_debt_area_rename",
        "search_debt_person_rename",
        "search_debt_org_alias_saved",
        "search_debt_org_alias_deleted",
        "search_debt_tag_alias_saved",
        "search_debt_tag_alias_deleted",
    ):
        assert uid in connected, f"{uid} was garbage-collected; connect it with weak=False"

    # And the behaviour the guard is standing in for, after a collection.
    organisation = factories.OrganisationFactory(name="Enne")
    organisation.name = "Pärast"
    organisation.save()
    assert freshness.status().owed == 1


def test_reading_the_status_does_not_load_the_debt_table(specialist):
    """The healthcheck runs every sixty seconds; it may not scale with the debt.

    A bulk writer that touched every alias in the corpus writes a row per alias.
    Loading them to count them would be fine on the empty table this normally
    is, and would not be fine on the one day it matters.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    SearchRebuildDebt.objects.bulk_create(
        [
            SearchRebuildDebt(reason=SearchRebuildReason.ORGANISATION_ALIAS_CHANGED)
            for _ in range(200)
        ]
    )

    with CaptureQueriesContext(connection) as queries:
        state = freshness.status()

    assert state.owed == 200
    assert state.reasons == {SearchRebuildReason.ORGANISATION_ALIAS_CHANGED.value: 200}
    # Aggregates and a group-by, not two hundred rows.
    assert len(queries.captured_queries) <= 3
    assert all(
        "LIMIT" in sql or "GROUP BY" in sql or "COUNT" in sql.upper()
        for sql in (query["sql"] for query in queries.captured_queries)
    )
