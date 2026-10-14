"""Independent QA 2026-10-14 — subdocs area: search (ADR 0006, 0013, 0038, 0041, 0136).

Through the real `/otsing/` view and the header suggestions endpoint, against the
live projection the save signals maintain (no manual rebuild unless named), with
the counts checked as carefully as the rows. Probes against main 81a7d6e4.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.core.enums import Visibility
from app.matters.enums import EngagementKind
from app.matters.services import add_engagement, complete_engagement_feedback, create_matter
from app.search.services import MAX_QUERY_CHARACTERS, result_count, search_documents
from app.workflow.models import StageVocabulary
from tests.follow_ups import day, send_koja_arvamus

pytestmark = pytest.mark.django_db


def _matter(owner, title="QA otsingu teema", **extra):
    return create_matter(
        title=title,
        actor=owner,
        owner=owner,
        stage=StageVocabulary.objects.get(key="consultation"),
        **extra,
    )


def _search(client, q, **params):
    return client.get(reverse("search:search"), {"q": q, **params})


@pytest.mark.parametrize(
    "q",
    [
        "!",
        "&|!():*",
        "'",
        '"',
        '"unclosed phrase',
        "-",
        "--",
        "or",
        "a <-> b",
        "%",
        "_",
        "\\",
        "2026_",
        "_184",
        "2026 184 2026 184",
        "x" * MAX_QUERY_CHARACTERS,
        "sõna" * 60,
        "ü" * 400,
        "½ ß æ",
        "😀",
        "a\u0000b",
        "<script>alert(1)</script>",
        "%%%%%%%%%%%%",
    ],
)
def test_malformed_queries_answer_without_error(signed_in, specialist, q):
    _matter(specialist, title="Õigusloome šokolaadi žürii")

    response = _search(signed_in, q)

    assert response.status_code == (400 if chr(0) in q else 200)  # ENG-046 middleware
    assert "<script>alert(1)</script>" not in response.content.decode()


def test_over_long_query_is_refused_visibly(signed_in, specialist):
    response = _search(signed_in, "a" * (MAX_QUERY_CHARACTERS + 1))
    assert response.status_code == 200
    assert response.context["query_was_refused"] is True
    assert response.context["result_count"] == 0


@pytest.mark.parametrize("leht", ["-1", "0", "abc", "999999", "1.5", ""])
def test_malformed_page_numbers_answer_a_page(signed_in, specialist, leht):
    _matter(specialist, title="Lehekülgede proov")
    assert _search(signed_in, "Lehekülgede", leht=leht).status_code == 200


@pytest.mark.parametrize(
    "query",
    ["oigusloome", "ÕIGUSLOOME", "sokolaadi", "zurii", "Šokolaadi", "õigusloom"],
)
def test_diacritics_and_case_fold_both_ways(specialist, query):
    matter = _matter(specialist, title="Õigusloome šokolaadi žürii")
    found = {row.matter_id for row in search_documents(query=query, user=specialist)}
    assert matter.pk in found


def test_inflected_estonian_title_is_found_by_its_nominative(specialist):
    matter = _matter(specialist, title="Pakendiseaduse muutmise seaduse eelnõu")
    found = {row.matter_id for row in search_documents(query="pakendiseadus", user=specialist)}
    assert matter.pk in found


# ---------------------------------------------------------------------------
# Restricted-result filtering: rows, counts, snippets, suggestions
# ---------------------------------------------------------------------------


SECRET = "Sarapuupähkliregister"  # noqa: S105


@pytest.fixture
def restricted_with_opinion(specialist, organisation):
    matter = _matter(specialist, title="Konfidentsiaalne teema", visibility=Visibility.RESTRICTED)
    send_koja_arvamus(matter, specialist, [organisation], day(-1))
    submission = matter.submissions.get()
    submission.summary = f"{SECRET} arvamuse kokkuvõte"
    submission.save()
    return matter


@pytest.mark.parametrize("persona", ["reader", "administrator", "superuser"])
def test_restricted_opinion_summary_is_not_found_counted_or_suggested(
    request, client, restricted_with_opinion, persona
):
    user = request.getfixturevalue(persona)
    client.force_login(user)

    page = _search(client, SECRET)
    suggestions = client.get(reverse("search:suggestions"), {"q": SECRET}).json()

    assert page.status_code == 200
    assert page.context["result_count"] == 0
    assert page.context["rows"] == []
    assert result_count(query=SECRET, user=user) == 0
    assert suggestions["results"] == []
    assert suggestions["has_more"] is False


def test_the_owner_finds_the_restricted_summary_with_a_highlight(
    signed_in, restricted_with_opinion
):
    page = _search(signed_in, SECRET)
    assert page.context["result_count"] == 1
    assert SECRET in page.content.decode()


def test_restricted_matter_reference_does_not_redirect_a_reader(client, reader, specialist):
    matter = _matter(specialist, visibility=Visibility.RESTRICTED)
    client.force_login(reader)
    reference = f"{matter.reference_year}_{matter.reference_number}"

    response = _search(client, reference)

    assert response.status_code == 200
    assert response.context["result_count"] == 0


# ---------------------------------------------------------------------------
# Freshness — every change is visible on the next query, no rebuild
# ---------------------------------------------------------------------------


def test_document_rename_through_the_view_is_searchable_at_once(
    signed_in, specialist, capture_evidence
):
    matter = _matter(specialist)
    version = capture_evidence(
        matter, b"%PDF-1.4 x", "kiri.pdf", "application/pdf", title="Vanapealkiri"
    )

    signed_in.post(
        reverse("documents:rename", kwargs={"pk": version.document_id}),
        {"title": "Uuspealkirjake"},
    )

    assert search_documents(query="Uuspealkirjake", user=specialist).exists()
    assert not search_documents(query="Vanapealkiri", user=specialist).exists()


def test_matter_restriction_hides_its_rows_from_a_reader_without_a_rebuild(
    specialist, reader, organisation
):
    from app.matters.services import set_matter_visibility

    matter = _matter(specialist, title="Hiljem piiratud teema")
    send_koja_arvamus(matter, specialist, [organisation], day(-1))
    assert search_documents(query="Hiljem piiratud", user=reader).exists()

    set_matter_visibility(matter=matter, visibility=Visibility.RESTRICTED, actor=specialist)

    assert not search_documents(query="Hiljem piiratud", user=reader).exists()
    assert result_count(query="Hiljem piiratud", user=reader) == 0


def test_corrected_opinion_summary_is_searchable_at_once(signed_in, specialist, organisation):
    from app.submissions.services import correct_sent_opinion, sent_opinion_revision

    matter = _matter(specialist)
    sent = send_koja_arvamus(matter, specialist, [organisation], day(-1))

    correct_sent_opinion(
        submission=sent,
        sent_at=sent.sent_at,
        sent_at_precision=sent.sent_at_precision,
        summary="Mustikamoosi parandatud kokkuvõte",
        kind=sent.kind,
        actor=specialist,
        expected_revision=sent_opinion_revision(sent),
    )

    assert search_documents(query="Mustikamoosi", user=specialist).exists()


# ---------------------------------------------------------------------------
# What came back from a Kaasamine — two doors, two search answers
# ---------------------------------------------------------------------------


def test_feedback_is_searchable_whichever_door_recorded_it(signed_in, specialist):
    """DC-01, resolved 2026-10-14 (owner decision D8, docs/adr/0167 §3). It pinned
    that ADR 0086 kept `feedback_received` out of search while ADR 0142's `Lisa
    tagasiside` filed the same words as an indexed `MatterExternalPosition`. The
    round's own feedback is now in its `ENGAGEMENT` row's body, so the same
    answer is findable whichever button recorded it."""
    matter = _matter(specialist)
    first = add_engagement(
        matter=matter,
        kind=EngagementKind.OTHER,
        title="liikmed",
        occurred_on=day(-5),
        feedback_deadline=day(5),
        actor=specialist,
    )
    complete_engagement_feedback(
        engagement=first, actor=specialist, feedback_received="Kukerpallimaks liikmetelt"
    )
    second = add_engagement(
        matter=matter,
        kind=EngagementKind.OTHER,
        title="liikmed 2",
        occurred_on=day(-5),
        feedback_deadline=day(5),
        actor=specialist,
    )
    signed_in.post(
        reverse("matters:add_engagement_reply", kwargs={"pk": matter.pk}),
        {
            "engagement": str(second.pk),
            "summary": "Kukerpallimaks teiselt ringilt",
            "stated_on": "",
        },
        headers={"HX-Request": "true"},
    )

    kinds = set(
        search_documents(query="Kukerpallimaks", user=specialist).values_list(
            "source_kind", flat=True
        )
    )
    assert kinds == {"ENGAGEMENT", "EXTERNAL_POSITION"}
