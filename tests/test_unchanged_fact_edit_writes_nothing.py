"""An unchanged `Muuda` on a fact writes nothing (bug-bounty C-03).

`Oluline tähtaeg`, `Jõustumine` and `Töövõit` saved every correction — even the
edit form drawn from the record and saved as it was — so an unchanged
`Salvesta` wrote a «…muudetud» row into the Teema's history that described no
change, and moved `updated_at`, which turned a colleague's open edit form of
the same fact into a 409 conflict. Every other correction in the product
returns early when nothing moved (`workflow.services.change_planned_action`).

Each fact kind is saved here exactly as its edit page draws it: the form's own
fields scraped from the rendered HTML, so the period widgets (exact day,
month, quarter, year) round-trip through the real form. A real change still
writes its event.
"""

from __future__ import annotations

from datetime import date
from html.parser import HTMLParser
from typing import Any

import pytest
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.intelligence.enums import EffectiveDateKind
from app.intelligence.services import (
    add_confirmed_work_victory,
    add_effective_date,
    add_important_date,
    add_work_victory_candidate,
    fact_revision,
)
from app.intelligence.views import NOTHING_CHANGED
from app.workflow.dates import quarter_bounds
from app.workflow.enums import DatePrecision
from tests import factories

pytestmark = pytest.mark.django_db


class _FormScraper(HTMLParser):
    """The fields of the one form whose action ends in `/muuda/`, as a browser posts them."""

    def __init__(self) -> None:
        super().__init__()
        self.fields: list[tuple[str, str]] = []
        self._in_form = False
        self._select: str | None = None
        self._textarea: str | None = None
        self._text = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {key: value or "" for key, value in attrs}
        if tag == "form":
            self._in_form = a.get("action", "").endswith("/muuda/")
            return
        if not self._in_form:
            return
        name = a.get("name")
        if tag == "input" and name:
            kind = a.get("type", "text")
            if kind in {"checkbox", "radio"} and "checked" not in a:
                return
            if kind in {"submit", "button", "file"}:
                return
            self.fields.append((name, a.get("value", "on" if kind == "checkbox" else "")))
        elif tag == "select" and name:
            self._select = name
        elif tag == "option" and self._select is not None and "selected" in a:
            self.fields.append((self._select, a.get("value", "")))
        elif tag == "textarea" and name:
            self._textarea, self._text = name, ""

    def handle_data(self, data: str) -> None:
        if self._textarea is not None:
            self._text += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._in_form = False
        elif tag == "select":
            self._select = None
        elif tag == "textarea" and self._textarea is not None:
            # A browser drops the one newline right after <textarea>.
            self.fields.append((self._textarea, self._text.removeprefix("\n")))
            self._textarea = None


def _as_drawn(client, url: str) -> dict[str, Any]:
    scraper = _FormScraper()
    scraper.feed(client.get(url).content.decode())
    posted: dict[str, Any] = {}
    for name, value in scraper.fields:
        posted.setdefault(name, []).append(value)
    assert posted, "the edit page draws its form"
    return posted


def _edit_url(kind: str, record) -> str:
    return reverse(
        f"intelligence:edit_{kind}", kwargs={"matter_id": record.matter_id, "pk": record.pk}
    )


def _changed_events(event_type: str, record) -> int:
    return ChangeEvent.objects.filter(event_type=event_type, object_id=record.pk).count()


def _important(matter, actor, precision: str):
    if precision == DatePrecision.QUARTER:
        start, end = quarter_bounds(2027, 3)
    elif precision == DatePrecision.MONTH:
        start, end = date(2027, 5, 1), date(2027, 5, 31)
    elif precision == DatePrecision.YEAR:
        start, end = date(2027, 1, 1), date(2027, 12, 31)
    else:
        start = end = date(2027, 5, 14)
    return add_important_date(
        matter=matter,
        title="Kooskõlastusring",
        date_value=start,
        period_end=end,
        date_precision=precision,
        note="Ministeeriumi teatel",
        actor=actor,
    )


def _effective(matter, actor, kind: str):
    if kind == EffectiveDateKind.KNOWN_DATE:
        return add_effective_date(
            matter=matter,
            kind=kind,
            date_value=date(2027, 1, 1),
            period_end=date(2027, 1, 1),
            description="Seadus jõustub",
            note="RT avaldus",
            source_url="https://www.riigiteataja.ee/akt/1",
            actor=actor,
        )
    return add_effective_date(matter=matter, kind=kind, description="Seadus jõustub", actor=actor)


