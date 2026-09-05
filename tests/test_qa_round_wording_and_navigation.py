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

#: Wording that describes the technical mechanism rather than what the reader
#: needs to know. Checked against everything Statistika renders.
FORBIDDEN = ("pahavarakontrolli", "Pahavarakontroll", "skanner")


def all_text(*values: object) -> str:
    return " ".join(str(value) for value in values if value)


def test_the_metric_no_longer_names_the_scanner():
    spec = definition(keys.EXTRACTION_AWAITING_SCANNER)
    text = all_text(spec.label_et, spec.description_et, spec.notes_et)

    for jargon in FORBIDDEN:
        assert jargon not in text, jargon
    assert "tekstitöötlust" in spec.label_et.lower()


def test_the_metric_says_the_files_are_known_to_be_clean():
    """The explanatory half. Without it the new wording is merely vaguer.

    A reader has to be able to learn from the page that this is not a safety
    finding — that is the whole correction.
    """
    spec = definition(keys.EXTRACTION_AWAITING_SCANNER)
    text = all_text(spec.description_et, spec.notes_et).lower()

    assert "pahavaravabad" in text
    assert "ei ole viga" in text or "ei tähenda" in text


def test_the_chart_segment_and_the_note_agree_with_the_metric(world, reporting_context):
    context = reporting_context(world.martin)
    labels = {segment.label for segment in documents.extraction_states(context)}

    assert "Ootab tekstitöötlust" in labels
    assert not any(jargon in " ".join(labels) for jargon in FORBIDDEN)

    note = " ".join(documents.extraction_awaiting_scanner(context).notes)
    assert "pahavaravabad" in note


def test_extraction_readiness_is_not_a_business_queue(world, reporting_context, settings):
    """It is a property of this system's pipeline, not of the record.

    While it sat among the Andmekvaliteet queues readers took it for outstanding
    work. The number did not go away — it is reported in full in the extraction
    section on the same tab.
    """
    # With the gate on, so the row would be non-zero and therefore rendered if
    # it were still in the list. A queue absent because it happens to be empty
    # proves nothing.
    settings.REAL_DATA_ALLOWED = True
    context = reporting_context(world.martin)
    assert documents.extraction_awaiting_scanner(context).value > 0

    assert "awaiting_scanner" not in {row.key for row in quality.queues(context)}


def test_the_bumped_metric_reports_the_same_population(world, reporting_context, settings):
    """The wording moved; the measurement did not.

    Version 2 of a metric is a promise that the number means the same thing it
    did — and this one has to, because the extraction gate is unchanged.
    """
    from app.documents.extraction.orchestrator import awaiting_scanner as queue_awaiting
    from app.reporting.selectors.documents import visible_versions

    settings.REAL_DATA_ALLOWED = True
    context = reporting_context(world.head)
    spec = definition(keys.EXTRACTION_AWAITING_SCANNER)
    assert spec.version == 2

    reported = set(documents.awaiting_scanner(context).values_list("pk", flat=True))
    gated = set(queue_awaiting().values_list("pk", flat=True))
    visible = set(visible_versions(context).values_list("pk", flat=True))
    assert reported == gated & visible


def test_no_file_became_extractable_because_a_label_was_rewritten(normal_matter, capture_evidence):
    """The stored security state is not touched by any of this.

    Falsifying ``malware_scan_state`` would have made the wording true and the
    record a lie. The correction is to the sentence, never to the column.
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
    (docs/adr/0060) and a draft is now a row in the `Arvamused` block on
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
    duplication the retired page created (docs/adr/0060 §15, §16).
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
