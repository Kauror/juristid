"""What a projection may show is bounded by what its source would show.

AUTH-002 answered "may this actor write". This answers a different question, and
one that a correct write boundary does nothing for: a reader who is properly
refused a restricted child can still learn its contents if some *other* surface
was built from an unscoped population.

The invariant:

    viewer sees a projection of child X
        IFF
    viewer could see X at its authoritative source

A Matter being visible is never sufficient. `Entry`, `Submission`, `Document`,
`NextAction`, `MatterEngagement` and the three `MatterFact` kinds are all
`VisibilityInheritingModel`s: each carries a `visibility_override` that can make
it stricter than the Matter it hangs off. Any surface that walks `.all()` on one
of those relations and prints, indexes or counts the result has widened
visibility without anybody deciding to.

The confirmed defect this file was written for is search. `_engagement_text_for`
walked `matter.engagements.all()` and concatenated every `Kaasamine` title and
note into the **MATTER** row's tsvector — and a MATTER row is authorized by the
Matter alone (`SOURCE_OVERRIDE_FIELDS` maps it to `None`). A RESTRICTED
consultation on a NORMAL Matter was therefore searchable by the whole
department.

Two things had to be true to close it, and only one of them is about new rows:

**New rows.** `Kaasamine` becomes its own `SearchSourceKind`, joined live, so its
current override participates in the query exactly as `Entry`'s does.

**Old rows.** A row indexed before the fix still has the restricted words inside
its stored vector, and no predicate can take them back out — visibility
filtering decides whether a row is returned, never what is in one. So the query
chokepoint refuses any row not carrying the current `INDEX_VERSION`. That makes
every pre-fix row ineligible the moment the code is deployed, before any
rebuild. `test_a_contaminated_pre_fix_row_is_not_searchable_without_a_rebuild`
is the load-bearing proof, and it is written to fail if that gate is removed.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.postgres.search import SearchQuery
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.documents.enums import DocumentRole
from app.matters.enums import EngagementKind
from app.matters.services import add_engagement
from app.search.indexing import (
    _recompute_vectors,
    indexable_matters,
    rebuild_all,
    refresh_matters,
)
from app.search.models import INDEX_VERSION, SearchDocument, SearchSourceKind
from app.search.services import search_documents, search_matters
from tests import factories

pytestmark = pytest.mark.django_db

#: Distinctive enough that a match can only have come from the restricted child.
HIDDEN = "SALAJANE-KAASAMINE-987"
PUBLIC_TITLE = "Harilik teema"


def _found(query: str, user) -> list[tuple[str, object]]:
    """What this reader gets from the *whole* corpus, by kind.

    `search_documents`, not `search_matters`. The latter filters to
    `source_kind=MATTER` for the navigation shortcut, so asking it whether a
    restricted `Kaasamine` is visible would answer "no" whatever authorization
    did — a test that passes because the row was never a candidate proves
    nothing about whether it was refused.
    """
    return [(row.source_kind, row.matter_id) for row in search_documents(query=query, user=user)]


def _matter(owner, **kwargs):
    return factories.MatterFactory(
        owner=owner,
        title=kwargs.pop("title", PUBLIC_TITLE),
        reference_year=2099,
        reference_number=kwargs.pop("number", 501),
        **kwargs,
    )


def _restricted_engagement(matter, actor, *, title: str, note: str = ""):
    engagement = add_engagement(
        matter=matter,
        kind=EngagementKind.WEB_CALL,
        title=title,
        note=note,
        occurred_on=timezone.localdate(),
        actor=actor,
    )
    engagement.visibility_override = Visibility.RESTRICTED
    engagement.save(update_fields=["visibility_override"])
    return engagement


# ---------------------------------------------------------------------------
# The stale-row gate — the part that has to hold before any rebuild
# ---------------------------------------------------------------------------


def test_a_contaminated_pre_fix_row_is_not_searchable_without_a_rebuild(specialist):
    """A row built under the old contract cannot leak, even unrebuilt.

    The sequence is the whole point, so it is spelled out rather than helper'd:
    build a row that really does contain the restricted phrase in its stored
    vector — exactly what production holds today — then run the *new* query code
    against it without reindexing, and require silence.

    If this test starts passing only after a rebuild, the security boundary is
    incomplete and the verdict changes.
    """
    stranger = factories.ReaderFactory()  # not a lawyer: docs/adr/0042
    matter = _matter(specialist)
    _restricted_engagement(matter, specialist, title=HIDDEN, note=f"{HIDDEN} märkus")

    # -- A: forge the pre-fix state -------------------------------------
    #
    # Index normally, then put the engagement text back into the MATTER row and
    # stamp it with the old version, which is byte-for-byte the row the previous
    # indexer wrote.
    refresh_matters(indexable_matters().filter(pk=matter.pk))
    contaminated = SearchDocument.objects.get(matter=matter, source_kind=SearchSourceKind.MATTER)
    contaminated.body_text = f"{contaminated.body_text} {HIDDEN} {HIDDEN} märkus".strip()
    contaminated.index_version = "2D.1"
    contaminated.save(update_fields=["body_text", "index_version"])
    _recompute_vectors(SearchDocument.objects.filter(pk=contaminated.pk))

    # The phrase really is in the stored vector. Without this the rest of the
    # test could pass because nothing was ever indexed.
    assert SearchDocument.objects.filter(
        pk=contaminated.pk,
        search_simple=SearchQuery(HIDDEN, config="simple"),
    ).exists(), "the forged row does not actually contain the phrase"

    # -- B/C: new code, no rebuild --------------------------------------
    # -- D: an unauthorized reader searches the phrase -------------------
    results = search_matters(query=HIDDEN, user=stranger)

    assert results == [], f"a pre-fix row leaked: {[r.matter.title for r in results]}"


def test_the_stale_gate_is_what_stops_it(specialist):
    """The gate is load-bearing, not decorative.

    Same forged row, but stamped with the *current* version — which is what a
    row would look like if somebody removed the version filter and rebuilt. The
    phrase becomes findable, which is precisely the failure the filter prevents,
    and asserting it here means a future change that drops the filter breaks a
    test that explains itself rather than one that merely goes red.
    """
    stranger = factories.ReaderFactory()  # not a lawyer: docs/adr/0042
    matter = _matter(specialist, number=502)
    _restricted_engagement(matter, specialist, title=HIDDEN, note="")

    refresh_matters(indexable_matters().filter(pk=matter.pk))
    row = SearchDocument.objects.get(matter=matter, source_kind=SearchSourceKind.MATTER)
    row.body_text = f"{row.body_text} {HIDDEN}".strip()
    row.index_version = INDEX_VERSION
    row.save(update_fields=["body_text", "index_version"])
    _recompute_vectors(SearchDocument.objects.filter(pk=row.pk))

    results = search_matters(query=HIDDEN, user=stranger)

    assert results, (
        "a MATTER row carrying the phrase at the current version is findable — "
        "which is why the pre-fix version must be refused instead"
    )


def test_rows_at_the_current_version_are_the_only_ones_read(specialist):
    """The gate is a version equality, not a guess about which versions are old."""
    matter = _matter(specialist, number=503)
    refresh_matters(indexable_matters().filter(pk=matter.pk))

    assert SearchDocument.objects.filter(index_version=INDEX_VERSION).exists()

    SearchDocument.objects.filter(matter=matter).update(index_version="SOMETHING-ELSE")

    assert search_matters(query="Harilik", user=specialist) == []


# ---------------------------------------------------------------------------
# After a rebuild — the projection is correct as well as safe
# ---------------------------------------------------------------------------


def test_a_rebuilt_matter_row_no_longer_carries_engagement_text(specialist):
    """The MATTER row may only hold what the Matter's own visibility governs."""
    matter = _matter(specialist, number=504)
    _restricted_engagement(matter, specialist, title=HIDDEN, note=f"{HIDDEN} märkus")

    rebuild_all()

    row = SearchDocument.objects.get(matter=matter, source_kind=SearchSourceKind.MATTER)
    assert HIDDEN not in row.title
    assert HIDDEN not in row.body_text
    assert HIDDEN not in row.alias_text
    assert row.index_version == INDEX_VERSION


