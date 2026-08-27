"""Child content in search, and the exact ways it must not leak.

ADR 0013 deferred this deliberately: indexing entries, submissions and document
text is only safe if a child's *current* restriction participates in the query.
This file is where that claim is checked, from every direction the brief names
(Stage-2B brief 43–45, 66).

The failure mode being tested for is not "a restricted document appears in a
list". It is subtler and much easier to ship: a restricted document *influencing*
a number. A count of four beside three rows discloses the fourth as surely as
showing its title, and it does so on a page where nothing looks wrong.
"""

from __future__ import annotations

import pytest

from app.core.enums import Visibility
from app.search.indexing import rebuild_all
from app.search.services import result_count, search
from tests import factories
from tests import synthetic_corpus as corpus

pytestmark = pytest.mark.django_db

PDF = "application/pdf"
HIDDEN_WORD = "salajanemarksona"
#: A word both documents contain, so a hidden one really could move a count.
SHARED_WORD = "kooskolastuskiri"


@pytest.fixture
def corpus_with_restricted_child(specialist, other_specialist, capture_evidence, extract):
    """One normal Matter holding one normal and one restricted document.

    The restricted document is the only place the secret word appears, so any
    number that moves when it is hidden is a leak.
    """
    matter = factories.MatterFactory(owner=specialist, title="Avalik teema")
    capture_evidence(
        matter,
        corpus.text_pdf([f"{SHARED_WORD} {corpus.MINISTRY} {corpus.ONLY_ON_PDF_PAGE_4}"]),
        "avalik.pdf",
        PDF,
        title="Avalik lisa",
    )
    hidden = capture_evidence(
        matter,
        corpus.text_pdf(
            [f"Konfidentsiaalne {SHARED_WORD}: {HIDDEN_WORD} on kokku lepitud. {corpus.MINISTRY}"]
        ),
        "salajane.pdf",
        PDF,
        title="Salajane lisa",
        visibility_override=Visibility.RESTRICTED,
    )
    for version in matter.documents.exclude(current_version=None):
        extract(version.current_version)
    return matter, hidden


# -- the matrix ------------------------------------------------------------


def test_an_owner_sees_the_content_of_their_restricted_document(
    corpus_with_restricted_child, specialist
) -> None:
    assert result_count(query=HIDDEN_WORD, user=specialist) == 1


def test_a_collaborator_sees_it(corpus_with_restricted_child, specialist, other_specialist) -> None:
    matter, _ = corpus_with_restricted_child
    matter.collaborators.add(other_specialist)
    assert result_count(query=HIDDEN_WORD, user=other_specialist) == 1


def test_an_unrelated_specialist_sees_nothing_of_it(corpus_with_restricted_child, reader) -> None:
    assert result_count(query=HIDDEN_WORD, user=reader) == 0
    assert search(query=HIDDEN_WORD, user=reader) == []


def test_the_department_head_sees_it(corpus_with_restricted_child, department_head) -> None:
    assert result_count(query=HIDDEN_WORD, user=department_head) == 1


def test_a_technical_administrator_does_not(corpus_with_restricted_child, administrator) -> None:
    """Technical administration is not business access, and this is the surface
    where forgetting that is cheapest."""
    assert result_count(query=HIDDEN_WORD, user=administrator) == 0


def test_a_superuser_does_not_either(corpus_with_restricted_child, superuser) -> None:
    assert result_count(query=HIDDEN_WORD, user=superuser) == 0


def test_an_anonymous_visitor_sees_nothing(corpus_with_restricted_child) -> None:
    from django.contrib.auth.models import AnonymousUser

    assert result_count(query=HIDDEN_WORD, user=AnonymousUser()) == 0


def test_a_break_glass_grant_opens_it(
    corpus_with_restricted_child, other_specialist, department_head
) -> None:
    from datetime import timedelta

    from django.utils import timezone

    from app.accounts.models import BreakGlassGrant

    BreakGlassGrant.objects.create(
        user=other_specialist,
        granted_by=department_head,
        reason="Sünteetiline juurdluskontroll",
        starts_at=timezone.now() - timedelta(minutes=1),
        expires_at=timezone.now() + timedelta(hours=1),
    )
    assert result_count(query=HIDDEN_WORD, user=other_specialist) == 1


# -- counts, ranking and pagination ----------------------------------------


def test_a_hidden_document_does_not_move_the_count(
    corpus_with_restricted_child, specialist, reader
) -> None:
    """Never "4 results, 1 hidden". The number must simply be 3.

    The term is one both documents contain, so the restricted one genuinely
    *would* be in the outsider's count if authorization were applied after
    counting rather than before it.
    """
    query = SHARED_WORD

    owner_total = result_count(query=query, user=specialist)
    outsider_total = result_count(query=query, user=reader)

    assert owner_total > outsider_total > 0
    assert outsider_total == len(search(query=query, user=reader))


