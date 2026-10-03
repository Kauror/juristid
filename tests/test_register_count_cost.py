"""What the register and Saabunud must not pay for.

Measured in the 2026-10-02 code-health review on a 5,016-Matter clone and
removed; pinned here so they cannot return unnoticed:

* **The page count is a count of Matters, not of annotated rows.** The
  register's queryset carries eight correlated column subqueries; counting
  `queryset.distinct()` evaluated all of them for every matching row (a
  reader's count, 499 ms → 8 ms on the clone).
* **A live-search keystroke builds no saved-view chips.** They sit outside
  the region the fragment swaps, and each costs a population count.
* **Saabunud builds no intake form.** No template on it renders one.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app.matters import register_filters
from tests import factories

pytestmark = pytest.mark.django_db

REGISTER = reverse("matters:matter_list")


def test_the_page_count_counts_matters_not_annotated_rows(signed_in, specialist):
    for number in range(3):
        factories.MatterFactory(owner=specialist, title=f"Loendatav {number}")

    with CaptureQueriesContext(connection) as captured:
        response = signed_in.get(REGISTER, {"olek": "koik"})
    assert response.status_code == 200
    assert response.context["total"] == 3

    counts = [q["sql"] for q in captured if q["sql"].startswith("SELECT COUNT(*)")]
    assert counts, "no count statement was issued"
    for sql in counts:
        # The activity and obligation annotations are aggregate subqueries; a
        # count that carries them evaluates them per row.
        assert "MAX(" not in sql.upper(), sql


def test_a_keystroke_builds_no_saved_view_chips(signed_in, monkeypatch):
    calls = []
    real = register_filters.saved_views

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(register_filters, "saved_views", counting)

    assert signed_in.get(REGISTER, {"q": "maks"}, HTTP_HX_REQUEST="true").status_code == 200
    assert calls == []
    assert signed_in.get(REGISTER).status_code == 200
    assert calls == [1]


def test_saabunud_builds_no_intake_form(signed_in, monkeypatch):
    built = []
    import app.matters.views as views

    real = views.IncomingIntakeForm

    class Counting(real):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            built.append(1)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(views, "IncomingIntakeForm", Counting)

    assert signed_in.get(reverse("matters:inbox")).status_code == 200
    assert built == []


def test_the_four_status_chips_are_one_statement(specialist):
    """`Avatud`, `Suletud`, `Arhiiv`, `Kõik` — one aggregate, not four counts."""
    from app.matters.views import _segment_counts

    factories.MatterFactory(owner=specialist)
    with CaptureQueriesContext(connection) as captured:
        counts = _segment_counts(specialist)
    assert set(counts) == {"avatud", "suletud", "arhiiv", "koik"}
    # One aggregate, after the reader's scope (which may itself cost a lookup).
    assert len([q for q in captured if "COUNT(" in q["sql"].upper()]) == 1
