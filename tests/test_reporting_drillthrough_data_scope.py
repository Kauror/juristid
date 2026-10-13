"""A drill-through opens real data only, because the number counted real data only.

Every Statistika population starts at `reporting_population`, which is
`visible_to(viewer).real_data()`. The Teemad register shows TEST Matters by
default (`?andmed=koik`), so a developer finds the record they just made. A link
from a figure into the register without `andmed=paris` therefore opened every
TEST Matter that matched its filters as well — a list longer than the number on
the card, with nothing on either screen saying which was wrong.

The shared Statistika world has no TEST Matters at all, which is why
`test_reporting_drillthrough` could not see this. Here every native Matter in it
gets a TEST twin that matches the same filters — owner, stage, track, policy
area, tag, sender, addressee, next action, a sent opinion — and then every
register link every metric renders is followed through the real view.

What is compared depends on what the link stands for:

* **a count or a segment** — the link opens exactly the records counted, so the
  register total equals the figure;
* **an aggregate** — a share, a sum of files, a "how many of them have X" whose
  register has no filter for X — the link opens the population the figure was
  computed over, so the register total equals `population_count`, never the
  figure itself. These are named in `_HEAD_OPENS_ITS_POPULATION`, one by one.

And, separately, the underlying populations: adding the TEST twins changes no
metric's value, population or segment.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from django.urls import reverse

from app.matters.enums import MatterDataClass, MatterOrigin
from app.matters.models import Matter, TagAssignment
from app.matters.selectors import DATA_CLASS_REAL
from app.reporting import metric_catalogue as keys
from app.reporting.selectors.base import REGISTER_DATA_SCOPE, register_url
from app.reporting.services import COMPUTERS, compute
from app.workflow.enums import Track
from app.workflow.models import NextAction

pytestmark = pytest.mark.django_db

#: Headline figures that are not a count of what their link opens. Each opens
#: the population the figure was measured over, and that is what is compared.
#: A new metric is a count unless it is added here with its reason.
_HEAD_OPENS_ITS_POPULATION = {
    # «N teemal on saatja»: the register has no "has a sender" filter, so the
    # headline opens every Matter the coverage was measured over.
    keys.MATTERS_BY_SOURCE_ORGANISATION,
    # The same, for the addressee.
    keys.MATTERS_BY_ADDRESSEE_ORGANISATION,
    # «Mitu arvamust»: no "how many opinions" filter either; opens the FULL
    # population the count was taken from.
    keys.MATTERS_WITH_MULTIPLE_SUBMISSIONS,
    # A sum of files over the Matters that have a source page; opens those
    # Matters (the distribution's `n`).
    keys.RESOURCES_PER_MATTER,
}

#: Links that must open a shorter list with `andmed=paris` than without it once
#: the twins exist — proof that this test is able to see the defect at all.
_MUST_MEET_A_TWIN = (
    keys.MATTERS_TOTAL,
    keys.ACTIVE_FULL_MATTERS,
    keys.MATTERS_BY_OWNER,
    keys.MATTERS_BY_TRACK,
    keys.ACTIVE_WITHOUT_OWNER,
    keys.OVERDUE_DO_DEADLINE,
)


@dataclass(frozen=True)
class Link:
    key: str
    where: str
    value: int
    url: str


def _register() -> str:
    return reverse("matters:matter_list")


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query, keep_blank_values=True)


def register_links(key: str, result) -> list[Link]:
    """Every link this result renders into the Teemad register."""
    found: list[tuple[str, int, str]] = []
    if result.drillthrough_url:
        found.append(("headline", result.value, result.drillthrough_url))
    found += [(f"segment {s.label}", s.value, s.url) for s in result.segments if s.url]
    if result.matrix is not None:
        for row in result.matrix.rows:
            for column, cell in zip(result.matrix.columns, row.cells, strict=False):
                if cell.url:
                    found.append((f"cell {row.label} / {column}", cell.value, cell.url))
    return [
        Link(key, where, value, url)
        for where, value, url in found
        if urlparse(url).path == _register()
    ]


def register_total(client, url: str, **replace: str) -> int:
    query = {name: values[0] for name, values in _query(url).items()}
    for name, value in replace.items():
        if value:
            query[name] = value
        else:
            query.pop(name, None)
    response = client.get(urlparse(url).path, query)
    assert response.status_code == 200, f"{url} -> {response.status_code}"
    return int(response.context["total"])


def add_test_twins(world) -> list[Matter]:
    """A TEST copy of every native Matter, matching every filter the original does.

    Only native Matters: the database refuses a TEST historical register row
    (`matters_test_data_is_native`), so the archive buckets meet no twin — which
    is also the truth in production.
    """
    twins = []
    for original in Matter.objects.filter(origin=MatterOrigin.NATIVE).order_by("pk"):
        twin = Matter.objects.create(
            title=f"{original.title} (TEST)",
            data_class=MatterDataClass.TEST,
            record_mode=original.record_mode,
            origin=original.origin,
            reference_year=original.reference_year,
            reference_number=(original.reference_number or 0) + 500,
            reporting_year=original.reporting_year,
            owner=original.owner,
            stage=original.stage,
            track=original.track,
            visibility=original.visibility,
            addressee_organisation=original.addressee_organisation,
            received_date=original.received_date,
            response_deadline=original.response_deadline,
            is_open=original.is_open,
            disposition=original.disposition,
            closed_at=original.closed_at,
            closed_by=original.closed_by,
        )
        twin.source_organisations.set(original.source_organisations.all())
        twin.policy_areas.set(original.policy_areas.all())
        for assignment in TagAssignment.objects.filter(matter=original):
            TagAssignment.objects.create(matter=twin, tag=assignment.tag)
        for action in NextAction.objects.filter(matter=original):
            NextAction.objects.create(
                matter=twin,
                text=action.text,
                kind=action.kind,
                date_semantics=action.date_semantics,
                target_date=action.target_date,
                status=action.status,
            )
        twins.append(twin)
    return twins


def _add_twin_opinions(world, twins: list[Matter]) -> None:
    """Two sent opinions on one twin, so the submission-count buckets meet TEST."""
    from tests.synthetic_statistics import _submission

    twin = next(t for t in twins if t.title.startswith(world.native_open.title))
    for title in ("TEST-arvamus üks", "TEST-arvamus kaks"):
        _submission(twin, title=title, sent_on=world.today)


def _snapshot(context) -> dict[str, tuple]:
    snapshot = {}
    for key in COMPUTERS:
        result = compute(key, context)
        snapshot[key] = (
            result.value,
            result.population_count,
            result.eligible_count,
            tuple((s.label, s.value) for s in result.segments),
        )
    return snapshot


# ---------------------------------------------------------------------------
# The link itself
# ---------------------------------------------------------------------------


def test_the_register_data_scope_is_the_reporting_population():
    """`paris` is the register's word for exactly what `real_data()` keeps."""
    from app.matters.selectors import DATA_CLASS_FILTERS

    assert REGISTER_DATA_SCOPE == DATA_CLASS_REAL
    assert DATA_CLASS_FILTERS[REGISTER_DATA_SCOPE] == MatterDataClass.REAL


