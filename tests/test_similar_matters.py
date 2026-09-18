"""The similar-Matter finder: the instrument signal, the indicators, the draft.

This file covers what the 2026-09-16 round added to the engine ADR 0062 built
(docs/adr/0087). The engine's original rules — the threshold, the caps, the
tie-break, authorization before ranking, GET writing nothing — are pinned by
`tests/test_related_materials.py` and are not restated here.

Three families:

* the **`Õigusakt` signal** — the reviewed instrument type is a reason, it is
  weak on its own by construction, `Muu` is not a match, and a named act in a
  title and an instrument type on the record are two different claims;
* the **indicators** — what Koda did on a candidate, computed under the
  reader's own child visibility, and costing the same three queries whether
  there is one card or fifteen;
* the **draft** — `Uus teema` gets the same answers before anything is saved,
  writes nothing at all, and says nothing at all when there is too little.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.documents.enums import DocumentRole
from app.documents.services import add_evidence_version
from app.intelligence.enums import WorkVictoryStatus
from app.matters.models import Matter
from app.related_materials import engine
from app.related_materials.models import MatterRelation, RelatedSuggestionDismissal
from app.submissions.enums import SubmissionStatus
from app.taxonomy.models import LegalInstrumentType
from app.workflow.enums import Track
from tests import factories

pytestmark = pytest.mark.django_db


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
    instruments: Any = (),
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
    if instruments:
        matter.legal_instruments.set(instruments)
    return matter


def _instrument(key: str) -> LegalInstrumentType:
    """One reviewed instrument type, looked up and never created.

    The vocabulary is reference data seeded by `taxonomy/0006`. A fixture that
    invented an eighteenth `Õigusakt` would be testing against a register that
    does not exist (`app/taxonomy/legal_instruments.py`).
    """
    return LegalInstrumentType.objects.get(key=key)


def _suggested(matter: Matter, viewer: Any, **kwargs: Any) -> list[Matter]:
    return [item.matter for item in engine.suggestions_for(matter, viewer, **kwargs).matters]


def _reasons_for(matter: Matter, viewer: Any, candidate: Matter) -> tuple[str, ...]:
    for item in engine.suggestions_for(matter, viewer).matters:
        if item.matter.pk == candidate.pk:
            return item.reasons
    raise AssertionError(f"{candidate.title!r} was not suggested")


def _outcome_for(matter: Matter, viewer: Any, candidate: Matter) -> engine.MatterOutcome:
    for item in engine.suggestions_for(matter, viewer).matters:
        if item.matter.pk == candidate.pk:
            return item.outcome
    raise AssertionError(f"{candidate.title!r} was not suggested")


@pytest.fixture
def keskkond(db):
    return factories.PolicyAreaFactory(name_et="Keskkond")


@pytest.fixture
def pakend(db):
    return factories.TagFactory(name_et="pakend")


@pytest.fixture
def ministry(db):
    return factories.OrganisationFactory(name="Näidiskliimaministeerium")


# ===========================================================================
# The `Õigusakt` signal
# ===========================================================================


def test_the_same_instrument_type_is_an_explained_reason(specialist, pakend, ministry):
    """A shared `Õigusakt` says so in the vocabulary's own words.

    The two titles share no subject word on purpose. `MAX_REASONS` is three and
    the instrument type is the weakest structured signal there is, so a pair
    that also shares a word would push it off the card — correctly, because
    three stronger reasons are a better explanation than four mixed ones. What
    is under test here is the reason, not the ordering.
    """
    maarus = _instrument("maarus")
    ours = _matter(
        specialist,
        "Ringlussevõetud materjalide kasutus",
        number=1,
        tags=[pakend],
        instruments=[maarus],
        addressee=ministry,
    )
    theirs = _matter(
        specialist,
        "Jäätmekäitluse tehnilised tingimused",
        number=2,
        tags=[pakend],
        instruments=[maarus],
        addressee=ministry,
    )

    assert theirs in _suggested(ours, specialist)
    assert "Sama õigusakti liik: Määrus" in _reasons_for(ours, specialist, theirs)


def test_two_shared_instrument_types_are_one_reason_and_not_two(specialist, pakend, ministry):
    """The cap is the product rule: a card lists liigid, it does not enumerate."""
    both = [_instrument("maarus"), _instrument("direktiiv")]
    ours = _matter(
        specialist, "Ringlussevõtt", number=1, tags=[pakend], instruments=both, addressee=ministry
    )
    theirs = _matter(
        specialist, "Jäätmekäitlus", number=2, tags=[pakend], instruments=both, addressee=ministry
    )

    reasons = _reasons_for(ours, specialist, theirs)
    liigid = [reason for reason in reasons if "õigusakti li" in reason.lower()]
    assert len(liigid) == 1
    assert liigid[0].startswith("Samad õigusakti liigid:")


def test_the_same_instrument_type_alone_is_never_enough(specialist):
    """Half the register is a `Seadus`. Alone it is not a recommendation.

    The sharp case for this whole signal: nothing else is shared — no tag, no
    area, no organisation, no subject word — and the two files are both laws.
    So is most of the register.
    """
    seadus = _instrument("seadus")
    ours = _matter(specialist, "Pakendite ringlussevõtu korraldus", number=1, instruments=[seadus])
    theirs = _matter(
        specialist, "Ühistranspordi rahastamise alused", number=2, instruments=[seadus]
    )

    assert theirs not in _suggested(ours, specialist)


def test_an_instrument_type_and_an_organisation_together_are_still_not_enough(specialist, ministry):
    """2.5 against a threshold of 3.5, and the arithmetic is the rule.

    A ministry sends hundreds of laws. «Same ministry, and both are laws» is a
    description of the department's ordinary week, not a reason to read a file.
    """
    seadus = _instrument("seadus")
    ours = _matter(
        specialist,
        "Pakendite ringlussevõtu korraldus",
        number=1,
        instruments=[seadus],
        addressee=ministry,
    )
    theirs = _matter(
        specialist,
        "Ühistranspordi rahastamise alused",
        number=2,
        instruments=[seadus],
        addressee=ministry,
    )

    assert theirs not in _suggested(ours, specialist)


def test_muu_is_not_a_shared_instrument(specialist, pakend, ministry):
    """Two files that both failed to fit the list have agreed about nothing.

    `Muu` plus free text is the vocabulary's escape hatch; matching on it would
    report a shared absence as a shared fact.
    """
    muu = _instrument("muu")
    ours = _matter(
        specialist, "Ringlussevõtt", number=1, tags=[pakend], instruments=[muu], addressee=ministry
    )
    theirs = _matter(
        specialist, "Jäätmekäitlus", number=2, tags=[pakend], instruments=[muu], addressee=ministry
    )

    # The tag and the ministry still qualify it; the instrument adds nothing.
    reasons = _reasons_for(ours, specialist, theirs)
    assert not any("õigusakti li" in reason.lower() for reason in reasons)
    assert not any("Muu" in reason for reason in reasons)


def test_a_named_act_and_an_instrument_type_are_different_claims(specialist):
    """«Sama õigusakt: pakendiseadus» and «Sama õigusakti liik: Seadus».

    One is read out of the title, the other is on the record, and a card may
    truthfully carry both. What it must never do is print one label twice.
    """
    seadus = _instrument("seadus")
    ours = _matter(specialist, "Pakendiseaduse muutmise eelnõu", number=1, instruments=[seadus])
    theirs = _matter(specialist, "Pakendiseaduse rakendusaktid", number=2, instruments=[seadus])

    reasons = _reasons_for(ours, specialist, theirs)
    assert any(reason.startswith("Sama õigusakt: ") for reason in reasons)
    assert any(reason.startswith("Sama õigusakti liik: ") for reason in reasons)
    assert len(set(reasons)) == len(reasons)


def test_an_instrument_type_never_reaches_a_matter_the_reader_may_not_open(
    specialist, other_specialist, reader, pakend, ministry
):
    """Authorization runs before the signal does, like every other signal."""
    maarus = _instrument("maarus")
    ours = _matter(
        specialist,
        "Ringlussevõtt",
        number=1,
        tags=[pakend],
        instruments=[maarus],
        addressee=ministry,
    )
    hidden = _matter(
        other_specialist,
        "Jäätmekäitlus",
        number=2,
        tags=[pakend],
        instruments=[maarus],
        addressee=ministry,
        visibility=Visibility.RESTRICTED,
    )

    assert hidden not in _suggested(ours, reader)


# ===========================================================================
# What Koda did there
# ===========================================================================


def _sent_opinion(matter: Matter, title: str) -> Any:
    document = factories.DocumentFactory(matter=matter, role=DocumentRole.KODA_SUBMISSION_FINAL)
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
        sent_at=timezone.now(),
        final_version=version,
    )


@pytest.fixture
def a_pair(specialist, pakend, ministry):
    """Two Matters that qualify for each other, so the card is always rendered."""
    ours = _matter(
        specialist,
        "Ringlussevõetud materjalide nõuded",
        number=1,
        tags=[pakend],
        addressee=ministry,
    )
    theirs = _matter(
        specialist,
        "Jäätmekäitluse tehnilised nõuded",
        number=2,
        tags=[pakend],
        addressee=ministry,
    )
    return ours, theirs


def test_a_candidate_with_nothing_recorded_shows_no_indicators(specialist, a_pair):
    ours, theirs = a_pair
    assert _outcome_for(ours, specialist, theirs).is_empty


def test_a_recorded_opinion_and_a_send_are_both_indicated(specialist, a_pair):
    ours, theirs = a_pair
    _sent_opinion(theirs, "Koja arvamus jäätmekäitluse kohta")

    outcome = _outcome_for(ours, specialist, theirs)
    assert outcome.has_opinion
    assert outcome.has_sent_submission
    assert not outcome.has_work_victory


def test_only_a_confirmed_work_victory_is_indicated(specialist, a_pair):
    """A candidate is somebody's proposal, not a win the file can claim."""
    ours, theirs = a_pair
    factories.WorkVictoryFactory(matter=theirs, status=WorkVictoryStatus.CANDIDATE)

    assert not _outcome_for(ours, specialist, theirs).has_work_victory

    factories.WorkVictoryFactory(
        matter=theirs, status=WorkVictoryStatus.CONFIRMED, confirmed_at=timezone.now()
    )
    assert _outcome_for(ours, specialist, theirs).has_work_victory


