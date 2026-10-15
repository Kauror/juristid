"""Statistika filters reach every figure they apply to (owner decision D2).

QA remediation G, 2026-10-14. The independent QA found the Ülevaade strip and
rail counting department-wide under a filter chip (QA-STATS-02), the rail's
«Aruandlus <year>» sends following the page period (QA-STATS-01), and a card's
fixed narrowing overriding the chip its count used (QA-STATS-06, QA-STATS-08).
The QA tests prove the reported shapes. These tests close the rest of the
matrix, and every count-to-list check compares **record ids**, not totals:

* REAL and TEST twins (`add_test_twins`): a twin matches every chip its
  original does, so `andmed=paris` must still be in every link;
* the restricted Matter, for the head who may see it and the reader who may not;
* an owner chip, a policy-area chip, a record-mode chip that contradicts a row,
  and an unreadable owner;
* an open ARCHIVE row, which the shared world does not contain.

Every figure is computed on a fresh `ReportingContext` after any data change:
a context memoises `count()` by SQL for its lifetime.
"""

from __future__ import annotations

import html
import re
import uuid
from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest
from django.urls import reverse
from django.utils import timezone

from app.matters.enums import MatterOrigin, RecordMode
from app.matters.models import Matter
from app.reporting import metric_catalogue as keys
from app.reporting import overview_strip
from app.reporting.selectors import quality
from app.reporting.selectors.base import REGISTER_DATA_SCOPE, merge_chip_params
from app.reporting.services import compute
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from tests.synthetic_statistics import _submission
from tests.test_reporting_drillthrough_data_scope import add_test_twins
from tests.test_statistika_rail_data_scope import _plant_year_facts, _twin_of

pytestmark = pytest.mark.django_db


@pytest.fixture
def pinned_today(world, monkeypatch):
    """The register view reads the day for itself; make it the world's day."""
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: world.today)
    return world.today


def _register_ids(client, url: str) -> set:
    """Every row id the register list behind ``url`` pages through."""
    parsed = urlparse(url)
    query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
    response = client.get(parsed.path, query)
    assert response.status_code == 200, url
    paginator = response.context["paginator"]
    ids: set = set()
    for number in paginator.page_range:
        ids |= {row.pk for row in paginator.page(number).object_list}
    return ids


def _rail(world, viewer, context) -> dict[str, overview_strip.Figure]:
    return {
        figure.caption: figure
        for block in overview_strip.rail(viewer, world.today, {}, context=context)
        for figure in block.rows
    }


def _open_full(viewer):
    return (
        Matter.objects.visible_to(viewer)
        .real_data()
        .filter(is_open=True, record_mode=RecordMode.FULL)
    )


@pytest.fixture
def twins_and_facts(world, pinned_today):
    """The world with TEST twins and this year's victory and in-force facts."""
    add_test_twins(world)
    originals = [world.native_open, world.native_waiting, world.restricted]
    _plant_year_facts(world, originals + [_twin_of(m) for m in originals])
    return world


# ---------------------------------------------------------------------------
# QA-STATS-02 — the rail and the strip obey the chips, by record identity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("viewer_name", ["head", "reader"])
@pytest.mark.parametrize("chip", ["owner", "policy_area"])
def test_rail_rows_under_a_chip_open_exactly_the_real_records_they_count(
    viewer_name, chip, client, twins_and_facts, reporting_context
):
    world = twins_and_facts
    viewer = getattr(world, viewer_name)
    if chip == "owner":
        context = reporting_context(viewer, owner_id=world.sandra.pk)
        narrowed = _open_full(viewer).filter(owner=world.sandra)
    else:
        context = reporting_context(viewer, policy_area_key=world.area_tax.key)
        narrowed = _open_full(viewer).filter(policy_areas=world.area_tax)
    rail = _rail(world, viewer, context)
    twins = set(Matter.objects.filter(title__endswith="(TEST)").values_list("pk", flat=True))
    client.force_login(viewer)

    expected = set(narrowed.values_list("pk", flat=True))
    opened = _register_ids(client, rail["avatud teemat"].url)
    assert opened == expected
    assert rail["avatud teemat"].value == len(expected)

    for caption, figure in rail.items():
        if not figure.url:
            assert figure.value == 0, caption
            continue
        query = parse_qs(urlparse(figure.url).query)
        assert query.get("andmed") == [REGISTER_DATA_SCOPE], caption
        ids = _register_ids(client, figure.url)
        assert len(ids) == figure.value, caption
        assert not ids & twins, caption
        # Every row is a narrowing of the chip: nothing outside it is listed.
        if chip == "owner":
            assert set(Matter.objects.filter(pk__in=ids).values_list("owner_id", flat=True)) <= {
                world.sandra.pk
            }, caption
        else:
            assert (
                not Matter.objects.filter(pk__in=ids).exclude(policy_areas=world.area_tax).exists()
            ), caption
        assert (world.restricted.pk in ids) is False or viewer_name == "head", caption