def test_every_register_link_carries_the_data_scope_once(world, reporting_context):
    """Every metric, every link — including one that names the scope itself."""
    context = reporting_context(world.head)
    seen = 0
    for key in COMPUTERS:
        for link in register_links(key, compute(key, context)):
            assert _query(link.url).get("andmed") == [DATA_CLASS_REAL], (key, link.where)
            seen += 1
    assert seen > 30, "the enumeration found too few register links to mean anything"

    explicit = register_url(context, andmed=DATA_CLASS_REAL)
    assert _query(explicit)["andmed"] == [DATA_CLASS_REAL]
    assert explicit.count("andmed=") == 1


def test_the_scope_travels_with_every_other_filter(world, reporting_context):
    """A corpus link drops the year but keeps the data scope."""
    from app.reporting.selectors.base import corpus_url, unknown_year_url

    context = reporting_context(world.head, period="kaesolev", track=Track.DOMESTIC.value)
    for url in (register_url(context), corpus_url(context), unknown_year_url(context)):
        query = _query(url)
        assert query["andmed"] == [DATA_CLASS_REAL], url
        assert query["menetlusliik"] == [Track.DOMESTIC.value], url
    assert "aasta" not in _query(corpus_url(context))


# ---------------------------------------------------------------------------
# Parity with TEST Matters present
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("viewer_name", ["head", "reader"])
@pytest.mark.parametrize("period", ["koik", "kaesolev"])
def test_every_register_link_opens_what_its_figure_counted_with_test_matters_present(
    viewer_name, period, client, world, reporting_context
):
    twins = add_test_twins(world)
    _add_twin_opinions(world, twins)
    viewer = getattr(world, viewer_name)
    client.force_login(viewer)
    context = reporting_context(viewer, period=period)

    links = 0
    for key in COMPUTERS:
        result = compute(key, context)
        for link in register_links(key, result):
            total = register_total(client, link.url)
            if link.where == "headline" and key in _HEAD_OPENS_ITS_POPULATION:
                expected = (
                    result.distribution.n
                    if result.distribution is not None
                    else result.population_count
                )
                assert total == expected, (key, link.where, link.url)
            else:
                assert total == link.value, (key, link.where, link.url)
            links += 1
    assert links > 20, links


def test_the_parity_test_can_see_a_test_matter(client, world, reporting_context):
    """Without `andmed=paris` the same links open the TEST twins too.

    Otherwise the test above would pass on a world where no TEST Matter ever
    matched a link, and prove nothing.
    """
    add_test_twins(world)
    client.force_login(world.head)
    context = reporting_context(world.head)
    for key in _MUST_MEET_A_TWIN:
        links = register_links(key, compute(key, context))
        assert links, key
        met = [
            link
            for link in links
            if register_total(client, link.url, andmed="") > register_total(client, link.url)
        ]
        assert met, f"{key}: no link of this metric meets a TEST twin"


@pytest.mark.parametrize("viewer_name", ["head", "reader"])
def test_test_matters_change_no_counted_population(viewer_name, world, reporting_context):
    """The figures themselves: real data before, real data after."""
    viewer = getattr(world, viewer_name)
    before = _snapshot(reporting_context(viewer))
    twins = add_test_twins(world)
    _add_twin_opinions(world, twins)
    after = _snapshot(reporting_context(viewer))
    changed = {key: (before[key], after[key]) for key in before if before[key] != after[key]}
    assert not changed, changed


def test_a_link_without_the_scope_is_what_the_register_shows_by_default(
    client, world, reporting_context
):
    """The register's own default is unchanged — the fix is in the link."""
    twins = add_test_twins(world)
    client.force_login(world.head)
    everything = register_total(client, f"{_register()}?{urlencode({'olek': 'koik'})}")
    real = register_total(client, f"{_register()}?{urlencode({'olek': 'koik', 'andmed': 'paris'})}")
    assert everything - real == len(twins)
