"""What the Teema page's hottest requests must not pay for.

Three costs the 2026-10-02 code-health review measured and removed, pinned
here so they cannot come back unnoticed:

* **The deletion plan is walked only where «Kustuta» is drawn.** `_header_context`
  is built for the whole page and for every HTMX save and refusal in the
  column, but only `header.html` reads `can_delete`, and a save response does
  not render the header. The plan was most of those responses' queries
  (149–159 of about 210 on a busy file; 207 → 58 once it went).
* **One plan asks each reference question once.** `_outside_references` used
  to repeat questions `_collect_owned` had already asked; the memo must not
  change what the plan says.
* **`Muuda kulgu` reads the Matter's step rows once**, and hands them to both
  rail helpers, as the Teema page does (plus the revision token's own read).
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app.matters import deletion
from app.matters.enums import EngagementKind
from app.matters.services import add_engagement, add_entry
from tests import factories

pytestmark = pytest.mark.django_db


@pytest.fixture
def busy_matter(specialist):
    """A Matter with children of several kinds, so the plan has a graph to walk."""
    matter = factories.MatterFactory(owner=specialist)
    for number in range(3):
        add_entry(matter=matter, body=f"Märge {number}", author=specialist)
        add_engagement(
            matter=matter,
            kind=EngagementKind.EMAIL_CAMPAIGN,
            title=f"Kaasamine {number}",
            occurred_on=dt.date(2026, 3, 1 + number),
            actor=specialist,
        )
    return matter


def test_a_save_response_never_walks_the_deletion_plan(signed_in, busy_matter, monkeypatch):
    calls = []
    real = deletion.plan_matter_deletion

    def counting(matter):
        calls.append(matter.pk)
        return real(matter)

    monkeypatch.setattr("app.matters.views.plan_matter_deletion", counting)

    response = signed_in.post(
        reverse("matters:add_received_feedback", kwargs={"pk": busy_matter.pk}),
        {"summary": "Toetab eelnõu.", "stated_on": "14.03.2026"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert calls == [], "an HTMX save response walked the deletion plan"

    # The full page draws the header, so it still asks — once.
    assert (
        signed_in.get(reverse("matters:matter_detail", kwargs={"pk": busy_matter.pk})).status_code
        == 200
    )
    assert calls == [busy_matter.pk]


def test_the_memo_changes_nothing_the_plan_says(busy_matter, monkeypatch):
    with CaptureQueriesContext(connection) as memoised:
        with_memo = deletion.plan_matter_deletion(busy_matter)

    monkeypatch.setattr(
        deletion._Owned,
        "referring_ids",
        lambda self, relation, ids: frozenset(deletion._referring_ids(relation, ids)),
    )
    with CaptureQueriesContext(connection) as plain:
        without = deletion.plan_matter_deletion(busy_matter)

    assert with_memo == without
    assert len(memoised) < len(plain)


def test_muuda_kulgu_reads_the_step_rows_once(signed_in, busy_matter):
    with CaptureQueriesContext(connection) as captured:
        response = signed_in.get(
            reverse("matters:timeline_steps", kwargs={"pk": busy_matter.pk}),
            HTTP_HX_REQUEST="true",
        )
    assert response.status_code == 200
    step_reads = [q["sql"] for q in captured if 'FROM "matters_mattertimelinestep"' in q["sql"]]
    # Two, and only two: the rows themselves (`timeline_step_rows`, shared by
    # the panel and both rail helpers — it was three reads), and the panel's
    # own revision token (`timeline_steps_revision_token`), which is a separate
    # question about the committed rows and is meant to be asked.
    assert len(step_reads) == 2, step_reads
