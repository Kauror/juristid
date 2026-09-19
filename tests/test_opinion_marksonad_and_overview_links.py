"""An opinion's own `Märksõnad`, and its links to this file's `Ülevaated / uudised`.

docs/adr/0093 resolved the two questions ADR 0091 left open, and resolved both of
them the other way. What makes that safe rather than merely possible is a short
list of properties, and this file is that list.

Five groups, because five different things can go wrong:

**The data model** — keywords are the governed vocabulary and only that; a Matter's
tags never become a letter's; nothing was backfilled.

**The relation** — many-to-many in both directions, same Matter or nothing, and a
refusal that takes the whole save with it. Nothing is ever inferred: the tests
that prove this are the ones that build a *tempting* coincidence — the same URL,
the same day, the same words — and then assert that no link appeared.

**The audit trail** — one row per fact that moved, none for a save that moved
nothing, and no letter's own text in a payload.

**Visibility** — the relation grants nothing in either direction, a guessed
identifier reaches nothing, and an actor cannot destroy a link to something they
were never shown.

**The surfaces, and what they must not have changed** — the send workflow, the
evidence invariants and the overview lifecycle are all exactly as they were.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.matters.enums import WebsiteOverviewStatus
from app.matters.services import (
    cancel_website_overview,
    plan_website_overview,
    publish_website_overview,
    set_tags,
)
from app.submissions.enums import SubmissionStatus
from app.submissions.links import (
    linked_submissions_by_overview,
    linked_website_overviews,
    selectable_tags,
    selectable_website_overviews,
)
from app.submissions.models import (
    Submission,
    SubmissionTagAssignment,
    SubmissionWebsiteOverviewLink,
)
from app.submissions.services import (
    set_submission_tags,
    set_submission_website_overviews,
)
from tests import factories

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def opinion(normal_matter):
    return factories.SubmissionFactory(matter=normal_matter, title="Koja arvamus eelnõule")


@pytest.fixture
def tags(db):
    return [factories.TagFactory(name_et=f"Märksõna {index}") for index in range(3)]


@pytest.fixture
def planned(normal_matter, specialist):
    return plan_website_overview(matter=normal_matter, actor=specialist)


@pytest.fixture
def published(normal_matter, specialist):
    return publish_website_overview(
        overview=plan_website_overview(matter=normal_matter, actor=specialist),
        url="https://koda.ee/uudised/pakendiseadus",
        published_on=timezone.localdate(),
        actor=specialist,
    )


def _metadata_url(submission):
    return reverse("submissions:metadata", kwargs={"pk": submission.pk})


# ---------------------------------------------------------------------------
# 1–10 · The data model: one governed vocabulary, and no inheritance
# ---------------------------------------------------------------------------


def test_an_opinion_can_carry_no_keywords(opinion):
    """Zero is the ordinary state, and the state every existing letter is in."""
    assert list(opinion.tags.all()) == []


def test_an_opinion_can_carry_one_keyword(opinion, tags, specialist):
    set_submission_tags(submission=opinion, tags=[tags[0]], actor=specialist)

    assert [tag.pk for tag in opinion.tags.all()] == [tags[0].pk]


def test_an_opinion_can_carry_several_keywords(opinion, tags, specialist):
    """Several, because one letter argues about more than one thing."""
    set_submission_tags(submission=opinion, tags=tags, actor=specialist)

    assert {tag.pk for tag in opinion.tags.all()} == {tag.pk for tag in tags}


def test_the_same_keyword_cannot_be_assigned_twice(opinion, tags):
    """The database refuses it, not only the service.

    A duplicate assignment is not a second classification, and a `.create()` in a
    shell or a future importer must hit the same wall the form does.
    """
    from django.db import IntegrityError

    SubmissionTagAssignment.objects.create(submission=opinion, tag=tags[0])
    with pytest.raises(IntegrityError):
        SubmissionTagAssignment.objects.create(submission=opinion, tag=tags[0])


def test_the_matters_own_tags_do_not_appear_on_its_opinion(
    normal_matter, opinion, tags, specialist
):
    """The decision this whole record turns on (docs/adr/0093 §1).

    The Matter is classified; the opinion on it is not. Two facts about two
    records, and neither is derived from the other.
    """
    set_tags(matter=normal_matter, tags=tags, actor=specialist)

    assert list(normal_matter.tags.all()) != []
    assert list(opinion.tags.all()) == []


def test_classifying_an_opinion_does_not_reclassify_its_matter(
    normal_matter, opinion, tags, specialist
):
    """And the inverse, which is the half a one-directional test would miss."""
    set_submission_tags(submission=opinion, tags=[tags[0]], actor=specialist)

    assert list(normal_matter.tags.all()) == []


def test_an_existing_opinion_receives_no_keyword_from_the_migration(opinion, tags, specialist):
    """No backfill: a letter filed before this shipped carries nothing.

    Proved the only way a test can prove an absence here — by creating exactly
    the situation a backfill would have acted on (a Matter with tags, an opinion
    on it) and asserting the opinion is still empty.
    """
    set_tags(matter=opinion.matter, tags=tags, actor=specialist)
    opinion.refresh_from_db()

    assert SubmissionTagAssignment.objects.filter(submission=opinion).count() == 0


def test_free_text_cannot_become_a_keyword(signed_in, specialist, opinion, tags):
    """The form validates against the governed vocabulary, not against a string.

    A posted word that is not a `Tag` identity is refused, and — the assertion
    that matters — no `Tag` is created to make it valid.
    """
    from app.taxonomy.models import Tag

    before = Tag.objects.count()

    response = signed_in.post(
        _metadata_url(opinion), {"tags": ["kliimapakett"], "website_overviews": []}
    )

    assert response.status_code == 400
    assert Tag.objects.count() == before
    assert list(opinion.tags.all()) == []


def test_only_active_tags_are_offered_for_a_new_assignment(opinion, tags):
    """`is_active` is the working vocabulary, exactly as it is for a Valdkond."""
    tags[2].is_active = False
    tags[2].save(update_fields=["is_active"])

    offered = set(selectable_tags(opinion).values_list("pk", flat=True))

    assert offered == {tags[0].pk, tags[1].pk}


def test_a_merged_tag_files_the_assignment_against_its_canonical(opinion, tags, specialist):
    """The taxonomy's own convention, followed rather than reimplemented.

    `Tag.canonical()` is what «a deprecated spelling stays searchable through the
    canonical tag» means, and an assignment made through a merged spelling lands
    on the tag that carries assignments today.
    """
    tags[1].merged_into = tags[0]
    tags[1].is_active = False
    tags[1].save(update_fields=["merged_into", "is_active"])

    set_submission_tags(submission=opinion, tags=[tags[1]], actor=specialist)

    assert [tag.pk for tag in opinion.tags.all()] == [tags[0].pk]


def test_a_retired_tag_cannot_be_newly_assigned(opinion, tags, specialist):
    """Deprecated without a successor is refused rather than silently dropped."""
    tags[0].is_active = False
    tags[0].save(update_fields=["is_active"])

    with pytest.raises(DomainError):
        set_submission_tags(submission=opinion, tags=[tags[0]], actor=specialist)

    assert list(opinion.tags.all()) == []


def test_an_assignment_survives_the_tag_being_retired_afterwards(opinion, tags, specialist):
    """A historical classification does not disappear because a word was retired.

    Both halves: the assignment is still there, and the tag is still *offered* —
    which is what stops the next save from quietly removing it because the form
    could not render a ticked box for it (docs/adr/0093 §1).
    """
    set_submission_tags(submission=opinion, tags=[tags[0]], actor=specialist)
    tags[0].is_active = False
    tags[0].save(update_fields=["is_active"])

    assert [tag.pk for tag in opinion.tags.all()] == [tags[0].pk]
    assert tags[0].pk in set(selectable_tags(opinion).values_list("pk", flat=True))

    # And re-saving the form as it renders keeps it, rather than diffing it away.
    set_submission_tags(submission=opinion, tags=[tags[0]], actor=specialist)
    assert [tag.pk for tag in opinion.tags.all()] == [tags[0].pk]


def test_removing_a_keyword_removes_only_the_assignment(opinion, tags, specialist):
    """The `Tag` is governed vocabulary and is never deleted by an edit here."""
    from app.taxonomy.models import Tag

    set_submission_tags(submission=opinion, tags=[tags[0], tags[1]], actor=specialist)
    set_submission_tags(submission=opinion, tags=[tags[1]], actor=specialist)

    assert [tag.pk for tag in opinion.tags.all()] == [tags[1].pk]
    assert Tag.objects.filter(pk=tags[0].pk).exists()


# ---------------------------------------------------------------------------
# 11–20 · The relation: many-to-many, same Matter, nothing inferred
# ---------------------------------------------------------------------------


def test_an_opinion_can_be_linked_to_no_overview(opinion):
    assert list(opinion.website_overviews.all()) == []


def test_one_opinion_can_be_linked_to_several_overviews(opinion, planned, published, specialist):
    """A letter written up twice — an overview for members and a news item."""
    set_submission_website_overviews(
        submission=opinion, overviews=[planned, published], actor=specialist
    )

    assert {row.pk for row in opinion.website_overviews.all()} == {planned.pk, published.pk}


def test_one_overview_can_be_linked_to_several_opinions(normal_matter, published, specialist):
    """A single write-up covering the initial opinion and the supplementary one."""
    first = factories.SubmissionFactory(matter=normal_matter, title="Esialgne arvamus")
    second = factories.SubmissionFactory(matter=normal_matter, title="Täiendav arvamus")

    set_submission_website_overviews(submission=first, overviews=[published], actor=specialist)
    set_submission_website_overviews(submission=second, overviews=[published], actor=specialist)

    assert {row.pk for row in published.submissions.all()} == {first.pk, second.pk}


def test_the_same_pair_cannot_be_linked_twice(opinion, published):
    from django.db import IntegrityError

    SubmissionWebsiteOverviewLink.objects.create(submission=opinion, website_overview=published)
    with pytest.raises(IntegrityError):
        SubmissionWebsiteOverviewLink.objects.create(submission=opinion, website_overview=published)


def test_an_overview_on_another_matter_is_refused(opinion, specialist, published):
    """The same-Matter invariant, at the boundary that decides."""
    other_matter = factories.MatterFactory(owner=specialist)
    foreign = publish_website_overview(
        overview=plan_website_overview(matter=other_matter, actor=specialist),
        url="https://koda.ee/uudised/teine-teema",
        published_on=timezone.localdate(),
        actor=specialist,
    )

    with pytest.raises(DomainError):
        set_submission_website_overviews(submission=opinion, overviews=[foreign], actor=specialist)

    assert list(opinion.website_overviews.all()) == []


def test_a_foreign_overview_refuses_the_whole_save(opinion, specialist, published):
    """Atomic: the good half is rolled back with the bad one.

    The failure this guards against is the one a partial write produces — the
    person's keywords saved, their links not, and no way to tell from the screen
    which of the two happened.
    """
    other_matter = factories.MatterFactory(owner=specialist)
    foreign = plan_website_overview(matter=other_matter, actor=specialist)

    with pytest.raises(DomainError):
        set_submission_website_overviews(
            submission=opinion, overviews=[published, foreign], actor=specialist
        )

    assert SubmissionWebsiteOverviewLink.objects.filter(submission=opinion).count() == 0


def test_the_model_refuses_a_cross_matter_row_in_clean(opinion, specialist):
    """The second guard, for the admin and for anything built outside the service."""
    from django.core.exceptions import ValidationError

    other_matter = factories.MatterFactory(owner=specialist)
    foreign = plan_website_overview(matter=other_matter, actor=specialist)

    link = SubmissionWebsiteOverviewLink(submission=opinion, website_overview=foreign)
    with pytest.raises(ValidationError):
        link.clean()


def test_nothing_is_inferred_from_a_matching_url_title_or_date(normal_matter, specialist):
    """The coincidence a matcher would have fallen for, and no link appears.

    Same words in the title and the address, the same day on both records, and
    the overview created moments after the send. This is the strongest hint any
    inference rule could ask for, which is exactly why the assertion is zero.
    """
    today = timezone.localdate()
    submission = factories.SubmissionFactory(
        matter=normal_matter, title="Koja arvamus pakendiseaduse eelnõule"
    )
    publish_website_overview(
        overview=plan_website_overview(matter=normal_matter, actor=specialist),
        url="https://koda.ee/uudised/koja-arvamus-pakendiseaduse-eelnoule",
        published_on=today,
        actor=specialist,
    )

    assert SubmissionWebsiteOverviewLink.objects.count() == 0
    assert list(submission.website_overviews.all()) == []


def test_nothing_is_inferred_from_a_shared_tag(normal_matter, opinion, tags, specialist):
    """Nor from the file's classification lining up with the letter's."""
    set_tags(matter=normal_matter, tags=[tags[0]], actor=specialist)
    set_submission_tags(submission=opinion, tags=[tags[0]], actor=specialist)
    plan_website_overview(matter=normal_matter, actor=specialist)

    assert SubmissionWebsiteOverviewLink.objects.count() == 0


def test_unlinking_deletes_neither_endpoint(opinion, published, specialist):
    from app.matters.models import MatterWebsiteOverview

    set_submission_website_overviews(submission=opinion, overviews=[published], actor=specialist)
    set_submission_website_overviews(submission=opinion, overviews=[], actor=specialist)

    assert Submission.objects.filter(pk=opinion.pk).exists()
    assert MatterWebsiteOverview.objects.filter(pk=published.pk).exists()
    assert SubmissionWebsiteOverviewLink.objects.count() == 0


def test_a_planned_overview_links_without_an_invented_url_or_date(opinion, planned, specialist):
    """`Plaanis` participates, and stays exactly as planned as it was.

    Linking is not publishing: no address is invented for a row that has none,
    no date is stamped on it, and its status does not move (docs/adr/0093 §2).
    """
    set_submission_website_overviews(submission=opinion, overviews=[planned], actor=specialist)
    planned.refresh_from_db()

    assert [row.pk for row in opinion.website_overviews.all()] == [planned.pk]
    assert planned.status == WebsiteOverviewStatus.PLANNED
    assert planned.url == ""
    assert planned.published_on is None


def test_a_cancelled_overview_can_still_participate(opinion, normal_matter, specialist):
    """A dropped plan is a real record, and no new status rule is invented."""
    cancelled = cancel_website_overview(
        overview=plan_website_overview(matter=normal_matter, actor=specialist), actor=specialist
    )

    set_submission_website_overviews(submission=opinion, overviews=[cancelled], actor=specialist)

    assert [row.pk for row in opinion.website_overviews.all()] == [cancelled.pk]


def test_editing_the_relation_leaves_the_submission_untouched(
    normal_matter, published, specialist, organisation, pdf_bytes, capture_evidence
):
    """Status, `sent_at`, recipients and immutable evidence are all unmoved."""
    from app.submissions.services import (
        attach_final_evidence,
        mark_submission_sent,
        set_recipients,
    )

    submission = factories.SubmissionFactory(matter=normal_matter, title="Saadetud arvamus")
    attach_final_evidence(
        submission=submission,
        content=pdf_bytes,
        original_filename="arvamus.pdf",
        mime_type="application/pdf",
        actor=specialist,
    )
    set_recipients(submission=submission, addressees=[organisation], actor=specialist)
    mark_submission_sent(submission=submission, actor=specialist, channel="EIS")
    submission.refresh_from_db()
    before = (
        submission.status,
        submission.sent_at,
        submission.final_version_id,
        {row.organisation_id for row in submission.recipient_rows.all()},
    )

    set_submission_website_overviews(submission=submission, overviews=[published], actor=specialist)
    set_submission_website_overviews(submission=submission, overviews=[], actor=specialist)
    submission.refresh_from_db()

    assert (
        submission.status,
        submission.sent_at,
        submission.final_version_id,
        {row.organisation_id for row in submission.recipient_rows.all()},
    ) == before


def test_editing_the_relation_leaves_the_overview_untouched(opinion, published, specialist):
    before = (published.status, published.url, published.published_on, published.published_at)

    set_submission_website_overviews(submission=opinion, overviews=[published], actor=specialist)
    set_submission_website_overviews(submission=opinion, overviews=[], actor=specialist)
    published.refresh_from_db()

    assert (
        published.status,
        published.url,
        published.published_on,
        published.published_at,
    ) == before


# ---------------------------------------------------------------------------
# 21–24 · The audit trail
# ---------------------------------------------------------------------------


def _events(submission, *types):
    return ChangeEvent.objects.filter(
        matter=submission.matter, event_type__in=[str(kind) for kind in types]
    )


def test_adding_and_removing_a_keyword_is_audited(opinion, tags, specialist):
    """One row per fact, exactly as `set_tags` has always written for a Matter."""
    set_submission_tags(submission=opinion, tags=[tags[0], tags[1]], actor=specialist)
    set_submission_tags(submission=opinion, tags=[tags[1], tags[2]], actor=specialist)

    added = _events(opinion, ChangeEventType.SUBMISSION_TAG_ASSIGNED)
    removed = _events(opinion, ChangeEventType.SUBMISSION_TAG_REMOVED)

    assert added.count() == 3
    assert removed.count() == 1
    assert removed.first().summary == tags[0].name_et


def test_linking_and_unlinking_an_overview_is_audited(opinion, planned, published, specialist):
    set_submission_website_overviews(
        submission=opinion, overviews=[planned, published], actor=specialist
    )
    set_submission_website_overviews(submission=opinion, overviews=[published], actor=specialist)

    assert _events(opinion, ChangeEventType.SUBMISSION_OVERVIEW_LINKED).count() == 2
    unlinked = _events(opinion, ChangeEventType.SUBMISSION_OVERVIEW_UNLINKED)
    assert unlinked.count() == 1
    assert unlinked.first().payload["overview"] == str(planned.pk)


def test_a_save_that_changes_nothing_writes_no_event(opinion, tags, published, specialist):
    """«Somebody opened this» must not read the same as «somebody changed this»."""
    set_submission_tags(submission=opinion, tags=[tags[0]], actor=specialist)
    set_submission_website_overviews(submission=opinion, overviews=[published], actor=specialist)
    before = ChangeEvent.objects.count()

    set_submission_tags(submission=opinion, tags=[tags[0]], actor=specialist)
    set_submission_website_overviews(submission=opinion, overviews=[published], actor=specialist)

    assert ChangeEvent.objects.count() == before


def test_no_audit_payload_carries_the_letters_own_text(normal_matter, tags, published, specialist):
    """Identities and vocabulary names, never content.

    `notes` is this office's professional working text and the sent file's bytes
    are evidence; neither belongs in an audit payload, which is the record a
    later reader reconstructs events from (docs/adr/0093 §3).
    """
    private_note = "Ministeeriumi põhjendus ei arvesta liikmete kulumõjuga."
    submission = factories.SubmissionFactory(
        matter=normal_matter, title="Koja arvamus", notes=private_note
    )

    set_submission_tags(submission=submission, tags=[tags[0]], actor=specialist)
    set_submission_website_overviews(submission=submission, overviews=[published], actor=specialist)

    rows = _events(
        submission,
        ChangeEventType.SUBMISSION_TAG_ASSIGNED,
        ChangeEventType.SUBMISSION_OVERVIEW_LINKED,
    )
    assert rows.count() == 2
    for event in rows:
        assert private_note not in str(event.payload)
        assert private_note not in event.summary


def test_the_new_events_stay_out_of_the_professional_chronology():
    """Classifying a letter is data management, not authored history.

    The same rule `TAG_ASSIGNED` follows, asserted here because the list is
    hand-kept and a fifth type added later would be easy to add to it by reflex.
    """
    from app.matters.timeline import TIMELINE_EVENT_TYPES

    for kind in (
        ChangeEventType.SUBMISSION_TAG_ASSIGNED,
        ChangeEventType.SUBMISSION_TAG_REMOVED,
        ChangeEventType.SUBMISSION_OVERVIEW_LINKED,
        ChangeEventType.SUBMISSION_OVERVIEW_UNLINKED,
    ):
        assert kind not in TIMELINE_EVENT_TYPES


# ---------------------------------------------------------------------------
# 25–30 · Visibility: the relation grants nothing
# ---------------------------------------------------------------------------


@pytest.fixture
def restricted_overview(normal_matter, specialist):
    """An `Ülevaade / uudis` restricted below a NORMAL Matter.

    The child-visibility case this product has always allowed and the one the
    relation must not widen: the file is department-wide, this row is not.
    """
    overview = plan_website_overview(matter=normal_matter, actor=specialist)
    overview.visibility_override = Visibility.RESTRICTED
    overview.save(update_fields=["visibility_override"])
    return overview


def test_a_restricted_overview_is_not_offered_to_a_reader_who_may_not_see_it(
    opinion, restricted_overview, published, reader
):
    """Not offered, not counted, not named."""
    offered = set(selectable_website_overviews(opinion, viewer=reader).values_list("pk", flat=True))

    assert restricted_overview.pk not in offered
    assert published.pk in offered


def test_a_restricted_overview_does_not_leak_through_a_visible_opinion(
    opinion, restricted_overview, specialist, reader
):
    """A reader of the letter is not told which write-ups they may not open."""
    set_submission_website_overviews(
        submission=opinion, overviews=[restricted_overview], actor=specialist
    )

    assert [row.pk for row in linked_website_overviews(opinion, viewer=specialist)] == [
        restricted_overview.pk
    ]
    assert linked_website_overviews(opinion, viewer=reader) == []


def test_a_restricted_opinion_does_not_leak_through_a_visible_overview(
    normal_matter, published, specialist, reader
):
    """And the other direction, which is the half a one-sided test would miss."""
    hidden = factories.SubmissionFactory(
        matter=normal_matter,
        title="Piiratud arvamus",
        visibility_override=Visibility.RESTRICTED,
    )
    set_submission_website_overviews(submission=hidden, overviews=[published], actor=specialist)

    assert linked_submissions_by_overview(normal_matter, user=specialist)[published.pk]
    assert linked_submissions_by_overview(normal_matter, user=reader) == {}


def test_a_guessed_identifier_cannot_link_a_hidden_overview(
    client, opinion, restricted_overview, reader
):
    """A crafted POST naming a record the actor may not see reaches nothing.

    The form's choices are a usability gate; the queryset behind them is the
    authorization one, and a browser posts whatever it likes.
    """
    client.force_login(reader)
    response = client.post(
        _metadata_url(opinion),
        {"tags": [], "website_overviews": [str(restricted_overview.pk)]},
    )

    # 404 rather than 403: `business_write_required` refuses a READER before the
    # form is ever built, and a reader who may not write is not told which
    # surfaces exist for those who may.
    assert response.status_code == 404
    assert SubmissionWebsiteOverviewLink.objects.count() == 0


def test_a_department_head_who_can_see_the_overview_may_link_it(
    client, opinion, restricted_overview, department_head
):
    """The other side of the boundary, asserted rather than assumed.

    Since docs/adr/0042 both lawyer roles read RESTRICTED children, so a
    `DEPARTMENT_HEAD` is *inside* the scope this relation is bounded by and the
    save must succeed. It is written down so that a future narrowing of the role
    shows up here as a change in behaviour rather than as a test that quietly
    goes on passing because it only ever asserted a refusal.
    """
    client.force_login(department_head)
    response = client.post(
        _metadata_url(opinion),
        {"tags": [], "website_overviews": [str(restricted_overview.pk)]},
    )

    assert response.status_code == 302
    assert SubmissionWebsiteOverviewLink.objects.count() == 1


def test_a_cross_matter_identifier_is_refused_through_the_form(signed_in, specialist, opinion):
    """The candidate queryset is scoped to the Matter, so this never validates."""
    other_matter = factories.MatterFactory(owner=specialist)
    foreign = plan_website_overview(matter=other_matter, actor=specialist)

    response = signed_in.post(
        _metadata_url(opinion), {"tags": [], "website_overviews": [str(foreign.pk)]}
    )

    assert response.status_code == 400
    assert SubmissionWebsiteOverviewLink.objects.count() == 0


def test_an_actor_cannot_unlink_what_they_were_never_shown(
    normal_matter, opinion, restricted_overview, published, specialist, department_head
):
    """The rule that keeps a scoped form from destroying an unscoped fact.

    A restricted overview is linked by somebody who can see it. A colleague who
    cannot opens the form — which shows them one ticked box, not two — and saves.
    The link they could not see must still be there afterwards.
    """
    set_submission_website_overviews(
        submission=opinion, overviews=[restricted_overview, published], actor=specialist
    )
    # Somebody whose scope excludes the restricted row. The sentinel department
    # viewer is exactly that reader: past the shared gate, no persona, NORMAL
    # visibility and no participation (`app.core.authorization`).
    from app.core.authorization import DEPARTMENT_VIEWER

    set_submission_website_overviews(
        submission=opinion, overviews=[published], actor=DEPARTMENT_VIEWER
    )

    assert {row.pk for row in opinion.website_overviews.all()} == {
        restricted_overview.pk,
        published.pk,
    }


# ---------------------------------------------------------------------------
# 31–38 · The surfaces
# ---------------------------------------------------------------------------


def test_the_metadata_page_renders_both_controls(signed_in, opinion, tags, planned):
    response = signed_in.get(_metadata_url(opinion))
    body = response.content.decode()

    assert response.status_code == 200
    assert "Märksõnad" in body
    assert "Seotud ülevaated / uudised" in body
    assert tags[0].name_et in body


def test_the_matters_tags_are_not_preselected_on_the_form(
    signed_in, normal_matter, opinion, tags, specialist
):
    """The file's classification is not ticked on the letter's form.

    Checked on the *bound* initial rather than on the HTML, because a checked
    attribute is what a template happens to render and the initial is what the
    save would act on.
    """
    set_tags(matter=normal_matter, tags=tags, actor=specialist)

    response = signed_in.get(_metadata_url(opinion))

    assert list(response.context["form"].initial["tags"]) == []


def test_several_keywords_can_be_saved_and_then_corrected(signed_in, opinion, tags):
    signed_in.post(
        _metadata_url(opinion),
        {"tags": [str(tags[0].pk), str(tags[1].pk)], "website_overviews": []},
    )
    assert {tag.pk for tag in opinion.tags.all()} == {tags[0].pk, tags[1].pk}

    signed_in.post(
        _metadata_url(opinion),
        {"tags": [str(tags[2].pk)], "website_overviews": []},
    )
    assert {tag.pk for tag in opinion.tags.all()} == {tags[2].pk}


def test_several_overviews_can_be_saved_through_the_form(signed_in, opinion, planned, published):
    signed_in.post(
        _metadata_url(opinion),
        {"tags": [], "website_overviews": [str(planned.pk), str(published.pk)]},
    )

    assert {row.pk for row in opinion.website_overviews.all()} == {planned.pk, published.pk}


def test_a_saved_relation_renders_on_the_opinions_own_row(
    signed_in, normal_matter, specialist, published, tags, pdf_bytes, capture_evidence
):
    """`Dokumendid` shows the letter's keywords and write-ups behind its `⋯`."""
    from app.submissions.services import attach_final_evidence, mark_submission_sent

    submission = factories.SubmissionFactory(matter=normal_matter, title="Saadetud arvamus")
    attach_final_evidence(
        submission=submission,
        content=pdf_bytes,
        original_filename="arvamus.pdf",
        mime_type="application/pdf",
        actor=specialist,
    )
    mark_submission_sent(submission=submission, actor=specialist, channel="EIS")
    set_submission_tags(submission=submission, tags=[tags[0]], actor=specialist)
    set_submission_website_overviews(submission=submission, overviews=[published], actor=specialist)

    response = signed_in.get(reverse("matters:matter_documents", kwargs={"pk": normal_matter.pk}))
    body = response.content.decode()

    assert tags[0].name_et in body
    assert "Seotud ülevaated / uudised" in body
    assert published.url in body


