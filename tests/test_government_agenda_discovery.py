"""BUG-001: the 26.11.2026 agenda, published and reported «not yet published».

The Government published «Valitsuse 26.11.26 istungi kommenteeritud päevakord» on
25.11.2026 at 18:14 at ``/uudised/valitsuse-261126-istungi-kommenteeritud-paevakord-0``;
the plain address held an unpublished page and answered 403. r94/#730 guessed only
the plain address (and a long form), took the 403 for «not yet published» and the
night ended as a clean check. Now the agenda is found through the site's own
listings (homepage, sitemap), and the guesses are a bounded fallback that also
tries ``-0`` and ``-1``.

Everything here answers from the public pages as they were on 26.11.2026, trimmed,
in tests/fixtures/valitsus/: the agenda (24 items), the 403 page, the homepage after
the session (no agenda link any more) and the sitemap's newest page. Nothing
contacts valitsus.ee or a model.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.test import override_settings

from app.core import nightly_outcome as no
from app.register_watch import agenda
from app.register_watch.models import (
    AgendaCheckState,
    AgendaItem,
    AgendaItemStatus,
    CandidateNotice,
    GovernmentAgenda,
    GovernmentAgendaCheck,
    GovernmentAgendaRevision,
    Strength,
)
from app.register_watch.sources import valitsus
from app.register_watch.sources.http import FetchError
from tests import factories

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("department_head")]

FIXTURES = Path(__file__).parent / "fixtures" / "valitsus"
AGENDA = (FIXTURES / "agenda-261126-0.html").read_text(encoding="utf-8")
FORBIDDEN = (FIXTURES / "forbidden-403.html").read_text(encoding="utf-8")
HOMEPAGE = (FIXTURES / "homepage-261126.html").read_text(encoding="utf-8")
SITEMAP_INDEX = (FIXTURES / "sitemap-index.xml").read_text(encoding="utf-8")
SITEMAP_17 = (FIXTURES / "sitemap-page-17.xml").read_text(encoding="utf-8")
PREVIOUS = (FIXTURES / "agenda-191126.html").read_text(encoding="utf-8")

SESSION = dt.date(2026, 11, 26)  # Thursday
THURSDAY, FRIDAY, SATURDAY = SESSION, dt.date(2026, 11, 27), dt.date(2026, 11, 28)
PLAIN = "/uudised/valitsuse-261126-istungi-kommenteeritud-paevakord"
SUFFIXED = PLAIN + "-0"
PREVIOUS_PATH = "/uudised/valitsuse-191126-istungi-kommenteeritud-paevakord"
#: The sitemap page 16 is not needed: page 17 is a full page (≥ 300 entries)?
#: It is not in the trimmed fixture, so discovery also asks page 16 — answered empty.
SITEMAP_16 = '<?xml version="1.0"?><urlset></urlset>'


class Site:
    """valitsus.ee on 26.11.2026, as a table. A status is raised as `FetchError`."""

    def __init__(self, **overrides: object) -> None:
        self.answers: dict[str, object] = {
            valitsus.HOMEPAGE: HOMEPAGE,
            valitsus.SITEMAP: SITEMAP_INDEX,
            f"{valitsus.SITEMAP}?page=17": SITEMAP_17,
            f"{valitsus.SITEMAP}?page=16": SITEMAP_16,
            PLAIN: 403,
            SUFFIXED: AGENDA,
            PREVIOUS_PATH: PREVIOUS,
        }
        self.answers.update(overrides)
        self.asked: list[str] = []

    def __call__(self, host: str, path: str, **_: object) -> str:
        assert host == valitsus.HOST
        self.asked.append(path)
        answer = self.answers.get(path, 404)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, int):
            raise FetchError(f"{host} vastas {answer}", status=answer)
        return str(answer)


@pytest.fixture
def site(monkeypatch):
    table = Site()
    monkeypatch.setattr(valitsus, "get_text", table)
    return table


@pytest.fixture
def previous_week():
    """The 19.11.26 agenda, stored the week before: the control page."""
    return GovernmentAgenda.objects.create(
        url=f"https://valitsus.ee{PREVIOUS_PATH}",
        title="Valitsuse 19.11.26 istungi kommenteeritud päevakord",
        session_date=dt.date(2026, 11, 19),
        judged_at=dt.datetime(2026, 11, 19, tzinfo=dt.UTC),
    )


class NoModel:
    beta = SimpleNamespace(
        messages=SimpleNamespace(create=lambda **_: pytest.fail("the model was asked"))
    )


def _run(today: dt.date, **kwargs):
    kwargs.setdefault("use_ai", False)
    return agenda.run(today=today, ai_client=NoModel(), **kwargs)


def _classified(outcome):
    import io

    from app.register_watch.management.commands.watch_government_agenda import Command

    out = io.StringIO()
    Command(stdout=out)._machine_line(outcome)
    reported = json.loads(out.getvalue().split("OUTCOME-JSON ", 1)[1])
    with override_settings(REGISTER_WATCH_FETCH=True):
        return no.classify(
            "government-agenda",
            since=dt.datetime(2026, 11, 25, tzinfo=dt.UTC),
            rc=0,
            reported=reported,
        )


# -- 1–3: the plain address is 403, the listing names -0, the agenda is read ------------


def test_the_plain_address_is_refused_and_the_sitemap_names_the_suffixed_one():
    assert re.search(r"<h1[^>]*><span>Ligipääs keelatud", FORBIDDEN)
    found = valitsus.agenda_links(SITEMAP_17)
    assert (SUFFIXED, "261126") in found
    assert PLAIN not in [path for path, _ in found]  # never an unpublished page
    # The homepage after the session no longer links it: the sitemap is what counts.
    assert not [p for p, _ in valitsus.agenda_links(HOMEPAGE) if "261126" in p]


def test_the_26_november_agenda_is_discovered_read_and_stored(site, previous_week):
    outcome = _run(THURSDAY)

    (stored,) = outcome.stored
    assert stored.url == f"https://valitsus.ee{SUFFIXED}"
    assert stored.session_date == SESSION
    assert stored.published_at == dt.datetime(2026, 11, 25, 18, 14, tzinfo=agenda.TALLINN)
    assert outcome.errors == []
    (check,) = outcome.checks
    assert check.state == AgendaCheckState.STORED and check.agenda == stored
    assert check.discovery_ok is True and check.source_failures == 0
    # The listed address is read first; the 403 address is never needed.
    assert PLAIN not in site.asked
    assert _classified(outcome).outcome == no.SUCCEEDED


# -- 4: every item of the official page is accounted for -------------------------------


def test_all_24_items_are_accounted_for_against_the_source(site, previous_week):
    (stored,) = _run(THURSDAY).stored
    in_source = [int(n) for n in re.findall(r"<strong>\s*(\d{1,2})\.\s", AGENDA)]
    assert in_source == list(range(1, 25))
    items = list(stored.items.order_by("number"))
    assert [i.number for i in items] == in_source
    assert all(i.title and i.kind for i in items)
    # Item 7 names its presenter as «Esitajad:» (plural): read as well.
    assert items[6].title == "Tuleohutuse seaduse muutmise seaduse eelnõu"
    assert items[6].presenter == "siseminister Igor Taro"
    assert all(i.presenter for i in items)
    assert items[0].title == "Kosmosetegevuse seaduse eelnõu"
    assert items[0].kind == "Seaduse eelnõu" and items[0].text


# -- the fallback, when the listings cannot be read -----------------------------------


def test_with_the_sitemap_down_the_fallback_finds_minus_0_and_the_failure_stays_visible(
    site, previous_week
):
    site.answers[valitsus.SITEMAP] = 503
    outcome = _run(THURSDAY)

    (stored,) = outcome.stored
    assert stored.url.endswith(SUFFIXED)
    # The plain address was tried and refused before -0 answered.
    assert site.asked.index(PLAIN) < site.asked.index(SUFFIXED)
    # 9: the stored agenda does not hide the unreadable sitemap.
    assert any("sitemap.xml" in e for e in outcome.errors)
    (check,) = outcome.checks
    assert check.state == AgendaCheckState.STORED
    assert check.discovery_ok is False and check.source_failures >= 1
    result = _classified(outcome)
    assert result.outcome == no.PARTIAL and "could not be read" in result.reason


def test_a_403_with_unreadable_listings_is_unconfirmed_never_not_published(site, previous_week):
    site.answers.update({valitsus.SITEMAP: 503, SUFFIXED: 404})
    outcome = _run(dt.date(2026, 11, 25))  # Wednesday night, session still to come

    assert outcome.stored == []
    states = {c.state for c in outcome.checks}
    assert states == {AgendaCheckState.UNCONFIRMED}
    assert _classified(outcome).outcome == no.PARTIAL


def test_a_403_at_an_address_the_sitemap_lists_is_inaccessible_not_waiting(site, previous_week):
    site.answers[SUFFIXED] = 403
    outcome = _run(THURSDAY)
    (check,) = outcome.checks
    assert check.state == AgendaCheckState.INACCESSIBLE
    assert check.url.endswith(SUFFIXED)
    assert _classified(outcome).outcome == no.PARTIAL


def test_a_control_page_that_is_not_the_previous_agenda_is_no_control(site, previous_week):
    site.answers.update(
        {
            PREVIOUS_PATH: "<html><h1>Hooldustööd</h1></html>",
            SUFFIXED: 404,
            f"{valitsus.SITEMAP}?page=17": SITEMAP_16,
        }
    )
    outcome = _run(dt.date(2026, 11, 25))
    assert {c.state for c in outcome.checks} == {AgendaCheckState.UNCONFIRMED}


# -- 6–7: stored once, an unchanged rerun tells nobody again ----------------------------


@override_settings(REGISTER_SWEEP_AI=False)
def test_stored_once_and_an_unchanged_rerun_creates_nothing_new(
    site, previous_week, department_head
):
    _run(THURSDAY)
    notice = CandidateNotice.objects.get(recipient=department_head)
    assert notice.agendas == 1

    again = _run(FRIDAY)  # Friday's cycle: the week is still read for revisions
    assert again.stored == [] and again.revised == []
    assert again.checks[0].state == AgendaCheckState.ALREADY_STORED
    assert GovernmentAgenda.objects.filter(session_date=SESSION).count() == 1
    assert AgendaItem.objects.filter(agenda__session_date=SESSION).count() == 24
    assert CandidateNotice.objects.get(recipient=department_head).agendas == 1
    assert GovernmentAgendaRevision.objects.count() == 0
    # Saturday: the week is over; the stored page is not read again.
    site.asked.clear()
    _run(SATURDAY)
    assert SUFFIXED not in site.asked


def test_a_second_store_of_the_same_session_is_refused_by_the_database(site, previous_week):
    from django.db import IntegrityError, transaction

    (stored,) = _run(THURSDAY).stored
    with pytest.raises(IntegrityError), transaction.atomic():
        GovernmentAgenda.objects.create(
            url="https://valitsus.ee/x", title="x", session_date=SESSION
        )
    assert stored.pk


# -- 8: a revised agenda -----------------------------------------------------------------


def _revised_page() -> str:
    """The agenda as the Government might change it on the session morning: item 1's
    explanation reworded, item 23 withdrawn (24 becomes 23), a new item 24 added."""
    page = AGENDA.replace(
        "Uue seadusega luuakse Eestis kosmosetegevuse õiguslik raamistik.",
        "Uue seadusega luuakse Eestis kosmosetegevuse uus õiguslik raamistik.",
    )
    assert page != AGENDA
    start_23 = page.rfind("<p", 0, page.index("<strong>23."))
    start_24 = page.rfind("<p", 0, page.index("<strong>24."))
    page = page[:start_23] + page[start_24:].replace("<strong>24.", "<strong>23.", 1)
    end = page.index("</article>")
    added = (
        "<p><strong>24. Lisapunkt: ehitusseadustiku muutmise seaduse eelnõu</strong><br>"
        "Esitaja: taristuminister Kuldar Leis<br>Tüüp: Seaduse eelnõu</p>"
        "<p>Punkt lisati päevakorda istungi hommikul.</p>"
    )
    return page[:end] + added + page[end:]


@override_settings(REGISTER_SWEEP_AI=False)
def test_a_revised_agenda_keeps_evidence_and_decisions_and_tells_only_what_is_new(
    site, previous_week, department_head
):
    (stored,) = _run(THURSDAY).stored
    person = factories.UserFactory()
    first, second = stored.items.get(number=1), stored.items.get(number=2)
    withdrawn = stored.items.get(number=23)
    # Item 1 was judged by the model; item 24 was decided by a person.
    first.strength, first.reason = Strength.POSSIBLE, "varasem hinnang"
    first.save()
    old_24 = stored.items.get(number=24)
    old_24.status, old_24.decided_by = AgendaItemStatus.DISMISSED, person
    old_24.decided_at = dt.datetime(2026, 11, 26, 9, tzinfo=dt.UTC)
    old_24.save()
    decided_title = old_24.title
    notices_before = CandidateNotice.objects.get(recipient=department_head).agendas

    site.answers[SUFFIXED] = _revised_page()
    outcome = _run(FRIDAY)

    (revision,) = outcome.revised
    assert (revision.added, revision.changed, revision.removed) == (1, 1, 1)
    assert outcome.checks[0].state == AgendaCheckState.REVISED
    stored.refresh_from_db()
    assert stored.revised_at is not None and stored.judged_at is None
    # The judged item whose text changed is judged again; its earlier verdict is kept.
    first.refresh_from_db()
    assert first.strength == Strength.UNJUDGED and "uus õiguslik" in first.text
    before = next(c for c in revision.changes if c["change"] == "changed")
    assert before["before"]["strength"] == Strength.POSSIBLE
    assert "uus õiguslik" not in before["before"]["text"]
    # Unchanged items are untouched.
    second.refresh_from_db()
    assert second.revised_at is None
    # The person's decision stands (the item only moved up to 23) …
    old_24.refresh_from_db()
    assert old_24.status == AgendaItemStatus.DISMISSED and old_24.number == 23
    assert old_24.title == decided_title and old_24.source_removed_at is None
    assert any(c["change"] == "renumbered" and c["decided"] for c in revision.changes)
    # … and the withdrawn item is kept as evidence, not deleted.
    withdrawn.refresh_from_db()
    assert withdrawn.source_removed_at is not None
    live = stored.items.filter(source_removed_at__isnull=True)
    assert live.count() == 24 and live.get(number=24).title.startswith("Lisapunkt")
    assert GovernmentAgenda.objects.filter(session_date=SESSION).count() == 1
    # New information is told once …
    told = CandidateNotice.objects.get(recipient=department_head).agendas
    assert told == notices_before + 1
    # … and the same page read again is no revision and tells nobody.
    again = _run(FRIDAY)
    assert again.revised == [] and GovernmentAgendaRevision.objects.count() == 1
    assert CandidateNotice.objects.get(recipient=department_head).agendas == told


# -- 11: missing after the deadline ----------------------------------------------------


def test_nothing_published_by_fridays_cycle_is_missing(site, previous_week):
    site.answers.update({SUFFIXED: 404, PLAIN: 404, f"{valitsus.SITEMAP}?page=17": SITEMAP_16})
    outcome = _run(FRIDAY)
    assert outcome.missing
    result = _classified(outcome)
    assert result.outcome == no.PARTIAL and "missing" in result.reason


# -- 12: a model failure is never a classification --------------------------------------


class FailingModel:
    def __init__(self) -> None:
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **_: object) -> object:
        raise RuntimeError("model unavailable")


@override_settings(REGISTER_SWEEP_AI=True, REGISTER_SWEEP_AI_MODEL="claude-test")
def test_a_model_failure_leaves_items_unjudged_and_the_night_partial(site, previous_week):
    outcome = agenda.run(today=THURSDAY, weeks=2, ai_client=FailingModel())

    (stored,) = outcome.stored
    assert not stored.items.exclude(strength=Strength.UNJUDGED).exists()
    stored.refresh_from_db()
    assert stored.judged_at is None and stored.ai_attempts == 1
    result = _classified(outcome)
    assert result.outcome == no.PARTIAL and "model failures" in result.reason
    # The model's use is written on the week it was for, not repeated on every week.
    rows = {c.week: c for c in GovernmentAgendaCheck.objects.all()}
    this_week, last_week = dt.date(2026, 11, 23), dt.date(2026, 11, 16)
    assert rows[this_week].ai_failures == 1
    assert rows[last_week].ai_failures == 0 and rows[last_week].ai_calls == 0


# -- r96 integration: discovery under the nightly step's deadline (SEC-010, ADR 0231) ---


def test_a_spent_run_deadline_ends_discovery_and_lookup_as_source_failures_unread():
    """The real `sources.http` door, no network: with the run's time spent, discovery
    reads nothing and records why, and a lookup is a source failure — never «not yet
    published» and never «not found»."""
    from app.register_watch.sources import http

    with override_settings(REGISTER_WATCH_FETCH=True), http.run_deadline(seconds=0):
        assert http.out_of_time()
        found = valitsus.discover()
        look = valitsus.look(dt.date(2026, 11, 26), today=dt.date(2026, 11, 25), discovery=found)

    assert found.out_of_time and not found.ok and found.read == []
    # Stopped at the first page: the rest of the listings were not even tried.
    assert [path for path, _ in found.failures] == [valitsus.HOMEPAGE]
    assert all(isinstance(error, http.DeadlineExceeded) for _, error in found.failures)
    assert look.state == valitsus.SOURCE_ERROR
    assert look.status is None and look.agenda is None
    assert "ajapiir" in look.detail or "ei vastanud" in look.detail


class _Clock:
    """The run's time, spent on demand (`http.remaining` is what every check reads)."""

    def __init__(self) -> None:
        self.spent = False

    def remaining(self) -> float | None:
        return -1.0 if self.spent else None


