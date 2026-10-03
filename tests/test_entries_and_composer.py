"""Entries, the sanitiser, and the atomic composer.

The composer is the adoption feature, and its correctness argument is entirely
about atomicity: if half a save lands, the lawyer believes something that is not
in the record.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import DatabaseError, transaction
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.core.errors import ImmutableRecordError
from app.core.richtext import excerpt, plain_text, sanitize_entry_html
from app.matters.entry_enums import EntryKind
from app.matters.models import Entry, EntryRevision
from app.matters.services import add_entry, edit_entry
from app.workflow.enums import ActionKind
from tests import factories
from tests.refusals import refused

pytestmark = pytest.mark.django_db


# -- sanitising -------------------------------------------------------------


def test_script_tags_never_survive():
    dirty = '<p>Ohutu</p><script>alert("x")</script>'
    clean = sanitize_entry_html(dirty)
    assert "script" not in clean.lower()
    assert "Ohutu" in clean


def test_event_handlers_and_javascript_urls_are_stripped():
    dirty = '<p onclick="steal()">Tekst</p><a href="javascript:evil()">link</a>'
    clean = sanitize_entry_html(dirty)
    assert "onclick" not in clean
    assert "javascript:" not in clean


def test_word_and_outlook_noise_is_removed_but_the_text_survives():
    """Pasted Office markup should come out as clean structure, not escaped junk."""
    pasted = (
        '<p class="MsoNormal"><span style="font-family:Calibri;color:#1F497D">'
        "Ministeerium lubas uue sõnastuse.</span></p>"
    )
    clean = sanitize_entry_html(pasted)
    assert "MsoNormal" not in clean
    assert "style" not in clean
    assert "Ministeerium lubas uue sõnastuse." in plain_text(clean)


def test_useful_structure_is_kept():
    kept = sanitize_entry_html(
        "<p><strong>Oluline</strong> ja <em>rõhutatud</em></p>"
        "<ul><li>Punkt</li></ul>"
        "<table><tr><td>Vasak</td><td>Parem</td></tr></table>"
        '<a href="https://example.invalid">viide</a>'
    )
    for fragment in ("<strong>", "<em>", "<ul>", "<li>", "<table>", "<td>", "href="):
        assert fragment in kept


def test_links_get_safe_relationship_attributes():
    clean = sanitize_entry_html('<a href="https://example.invalid">viide</a>')
    assert "noopener" in clean


def test_excerpt_strips_markup_and_truncates():
    assert excerpt("<p>" + "a" * 400 + "</p>", limit=50).endswith("…")
    assert "<p>" not in excerpt("<p>lühike</p>")


# -- entries ----------------------------------------------------------------


def test_creating_an_entry_records_author_time_and_audit(normal_matter, specialist):
    entry = add_entry(
        matter=normal_matter,
        body="<p>Kohtumine ministeeriumiga.</p>",
        author=specialist,
        kind=EntryKind.MEETING,
    )
    assert entry.author == specialist
    assert entry.kind == EntryKind.MEETING
    assert entry.occurred_at is not None
    assert ChangeEvent.objects.filter(
        matter=normal_matter, event_type=ChangeEventType.ENTRY_ADDED
    ).exists()


def test_an_empty_body_is_refused(normal_matter, specialist):
    with refused("Sissekanne vajab sisu."):
        add_entry(matter=normal_matter, body="   ", author=specialist)


def test_a_body_that_is_only_markup_is_refused(normal_matter, specialist):
    """Stripping everything unsafe can leave nothing; that is not an entry."""
    with refused("Sissekanne vajab sisu."):
        add_entry(matter=normal_matter, body="<script>alert(1)</script>", author=specialist)


def test_occurred_at_can_differ_from_creation(normal_matter, specialist):
    """Friday's meeting written up on Monday belongs on Friday."""
    friday = timezone.now() - timedelta(days=3)
    entry = add_entry(
        matter=normal_matter, body="<p>Reedene kohtumine</p>", author=specialist, occurred_at=friday
    )
    assert entry.occurred_at == friday
    assert entry.created_at > entry.occurred_at


def test_entries_are_ordered_newest_first_with_a_deterministic_tie_break(normal_matter, specialist):
    same_moment = timezone.now()
    for index in range(3):
        add_entry(
            matter=normal_matter,
            body=f"<p>Sissekanne {index}</p>",
            author=specialist,
            occurred_at=same_moment,
        )
    first_page = list(Entry.objects.filter(matter=normal_matter).chronological())
    second_page = list(Entry.objects.filter(matter=normal_matter).chronological())
    assert [entry.id for entry in first_page] == [entry.id for entry in second_page]