def test_an_engagement_gets_its_own_row_joined_to_the_live_record(specialist):
    matter = _matter(specialist, number=505)
    engagement = _restricted_engagement(matter, specialist, title=HIDDEN, note="Märkus")

    rebuild_all()

    row = SearchDocument.objects.get(source_kind=SearchSourceKind.ENGAGEMENT)
    assert row.engagement_id == engagement.pk
    assert row.source_object_id == engagement.pk
    assert row.matter_id == matter.pk
    assert row.index_version == INDEX_VERSION
    assert HIDDEN in row.title


def test_a_restricted_engagement_is_invisible_to_a_stranger_after_rebuild(specialist):
    stranger = factories.ReaderFactory()  # not a lawyer: docs/adr/0042
    matter = _matter(specialist, number=506)
    _restricted_engagement(matter, specialist, title=HIDDEN, note=f"{HIDDEN} märkus")

    rebuild_all()

    assert _found(HIDDEN, stranger) == []
    # And the Matter itself is still perfectly findable by its own words.
    assert (SearchSourceKind.MATTER, matter.pk) in _found("Harilik", stranger)


def test_a_participant_can_still_find_their_own_restricted_engagement(specialist):
    """Scoping narrows who may read the row; it does not stop indexing it."""
    matter = _matter(specialist, number=507)
    _restricted_engagement(matter, specialist, title=HIDDEN, note="")

    rebuild_all()

    assert _found(HIDDEN, specialist) == [(SearchSourceKind.ENGAGEMENT, matter.pk)]


