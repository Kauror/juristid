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
from django.db import IntegrityError, OperationalError, connection, connections, transaction
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
    # The class, not the message. Whoever raised the exception wrote that
    # sentence and may have quoted the record it was working on into it, and
    # this column is printed by a health probe — so nothing writes it but
    # `describe_failure`, which reads no message at all.
    assert state.last_error == "RuntimeError"
    assert "katkes" not in state.last_error


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


def test_a_consumer_killed_before_its_rebuild_leaves_the_debt(specialist):
    """The claim is a read, so dying after it costs nothing.

    Nothing is marked as "in progress" and nothing is reserved: the obligation
    is still a plain outstanding row until a rebuild has committed and the
    delete has run. A worker killed anywhere before that leaves the next one
    exactly the work it would have had.
    """
    organisation = factories.OrganisationFactory(name="Enne")
    factories.MatterFactory(owner=specialist)
    organisation.name = "Pärast"
    organisation.save()

    class Killed(Exception):
        pass

    def die(*args: object, **kwargs: object) -> None:
        raise Killed

    real = freshness.rebuild_all
    freshness.rebuild_all = die  # type: ignore[assignment]
    try:
        with pytest.raises(Killed):
            consume()
    finally:
        freshness.rebuild_all = real  # type: ignore[assignment]

    assert freshness.status().owed == 1
    consume()
    assert freshness.status().is_clear


def test_deleting_a_matter_leaves_no_orphaned_search_result(specialist):
    """A projection row for a deleted source is not stale data to tidy up later:
    it is a search result pointing at nothing.

    A Matter with any recorded history cannot be deleted at all — `ChangeEvent`
    holds it with PROTECT — so the reachable case is a Matter with none, which
    is what the TEST-data purge is left with once it has removed the audit rows.
    """
    matter = factories.MatterFactory(owner=specialist, title="Kustutatav teema")
    factories.EntryFactory(matter=matter, author=specialist, body="<p>OMEGA sisu</p>")
    MatterEngagement.objects.create(matter=matter, kind="SURVEY", title="OMEGA küsitlus")
    survivor = factories.MatterFactory(owner=specialist, title="Alles jääv teema")
    rebuild_all()
    assert found("OMEGA", specialist)

    matter.delete()

    assert SearchDocument.objects.filter(matter_id=matter.pk).count() == 0
    assert found("OMEGA", specialist) == []
    # And nothing else went with it.
    assert SearchDocument.objects.filter(matter=survivor).exists()


def test_deleting_a_matter_with_senders_and_tags_does_not_fail(specialist):
    """A `post_delete` that re-projects must not fire during its parent's cascade.

    Django raw-deletes the receiver-less rows first — `SearchDocument` among
    them — and only then loops over the children. A handler that re-projects at
    that point inserts a row for a Matter the delete is partway through
    removing; the deferred foreign key then fails at COMMIT and the delete the
    operator asked for does not happen at all. CI caught it for senders; the
    tag handler had the same hole and no test that deleted a tagged Matter.
    """
    organisation = factories.OrganisationFactory(name="Saatja")
    tag = factories.TagFactory(name_et="Silt")
    matter = factories.MatterFactory(owner=specialist, title="Kustutatav teema")
    matter.source_organisations.add(organisation)
    matter.tags.add(tag)
    rebuild_all()

    matter.delete()

    assert SearchDocument.objects.filter(matter_id=matter.pk).count() == 0
    assert MatterSourceOrganisation.objects.filter(matter_id=matter.pk).count() == 0


def test_removing_one_sender_still_reprojects_the_matter(specialist):
    """The guard must not turn into "never refresh on delete"."""
    kept = factories.OrganisationFactory(name="Kultuuriministeerium")
    dropped = factories.OrganisationFactory(name="Loomeliitude Keskliit")
    matter = factories.MatterFactory(owner=specialist, title="Autoriõiguse teema")
    matter.source_organisations.set([kept, dropped])

    MatterSourceOrganisation.objects.filter(matter=matter, organisation=dropped).delete()

    assert found("Loomeliitude", specialist) == []
    assert found("Kultuuriministeerium", specialist) == [
        (SearchSourceKind.MATTER.value, str(matter.pk))
    ]


def test_every_source_kind_has_a_badge_label():
    """A kind with no label prints an empty badge, and only shows up when a row
    of that kind finally reaches a result page.

    ENGAGEMENT went a whole release without one: AUTH-003 created the kind, only
    a full rebuild ever wrote a row, and nothing put one in front of a reader
    until SEARCH-001 did. The next kind added should not need a rendered page to
    discover the same omission.
    """
    from app.search.services import SOURCE_LABELS

    missing = [kind.value for kind in SearchSourceKind if not SOURCE_LABELS.get(kind.value)]
    assert missing == []


