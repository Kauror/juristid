"""The owner's acceptance fixes of 2026-10-19 for `Muu` and `Menetluse kulg` (docs/adr/0172).

* **Fix 4** — a fresh `+ Lisa · Muu` form says today's *Tallinn* day; a refusal
  keeps what was typed; a day after today is still refused; nothing saved moves.
* **Fix 5** — a saved `Muu` is in `Tegevused` at once, at the head of its day
  (above «Teema loodud», not below it), its closed line carrying its first
  words; on a busy day it is still in the first page; a correction renews the
  words and a removal takes the row and its count away.
* **Fix 6** — a watched `Oluline tähtaeg` and a `Töövõit` are event markers on
  `Menetluse kulg` — their title, their kind in words, their own date — and
  never stages; cancelled, unrealised, removed and restricted ones draw none.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.dates import format_estonian_date, start_of_local_day
from app.intelligence.enums import FactStatus, ImportantDateKind, WorkVictoryStatus
from app.intelligence.models import MatterImportantDate, MatterWorkVictory
from app.matters.entry_enums import EntryKind
from app.matters.forms import CompactOtherForm
from app.matters.legal_process import KIND_EVENT
from app.matters.models import Entry
from app.matters.process_timeline import process_steps
from app.matters.removal import record_revision
from app.matters.services import add_entry, entry_revision_token
from app.matters.timeline import entry_chronology_moment, matter_timeline
from app.workflow.enums import DatePrecision
from tests import factories

pytestmark = pytest.mark.django_db


def _post(client, name: str, matter, data: dict, **kwargs):
    return client.post(
        reverse(f"matters:{name}", kwargs={"pk": matter.pk, **kwargs}),
        data,
        headers={"HX-Request": "true"},
    )


def _page(client, matter) -> str:
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    return response.content.decode()


def _muu_box(html: str) -> str:
    return re.search(r'<input[^>]*id="id_muu_occurred_on"[^>]*>', html).group(0)


def _today() -> str:
    return format_estonian_date(timezone.localdate())


def _save_muu(client, matter, description: str, day: str | None = None):
    return _post(
        client,
        "add_other",
        matter,
        {"description": description, "url": "", "occurred_on": day or _today()},
    )


def _rows(html: str) -> list[str]:
    """The `Tegevused` rows of a page or a swap, in order, as text."""
    return [
        re.sub(r"<[^>]+>", " ", chunk)
        for chunk in re.findall(r'<article class="uxtl__item.*?</article>', html, re.DOTALL)
    ]


# ---------------------------------------------------------------------------
# Fix 4 — `Millal?` says today
# ---------------------------------------------------------------------------


def test_4_a_fresh_muu_form_says_today(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    box = _muu_box(_page(signed_in, matter))

    assert f'value="{_today()}"' in box
    assert 'data-today-default="true"' in box
    assert "data-datepicker" in box


@pytest.mark.parametrize(
    ("utc_moment", "tallinn_day"),
    [
        # 23:59 in Tallinn (EEST, +3) is still the 19th …
        (dt.datetime(2026, 10, 19, 20, 59, tzinfo=dt.UTC), dt.date(2026, 10, 19)),
        # … and 00:30 is the 20th, while UTC still says the 19th.
        (dt.datetime(2026, 10, 19, 21, 30, tzinfo=dt.UTC), dt.date(2026, 10, 20)),
        # Winter time (EET, +2): 23:30 UTC on 31.12 is already 1.1 in Tallinn.
        (dt.datetime(2026, 12, 31, 22, 30, tzinfo=dt.UTC), dt.date(2027, 1, 1)),
    ],
)
def test_4_the_default_is_the_tallinn_day_not_the_utc_one(monkeypatch, utc_moment, tallinn_day):
    monkeypatch.setattr(timezone, "now", lambda: utc_moment)

    assert CompactOtherForm().initial["occurred_on"] == tallinn_day


def test_4_a_refusal_keeps_the_day_that_was_typed(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    earlier = format_estonian_date(timezone.localdate() - dt.timedelta(days=5))

    refused = _post(
        signed_in, "add_other", matter, {"description": "", "url": "", "occurred_on": earlier}
    )

    assert refused.status_code == 400
    box = _muu_box(refused.content.decode())
    assert f'value="{earlier}"' in box
    # A redisplayed refusal is not a fresh form: the page must not renew it.
    assert "data-today-default" not in box
    assert not Entry.objects.filter(matter=matter, kind=EntryKind.OTHER).exists()


def test_4_a_day_after_today_is_still_refused(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    tomorrow = format_estonian_date(timezone.localdate() + dt.timedelta(days=1))

    refused = _save_muu(signed_in, matter, "Homne asi.", tomorrow)

    assert refused.status_code == 400
    assert "Toimumise kuupäev ei saa olla tulevikus." in refused.content.decode()
    assert not Entry.objects.filter(matter=matter, kind=EntryKind.OTHER).exists()


def test_4_a_saved_entry_keeps_its_day(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    earlier = timezone.localdate() - dt.timedelta(days=9)
    assert (
        _save_muu(signed_in, matter, "Varasem materjal.", format_estonian_date(earlier)).status_code
        == 200
    )
    entry = Entry.objects.get(matter=matter, kind=EntryKind.OTHER)

    assert entry.occurred_at == start_of_local_day(earlier)
    # The next fresh form says today again; the saved day is untouched.
    assert f'value="{_today()}"' in _muu_box(_page(signed_in, matter))
    entry.refresh_from_db()
    assert entry.occurred_at == start_of_local_day(earlier)


# ---------------------------------------------------------------------------
# Fix 5 — a `Muu` in `Tegevused`
# ---------------------------------------------------------------------------


def test_5_a_day_dated_entry_sorts_at_the_end_of_its_day(specialist):
    matter = factories.MatterFactory(owner=specialist)
    day = dt.date(2026, 10, 19)
    muu = add_entry(
        matter=matter,
        body="Muu.",
        author=specialist,
        kind=EntryKind.OTHER,
        occurred_at=start_of_local_day(day),
    )
    meeting = add_entry(
        matter=matter,
        body="Kohtumine.",
        author=specialist,
        kind=EntryKind.MEETING,
        occurred_at=start_of_local_day(day),
    )
    timed = add_entry(
        matter=matter,
        body="Muu kellaajaga.",
        author=specialist,
        kind=EntryKind.OTHER,
        occurred_at=start_of_local_day(day) + dt.timedelta(hours=9),
    )
    note = add_entry(
        matter=matter, body="Märge.", author=specialist, occurred_at=start_of_local_day(day)
    )

    for entry in (muu, meeting):
        moment = timezone.localtime(entry_chronology_moment(entry))
        assert moment.date() == day
        assert (moment.hour, moment.minute) == (23, 59)
    # An entry with a real time keeps it, and an ordinary note is untouched.
    assert entry_chronology_moment(timed) == timed.occurred_at
    assert entry_chronology_moment(note) == note.occurred_at


def test_5_a_saved_muu_is_in_the_swap_above_teema_loodud_with_its_words(signed_in, specialist):
    """The owner's screen: a Muu added minutes after the Teema read below it."""
    matter = factories.MatterFactory(owner=specialist)
    _page(signed_in, matter)

    response = _save_muu(signed_in, matter, "Ministeeriumi kiri selgitas ajakava.")

    assert response.status_code == 200
    rows = _rows(response.content.decode())
    muu = next(i for i, row in enumerate(rows) if "Ministeeriumi kiri selgitas" in row)
    assert "Muu" in rows[muu]
    created = [i for i, row in enumerate(rows) if "Teema loodud" in row]
    if created:
        assert muu < created[0], rows
    # Its closed line says what it is about.
    gist = re.search(
        r'<span class="uxtl__gist" id="sissekanne-[^"]+-sisu-algus">([^<]*)</span>',
        response.content.decode(),
    )
    assert gist and gist.group(1) == "Ministeeriumi kiri selgitas ajakava."
    # And it stays there after a reload.
    assert any("Ministeeriumi kiri selgitas" in row for row in _rows(_page(signed_in, matter)))