def test_the_department_head_still_reads_restricted_engagements(specialist, department_head):
    """Role-based restricted access is unchanged by this hardening."""
    matter = _matter(specialist, number=508)
    _restricted_engagement(matter, specialist, title=HIDDEN, note="")

    rebuild_all()

    assert _found(HIDDEN, department_head) == [(SearchSourceKind.ENGAGEMENT, matter.pk)]


def test_an_ordinary_engagement_stays_searchable_for_everybody(specialist):
    """The fix must not have made every Kaasamine private."""
    stranger = factories.ReaderFactory()  # not a lawyer: docs/adr/0042
    matter = _matter(specialist, number=509)
    add_engagement(
        matter=matter,
        kind=EngagementKind.WEB_CALL,
        title="AVALIK-KAASAMINE-123",
        note="Liikmete kaasamiskutse",
        occurred_on=timezone.localdate(),
        actor=specialist,
    )

    rebuild_all()

    assert _found("AVALIK-KAASAMINE-123", stranger) == [(SearchSourceKind.ENGAGEMENT, matter.pk)]


def test_restricting_an_engagement_takes_effect_without_reindexing(specialist):
    """Live derivation, not a stored flag.

    The whole reason the row joins the engagement rather than copying its state:
    restricting a consultation has to remove it from search on the next query,
    not on the next rebuild.
    """
    stranger = factories.ReaderFactory()  # not a lawyer: docs/adr/0042
    matter = _matter(specialist, number=510)
    engagement = add_engagement(
        matter=matter,
        kind=EngagementKind.WEB_CALL,
        title=HIDDEN,
        note="",
        occurred_on=timezone.localdate(),
        actor=specialist,
    )
    rebuild_all()
    assert _found(HIDDEN, stranger) == [(SearchSourceKind.ENGAGEMENT, matter.pk)]

    engagement.visibility_override = Visibility.RESTRICTED
    engagement.save(update_fields=["visibility_override"])

    assert _found(HIDDEN, stranger) == []


