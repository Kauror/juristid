"""«Seotud materjalid»: derived suggestions, human-confirmed links.

The product rule under test is short. The application may *propose* that a
Matter, an earlier opinion or an archive letter is worth a look, and must say
why; only a person may turn that into a relation or a background selection;
saying «Ei ole seotud» is remembered for the Matter; and nothing a reader may
not open can shape what they are shown — not a count, not a rank, not a reason
(docs/adr/0061).

Three families of test:

* the **services** — one symmetric row per pair, idempotent, atomic, never a
  Submission moved or an archive link written;
* the **engine** — the synthetic corpus from the brief, case by case, with the
  weights that hold «same area alone is not enough» pinned in place;
* the **boundary** — authorization before ranking, read-only GETs, and the
  routes that answer 404 for a target the caller may not see.
"""

from __future__ import annotations

import hashlib
from datetime import date
from io import StringIO
from typing import Any

import pytest
from django.core.management import CommandError, call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.accounts.enums import UserRole
from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.authorization import DEPARTMENT_VIEWER
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.documents.services import add_evidence_version
from app.legacy_import.opinion_archive import (
    OpinionArchiveBatch,
    OpinionArchiveItem,
    OpinionMatchCandidate,
    OpinionSubmissionImport,
)
from app.legacy_import.opinion_binary import (
    OpinionArchiveBinary,
    OpinionArchiveMatterLink,
    OpinionArchiveText,
)
from app.legacy_import.opinion_enums import (
    ArchiveLinkBasis,
    ArchiveTextState,
    OpinionCandidateState,
    OpinionMatchClass,
)
from app.legacy_import.opinion_links import link_matter
from app.legacy_import.opinion_search import refresh_archive_binaries
from app.legacy_import.opinion_search_models import (
    ARCHIVE_INDEX_VERSION,
    OpinionArchiveSearchDocument,
)
from app.matters import purge
from app.matters.enums import MatterDataClass
from app.matters.models import Matter
from app.matters.services import close_matter, create_matter
from app.matters.timeline import TIMELINE_EVENT_TYPES
from app.related_materials import engine, services
from app.related_materials.models import (
    MatterBackgroundMaterial,
    MatterRelation,
    RelatedSuggestionDismissal,
)
from app.related_materials.selectors import related_materials_for
from app.search.models import INDEX_VERSION, SearchDocument
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.workflow.enums import Disposition, Track
from tests import factories

pytestmark = pytest.mark.django_db

SECTION = "related_materials:section"
HX = {"HTTP_HX_REQUEST": "true"}


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _matter(
    owner: Any,
    title: str,
    *,
    number: int,
    year: int = 2026,
    areas: Any = (),
    tags: Any = (),
    senders: Any = (),
    addressee: Any = None,
    track: str = Track.DOMESTIC,
    **extra: Any,
) -> Matter:
    matter = factories.MatterFactory(
        owner=owner,
        title=title,
        reference_year=year,
        reference_number=number,
        addressee_organisation=addressee,
        track=track,
        **extra,
    )
    if senders:
        matter.source_organisations.set(senders)
    if areas:
        matter.policy_areas.set(areas)
    if tags:
        matter.tags.set(tags)
    return matter


def _sent_opinion(matter: Matter, title: str, *, sent: Any = None) -> Submission:
    """A canonical, sent Submission with the evidence the constraint requires."""
    document = factories.DocumentFactory(matter=matter)
    version = add_evidence_version(
        document=document,
        content=b"%PDF-1.4\n" + title.encode("utf-8"),
        original_filename="arvamus.pdf",
        mime_type="application/pdf",
    )
    return factories.SubmissionFactory(
        matter=matter,
        title=title,
        status=SubmissionStatus.SENT,
        sent_at=sent or timezone.now(),
        final_version=version,
    )


def _hold_letter(
    *,
    seed: str,
    title: str,
    recipient: str = "Näidisministeerium",
    when: date = date(2022, 3, 14),
    body: str = "",
    batch: OpinionArchiveBatch | None = None,
) -> tuple[OpinionArchiveBinary, OpinionArchiveItem, OpinionArchiveBatch]:
    """One held archive letter, projected, as materialisation would leave it."""
    sha = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    batch = batch or OpinionArchiveBatch.objects.create(
        archive_sha256="a" * 64, importer_version="test/0", started_at=timezone.now()
    )
    binary = OpinionArchiveBinary.objects.create(
        sha256=sha,
        size_bytes=1024,
        mime_type="application/pdf",
        storage_key=f"opinion-archive/{sha[:2]}/{sha[2:4]}/{sha}",
        source_archive_sha256="a" * 64,
        materialized_at=timezone.now(),
    )
    item = OpinionArchiveItem.objects.create(
        batch=batch,
        archive_sha256="a" * 64,
        archive_relative_path=f"Opinions/{title}.pdf",
        original_filename=f"{title}.pdf",
        sha256=sha,
        size_bytes=1024,
        detected_type="application/pdf",
        filename_date=when,
        filename_recipient=recipient,
        filename_title=title,
        binary=binary,
    )
    if body:
        OpinionArchiveText.objects.create(
            binary=binary,
            state=ArchiveTextState.DONE,
            body=body,
            characters=len(body),
            parser="test",
            parser_version="1",
        )
    refresh_archive_binaries([binary.pk])
    return binary, item, batch


def _restrict(submission: Submission) -> None:
    """Restrict a sent opinion, evidence and all.

    A trigger refuses a RESTRICTED submission standing on NORMAL final evidence
    (`app/submissions/migrations/0002_final_evidence_integrity.py`), so the
    document goes first. Doing it the other way round is how a test discovers
    an integrity rule it was not written to be about.
    """
    version = submission.final_version
    if version is not None:
        document = version.document
        document.visibility_override = Visibility.RESTRICTED
        document.save(update_fields=["visibility_override"])
    submission.visibility_override = Visibility.RESTRICTED
    submission.save(update_fields=["visibility_override"])


def _suggested_matters(matter: Matter, viewer: Any, **kwargs: Any) -> list[Matter]:
    return [item.matter for item in engine.suggestions_for(matter, viewer, **kwargs).matters]


def _suggested_materials(matter: Matter, viewer: Any, **kwargs: Any) -> list[str]:
    return [item.title for item in engine.suggestions_for(matter, viewer, **kwargs).materials]


def _reasons_for(matter: Matter, viewer: Any, candidate: Matter) -> tuple[str, ...]:
    for item in engine.suggestions_for(matter, viewer).matters:
        if item.matter.pk == candidate.pk:
            return item.reasons
    raise AssertionError(f"{candidate.title!r} was not suggested")


@pytest.fixture
def keskkond(db):
    return factories.PolicyAreaFactory(name_et="Keskkond")


@pytest.fixture
def ministry(db):
    return factories.OrganisationFactory(name="Näidiskliimaministeerium")


