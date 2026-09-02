"""Ülevaade: two scopes, one shell, and the counts that must stay honest.

Most of the risk on this page is arithmetic that looks fine. A restricted Matter
counted for somebody who may not read it, an area called *vastutajata* because
one of ten files is unassigned, an archive letter counted as an opinion Koda
sent this year — none of those look wrong on screen, and all of them make the
page worth less than nothing to the person who has to act on it.
"""

from __future__ import annotations

import datetime
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.intelligence.services import add_important_date
from app.matters import department as dep
from app.matters import overview as ov
from app.matters import work_items as wi
from app.matters.enums import RecordMode
from app.matters.services import create_matter
from app.submissions.enums import SubmissionStatus
from app.taxonomy.models import PolicyArea
from app.workflow.enums import ActionKind, DateSemantics
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

OVERVIEW = "matters:department"


def _sent(matter, *, title: str, sent):
    """A canonical sent Submission, with the evidence its state requires.

    The database refuses a sent submission without a final version — a sent
    opinion with no text is an unverifiable claim about what Koda argued — so
    this supplies one rather than working around the constraint.
    """
    from app.documents.services import add_evidence_version

    document = factories.DocumentFactory(matter=matter)
    version = add_evidence_version(
        document=document,
        content=f"%PDF-1.4\n{title}".encode(),
        original_filename="naidis.pdf",
        mime_type="application/pdf",
    )
    return factories.SubmissionFactory(
        matter=matter,
        title=title,
        status=SubmissionStatus.SENT,
        sent_at=datetime.datetime.combine(sent, datetime.time(9, 0), tzinfo=datetime.UTC),
        final_version=version,
    )


@pytest.fixture
def today():
    return timezone.localdate()


# --- A: the two scopes share one shell -----------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("osakond", ov.SCOPE_DEPARTMENT),
        ("valdkonniti", ov.SCOPE_AREAS),
        # The retired third scope, which is now an unrecognised value like any
        # other and therefore an old bookmark that still opens (docs/adr/0039).
        ("tiim", ov.SCOPE_DEPARTMENT),
        ("midagi-muud", ov.SCOPE_DEPARTMENT),
        (None, ov.SCOPE_DEPARTMENT),
    ],
)
def test_the_scope_comes_from_the_url_and_falls_back(value, expected):
    assert ov.scope_from(value) == expected


@pytest.mark.parametrize(
    ("query", "marker"),
    [
        ("?vaade=osakond", "Vajab sekkumist"),
        ("?vaade=valdkonniti", "Valdkond"),
    ],
)
def test_each_scope_renders_its_own_body_under_the_same_shell(
    client, department_head, query, marker
):
    client.force_login(department_head)

    body = client.get(reverse(OVERVIEW) + query).content.decode()

    # The shell is the same in both.
    assert "Osakond" in body
    assert "Kogu osakond" in body and "Valdkonniti" in body
    assert marker in body


# --- B: an ownerless Matter is never silently omitted ---------------------


def test_an_ownerless_matter_reaches_the_intervention_list_and_the_count(department_head, today):
    create_matter(
        title="Meresõiduohutuse seaduse eelnõu",
        reference_year=2026,
        actor=department_head,
        received_date=today - timedelta(days=2),
    )

    built = dep.build_department(department_head, is_head=True, today=today)
    reasons = {(row.reason, row.matter.title) for row in built.interventions}

    assert (ov.REASON_OWNERLESS, "Meresõiduohutuse seaduse eelnõu") in reasons
    assert next(f for f in built.seis if f.key == "unassigned").value == 1


# --- C: a restricted Matter is counted for those entitled, masked for the rest


def test_a_restricted_title_never_reaches_a_non_participants_page(
    client, specialist, reader, today
):
    create_matter(
        title="Konkurentsiseaduse järelevalvemenetlus",
        owner=specialist,
        reference_year=2026,
        visibility=Visibility.RESTRICTED,
        actor=specialist,
    )
    client.force_login(reader)

    body = client.get(reverse(OVERVIEW) + "?vaade=osakond").content.decode()

    assert "Konkurentsiseaduse" not in body