def test_a_draft_submission_is_not_a_send(specialist, a_pair):
    ours, theirs = a_pair
    factories.SubmissionFactory(matter=theirs, title="Mustand", status=SubmissionStatus.DRAFT)

    assert not _outcome_for(ours, specialist, theirs).has_sent_submission


def test_an_indicator_never_reports_a_record_the_reader_may_not_open(
    specialist, other_specialist, reader, pakend, ministry
):
    """The sharp one: the Matter is visible and the opinion on it is not.

    A pill saying «Arvamus olemas» is an assertion that a record exists. On a
    restricted child it is exactly the existence disclosure ADR 0038 forbids,
    and the Matter being open to this reader does not make it their opinion.
    """
    ours = _matter(
        other_specialist,
        "Ringlussevõetud materjalide nõuded",
        number=1,
        tags=[pakend],
        addressee=ministry,
    )
    theirs = _matter(
        other_specialist,
        "Jäätmekäitluse tehnilised nõuded",
        number=2,
        tags=[pakend],
        addressee=ministry,
    )
    submission = _sent_opinion(theirs, "Koja arvamus jäätmekäitluse kohta")
    # The document first. `submissions_check_final_evidence` refuses final
    # evidence less restricted than the Submission that points at it, so
    # restricting the Submission before its document is refused by the
    # database — which is the invariant doing its job, not a test detail.
    document = submission.final_version.document
    document.visibility_override = Visibility.RESTRICTED
    document.save(update_fields=["visibility_override"])
    submission.visibility_override = Visibility.RESTRICTED
    submission.save(update_fields=["visibility_override"])

    outcome = _outcome_for(ours, reader, theirs)
    assert not outcome.has_opinion
    assert not outcome.has_sent_submission

    # And the owner, who may open both, still sees them — so the test above is
    # measuring visibility rather than a broken query.
    owner_outcome = _outcome_for(ours, other_specialist, theirs)
    assert owner_outcome.has_opinion
    assert owner_outcome.has_sent_submission