# ===========================================================================
# Services
# ===========================================================================


def test_a_relation_is_one_row_shown_from_both_sides(specialist):
    first = _matter(specialist, "Jäätmeseaduse muutmine", number=901)
    second = _matter(specialist, "Pakendiseaduse muutmine", number=902)

    relation, created = services.link_related_matters(matter=second, other=first, actor=specialist)
    again, created_again = services.link_related_matters(
        matter=first, other=second, actor=specialist
    )

    assert created and not created_again
    assert again.pk == relation.pk
    assert MatterRelation.objects.count() == 1
    assert relation.matter_a.pk < relation.matter_b.pk
    assert [item.other for item in related_materials_for(first, specialist).relations] == [second]
    assert [item.other for item in related_materials_for(second, specialist).relations] == [first]


def test_a_relation_records_who_and_when_on_both_files(specialist):
    first = _matter(specialist, "Jäätmeseaduse muutmine", number=903)
    second = _matter(specialist, "Pakendiseaduse muutmine", number=904)

    relation, _ = services.link_related_matters(matter=first, other=second, actor=specialist)

    assert relation.linked_by == specialist
    assert relation.linked_at is not None
    events = ChangeEvent.objects.filter(event_type=ChangeEventType.MATTER_RELATION_ADDED)
    assert {event.matter_id for event in events} == {first.pk, second.pk}
    assert all(event.actor == specialist for event in events)


def test_a_matter_cannot_be_related_to_itself(specialist):
    matter = _matter(specialist, "Jäätmeseaduse muutmine", number=905)
    with pytest.raises(DomainError):
        services.link_related_matters(matter=matter, other=matter, actor=specialist)
    assert MatterRelation.objects.count() == 0