# ---------------------------------------------------------------------------
# The other half of a PolicyArea's lifecycle
# ---------------------------------------------------------------------------
#
# Renaming a Valdkond owed a rebuild from the first version of this branch.
# Deleting one owed nothing, so the corpus kept the old name and public search
# kept returning results for a Valdkond the taxonomy no longer has — the same
# silent staleness SEARCH-001 exists to remove, one lifecycle event further on.


def test_deleting_a_policy_area_owes_a_rebuild(specialist):
    area = factories.PolicyAreaFactory(name_et="Merendusvaldkond")
    matter = factories.MatterFactory(owner=specialist, title="Valdkonnaga teema")
    matter.policy_areas.add(area)
    rebuild_all()
    assert found("Merendusvaldkond", specialist) == [
        (SearchSourceKind.MATTER.value, str(matter.pk))
    ]

    area.delete()

    assert freshness.status().reasons == {SearchRebuildReason.POLICY_AREA_REMOVED.value: 1}


def test_a_deleted_policy_area_stops_being_findable_once_the_debt_is_paid(specialist):
    """The reader's half of the same fact.

    A Matter whose only Valdkond was deleted must stop matching that name — not
    because the row is gone, but because the row's `alias_text` no longer says
    it. The Matter itself is untouched and stays findable by its title.
    """
    area = factories.PolicyAreaFactory(name_et="Merendusvaldkond")
    matter = factories.MatterFactory(owner=specialist, title="Laevanduse teema")
    matter.policy_areas.add(area)
    rebuild_all()

    area.delete()
    consume()

    assert found("Merendusvaldkond", specialist) == []
    assert found("Laevanduse", specialist) == [(SearchSourceKind.MATTER.value, str(matter.pk))]
    assert freshness.status().is_clear


def test_a_rolled_back_policy_area_deletion_leaves_no_debt(specialist):
    """The mark belongs to the deleting transaction, not to the process.

    `post_delete` fires inside the collector's own `atomic`, so a deletion that
    is rolled back — a `PROTECT` further down the cascade, a validation error
    after it, an operator's interrupt — takes its debt with it. The alternative
    is a debt table that accumulates obligations for changes that never
    happened, and a worker rebuilding the corpus to converge on nothing.
    """
    area = factories.PolicyAreaFactory(name_et="Põllumajandusvaldkond")
    matter = factories.MatterFactory(owner=specialist, title="Maaelu teema")
    matter.policy_areas.add(area)
    rebuild_all()

    class Rollback(Exception):
        pass

    with pytest.raises(Rollback):
        with transaction.atomic():
            area.delete()
            assert freshness.outstanding().count() == 1, "not marked inside the transaction"
            raise Rollback

    assert freshness.status().is_clear
    assert found("Põllumajandusvaldkond", specialist) == [
        (SearchSourceKind.MATTER.value, str(matter.pk))
    ]


def test_deleting_a_matter_does_not_owe_a_full_rebuild(specialist):
    """The delete hook is on the reference row, not on everything deletable.

    A Matter's own deletion is bounded — its rows go with it through the
    CASCADE — and marking a corpus-wide rebuild for one would turn ordinary
    business activity into a rebuild every few minutes, which is a worse
    failure than the one being fixed.
    """
    area = factories.PolicyAreaFactory(name_et="Energeetikavaldkond")
    matter = factories.MatterFactory(owner=specialist, title="Kaduv teema")
    matter.policy_areas.add(area)
    rebuild_all()

    matter.delete()

    assert freshness.status().is_clear


def test_every_other_reference_name_is_protected_from_deletion(specialist):
    """Why PolicyArea is the only model in this file with a `post_delete`.

    Not an assertion about signals — an assertion about the schema they would
    otherwise have to compensate for. Every other name that reaches the
    projection is held by a `PROTECT` foreign key the moment something indexes
    it, so the delete raises instead of quietly changing the corpus. If one of
    these is ever relaxed to `CASCADE` or `SET_NULL`, this fails and whoever
    relaxed it has to decide what the projection owes.
    """
    from django.db.models import ProtectedError

    organisation = factories.OrganisationFactory(name="Siseministeerium")
    tag = factories.TagFactory(name_et="Piirivalve")
    matter = factories.MatterFactory(owner=specialist, title="Piiri teema")
    matter.source_organisations.add(organisation)
    matter.tags.add(tag)
    factories.EntryFactory(matter=matter, author=specialist, organisation=organisation)
    rebuild_all()

    for indexed in (organisation, tag, specialist):
        with pytest.raises(ProtectedError):
            indexed.delete()