def test_the_indicators_cost_the_same_three_queries_however_many_cards(
    specialist, pakend, ministry
):
    """Three per kind of record, never three per card.

    An `IN` over the five keys that will be rendered. The comparison is against
    a world with one candidate rather than an absolute number, because the
    engine's own pool queries dominate the total and this test is about the
    indicators' shape, not the engine's cost.
    """
    ours = _matter(
        specialist,
        "Ringlussevõetud materjalide nõuded",
        number=1,
        tags=[pakend],
        addressee=ministry,
    )
    one = _matter(
        specialist,
        "Jäätmekäitluse tehnilised nõuded",
        number=2,
        tags=[pakend],
        addressee=ministry,
    )
    _sent_opinion(one, "Koja arvamus 1")

    with CaptureQueriesContext(connection) as few:
        engine.suggestions_for(ours, specialist)

    for index in range(10):
        extra = _matter(
            specialist,
            f"Jäätmekäitluse kord {index}",
            number=100 + index,
            tags=[pakend],
            addressee=ministry,
        )
        _sent_opinion(extra, f"Koja arvamus {index}")
        factories.WorkVictoryFactory(
            matter=extra, status=WorkVictoryStatus.CONFIRMED, confirmed_at=timezone.now()
        )

    with CaptureQueriesContext(connection) as many:
        engine.suggestions_for(ours, specialist)

    assert len(many) == len(few), (
        f"{len(few)} queries for one candidate, {len(many)} for eleven — "
        "the indicators are being asked per card"
    )