def _victory(matter, actor, *, candidate: bool):
    if candidate:
        return add_work_victory_candidate(
            matter=matter,
            title="Ettepanek arvestati",
            detail="Seletuskirjas",
            note="Ettepaneku tegija märkus",
            actor=actor,
        )
    return add_confirmed_work_victory(
        matter=matter, title="Ettepanek arvestati", detail="Seletuskirjas", actor=actor
    )


CASES = [
    pytest.param(
        "important_date",
        lambda m, a: _important(m, a, DatePrecision.EXACT),
        ChangeEventType.IMPORTANT_DATE_CHANGED,
        id="oluline-tahtaeg-paev",
    ),
    pytest.param(
        "important_date",
        lambda m, a: _important(m, a, DatePrecision.MONTH),
        ChangeEventType.IMPORTANT_DATE_CHANGED,
        id="oluline-tahtaeg-kuu",
    ),
    pytest.param(
        "important_date",
        lambda m, a: _important(m, a, DatePrecision.QUARTER),
        ChangeEventType.IMPORTANT_DATE_CHANGED,
        id="oluline-tahtaeg-kvartal",
    ),
    pytest.param(
        "important_date",
        lambda m, a: _important(m, a, DatePrecision.YEAR),
        ChangeEventType.IMPORTANT_DATE_CHANGED,
        id="oluline-tahtaeg-aasta",
    ),
    pytest.param(
        "effective_date",
        lambda m, a: _effective(m, a, EffectiveDateKind.KNOWN_DATE),
        ChangeEventType.EFFECTIVE_DATE_CHANGED,
        id="joustumine-kuupaev",
    ),
    pytest.param(
        "effective_date",
        lambda m, a: _effective(m, a, EffectiveDateKind.UNKNOWN),
        ChangeEventType.EFFECTIVE_DATE_CHANGED,
        id="joustumine-teadmata",
    ),
    pytest.param(
        "work_victory",
        lambda m, a: _victory(m, a, candidate=False),
        ChangeEventType.WORK_VICTORY_CHANGED,
        id="toovoit",
    ),
    pytest.param(
        "work_victory",
        lambda m, a: _victory(m, a, candidate=True),
        ChangeEventType.WORK_VICTORY_CHANGED,
        id="toovoidu-kandidaat-markusega",
    ),
]


@pytest.mark.parametrize(("kind", "make", "event_type"), CASES)
def test_an_unchanged_muuda_writes_no_event_and_keeps_the_version(
    signed_in, specialist, kind, make, event_type
):
    record = make(factories.MatterFactory(owner=specialist), specialist)
    stored = type(record).objects.get(pk=record.pk)
    url = _edit_url(kind, record)

    response = signed_in.post(url, _as_drawn(signed_in, url))

    assert response.status_code == 302
    after = type(record).objects.get(pk=record.pk)
    assert after.updated_at == stored.updated_at
    assert _changed_events(event_type, record) == 0
    assert after.note == stored.note
    shown = [str(m) for m in signed_in.get(response["Location"]).context["messages"]]
    assert NOTHING_CHANGED in shown
    assert not any("muudetud" in message for message in shown)


@pytest.mark.parametrize(("kind", "make", "event_type"), CASES)
def test_a_colleagues_open_form_still_saves_after_an_unchanged_muuda(
    client, signed_in, specialist, kind, make, event_type
):
    """The 409 the false write caused: the colleague's version is still the stored one."""
    record = make(factories.MatterFactory(owner=specialist), specialist)
    url = _edit_url(kind, record)
    colleague = factories.UserFactory()
    client.force_login(colleague)
    colleagues_form = _as_drawn(client, url)

    signed_in.post(url, _as_drawn(signed_in, url))

    text_field = "description" if kind == "effective_date" else "title"
    colleagues_form[text_field] = ["Kolleegi parandus"]
    response = client.post(url, colleagues_form)

    assert response.status_code == 302
    after = type(record).objects.get(pk=record.pk)
    assert getattr(after, text_field) == "Kolleegi parandus"
    assert _changed_events(event_type, record) == 1


@pytest.mark.parametrize(("kind", "make", "event_type"), CASES)
def test_a_real_change_still_writes_its_event(signed_in, specialist, kind, make, event_type):
    record = make(factories.MatterFactory(owner=specialist), specialist)
    before = fact_revision(type(record).objects.get(pk=record.pk))
    url = _edit_url(kind, record)
    posted = _as_drawn(signed_in, url)
    posted["note" if kind != "work_victory" else "detail"] = ["Uus sõnastus"]

    response = signed_in.post(url, posted)

    assert response.status_code == 302
    after = type(record).objects.get(pk=record.pk)
    assert fact_revision(after) != before
    assert _changed_events(event_type, record) == 1
    shown = [str(m) for m in signed_in.get(response["Location"]).context["messages"]]
    assert NOTHING_CHANGED not in shown
