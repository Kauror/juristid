"""Tehtud's «Vali periood…» says why a typed range was refused (B-06).

The 2026-10-23 bug-bounty typed `31.02.2026` — and, separately, an end before
its start — into Osakond's Tehtud period control. Both silently became the
default seven days: the boxes were refilled with the fallback's dates, the
popover closed, nothing said a word, although `period_from`'s own docstring
promised the refused dates would be shown again. Muudatused, over the same
parser, already said why (tests/test_department_changes.py).

`period_from` now carries the reason and the two strings as typed on the
fallback window, and the control opens on them with Muudatused' sentence.
"""

from __future__ import annotations

import re
from datetime import date

import pytest
from django.urls import reverse

from app.matters import department_dashboard as dd

TODAY = date(2026, 10, 13)


def period_control(body: str) -> str:
    """The opening tag of Tehtud's «Vali periood…» popover."""
    found = re.search(r'<details class="uxdg__period"[^>]*>', body)
    assert found, "no period control on the page"
    return found.group(0)


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        ("31.02.2026", "1.1.2026", dd.CUSTOM_RANGE_UNREADABLE),
        ("1.12.2026", "1.11.2026", dd.CUSTOM_RANGE_REVERSED),
        ("", "1.11.2026", dd.CUSTOM_RANGE_INCOMPLETE),
    ],
)
def test_a_refused_range_falls_back_and_keeps_what_was_typed(start, end, message):
    period = dd.period_from({"periood": "vahemik", "alates": start, "kuni": end}, TODAY)

    assert (period.key, period.start, period.end) == (
        dd.PERIOD_7,
        dd.last_days(TODAY, 7),
        TODAY,
    )
    assert period.refused == message
    assert (period.typed_start, period.typed_end) == (start, end)
    # No chip claims the reader chose the fallback.
    assert not any(option.active for option in dd.period_options(period))


def test_a_usable_range_and_a_named_window_carry_no_refusal():
    chosen = dd.period_from(
        {"periood": "vahemik", "alates": "1.9.2026", "kuni": "30.9.2026"}, TODAY
    )
    assert (chosen.key, chosen.start, chosen.end) == (
        dd.PERIOD_CUSTOM,
        date(2026, 9, 1),
        date(2026, 9, 30),
    )
    assert chosen.refused == ""

    named = dd.period_from({"periood": "30"}, TODAY)
    assert named.refused == ""
    assert [option.active for option in dd.period_options(named)].count(True) == 1


@pytest.mark.django_db
def test_osakond_reopens_the_period_control_on_the_refused_dates(client, department_head):
    client.force_login(department_head)

    response = client.get(
        reverse("matters:department"),
        {"periood": "vahemik", "liik": "koik", "alates": "31.02.2026", "kuni": "1.1.2026"},
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert f"{dd.CUSTOM_RANGE_UNREADABLE} Näitan vaikimisi perioodi." in body
    assert "data-period-error" in body
    assert 'name="alates" value="31.02.2026"' in body
    assert 'name="kuni" value="1.1.2026"' in body
    assert re.search(r"\bopen\b", period_control(body))
    assert response.context["page"].digest.period.key == dd.PERIOD_7


@pytest.mark.django_db
def test_osakond_draws_no_refusal_for_a_named_window(client, department_head):
    client.force_login(department_head)

    response = client.get(reverse("matters:department"), {"periood": "14"})

    body = response.content.decode()
    assert "data-period-error" not in body
    assert not re.search(r"\bopen\b", period_control(body))
