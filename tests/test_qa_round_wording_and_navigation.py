"""Two statements the product makes to a reader, and what they are allowed to say.

Both come from the same QA round and neither is about arithmetic.

**"Ootab pahavarakontrolli."** All files in the Juristid corpus are known to be
free of malware. The state that wording described is real — those evidence
versions are not yet offered to the extraction queue — but naming the number
after the scanner told every reader that their own archive might be infected and
that somebody had an unresolved safety question to answer. Neither is true, and
a statistic that leaves a department believing its own archive is suspect is
worse than no statistic.

The gate itself does not move, and this file asserts that too: the stored
``malware_scan_state``, the orchestrator's eligibility rule and the population
the metric reports are all untouched. Only the sentence changed.

**Saabunud on the bar.** It is a triage surface somebody opens when they are
triaging, not a destination in the daily rotation. It came off the primary
navigation; its route, its models and its data did not move, and the way in is
Ülevaade's *Uued teemad* rail. Both halves are asserted, because a route quietly
deleted with the link would take the intake surface out of the product.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.documents.enums import ExtractionState, MalwareScanState
from app.reporting import metric_catalogue as keys
from app.reporting.metric_catalogue import definition
from app.reporting.selectors import documents, quality

pytestmark = pytest.mark.django_db

#: Wording that describes a technical mechanism rather than what the reader
#: needs to know — and, since docs/adr/0072, a mechanism that no longer exists
#: at all. Checked against every metric definition Statistika publishes.
FORBIDDEN = ("pahavarakontroll", "Pahavarakontroll", "skanner", "Skanner")


def all_text(*values: object) -> str:
    return " ".join(str(value) for value in values if value)


def test_no_statistika_metric_names_a_scanner_that_does_not_exist():
    """The jargon check outlived the thing it was written about, and widens.

    It used to hold one metric to plain wording, because «Ootab
    pahavarakontrolli» made readers believe their own archive was suspect.
    There is no scanner at all now (docs/adr/0072), so a metric that mentioned
    one would be describing a subsystem this deployment does not have — which
    is worse than jargon. Every published definition is checked, not one.
    """
    from app.reporting.metric_catalogue import CATALOGUE

    for key, spec in CATALOGUE.items():
        text = all_text(spec.label_et, spec.description_et, spec.notes_et)
        for jargon in FORBIDDEN:
            assert jargon not in text, f"{key}: {jargon}"


def test_the_metric_says_a_file_read_at_intake_is_finished():
    """The explanatory half. Without it the new wording is merely vaguer.

    A reader has to be able to learn from the page that this is a terminal
    success rather than outstanding work, *and* what it costs — those files
    have no permanent text, so their content is not searchable. Saying only the
    first half would be the same mistake in the other direction.
    """
    spec = definition(keys.EXTRACTION_INTAKE_READ)
    text = all_text(spec.label_et, spec.description_et, spec.notes_et).lower()

    assert "teema loomisel" in spec.label_et.lower()
    assert "ei loeta uuesti" in text
    assert "ei ole viga" in text


def test_the_chart_segment_and_the_note_agree_with_the_metric(world, reporting_context):
    context = reporting_context(world.martin)
    labels = {segment.label for segment in documents.extraction_states(context)}

    assert "Loetud teema loomisel" in labels
    assert not any(jargon in " ".join(labels) for jargon in FORBIDDEN)

    note = " ".join(documents.extraction_intake_read(context).notes)
    assert "vormile" in note
    assert "teadlik valik" in note


def test_the_chart_segments_add_up_to_the_visible_versions(world, reporting_context):
    """Five states, one population. A state left out of the chart is a file
    the reader cannot see anywhere on the tab."""
    context = reporting_context(world.martin)
    segments = documents.extraction_states(context)
    assert sum(segment.value for segment in segments) == documents.visible_versions(context).count()


def test_extraction_readiness_is_not_a_business_queue(world, reporting_context):
    """It is a property of this system's pipeline, not of the record.

    While it sat among the Andmekvaliteet queues readers took it for outstanding
    work. The number did not go away — it is reported in full in the extraction
    section on the same tab.
    """
    context = reporting_context(world.martin)
    # Non-zero, so a row absent from the queue list is absent because it was
    # taken out rather than because it happens to be empty.
    assert documents.extraction_intake_read(context).value > 0

    keys_shown = {row.key for row in quality.queues(context)}
    assert "awaiting_scanner" not in keys_shown
    assert keys.EXTRACTION_INTAKE_READ not in keys_shown


def test_the_bumped_metric_reports_what_its_new_version_says(world, reporting_context):
    """A version bump is a promise about what changed, and this one changed the
    measurement — so it is asserted rather than described.

    `EXTRACTION_ELIGIBLE` was the positive side of the malware gate: the files a
    worker was allowed to open. With the gate gone, the only surviving reading
    of that question is whether any parser opens the format at all, which is
    also the searchability denominator.
    """
    from app.reporting.selectors.documents import openable, visible_versions

    context = reporting_context(world.head)
    spec = definition(keys.EXTRACTION_ELIGIBLE)
    assert spec.version == 2

    reported = set(openable(context).values_list("pk", flat=True))
    visible = set(visible_versions(context).values_list("pk", flat=True))
    not_applicable = set(
        visible_versions(context)
        .filter(extraction_state=ExtractionState.NOT_APPLICABLE)
        .values_list("pk", flat=True)
    )
    assert reported == visible - not_applicable
    assert not_applicable, "the world must hold one, or this asserts nothing"


def test_no_file_became_extractable_because_a_label_was_rewritten(normal_matter, capture_evidence):
    """A new upload is still exactly as unread as it was.

    The original point of this test was that a wording correction had not
    quietly falsified the stored security state. It is kept, and now says the
    weaker true thing: removing the scanner did not make an ordinary upload
    arrive pre-read. `PENDING` on both columns, and the second one still means
    "nothing has derived text from these bytes".
    """
    version = capture_evidence(
        normal_matter, b"%PDF-1.4 synthetic", "naidis.pdf", "application/pdf"
    )

    assert version.malware_scan_state == MalwareScanState.PENDING
    assert version.extraction_state == ExtractionState.PENDING


# ---------------------------------------------------------------------------
# Saabunud
# ---------------------------------------------------------------------------


def test_saabunud_is_not_in_the_primary_navigation(client, specialist):
    client.force_login(specialist)
    body = client.get(reverse("matters:department")).content.decode()
    bar = body.split('<nav class="topnav"', 1)[1].split("</nav>", 1)[0]

    assert ">Saabunud<" not in bar
    for destination in (">Osakond<", ">Minu asjad<", ">Teemad<"):
        assert destination in bar


def test_saabunud_is_still_a_working_page(client, specialist):
    """The route, the models and the data are untouched — only the link moved."""
    client.force_login(specialist)
    response = client.get(reverse("matters:inbox"))

    assert response.status_code == 200
    assert "Saabunud" in response.content.decode()


def test_the_department_page_still_offers_the_way_in(client, specialist):
    """*Uued teemad* on the facts rail is where the question actually occurs."""
    client.force_login(specialist)
    body = client.get(reverse("matters:department")).content.decode()

    assert reverse("matters:inbox") in body


# ---------------------------------------------------------------------------
# Koja arvamused
# ---------------------------------------------------------------------------
#
# The third wording correction of the same shape, found while integrating this
# round with the Uus teema redesign. The opinions section on a Matter lists
# every Submission the reader may see and counts all of them, but it was headed
# *Väljasaadetud arvamused* — so a colleague's Koostamisel draft appeared under
# a heading saying it had been sent, and the count above it described a
# population the heading did not name.
#
# A label change only. The query is untouched, and the tests below assert that
# in both directions: the draft is still listed and still counted, and the
# heading no longer claims it left the building.


@pytest.fixture
def drafted_opinion(normal_matter, specialist):
    from app.submissions.services import create_submission

    return create_submission(
        matter=normal_matter,
        title="Koostamisel arvamus",
        actor=specialist,
    )


def test_a_draft_appears_where_it_is_waiting_and_is_not_called_sent(
    client, specialist, normal_matter, drafted_opinion
):
    """The heading that made this test is retired; the statement it made is not.

    `Väljasaadetud arvamused` described a colleague's unfinished draft as
    something that had already left the building. The page carrying it is gone
    (docs/adr/0061) and a draft is now a row in the `Arvamused` block on
    Dokumendid — a block that says «koostamisel» and names no send at all.
    """
    from app.submissions.enums import SubmissionStatus

    assert drafted_opinion.status == SubmissionStatus.DRAFT

    client.force_login(specialist)
    page = client.get(reverse("matters:matter_documents", kwargs={"pk": normal_matter.pk}))
    body = page.content.decode()

    assert page.status_code == 200
    assert "Väljasaadetud arvamused" not in body
    assert "koostamisel" in body
    assert "Koostamisel arvamus" in body


def test_the_block_counts_exactly_the_drafts_it_renders(
    client, specialist, normal_matter, drafted_opinion
):
    """The population did not move, so the number must not have either."""
    from app.submissions.enums import SubmissionStatus
    from app.submissions.models import Submission

    client.force_login(specialist)
    page = client.get(reverse("matters:matter_documents", kwargs={"pk": normal_matter.pk}))

    rendered = page.context["opinion_drafts"]
    assert list(rendered) == [drafted_opinion]
    assert (
        len(rendered)
        == Submission.objects.filter(matter=normal_matter, status=SubmissionStatus.DRAFT).count()
    )


def test_a_sent_opinion_becomes_a_file_row_and_leaves_the_draft_block(
    client, specialist, normal_matter, drafted_opinion
):
    """Sent and unsent are now two different shapes, which is the whole point.

    A draft is an action somebody owes and lives in the `Arvamused` block. Once
    it has been sent it is a file the Matter holds, so it is a row in the table
    badged `Arvamus` — and it stops being listed twice, which is exactly the
    duplication the retired page created (docs/adr/0061 §15, §16).
    """
    from app.submissions.services import (
        attach_final_evidence,
        create_submission,
        mark_submission_sent,
    )

    sent = create_submission(matter=normal_matter, title="Saadetud arvamus", actor=specialist)
    attach_final_evidence(
        submission=sent,
        content=b"%PDF-1.4 arvamus",
        original_filename="arvamus.pdf",
        mime_type="application/pdf",
        actor=specialist,
    )
    mark_submission_sent(submission=sent, actor=specialist)

    client.force_login(specialist)
    page = client.get(reverse("matters:matter_documents", kwargs={"pk": normal_matter.pk}))
    body = page.content.decode()

    assert "Koostamisel arvamus" in body
    assert "arvamus.pdf" in body
    assert "Arvamus" in body
    # The sent one is a file row; only the draft is in the draft block.
    assert list(page.context["opinion_drafts"]) == [drafted_opinion]
