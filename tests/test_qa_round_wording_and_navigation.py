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
    body = client.get(reverse("matters:overview")).content.decode()
    bar = body.split('<nav class="topnav"', 1)[1].split("</nav>", 1)[0]

    assert ">Saabunud<" not in bar
    for destination in (">Ülevaade<", ">Minu töö<", ">Teemad<"):
        assert destination in bar


def test_saabunud_is_still_a_working_page(client, specialist):
    """The route, the models and the data are untouched — only the link moved."""
    client.force_login(specialist)
    response = client.get(reverse("matters:inbox"))

    assert response.status_code == 200
    assert "Saabunud" in response.content.decode()


def test_ulevaade_still_offers_the_way_in(client, specialist):
    """*Uued teemad* on the facts rail is where the question actually occurs."""
    client.force_login(specialist)
    body = client.get(reverse("matters:overview")).content.decode()

    assert reverse("matters:inbox") in body
