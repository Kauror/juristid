"""One definition each for four small work-model rules (2026-10-28 audit A-06–A-09).

* **A-06** — why an `Arvamuse tähtaeg` no longer asks for work: the Teema
  header (`selectors.response_deadline_of`) and the register's Kuupäev cell
  (`register_dates.register_date`) read the discharge their own way and print
  the word `work_items.settlement_word` gives, in each of the three states.
* **A-07** — one register link builder (`register_filters.register_url`) and
  one «open FULL» (`register_filters.OPEN_FULL`): Osakond, Ülevaade's rows and
  Minu asjad all land on the results, with the same parameters as before.
* **A-08** — the calendar's estimates and the work model's milestones are a
  partition of one base (`work_items.active_milestones`).
* **A-09** — the calendar's week start and Minu asjad's month end are the
  shared helpers.
"""

from __future__ import annotations

import datetime
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.dates import end_of_month
from app.intelligence.enums import INFORMATIONAL_IMPORTANT_DATE_KINDS, ImportantDateKind
from app.intelligence.models import MatterImportantDate
from app.intelligence.services import add_important_date
from app.matters import my_work, register_filters, selectors
from app.matters import work_items as wi
from app.matters.register_dates import register_date
from app.matters.services import close_matter
from app.workflow.enums import Disposition
from tests import factories

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# A-06: one settlement word
# ---------------------------------------------------------------------------


def _both_words(matter, user, today):
    header = selectors.response_deadline_of(matter, user, today=today)
    row = selectors.matter_list_queryset(user).get(pk=matter.pk)
    cell = register_date(row, today)
    assert header is not None and cell is not None
    return header.settled, cell.settled


def test_an_owed_deadline_is_settled_nowhere(specialist):
    today = timezone.localdate()
    matter = factories.MatterFactory(owner=specialist, response_deadline=today - timedelta(days=3))

    assert _both_words(matter, specialist, today) == ("", "")


def test_a_deadline_an_opinion_answered_is_lopetatud_on_both(specialist):
    today = timezone.localdate()
    matter = factories.MatterFactory(owner=specialist, response_deadline=today - timedelta(days=3))
    factories.send_opinion_through_services(matter, specialist)
    matter.refresh_from_db()

    assert matter.is_open
    assert _both_words(matter, specialist, today) == (wi.SETTLED_DISCHARGED,) * 2


def test_a_closed_matters_deadline_is_teema_suletud_on_both(specialist):
    today = timezone.localdate()
    matter = factories.MatterFactory(owner=specialist, response_deadline=today + timedelta(days=9))
    close_matter(
        matter=matter,
        disposition=Disposition.INITIATIVE_WITHDRAWN,
        actor=specialist,
        follow_ups_confirmed=True,
    )
    matter.refresh_from_db()

    assert _both_words(matter, specialist, today) == (wi.SETTLED_CLOSED,) * 2


# ---------------------------------------------------------------------------
# A-07: one register link builder
# ---------------------------------------------------------------------------


def _parts(url):
    parsed = urlparse(url)
    return (
        parsed.path,
        {key: values[0] for key, values in parse_qs(parsed.query).items()},
        (parsed.fragment),
    )


def test_the_builder_lands_on_the_rows_and_drops_empty_values():
    url = register_filters.register_url(**register_filters.OPEN_FULL, vastutaja="", too="x")

    assert url == f"{reverse('matters:matter_list')}?olek=avatud&liik=FULL&too=x#tulemused"
    assert register_filters.register_url() == f"{reverse('matters:matter_list')}#tulemused"


def test_minu_asjad_links_carry_the_same_parameters_and_land_on_the_rows(specialist):
    factories.MatterFactory(owner=specialist)
    page = my_work.build_my_work(specialist, timezone.localdate())

    urls = [figure.url for figure in page.seis if figure.url.startswith("/")]
    urls.append(page.portfolio.register_url)
    for url in urls:
        path, params, fragment = _parts(url)
        assert path == reverse("matters:matter_list"), url
        assert fragment == "tulemused", url
        assert (params["olek"], params["liik"]) == ("avatud", "FULL"), url
        assert str(specialist.pk) in (params.get("vastutaja"), params.get("too_vastutaja")), url


# ---------------------------------------------------------------------------
# A-08: one milestone base, partitioned
# ---------------------------------------------------------------------------


def test_the_estimates_and_the_work_milestones_partition_one_base(specialist, reader):
    today = timezone.localdate()
    matter = factories.MatterFactory(owner=specialist)
    for kind in (ImportantDateKind.OTHER, *sorted(INFORMATIONAL_IMPORTANT_DATE_KINDS)):
        add_important_date(
            matter=matter,
            title=f"Kuupäev {kind}",
            kind=kind,
            date_value=today + timedelta(days=20),
            period_end=today + timedelta(days=20),
            actor=specialist,
        )
    closed = factories.MatterFactory(
        owner=specialist, is_open=False, disposition="COMPLETED", closed_at=timezone.now()
    )
    MatterImportantDate.objects.create(
        matter=closed,
        title="Suletud",
        date_value=today + timedelta(days=20),
        period_end=today + timedelta(days=20),
    )

    for user in (specialist, reader):
        base = set(wi.active_milestones(user))
        work = set(wi.important_deadlines(user))
        estimates = set(
            wi.active_milestones(user).filter(kind__in=INFORMATIONAL_IMPORTANT_DATE_KINDS)
        )
        assert work | estimates == base
        assert not work & estimates
        assert {record.matter_id for record in base} == {matter.pk}
        assert len(estimates) == len(INFORMATIONAL_IMPORTANT_DATE_KINDS)


# ---------------------------------------------------------------------------
# A-09: the shared trivial helpers
# ---------------------------------------------------------------------------


def test_the_shared_week_start_and_month_end():
    sunday = datetime.date(2026, 11, 1)

    assert wi.start_of_iso_week(sunday) == datetime.date(2026, 10, 26)
    assert end_of_month(datetime.date(2026, 12, 15)) == datetime.date(2026, 12, 31)
    assert end_of_month(datetime.date(2028, 2, 1)) == datetime.date(2028, 2, 29)