def test_the_reciprocal_list_filters_visibility(client, normal_matter, planned, specialist):
    """An opinion's title reaches the strip only for a reader who may see it."""
    hidden = factories.SubmissionFactory(
        matter=normal_matter,
        title="Piiratud saadetud arvamus",
        visibility_override=Visibility.RESTRICTED,
    )
    set_submission_website_overviews(submission=hidden, overviews=[planned], actor=specialist)

    client.force_login(specialist)
    mine = client.get(reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}))
    assert hidden.title in mine.content.decode()

    client.force_login(factories.ReaderFactory())
    theirs = client.get(reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}))
    assert hidden.title not in theirs.content.decode()


def test_no_relation_appears_without_somebody_making_one(
    signed_in, normal_matter, planned, specialist
):
    """The strip says nothing about opinions until a link exists."""
    factories.SubmissionFactory(matter=normal_matter, title="Sidumata arvamus")

    response = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}))

    assert "Seotud arvamused" not in response.content.decode()


def test_the_send_workflow_is_unchanged(
    signed_in, normal_matter, specialist, organisation, pdf_bytes, capture_evidence
):
    """The four-question send still works exactly as it did.

    The regression this guards: a metadata surface that reached into the send
    would break the one act this whole module exists to protect.
    """
    from app.submissions.services import attach_final_evidence, mark_submission_sent

    submission = factories.SubmissionFactory(matter=normal_matter, title="Koja arvamus")
    attach_final_evidence(
        submission=submission,
        content=pdf_bytes,
        original_filename="arvamus.pdf",
        mime_type="application/pdf",
        actor=specialist,
    )
    mark_submission_sent(submission=submission, actor=specialist, channel="EIS")
    submission.refresh_from_db()

    assert submission.status == SubmissionStatus.SENT
    assert submission.sent_at is not None
    assert submission.final_version_id is not None


