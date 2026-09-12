"""Juristid as a genuinely empty operational application.

The world this file builds is the one a clean install — or a production reset —
actually leaves behind: the reviewed reference data and the ordinary people, and
no business records of any kind. No Matter, no Entry, no NextAction, no
Submission, no Document, no evidence, no engagement, no fact, no related
material, no register row, no archive item, and an empty search index.

That combination is not reachable from the other test modules. Every world in
`tests/` exists to give some behaviour something to be about, so each of them
supplies at least the records the surface under test needs — which means the
question *this* file asks, "what does the product say when it has nothing to say
yet", has no home anywhere else.

Three rules for what goes in here.

**A clear empty state is not a defect.** «Selles vaates ei ole ühtegi teemat» is
the product working. What this file asserts is the narrower thing: that no
surface crashes, and that no surface prints a figure the product has decided not
to stand behind.

**The smoke contract is a contract, not a screenshot.** It asks each principal
read surface for its status code and nothing else. Assertions about wording
belong beside the behaviour they describe, and there are three of those below —
one per defect this round actually found.

**The reference data is seeded, never created.** `reference_baseline` holds the
migrated vocabulary; a fixture that created a PolicyArea here would collide with
`taxonomy/0002` (tests/conftest.py).
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from app.documents.models import Document
from app.matters.models import Entry, Matter, MatterReferenceSequence
from app.organisations.models import Organisation
from app.reporting import metric_catalogue as keys
from app.reporting.context import ReportingContext, parse_period
from app.reporting.metric_types import MetricStatus, Unit
from app.reporting.services import compute
from app.search.models import SearchDocument
from app.submissions.models import Submission
from app.taxonomy.models import PolicyArea
from app.workflow.models import NextAction
from tests import factories

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# The empty world
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_world(db):
    """Reference data and people, and nothing that has happened yet.

    The organisations are the reviewed baseline itself, applied exactly as
    ``manage.py reference_data apply`` applies it, so the pickers here hold what
    a real clean install holds rather than an invented catalogue. The policy
    areas need nothing: they arrive by migration and are already there
    (app/core/reference_data.py).
    """
    from app.core.reference_data import apply_reference_plan, build_reference_plan

    apply_reference_plan(expected_sha256=build_reference_plan().digest())

    class World:
        pass

    world = World()
    world.specialist = factories.UserFactory(display_name="Jaan Jurist")
    world.colleague = factories.UserFactory(display_name="Mari Mets")
    world.head = factories.DepartmentHeadFactory(display_name="Kati Juht")
    world.administrator = factories.AdministratorFactory(display_name="Aadu Haldur")
    world.today = timezone.localdate()
    return world


@pytest.fixture
def empty_context(empty_world):
    def build(viewer, *, period: str = "koik", **overrides):
        return ReportingContext(
            viewer=viewer,
            period=parse_period(period, empty_world.today),
            today=empty_world.today,
            now=timezone.now(),
            **overrides,
        )

    return build


#: One sent opinion, built through the services rather than the models, so that
#: "there is now something to count" means what the product means by it: a
#: submission with a real sent moment and the exact file that went out.
PDF = b"%PDF-1.4 synthetic final opinion"


def _one_sent_submission(actor):
    from app.documents.enums import DocumentRole
    from app.documents.services import add_evidence_version, create_document
    from app.matters.services import create_matter
    from app.submissions.services import (
        create_submission,
        mark_submission_sent,
        select_final_evidence,
    )

    matter = create_matter(title="Teema, millelt arvamus välja läks", actor=actor, owner=actor)
    submission = create_submission(matter=matter, title="Koja arvamus", actor=actor)
    document = create_document(
        matter=matter,
        title="Lõplik arvamus",
        role=DocumentRole.KODA_SUBMISSION_FINAL,
        created_by=actor,
    )
    version = add_evidence_version(
        document=document,
        content=PDF,
        original_filename="arvamus.pdf",
        mime_type="application/pdf",
        uploaded_by=actor,
    )
    select_final_evidence(submission=submission, version=version, actor=actor)
    submission.refresh_from_db()
    return mark_submission_sent(submission=submission, actor=actor)


def test_the_world_this_file_describes_really_is_empty(empty_world):
    """The premise, asserted once. Every test below leans on it.

    A fixture that quietly grew a Matter would turn this whole file green for
    the wrong reason, and the failure would look like the product working.
    """
    assert Matter.objects.count() == 0
    assert Entry.objects.count() == 0
    assert NextAction.objects.count() == 0
    assert Submission.objects.count() == 0
    assert Document.objects.count() == 0
    assert SearchDocument.objects.count() == 0
    assert MatterReferenceSequence.objects.count() == 0

    # …and the reference data is there, which is the other half of "empty
    # operational application" rather than "empty database".
    assert Organisation.objects.count() >= 15
    assert PolicyArea.objects.count() >= 9


# ---------------------------------------------------------------------------
# A. the smoke contract
# ---------------------------------------------------------------------------
#
# Every principal read surface, by route name, answered for somebody who has
# never done anything. Parametrised on the name so a failure names the route.

PRINCIPAL_SURFACES = [
    "core:home",
    "matters:my_work",
    "matters:department",
    "matters:matter_list",
    "matters:matter_create",
    "matters:inbox",
    "matters:intake",
    "matters:organisation_choices",
    "submissions:sent",
    "submissions:archive",
    "submissions:embedded_block",
    "search:search",
    "search:suggestions",
    "reporting:overview",
    "reporting:matters",
    "reporting:activity",
    "reporting:historical",
    "reporting:quality",
    "reporting:submissions",
    "reporting:materials",
    "reporting:definitions",
    "intelligence:important_dates",
    "intelligence:effective_dates",
    "intelligence:work_victories",
    "core:release_notes",
    "core:design_tokens",
]


@pytest.mark.parametrize("route", PRINCIPAL_SURFACES)
def test_every_principal_surface_answers_on_an_empty_application(client, empty_world, route):
    client.force_login(empty_world.specialist)

    response = client.get(reverse(route), follow=True)

    assert response.status_code == 200, route


@pytest.mark.parametrize("route", PRINCIPAL_SURFACES)
def test_the_department_head_sees_the_same_surfaces_answer(client, empty_world, route):
    """The head's page carries Meeskond and Tehtud, which nobody else renders.

    Worth its own pass rather than a role parameter on the one above: the two
    sections are built from a different query and the empty case is therefore a
    different code path (app/matters/department_views.py).
    """
    client.force_login(empty_world.head)

    response = client.get(reverse(route), follow=True)

    assert response.status_code == 200, route


ADMINISTRATOR_SURFACES = [
    "legacy_import:opinion_queue",
    "legacy_import:review_queue",
    "legacy_import:opinion_archive_browse",
]


@pytest.mark.parametrize("route", ADMINISTRATOR_SURFACES)
def test_the_migration_queues_answer_for_an_administrator(client, empty_world, route):
    client.force_login(empty_world.administrator)

    assert client.get(reverse(route)).status_code == 200, route


@pytest.mark.parametrize("slug", ["teemad", "arvamused", "materjalid", "andmekvaliteet"])
def test_every_export_is_a_header_and_no_rows(client, empty_world, slug):
    """An export of nothing is a file with its columns, not a failure.

    Somebody who downloads this and opens it in Excel should see the shape of
    the answer, so that the file is recognisably the same one they will get in
    six months with rows in it (app/reporting/exports.py).
    """
    client.force_login(empty_world.specialist)

    response = client.get(reverse("reporting:export", kwargs={"slug": slug}))

    assert response.status_code == 200
    body = b"".join(response.streaming_content).decode("utf-8-sig")
    lines = [line for line in body.splitlines() if line.strip()]
    assert len(lines) == 1, lines
    assert ";" in lines[0]


def test_an_out_of_range_page_number_does_not_break_the_register(client, empty_world):
    """`?leht=5` over nothing is page one of one, not «lehekülg 5 / 0»."""
    client.force_login(empty_world.specialist)

    body = client.get(reverse("matters:matter_list"), {"leht": "5"}).content.decode()

    assert "/ 0" not in body


def test_search_answers_without_a_single_projected_row(client, empty_world):
    """The index is genuinely empty here — nothing has ever been written to it."""
    assert SearchDocument.objects.count() == 0
    client.force_login(empty_world.specialist)

    response = client.get(reverse("search:search"), {"q": "maksud"})

    assert response.status_code == 200
    assert "0 vastet" in response.content.decode()


def test_uus_teema_is_reachable_when_there_are_no_teemad(client, empty_world):
    """The one surface that must never be hidden by having nothing yet.

    Everything else on the bar is a list, and a list of nothing is still a
    destination; this is the only route by which an empty application stops
    being one.
    """
    client.force_login(empty_world.specialist)

    body = client.get(reverse("matters:my_work")).content.decode()

    assert reverse("matters:matter_create") in body


def test_a_colleagues_desk_answers_when_they_have_no_matters(client, empty_world):
    """Person and team views are keyed on an id, not on having work.

    Left out of the parametrised sweep because it is the one principal surface
    that takes an argument, and the argument is the point: the page has to exist
    for somebody who has never been given anything (app/matters/views.py,
    `person_work`).
    """
    client.force_login(empty_world.head)

    response = client.get(reverse("matters:person_work", kwargs={"pk": empty_world.colleague.pk}))

    assert response.status_code == 200
    assert empty_world.colleague.display_name in response.content.decode()


def test_the_reference_pickers_are_populated_before_any_matter_exists(client, empty_world):
    """Uus teema's institutions and valdkonnad come from reference data.

    They are the fields most easily built from "whatever the existing Matters
    use", and that construction is invisible until the day there are none.
    """
    client.force_login(empty_world.specialist)

    body = client.get(reverse("matters:matter_create")).content.decode()

    assert "Rahandusministeerium" in body
    assert "Keskkond" in body


# ---------------------------------------------------------------------------
# B. the first Matter
# ---------------------------------------------------------------------------


def test_the_first_matter_after_a_reset_gets_the_years_first_reference(empty_world):
    """No prior Matter, no previous year, no register row — and it still numbers.

    The allocator is a per-year counter row rather than `max(reference_number)`
    over the Matters, which is what makes this work; the test is here because
    the two are indistinguishable in every world that already has data in it
    (app/matters/services.py, `allocate_matter_reference`).

    **`1` here is a fact about a migrated database, not about a reset one.** The
    counter row is what carries sequence continuity, so an instance that has
    ever imported the register keeps issuing from wherever that import reserved
    to, and an operational reset that preserves the counter — as the intended
    one does — continues from there rather than reopening numbers a
    re-ingestion would re-create. What this test fixes is the narrower
    guarantee: that allocation needs nothing but the year.
    """
    from app.matters.services import create_matter

    assert Matter.objects.count() == 0
    assert MatterReferenceSequence.objects.count() == 0

    matter = create_matter(
        title="Esimene teema pärast lähtestamist",
        actor=empty_world.specialist,
        owner=empty_world.specialist,
    )

    assert matter.reference_year == empty_world.today.year
    assert matter.reference_number == 1


def test_the_first_matter_becomes_the_register(client, empty_world):
    from app.matters.services import create_matter

    create_matter(
        title="Esimene teema pärast lähtestamist",
        actor=empty_world.specialist,
        owner=empty_world.specialist,
    )
    client.force_login(empty_world.specialist)

    body = client.get(reverse("matters:matter_list")).content.decode()

    assert "Esimene teema pärast lähtestamist" in body
    assert Matter.objects.count() == 1


def test_the_first_matter_reaches_its_owners_own_surfaces(client, empty_world):
    from app.matters.services import create_matter

    create_matter(
        title="Esimene teema pärast lähtestamist",
        actor=empty_world.specialist,
        owner=empty_world.specialist,
    )
    client.force_login(empty_world.specialist)

    mine = client.get(reverse("matters:my_work")).content.decode()
    department = client.get(reverse("matters:department")).content.decode()

    assert "Esimene teema pärast lähtestamist" in mine
    # «1 avatud teema», nominative: the partitive is right from two upwards and
    # was the QA-12 slip (app/core/templatetags/counts.py).
    assert "1 avatud teema" in department


def test_the_first_matter_is_searchable(client, empty_world):
    """The projection is written on creation, not by a later sweep."""
    from app.matters.services import create_matter

    create_matter(
        title="Esimene teema pärast lähtestamist",
        actor=empty_world.specialist,
        owner=empty_world.specialist,
    )
    assert SearchDocument.objects.count() == 1
    client.force_login(empty_world.specialist)

    body = client.get(reverse("search:search"), {"q": "lähtestamist"}).content.decode()

    assert "Esimene teema pärast lähtestamist" in body


# ---------------------------------------------------------------------------
# C. what the statistics may say about nothing
# ---------------------------------------------------------------------------


PERCENTAGE_METRICS = [
    keys.SEARCHABLE_DOCUMENT_COVERAGE,
    keys.OPINION_ARCHIVE_MATTER_COVERAGE,
    keys.OPINION_ARCHIVE_LINK_COVERAGE,
    keys.HISTORICAL_SUBMISSION_COVERAGE,
    keys.SUBMISSION_RECIPIENT_COVERAGE,
]


def test_the_catalogue_has_no_percentage_this_file_forgot():
    """The list above is every PERCENT metric, checked rather than trusted.

    A sixth percentage added later would otherwise be exempt from the rule the
    next test states, and nothing would say so.
    """
    from app.reporting.metric_catalogue import CATALOGUE

    catalogued = {
        definition.key for definition in CATALOGUE.values() if definition.unit == Unit.PERCENT
    }
    assert catalogued == set(PERCENTAGE_METRICS)


@pytest.mark.parametrize("key", PERCENTAGE_METRICS)
def test_a_share_of_nothing_is_declined_rather_than_reported_as_zero(
    empty_world, empty_context, key
):
    """«0%» over an empty denominator is a measurement nobody made.

    `MetricResult.coverage_percentage` has always returned None here and
    `opinion_archive_link_coverage` has always declined by hand, but `grade`
    short-circuited on a falsy denominator and let the other four through. On
    Andmekvaliteet that put «Arhiivi failid teemaga seotud 0%» directly above
    «Arhiivi failid teemaga seotud (seoste järgi) Ebapiisavad andmed» — the same
    question about the same empty archive, answered two ways
    (app/reporting/metric_types.py, `grade`).
    """
    result = compute(key, empty_context(empty_world.specialist))

    assert result.coverage_denominator in (0, None)
    assert result.status == MetricStatus.INSUFFICIENT_DATA
    assert result.has_value is False


def test_a_share_of_something_is_still_reported(empty_world, empty_context):
    """The guard is about an empty denominator, not about a zero numerator.

    A real 0 % — nothing covered *out of something* — is a measurement and has
    to keep being published, or the fix above would have replaced one lie with
    another.
    """
    _one_sent_submission(empty_world.specialist)

    result = compute(keys.SUBMISSION_RECIPIENT_COVERAGE, empty_context(empty_world.specialist))

    assert result.coverage_denominator == 1
    assert result.value == 0
    assert result.has_value is True


def test_the_overview_does_not_print_a_figure_its_own_catalogue_declined(client, empty_world):
    """«arvamusi välja 0» is the claim `minimum_population=1` exists to refuse.

    The Tegevus tab already said «Ebapiisavad andmed» for `SUBMISSIONS_SENT` on
    an empty application; the Ülevaade rail one click away printed a confident
    zero for the same metric over the same period, because it read
    `result.value` without reading `result.status`
    (app/reporting/overview_strip.py).
    """
    client.force_login(empty_world.specialist)

    body = client.get(reverse("reporting:overview")).content.decode()

    assert "arvamusi välja" not in body


def test_the_declined_figure_comes_back_once_there_is_something_to_count(client, empty_world):
    _one_sent_submission(empty_world.specialist)
    client.force_login(empty_world.specialist)

    body = client.get(reverse("reporting:overview")).content.decode()

    assert "arvamusi välja" in body


# ---------------------------------------------------------------------------
# D. an empty corpus is not an over-narrow filter
# ---------------------------------------------------------------------------


def test_the_archive_says_it_is_empty_rather_than_blaming_the_filters(client, empty_world):
    """Two statements about the corpus, and only one of them is true here.

    «Ükski arhiivi kiri ei vasta nendele tingimustele» sends an operator off to
    loosen filters that were never narrowing anything. The same page's sibling
    in the Arvamused workspace has always split the two
    (templates/submissions/archive.html), and the view itself already draws the
    distinction for a refused query.
    """
    client.force_login(empty_world.administrator)

    body = client.get(reverse("legacy_import:opinion_archive_browse")).content.decode()

    assert "Arhiivis ei ole ühtegi kirja." in body
    assert "ei vasta nendele tingimustele" not in body
