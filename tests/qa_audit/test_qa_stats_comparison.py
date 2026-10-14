"""QA audit 2026-10-14 (area `stats`): comparisons, months and the Tallinn clock.

Probes that every submission number which should add up does add up, by record
identity rather than totals, with a TEST twin and a restricted opinion sitting
on the year boundary; the leap-day comparison; and one confirmed edge:

* QA-STATS-05 — `SUBMISSIONS_SENT_YOY_CHANGE` declines only when the first
  recorded Submission is in a *later year* than the previous window; when it is
  later in the *same* year, after the cutoff, the previous window was never
  measured and the card still reports a rise from 0 (ADR 0160 §5).
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, time
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.matters.enums import MatterDataClass, MatterOrigin, RecordMode
from app.matters.models import Matter
from app.reporting import metric_catalogue as keys
from app.reporting.context import ReportingContext, parse_period
from app.reporting.metric_types import MetricStatus
from app.reporting.services import compute
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from tests.synthetic_statistics import _document, _version

pytestmark = pytest.mark.django_db

TALLINN = ZoneInfo("Europe/Tallinn")


def _ctx(viewer, period: str, today: date) -> ReportingContext:
    return ReportingContext(
        viewer=viewer, period=parse_period(period, today), today=today, now=timezone.now()
    )


def _send(matter: Matter, title: str, when: datetime, *, restricted: bool = False) -> Submission:
    document = _document(matter, f"{title} lõplik")
    if restricted:
        document.visibility_override = Visibility.RESTRICTED
        document.save(update_fields=["visibility_override"])
    version = _version(document, filename=f"{title}.pdf", payload=f"qa {title} {matter.pk}")
    return Submission.objects.create(
        matter=matter,
        title=title,
        status=SubmissionStatus.SENT,
        sent_at=when,
        final_version=version,
        visibility_override=Visibility.RESTRICTED if restricted else "",
    )


def _list_ids(client, url: str) -> set:
    parsed = urlparse(url)
    query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    response = client.get(parsed.path, {**query, "leht": "1"})
    assert response.status_code == 200, url
    paginator = response.context["paginator"]
    ids = set()
    for number in paginator.page_range:
        ids |= {row.pk for row in paginator.page(number).object_list}
    assert len(ids) == response.context["total"]
    return ids


def _csv_titles(client, viewer, **query: str) -> list[str]:
    client.force_login(viewer)
    response = client.get(reverse("reporting:export", kwargs={"slug": "arvamused"}), query)
    assert response.status_code == 200
    text = b"".join(response.streaming_content).decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    return [row[1] for row in rows[1:]]


@pytest.fixture
def boundary(world):
    """Sends either side of New Year in Tallinn — real, TEST twin, restricted."""
    year = world.today.year
    real = world.native_open
    twin = Matter.objects.create(
        title="QA TEST twin aastavahetus",
        data_class=MatterDataClass.TEST,
        record_mode=RecordMode.FULL,
        origin=MatterOrigin.NATIVE,
        reference_year=year,
        reference_number=870,
        reporting_year=year,
        owner=real.owner,
    )
    late = datetime.combine(date(year - 1, 12, 31), time(23, 30), tzinfo=TALLINN)  # UTC: 21:30
    early = datetime.combine(date(year, 1, 1), time(0, 30), tzinfo=TALLINN)  # UTC: 31.12 22:30
    sends = {
        "real_late": _send(real, "QA vana-aasta õhtu", late),
        "real_early": _send(real, "QA uusaasta öö", early),
        "test_late": _send(twin, "QA TEST vana-aasta õhtu", late),
        "test_early": _send(twin, "QA TEST uusaasta öö", early),
        "restricted_early": _send(real, "QA piiratud uusaasta öö", early, restricted=True),
    }
    return world, sends


@pytest.mark.parametrize("viewer_name", ["head", "reader"])
def test_year_figures_agree_by_record_identity_across_new_year(viewer_name, client, boundary):
    world, sends = boundary
    viewer = getattr(world, viewer_name)
    year = world.today.year
    client.force_login(viewer)

    visible = Submission.objects.visible_to(viewer).filter(
        status=SubmissionStatus.SENT, matter__data_class=MatterDataClass.REAL
    )
    expected = {
        pk
        for pk in visible.values_list("pk", flat=True)
        if timezone.localdate(Submission.objects.get(pk=pk).sent_at).year == year
    }
    assert sends["real_early"].pk in expected and sends["real_late"].pk not in expected
    assert sends["test_early"].pk not in expected
    assert (sends["restricted_early"].pk in expected) is (viewer_name == "head")

    period_card = compute(keys.SUBMISSIONS_SENT, _ctx(viewer, str(year), world.today))
    by_period = compute(keys.SUBMISSIONS_SENT_BY_PERIOD, _ctx(viewer, "koik", world.today))
    by_month = compute(keys.SUBMISSIONS_SENT_BY_MONTH_AND_YEAR, _ctx(viewer, "koik", world.today))
    year_segment = next(s for s in by_period.segments if s.label == str(year))
    column = by_month.matrix.columns.index(str(year))

    assert period_card.value == len(expected)
    assert year_segment.value == len(expected)
    assert by_month.matrix.column_totals[column] == len(expected)
    assert _list_ids(client, period_card.drillthrough_url) == expected
    assert _list_ids(client, year_segment.url) == expected
    january = by_month.matrix.rows[0].cells[column]
    january_ids = _list_ids(client, january.url)
    assert sends["real_early"].pk in january_ids
    assert sends["test_early"].pk not in january_ids
    titles = _csv_titles(client, viewer, periood="koik", aasta=str(year))
    assert sorted(titles) == sorted(
        Submission.objects.filter(pk__in=expected).values_list("title", flat=True)
    )


def test_leap_day_comparison_cuts_both_sides_on_the_right_day(client, world):
    """29.02.2028 against 28.02.2027, both read in Tallinn, both lists open."""
    today = date(2028, 2, 29)
    matter = world.native_open
    inside_now = _send(
        matter, "QA liigpäeva hilisõhtu", datetime(2028, 2, 29, 23, 30, tzinfo=TALLINN)
    )
    outside_now = _send(matter, "QA märtsi öö", datetime(2028, 3, 1, 0, 30, tzinfo=TALLINN))
    inside_then = _send(
        matter, "QA 2027 viimane veebruar", datetime(2027, 2, 28, 23, 30, tzinfo=TALLINN)
    )
    outside_then = _send(matter, "QA 2027 1. märts", datetime(2027, 3, 1, 0, 30, tzinfo=TALLINN))

    result = compute(keys.SUBMISSIONS_SENT_YOY_CHANGE, _ctx(world.head, "kaesolev", today))
    comparison = result.comparison
    assert "kuni=02-29" in comparison.current_url and "aasta=2028" in comparison.current_url
    assert "kuni=02-28" in comparison.previous_url and "aasta=2027" in comparison.previous_url

    client.force_login(world.head)
    current_ids = _list_ids(client, comparison.current_url)
    previous_ids = _list_ids(client, comparison.previous_url)
    assert inside_now.pk in current_ids and outside_now.pk not in current_ids
    assert inside_then.pk in previous_ids and outside_then.pk not in previous_ids
    assert comparison.current_value == len(current_ids)
    assert comparison.previous_value == len(previous_ids)


def test_comparison_declines_when_the_previous_window_predates_the_first_record(world):
    """The shared world's first send is on 9 September of last year.

    Cut on 1 August, last year's window is 1.1–1.8, which no Submission record
    can speak for, so the card must decline (status INSUFFICIENT_DATA) rather
    than compare this year with a 0 that was never measured.
    """
    first = (
        Submission.objects.filter(status=SubmissionStatus.SENT)
        .order_by("sent_at")
        .values_list("sent_at", flat=True)
        .first()
    )
    today = date(world.today.year, 8, 1)
    assert timezone.localdate(first) == date(world.today.year - 1, 9, 9)
    result = compute(keys.SUBMISSIONS_SENT_YOY_CHANGE, _ctx(world.head, "kaesolev", today))
    assert result.comparison.previous_value == 0
    assert result.status == MetricStatus.INSUFFICIENT_DATA, (result.status, result.comparison)