def test_the_count_equals_the_rows_for_every_viewpoint(
    corpus_with_restricted_child, specialist, other_specialist, department_head, administrator
) -> None:
    """A count computed anywhere other than alongside the rows can disagree."""
    for user in (specialist, other_specialist, department_head, administrator):
        rows = search(query="eelnõu", user=user)
        assert result_count(query="eelnõu", user=user) == len(rows)


def test_a_hidden_document_contributes_no_snippet(corpus_with_restricted_child, reader) -> None:
    for result in search(query="Näidisministeerium", user=reader):
        text = "".join(run.text for run in result.snippet)
        assert HIDDEN_WORD not in text


def test_restricting_a_document_takes_effect_without_a_reindex(
    corpus_with_restricted_child, specialist, reader
) -> None:
    """The property ADR 0013 refused to trade away, one level further down.

    Nothing is reindexed here on purpose. If visibility were stored in the
    projection this test would pass only after a rebuild — and the window
    between restricting something and rebuilding is exactly when somebody looks.
    """
    matter, _ = corpus_with_restricted_child
    visible = matter.documents.get(title="Avalik lisa")
    assert result_count(query=corpus.ONLY_ON_PDF_PAGE_4, user=reader) == 1

    visible.visibility_override = Visibility.RESTRICTED
    visible.save(update_fields=["visibility_override"])

    assert result_count(query=corpus.ONLY_ON_PDF_PAGE_4, user=reader) == 0
    assert result_count(query=corpus.ONLY_ON_PDF_PAGE_4, user=specialist) == 1


def test_restricting_the_matter_hides_all_of_its_content(
    corpus_with_restricted_child, reader
) -> None:
    matter, _ = corpus_with_restricted_child
    matter.visibility = Visibility.RESTRICTED
    matter.save(update_fields=["visibility"])

    assert result_count(query="Näidisministeerium", user=reader) == 0


# -- entries and submissions -----------------------------------------------


@pytest.fixture
def matter_with_children(specialist):
    matter = factories.MatterFactory(owner=specialist, title="Teema lastega")
    factories.EntryFactory(
        matter=matter, author=specialist, body="<p>Avalik märkus koosolekult.</p>"
    )
    factories.EntryFactory(
        matter=matter,
        author=specialist,
        body=f"<p>Piiratud märkus: {HIDDEN_WORD}.</p>",
        visibility_override=Visibility.RESTRICTED,
    )
    factories.SubmissionFactory(matter=matter, title="Avalik arvamus", notes="Toetame eelnõu.")
    factories.SubmissionFactory(
        matter=matter,
        title="Piiratud arvamus",
        notes=f"Sisemine seisukoht: {HIDDEN_WORD}.",
        visibility_override=Visibility.RESTRICTED,
    )
    return matter


def test_an_entry_is_searchable_by_its_text(matter_with_children, specialist) -> None:
    results = search(query="koosolekult", user=specialist)
    assert [result.source_kind for result in results] == ["ENTRY"]


def test_a_restricted_entry_is_invisible_to_an_outsider(
    matter_with_children, reader, specialist
) -> None:
    assert result_count(query=HIDDEN_WORD, user=specialist) == 2  # entry and submission
    assert result_count(query=HIDDEN_WORD, user=reader) == 0


def test_a_submission_is_searchable_by_title_and_notes(matter_with_children, specialist) -> None:
    by_title = search(query="Avalik arvamus", user=specialist)
    assert any(result.source_kind == "SUBMISSION" for result in by_title)

    by_notes = search(query="Toetame", user=specialist)
    assert any(result.source_kind == "SUBMISSION" for result in by_notes)


def test_a_restricted_submission_contributes_nothing_to_an_outsider(
    matter_with_children, reader
) -> None:
    for result in search(query="arvamus", user=reader):
        assert "Piiratud arvamus" not in result.matter.title
        assert HIDDEN_WORD not in "".join(run.text for run in result.snippet)


def test_a_rebuild_does_not_change_who_can_see_what(
    matter_with_children, specialist, reader
) -> None:
    """Rebuilding writes the index from scratch. It must not write authorization."""
    before = (
        result_count(query=HIDDEN_WORD, user=specialist),
        result_count(query=HIDDEN_WORD, user=reader),
    )
    rebuild_all()
    after = (
        result_count(query=HIDDEN_WORD, user=specialist),
        result_count(query=HIDDEN_WORD, user=reader),
    )
    assert before == after == (2, 0)


def test_the_projection_stores_no_visibility_column() -> None:
    """Asserted structurally, because this is the mistake the design forbids.

    A future migration adding a `visibility` column here would be the start of
    the stale-authorization failure ADR 0005 and 0013 both rejected.
    """
    from app.search.models import SearchDocument

    fields = {field.name for field in SearchDocument._meta.get_fields()}
    assert "visibility" not in fields
    assert "effective_visibility" not in fields
    assert "visibility_override" not in fields
