"""Where Stage 2G meets what was already on main.

Stage 2G was branched before Stage 2E.1 and Stage 2F merged, so the seams
between them were never exercised by either branch's own suite. Each test here
covers one seam, and nothing here is a new feature: every behaviour asserted is
one of the three stages' own, checked in the presence of the other two.

The authorization tests matter most. Stage 2F introduced `is_department_head`
for Osakonna töö and Stage 2G introduced `may_review_work_victory` for
confirming a Töövõit, and the two arrived as independent copies of the same
three refusals. They are one resolver now (`_business_role`), and these tests
are what would notice if they ever stopped agreeing.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.urls import reverse

from app.core.authorization import (
    DEPARTMENT_VIEWER,
    is_department_head,
    may_review_work_victory,
    may_write_business_content,
)
from app.intelligence.enums import WorkVictoryStatus
from app.intelligence.services import add_important_date, add_work_victory_candidate
from app.matters.services import promote_matter_to_full
from app.search.models import SearchDocument
from app.workflow.enums import DatePrecision
from tests import factories

pytestmark = pytest.mark.django_db

FUTURE = date(2030, 6, 1)


# -- one department head, two surfaces --------------------------------------


def test_the_head_who_opens_osakonna_too_is_the_one_who_may_confirm(department_head):
    """Stage 2F's surface and Stage 2G's decision ask about the same person.

    Two predicates, one identity. If a later change teaches one of them about a
    new kind of non-person and not the other, this is the test that fails.
    """
    assert is_department_head(department_head)
    assert may_review_work_victory(department_head)
    assert may_write_business_content(department_head)


def test_the_head_reaches_both_surfaces_in_one_session(client, department_head):
    matter = factories.MatterFactory(owner=department_head)
    record = add_work_victory_candidate(
        matter=matter, title="Sünteetiline võit", actor=department_head
    )
    client.force_login(department_head)

    assert client.get(reverse("matters:department_work")).status_code == 200
    confirmed = client.post(
        reverse(
            "intelligence:confirm_work_victory",
            kwargs={"matter_id": matter.pk, "pk": record.pk},
        )
    )

    assert confirmed.status_code == 302
    record.refresh_from_db()
    assert record.status == WorkVictoryStatus.CONFIRMED


def test_a_technical_administrator_is_refused_both_stages_controls(
    client, administrator, specialist
):
    """Administration is not business access, on either stage's surface.

    Stage 2F's dashboard 404s; Stage 2G's confirmation route 403s. Jälgimine
    itself is a reading surface and is deliberately not asserted closed — what
    an administrator must not get is the ability to *act*.
    """
    matter = factories.MatterFactory(owner=specialist)
    record = add_work_victory_candidate(matter=matter, title="Sünteetiline võit", actor=specialist)
    client.force_login(administrator)

    assert client.get(reverse("matters:department_work")).status_code == 404

    refused = client.post(
        reverse(
            "intelligence:confirm_work_victory",
            kwargs={"matter_id": matter.pk, "pk": record.pk},
        )
    )
    assert refused.status_code == 403
    record.refresh_from_db()
    assert record.status == WorkVictoryStatus.CANDIDATE


def test_an_administrator_may_neither_head_the_department_nor_author_facts(administrator):
    assert not is_department_head(administrator)
    assert not may_review_work_victory(administrator)
    assert not may_write_business_content(administrator)


def test_the_shared_gate_sentinel_is_nobody_to_either_stage():
    """Knowing a password is not being somebody — on both surfaces."""
    assert not is_department_head(DEPARTMENT_VIEWER)
    assert not may_write_business_content(DEPARTMENT_VIEWER)
    assert not may_review_work_victory(DEPARTMENT_VIEWER)


def test_a_specialist_authors_facts_but_heads_nothing(specialist):
    assert may_write_business_content(specialist)
    assert not may_review_work_victory(specialist)
    assert not is_department_head(specialist)


# -- Stage 2F's portfolio, carrying Stage 2G's facts ------------------------


def test_a_promoted_matter_can_carry_structured_facts(specialist):
    """The cutover activates an archive row; the row then behaves like any other.

    Stage 2F promotes ARCHIVE to FULL, and Stage 2G attaches facts to Matters.
    Neither branch could test the pair.
    """
    matter = factories.ArchiveMatterFactory(owner=specialist)
    promote_matter_to_full(matter=matter, actor=specialist)
    matter.refresh_from_db()

    record = add_important_date(
        matter=matter,
        title="Tähtaeg pärast aktiveerimist",
        date_value=FUTURE,
        period_end=FUTURE,
        actor=specialist,
    )

    assert record.matter_id == matter.pk
    assert matter.important_dates.count() == 1


def test_restoring_an_archive_owner_does_not_widen_fact_authorization(specialist, reader):
    """Stage 2F's backfill sets owners. Visibility still decides who reads.

    An unrelated specialist must not reach a restricted Matter's facts merely
    because somebody now owns it.
    """
    from app.core.enums import Visibility
    from app.intelligence.models import MatterImportantDate

    matter = factories.ArchiveMatterFactory(owner=None, visibility=Visibility.RESTRICTED)
    add_important_date(
        matter=matter, title="Piiratud tähtaeg", date_value=FUTURE, period_end=FUTURE
    )

    matter.owner = specialist
    matter.save(update_fields=["owner", "updated_at"])

    reachable = MatterImportantDate.objects.visible_to(reader)
    assert not reachable.filter(matter=matter).exists()


# -- navigation -------------------------------------------------------------


def test_both_navigation_entries_survive_for_the_head(client, department_head):
    client.force_login(department_head)
    body = client.get(reverse("matters:overview")).content.decode()

    assert "Osakonna töö" in body
    assert "Jälgimine" in body


def test_a_specialist_is_offered_jalgimine_but_not_osakonna_too(client, specialist):
    client.force_login(specialist)
    body = client.get(reverse("matters:overview")).content.decode()

    assert "Jälgimine" in body
    assert reverse("matters:department_work") not in body


# -- search stays where Stage 2E.1 left it ----------------------------------


def test_structured_facts_are_not_indexed_for_search(specialist):
    """Deliberately deferred, and asserted so the deferral is visible.

    Stage 2G left intelligence records out of the search projection to avoid
    colliding with Stage 2E.1's live search. If somebody later indexes them,
    that should be a decision with a failing test in front of it — not a
    surprise in the register's results.
    """
    matter = factories.MatterFactory(owner=specialist)
    add_important_date(
        matter=matter, title="Indekseerimata tähtaeg", date_value=FUTURE, period_end=FUTURE
    )
    add_work_victory_candidate(
        matter=matter,
        title="Indekseerimata võit",
        date_precision=DatePrecision.YEAR,
        actor=specialist,
    )

    kinds = set(SearchDocument.objects.filter(matter=matter).values_list("source_kind", flat=True))
    assert "IMPORTANT_DATE" not in kinds
    assert "WORK_VICTORY" not in kinds
    assert "EFFECTIVE_DATE" not in kinds