@pytest.fixture
def clock(monkeypatch):
    from app.register_watch.sources import http

    spent = _Clock()
    monkeypatch.setattr(http, "remaining", spent.remaining)
    return spent


def _deadline_at(site, clock, path):
    """``path`` is where the run's time runs out: it and every later read are cut."""
    from app.register_watch.sources import http

    inner = valitsus.get_text

    def get_text(host: str, wanted: str, **kwargs: object) -> str:
        if wanted == path:
            clock.spent = True
        if clock.spent:
            site.asked.append(wanted)
            raise http.DeadlineExceeded(f"{host} ei vastanud ettenähtud aja jooksul")
        return inner(host, wanted, **kwargs)

    return get_text


def test_a_deadline_in_the_sitemap_is_a_source_failure_never_not_published(
    site, previous_week, clock, monkeypatch
):
    # Wednesday night: the session is still to come and the plain address is 403 —
    # exactly where r95 must not say «not yet published» without the listings.
    monkeypatch.setattr(valitsus, "get_text", _deadline_at(site, clock, valitsus.SITEMAP))
    outcome = _run(dt.date(2026, 11, 25))

    assert outcome.stored == []
    states = {c.state for c in outcome.checks}
    assert states == {AgendaCheckState.SOURCE_ERROR}
    assert not states & {AgendaCheckState.NOT_PUBLISHED, AgendaCheckState.NOT_FOUND}
    assert outcome.out_of_time and outcome.discovery_failed
    # Discovery stopped at the deadline: the sitemap's pages were never asked.
    assert f"{valitsus.SITEMAP}?page=17" not in site.asked
    result = _classified(outcome)
    assert result.outcome == no.PARTIAL and "time limit" in result.reason