def test_the_chip_moves_the_rail_and_the_strip_together(client, twins_and_facts):
    """The page: with the owner chip, the rail and the strip agree on the same rows."""
    world = twins_and_facts
    client.force_login(world.head)
    body = client.get(reverse("reporting:overview"), {"vastutaja": str(world.martin.pk)})
    page = body.content.decode()
    rail_href = re.search(
        r'<section class="railblock" aria-label="Praegu">.*?<a class="railrow" href="([^"]+)">',
        page,
        re.S,
    )
    strip_href = re.search(r'<a class="seis__figure" href="([^"]+)">', page)
    assert rail_href and strip_href
    rail_ids = _register_ids(client, html.unescape(rail_href[1]))
    strip_ids = _register_ids(client, html.unescape(strip_href[1]))
    assert (
        rail_ids
        == strip_ids
        == set(_open_full(world.head).filter(owner=world.martin).values_list("pk", flat=True))
    )


def test_a_row_the_chip_contradicts_is_zero_and_opens_nothing(twins_and_facts, reporting_context):
    """«vastutajata» under an owner chip, every FULL row under «Kirje liik: Arhiiv»."""
    world = twins_and_facts
    owner = _rail(world, world.head, reporting_context(world.head, owner_id=world.sandra.pk))
    assert (owner["vastutajata"].value, owner["vastutajata"].url) == (0, "")

    archive = _rail(
        world, world.head, reporting_context(world.head, record_mode=RecordMode.ARCHIVE.value)
    )
    for caption in (
        "avatud teemat",
        "üle tähtaja",
        f"tähtaeg {overview_strip.DEADLINE_WINDOW_DAYS} p jooksul",
        "vastutajata",
        "järgmise tegevuseta",
    ):
        assert (archive[caption].value, archive[caption].url) == (0, ""), caption


def test_an_unreadable_owner_empties_the_rail_rather_than_widening_it(
    twins_and_facts, reporting_context
):
    world = twins_and_facts
    context = reporting_context(world.head, owner_unreadable=True)
    for caption, figure in _rail(world, world.head, context).items():
        assert (figure.value, figure.url) == (0, ""), caption
    [victory] = overview_strip.strip({}, world.head, world.today, "x", context=context)
    assert (victory.value, victory.url) == (0, "")


def test_contradicting_rows_render_as_text_not_links(client, twins_and_facts):
    world = twins_and_facts
    client.force_login(world.head)
    page = client.get(
        reverse("reporting:overview"), {"vastutaja": str(world.sandra.pk)}
    ).content.decode()
    praegu = re.search(r'aria-label="Praegu">(.*?)</section>', page, re.S)[1]
    assert re.search(
        r'<span class="railrow">\s*<span class="railrow__key">vastutajata</span>', praegu
    ), praegu


@pytest.mark.parametrize("viewer_name", ["head", "reader"])
def test_strip_and_rail_victory_rows_list_the_chips_matters(
    viewer_name, client, twins_and_facts, reporting_context
):
    world = twins_and_facts
    viewer = getattr(world, viewer_name)
    context = reporting_context(viewer, owner_id=world.sandra.pk)
    [strip] = overview_strip.strip({}, viewer, world.today, "x", context=context)
    rail = _rail(world, viewer, context)["teemat töövõiduga"]
    client.force_login(viewer)
    expected = {
        m.pk
        for m in (world.native_open, world.native_waiting, world.restricted)
        if m.owner_id == world.sandra.pk
        and Matter.objects.visible_to(viewer).filter(pk=m.pk).exists()
    }
    assert _register_ids(client, strip.url) == _register_ids(client, rail.url) == expected
    assert strip.value == rail.value == len(expected)
    assert f"vastutaja={world.sandra.pk}" in strip.url


