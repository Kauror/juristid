"""QA audit 2026-10-14 (area `stats`): deadlines and preparation time (ADR 0160 §2–§4).

Built on the hand-derived management world (`tests/test_reporting_management`,
day fixed at 13.10.2026) and adding the shapes it does not hold:

* a deadline revised twice before sending — preparation reads the first;
* a letter sent at 00:30 Tallinn the day after the deadline (still the deadline
  day in UTC) — late;
* a TEST twin and a restricted Matter with the same full history — record
  identity of every list for head and reader (since docs/adr/0181 the reader
  lists the restricted one too; an administrator without a grant lists none);
* a request recorded on the same day as a date-only send — «ei saa kontrollida»
  (DC-STATS-02, owner decision D10 of 2026-10-14).

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
from tests import factories
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
def test_lists_hold_real_records_and_the_same_ones_for_every_reader(
    mgmt,  # noqa: F811
    mirrored,
    client,
    viewer,
    key,
    group,
):
    """Since docs/adr/0181 a READER lists the restricted Matter exactly as the head does."""
    user = getattr(mgmt, viewer)
    titles = _rows(ctx(user), key, group)
    assert mirrored["peegel päris"].title in titles
    assert mirrored["peegel TEST"].title not in titles
    assert mirrored["peegel piiratud"].title in titles
    assert titles == _rows(ctx(mgmt.head), key, group)
    url = management.metric_list_url(ctx(user), key, group)
    client.force_login(user)
    assert _list_titles(client, url) == titles


@pytest.mark.parametrize(
    "key,group",
    [
        (keys.OPINIONS_ON_TIME, management.ON_TIME),
        (keys.RESPONSE_PREPARATION_TIME, management.PREP_ALL),
    ],
)
def test_an_administrator_without_a_grant_lists_none_of_them(mgmt, mirrored, key, group):  # noqa: F811
    """The perimeter of docs/adr/0181: an outsider's list holds no record at all."""
    outsider = factories.AdministratorFactory()
    assert _rows(ctx(outsider), key, group) == set()


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


def test_same_day_request_on_a_date_only_send_is_not_verifiable(mgmt):  # noqa: F811
    """DC-STATS-02, decided by the owner (D10, 2026-10-14): a request recorded on the
    sending day of a date-only send cannot be ordered against it, so the answer is
    «ei saa kontrollida» — neither on time nor late, and outside the percentage.

    (Was `test_same_day_request_on_a_date_only_send_is_judged`, which pinned the
    old reading `same_day=False`: on time.)"""
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
    before = compute(keys.OPINIONS_ON_TIME, ctx(mgmt.head))
    context = ctx(mgmt.head)
    assert m.title not in _rows(context, keys.OPINIONS_ON_TIME, management.ON_TIME)
    assert m.title not in _rows(context, keys.OPINIONS_ON_TIME, management.LATE)
    assert m.title not in _rows(context, keys.OPINIONS_ON_TIME, management.VERIFIABLE)
    assert m.title in _rows(context, keys.OPINIONS_ON_TIME, management.SAME_DAY_REQUEST)
    assert management.SAME_DAY_REQUEST in management.UNVERIFIABLE_GROUPS
    labels = {segment.label: segment for segment in before.segments}
    same_day = labels[management.ANSWER_GROUP_LABELS[management.SAME_DAY_REQUEST]]
    assert same_day.value == 1
    assert same_day.is_unknown


def test_a_request_recorded_the_day_before_a_date_only_send_is_still_judged(mgmt):  # noqa: F811
    """D10 does not reach an ordinary verifiable case: the request day is earlier."""
    m = _matter("eelmise päeva küsimine")
    s = _sent(
        m, "Järgmisel päeval saadetud", at(date(2026, 9, 15), 0), precision=SentAtPrecision.DATE
    )
    _ended(
        m,
        date(2026, 9, 20),
        outcome=ANSWERED,
        ended=at(date(2026, 9, 16)),
        requested=at(date(2026, 9, 14), 16),
        submission=s,
    )
    assert m.title in _rows(ctx(mgmt.head), keys.OPINIONS_ON_TIME, management.ON_TIME)


def test_a_same_day_request_before_a_timestamped_send_is_still_judged(mgmt):  # noqa: F811
    """With a real send time the two instants are ordered: nothing is unknown."""
    m = _matter("sama päeva küsimine kellaajaga")
    s = _sent(m, "Kellaajaga arvamus", at(date(2026, 9, 15), 17))
    _ended(
        m,
        date(2026, 9, 14),
        outcome=ANSWERED,
        ended=at(date(2026, 9, 16)),
        requested=at(date(2026, 9, 15), 9),
        submission=s,
    )
    assert m.title in _rows(ctx(mgmt.head), keys.OPINIONS_ON_TIME, management.LATE)


def test_the_same_day_group_opens_its_own_list(mgmt, client):  # noqa: F811
    m = _matter("sama päeva loend")
    s = _sent(
        m, "Sama päeva loendi arvamus", at(date(2026, 9, 15), 0), precision=SentAtPrecision.DATE
    )
    _ended(
        m,
        date(2026, 9, 20),
        outcome=ANSWERED,
        ended=at(date(2026, 9, 16)),
        requested=at(date(2026, 9, 15), 11),
        submission=s,
    )
    result = compute(keys.OPINIONS_ON_TIME, ctx(mgmt.head))
    label = management.ANSWER_GROUP_LABELS[management.SAME_DAY_REQUEST]
    (segment,) = [segment for segment in result.segments if segment.label == label]
    client.force_login(mgmt.head)
    assert m.title in _list_titles(client, segment.url)
