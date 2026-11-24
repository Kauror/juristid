"""UX-001 (2026-11-25): «Menetluse kulg» as one track of compact items with popovers.

* Every item is one component: dot, name, date at its recorded precision and at
  most one line per fact; the whole text is in the item's popover.
* A phase carrying several commencements keeps them as separate lines and as
  separate popover facts, each ending in its **own** «Allikas».
* «Allikas» is what is stored: an http(s) source address is a safe link
  (new tab, no opener); Lovable is named and said to be changed in Lovable;
  otherwise the colleague or «teadmata». Never an invented address.
* No removal control on the rail; dates, precision and order are unchanged.
* The page's reads do not grow with the number of items and stay under the
  Teema page ceiling.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.intelligence.enums import FactStatus, ImportantDateKind
from app.intelligence.models import MatterEffectiveDate, MatterImportantDate
from app.matters.legal_process import KIND_EVENT, KIND_MILESTONE, KIND_PHASE, RailStep
from app.matters.rail_tips import LOVABLE_OWNED_NOTE, tip_for
from app.matters.services import change_stage
from app.workflow.enums import DatePrecision, Track
from app.workflow.models import StageVocabulary
from tests import factories
from tests.test_teema_redesign import TEEMA_PAGE_QUERY_CEILING

# -- the popover model, without a database --------------------------------------------


def _person(name: str) -> SimpleNamespace:
    return SimpleNamespace(display_name=name)


def _effective(pk: str, *, description: str, url: str = "", by=None, display: str = ""):
    return SimpleNamespace(
        pk=pk,
        description=description,
        source_url=url,
        created_by=by,
        note="",
        display_date=display,
    )


def test_a_phase_with_two_commencements_keeps_each_with_its_own_source():
    first = _effective(
        "a", description="põhiosa", url="https://www.riigiteataja.ee/akt/1", display="1.1.2027"
    )
    second = _effective("b", description="osad sätted", by=_person("Mari"), display="1.7.2028")
    step = RailStep(
        key="joustumine",
        label="Jõustumine",
        kind=KIND_PHASE,
        state="future",
        notes=("põhiosa 1.1.2027", "osad sätted 1.7.2028"),
        note_records=(first, second),
    )

    tip = tip_for(step, frozenset())

    # Date first on the rail's line, so a narrow column cuts the words, not the date.
    assert tip.lines == ("1.1.2027 · põhiosa", "1.7.2028 · osad sätted")
    assert [fact.date for fact in tip.facts] == ["1.1.2027", "1.7.2028"]
    assert [fact.description for fact in tip.facts] == ["põhiosa", "osad sätted"]
    assert tip.facts[0].source.url == "https://www.riigiteataja.ee/akt/1"
    assert tip.facts[0].source.host == "riigiteataja.ee"
    assert tip.facts[1].source.url == ""
    assert tip.facts[1].source.label == "Mari"


@pytest.mark.parametrize(
    "address",
    [
        "javascript:alert(1)",
        "ftp://example.invalid/x",
        "riigiteataja.ee/x",
        " ",
        # Malformed: urlsplit / the host parser raise on these (UX-001 refinement).
        "http://[::1",
        "https://[not-an-ip]/akt",
        "https://www.riigiteataja.ee:99999/akt",
        "https://www.riigi teataja.ee/akt",
        "https://www.riigiteataja.ee/akt\n1",
        "https:///akt",
        None,
    ],
)
def test_only_a_well_formed_http_address_becomes_a_link(address):
    record = _effective("a", description="põhiosa", url=address, by=_person("Mari"))
    step = RailStep(key="m", label="Jõustumine", kind=KIND_MILESTONE, state="future", record=record)

    source = tip_for(step, frozenset()).facts[0].source

    assert source.url == ""
    assert source.label == "Mari"


def test_a_lovable_record_says_lovable_and_where_it_is_changed():
    record = SimpleNamespace(
        pk="x", title="Eeldatav jõustumine", note="Üldises korras", created_by=None
    )
    step = RailStep(
        key="event:x",
        label="Eeldatav jõustumine",
        kind=KIND_EVENT,
        state="future",
        display_date="1.7.2027",
        record=record,
    )

    tip = tip_for(step, frozenset({"x"}))

    assert tip.lines == ("1.7.2027", "Üldises korras")
    assert tip.facts[0].owned_by_lovable is True
    assert tip.facts[0].source.label == "Lovable"
    assert "Lovable'is" in LOVABLE_OWNED_NOTE


def test_a_record_without_an_author_is_teadmata_not_invented():
    record = SimpleNamespace(pk="x", title="T", note="", created_by=None, source_url="")
    step = RailStep(key="event:x", label="T", kind=KIND_EVENT, state="future", record=record)

    assert tip_for(step, frozenset()).facts[0].source.label == "teadmata"


def test_a_roadmap_phase_has_no_popover_and_its_date_stays():
    step = RailStep(
        key="riigikogu", label="Riigikogus", kind=KIND_PHASE, state="future", display_date="2027"
    )

    tip = tip_for(step, frozenset())

    assert tip.lines == ("2027",)
    assert not tip.has_popover


def test_a_detail_only_point_shows_one_line_and_the_whole_in_the_popover():
    step = RailStep(
        key="milestone:x",
        label="Lõpetatud",
        kind=KIND_MILESTONE,
        state="reached",
        display_date="1.10.2026",
        detail="Vastus esitatud ja järeltegevus tehtud",
    )

    tip = tip_for(step, frozenset())

    assert tip.lines == ("1.10.2026", "Vastus esitatud ja järeltegevus tehtud")
    assert tip.facts[0].description == "Vastus esitatud ja järeltegevus tehtud"
    assert tip.facts[0].source is None


# -- the page ---------------------------------------------------------------------------


def _riigikogus(owner):
    matter = factories.MatterFactory(owner=owner, track=Track.DOMESTIC.value, received_date=None)
    change_stage(matter=matter, stage=StageVocabulary.objects.get(key="parliament"), actor=owner)
    return matter


def _commencement(matter, *, when: date, description: str, url: str = "", by=None):
    return MatterEffectiveDate.objects.create(
        matter=matter,
        date_value=when,
        period_end=when,
        date_precision=DatePrecision.EXACT,
        description=description,
        source_url=url,
        status=FactStatus.ACTIVE,
        created_by=by,
    )


def _estimate(matter, *, when: date, note: str = "", by=None, precision=DatePrecision.EXACT):
    return MatterImportantDate.objects.create(
        matter=matter,
        title="Eeldatav jõustumine",
        kind=ImportantDateKind.ESTIMATED_COMMENCEMENT,
        date_value=when,
        period_end=when,
        date_precision=precision,
        status=FactStatus.ACTIVE,
        note=note,
        created_by=by,
    )


def _strip(client, matter) -> str:
    html = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()
    strip = html[html.index('class="tl-strip"') :]
    return strip[: strip.index("</ol>")]


def _items(strip: str) -> list[str]:
    return strip.split('<li class="tl-step ')[1:]


@pytest.mark.django_db
def test_two_commencements_stay_two_each_ending_in_its_own_source(signed_in, specialist):
    matter = _riigikogus(specialist)
    ahead = timezone.localdate() + timedelta(days=400)
    _commencement(
        matter,
        when=ahead,
        description="põhiosa",
        url="https://www.riigiteataja.ee/akt/123",
        by=specialist,
    )
    _commencement(
        matter, when=ahead + timedelta(days=365), description="osad sätted", by=specialist
    )

    strip = _strip(signed_in, matter)
    joustumine = next(item for item in _items(strip) if ">Jõustumine<" in item)

    assert joustumine.count('<span class="tl-step__note">') == 2
    facts = joustumine.split('class="tl-tip__fact"')[1:]
    assert len(facts) == 2
    assert "põhiosa" in facts[0] and "riigiteataja.ee" in facts[0]
    assert "osad sätted" in facts[1] and "riigiteataja.ee" not in facts[1]
    for fact in facts:
        parts = re.findall(r'class="(tl-tip__\w+)"', fact)
        # The fact's own source, and last in it.
        assert parts[-1] == "tl-tip__source", parts
        assert parts.count("tl-tip__source") == 1
    link = re.search(r'<a href="https://www.riigiteataja.ee/akt/123"[^>]*>', joustumine)
    assert link
    assert 'target="_blank"' in link.group(0) and 'rel="noopener noreferrer"' in link.group(0)


@pytest.mark.django_db
def test_estimates_over_several_years_draw_one_compact_item_each(signed_in, specialist):
    """The owner's screenshot: Riigikogus, estimates over years, then Jõustumine."""
    matter = _riigikogus(specialist)
    today = timezone.localdate()
    for years, precision in (
        (1, DatePrecision.EXACT),
        (2, DatePrecision.QUARTER),
        (3, DatePrecision.YEAR),
    ):
        _estimate(
            matter,
            when=today.replace(month=6, day=30) + timedelta(days=365 * years),
            note="Ministeeriumi hinnang " * 6,
            by=specialist,
            precision=precision,
        )

    strip = _strip(signed_in, matter)
    estimates = [item for item in _items(strip) if "Eeldatav jõustumine" in item]

    assert len(estimates) == 3
    for item in estimates:
        visible = item[: item.index('class="tl-tip"')]
        assert "Allikas" not in visible
        assert "uxtl__icon--remove" not in item
        # Date and one line of the note, nothing more on the rail.
        assert visible.count('class="tl-step__date"') == 1
        assert visible.count('class="tl-step__note"') == 1
    # The precision the record holds, unchanged: a quarter and a year read as such.
    assert re.search(r"tl-step__date\">\s*[IV]+ kv", strip) or "kvartal" in strip
    assert re.search(r'tl-step__date">\d{4}<', strip)