def test_5_a_long_description_is_cut_on_the_closed_line(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    words = "Väga pikk kirjeldus " * 12

    _save_muu(signed_in, matter, words)

    gist = re.search(r'class="uxtl__gist"[^>]*>([^<]*)<', _page(signed_in, matter)).group(1)
    assert gist.endswith("…")
    assert len(gist) <= 90


def test_5_a_muu_today_is_on_the_first_page_of_a_busy_day(signed_in, specialist):
    """Keyed on its midnight it was the 31st row of a day with thirty notes."""
    matter = factories.MatterFactory(owner=specialist)
    now = timezone.now()
    _save_muu(signed_in, matter, "Tänane taustamaterjal.")
    for index in range(35):
        add_entry(
            matter=matter,
            body=f"Märge {index}.",
            author=specialist,
            occurred_at=now - dt.timedelta(minutes=index),
        )

    first, more = matter_timeline(matter=matter, user=specialist, limit=30)

    assert more
    assert any(item.entry is not None and item.entry.kind == EntryKind.OTHER for item in first), (
        "the Muu must be among the first rows"
    )


def test_5_paging_shows_every_row_once(specialist):
    matter = factories.MatterFactory(owner=specialist)
    now = timezone.now()
    days = [timezone.localdate() - dt.timedelta(days=offset) for offset in range(4)]
    for day in days:
        add_entry(
            matter=matter,
            body=f"Muu {day}.",
            author=specialist,
            kind=EntryKind.OTHER,
            occurred_at=start_of_local_day(day),
        )
    for index in range(40):
        add_entry(
            matter=matter,
            body=f"Märge {index}.",
            author=specialist,
            occurred_at=now - dt.timedelta(hours=index * 2),
        )

    seen: list[str] = []
    offset = 0
    while True:
        page, more = matter_timeline(matter=matter, user=specialist, limit=10, offset=offset)
        seen.extend(str(item.entry.pk) for item in page if item.entry is not None)
        if not more:
            break
        offset += 10

    entries = Entry.objects.filter(matter=matter)
    assert sorted(seen) == sorted(str(pk) for pk in entries.values_list("pk", flat=True))
    assert len(seen) == len(set(seen))


def test_5_a_correction_renews_the_words_out_of_band(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _save_muu(signed_in, matter, "Esialgne kirjeldus.")
    entry = Entry.objects.get(matter=matter, kind=EntryKind.OTHER)

    response = _post(
        signed_in,
        "edit_entry",
        matter,
        {"body": "Parandatud kirjeldus.", "url": "", "revision": entry_revision_token(entry)},
        entry_id=entry.pk,
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert (
        f'<span class="uxtl__gist" id="sissekanne-{entry.pk}-sisu-algus" hx-swap-oob="true">'
        "Parandatud kirjeldus.</span>"
    ) in body
    entry.refresh_from_db()
    assert entry.occurred_at == start_of_local_day(timezone.localdate())


def test_5_a_removal_takes_the_row_and_its_count_away(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _save_muu(signed_in, matter, "Eemaldatav materjal.")
    entry = Entry.objects.get(matter=matter, kind=EntryKind.OTHER)
    rows_before = len(_rows(_page(signed_in, matter)))

    response = _post(
        signed_in,
        "remove_record",
        matter,
        {"revision": record_revision(entry)},
        kind="sissekanne",
        record_id=entry.pk,
    )

    assert response.status_code == 200
    after = _page(signed_in, matter)
    assert "Eemaldatav materjal." not in after
    assert len(_rows(after)) == rows_before - 1


# ---------------------------------------------------------------------------
# Fix 6 — events on `Menetluse kulg`
# ---------------------------------------------------------------------------


def _deadline(matter, title: str, when: dt.date, **extra) -> MatterImportantDate:
    return MatterImportantDate.objects.create(
        matter=matter,
        title=title,
        kind=extra.pop("kind", ImportantDateKind.OTHER),
        date_value=when,
        period_end=when,
        date_precision=DatePrecision.EXACT.value,
        status=extra.pop("status", FactStatus.ACTIVE),
        **extra,
    )


def _victory(matter, title: str, when: dt.date, **extra) -> MatterWorkVictory:
    return MatterWorkVictory.objects.create(
        matter=matter,
        title=title,
        period_date=when,
        period_end=when,
        date_precision=DatePrecision.EXACT.value,
        status=extra.pop("status", WorkVictoryStatus.CONFIRMED),
        confirmed_at=extra.pop("confirmed_at", timezone.now()),
        **extra,
    )


def _events(matter, user):
    return [step for step in process_steps(matter=matter, user=user) if step.is_event]


def test_6_the_owners_two_events_are_markers(normal_matter, specialist):
    today = timezone.localdate()
    _deadline(normal_matter, "isiklik põhjus", today + dt.timedelta(days=2))
    _victory(normal_matter, "Ministeerium loobus aruandlusest", today)

    events = {step.label: step for step in _events(normal_matter, specialist)}

    deadline = events["isiklik põhjus"]
    assert deadline.event_label == "Eesolev tähtaeg"
    assert deadline.sort_on == today + dt.timedelta(days=2)
    assert deadline.display == format_estonian_date(today + dt.timedelta(days=2))
    victory = events["Ministeerium loobus aruandlusest"]
    assert victory.event_label == "Töövõit"
    assert victory.sort_on == today


def test_6_what_is_not_a_standing_event_draws_none(normal_matter, specialist):
    today = timezone.localdate()
    _deadline(
        normal_matter, "tühistatud", today + dt.timedelta(days=5), status=FactStatus.CANCELLED
    )
    removed = _deadline(normal_matter, "eemaldatud", today + dt.timedelta(days=6))
    removed.removed_at = timezone.now()
    removed.save(update_fields=["removed_at"])
    _victory(normal_matter, "teostumata", today, status=WorkVictoryStatus.NOT_REALIZED)
    _deadline(
        normal_matter,
        "ülevõtmine",
        today + dt.timedelta(days=30),
        kind=ImportantDateKind.TRANSPOSITION_DEADLINE,
    )

    labels = [step.label for step in _events(normal_matter, specialist)]

    assert labels == []
    # The transposition deadline keeps its own column, under its own name.
    assert "Ülevõtmise tähtaeg" in [
        step.label for step in process_steps(matter=normal_matter, user=specialist)
    ]


def test_6_a_restricted_event_draws_nothing_for_who_may_not_see_it(
    normal_matter, specialist, reader, administrator
):
    # docs/adr/0181: a READER may see it (draws it as the owner does); an
    # administrator without a grant may not.
    from app.core.enums import Visibility

    _deadline(
        normal_matter,
        "salajane tähtaeg",
        timezone.localdate() + dt.timedelta(days=3),
        visibility_override=Visibility.RESTRICTED,
    )

    assert "salajane tähtaeg" in [step.label for step in _events(normal_matter, specialist)]
    assert "salajane tähtaeg" in [step.label for step in _events(normal_matter, reader)]
    assert "salajane tähtaeg" not in [step.label for step in _events(normal_matter, administrator)]


def test_6_markers_are_events_beside_unchanged_stages(signed_in, specialist, normal_matter):
    before = _page(signed_in, normal_matter)
    phases_before = re.findall(
        r'tl-step--phase"[^>]*>\s*<span class="tl-step__dot"[^>]*></span>\s*'
        r'<span class="tl-step__what">([^<]+)</span>',
        before,
    )
    today = timezone.localdate()
    _deadline(normal_matter, "isiklik põhjus", today + dt.timedelta(days=2))
    _victory(normal_matter, "Töövõidu pealkiri", today)

    after = _page(signed_in, normal_matter)

    strip = after[after.index('class="tl-strip"') :]
    strip = strip[: strip.index("</div>")]
    assert strip.count("tl-step--event") == 2
    assert '<span class="tl-step__type">Eesolev tähtaeg</span>' in strip
    assert '<span class="tl-step__type">Töövõit</span>' in strip
    assert "isiklik põhjus" in strip and "Töövõidu pealkiri" in strip
    phases_after = re.findall(
        r'tl-step--phase"[^>]*>\s*<span class="tl-step__dot"[^>]*></span>\s*'
        r'<span class="tl-step__what">([^<]+)</span>',
        after,
    )
    assert phases_after == phases_before


def test_6_the_rail_follows_a_cancellation(normal_matter, specialist):
    from app.matters.legal_process import legal_process_rail, matter_rail

    record = _deadline(normal_matter, "isiklik põhjus", timezone.localdate() + dt.timedelta(days=2))

    def kinds():
        rail = legal_process_rail(matter=normal_matter, user=specialist)
        return {
            step.label: step.kind
            for step in matter_rail(
                matter=normal_matter,
                user=specialist,
                rail=rail,
                milestones=process_steps(matter=normal_matter, user=specialist),
            )
        }

    assert kinds().get("isiklik põhjus") == KIND_EVENT
    record.status = FactStatus.CANCELLED
    record.save(update_fields=["status"])
    assert "isiklik põhjus" not in kinds()
