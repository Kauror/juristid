"""QA audit 2026-10-14 (area `stats`): deadlines and preparation time (ADR 0160 §2–§4).

Built on the hand-derived management world (`tests/test_reporting_management`,
day fixed at 13.10.2026) and adding the shapes it does not hold:

* a deadline revised twice before sending — preparation reads the first;
* a letter sent at 00:30 Tallinn the day after the deadline (still the deadline
  day in UTC) — late;
* a TEST twin and a restricted Matter with the same full history — record
  identity of every list for head and reader;
* a request recorded on the same day as a date-only send — documents that it is
  judged (DESIGN CONCERN DC-STATS-02), not asserted to be wrong.

Every "after" figure uses a fresh context (`ctx` builds one per call).
"""

from __future__ import annotations

from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest

from app.core.enums import Visibility
from app.matters.enums import MatterDataClass
from app.reporting import metric_catalogue as keys
from app.reporting.selectors import management
from app.reporting.services import compute
from app.submissions.enums import SentAtPrecision
from app.workflow.enums import Track
from tests.test_reporting_management import (  # noqa: F401 - `mgmt` is a fixture
    ANSWERED,
    MOVED,
    _ended,
    _matter,
    _sent,
    at,
    ctx,
    mgmt,
)

pytestmark = pytest.mark.django_db


def _rows(context, key: str, group: str) -> set[str]:
    return {row.title for row in management.metric_rows(context, key, group).rows}


def _list_titles(client, url: str) -> set[str]:
    parsed = urlparse(url)
    query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    response = client.get(parsed.path, query)
    assert response.status_code == 200, url
    return {row.title for row in response.context["listing"].rows}


@pytest.fixture
def twice_moved(mgmt):  # noqa: F811
    """Saabus 1.6; first deadline 10.6, moved to 20.6, moved again to 30.6; sent 25.6."""
    m = _matter("kaks korda nihutatud", received_date=date(2026, 6, 1), track=Track.DOMESTIC)
    _ended(
        m,
        date(2026, 6, 10),
        outcome=MOVED,
        ended=at(date(2026, 6, 5)),
        requested=at(date(2026, 6, 1)),
        next_deadline=date(2026, 6, 20),
    )
    _ended(
        m,
        date(2026, 6, 20),
        outcome=MOVED,
        ended=at(date(2026, 6, 15)),
        requested=at(date(2026, 6, 1)),
        next_deadline=date(2026, 6, 30),
    )
    s = _sent(m, "Kaks korda nihutatud arvamus", at(date(2026, 6, 25)))
    _ended(
        m,
        date(2026, 6, 30),
        outcome=ANSWERED,
        ended=at(date(2026, 6, 25), 12),
        requested=at(date(2026, 6, 1)),
        submission=s,
    )
    return m


def test_preparation_time_reads_the_first_of_several_deadlines(mgmt, twice_moved):  # noqa: F811
    rows = {r.title: r for r in management.preparation_rows(ctx(mgmt.head))}
    row = rows[twice_moved.title]
    assert row.deadline == date(2026, 6, 10)
    assert row.days == 9 and row.group == "8-14"


def test_twice_moved_answer_is_judged_against_the_deadline_in_force_at_sending(mgmt, twice_moved):  # noqa: F811
    titles = _rows(ctx(mgmt.head), keys.OPINIONS_ON_TIME, management.ON_TIME)
    assert twice_moved.title in titles  # 25.6 <= 30.6, both moves before sending


def test_a_letter_sent_at_half_past_midnight_after_the_deadline_is_late(mgmt):  # noqa: F811
    """00:30 Tallinn on 11.8 is 21:30 UTC on 10.8 — the Tallinn day decides."""
    m = _matter("südaöö", received_date=date(2026, 8, 1))
    s = _sent(m, "Südaöö arvamus", at(date(2026, 8, 11), 0, 30))
    _ended(
        m,
        date(2026, 8, 10),
        outcome=ANSWERED,
        ended=at(date(2026, 8, 11), 9),
        requested=at(date(2026, 8, 1)),
        submission=s,
    )
    context = ctx(mgmt.head)
    assert m.title in _rows(context, keys.OPINIONS_ON_TIME, management.LATE)
    assert m.title not in _rows(ctx(mgmt.head), keys.OPINIONS_ON_TIME, management.ON_TIME)


