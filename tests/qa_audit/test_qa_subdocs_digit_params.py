"""Independent QA 2026-10-14 — subdocs area: `str.isdigit()` lets «²» through to `int()`.

`"²".isdigit()` is ``True`` and `int("²")` raises `ValueError`. The codebase
already knows this — `app.workflow.dates.year_from` checks `isascii()` for
exactly this reason (CORR-02) — but several query-string readers still guard
`int()` with a bare `isdigit()`, so a hand-edited URL answers 500 instead of
the refusal or fallback each surface promises. Probes against main 81a7d6e4.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

SUPERSCRIPT_TWO = "²"


@pytest.fixture
def lenient(client, specialist):
    """A signed-in client that reports a server error as a status, not a raise."""
    client.force_login(specialist)
    client.raise_request_exception = False
    return client


# ---------------------------------------------------------------------------
# My area: the Arvamused workspace and the opinion archive
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="QA-SUBDOCS-01: /arvamused/?kuu=² passes isdigit() and int() raises -> 500 "
    "(app/submissions/workspace.py sent_queryset)",
)
def test_opinions_workspace_month_with_superscript_digit_is_refused_not_500(lenient):
    response = lenient.get(reverse("submissions:sent"), {"kuu": SUPERSCRIPT_TWO})
    assert response.status_code != 500


def test_opinions_embedded_block_month_with_superscript_digit_is_not_500(lenient):
    response = lenient.get(reverse("submissions:embedded_block"), {"kuu": SUPERSCRIPT_TWO})
    assert response.status_code != 500


@pytest.mark.xfail(
    strict=True,
    reason="QA-SUBDOCS-01: /arvamused/arhiiv/?aasta=² passes isdigit() and int() raises "
    "-> 500 (app/legacy_import/opinion_search.py search_archive)",
)
def test_opinion_archive_year_with_superscript_digit_is_refused_not_500(lenient):
    response = lenient.get(reverse("submissions:archive"), {"aasta": SUPERSCRIPT_TWO})
    assert response.status_code != 500


def test_opinions_workspace_ordinary_bad_month_is_refused_with_a_sentence(lenient):
    """The control case: an ASCII nonsense month is refused, not crashed."""
    response = lenient.get(reverse("submissions:sent"), {"kuu": "13"})
    assert response.status_code != 500
    assert "Kuu peab olema arv vahemikus 1-12." in response.content.decode()


# ---------------------------------------------------------------------------
# Same root cause, found in passing outside this area
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="QA-SUBDOCS-01 (outside area): /teemad/?kaupa=² -> page_size_from int() raises "
    "-> 500 (app/matters/views.py)",
)
def test_register_page_size_with_superscript_digit_falls_back_not_500(lenient):
    response = lenient.get(reverse("matters:matter_list"), {"kaupa": SUPERSCRIPT_TWO})
    assert response.status_code != 500


@pytest.mark.xfail(
    strict=True,
    reason="QA-SUBDOCS-01 (outside area): Statistika Arvamused ?aasta=2026&kuu=² -> int() "
    "raises -> 500 (app/reporting/views.py _submission_selection)",
)
def test_statistics_submission_month_with_superscript_digit_is_404_not_500(lenient):
    response = lenient.get(
        reverse("reporting:submissions"), {"aasta": "2026", "kuu": SUPERSCRIPT_TWO}
    )
    assert response.status_code != 500


@pytest.mark.xfail(
    strict=True,
    reason="QA-SUBDOCS-01: administrator archive browse ?aasta=² -> the same search_archive "
    "int() -> 500 (app/legacy_import/archive_views.py archive_browse)",
)
def test_admin_archive_browse_year_with_superscript_digit_is_not_500(client, administrator):
    client.force_login(administrator)
    client.raise_request_exception = False
    response = client.get(
        reverse("legacy_import:opinion_archive_browse"), {"aasta": SUPERSCRIPT_TWO}
    )
    assert response.status_code != 500


@pytest.mark.xfail(
    strict=True,
    reason="QA-SUBDOCS-01: archive link POST viide=²_1 -> _matter_by_reference int() -> 500 "
    "(app/legacy_import/archive_views.py)",
)
def test_archive_link_reference_with_superscript_digit_is_refused_not_500(client, administrator):
    from django.utils import timezone

    from app.legacy_import.opinion_binary import OpinionArchiveBinary

    binary = OpinionArchiveBinary.objects.create(
        sha256="a" * 64,
        size_bytes=1,
        mime_type="application/pdf",
        storage_key="qa/subdocs/a.pdf",
        source_archive_sha256="b" * 64,
        materialized_at=timezone.now(),
    )
    client.force_login(administrator)
    client.raise_request_exception = False
    response = client.post(
        reverse("legacy_import:opinion_archive_link", kwargs={"pk": binary.pk}),
        {"action": "link", "viide": f"{SUPERSCRIPT_TWO}_1"},
    )
    assert response.status_code != 500