def test_restricting_the_matter_hides_its_engagement_row_too(specialist):
    """The child is bounded by its parent as well as by its own override."""
    stranger = factories.ReaderFactory()  # not a lawyer: docs/adr/0042
    matter = _matter(specialist, number=511)
    add_engagement(
        matter=matter,
        kind=EngagementKind.WEB_CALL,
        title=HIDDEN,
        note="",
        occurred_on=timezone.localdate(),
        actor=specialist,
    )
    rebuild_all()
    assert _found(HIDDEN, stranger) == [(SearchSourceKind.ENGAGEMENT, matter.pk)]

    matter.visibility = Visibility.RESTRICTED
    matter.save(update_fields=["visibility"])

    assert _found(HIDDEN, stranger) == []


# ---------------------------------------------------------------------------
# Snippets — a result may be allowed while its highlighted text is not
# ---------------------------------------------------------------------------


def test_no_snippet_ever_carries_restricted_engagement_text(client, specialist):
    """Rendered response, not queryset counts.

    A Matter a reader may open can still leak a child through `ts_headline`,
    which is why this asserts the page rather than the population.
    """
    stranger = factories.ReaderFactory()  # not a lawyer: docs/adr/0042
    matter = _matter(specialist, number=512)
    # A second phrase the reader never types, so its appearance anywhere on the
    # page can only have come from the restricted note. Asserting on HIDDEN
    # alone would be meaningless: the search box echoes the reader's own query.
    hidden_note = "SALAJANE-MARKUS-654"
    _restricted_engagement(matter, specialist, title=HIDDEN, note=f"{hidden_note} sisu")
    rebuild_all()
    client.force_login(stranger)

    response = client.get(reverse("search:search"), {"q": HIDDEN})
    body = response.content.decode()

    assert response.context["result_count"] == 0
    assert list(response.context["rows"]) == []
    assert hidden_note not in body
    assert PUBLIC_TITLE not in body


def test_an_authorized_reader_does_get_the_snippet(client, specialist):
    matter = _matter(specialist, number=513)
    _restricted_engagement(matter, specialist, title="Piiratud kaasamine", note=HIDDEN)
    rebuild_all()
    client.force_login(specialist)

    body = client.get(reverse("search:search"), {"q": HIDDEN}).content.decode()

    results = body.split('class="searchresults"', 1)[-1]
    assert PUBLIC_TITLE in results


# ---------------------------------------------------------------------------
# Break-glass reads through the same scope
# ---------------------------------------------------------------------------


def test_break_glass_reaches_a_restricted_engagement_and_expiry_ends_it(specialist):
    """One scope, not a second rule in the projection."""
    from app.accounts.models import BreakGlassGrant

    administrator = factories.AdministratorFactory()
    matter = _matter(specialist, number=514)
    _restricted_engagement(matter, specialist, title=HIDDEN, note="")
    rebuild_all()

    assert _found(HIDDEN, administrator) == []

    grant = BreakGlassGrant.objects.create(
        user=administrator,
        granted_by=specialist,
        reason="Intsidendi uurimine",
        starts_at=timezone.now() - timedelta(minutes=5),
        expires_at=timezone.now() + timedelta(hours=2),
    )

    assert _found(HIDDEN, administrator) == [(SearchSourceKind.ENGAGEMENT, matter.pk)]

    # And it stops when the grant does — no stale projection state survives it.
    grant.expires_at = timezone.now() - timedelta(minutes=1)
    grant.save(update_fields=["expires_at"])

    assert _found(HIDDEN, administrator) == []


# ---------------------------------------------------------------------------
# Timeline — a row about a child follows the child
# ---------------------------------------------------------------------------


HIDDEN_FILE = "SALAJANE-TOEND-321.pdf"