# --- E: only a real deadline is called a deadline ------------------------


def test_the_deadline_list_holds_deadlines_and_not_review_dates(department_head, specialist, today):
    deadline_matter = create_matter(
        title="Tähtajaga teema", owner=specialist, reference_year=2026, actor=specialist
    )
    set_next_action(
        matter=deadline_matter,
        text="TEEN tähtajaks",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today + timedelta(days=1),
        actor=specialist,
    )
    milestone_matter = create_matter(
        title="Olulise tähtajaga teema",
        owner=specialist,
        reference_year=2026,
        actor=specialist,
    )
    add_important_date(
        matter=milestone_matter,
        title="OLULINE tähtaeg",
        date_value=today + timedelta(days=2),
        period_end=today + timedelta(days=2),
        actor=specialist,
    )
    waiting_matter = create_matter(
        title="Ootav teema", owner=specialist, reference_year=2026, actor=specialist
    )
    set_next_action(
        matter=waiting_matter,
        text="OOTAN vastust",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.EXPECTED_AROUND,
        target_date=today + timedelta(days=3),
        actor=specialist,
    )

    items = wi.work_items(department_head, today=today)
    deadlines = {item.text for item in ov.real_deadlines(items)}

    assert "TEEN tähtajaks" in deadlines
    assert "OLULINE tähtaeg" in deadlines
    assert "OOTAN vastust" not in deadlines


# --- F: the governed vocabulary, and the legacy areas it must not lose ---


def test_the_area_table_uses_the_governed_vocabulary(department_head, today):
    """As many rows as there are offered areas, read rather than restated.

    Counted from `selectable_policy_areas` rather than written out, because the
    number is not the point: the point is that this table and Uus teema offer
    the same vocabulary. A literal here would have to be edited every time the
    department adds or withdraws a label, and the edit that forgot would look
    like a broken table rather than a stale test (Uus teema redesign §7.1).
    """
    from app.taxonomy.vocabulary import selectable_policy_areas

    offered = selectable_policy_areas().count()
    assert offered
    assert PolicyArea.objects.filter(is_active=True).count() == offered

    rows, empty = ov.area_rows(department_head, today, [])

    assert len(rows) + empty == offered


def test_a_retired_area_with_open_work_is_kept_and_marked(department_head, specialist, today):
    """A Matter classified in 2019 does not vanish because the vocabulary moved on.

    It is kept, marked *varasem*, and never remapped onto a current area — a
    silent reclassification would be a decision nobody reviewed.
    """
    # A real retired key, not an invented one. `halduskoormus` is one of the
    # five the working-vocabulary migration retired, so this exercises the
    # actual shape of the problem rather than a fixture that resembles it
    # (app/taxonomy/migrations/0003_working_policy_area_vocabulary.py).
    legacy = PolicyArea.objects.get(key="halduskoormus")
    assert legacy.is_active is False
    matter = create_matter(
        title="Vana valdkonna teema",
        owner=specialist,
        reference_year=2026,
        actor=specialist,
    )
    matter.policy_areas.add(legacy)

    rows, _ = ov.area_rows(department_head, today, [])
    row = next(row for row in rows if row.key == "halduskoormus")

    assert row.is_legacy is True
    assert row.open_count == 1


# --- G: "vastutajata" means nobody at all --------------------------------