def test_the_database_refuses_a_reversed_or_duplicate_pair(specialist):
    """The canonical order and the uniqueness are held by PostgreSQL, not by the UI."""
    from django.db import IntegrityError, transaction

    first = _matter(specialist, "A", number=906)
    second = _matter(specialist, "B", number=907)
    a, b = services.canonical_pair(first, second)
    MatterRelation.objects.create(
        matter_a=a, matter_b=b, linked_by=specialist, linked_at=timezone.now()
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        MatterRelation.objects.create(
            matter_a=b, matter_b=a, linked_by=specialist, linked_at=timezone.now()
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        MatterRelation.objects.create(
            matter_a=a, matter_b=b, linked_by=specialist, linked_at=timezone.now()
        )


def test_unlinking_removes_the_one_row_and_dismisses_nothing(specialist):
    first = _matter(specialist, "Jäätmeseaduse muutmine", number=908)
    second = _matter(specialist, "Pakendiseaduse muutmine", number=909)
    services.link_related_matters(matter=first, other=second, actor=specialist)

    assert services.unlink_related_matters(matter=second, other=first, actor=specialist)
    assert not services.unlink_related_matters(matter=second, other=first, actor=specialist)

    assert MatterRelation.objects.count() == 0
    assert RelatedSuggestionDismissal.objects.count() == 0
    assert related_materials_for(first, specialist).relations == ()
    assert (
        ChangeEvent.objects.filter(event_type=ChangeEventType.MATTER_RELATION_REMOVED).count() == 2
    )


def test_a_relation_needs_a_person(specialist):
    first = _matter(specialist, "A", number=910)
    second = _matter(specialist, "B", number=911)
    for actor in (None, DEPARTMENT_VIEWER):
        with pytest.raises(DomainError):
            services.link_related_matters(matter=first, other=second, actor=actor)
    assert MatterRelation.objects.count() == 0


def test_background_selection_leaves_the_submission_exactly_as_it_was(specialist, ministry):
    source = _matter(specialist, "Pakendiseaduse muutmine 2024", number=912, year=2024)
    current = _matter(specialist, "Pakendiseaduse muutmine 2026", number=913)
    opinion = _sent_opinion(source, "Koja arvamus pakendiseaduse muutmise kohta")
    before = Submission.objects.filter(pk=opinion.pk).values().get()

    row, created = services.add_background_submission(
        matter=current, submission=opinion, actor=specialist
    )
    _, created_again = services.add_background_submission(
        matter=current, submission=opinion, actor=specialist
    )

    assert created and not created_again
    assert MatterBackgroundMaterial.objects.count() == 1
    assert row.added_by == specialist
    assert Submission.objects.filter(pk=opinion.pk).values().get() == before
    assert Submission.objects.count() == 1
    assert opinion.matter_id == source.pk
    items = related_materials_for(current, specialist).background
    assert [item.title for item in items] == ["Koja arvamus pakendiseaduse muutmise kohta"]
    assert items[0].source_reference == "2024_912"
    assert items[0].open_url == reverse("matters:matter_position", kwargs={"pk": source.pk})


def test_a_matters_own_opinion_is_not_its_background(specialist):
    current = _matter(specialist, "Pakendiseaduse muutmine", number=914)
    opinion = _sent_opinion(current, "Koja arvamus")
    with pytest.raises(DomainError):
        services.add_background_submission(matter=current, submission=opinion, actor=specialist)
    assert MatterBackgroundMaterial.objects.count() == 0


def test_archive_background_creates_no_archive_link_and_removal_keeps_one(specialist):
    """The two concepts stay independent in both directions (brief §40)."""
    current = _matter(specialist, "Pakendiseaduse muutmine", number=915)
    binary, _, _ = _hold_letter(seed="letter-915", title="Pakendiseaduse arvamus")

    _, created = services.add_background_archive_material(
        matter=current, binary=binary, actor=specialist
    )

    assert created
    assert OpinionArchiveMatterLink.objects.count() == 0
    assert MatterBackgroundMaterial.objects.get().archive_binary == binary
    items = related_materials_for(current, specialist).background
    assert items[0].open_url == reverse(
        "legacy_import:opinion_archive_detail", kwargs={"pk": binary.pk}
    )
    assert binary.storage_key not in items[0].open_url

    # An independent reviewed link exists; withdrawing the background leaves it.
    link_matter(binary=binary, matter=current, basis=ArchiveLinkBasis.EXACT_BINARY)
    assert services.remove_background_material(
        matter=current, archive_binary=binary, actor=specialist
    )
    assert MatterBackgroundMaterial.objects.count() == 0
    assert OpinionArchiveMatterLink.objects.filter(binary=binary, matter=current).count() == 1
    assert ChangeEvent.objects.filter(
        event_type=ChangeEventType.BACKGROUND_MATERIAL_REMOVED, matter=current
    ).exists()


def test_a_dismissal_is_durable_reversible_and_cleared_by_the_opposite_decision(specialist):
    current = _matter(specialist, "Jäätmeseaduse muutmine", number=916)
    candidate = _matter(specialist, "Jäätmeseaduse ja pakendiseaduse muutmine", number=917)

    _, created = services.dismiss_related_suggestion(
        matter=current, actor=specialist, candidate_matter=candidate
    )
    _, again = services.dismiss_related_suggestion(
        matter=current, actor=specialist, candidate_matter=candidate
    )
    assert created and not again
    assert RelatedSuggestionDismissal.objects.count() == 1
    assert candidate not in _suggested_matters(current, specialist)

    assert services.restore_related_suggestion(
        matter=current, actor=specialist, candidate_matter=candidate
    )
    assert candidate in _suggested_matters(current, specialist)

    # Dismissed again, then linked from the *other* side: the link clears it.
    services.dismiss_related_suggestion(
        matter=current, actor=specialist, candidate_matter=candidate
    )
    services.link_related_matters(matter=candidate, other=current, actor=specialist)
    assert RelatedSuggestionDismissal.objects.count() == 0
    # A dismissal writes no history line of its own.
    assert not ChangeEvent.objects.filter(event_type__startswith="RELATED_SUGGESTION").exists()


def test_a_dismissal_names_exactly_one_legitimate_candidate(specialist):
    current = _matter(specialist, "A", number=918)
    other = _matter(specialist, "B", number=919)
    opinion = _sent_opinion(other, "Arvamus")
    with pytest.raises(DomainError):
        services.dismiss_related_suggestion(matter=current, actor=specialist)
    with pytest.raises(DomainError):
        services.dismiss_related_suggestion(
            matter=current, actor=specialist, candidate_matter=other, candidate_submission=opinion
        )
    with pytest.raises(DomainError):
        services.dismiss_related_suggestion(
            matter=current, actor=specialist, candidate_matter=current
        )
    assert RelatedSuggestionDismissal.objects.count() == 0


# ===========================================================================
# The engine: the corpus from the brief
# ===========================================================================


def test_the_same_named_act_is_a_strong_candidate(specialist):
    current = _matter(specialist, "Jäätmeseaduse muutmine", number=920)
    candidate = _matter(specialist, "Jäätmeseaduse ja pakendiseaduse muutmine", number=921)

    assert _suggested_matters(current, specialist) == [candidate]
    assert "Sama õigusakt: jäätmeseadus" in _reasons_for(current, specialist, candidate)


def test_the_same_policy_area_alone_is_not_enough(specialist, keskkond):
    """Brief §26, as it words it: A shares only the area, B shares the act.

    Both candidates are Keskkond and so is the current Matter, so the area does
    no discriminating work at all — which is the point. Only the named act
    separates them, and only the act's candidate is suggested.
    """
    current = _matter(specialist, "Pakendiseaduse muutmise eelnõu", number=922, areas=[keskkond])
    only_the_area = _matter(specialist, "Metsanduse arengukava 2030", number=923, areas=[keskkond])
    same_act = _matter(specialist, "Pakendiseaduse rakendamise kord", number=924, areas=[keskkond])

    suggested = _suggested_matters(current, specialist)

    assert suggested == [same_act]
    assert only_the_area not in suggested
    assert "Sama õigusakt: pakendiseadus" in _reasons_for(current, specialist, same_act)


def test_one_shared_subject_word_and_an_area_is_below_the_line(specialist, keskkond):
    """The other side of the same rule, and what decides the weights.

    Two environmental Matters repeating *one* word between them — «Sama
    valdkond» plus «Pealkirjas kordub» — is the shape a lawyer would call a
    coincidence, and 1.0 + 1.5 keeps it under the threshold. A second shared
    word is what makes it a similar title rather than a shared noun.
    """
    current = _matter(
        specialist, "Pakendijäätmete ringmajanduse eelnõu", number=1600, areas=[keskkond]
    )
    one_word = _matter(specialist, "Pakendijäätmete arvestuse kord", number=1601, areas=[keskkond])
    two_words = _matter(
        specialist, "Ringmajanduse ja pakendijäätmete nõuded", number=1602, areas=[keskkond]
    )

    suggested = _suggested_matters(current, specialist)

    assert two_words in suggested
    assert one_word not in suggested


def test_the_same_ministry_alone_is_not_enough(specialist, ministry):
    current = _matter(specialist, "Pakendiseaduse muutmine", number=925, senders=[ministry])
    other = _matter(specialist, "Metsanduse arengukava", number=926, senders=[ministry])
    assert _suggested_matters(current, specialist) == []
    assert other not in _suggested_matters(current, specialist)


def test_the_same_track_alone_is_nothing(specialist):
    current = _matter(specialist, "Pakendiseaduse muutmine", number=927, track=Track.EU_INITIATIVE)
    _matter(specialist, "Metsanduse arengukava", number=928, track=Track.EU_INITIATIVE)
    assert _suggested_matters(current, specialist) == []


def test_a_similar_title_without_a_named_act_is_a_candidate(specialist):
    current = _matter(specialist, "Pakendijäätmete ringmajanduse nõuded", number=929)
    candidate = _matter(
        specialist, "Ringmajanduse edendamine pakendijäätmete valdkonnas", number=930
    )

    assert candidate in _suggested_matters(current, specialist)
    reasons = _reasons_for(current, specialist, candidate)
    assert any(reason.startswith("Sarnane pealkiri") for reason in reasons)


def test_a_credible_combination_of_structured_signals_is_a_candidate(
    specialist, keskkond, ministry
):
    """Brief §42.5: no near-identical title, several catalogue facts in common."""
    tag = factories.TagFactory(name_et="pakend")
    current = _matter(
        specialist,
        "Ettevõtjate aruandluskohustus 2026",
        number=931,
        areas=[keskkond],
        tags=[tag],
        senders=[ministry],
    )
    candidate = _matter(
        specialist,
        "Tootjavastutuse rakendamise kord",
        number=932,
        areas=[keskkond],
        tags=[tag],
        senders=[ministry],
    )
    only_area = _matter(specialist, "Metsanduse arengukava", number=933, areas=[keskkond])

    suggested = _suggested_matters(current, specialist)
    assert candidate in suggested
    assert only_area not in suggested
    reasons = _reasons_for(current, specialist, candidate)
    assert "Sama silt: pakend" in reasons
    assert "Sama asutus: Näidiskliimaministeerium" in reasons
    assert "Sama valdkond: Keskkond" in reasons
    assert len(reasons) <= engine.MAX_REASONS


def test_a_tag_plus_an_area_is_not_enough_but_a_tag_plus_a_ministry_is(
    specialist, keskkond, ministry
):
    """The weights, at the boundary the product rule draws."""
    tag = factories.TagFactory(name_et="käibemaks")
    current = _matter(
        specialist, "Aruandlus A", number=934, areas=[keskkond], tags=[tag], senders=[ministry]
    )
    tag_and_area = _matter(specialist, "Kord B", number=935, areas=[keskkond], tags=[tag])
    tag_and_ministry = _matter(specialist, "Kord C", number=936, tags=[tag], senders=[ministry])

    suggested = _suggested_matters(current, specialist)
    assert tag_and_ministry in suggested
    # 2.0 + 1.0 = 3.0, under the line — and the shared Track, which every Matter
    # here carries, must not be able to make up the remaining 0.5.
    assert tag_and_area not in suggested


def test_the_track_orders_candidates_and_never_qualifies_one(specialist, keskkond):
    """Same subject, one candidate on the same Track: order moves, membership does not."""
    tag = factories.TagFactory(name_et="energia")
    current = _matter(
        specialist,
        "Elektrituruseaduse muutmine",
        number=1610,
        areas=[keskkond],
        tags=[tag],
        track=Track.EU_INITIATIVE,
    )
    same_track = _matter(
        specialist, "Elektrituruseaduse kord", number=1611, track=Track.EU_INITIATIVE
    )
    other_track = _matter(
        specialist, "Elektrituruseaduse nõuded", number=1612, track=Track.DOMESTIC
    )
    below_the_line = _matter(
        specialist, "Miski muu", number=1613, areas=[keskkond], track=Track.EU_INITIATIVE
    )

    suggested = _suggested_matters(current, specialist)

    assert suggested[0] == same_track
    assert other_track in suggested
    assert below_the_line not in suggested
    assert not any(
        "menetlusliik" in reason.casefold()
        for item in engine.suggestions_for(current, specialist).matters
        for reason in item.reasons
    )


def test_a_different_subject_is_not_suggested(specialist):
    current = _matter(specialist, "Pakendiseaduse muutmine", number=937)
    _matter(specialist, "Töölepingu seaduse muutmine", number=938)
    assert _suggested_matters(current, specialist) == []


def test_a_closed_historical_matter_is_suggested_and_says_so(specialist):
    """Brief §27: old closed files are where the background lives."""
    current = _matter(specialist, "Elektrituruseaduse muutmine", number=939)
    old = _matter(specialist, "Elektrituruseaduse muutmise seaduse eelnõu", number=940, year=2018)
    close_matter(matter=old, disposition=Disposition.COMPLETED, actor=specialist)

    (item,) = engine.suggestions_for(current, specialist).matters
    assert item.matter == old
    assert item.state_label == "suletud"


def test_recency_never_outranks_substance(specialist, keskkond, ministry):
    """A 2015 file that shares the act outranks this year's weaker candidate.

    Both qualify: the old one on the named act, the new one on a tag plus the
    same ministry. Recency is at most a tie-break, so the order is substance
    first — which is the whole reason a lawyer would open the older file.
    """
    tag = factories.TagFactory(name_et="energia")
    current = _matter(
        specialist,
        "Elektrituruseaduse muutmine",
        number=941,
        areas=[keskkond],
        tags=[tag],
        senders=[ministry],
    )
    strong_old = _matter(specialist, "Elektrituruseaduse muutmine 2015", number=942, year=2015)
    weaker_new = _matter(
        specialist, "Elektrituru arengud", number=943, tags=[tag], senders=[ministry]
    )

    suggested = _suggested_matters(current, specialist)

    assert suggested == [strong_old, weaker_new]


def test_the_continuation_chain_is_not_offered_again(specialist):
    """Brief §4: superseded_by stays where it is and is not a suggestion."""
    earlier = _matter(specialist, "Jäätmeseaduse muutmine 2024", number=944, year=2024)
    later = _matter(specialist, "Jäätmeseaduse muutmine 2026", number=945)
    close_matter(
        matter=earlier, disposition=Disposition.SUPERSEDED, actor=specialist, successor=later
    )
    earlier.refresh_from_db()
    later.refresh_from_db()

    assert _suggested_matters(later, specialist) == []
    assert _suggested_matters(earlier, specialist) == []


def test_a_confirmed_relation_is_not_suggested_again(specialist):
    current = _matter(specialist, "Jäätmeseaduse muutmine", number=946)
    candidate = _matter(specialist, "Jäätmeseaduse ja pakendiseaduse muutmine", number=947)
    assert candidate in _suggested_matters(current, specialist)

    services.link_related_matters(matter=current, other=candidate, actor=specialist)

    assert _suggested_matters(current, specialist) == []
    assert _suggested_matters(candidate, specialist) == []


def test_a_restricted_matter_leaves_no_trace_for_a_reader(specialist, reader):
    """Brief §29–30: count, ranking, reasons, hidden count — nothing."""
    current = _matter(specialist, "Jäätmeseaduse muutmine", number=948)
    visible = _matter(specialist, "Jäätmeseaduse rakendamine", number=949)
    hidden = _matter(
        specialist,
        "Jäätmeseaduse ja pakendiseaduse muutmine",
        number=950,
        visibility=Visibility.RESTRICTED,
    )
    services.dismiss_related_suggestion(matter=current, actor=specialist, candidate_matter=hidden)

    for_reader = engine.suggestions_for(current, reader, include_hidden=True)
    for_specialist = engine.suggestions_for(current, specialist, include_hidden=True)

    assert [item.matter for item in for_reader.matters] == [visible]
    assert for_reader.hidden_count == 0
    assert for_reader.hidden_matters == ()
    assert for_specialist.hidden_count == 1
    assert [item.matter for item in for_specialist.hidden_matters] == [hidden]


def test_a_confirmed_relation_to_a_restricted_matter_is_invisible_to_a_reader(
    specialist, reader, signed_in, client
):
    normal = _matter(specialist, "Jäätmeseaduse muutmine", number=951)
    restricted = _matter(
        specialist, "Konfidentsiaalne jäätmeteema", number=952, visibility=Visibility.RESTRICTED
    )
    services.link_related_matters(matter=normal, other=restricted, actor=specialist)

    assert related_materials_for(normal, reader).relations == ()
    assert [item.other for item in related_materials_for(normal, specialist).relations] == [
        restricted
    ]

    client.force_login(reader)
    body = client.get(reverse("matters:matter_detail", kwargs={"pk": normal.pk})).content.decode()
    assert "Konfidentsiaalne jäätmeteema" not in body
    assert "Seotud teemad" not in body
    body = client.get(
        reverse(SECTION, kwargs={"pk": normal.pk}) + "?avatud=1", **HX
    ).content.decode()
    assert "Konfidentsiaalne jäätmeteema" not in body


def test_test_data_is_never_suggested_for_a_real_matter(specialist):
    current = _matter(specialist, "Jäätmeseaduse muutmine", number=953)
    synthetic = create_matter(
        title="Jäätmeseaduse ja pakendiseaduse muutmine",
        actor=specialist,
        owner=specialist,
        data_class=MatterDataClass.TEST,
    )
    real = _matter(specialist, "Jäätmeseaduse rakendamine", number=954)

    suggested = _suggested_matters(current, specialist)
    assert synthetic not in suggested
    assert real in suggested
    # And a TEST Matter is recommended from TEST data, not from the business record.
    assert _suggested_matters(synthetic, specialist) == []


def test_a_sent_opinion_on_another_matter_is_offered_as_background(specialist):
    source = _matter(specialist, "Pakendiseaduse muutmine 2024", number=955, year=2024)
    current = _matter(specialist, "Pakendiseaduse muutmine 2026", number=956)
    opinion = _sent_opinion(source, "Koja arvamus pakendiseaduse muutmise kohta")
    factories.SubmissionFactory(matter=source, title="Mustand pakendiseadusest")
    _sent_opinion(current, "Selle teema enda arvamus pakendiseadusest")

    (item,) = engine.suggestions_for(current, specialist).materials

    assert item.kind == engine.KIND_SUBMISSION
    assert item.key == opinion.pk
    assert item.label == "Varasem arvamus"
    assert item.source_reference == "2024_955"
    assert "Sama õigusakt: pakendiseadus" in item.reasons
    assert item.open_url == reverse("matters:matter_position", kwargs={"pk": source.pk})


def test_a_restricted_opinion_is_invisible_to_a_reader(specialist, reader):
    source = _matter(specialist, "Pakendiseaduse muutmine 2024", number=957, year=2024)
    current = _matter(specialist, "Pakendiseaduse muutmine 2026", number=958)
    opinion = _sent_opinion(source, "Koja arvamus pakendiseaduse muutmise kohta")
    _restrict(opinion)

    assert _suggested_materials(current, reader) == []
    assert _suggested_materials(current, specialist) == [
        "Koja arvamus pakendiseaduse muutmise kohta"
    ]


def test_an_archive_letter_is_offered_only_to_an_archive_reader(specialist, reader):
    current = _matter(specialist, "Pakendiseaduse muutmine", number=959)
    binary, _, _ = _hold_letter(seed="letter-959", title="Pakendiseaduse muutmise arvamus")

    for_specialist = engine.suggestions_for(current, specialist).materials
    assert [item.key for item in for_specialist] == [binary.pk]
    assert for_specialist[0].label == "Arhiivimaterjal"
    assert for_specialist[0].open_url == reverse(
        "legacy_import:opinion_archive_detail", kwargs={"pk": binary.pk}
    )
    assert "Sama õigusakt: pakendiseadus" in for_specialist[0].reasons

    assert engine.suggestions_for(current, reader).materials == ()


def test_a_letter_called_not_an_opinion_is_never_offered(specialist):
    current = _matter(specialist, "Pakendiseaduse muutmine", number=960)
    binary, item, batch = _hold_letter(seed="letter-960", title="Pakendiseaduse muutmise arvamus")
    OpinionMatchCandidate.objects.create(
        item=item,
        batch=batch,
        match_class=OpinionMatchClass.EXACT_BINARY_MATTER,
        state=OpinionCandidateState.NOT_AN_OPINION,
    )
    refresh_archive_binaries([binary.pk])

    assert engine.suggestions_for(current, specialist).materials == ()


@pytest.mark.parametrize(
    "state",
    [
        OpinionCandidateState.REJECTED,
        OpinionCandidateState.DUPLICATE,
        OpinionCandidateState.DEFERRED,
    ],
)
def test_a_rejected_duplicate_or_deferred_match_does_not_hide_the_letter(specialist, state):
    """Those states judge one proposed Matter match, not the letter's worth."""
    current = _matter(specialist, "Pakendiseaduse muutmine", number=961)
    other = _matter(specialist, "Metsanduse arengukava", number=962)
    binary, item, batch = _hold_letter(seed=f"letter-961-{state}", title="Pakendiseaduse arvamus")
    OpinionMatchCandidate.objects.create(
        item=item,
        batch=batch,
        matter=other,
        match_class=OpinionMatchClass.EXACT_BINARY_MATTER,
        state=state,
    )
    refresh_archive_binaries([binary.pk])

    assert [item.key for item in engine.suggestions_for(current, specialist).materials] == [
        binary.pk
    ]


def test_a_letter_already_filed_onto_this_matter_is_not_a_suggestion(specialist):
    current = _matter(specialist, "Pakendiseaduse muutmine", number=963)
    binary, _, _ = _hold_letter(seed="letter-963", title="Pakendiseaduse muutmise arvamus")
    link_matter(binary=binary, matter=current, basis=ArchiveLinkBasis.EXACT_BINARY)
    refresh_archive_binaries([binary.pk])

    assert engine.suggestions_for(current, specialist).materials == ()


def test_a_letter_with_a_visible_submission_is_offered_once_as_the_submission(specialist):
    source = _matter(specialist, "Pakendiseaduse muutmine 2024", number=964, year=2024)
    current = _matter(specialist, "Pakendiseaduse muutmine 2026", number=965)
    opinion = _sent_opinion(source, "Koja arvamus pakendiseaduse muutmise kohta")
    binary, item, batch = _hold_letter(seed="letter-964", title="Pakendiseaduse muutmise arvamus")
    OpinionSubmissionImport.objects.create(
        item=item,
        submission=opinion,
        batch=batch,
        match_class=OpinionMatchClass.EXACT_BINARY_MATTER,
    )
    refresh_archive_binaries([binary.pk])

    materials = engine.suggestions_for(current, specialist).materials
    assert [(item.kind, item.key) for item in materials] == [(engine.KIND_SUBMISSION, opinion.pk)]


def test_a_hidden_submission_does_not_silence_a_visible_letter(specialist, administrator):
    """Brief §17: the archive is the administrator's to read; the restricted
    Submission behind a letter must not become an oracle by suppressing it."""
    source = _matter(specialist, "Pakendiseaduse muutmine 2024", number=966, year=2024)
    current = _matter(specialist, "Pakendiseaduse muutmine 2026", number=967)
    opinion = _sent_opinion(source, "Koja arvamus pakendiseaduse muutmise kohta")
    _restrict(opinion)
    binary, item, batch = _hold_letter(seed="letter-966", title="Pakendiseaduse muutmise arvamus")
    OpinionSubmissionImport.objects.create(
        item=item,
        submission=opinion,
        batch=batch,
        match_class=OpinionMatchClass.EXACT_BINARY_MATTER,
    )
    refresh_archive_binaries([binary.pk])

    for_admin = engine.suggestions_for(current, administrator).materials
    assert [(item.kind, item.key) for item in for_admin] == [(engine.KIND_ARCHIVE, binary.pk)]
    assert for_admin[0].label == "Arhiivimaterjal"
    assert "Koja arvamus" not in for_admin[0].title

    for_specialist = engine.suggestions_for(current, specialist).materials
    assert [(item.kind, item.key) for item in for_specialist] == [
        (engine.KIND_SUBMISSION, opinion.pk)
    ]


def test_an_opinion_on_a_confirmed_related_matter_is_offered_and_says_why(specialist):
    related = _matter(specialist, "Metsanduse arengukava", number=968)
    current = _matter(specialist, "Pakendiseaduse muutmine", number=969)
    _sent_opinion(related, "Koja arvamus metsanduse arengukavale")
    services.link_related_matters(matter=current, other=related, actor=specialist)

    (item,) = engine.suggestions_for(current, specialist).materials
    assert item.reasons == ("Seotud teema arvamus",)


def test_without_a_search_row_the_page_still_works_and_suggestions_degrade(specialist, signed_in):
    tag = factories.TagFactory(name_et="jäätmed")
    current = _matter(specialist, "Jäätmeseaduse muutmine", number=970, tags=[tag])
    by_catalogue = _matter(specialist, "Jäätmeseaduse rakendamine", number=971, tags=[tag])
    by_text_only = _matter(specialist, "Jäätmeseaduse ja pakendiseaduse muutmine", number=972)
    SearchDocument.objects.all().delete()

    result = engine.suggestions_for(current, specialist)

    # The catalogue still finds one, and the act is still read off the title.
    assert [item.matter for item in result.matters] == [by_catalogue]
    assert "Sama õigusakt: jäätmeseadus" in result.matters[0].reasons
    assert by_text_only not in [item.matter for item in result.matters]
    response = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": current.pk}))
    assert response.status_code == 200
    assert SearchDocument.objects.count() == 0, "nothing rebuilt anything"


def test_the_order_is_the_same_every_time(specialist):
    current = _matter(specialist, "Jäätmeseaduse muutmine", number=973)
    for number, title in enumerate(
        ["Jäätmeseaduse rakendamine", "Jäätmeseaduse täiendamine", "Jäätmeseaduse kord"],
        start=974,
    ):
        _matter(specialist, title, number=number)

    first = [
        (item.matter.pk, item.score) for item in engine.suggestions_for(current, specialist).matters
    ]
    second = [
        (item.matter.pk, item.score) for item in engine.suggestions_for(current, specialist).matters
    ]

    assert first == second
    assert len(first) == 3


def test_several_shared_areas_are_one_concise_reason(specialist):
    areas = [
        factories.PolicyAreaFactory(name_et=name) for name in ("Keskkond", "Maksud", "Energia")
    ]
    current = _matter(specialist, "Elektrituruseaduse muutmine", number=977, areas=areas)
    candidate = _matter(specialist, "Elektrituruseaduse rakendamine", number=978, areas=areas)

    reasons = _reasons_for(current, specialist, candidate)

    assert len(reasons) <= engine.MAX_REASONS
    assert sum(reason.startswith("Sama") for reason in reasons) >= 2
    assert any(reason.startswith("Samad valdkonnad") for reason in reasons)
    assert not any("Maksud, Keskkond, Energia" in reason for reason in reasons)


def test_top_five_then_show_more_up_to_fifteen(specialist):
    current = _matter(specialist, "Jäätmeseaduse muutmine", number=979)
    for offset in range(18):
        _matter(specialist, f"Jäätmeseaduse rakendamine {offset}", number=980 + offset, year=2020)

    default = engine.suggestions_for(current, specialist)
    more = engine.suggestions_for(current, specialist, limit=99)

    assert len(default.matters) == engine.DEFAULT_LIMIT
    assert default.has_more
    assert len(more.matters) == engine.MAX_LIMIT
    assert more.limit == engine.MAX_LIMIT


# ===========================================================================
# Read only, and bounded
# ===========================================================================


def _row_counts() -> dict[str, int]:
    return {
        model.__name__: model.objects.count()
        for model in (
            Matter,
            Submission,
            OpinionArchiveMatterLink,
            SearchDocument,
            OpinionArchiveSearchDocument,
            ChangeEvent,
            MatterRelation,
            MatterBackgroundMaterial,
            RelatedSuggestionDismissal,
        )
    }


def test_requesting_suggestions_writes_nothing(specialist, signed_in, keskkond):
    current = _matter(specialist, "Pakendiseaduse muutmine", number=998, areas=[keskkond])
    source = _matter(specialist, "Pakendiseaduse muutmine 2024", number=999, year=2024)
    _sent_opinion(source, "Koja arvamus pakendiseaduse kohta")
    _hold_letter(seed="letter-998", title="Pakendiseaduse arvamus")
    before = _row_counts()
    latest_event = ChangeEvent.objects.order_by("-created_at").values_list("pk", flat=True).first()

    engine.suggestions_for(current, specialist, include_hidden=True, limit=15)
    signed_in.get(reverse("matters:matter_detail", kwargs={"pk": current.pk}))
    signed_in.get(reverse(SECTION, kwargs={"pk": current.pk}) + "?avatud=1&peidetud=1", **HX)
    signed_in.get(reverse(SECTION, kwargs={"pk": current.pk}) + "?avatud=1")

    assert _row_counts() == before
    assert (
        ChangeEvent.objects.order_by("-created_at").values_list("pk", flat=True).first()
        == latest_event
    )
    assert not hasattr(Matter, "last_recommended_at")


def test_the_engine_costs_a_fixed_number_of_queries_however_many_candidates(specialist, keskkond):
    def world(count: int, base: int) -> Matter:
        tag = factories.TagFactory()
        current = _matter(
            specialist, f"Jäätmeseaduse muutmine {base}", number=base, areas=[keskkond], tags=[tag]
        )
        for offset in range(count):
            other = _matter(
                specialist,
                f"Jäätmeseaduse rakendamine {base + offset}",
                number=base + 1 + offset,
                areas=[keskkond],
                tags=[tag],
            )
            _sent_opinion(other, f"Koja arvamus jäätmeseaduse kohta {base + offset}")
        return current

    small = world(2, 1100)
    large = world(12, 1200)

    with CaptureQueriesContext(connection) as few:
        engine.suggestions_for(small, specialist)
    with CaptureQueriesContext(connection) as many:
        engine.suggestions_for(large, specialist)

    assert len(few) == len(many), (len(few), len(many))
    assert len(many) <= 30


def test_the_projections_are_read_under_their_current_versions():
    """A new consumer of the projections changes neither recipe (brief §47)."""
    assert INDEX_VERSION == "AUTH003.1"
    assert ARCHIVE_INDEX_VERSION == "1"


def test_relation_events_stay_out_of_the_timeline_and_the_feed():
    from app.matters.overview import _MATTER_EVENTS

    for event_type in (
        ChangeEventType.MATTER_RELATION_ADDED,
        ChangeEventType.MATTER_RELATION_REMOVED,
        ChangeEventType.BACKGROUND_MATERIAL_ADDED,
        ChangeEventType.BACKGROUND_MATERIAL_REMOVED,
    ):
        assert event_type not in TIMELINE_EVENT_TYPES
        assert event_type not in _MATTER_EVENTS


def test_the_migration_infers_nothing():
    import importlib

    module = importlib.import_module("app.related_materials.migrations.0001_initial")
    operations = module.Migration.operations
    assert all(type(operation).__name__ == "CreateModel" for operation in operations)
    assert MatterRelation.objects.count() == 0
    assert MatterBackgroundMaterial.objects.count() == 0
    assert RelatedSuggestionDismissal.objects.count() == 0


# ===========================================================================
# The TEST-data purge sees the new rows
# ===========================================================================


def test_a_relation_across_the_test_boundary_blocks_a_purge(specialist):
    synthetic = create_matter(
        title="Arendusteema", actor=specialist, owner=specialist, data_class=MatterDataClass.TEST
    )
    real = _matter(specialist, "Päris teema", number=1300)
    services.link_related_matters(matter=synthetic, other=real, actor=specialist)

    plan = purge.build_purge_plan()

    assert plan.is_blocked
    assert any(
        blocker.category == purge.BLOCKED_BY_REAL_REFERENCE
        and blocker.label.startswith("related_materials.MatterRelation.")
        for blocker in plan.blockers
    )
    assert MatterRelation.objects.count() == 1, "an inventory deletes nothing"


def test_a_relation_between_two_test_matters_is_owned_and_purgeable(specialist):
    first = create_matter(
        title="Arendusteema A", actor=specialist, owner=specialist, data_class=MatterDataClass.TEST
    )
    second = create_matter(
        title="Arendusteema B", actor=specialist, owner=specialist, data_class=MatterDataClass.TEST
    )
    services.link_related_matters(matter=first, other=second, actor=specialist)
    services.dismiss_related_suggestion(matter=first, actor=specialist, candidate_matter=second)

    plan = purge.build_purge_plan()

    assert plan.count_of("related_materials.MatterRelation") == 1
    assert not any(blocker.label.startswith("related_materials.") for blocker in plan.blockers)


# ===========================================================================
# The HTTP boundary
# ===========================================================================


def test_the_matter_page_renders_the_section_closed_and_computes_no_suggestions(
    specialist, signed_in
):
    current = _matter(specialist, "Jäätmeseaduse muutmine", number=1400)
    _matter(specialist, "Jäätmeseaduse rakendamine", number=1401)

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": current.pk})
    ).content.decode()

    assert 'id="seotud-materjalid"' in body
    assert 'data-suggestions="closed"' in body
    assert "Võimalikud seosed" in body
    assert "data-related-suggestions" not in body
    assert "Jäätmeseaduse rakendamine" not in body
    assert "Lisa seotud teema" in body


def test_a_reader_gets_the_section_without_controls(specialist, reader, client):
    current = _matter(specialist, "Jäätmeseaduse muutmine", number=1402)
    _matter(specialist, "Jäätmeseaduse rakendamine", number=1403)
    client.force_login(reader)

    body = client.get(
        reverse(SECTION, kwargs={"pk": current.pk}) + "?avatud=1", **HX
    ).content.decode()

    assert "Jäätmeseaduse rakendamine" in body
    assert "Sama õigusakt: jäätmeseadus" in body
    for control in ("Seo teemaga", "Ei ole seotud", "Lisa seotud teema", "Lisa taustmaterjaliks"):
        assert control not in body


def test_the_fragment_and_the_page_carry_the_reasons(specialist, signed_in):
    current = _matter(specialist, "Jäätmeseaduse muutmine", number=1404)
    _matter(specialist, "Jäätmeseaduse rakendamine", number=1405)
    url = reverse(SECTION, kwargs={"pk": current.pk}) + "?avatud=1"

    fragment = signed_in.get(url, **HX)
    page = signed_in.get(url)

    for response in (fragment, page):
        body = response.content.decode()
        assert response.status_code == 200
        assert 'data-suggestions="open"' in body
        assert "Sama õigusakt: jäätmeseadus" in body
        assert "Seo teemaga" in body
        assert "%" not in body.split('id="seotud-materjalid"', 1)[1].split("</section>", 1)[0]
    assert "<title>" in page.content.decode()
    assert "<title>" not in fragment.content.decode()


def test_linking_dismissing_and_restoring_through_the_routes(specialist, signed_in):
    current = _matter(specialist, "Jäätmeseaduse muutmine", number=1406)
    candidate = _matter(specialist, "Jäätmeseaduse rakendamine", number=1407)
    other = _matter(specialist, "Jäätmeseaduse täiendamine", number=1408)

    response = signed_in.post(
        reverse("related_materials:dismiss", kwargs={"pk": current.pk}),
        {"liik": "teema", "kandidaat": str(other.pk)},
        **HX,
    )
    assert response.status_code == 200
    body = response.content.decode()
    assert "Soovitus on peidetud." in body
    assert "Jäätmeseaduse täiendamine" not in body.split("Näita peidetud", 1)[0]
    assert "Näita peidetud · 1" in body

    response = signed_in.post(
        reverse("related_materials:link", kwargs={"pk": current.pk}),
        {"teema": str(candidate.pk)},
    )
    assert response.status_code == 302
    assert response["Location"].endswith("#seotud-materjalid")
    assert MatterRelation.objects.count() == 1

    body = signed_in.get(
        reverse(SECTION, kwargs={"pk": current.pk}) + "?avatud=1&peidetud=1", **HX
    ).content.decode()
    confirmed = body.split("data-related-confirmed", 1)[1].split("</ul>", 1)[0]
    assert "Jäätmeseaduse rakendamine" in confirmed
    hidden = body.split("data-related-hidden", 1)[1]
    assert "Jäätmeseaduse täiendamine" in hidden
    assert "Taasta soovitus" in hidden

    response = signed_in.post(
        reverse("related_materials:restore", kwargs={"pk": current.pk}),
        {"liik": "teema", "kandidaat": str(other.pk)},
        **HX,
    )
    assert response.status_code == 200
    assert RelatedSuggestionDismissal.objects.count() == 0
    assert "Jäätmeseaduse täiendamine" in response.content.decode()


def test_background_routes_for_an_opinion_and_a_letter(specialist, signed_in):
    source = _matter(specialist, "Pakendiseaduse muutmine 2024", number=1409, year=2024)
    current = _matter(specialist, "Pakendiseaduse muutmine 2026", number=1410)
    opinion = _sent_opinion(source, "Koja arvamus pakendiseaduse muutmise kohta")
    binary, _, _ = _hold_letter(seed="letter-1409", title="Pakendiseaduse muutmise arvamus")

    response = signed_in.post(
        reverse("related_materials:add_background", kwargs={"pk": current.pk}),
        {"liik": "arvamus", "kandidaat": str(opinion.pk)},
        **HX,
    )
    assert response.status_code == 200
    response = signed_in.post(
        reverse("related_materials:add_background", kwargs={"pk": current.pk}),
        {"liik": "arhiiv", "kandidaat": str(binary.pk)},
        **HX,
    )
    assert response.status_code == 200
    body = response.content.decode()
    background = body.split("data-related-background", 1)[1].split("</ul>", 1)[0]
    assert "Koja arvamus pakendiseaduse muutmise kohta" in background
    assert "Pakendiseaduse muutmise arvamus" in background
    assert reverse("legacy_import:opinion_archive_detail", kwargs={"pk": binary.pk}) in background
    assert binary.storage_key not in body
    assert OpinionArchiveMatterLink.objects.count() == 0
    assert Submission.objects.get(pk=opinion.pk).matter_id == source.pk

    response = signed_in.post(
        reverse("related_materials:remove_background", kwargs={"pk": current.pk}),
        {"liik": "arhiiv", "kandidaat": str(binary.pk)},
    )
    assert response.status_code == 302
    assert MatterBackgroundMaterial.objects.count() == 1


def test_an_unknown_or_malformed_target_is_a_404_and_writes_nothing(specialist, signed_in):
    current = _matter(specialist, "Jäätmeseaduse muutmine", number=1411)
    for payload in (
        {"teema": "not-a-uuid"},
        {"teema": "00000000-0000-7000-8000-000000000000"},
        {},
    ):
        response = signed_in.post(
            reverse("related_materials:link", kwargs={"pk": current.pk}), payload
        )
        assert response.status_code == 404
    response = signed_in.post(
        reverse("related_materials:dismiss", kwargs={"pk": current.pk}),
        {"liik": "midagi", "kandidaat": str(current.pk)},
    )
    assert response.status_code == 404
    assert MatterRelation.objects.count() == 0
    assert RelatedSuggestionDismissal.objects.count() == 0


def test_a_reader_cannot_write_and_learns_nothing_from_the_verb(specialist, reader, client):
    current = _matter(specialist, "Jäätmeseaduse muutmine", number=1412)
    other = _matter(specialist, "Jäätmeseaduse rakendamine", number=1413)
    client.force_login(reader)

    for name in ("link", "unlink"):
        response = client.post(
            reverse(f"related_materials:{name}", kwargs={"pk": current.pk}),
            {"teema": str(other.pk)},
        )
        assert response.status_code == 404
    assert (
        client.get(reverse("related_materials:picker", kwargs={"pk": current.pk})).status_code
        == 404
    )
    assert MatterRelation.objects.count() == 0


def test_the_archive_route_is_a_404_for_somebody_who_may_not_read_the_archive(
    specialist, reader, client
):
    current = _matter(specialist, "Pakendiseaduse muutmine", number=1414)
    binary, _, _ = _hold_letter(seed="letter-1414", title="Pakendiseaduse arvamus")
    head = factories.DepartmentHeadFactory()
    client.force_login(head)
    assert (
        client.post(
            reverse("related_materials:add_background", kwargs={"pk": current.pk}),
            {"liik": "arhiiv", "kandidaat": str(binary.pk)},
        ).status_code
        == 302
    )
    assert MatterBackgroundMaterial.objects.count() == 1
    # A reader is refused before the target is even looked up.
    client.force_login(reader)
    assert (
        client.post(
            reverse("related_materials:dismiss", kwargs={"pk": current.pk}),
            {"liik": "arhiiv", "kandidaat": str(binary.pk)},
        ).status_code
        == 404
    )


def test_the_picker_offers_visible_matters_and_links_without_a_score(specialist, signed_in):
    current = _matter(specialist, "Jäätmeseaduse muutmine", number=1415)
    unrelated = _matter(specialist, "Metsanduse arengukava aastani 2030", number=1416)
    assert unrelated not in _suggested_matters(current, specialist)

    body = signed_in.get(
        reverse("related_materials:picker", kwargs={"pk": current.pk}) + "?q=metsanduse", **HX
    ).content.decode()
    assert "Metsanduse arengukava aastani 2030" in body
    assert "Seo teemaga" in body

    response = signed_in.post(
        reverse("related_materials:link", kwargs={"pk": current.pk}), {"teema": str(unrelated.pk)}
    )
    assert response.status_code == 302
    assert [item.other for item in related_materials_for(current, specialist).relations] == [
        unrelated
    ]
    # The picker no longer offers what is already related, nor the Matter itself.
    body = signed_in.get(
        reverse("related_materials:picker", kwargs={"pk": current.pk}) + "?q=metsanduse", **HX
    ).content.decode()
    assert "Metsanduse arengukava aastani 2030" not in body


def test_the_write_gate_refuses_every_non_business_role(specialist, client):
    current = _matter(specialist, "A", number=1417)
    other = _matter(specialist, "B", number=1418)
    for actor in (
        factories.ReaderFactory(),
        factories.AdministratorFactory(),
        factories.UserFactory(is_active=False),
    ):
        client.force_login(actor)
        response = client.post(
            reverse("related_materials:link", kwargs={"pk": current.pk}), {"teema": str(other.pk)}
        )
        assert response.status_code in {302, 404}, actor.role
        assert MatterRelation.objects.count() == 0
    assert UserRole.READER  # the roles above are the ones the gate names


# ===========================================================================
# The read-only preview command
# ===========================================================================


def test_the_preview_command_prints_candidates_and_writes_nothing(specialist):
    _matter(specialist, "Jäätmeseaduse muutmine", number=1500)
    _matter(specialist, "Jäätmeseaduse rakendamine", number=1501)
    before = _row_counts()
    out = StringIO()

    call_command("related_materials_preview", "2026_1500", viewer=specialist.upn, stdout=out)

    text = out.getvalue()
    assert "Jäätmeseaduse rakendamine" in text
    assert "Sama õigusakt: jäätmeseadus" in text
    assert _row_counts() == before


def test_the_preview_command_refuses_real_data_without_the_flag(specialist, settings):
    _matter(specialist, "Jäätmeseaduse muutmine", number=1502)
    settings.REAL_DATA_ALLOWED = True
    with pytest.raises(CommandError):
        call_command("related_materials_preview", "2026_1502", viewer=specialist.upn)


def test_the_preview_command_does_not_distinguish_hidden_from_missing(specialist, reader):
    restricted = _matter(
        specialist, "Konfidentsiaalne", number=1503, visibility=Visibility.RESTRICTED
    )
    with pytest.raises(CommandError) as hidden:
        call_command("related_materials_preview", "2026_1503", viewer=reader.upn)
    with pytest.raises(CommandError) as missing:
        call_command("related_materials_preview", "2026_9999", viewer=reader.upn)
    assert str(hidden.value) == str(missing.value)
    assert restricted.title not in str(hidden.value)