# ---------------------------------------------------------------------------
# A failed rebuild records why, and never what
# ---------------------------------------------------------------------------
#
# `check_search_freshness` prints `last_error` to a terminal and a container
# log, and a PostgreSQL error message is composed out of the row that failed. A
# not-null violation against `SearchDocument` raises with `DETAIL: Failing row
# contains (…)` — and those brackets hold the projected `title` and `body_text`,
# which is the most confidential material in the system.

#: Unique enough that finding it anywhere is proof rather than coincidence.
SENTINEL_TITLE = "SALAJANE-SEARCH-ERROR-987"


def _rebuild_that_violates_a_not_null_constraint(monkeypatch) -> None:
    """Make the next rebuild fail the way PostgreSQL fails, not the way a mock does.

    A raised `Exception("boom")` would pass any sanitiser ever written. The
    defect is specifically that the *database* composes its message out of the
    row, so the test has to reach PostgreSQL and let it do that.
    """
    from app.search import indexing

    original = indexing._document_values

    def without_indexed_at(matter: object, now: object) -> dict:
        values = original(matter, now)
        values["indexed_at"] = None
        return values

    monkeypatch.setattr(indexing, "_document_values", without_indexed_at)


@pytest.mark.django_db(transaction=True)
def test_a_database_failure_does_not_persist_the_row_that_failed(monkeypatch, specialist):
    factories.MatterFactory(owner=specialist, title=SENTINEL_TITLE)
    freshness.mark_rebuild_owed(SearchRebuildReason.TAG_RENAMED)
    _rebuild_that_violates_a_not_null_constraint(monkeypatch)

    with pytest.raises(IntegrityError) as caught:
        consume()

    # The exception itself carries the content — that is PostgreSQL's doing and
    # is not something this change can or should alter. What matters is what
    # survives it.
    assert SENTINEL_TITLE in str(caught.value), "the reproduction stopped reproducing"

    debt = SearchRebuildDebt.objects.get()
    assert SENTINEL_TITLE not in debt.last_error
    assert "Failing row contains" not in debt.last_error


@pytest.mark.django_db(transaction=True)
def test_a_sanitised_failure_still_says_what_broke(monkeypatch, specialist):
    """Useful, not merely safe.

    "the rebuild failed" would pass the test above and tell an operator nothing.
    The SQLSTATE and the schema names PostgreSQL supplies separately are enough
    to find the change that caused it, and none of them is a value from a row.
    """
    factories.MatterFactory(owner=specialist, title=SENTINEL_TITLE)
    freshness.mark_rebuild_owed(SearchRebuildReason.TAG_RENAMED)
    _rebuild_that_violates_a_not_null_constraint(monkeypatch)

    with pytest.raises(IntegrityError):
        consume()

    debt = SearchRebuildDebt.objects.get()
    assert "IntegrityError" in debt.last_error
    assert "23502" in debt.last_error
    assert "search_searchdocument.indexed_at" in debt.last_error


@pytest.mark.django_db(transaction=True)
def test_the_freshness_probe_never_prints_the_row_that_failed(monkeypatch, specialist, capsys):
    """The place the leak would have been read from.

    This command is a container healthcheck, so its output goes to the Docker
    log by default and to an operator's terminal on demand. It is the reason
    `last_error` has to be safe rather than merely short.
    """
    factories.MatterFactory(owner=specialist, title=SENTINEL_TITLE)
    freshness.mark_rebuild_owed(SearchRebuildReason.TAG_RENAMED)
    _rebuild_that_violates_a_not_null_constraint(monkeypatch)

    with pytest.raises(IntegrityError):
        consume()
    monkeypatch.undo()

    with pytest.raises(SystemExit):
        call_command("check_search_freshness")

    printed = capsys.readouterr()
    assert SENTINEL_TITLE not in printed.out + printed.err
    # Still a fault, and still says one rebuild has been attempted and failed.
    assert "ebaõnnestunud 1 korda" in printed.err


@pytest.mark.django_db(transaction=True)
def test_a_failed_rebuild_is_still_a_fault_after_sanitisation(monkeypatch, specialist):
    """Sanitising the message must not soften the state.

    The probe faults on a failed attempt rather than waiting for the staleness
    threshold, because a rebuild that raised is not going to fix itself by
    being left alone. That is read off `attempts`, not off the message, and
    this pins the two apart.
    """
    factories.MatterFactory(owner=specialist, title=SENTINEL_TITLE)
    freshness.mark_rebuild_owed(SearchRebuildReason.TAG_RENAMED)
    _rebuild_that_violates_a_not_null_constraint(monkeypatch)

    with pytest.raises(IntegrityError):
        consume()

    state = freshness.status()
    assert state.failed_attempts == 1
    assert state.last_error
    assert not state.is_clear