# ---------------------------------------------------------------------------
# QA-STATS-01 — «Aruandlus <year>» counts that year, with the chips applied
# ---------------------------------------------------------------------------


def _reporting_block(page: str, year: int) -> dict[str, tuple[int, str]]:
    block = re.search(rf'aria-label="Aruandlus {year}">(.*?)</section>', page, re.S)[1]
    return {
        html.unescape(m[2]).strip(): (int(m[3]), html.unescape(m[1]))
        for m in re.finditer(
            r'<a class="railrow" href="([^"]+)">\s*<span class="railrow__key">([^<]+)</span>\s*'
            r'<span class="railrow__value[^"]*">(\d+)</span>',
            block,
        )
    }


@pytest.mark.parametrize("period", ["kaesolev", "eelmine", "koik", "2020-2021"])
def test_reporting_year_sends_open_that_years_records_with_the_owner_chip(
    period, client, world, pinned_today
):
    year = world.today.year
    mine = _submission(world.native_open, title="QA tänavune Sandra", sent_on=world.today)
    _submission(world.native_waiting, title="QA tänavune Martin", sent_on=world.today)
    _submission(world.native_open, title="QA mullune Sandra", sent_on=date(year - 1, 6, 1))
    assert world.native_open.owner_id == world.sandra.pk != world.native_waiting.owner_id

    client.force_login(world.head)
    page = client.get(
        reverse("reporting:overview"), {"periood": period, "vastutaja": str(world.sandra.pk)}
    ).content.decode()
    value, url = _reporting_block(page, year)["arvamusi välja"]

    expected = set(
        Submission.objects.filter(
            status=SubmissionStatus.SENT,
            matter__owner=world.sandra,
            matter__data_class="REAL",
            sent_at__year=year,
        ).values_list("pk", flat=True)
    )
    assert mine.pk in expected
    response = client.get(
        urlparse(url).path, {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    )
    listed = {row.pk for row in response.context["paginator"].object_list}
    assert listed == expected
    assert value == len(expected)


# ---------------------------------------------------------------------------
# QA-STATS-08 — the link builder never replaces a chip
# ---------------------------------------------------------------------------


def test_merging_keeps_a_chip_a_narrowing_allows_and_refuses_one_it_does_not():
    chips = {"liik": "ARCHIVE", "paritolu": "LEGACY_IMPORT"}
    assert merge_chip_params(chips, {"liik": "FULL"}) is None
    assert merge_chip_params(chips, {"liik": "ARCHIVE", "olek": "avatud"}) == {
        "liik": "ARCHIVE",
        "paritolu": "LEGACY_IMPORT",
        "olek": "avatud",
    }
    # Several origins: the chip's one survives when it is among them.
    assert merge_chip_params(chips, {"paritolu": "LEGACY_IMPORT,PROMOTED_LEGACY"})["paritolu"] == (
        "LEGACY_IMPORT"
    )
    assert merge_chip_params(chips, {"paritolu": "NATIVE"}) is None
    # A narrowing may not drop a chip; a non-chip key may still be dropped.
    assert merge_chip_params({**chips, "aasta": "2026"}, {"liik": "", "aasta": ""}) == chips


@pytest.mark.parametrize(
    ("key", "context_overrides"),
    [
        (keys.ACTIVE_WITHOUT_OWNER, {"owner_id": "sandra"}),
        (keys.ACTIVE_WITHOUT_STAGE, {"stage_key": "stage"}),
        (keys.ACTIVE_FULL_MATTERS, {"record_mode": RecordMode.ARCHIVE.value}),
        (keys.ACTIVE_WITHOUT_NEXT_ACTION, {"record_mode": RecordMode.ARCHIVE.value}),
    ],
)
def test_a_card_under_a_contradicting_chip_is_zero_without_a_link(
    key, context_overrides, world, reporting_context
):
    overrides = {
        name: (
            world.sandra.pk if value == "sandra" else world.stage.key if value == "stage" else value
        )
        for name, value in context_overrides.items()
    }
    result = compute(key, reporting_context(world.head, **overrides))
    assert result.value == 0, key
    assert result.drillthrough_url == "", (key, result.drillthrough_url)


@pytest.mark.parametrize(
    "key", [keys.ACTIVE_WITHOUT_OWNER, keys.ACTIVE_WITHOUT_STAGE, keys.ACTIVE_WITHOUT_NEXT_ACTION]
)
def test_a_card_under_an_agreeing_chip_opens_exactly_its_records(
    key, client, world, reporting_context, open_archive_row
):
    add_test_twins(world)
    result = compute(key, reporting_context(world.head, policy_area_key=world.area_tax.key))
    client.force_login(world.head)
    if not result.drillthrough_url:
        assert result.value == 0
        return
    ids = _register_ids(client, result.drillthrough_url)
    assert len(ids) == result.value, key
    assert open_archive_row.pk not in ids


# ---------------------------------------------------------------------------
# QA-STATS-06 — the quality queues, with an open ARCHIVE row and TEST twins
# ---------------------------------------------------------------------------


@pytest.fixture
def open_archive_row(world):
    return Matter.objects.create(
        title="Avatud arhiivirida ilma valdkonna ja asutuseta",
        record_mode=RecordMode.ARCHIVE,
        origin=MatterOrigin.LEGACY_IMPORT,
        reference_year=world.today.year - 3,
        reference_number=778,
        reporting_year=world.today.year - 3,
        is_open=True,
    )


@pytest.mark.parametrize("viewer_name", ["head", "reader"])
@pytest.mark.parametrize(
    ("queue_key", "narrow"),
    [
        ("active_without_policy_area", {"policy_areas__isnull": True}),
        (
            "unresolved_organisation",
            {"source_organisations__isnull": True, "addressee_organisation__isnull": True},
        ),
    ],
)
def test_quality_queue_opens_exactly_the_matters_it_counted(
    viewer_name, queue_key, narrow, client, world, reporting_context, open_archive_row
):
    add_test_twins(world)
    viewer = getattr(world, viewer_name)
    queue = {q.key: q for q in quality.queues(reporting_context(viewer))}[queue_key]
    expected = set(_open_full(viewer).filter(**narrow).values_list("pk", flat=True))
    client.force_login(viewer)
    assert queue.count == len(expected)
    if not expected:
        return
    assert _register_ids(client, queue.url) == expected
    assert open_archive_row.pk not in expected


# ---------------------------------------------------------------------------
# QA-STATS-07 and QA-STATS-11 — the words around the Ülevaade tables and people
# ---------------------------------------------------------------------------


def test_policy_area_table_prints_its_population_and_says_rows_overlap(client, world):
    world.native_open.policy_areas.add(world.area_tax, world.area_env)
    client.force_login(world.head)
    page = html.unescape(
        client.get(reverse("reporting:overview"), {"periood": "koik"}).content.decode()
    )
    section = re.search(
        r'aria-labelledby="stat-matters_by_policy_area-heading">(.*?)</section>', page, re.S
    )[1]
    kokku = int(re.search(r"kokku (\d+)", section)[1])
    population = Matter.objects.visible_to(world.head).real_data().values("pk").distinct().count()
    assert kokku == population
    rows = [int(v) for v in re.findall(r'<td class="table__date">(\d+)</td>', section)]
    assert sum(rows) > kokku
    assert "Teema võib olla mitmes reas" in section


def test_inimesed_names_its_period_and_never_claims_open_matters(client, world, pinned_today):
    """`MATTERS_BY_OWNER` is approved as every visible Matter of the period.

    The catalogue entry says «Nähtavad teemad». The definition is kept, so the
    wording follows it (QA-STATS-11): the heading names the period, and the
    empty state no longer speaks of «avatud teemasid».
    """
    client.force_login(world.head)
    page = html.unescape(
        client.get(reverse("reporting:overview"), {"periood": "eelmine"}).content.decode()
    )
    block = re.search(r'aria-label="Inimesed">(.*?)</section>', page, re.S)[1]
    assert f"Inimesed · {world.today.year - 1}" in block or "Inimesed · Eelmine aasta" in block
    assert "avatud teemasid" not in block

    empty = html.unescape(
        client.get(
            reverse("reporting:overview"), {"periood": "1990", "vastutaja": str(uuid.uuid4())}
        ).content.decode()
    )
    block = re.search(r'aria-label="Inimesed">(.*?)</section>', empty, re.S)[1]
    assert "Valitud perioodil ei ole ühelgi inimesel teemasid." in block


# ---------------------------------------------------------------------------
# QA-STATS-05 — the same-period comparison declines on the day, not the year
# ---------------------------------------------------------------------------


def _first_send_day() -> date:
    first = (
        Submission.objects.filter(status=SubmissionStatus.SENT, matter__data_class="REAL")
        .order_by("sent_at")
        .values_list("sent_at", flat=True)
        .first()
    )
    return timezone.localdate(first)


@pytest.mark.parametrize(
    ("days_after_first", "declines"),
    [(-1, True), (0, False), (1, False)],
)
def test_comparison_is_measured_from_the_first_send_day_exactly(days_after_first, declines, world):
    """Cut a day before, on, and a day after the first send's anniversary."""
    from datetime import timedelta

    from app.reporting.context import ReportingContext, parse_period
    from app.reporting.metric_types import MetricStatus

    first = _first_send_day()
    previous_cutoff = first + timedelta(days=days_after_first)
    today = date(previous_cutoff.year + 1, previous_cutoff.month, previous_cutoff.day)
    context = ReportingContext(
        viewer=world.head, period=parse_period("kaesolev", today), today=today, now=timezone.now()
    )
    result = compute(keys.SUBMISSIONS_SENT_YOY_CHANGE, context)
    assert (result.status == MetricStatus.INSUFFICIENT_DATA) is declines, (
        first,
        today,
        result.status,
    )
    if declines:
        assert any("mõõtmist ei ole" in note for note in result.notes)
    else:
        assert result.comparison.previous_value >= 1


# ---------------------------------------------------------------------------
# Naming — colleagues by first name on Statistika (Agent I handoff)
# ---------------------------------------------------------------------------


def test_statistika_names_colleagues_by_first_name_and_keeps_filtering_by_id(
    client, world, pinned_today
):
    """The «Vastutaja» picker, its chip and the «Inimesed» rows read «Sandra».

    The full name is never needed to tell these colleagues apart; the value
    submitted and every link still carry the account id.
    """
    client.force_login(world.head)
    page = html.unescape(
        client.get(
            reverse("reporting:overview"), {"vastutaja": str(world.sandra.pk)}
        ).content.decode()
    )
    option = re.search(
        rf'<option value="{world.sandra.pk}"\s*selected>\s*([^<]+?)\s*</option>', page
    )
    assert option and option[1] == "Sandra", option
    assert (
        world.sandra.display_name
        not in re.search(r'aria-label="Inimesed">(.*?)</section>', page, re.S)[1]
    )
    chips = client.get(reverse("reporting:overview"), {"vastutaja": str(world.sandra.pk)}).context[
        "chips"
    ]
    assert [chip.value for chip in chips] == ["Sandra"]


def test_two_colleagues_with_one_first_name_are_told_apart(world, reporting_context):
    from app.accounts.models import User

    other = User.objects.create(
        upn="sandra.kask@example.invalid", display_name="Sandra Kask", is_synthetic=True
    )
    Matter.objects.create(
        title="Teise Sandra teema",
        record_mode=RecordMode.FULL,
        origin=MatterOrigin.NATIVE,
        reference_year=world.today.year,
        reference_number=991,
        reporting_year=world.today.year,
        owner=other,
    )
    labels = {
        s.label for s in compute(keys.MATTERS_BY_OWNER, reporting_context(world.head)).segments
    }
    assert {"Sandra Testjurist", "Sandra Kask"} <= labels
    assert "Sandra" not in labels