@pytest.mark.django_db
def test_no_removal_control_anywhere_on_the_rail(signed_in, specialist):
    matter = _riigikogus(specialist)
    _estimate(matter, when=timezone.localdate() + timedelta(days=90), by=specialist)

    strip = _strip(signed_in, matter)

    assert "uxtl__remove" not in strip
    assert "remove_record" not in strip and "/eemalda/" not in strip


@pytest.mark.django_db
def test_a_closed_teema_draws_the_rail_but_offers_no_removal(signed_in, specialist):
    from app.matters.services import close_matter
    from app.workflow.enums import Disposition

    matter = _riigikogus(specialist)
    _estimate(matter, when=timezone.localdate() + timedelta(days=90), by=specialist)
    close_matter(
        matter=matter,
        disposition=Disposition.COMPLETED,
        actor=specialist,
    )

    strip = _strip(signed_in, matter)

    assert "Eeldatav jõustumine" in strip
    assert "uxtl__remove" not in strip
    assert "tl-step__pick" not in strip


def _page_queries(client, matter) -> int:
    url = reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    client.get(url)
    with CaptureQueriesContext(connection) as queries:
        assert client.get(url).status_code == 200
    return len(queries.captured_queries)


@pytest.mark.django_db
def test_more_rail_items_cost_no_more_reads_and_stay_under_the_ceiling(signed_in, specialist):
    small = _riigikogus(specialist)
    _estimate(small, when=timezone.localdate() + timedelta(days=90), by=specialist)
    large = _riigikogus(specialist)
    today = timezone.localdate()
    for number in range(8):
        _estimate(
            large,
            when=today + timedelta(days=60 * (number + 1)),
            note=f"Hinnang {number}",
            by=specialist,
        )
    for number in range(4):
        _commencement(
            large,
            when=today + timedelta(days=500 + 100 * number),
            description=f"osa {number}",
            url=f"https://www.riigiteataja.ee/akt/{number}",
            by=specialist,
        )

    few = _page_queries(signed_in, small)
    many = _page_queries(signed_in, large)

    assert many == few
    assert many <= TEEMA_PAGE_QUERY_CEILING


@pytest.mark.django_db
def test_a_malformed_stored_address_does_not_break_the_teema_page(signed_in, specialist):
    """A `source_url` the URL parser refuses is plain text, and the page renders."""
    matter = _riigikogus(specialist)
    record = _commencement(
        matter,
        when=timezone.localdate() + timedelta(days=300),
        description="põhiosa",
        by=specialist,
    )
    # Written past the form's validation, as a hand-edited or imported row could be.
    MatterEffectiveDate.objects.filter(pk=record.pk).update(source_url="http://[::1/akt")

    strip = _strip(signed_in, matter)

    assert "põhiosa" in strip
    assert "[::1" not in strip
    assert f"Allikas: {specialist.display_name}" in strip
