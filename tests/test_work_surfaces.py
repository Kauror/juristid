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
from app.matters import selectors
from app.matters.services import add_entry, close_matter, compose_update, create_matter
from app.matters.timeline import matter_timeline
from app.search.services import search_matters
from app.submissions.services import (
    attach_final_evidence,
    create_submission,
    mark_submission_sent,
)
from app.workflow.enums import ActionKind, DateSemantics
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.4 arvamus"


def _days(offset: int):
    return timezone.localdate() + timedelta(days=offset)


# -- Minu töö ---------------------------------------------------------------


def _bands(user):
    return {group.key: group for group in selectors.my_work_timeline(user)}


def test_work_is_banded_by_date_not_by_mode(specialist):
    passed = factories.MatterFactory(owner=specialist)
    today = factories.MatterFactory(owner=specialist)
    soon = factories.MatterFactory(owner=specialist)
    later = factories.MatterFactory(owner=specialist)

    for matter, day in ((passed, -3), (today, 0), (soon, 3), (later, 40)):
        set_next_action(
            matter=matter, text="Koosta arvamus", actor=specialist, target_date=_days(day)
        )

    groups = _bands(specialist)
    assert [action.matter_id for action in groups["passed"].actions] == [passed.id]
    assert [action.matter_id for action in groups["today"].actions] == [today.id]
    assert [action.matter_id for action in groups["soon"].actions] == [soon.id]
    assert [action.matter_id for action in groups["later"].actions] == [later.id]


def test_waiting_and_monitoring_are_in_the_same_list(specialist):
    """The QA correction: one list, banded by date, whatever the mode.

    The split columns made a lawyer read two lists and merge them in their head.
    """
    doing = factories.MatterFactory(owner=specialist)
    waiting = factories.MatterFactory(owner=specialist)
    monitoring = factories.MatterFactory(owner=specialist)

    set_next_action(matter=doing, text="Teen", actor=specialist, target_date=_days(2))
    set_next_action(
        matter=waiting,
        text="Ootan ministeeriumi",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=_days(3),
        actor=specialist,
    )
    set_next_action(
        matter=monitoring,
        text="Jälgin",
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=_days(4),
        actor=specialist,
    )

    soon = _bands(specialist)["soon"]
    assert [action.matter_id for action in soon.actions] == [doing.id, waiting.id, monitoring.id]


def test_a_do_action_without_deadline_semantics_still_appears(specialist):
    """The band that used to swallow it.

    `overdue`, `today` and `soon` each required DEADLINE semantics while `later`
    required a date beyond the horizon, so a DO dated inside the next week with
    any other semantics fell into no band at all and vanished from the page —
    which is exactly what the register's own parser produces for a vague month
    (Teema QA §4).
    """
    matter = factories.MatterFactory(owner=specialist)
    set_next_action(
        matter=matter,
        text="Eeldatavasti augustis",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.EXPECTED_AROUND,
        target_date=_days(5),
        actor=specialist,
    )
    assert [action.matter_id for action in _bands(specialist)["soon"].actions] == [matter.id]


def test_a_passed_review_is_not_counted_as_late(specialist):
    """One list does not mean one vocabulary. Only DO + DEADLINE is overdue."""
    late = factories.MatterFactory(owner=specialist)
    review = factories.MatterFactory(owner=specialist)
    set_next_action(matter=late, text="Tähtaeg", actor=specialist, target_date=_days(-2))
    set_next_action(
        matter=review,
        text="Ootan vastust",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=_days(-2),
        actor=specialist,
    )

    passed = _bands(specialist)["passed"]
    assert passed.count == 2
    assert selectors.overdue_count(passed.actions) == 1


def test_an_undated_action_has_its_own_band(specialist):
    # Not DO + DEADLINE: a deadline with no date cannot be met, missed or
    # planned against, and the domain refuses one (app/workflow/services.py).
    # Every other combination may legitimately have no date at all.
    matter = factories.MatterFactory(owner=specialist)
    set_next_action(
        matter=matter,
        text="Millalgi",
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.REVIEW_ON,
        actor=specialist,
    )
    groups = _bands(specialist)
    assert [action.matter_id for action in groups["undated"].actions] == [matter.id]
    assert groups["later"].count == 0


def test_another_persons_actions_are_not_mine(specialist, other_specialist):
    matter = factories.MatterFactory(owner=other_specialist)
    set_next_action(matter=matter, text="Nende töö", actor=other_specialist, target_date=_days(1))
    assert sum(group.count for group in selectors.my_work_timeline(specialist)) == 0


def test_attention_flags_an_active_matter_without_a_next_action(specialist):
    factories.MatterFactory(owner=specialist)
    items = selectors.my_attention_items(specialist)
    assert any(item.key == "no_next_action" for item in items)


def test_attention_stops_once_an_action_exists(specialist):
    matter = factories.MatterFactory(owner=specialist)
    set_next_action(matter=matter, text="Tegevus", actor=specialist, target_date=_days(1))
    items = selectors.my_attention_items(specialist)
    assert not any(item.key == "no_next_action" for item in items)


def test_attention_flags_a_passed_deadline_with_nothing_sent(specialist):
    matter = factories.MatterFactory(owner=specialist, response_deadline=_days(-2))
    set_next_action(matter=matter, text="Tegevus", actor=specialist, target_date=_days(1))

    items = selectors.my_attention_items(specialist)
    assert any(
        item.key == "deadline_without_submission" and item.matter.id == matter.id for item in items
    )


def test_attention_clears_once_a_submission_is_sent(specialist):
    matter = factories.MatterFactory(owner=specialist, response_deadline=_days(-2))
    submission = create_submission(matter=matter, title="Arvamus", actor=specialist)
    attach_final_evidence(
        submission=submission,
        content=PDF,
        original_filename="a.pdf",
        mime_type="application/pdf",
        actor=specialist,
    )
    submission.refresh_from_db()
    mark_submission_sent(submission=submission, actor=specialist)

    items = selectors.my_attention_items(specialist)
    assert not any(item.key == "deadline_without_submission" for item in items)


def test_attention_never_reveals_someone_elses_restricted_matter(specialist, other_specialist):
    factories.MatterFactory(owner=other_specialist, visibility=Visibility.RESTRICTED)
    items = selectors.my_attention_items(specialist)
    assert items == []


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
    create_submission(matter=normal_matter, title="Arvamus", actor=specialist)
    add_entry(matter=normal_matter, body="<p>Kohtumine</p>", author=specialist)
    set_next_action(
        matter=normal_matter, text="Koosta arvamus", actor=specialist, target_date=_days(1)
    )

    items, _has_more = matter_timeline(matter=normal_matter, user=specialist)
    kinds = {item.item_type for item in items}
    assert any(item.is_entry for item in items)
    assert ChangeEventType.NEXT_ACTION_SET in kinds
    # SUBMISSION_CREATED is not timeline-worthy; sending one is.
    assert ChangeEventType.SUBMISSION_CREATED not in kinds


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


def test_composer_result_is_visible_immediately_on_the_page(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    compose_update(
        matter=matter,
        author=specialist,
        body="<p>Kohtumine ministeeriumiga.</p>",
        next_action={"text": "Ootan uut sõnastust", "kind": ActionKind.WAIT},
    )
    response = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    body = response.content.decode()
    assert "Kohtumine ministeeriumiga." in body
    assert "Ootan uut sõnastust" in body