def test_the_card_prints_the_indicators_it_was_given(client, specialist, a_pair):
    ours, theirs = a_pair
    _sent_opinion(theirs, "Koja arvamus jäätmekäitluse kohta")
    factories.WorkVictoryFactory(
        matter=theirs, status=WorkVictoryStatus.CONFIRMED, confirmed_at=timezone.now()
    )
    client.force_login(specialist)

    body = client.get(
        reverse("related_materials:section", kwargs={"pk": ours.pk}), {"avatud": "1"}
    ).content.decode()

    assert "Arvamus olemas" in body
    assert "Välja saadetud" in body
    assert "Töövõit" in body
    # The score is internal and stays internal, on this surface as on the others.
    assert "%" not in body.split('class="relatedcard__did"')[1][:400]


# ===========================================================================
# `Uus teema` — the draft
# ===========================================================================


DRAFT_URL_NAME = "related_materials:draft_suggestions"


def _draft(client, **params: Any) -> str:
    return client.get(reverse(DRAFT_URL_NAME), params).content.decode()


def test_a_blank_form_suggests_nothing_at_all(client, specialist, a_pair):
    """Not an empty state — nothing. The element stays empty and the page ends."""
    client.force_login(specialist)

    body = _draft(client)

    assert body.strip() == ""


def test_a_title_of_generic_words_alone_suggests_nothing(client, specialist, a_pair):
    """«Eelnõu kooskõlastamine» is a title half the register shares."""
    client.force_login(specialist)

    body = _draft(client, title="Eelnõu kooskõlastamine")

    assert "relatedcard" not in body