def test_the_metadata_surface_cannot_change_anything_else(signed_in, opinion, tags):
    """A posted title, status or date is not a field this form has.

    The assertion is on the record, not on the form: extra keys in a POST are
    simply not read, and the proof is that nothing moved.
    """
    before = (opinion.title, opinion.status, opinion.kind, opinion.channel)

    signed_in.post(
        _metadata_url(opinion),
        {
            "tags": [str(tags[0].pk)],
            "website_overviews": [],
            "title": "Ümbernimetatud",
            "status": SubmissionStatus.SENT,
            "kind": "OTHER",
            "channel": "e-post",
        },
    )
    opinion.refresh_from_db()

    assert (opinion.title, opinion.status, opinion.kind, opinion.channel) == before


def test_a_closed_matter_still_accepts_a_metadata_correction(signed_in, specialist, tags):
    """The existing contract, not an exception invented for this surface.

    `Muuda teemat` writes a Matter's own `Sildid` on a closed file and `Võta
    tagasi` corrects a recorded send on one. Classifying a record that already
    exists is a correction, not new canonical business content
    (docs/adr/0093 §3, docs/adr/0075 §12).
    """
    from app.matters.services import close_matter, create_matter

    matter = create_matter(title="Suletud teema", actor=specialist, owner=specialist)
    submission = factories.SubmissionFactory(matter=matter, title="Vana arvamus")
    close_matter(matter=matter, actor=specialist, disposition="COMPLETED")

    response = signed_in.post(
        _metadata_url(submission), {"tags": [str(tags[0].pk)], "website_overviews": []}
    )

    assert response.status_code == 302
    assert [tag.pk for tag in submission.tags.all()] == [tags[0].pk]