def _matter_with_restricted_document(specialist):
    """A NORMAL Matter carrying one document nobody else may open."""
    from app.documents.services import add_evidence_version, create_document

    matter = _matter(specialist, number=520)
    document = create_document(
        matter=matter,
        title="Piiratud tõend",
        role=DocumentRole.INCOMING_AUTHORITY,
        created_by=specialist,
    )
    add_evidence_version(
        document=document,
        content=b"%PDF-1.4\nsalajane",
        original_filename=HIDDEN_FILE,
        mime_type="application/pdf",
        uploaded_by=specialist,
    )
    document.visibility_override = Visibility.RESTRICTED
    document.save(update_fields=["visibility_override"])
    return matter, document


def test_a_restricted_documents_filename_is_not_in_the_timeline(specialist):
    """The reported AUTH-003 defect, reproduced and closed.

    `EVIDENCE_VERSION_ADDED` records the original filename as its summary, and
    the timeline templates print `event.summary`. Selecting those rows by Matter
    alone meant a document properly hidden from Dokumendid still announced its
    own name one tab away.
    """
    from app.matters.timeline import matter_timeline

    stranger = factories.ReaderFactory()  # not a lawyer: docs/adr/0042
    matter, document = _matter_with_restricted_document(specialist)

    items, _ = matter_timeline(matter=matter, user=stranger, limit=50)

    rendered = " ".join(
        str((getattr(item, "event", None) and item.event.summary) or "") for item in items
    )
    assert HIDDEN_FILE not in rendered
    # And the row is absent rather than blanked: the event's existence is itself
    # the disclosure, because only a restricted document produces one here.
    assert not any(
        getattr(item, "event", None)
        and item.event.object_id
        and str(item.event.object_id) in {str(v.pk) for v in document.versions.all()}
        for item in items
    )


def test_the_owner_still_sees_their_own_restricted_document_in_the_timeline(specialist):
    """Narrowing, not deleting. The participant's chronology is unchanged."""
    from app.matters.timeline import matter_timeline

    matter, _ = _matter_with_restricted_document(specialist)

    items, _ = matter_timeline(matter=matter, user=specialist, limit=50)

    rendered = " ".join(
        str((getattr(item, "event", None) and item.event.summary) or "") for item in items
    )
    assert HIDDEN_FILE in rendered


def test_matter_level_timeline_events_are_untouched(specialist):
    """A Matter event's subject really is the Matter, so it stays visible.

    Created through the service rather than the factory, because only the
    service writes `MATTER_CREATED` — the fact this test is about.
    """
    from app.matters.services import close_matter, create_matter
    from app.matters.timeline import matter_timeline

    stranger = factories.ReaderFactory()  # not a lawyer: docs/adr/0042
    matter = create_matter(title="Ajaloo teema", actor=specialist, owner=specialist)
    close_matter(matter=matter, actor=specialist, disposition="COMPLETED")

    items, _ = matter_timeline(matter=matter, user=stranger, limit=50)

    kinds = {item.event.event_type for item in items if getattr(item, "event", None) is not None}
    assert "MATTER_CREATED" in kinds
    assert "MATTER_CLOSED" in kinds


# ---------------------------------------------------------------------------
# Filter chips — a label is a rendered name, and the address bar is untrusted
# ---------------------------------------------------------------------------


HIDDEN_PERSON = "Salajane Varjatu"


def _hidden_owner_and_matter():
    """Somebody who owns only work nobody else may see.

    Deliberately not a department worker: `owner_filter_choices` offers today's
    colleagues whether or not they hold anything, so a current worker's name is
    legitimately public and would prove nothing here. This is the other half —
    a person represented *only* on restricted records.
    """
    hidden = factories.UserFactory(display_name=HIDDEN_PERSON, is_active=False)
    matter = factories.MatterFactory(
        owner=hidden,
        title="Piiratud teema",
        visibility=Visibility.RESTRICTED,
        reference_year=2099,
        reference_number=530,
    )
    return hidden, matter