def test_a_deadline_during_the_lookup_after_a_403_is_a_source_failure(
    site, previous_week, clock, monkeypatch
):
    # The listings are down, the plain address is refused, and the time runs out on
    # the next address: the refusal must not decide the week.
    site.answers[valitsus.SITEMAP] = 503
    monkeypatch.setattr(valitsus, "get_text", _deadline_at(site, clock, SUFFIXED))
    outcome = _run(dt.date(2026, 11, 25))

    (check,) = outcome.checks
    assert check.state == AgendaCheckState.SOURCE_ERROR
    assert PLAIN in site.asked and SUFFIXED in site.asked
    # The lookup stopped at the deadline: no later guessed address was tried.
    assert site.asked[-1] == SUFFIXED
    assert _classified(outcome).outcome == no.PARTIAL


def test_one_slow_call_cut_with_time_left_does_not_stop_the_lookup(site, previous_week):
    # `CALL_SECONDS` cut one read, but the run still has time: the next address is
    # read and the agenda is stored; the cut read stays visible as a failure.
    from app.register_watch.sources import http

    site.answers[valitsus.SITEMAP] = 503
    site.answers[PLAIN] = http.DeadlineExceeded("valitsus.ee ei vastanud ettenähtud aja jooksul")
    outcome = _run(THURSDAY)

    (stored,) = outcome.stored
    assert stored.url.endswith(SUFFIXED)
    assert not outcome.out_of_time
    assert any(PLAIN in e for e in outcome.errors)
    assert _classified(outcome).outcome == no.PARTIAL