def test_an_opinion_on_another_matter_is_not_reachable(client, reader, opinion):
    """Visibility first: the page is a 404 for anybody who may not write."""
    client.force_login(reader)

    assert client.get(_metadata_url(opinion)).status_code == 404


# ---------------------------------------------------------------------------
# 39–48 · Regression: what this round must not have touched
# ---------------------------------------------------------------------------


def test_the_search_index_version_did_not_move():
    """No child row carries taxonomy, so no rebuild follows this release.

    Pinned as a literal rather than compared to itself, because the decision
    docs/adr/0093 §5 records is that this number does **not** change — and a test
    that compared the constant to itself would pass however it moved.
    """
    from app.legacy_import.opinion_search_models import ARCHIVE_INDEX_VERSION
    from app.search.models import INDEX_VERSION

    assert INDEX_VERSION == "AUTH003.1"
    assert ARCHIVE_INDEX_VERSION == "1"


def test_a_keyword_is_not_projected_into_the_search_corpus(opinion, tags, specialist):
    """The decision, asserted on the corpus rather than on the version number."""
    from app.search.indexing import reindex_submission
    from app.search.models import SearchDocument, SearchSourceKind

    set_submission_tags(submission=opinion, tags=[tags[0]], actor=specialist)
    reindex_submission(opinion)

    row = SearchDocument.objects.filter(
        source_kind=SearchSourceKind.SUBMISSION, source_object_id=opinion.pk
    ).first()
    assert row is not None
    assert tags[0].name_et not in row.alias_text
    assert tags[0].name_et not in row.body_text