def test_a_crafted_owner_filter_does_not_name_a_hidden_colleague_on_teemad(client):
    stranger = factories.ReaderFactory()  # not a lawyer: docs/adr/0042
    hidden, matter = _hidden_owner_and_matter()
    client.force_login(stranger)

    body = client.get(
        reverse("matters:matter_list"), {"vastutaja": str(hidden.pk)}
    ).content.decode()

    assert HIDDEN_PERSON not in body
    assert matter.title not in body
    # The identifier itself is deliberately *not* asserted absent. The register
    # keeps active filters in hidden form fields so the next search preserves
    # them, and echoing back a value the caller supplied tells them nothing they
    # did not already have. What must not come back is the name (§19).


def test_a_crafted_owner_filter_does_not_name_a_hidden_colleague_on_statistika(client):
    stranger = factories.ReaderFactory()  # not a lawyer: docs/adr/0042
    hidden, matter = _hidden_owner_and_matter()
    client.force_login(stranger)

    body = client.get(reverse("reporting:matters"), {"vastutaja": str(hidden.pk)}).content.decode()

    assert HIDDEN_PERSON not in body
    assert matter.title not in body


def test_a_departed_owner_of_visible_work_still_gets_a_truthful_chip(client, specialist):
    """PR #69's historical discoverability is not regressed.

    A colleague who has left still owns files, and a register that could not
    name them would hide exactly the work somebody is looking for. The rule is
    not "only current workers" — it is "only people this reader's own data
    already names".
    """
    departed = factories.UserFactory(display_name="Lahkunud Kolleeg", is_active=False)
    factories.MatterFactory(
        owner=departed,
        title="Nähtav vana teema",
        reference_year=2099,
        reference_number=531,
    )
    client.force_login(specialist)

    body = client.get(
        reverse("matters:matter_list"), {"vastutaja": str(departed.pk)}
    ).content.decode()

    assert departed.get_short_name() in body


# ---------------------------------------------------------------------------
# Current action — a restricted Järgmiseks is not printed on a visible row
# ---------------------------------------------------------------------------


HIDDEN_STEP = "SALAJANE-SAMM-741"


def _matter_with_restricted_action(specialist):
    from app.workflow.services import set_next_action

    matter = _matter(specialist, number=540)
    action = set_next_action(
        matter=matter,
        text=HIDDEN_STEP,
        target_date=timezone.localdate() + timedelta(days=7),
        actor=specialist,
    )
    action.visibility_override = Visibility.RESTRICTED
    action.save(update_fields=["visibility_override"])
    return matter, action


def test_a_restricted_next_action_is_not_printed_on_the_register(client, specialist):
    """`open_action_prefetch` decorates rows whose Matters are visible.

    Unscoped it printed a restricted step's text — and the colleague responsible
    for it — onto a row anybody could read.
    """
    stranger = factories.ReaderFactory()  # not a lawyer: docs/adr/0042
    matter, _ = _matter_with_restricted_action(specialist)
    client.force_login(stranger)

    body = client.get(reverse("matters:matter_list"), {"olek": "koik"}).content.decode()

    assert matter.title in body, "the Matter itself should still be listed"
    assert HIDDEN_STEP not in body


def test_the_owner_still_sees_their_own_restricted_next_action(client, specialist):
    _matter_with_restricted_action(specialist)
    client.force_login(specialist)

    body = client.get(reverse("matters:matter_list"), {"olek": "koik"}).content.decode()

    assert HIDDEN_STEP in body


def test_current_action_of_refuses_to_guess_without_a_reader(specialist):
    """The fallback path answers "none" rather than answering unscoped."""
    from app.matters.selectors import current_action_of

    matter, _ = _matter_with_restricted_action(specialist)
    fresh = type(matter).objects.get(pk=matter.pk)

    assert current_action_of(fresh) is None
    assert current_action_of(fresh, specialist) is not None