@override_settings(REGISTER_SWEEP_AI=True, REGISTER_SWEEP_AI_MODEL="claude-test")
def test_a_spent_deadline_charges_no_model_attempt_and_the_night_says_so(
    site, previous_week, clock
):
    stored = agenda.store(valitsus.parse(f"https://valitsus.ee{SUFFIXED}", AGENDA))
    clock.spent = True
    outcome = agenda.run(today=THURSDAY, ai_client=NoModel())

    stored.refresh_from_db()
    # No request was made, so none is counted: the three attempts stay for real ones.
    assert stored.judged_at is None and stored.ai_attempts == 0
    assert outcome.usage.calls == 0 and outcome.usage.failures == 0
    assert outcome.out_of_time
    assert any("ajapiir" in e for e in outcome.errors)
    result = _classified(outcome)
    assert result.outcome == no.PARTIAL and "time limit" in result.reason


@override_settings(REGISTER_SWEEP_AI=True, REGISTER_SWEEP_AI_MODEL="claude-test")
def test_judge_charges_no_attempt_when_the_request_was_never_made(clock):
    stored = agenda.store(valitsus.parse(f"https://valitsus.ee{SUFFIXED}", AGENDA))
    clock.spent = True
    from app.register_watch import candidates_ai

    usage = candidates_ai.Usage()
    assert agenda.judge(stored, usage, client=NoModel()) == 0
    stored.refresh_from_db()
    assert stored.ai_attempts == 0 and usage.failures == 0 and usage.calls == 0