@pytest.fixture
def mirrored(mgmt):  # noqa: F811
    """A REAL, a TEST and a RESTRICTED Matter with the identical answered history."""
    made = {}
    for name, extra in (
        ("peegel päris", {}),
        ("peegel TEST", {"data_class": MatterDataClass.TEST}),
        ("peegel piiratud", {"visibility": Visibility.RESTRICTED}),
    ):
        m = _matter(name, received_date=date(2026, 9, 1), **extra)
        s = _sent(m, f"{name} arvamus", at(date(2026, 9, 9)))
        _ended(
            m,
            date(2026, 9, 10),
            outcome=ANSWERED,
            ended=at(date(2026, 9, 9), 12),
            requested=at(date(2026, 9, 1)),
            submission=s,
        )
        made[name] = m
    return made


@pytest.mark.parametrize("viewer", ["head", "reader"])
@pytest.mark.parametrize(
    "key,group",
    [
        (keys.OPINIONS_ON_TIME, management.ON_TIME),
        (keys.OPINIONS_ON_TIME, management.VERIFIABLE),
        (keys.RESPONSE_PREPARATION_TIME, management.PREP_ALL),
        (keys.RESPONSE_PREPARATION_TIME, "8-14"),
    ],
)
def test_lists_hold_real_and_entitled_records_only(mgmt, mirrored, client, viewer, key, group):  # noqa: F811
    user = getattr(mgmt, viewer)
    titles = _rows(ctx(user), key, group)
    assert mirrored["peegel päris"].title in titles
    assert mirrored["peegel TEST"].title not in titles
    assert (mirrored["peegel piiratud"].title in titles) is (viewer == "head")
    url = management.metric_list_url(ctx(user), key, group)
    client.force_login(user)
    assert _list_titles(client, url) == titles


def test_mirrored_test_matter_moves_no_deadline_figure(mgmt):  # noqa: F811
    before = {
        k: compute(k, ctx(mgmt.head)).value
        for k in (keys.OPINIONS_ON_TIME, keys.RESPONSE_PREPARATION_TIME)
    }
    m = _matter("ainult TEST", received_date=date(2026, 9, 1), data_class=MatterDataClass.TEST)
    s = _sent(m, "ainult TEST arvamus", at(date(2026, 9, 20)))  # late
    _ended(
        m,
        date(2026, 9, 10),
        outcome=ANSWERED,
        ended=at(date(2026, 9, 20), 12),
        requested=at(date(2026, 9, 1)),
        submission=s,
    )
    after = {k: compute(k, ctx(mgmt.head)).value for k in before}
    assert after == before


def test_an_initiative_with_an_arrival_and_its_own_deadline_is_excluded_and_named(mgmt):  # noqa: F811
    """A historical initiative holding a Saabus *and* a current deadline."""
    m = _matter(
        "algatus saabumisega",
        received_date=date(2026, 9, 1),
        track=Track.KODA_INITIATIVE,
        response_deadline=date(2026, 9, 30),
        response_requested_at=at(date(2026, 9, 1)),
    )
    result = compute(keys.RESPONSE_PREPARATION_TIME, ctx(mgmt.head))
    assert m.title not in {r.title for r in management.preparation_rows(ctx(mgmt.head))}
    assert any("Koja algatust" in note for note in result.notes)


def test_same_day_request_on_a_date_only_send_is_judged(mgmt):  # noqa: F811
    """Documents DC-STATS-02: the request recorded on the sending day of a date-only
    send is read as *before* the send and the answer is judged (code: same_day=False).
    ADR 0160 §4 names only a deadline *change* on the sending day as unknowable."""
    m = _matter("sama päeva küsimine")
    s = _sent(m, "Sama päeva arvamus", at(date(2026, 9, 15), 0), precision=SentAtPrecision.DATE)
    _ended(
        m,
        date(2026, 9, 20),
        outcome=ANSWERED,
        ended=at(date(2026, 9, 16)),
        requested=at(date(2026, 9, 15), 16),
        submission=s,
    )
    assert m.title in _rows(ctx(mgmt.head), keys.OPINIONS_ON_TIME, management.ON_TIME)