def test_editing_keeps_the_previous_wording(normal_matter, specialist):
    entry = add_entry(matter=normal_matter, body="<p>Algne</p>", author=specialist)
    edit_entry(entry=entry, body="<p>Parandatud</p>", actor=specialist)

    entry.refresh_from_db()
    assert "Parandatud" in entry.body
    assert entry.edit_count == 1
    assert entry.was_edited is True

    revision = EntryRevision.objects.get(entry=entry)
    assert "Algne" in revision.body
    assert revision.revision_number == 1


def test_editing_to_the_same_text_is_not_an_edit(normal_matter, specialist):
    entry = add_entry(matter=normal_matter, body="<p>Sama</p>", author=specialist)
    edit_entry(entry=entry, body="<p>Sama</p>", actor=specialist)
    entry.refresh_from_db()
    assert entry.edit_count == 0
    assert EntryRevision.objects.filter(entry=entry).count() == 0


def test_entry_inherits_matter_visibility(restricted_matter, specialist, reader):
    add_entry(matter=restricted_matter, body="<p>Tundlik</p>", author=specialist)
    assert Entry.objects.visible_to(reader).count() == 0
    assert Entry.objects.visible_to(specialist).count() == 1


def test_an_entry_can_be_more_restrictive_than_its_matter(normal_matter, specialist, reader):
    add_entry(matter=normal_matter, body="<p>Avalik</p>", author=specialist)
    add_entry(
        matter=normal_matter,
        body="<p>Ainult vastutajale</p>",
        author=specialist,
        visibility_override=Visibility.RESTRICTED,
    )
    assert Entry.objects.visible_to(reader).count() == 1
    assert Entry.objects.visible_to(specialist).count() == 2


# -- the composer -----------------------------------------------------------


def test_one_act_produces_no_duplicate_timeline_events(normal_matter, specialist):
    """One act, one entry event — not one per surface that renders it.

    `PRAEGUNE TEGEVUS` with `Järgmisena`, the current act that writes an entry
    and a step together (the composer that used to was retired, ENG-050A2).
    """
    from app.matters import workspace
    from app.workflow.services import set_next_action

    current = set_next_action(
        matter=normal_matter, text="Kohtun ministeeriumiga", kind=ActionKind.DO, actor=specialist
    )
    before = set(ChangeEvent.objects.filter(matter=normal_matter).values_list("pk", flat=True))
    workspace.complete_current_action(
        matter=normal_matter,
        author=specialist,
        action_id=current.pk,
        body="<p>Üks sissekanne</p>",
        next_text="Üks tegevus",
    )

    written = ChangeEvent.objects.filter(matter=normal_matter).exclude(pk__in=before)
    assert written.filter(event_type=ChangeEventType.ENTRY_ADDED).count() == 1
    assert written.filter(event_type=ChangeEventType.NEXT_ACTION_SET).count() == 1


def test_entry_factory_is_synthetic():
    entry = factories.EntryFactory()
    assert "Sünteetiline" in plain_text(entry.body)


# -- EntryRevision is append-only in the database ---------------------------


def test_entry_revisions_cannot_be_edited_through_the_orm(normal_matter, specialist):
    """A revision that can be rewritten proves nothing about what was said."""
    entry = add_entry(matter=normal_matter, body="<p>Algne</p>", author=specialist)
    edit_entry(entry=entry, body="<p>Parandatud</p>", actor=specialist)
    revision = EntryRevision.objects.get(entry=entry)

    revision.body = "<p>Ümber kirjutatud</p>"
    with pytest.raises(ImmutableRecordError):
        revision.save()


def test_a_bulk_update_cannot_rewrite_an_entry_revision(normal_matter, specialist):
    entry = add_entry(matter=normal_matter, body="<p>Algne</p>", author=specialist)
    edit_entry(entry=entry, body="<p>Parandatud</p>", actor=specialist)
    revision = EntryRevision.objects.get(entry=entry)

    with pytest.raises(DatabaseError), transaction.atomic():
        EntryRevision.objects.filter(pk=revision.pk).update(body="<p>Ümber kirjutatud</p>")


def test_an_entry_revision_cannot_be_deleted(normal_matter, specialist):
    entry = add_entry(matter=normal_matter, body="<p>Algne</p>", author=specialist)
    edit_entry(entry=entry, body="<p>Parandatud</p>", actor=specialist)
    revision = EntryRevision.objects.get(entry=entry)

    with pytest.raises(DatabaseError), transaction.atomic():
        EntryRevision.objects.filter(pk=revision.pk).delete()

    assert EntryRevision.objects.filter(pk=revision.pk).exists()


def test_an_unknown_visibility_override_is_refused_by_the_service(normal_matter, specialist):
    """A clear domain error at the boundary, not an IntegrityError deep inside."""
    with refused("Unknown visibility override 'PUBLIC'"):
        add_entry(
            matter=normal_matter,
            body="<p>Tekst</p>",
            author=specialist,
            visibility_override="PUBLIC",
        )