def test_a_failure_with_no_database_underneath_it_still_records_its_class():
    """Not every failure is PostgreSQL's, and the safe rule is the same.

    A plain Python exception's message is written by whoever raised it and may
    quote anything it was working on, so it is not read either. The class name
    is.
    """
    described = freshness.describe_failure(ValueError(SENTINEL_TITLE))
    assert described == "ValueError"


# ---------------------------------------------------------------------------
# The worker survives the database going away
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_a_worker_pass_reconnects_after_the_connection_is_lost(specialist):
    """`restart: unless-stopped` cannot see this failure, so the loop has to.

    A PostgreSQL restart leaves the worker holding a dead socket. Every
    subsequent pass raised `OperationalError` against it, for ever: the process
    had not exited, so nothing restarted it, the debt kept accumulating, and
    converging again needed a human to notice and restart the container — the
    exact failure this branch exists to remove.

    A real invalidation rather than a patched `close_old_connections`: the
    regression is the state of the connection, and a test that mocks the cure
    proves only that the cure was called.
    """
    organisation = factories.OrganisationFactory(name="Kliimaministeerium")
    matter = factories.MatterFactory(owner=specialist, title="Kliimateema")
    matter.source_organisations.add(organisation)
    rebuild_all()
    organisation.name = "Keskkonnaamet"
    organisation.save()
    assert freshness.outstanding().count() == 1

    # What a database restart leaves behind: Django still holds a connection
    # object, and the socket under it is gone.
    connection.connection.close()

    # The pass that meets the dead socket fails, and fails honestly.
    with pytest.raises(OperationalError):
        freshness.worker_pass()

    # The next one reconnects and pays the debt off, with no human involved.
    outcome = freshness.worker_pass()
    assert outcome.rebuilt
    assert outcome.cleared == 1
    assert freshness.status().is_clear
    assert found("Keskkonnaamet", specialist) == [(SearchSourceKind.MATTER.value, str(matter.pk))]


@pytest.mark.django_db(transaction=True)
def test_the_loop_without_the_hygiene_would_stay_wedged(specialist):
    """What makes the test above load-bearing rather than decorative.

    `consume_once` is the loop body without the connection hygiene, and it is
    what the worker called before this correction. Four passes, four failures,
    and the debt still outstanding.
    """
    freshness.mark_rebuild_owed(SearchRebuildReason.TAG_RENAMED)
    connection.connection.close()

    for _ in range(4):
        with pytest.raises(OperationalError):
            freshness.consume_once()

    connection.close()
    assert freshness.outstanding().count() == 1


@pytest.mark.django_db(transaction=True)
def test_a_healthy_pass_keeps_the_connection_it_had(specialist):
    """The hygiene must be free in the ordinary case.

    `close_old_connections` drops a connection only when it is unusable or past
    `CONN_MAX_AGE`, so a worker polling every ten seconds is not opening a new
    PostgreSQL connection every ten seconds.
    """
    freshness.mark_rebuild_owed(SearchRebuildReason.TAG_RENAMED)
    before = connection.connection

    freshness.worker_pass()

    assert connection.connection is before


@pytest.mark.django_db(transaction=True)
def test_the_integrity_check_does_not_print_the_row_that_failed(monkeypatch, specialist):
    """The second reader of the same column.

    `check_search_freshness` is the healthcheck and `check_search_integrity` is
    the fuller diagnostic, and both render `last_error` into their output.
    Sanitising at the single write site is what covers both — and this pins that
    a future third reader inherits the same guarantee rather than needing its
    own filter.
    """
    from app.search.management.commands.check_search_integrity import build_report

    factories.MatterFactory(owner=specialist, title=SENTINEL_TITLE)
    freshness.mark_rebuild_owed(SearchRebuildReason.TAG_RENAMED)
    _rebuild_that_violates_a_not_null_constraint(monkeypatch)

    with pytest.raises(IntegrityError):
        consume()
    monkeypatch.undo()

    report = build_report(sample=0)

    assert not report.ok
    rendered = " ".join(f"{finding.label} {finding.detail}" for finding in report.findings)
    assert SENTINEL_TITLE not in rendered
    assert "Indeksi taastamine" in rendered