def test_the_overview_lifecycle_is_unchanged(normal_matter, specialist, opinion):
    """Plan, publish, correct and cancel all behave exactly as before."""
    from app.matters.services import correct_website_overview_link

    overview = plan_website_overview(matter=normal_matter, actor=specialist)
    assert overview.status == WebsiteOverviewStatus.PLANNED

    set_submission_website_overviews(submission=opinion, overviews=[overview], actor=specialist)

    publish_website_overview(
        overview=overview,
        url="https://koda.ee/uudised/esimene",
        published_on=timezone.localdate() - timedelta(days=1),
        actor=specialist,
    )
    correct_website_overview_link(
        overview=overview,
        url="https://koda.ee/uudised/parandatud",
        published_on=None,
        actor=specialist,
    )
    overview.refresh_from_db()

    assert overview.status == WebsiteOverviewStatus.PUBLISHED
    assert overview.url == "https://koda.ee/uudised/parandatud"
    assert overview.published_on is None
    # And the link survived every one of those transitions untouched.
    assert [row.pk for row in opinion.website_overviews.all()] == [overview.pk]


def test_closing_a_matter_still_cancels_its_planned_overviews(
    normal_matter, specialist, opinion, planned
):
    """Closure's own rule is unchanged, link or no link."""
    from app.matters.services import close_matter

    set_submission_website_overviews(submission=opinion, overviews=[planned], actor=specialist)
    close_matter(matter=normal_matter, actor=specialist, disposition="COMPLETED")
    planned.refresh_from_db()

    assert planned.status == WebsiteOverviewStatus.CANCELLED
    assert [row.pk for row in opinion.website_overviews.all()] == [planned.pk]