def test_a_meaningful_title_finds_the_earlier_matter(client, specialist, pakend, ministry):
    """The same engine, before the file exists."""
    earlier = _matter(
        specialist, "Pakendiseaduse muutmise eelnõu", number=1, tags=[pakend], addressee=ministry
    )
    client.force_login(specialist)

    body = _draft(client, title="Pakendiseaduse rakendusaktide eelnõu")

    assert earlier.title in body
    assert "Sarnased teemad" in body


def test_two_structured_facts_are_enough_without_a_title(client, specialist, pakend, ministry):
    """A ministry and a tag chosen from the pickers, before anything is typed."""
    maarus = _instrument("maarus")
    earlier = _matter(
        specialist,
        "Jäätmekäitluse tehnilised nõuded",
        number=1,
        tags=[pakend],
        instruments=[maarus],
        addressee=ministry,
    )
    client.force_login(specialist)

    body = _draft(
        client,
        addressee_organisation=str(ministry.pk),
        legal_instruments=str(maarus.pk),
        title="Ringlussevõetud materjalide nõuded",
    )

    assert earlier.title in body


def test_the_draft_never_suggests_a_matter_the_reader_may_not_open(
    client, specialist, other_specialist, reader, pakend, ministry
):
    """The boundary is the engine's, and a crafted request does not get past it."""
    hidden = _matter(
        other_specialist,
        "Pakendiseaduse muutmise eelnõu",
        number=1,
        tags=[pakend],
        addressee=ministry,
        visibility=Visibility.RESTRICTED,
    )
    client.force_login(reader)

    body = _draft(
        client,
        title="Pakendiseaduse rakendusaktide eelnõu",
        addressee_organisation=str(ministry.pk),
    )

    assert hidden.title not in body


def test_the_draft_writes_nothing(client, specialist, pakend, ministry):
    """No Matter, no relation, no dismissal. It is a GET and it stays one."""
    _matter(
        specialist, "Pakendiseaduse muutmise eelnõu", number=1, tags=[pakend], addressee=ministry
    )
    client.force_login(specialist)

    before = (
        Matter.objects.count(),
        MatterRelation.objects.count(),
        RelatedSuggestionDismissal.objects.count(),
    )
    _draft(client, title="Pakendiseaduse rakendusaktide eelnõu")
    after = (
        Matter.objects.count(),
        MatterRelation.objects.count(),
        RelatedSuggestionDismissal.objects.count(),
    )

    assert before == after


def test_the_draft_offers_no_control_that_could_write(client, specialist, pakend, ministry):
    """There is no Matter to attach anything to, so there is no button."""
    _matter(
        specialist, "Pakendiseaduse muutmise eelnõu", number=1, tags=[pakend], addressee=ministry
    )
    client.force_login(specialist)

    body = _draft(client, title="Pakendiseaduse rakendusaktide eelnõu")

    assert "relatedcard" in body
    assert "<form" not in body
    assert "<button" not in body


def test_the_draft_gives_the_same_reasons_the_saved_page_would(client, specialist, ministry):
    """A candidate that qualifies while typing still qualifies once saved.

    The point of reusing the engine rather than writing a second one: the
    lawyer is shown the same answer, in the same words, on both surfaces.

    Only what `Uus teema` can actually send — a title, a ministry and an
    `Õigusakt`. The create form has no `Sildid` control, so giving the saved
    side a tag would compare two different questions and pass or fail for a
    reason that has nothing to do with parity.
    """
    maarus = _instrument("maarus")
    title = "Ringlussevõetud pakendimaterjalide nõuded"
    earlier = _matter(
        specialist,
        "Ringlussevõetud pakendimaterjalide arvestus",
        number=1,
        instruments=[maarus],
        addressee=ministry,
    )
    saved = _matter(
        specialist,
        title,
        number=2,
        instruments=[maarus],
        addressee=ministry,
    )
    client.force_login(specialist)

    drafted = _draft(
        client,
        title=title,
        addressee_organisation=str(ministry.pk),
        legal_instruments=str(maarus.pk),
    )

    for reason in _reasons_for(saved, specialist, earlier):
        assert reason in drafted


