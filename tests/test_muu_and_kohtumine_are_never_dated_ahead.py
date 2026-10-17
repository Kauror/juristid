"""A `Muu` or a `Kohtumine` is never dated after today (JQA-001, owner decision D1).

`+ Lisa · Muu` and `Kohtumine / arutelu` store the stated day in
`Entry.occurred_at`, and `app.matters.activity` reads every readable entry as
work done on that day. A day ahead therefore became «Viimane tegevus», took a
quiet Teema out of «Muutusteta 30 p» and was counted by Osakond in a week that
had not happened. Both records say what *happened* — material met, a meeting
held (docs/adr/0097, amendments of 2026-10-15 and 2026-10-17) — so a day after
today is refused, on the form beside the box and in the service for every other
caller, in the same words a send dated ahead is refused in («Saatmise kuupäev ei
saa olla tulevikus.», docs/adr/0121 §3). A plan has its own records:
`Järgmine tegevus` and `Oluline tähtaeg`.

**Today is today in Tallinn.** The day box is compared with
`timezone.localdate()`, so half past midnight in Tallinn (still the previous day
in UTC) already accepts the new day, and the last minute of a day still refuses
the next one.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.dates import start_of_local_day
from app.matters import work_items as wi
from app.matters import workspace
from app.matters.activity import ActivityBasis, activity_for_matter
from app.matters.department_dashboard import _matters_changed_by_owner
from app.matters.entry_enums import EntryKind
from app.matters.forms import CompactMeetingForm, CompactOtherForm
from app.matters.models import Entry, Matter
from tests import factories
from tests.refusals import refused

pytestmark = pytest.mark.django_db

UTC = dt.UTC
HX = {"HX-Request": "true"}
OLD = -60

#: The sentences the person reads; `test_the_sentences_are_the_services` ties
#: them to the service's constants.
MUU_AHEAD = "Toimumise kuupäev ei saa olla tulevikus."
KOHTUMINE_AHEAD = "Kohtumise kuupäev ei saa olla tulevikus."


def _today() -> dt.date:
    return timezone.localdate()


def _day(offset: int) -> dt.date:
    return _today() + dt.timedelta(days=offset)


def _et(day: dt.date) -> str:
    return f"{day.day:02d}.{day.month:02d}.{day.year:04d}"


def _add_muu(matter: Matter, author: Any, day: dt.date) -> Any:
    return workspace.add_matter_other(
        matter=matter, author=author, description="Taustamaterjal.", occurred_on=day
    )


def _add_kohtumine(matter: Matter, author: Any, day: dt.date) -> Any:
    return workspace.add_matter_meeting(
        matter=matter, author=author, summary="Arutelu ministeeriumiga.", occurred_on=day
    )


#: name -> (route, body field, typed body, form, form id prefix, kind, sentence, service)
KINDS: dict[str, tuple[str, str, str, Any, str, str, str, Any]] = {
    "muu": (
        "add_other",
        "description",
        "Ministeeriumi taustaanalüüs homsest.",
        CompactOtherForm,
        "id_muu",
        EntryKind.OTHER,
        MUU_AHEAD,
        _add_muu,
    ),
    "kohtumine": (
        "add_meeting",
        "summary",
        "Kohtumine, mis on alles kokku lepitud.",
        CompactMeetingForm,
        "id_kohtumine",
        EntryKind.MEETING,
        KOHTUMINE_AHEAD,
        _add_kohtumine,
    ),
}
WHICH = sorted(KINDS)


def _post(client, which: str, matter: Matter, day_typed: str):
    route, field, text, *_ = KINDS[which]
    return client.post(
        reverse(f"matters:{route}", kwargs={"pk": matter.pk}),
        {field: text, "occurred_on": day_typed, "url": ""},
        headers=HX,
    )


def _entries(matter: Matter, which: str):
    return Entry.objects.filter(matter=matter, kind=KINDS[which][5])


def _form(which: str, day_typed: str):
    _, field, text, form_class, *_ = KINDS[which]
    return form_class({field: text, "occurred_on": day_typed, "url": ""})


def _frozen_at(monkeypatch, instant: dt.datetime) -> None:
    """Every `timezone.now()` — and so `timezone.localdate()` — answers ``instant``."""
    monkeypatch.setattr(timezone, "now", lambda: instant)


def test_the_sentences_are_the_services():
    """Worded as the send refusal is: «<what> kuupäev ei saa olla tulevikus.»"""
    from app.submissions.services import SENT_DATE_IN_THE_FUTURE

    assert workspace.OTHER_ENTRY_DAY_AHEAD == MUU_AHEAD
    assert workspace.MEETING_ENTRY_DAY_AHEAD == KOHTUMINE_AHEAD
    for sentence in (MUU_AHEAD, KOHTUMINE_AHEAD, SENT_DATE_IN_THE_FUTURE):
        assert sentence.endswith(" kuupäev ei saa olla tulevikus.")


# -- the form ---------------------------------------------------------------------


@pytest.mark.parametrize("which", WHICH)
@pytest.mark.parametrize("offset", [1, 7, 365 * 36])
def test_the_form_refuses_a_day_ahead_under_the_day_box(which, offset):
    form = _form(which, _et(_day(offset)))

    assert not form.is_valid()
    assert form.errors["occurred_on"] == [KINDS[which][6]]


@pytest.mark.parametrize("which", WHICH)
@pytest.mark.parametrize("offset", [0, -1, -400])
def test_the_form_accepts_today_and_any_past_day(which, offset):
    form = _form(which, _et(_day(offset)))

    assert form.is_valid(), form.errors
    assert form.cleaned_data["occurred_on"] == _day(offset)


# -- the service ------------------------------------------------------------------


@pytest.mark.parametrize("which", WHICH)
def test_the_service_refuses_a_day_ahead_and_writes_nothing(which, specialist):
    matter = factories.MatterFactory(owner=specialist)
    add = KINDS[which][7]

    with refused(KINDS[which][6]):
        add(matter, specialist, _day(1))

    assert not _entries(matter, which).exists()


@pytest.mark.parametrize("which", WHICH)
def test_the_service_accepts_today(which, specialist):
    matter = factories.MatterFactory(owner=specialist)

    KINDS[which][7](matter, specialist, _today())

    assert _entries(matter, which).get().occurred_at == start_of_local_day(_today())


# -- today is the Tallinn day -----------------------------------------------------

#: (the instant, the Tallinn day it is). EEST is UTC+3 until 25.10.2026.
BOUNDARY = [
    # 00:30 in Tallinn on the 17th is still the 16th in UTC.
    pytest.param(dt.datetime(2026, 10, 16, 21, 30, tzinfo=UTC), dt.date(2026, 10, 17), id="00:30"),
    # 23:59 in Tallinn on the 17th: the 18th has not begun.
    pytest.param(dt.datetime(2026, 10, 17, 20, 59, tzinfo=UTC), dt.date(2026, 10, 17), id="23:59"),
    # 00:15 on New Year's Day in Tallinn (EET, UTC+2) is 31.12 in UTC.
    pytest.param(dt.datetime(2026, 12, 31, 22, 15, tzinfo=UTC), dt.date(2027, 1, 1), id="newyear"),
]


@pytest.mark.parametrize("which", WHICH)
@pytest.mark.parametrize(("instant", "tallinn_day"), BOUNDARY)
def test_the_tallinn_day_is_today_on_the_form(monkeypatch, which, instant, tallinn_day):
    _frozen_at(monkeypatch, instant)
    assert timezone.localdate() == tallinn_day

    assert _form(which, _et(tallinn_day)).is_valid()
    tomorrow = _form(which, _et(tallinn_day + dt.timedelta(days=1)))
    assert not tomorrow.is_valid()
    assert tomorrow.errors["occurred_on"] == [KINDS[which][6]]


@pytest.mark.parametrize("which", WHICH)
@pytest.mark.parametrize(("instant", "tallinn_day"), BOUNDARY)
def test_the_tallinn_day_is_today_in_the_service(
    monkeypatch, which, specialist, instant, tallinn_day
):
    matter = factories.MatterFactory(owner=specialist)
    _frozen_at(monkeypatch, instant)
    add = KINDS[which][7]

    with refused(KINDS[which][6]):
        add(matter, specialist, tallinn_day + dt.timedelta(days=1))
    add(matter, specialist, tallinn_day)

    entry = _entries(matter, which).get()
    assert entry.occurred_at == start_of_local_day(tallinn_day)


# -- over HTTP --------------------------------------------------------------------


@pytest.mark.parametrize("which", WHICH)
def test_a_post_dated_ahead_is_400_keeps_what_was_typed_and_stores_nothing(
    which, signed_in, specialist
):
    matter = factories.MatterFactory(owner=specialist)
    typed = _et(_day(1))

    response = _post(signed_in, which, matter, typed)

    assert response.status_code == 400
    body = response.content.decode()
    prefix = KINDS[which][4]
    assert f'id="{prefix}_occurred_on_error">{KINDS[which][6]}<' in body
    box = re.search(rf'<input[^>]*id="{prefix}_occurred_on"[^>]*>', body).group(0)
    assert typed in box
    assert KINDS[which][2] in body
    assert not _entries(matter, which).exists()


@pytest.mark.parametrize("which", WHICH)
def test_a_post_dated_today_is_saved(which, signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    response = _post(signed_in, which, matter, _et(_today()))

    assert response.status_code == 200
    assert _entries(matter, which).get().occurred_at == start_of_local_day(_today())


# -- what the activity surfaces see (the audit's A01 probe, flipped) --------------


@pytest.fixture
def quiet_matter(specialist):
    """Last work 60 days ago — quiet; the fixture `tests/test_work_activity.py` uses."""
    matter = factories.MatterFactory(owner=specialist)
    factories.EntryFactory(
        matter=matter, author=specialist, occurred_at=timezone.now() + dt.timedelta(days=OLD)
    )
    return matter


def _last(matter: Matter, user: Any) -> tuple[int, str] | None:
    fact = activity_for_matter(Matter.all_objects.get(pk=matter.pk), user)
    if fact is None:
        return None
    return (fact.occurred_on - _today()).days, fact.basis


@pytest.mark.parametrize("which", WHICH)
def test_a_day_ahead_cannot_become_the_last_activity(
    which, signed_in, quiet_matter, specialist, department_head
):
    """JQA-001: refused, so «Viimane tegevus», «Muutusteta 30 p» and Osakond stand."""
    ahead = _day(5)

    response = _post(signed_in, which, quiet_matter, _et(ahead))

    assert response.status_code == 400
    assert not _entries(quiet_matter, which).exists()
    assert _last(quiet_matter, department_head) == (OLD, ActivityBasis.ENTRY)
    assert quiet_matter.pk in set(wi.quiet_matters(department_head, _today()))
    changed = _matters_changed_by_owner(department_head, ahead, ahead)
    assert changed.get(quiet_matter.owner_id, 0) == 0


def test_a_typo_year_cannot_head_recent_work(signed_in, specialist, department_head):
    """2062 for 2026 is refused; the meeting held today is the Teema's latest work."""
    from app.matters.my_work import recent_entries
    from app.matters.overview import FEED_ALL, activity_feed

    matter = factories.MatterFactory(owner=specialist)
    typo = dt.date(2062, _today().month, min(_today().day, 28))

    assert _post(signed_in, "muu", matter, _et(typo)).status_code == 400
    assert _post(signed_in, "kohtumine", matter, _et(_today())).status_code == 200

    assert [entry.kind for entry in recent_entries(specialist, specialist)] == [EntryKind.MEETING]
    first = activity_feed(department_head, _today(), FEED_ALL)[0]
    assert timezone.localtime(first.when).date() == _today()
    assert _last(matter, department_head) == (0, ActivityBasis.ENTRY)
