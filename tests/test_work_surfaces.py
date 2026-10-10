"""Minu töö, Teemad, search and the timeline.

These are the surfaces where an authorization mistake would be invisible: a
restricted Matter that leaks into a count, a page boundary or a search snippet
does not look like a bug from the outside.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType, SecurityEventType
from app.audit.services import record_security_event
from app.core.enums import Visibility
from app.matters.services import add_entry, close_matter, create_matter
from app.matters.timeline import matter_timeline
from app.search.services import search_matters
from app.submissions.services import (
    create_submission,
)
from app.workflow.enums import ActionKind
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.4 arvamus"


def _days(offset: int):
    return timezone.localdate() + timedelta(days=offset)


# -- Minu töö through the browser ------------------------------------------


def test_my_work_page_renders_and_counts_only_visible_matters(
    signed_in, specialist, other_specialist
):
    mine = factories.MatterFactory(owner=specialist, title="Minu teema")
    factories.MatterFactory(owner=other_specialist, visibility=Visibility.RESTRICTED)
    set_next_action(matter=mine, text="Koosta arvamus", actor=specialist, target_date=_days(1))

    response = signed_in.get(reverse("matters:my_work"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Minu teema" in body
    assert response.context["work"].open_matters == 1


def test_matter_list_paginates_and_filters(signed_in, specialist):
    for index in range(30):
        factories.MatterFactory(owner=specialist, title=f"Teema {index}")

    response = signed_in.get(reverse("matters:matter_list"))
    assert response.status_code == 200
    assert response.context["page"].paginator.count == 30
    # Twelve by default since the v2 rebuild, and the reader may ask for more
    # (02-EKRAANID §C).
    assert len(response.context["page"].object_list) == 12

    second = signed_in.get(reverse("matters:matter_list"), {"leht": 3})
    assert len(second.context["page"].object_list) == 6

    everything = signed_in.get(reverse("matters:matter_list"), {"kaupa": "koik"})
    assert len(everything.context["page"].object_list) == 30


def test_matter_list_counts_exclude_restricted_matters(client, specialist, reader):
    """Authorization runs before the count, not after the page is built.

    Counted for the reader, who may not open the restricted one. A lawyer sees
    both since docs/adr/0042, so only somebody outside the legal team can show
    that the count is scoped at all.
    """
    factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    factories.MatterFactory(owner=specialist, visibility=Visibility.NORMAL)

    client.force_login(reader)
    response = client.get(reverse("matters:matter_list"))
    assert response.context["page"].paginator.count == 1


def test_matter_list_filters_by_owner_and_status(signed_in, specialist, other_specialist):
    mine = factories.MatterFactory(owner=specialist)
    factories.MatterFactory(owner=other_specialist)
    closed = factories.MatterFactory(owner=specialist)
    close_matter(matter=closed, disposition="COMPLETED", actor=specialist)

    response = signed_in.get(reverse("matters:matter_list"), {"ulatus": "minu", "olek": "avatud"})
    ids = {matter.id for matter in response.context["page"].object_list}
    assert ids == {mine.id}


def test_full_and_archive_matters_coexist_in_the_register(signed_in, specialist):
    full = factories.MatterFactory(owner=specialist, title="Aktiivne teema")
    archive = factories.ArchiveMatterFactory(title="Ajalooline kirje")

    response = signed_in.get(reverse("matters:matter_list"), {"olek": "koik"})
    ids = {matter.id for matter in response.context["page"].object_list}
    assert {full.id, archive.id} <= ids

    only_archive = signed_in.get(
        reverse("matters:matter_list"), {"olek": "koik", "liik": "ARCHIVE"}
    )
    assert {matter.id for matter in only_archive.context["page"].object_list} == {archive.id}


# -- search -----------------------------------------------------------------


def test_search_finds_a_matter_by_reference(specialist):
    matter = create_matter(
        title="Pakendiseaduse muutmise eelnõu", actor=specialist, owner=specialist
    )
    results = search_matters(query=matter.display_reference, user=specialist)
    assert [result.matter.id for result in results] == [matter.id]
    assert results[0].match_kind == "reference"


def test_search_finds_a_matter_by_title_fragment(specialist):
    matter = factories.MatterFactory(owner=specialist, title="Pakendiseaduse muutmise eelnõu")
    results = search_matters(query="pakendiseaduse", user=specialist)
    assert matter.id in {result.matter.id for result in results}


def test_search_finds_a_matter_by_alternate_title(specialist):
    matter = factories.MatterFactory(
        owner=specialist, title="Ametlik pealkiri", alternate_titles=["Rahvasuus pakendipakett"]
    )
    results = search_matters(query="pakendipakett", user=specialist)
    assert matter.id in {result.matter.id for result in results}


def test_search_finds_a_matter_by_organisation(specialist):
    ministry = factories.OrganisationFactory(name="Kliimaministeerium")
    matter = factories.MatterFactory(owner=specialist, source_organisations=[ministry])
    results = search_matters(query="kliima", user=specialist)
    assert matter.id in {result.matter.id for result in results}


def test_search_never_returns_a_restricted_matter(specialist, reader):
    factories.MatterFactory(
        owner=specialist,
        title="Salajane pakendiseaduse teema",
        visibility=Visibility.RESTRICTED,
    )
    assert search_matters(query="pakendiseaduse", user=reader) == []


def test_search_by_reference_does_not_leak_a_restricted_matter(specialist, reader):
    """Guessing an exact reference must not confirm the record exists."""
    hidden = create_matter(
        title="Salajane",
        actor=specialist,
        owner=specialist,
        visibility=Visibility.RESTRICTED,
    )
    assert search_matters(query=hidden.display_reference, user=reader) == []


def test_an_empty_query_returns_nothing(specialist):
    factories.MatterFactory(owner=specialist)
    assert search_matters(query="   ", user=specialist) == []


def test_search_page_renders(signed_in, specialist):
    factories.MatterFactory(owner=specialist, title="Pakendiseaduse muutmise eelnõu")
    response = signed_in.get(reverse("search:search"), {"q": "pakendiseaduse"})
    assert response.status_code == 200
    assert "Pakendiseaduse" in response.content.decode()


def test_a_unique_reference_hit_opens_the_matter(signed_in, specialist):
    matter = create_matter(title="Otsene viide", actor=specialist, owner=specialist)
    response = signed_in.get(reverse("search:search"), {"q": matter.display_reference})
    assert response.status_code == 302
    assert str(matter.pk) in response.url


# -- timeline ---------------------------------------------------------------


def test_timeline_merges_entries_and_selected_events(normal_matter, specialist):
    """Both sources reach the projection, and the event vocabulary still filters.

    The step is **superseded** rather than left open, because since
    docs/adr/0092 §8 the action that is currently open reads at the top of the
    page under `PRAEGUNE TEGEVUS` and not a second time as history — a
    `Järgmiseks` printed in both places reads as two instructions. A step that
    has been replaced is history, and draws its row exactly as it always did,
    which is what this test is about.
    """
    create_submission(matter=normal_matter, title="Arvamus", actor=specialist)
    add_entry(matter=normal_matter, body="<p>Kohtumine</p>", author=specialist)
    set_next_action(
        matter=normal_matter, text="Koosta arvamus", actor=specialist, target_date=_days(1)
    )
    set_next_action(
        matter=normal_matter, text="Vaata vastus üle", actor=specialist, target_date=_days(3)
    )

    items, _has_more = matter_timeline(matter=normal_matter, user=specialist)
    kinds = {item.item_type for item in items}
    assert any(item.is_entry for item in items)
    assert ChangeEventType.NEXT_ACTION_SET in kinds
    # SUBMISSION_CREATED is not timeline-worthy; sending one is.
    assert ChangeEventType.SUBMISSION_CREATED not in kinds
    # And the one that is still open is not among them.
    rendered = " ".join(item.next_step.text for item in items if item.next_step is not None)
    assert "Vaata vastus üle" not in rendered


def test_timeline_is_newest_first_by_occurrence(normal_matter, specialist):
    old = timezone.now() - timedelta(days=10)
    new = timezone.now() - timedelta(days=1)
    add_entry(matter=normal_matter, body="<p>Vana</p>", author=specialist, occurred_at=old)
    add_entry(matter=normal_matter, body="<p>Uus</p>", author=specialist, occurred_at=new)

    items, _ = matter_timeline(matter=normal_matter, user=specialist)
    entry_items = [item for item in items if item.is_entry]
    assert entry_items[0].occurred_at > entry_items[1].occurred_at


def test_timeline_never_shows_security_audit_events(normal_matter, specialist):
    record_security_event(
        event_type=SecurityEventType.DOCUMENT_DOWNLOADED, actor=specialist, subject=normal_matter
    )
    add_entry(matter=normal_matter, body="<p>Sissekanne</p>", author=specialist)

    items, _ = matter_timeline(matter=normal_matter, user=specialist)
    assert all(
        item.event is None or item.event.event_type != SecurityEventType.DOCUMENT_DOWNLOADED
        for item in items
    )


def test_timeline_hides_a_restricted_entry_from_an_uninvolved_user(
    normal_matter, specialist, reader
):
    add_entry(matter=normal_matter, body="<p>Avalik</p>", author=specialist)
    add_entry(
        matter=normal_matter,
        body="<p>Salajane märkus</p>",
        author=specialist,
        visibility_override=Visibility.RESTRICTED,
    )

    items, _ = matter_timeline(matter=normal_matter, user=reader)
    bodies = " ".join(item.entry.body for item in items if item.is_entry)
    assert "Salajane" not in bodies
    assert "Avalik" in bodies


def test_timeline_paginates_deterministically(normal_matter, specialist):
    for index in range(40):
        add_entry(
            matter=normal_matter,
            body=f"<p>Sissekanne {index}</p>",
            author=specialist,
            occurred_at=timezone.now() - timedelta(minutes=index),
        )

    first, has_more = matter_timeline(matter=normal_matter, user=specialist, limit=10)
    second, _ = matter_timeline(matter=normal_matter, user=specialist, limit=10, offset=10)

    assert has_more is True
    assert len(first) == 10
    first_ids = {item.sort_key for item in first}
    second_ids = {item.sort_key for item in second}
    assert not (first_ids & second_ids)


def test_a_saved_act_is_visible_immediately_on_the_page(signed_in, specialist):
    """`PRAEGUNE TEGEVUS` with `Järgmisena` — the note and the new step, at once."""
    from app.matters import workspace

    matter = factories.MatterFactory(owner=specialist)
    current = set_next_action(
        matter=matter, text="Kohtun ministeeriumiga", kind=ActionKind.DO, actor=specialist
    )
    workspace.complete_current_action(
        matter=matter,
        author=specialist,
        action_id=current.pk,
        body="<p>Kohtumine ministeeriumiga.</p>",
        next_text="Ootan uut sõnastust",
    )
    response = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    body = response.content.decode()
    assert "Kohtumine ministeeriumiga." in body
    assert "Ootan uut sõnastust" in body