def test_an_area_is_unowned_only_when_nobody_owns_any_of_its_work(
    department_head, specialist, today
):
    watched = PolicyArea.objects.filter(is_active=True).order_by("sort_order")[0]
    unwatched = PolicyArea.objects.filter(is_active=True).order_by("sort_order")[1]

    for index in range(5):
        owned = create_matter(
            title=f"Vaadatud {index}",
            owner=specialist,
            reference_year=2026,
            actor=specialist,
        )
        owned.policy_areas.add(watched)
    stray = create_matter(title="Jaotamata üks", reference_year=2026, actor=specialist)
    stray.policy_areas.add(watched)

    orphan = create_matter(title="Jaotamata kaks", reference_year=2026, actor=specialist)
    orphan.policy_areas.add(unwatched)

    rows, _ = ov.area_rows(department_head, today, [])
    by_key = {row.key: row for row in rows}

    # Five owned files and one unassigned: somebody is watching this area.
    assert by_key[watched.key].is_unowned is False
    assert by_key[unwatched.key].is_unowned is True

    page = ov.build_overview(department_head, scope=ov.SCOPE_AREAS, today=today)
    assert [row.key for row in page.unowned_areas] == [unwatched.key]


# --- H: an opinion is a canonical Submission, not an archive file --------


def test_only_canonical_sent_submissions_count_as_opinions(department_head, specialist, today):
    """The 767 historical archive letters are evidence, not this year's output."""
    matter = create_matter(
        title="Arvamusega teema", owner=specialist, reference_year=2026, actor=specialist
    )
    _sent(matter, title="Selle kuu arvamus", sent=today)
    factories.SubmissionFactory(matter=matter, status=SubmissionStatus.DRAFT)
    # An archive row, which is what the historical corpus looks like here.
    factories.ArchiveMatterFactory(title="Ajalooline kiri", record_mode=RecordMode.ARCHIVE)

    built = dep.build_department(department_head, is_head=True, today=today)

    assert next(f for f in built.seis if f.key == "sent").value == 1
    assert {row.label: row.count for row in built.reporting}["Saadetud arvamusi"] == 1


# --- I: every figure leads somewhere real --------------------------------


def test_every_seis_figure_resolves_to_a_real_list(client, department_head, today):
    """No dead numbers. A figure a reader cannot follow is a figure they stop trusting.

    Quantified over the strip rather than over a list of keys, so a figure added
    later is inside this guarantee without anybody remembering to add it. The
    one figure that deliberately carries no destination is `sent`, and that
    exception is pinned by name in `tests/test_department_page.py`; here it is
    simply skipped, because this test is about links that exist being real.
    """
    client.force_login(department_head)
    built = dep.build_department(department_head, is_head=True, today=today)

    assert built.seis
    for figure in built.seis:
        if not figure.url:
            continue
        response = client.get(figure.url)
        assert response.status_code == 200, f"{figure.key} -> {figure.url}"


def test_a_figure_that_opens_the_register_carries_a_filter(department_head, today):
    """A figure linking to the bare register is the defect this page keeps finding.

    Not every figure opens the register — *saadetud* states a number it cannot
    open — but every one that does must arrive somewhere narrowed, or the reader
    is looking at the whole corpus and a number that no longer means anything.
    `register_link` returns the bare register plus the results anchor when its
    parameter dict is empty, so the anchor test next door cannot see this.
    """
    from urllib.parse import parse_qsl, urlparse

    built = dep.build_department(department_head, is_head=True, today=today)
    register = reverse("matters:matter_list")

    for figure in built.seis:
        if not figure.url.startswith(register):
            continue
        assert dict(parse_qsl(urlparse(figure.url).query)), (
            f"{figure.key} opens an unfiltered register"
        )


def test_the_month_filter_narrows_the_sent_list(client, department_head, specialist):
    """The one small read-only filter this round added, doing what the figure promises."""
    now = timezone.now()
    matter = create_matter(
        title="Arvamusega teema", owner=specialist, reference_year=2026, actor=specialist
    )
    _sent(matter, title="Selle kuu arvamus", sent=now.date())
    _sent(matter, title="Eelmise aasta arvamus", sent=now.date() - timedelta(days=400))
    client.force_login(department_head)

    url = f"{reverse('submissions:sent')}?aasta={now.year}&kuu={now.month}"
    body = client.get(url).content.decode()

    assert "Selle kuu arvamus" in body
    assert "Eelmise aasta arvamus" not in body