def test_a_crafted_key_that_does_not_exist_contributes_nothing(client, specialist):
    """Catalogue keys are resolved, not trusted."""
    client.force_login(specialist)

    body = _draft(
        client,
        title="Pakendiseaduse rakendusaktide eelnõu",
        legal_instruments="00000000-0000-0000-0000-000000000000",
        policy_areas="not-a-uuid",
    )

    assert "relatedcard" not in body


def test_the_draft_route_needs_a_signed_in_reader(client):
    response = client.get(reverse(DRAFT_URL_NAME), {"title": "Pakendiseadus"})

    assert response.status_code in {302, 403}


def test_the_create_page_asks_for_suggestions_without_touching_the_form(client, specialist):
    """The wiring: one region, replaced by itself, including the whole form.

    `hx-target="this"` and `hx-swap="innerHTML"` together are what make the
    guarantee structural rather than careful — the response cannot name a form
    control because it never replaces one.
    """
    client.force_login(specialist)

    body = client.get(reverse("matters:matter_create")).content.decode()

    assert 'id="sarnased-teemad"' in body
    assert reverse(DRAFT_URL_NAME) in body
    region = body.split('id="sarnased-teemad"')[1][:600]
    assert 'hx-target="this"' in region
    assert 'hx-swap="innerHTML"' in region
    # Nothing but the four matching fields may cost a round trip.
    assert "from:#id_title" in region
    assert "from:#id_stage" not in region


def test_the_region_listens_for_a_restored_form(client, specialist):
    """QA-11: the region is woken by the browser as well as by the fields.

    A restored form fires none of the field triggers — nothing changed — so
    the region needs one trigger that is not a field. `app.js` says
    `sarnased:restored` on `pageshow` when the form has a matching answer in
    it, and this holds the two halves to the same name.

    Asserted as *added to* the field triggers rather than instead of them: the
    debounced list is ADR 0087 §4's and this round changes none of it.
    """
    client.force_login(specialist)

    body = client.get(reverse("matters:matter_create")).content.decode()
    region = body.split('id="sarnased-teemad"')[1][:900]

    assert "sarnased:restored" in region
    for field in (
        "#id_title",
        "#id_brief_summary",
        "#id_policy_areas",
        "#id_legal_instruments",
        "#id_source_organisations",
    ):
        assert f"from:{field}" in region
    # And still nothing that cannot change the answer.
    assert "from:#id_stage" not in region


def test_the_restore_asks_the_same_read_only_route(client, specialist, pakend, ministry):
    """A restore is the ordinary GET, so it inherits the ordinary boundary.

    There is no second endpoint, no restore flag and no server-side memory of a
    previous page: the same URL, answered the same way, writes nothing. The
    permission filtering that makes
    `test_the_draft_never_suggests_a_matter_the_reader_may_not_open` true is
    therefore true of a restored form for free — which is the whole reason the
    fix is a browser event rather than a route.
    """
    client.force_login(specialist)
    _matter(
        specialist, "Pakendiseaduse muutmise eelnõu", number=1, tags=[pakend], addressee=ministry
    )
    before = (
        Matter.objects.count(),
        MatterRelation.objects.count(),
        RelatedSuggestionDismissal.objects.count(),
    )

    first = _draft(client, title="Pakendiseaduse muutmise eelnõu")
    again = _draft(client, title="Pakendiseaduse muutmise eelnõu")

    assert first == again
    after = (
        Matter.objects.count(),
        MatterRelation.objects.count(),
        RelatedSuggestionDismissal.objects.count(),
    )
    assert after == before