# ---------------------------------------------------------------------------
# Final evidence — the bytes were already refused; the metadata was not
# ---------------------------------------------------------------------------


def test_a_restricted_final_evidence_file_is_not_named_on_the_opinion_card(client, specialist):
    """A filename is frequently the most telling thing about a document."""
    from app.documents.services import add_evidence_version, create_document
    from app.submissions.enums import SubmissionStatus

    stranger = factories.ReaderFactory()  # not a lawyer: docs/adr/0042
    matter = _matter(specialist, number=541)
    document = create_document(
        matter=matter,
        title="Lõplik tõend",
        role=DocumentRole.KODA_SUBMISSION_FINAL,
        created_by=specialist,
    )
    version = add_evidence_version(
        document=document,
        content=b"%PDF-1.4\nlopp",
        original_filename=HIDDEN_FILE,
        mime_type="application/pdf",
        uploaded_by=specialist,
    )
    factories.SubmissionFactory(
        matter=matter,
        title="Koja arvamus",
        status=SubmissionStatus.SENT,
        sent_at=timezone.now(),
        final_version=version,
    )
    document.visibility_override = Visibility.RESTRICTED
    document.save(update_fields=["visibility_override"])
    client.force_login(stranger)

    body = client.get(
        reverse("matters:matter_documents", kwargs={"pk": matter.pk})
    ).content.decode()

    # Nothing about the restricted file: not its name, not its checksum, and not
    # an `Arvamus` badge or an anchor admitting a row was suppressed. An opinion
    # is a document row here, so a document this reader may not see produces no
    # row at all — a visible Submission is not authority to name its evidence
    # (AUTH-003 §21, docs/adr/0061 §25).
    assert HIDDEN_FILE not in body
    assert version.sha256[:16] not in body
    assert f"dokument-{document.pk}" not in body


def test_the_owner_still_sees_the_evidence_card(client, specialist):
    from app.documents.services import add_evidence_version, create_document
    from app.submissions.enums import SubmissionStatus

    matter = _matter(specialist, number=542)
    document = create_document(
        matter=matter,
        title="Lõplik tõend",
        role=DocumentRole.KODA_SUBMISSION_FINAL,
        created_by=specialist,
    )
    version = add_evidence_version(
        document=document,
        content=b"%PDF-1.4\nlopp",
        original_filename=HIDDEN_FILE,
        mime_type="application/pdf",
        uploaded_by=specialist,
    )
    factories.SubmissionFactory(
        matter=matter,
        title="Koja arvamus",
        status=SubmissionStatus.SENT,
        sent_at=timezone.now(),
        final_version=version,
    )
    client.force_login(specialist)

    body = client.get(
        reverse("matters:matter_documents", kwargs={"pk": matter.pk})
    ).content.decode()

    assert HIDDEN_FILE in body


# ---------------------------------------------------------------------------
# Personal notes stay out of every shared projection
# ---------------------------------------------------------------------------


def test_a_personal_note_never_reaches_a_shared_projection(specialist):
    """Private by design, and verified rather than assumed.

    Notes are not indexed, not timelined and not in the department feed. This
    asserts the property rather than the absence of code, so folding notes into
    any of the three later fails here.
    """
    from app.matters.overview import activity_feed
    from app.matters.services import save_personal_note
    from app.matters.timeline import matter_timeline

    other = factories.UserFactory()
    matter = _matter(specialist, number=543)
    save_personal_note(matter=matter, author=specialist, body="SALAJANE-MARGE-852")
    rebuild_all()

    assert _found("SALAJANE-MARGE-852", specialist) == []
    assert _found("SALAJANE-MARGE-852", other) == []

    items, _ = matter_timeline(matter=matter, user=other, limit=50)
    assert "SALAJANE-MARGE-852" not in str(items)

    feed = activity_feed(other, timezone.localdate())
    assert "SALAJANE-MARGE-852" not in str([(i.verb, i.matter_title) for i in feed])
