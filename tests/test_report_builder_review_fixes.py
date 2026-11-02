"""The owner's review of PR #585 (2026-11-02), Word builder findings, each held by a test.

* P1 — the Word file says what each event is and where a historical Excel
  date came from (read from the generated DOCX, not from Python objects);
* P1 — the download is the report last previewed, enforced by the server;
* P2 — a topic with no event in the report carries the reader's explanation;
* P2 — the action-plan opening belongs to its year.
"""

from __future__ import annotations

import io
from datetime import date

import pytest
from django.urls import reverse
from docx import Document

from app.audit.models import ChangeEvent
from app.matters.entry_enums import EntryKind
from app.matters.models import Entry, Matter
from app.reporting.report_builder import draft as report_draft
from app.reporting.report_builder import plan_text
from app.submissions.enums import SubmissionKind
from app.submissions.models import Submission
from tests import factories
from tests.report_builder_support import (
    as_post,
    entry,
    form_fields,
    preview_rows,
    register,
    replace,
    sent,
    values,
)

pytestmark = pytest.mark.django_db

PAGE = "reporting:report_builder"
PREVIEW = "reporting:report_preview"
EXPORT = "reporting:report_export"
OCTOBER = {"algus": "1.10.2026", "lopp": "31.10.2026"}


def open_page(client, period=OCTOBER):
    response = client.get(reverse(PAGE), period)
    assert response.status_code == 200
    return response


def post(client, fields, action="eelvaade", route=PREVIEW):
    return client.post(reverse(route), as_post(fields, toiming=action))


def export(client, fields):
    return post(client, fields, action="laadi", route=EXPORT)


def report_cells(response):
    """The report table's cells as (text, [(paragraph, italic, size pt)]) per cell."""
    assert response.status_code == 200, response.status_code
    table = Document(io.BytesIO(response.content)).tables[1]
    rows = []
    for row in table.rows[1:]:
        rows.append(
            [
                [
                    (
                        paragraph.text,
                        any(run.italic for run in paragraph.runs),
                        paragraph.runs[0].font.size.pt
                        if paragraph.runs and paragraph.runs[0].font.size
                        else None,
                    )
                    for paragraph in cell.paragraphs
                    if paragraph.text
                ]
                for cell in row.cells
            ]
        )
    return rows


def texts(rows):
    return [["\n".join(text for text, _, _ in cell) for cell in row] for row in rows]


# ---------------------------------------------------------------------------
# P1 — provenance in the Word file
# ---------------------------------------------------------------------------


def test_each_kind_of_event_says_what_it_is_in_the_word_file(signed_in, capture_evidence):
    opinion = factories.MatterFactory(title="A arvamus")
    answer = factories.MatterFactory(title="B märkusteta vastus")
    meeting = factories.MatterFactory(title="C kohtumine")
    historical = factories.MatterFactory(title="D ajalooline")
    sent(opinion, capture_evidence, day=date(2026, 10, 1), summary="Arvamus.")
    sent(
        answer,
        capture_evidence,
        day=date(2026, 10, 2),
        kind=SubmissionKind.NO_OBJECTION_RESPONSE,
        summary="Vastus.",
    )
    entry(meeting, day=date(2026, 10, 3), kind=EntryKind.HEARING, body="<p>Kuulamine.</p>")
    register(historical, day=date(2026, 10, 4), sheet="2026", row=27)

    rows = report_cells(export(signed_in, form_fields(open_page(signed_in))))

    by_topic = {row[0][0][0]: row[1] for row in rows}
    assert [text for text, _, _ in by_topic["A arvamus"]] == ["01.10", "Ametlik arvamus"]
    assert [text for text, _, _ in by_topic["B märkusteta vastus"]] == [
        "02.10",
        "Vastus: märkusi ja ettepanekuid ei ole",
    ]
    assert [text for text, _, _ in by_topic["C kohtumine"]] == ["03.10", "Istung või kuulamine"]
    assert [text for text, _, _ in by_topic["D ajalooline"]] == [
        "04.10",
        "Ajalooline Exceli VÄLJA-kuupäev",
        "leht 2026, rida 27",
    ]
    # The notes are small italic lines under a day in ordinary type.
    day, *notes = by_topic["D ajalooline"]
    assert day[1] is False
    assert all(italic and size == 8 for _, italic, size in notes)


def test_a_possible_duplicate_is_flagged_in_the_word_file(signed_in, capture_evidence):
    matter = factories.MatterFactory(title="Kahtlane kordus")
    sent(matter, capture_evidence, day=date(2026, 10, 2), title="Koja arvamus")
    register(matter, day=date(2026, 10, 6))

    preview = post(signed_in, form_fields(open_page(signed_in)))
    rows = texts(report_cells(export(signed_in, form_fields(preview))))

    (valja,) = [row for row in rows if "VÄLJA" in row[1]]
    assert valja[1].endswith("Võimalik kordus: arvamus 2.10.2026")
    # Never upgraded to a send: no recipient is invented, the Selgitus is empty.
    assert valja[2:] == ["", ""]
    # The preview shows the same cell.
    assert next(row for row in preview_rows(preview) if "VÄLJA" in row[1])[1] == valja[1]


# ---------------------------------------------------------------------------
# P1 — the download is the report last previewed
# ---------------------------------------------------------------------------


@pytest.fixture
def two_topics(capture_evidence):
    first = factories.MatterFactory(title="Esimene teema")
    second = factories.MatterFactory(title="Teine teema")
    send = sent(first, capture_evidence, day=date(2026, 10, 2), summary="Esimene tekst.")
    sent(second, capture_evidence, day=date(2026, 10, 9), summary="Teine tekst.")
    return {"first": first, "second": second, "send": send}


def test_an_untouched_first_page_downloads_at_once(signed_in, two_topics):
    response = export(signed_in, form_fields(open_page(signed_in)))

    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/vnd.openxmlformats")


@pytest.mark.parametrize("change", ["checkbox", "text", "order", "signers", "event"])
def test_a_change_after_the_preview_is_refused_then_downloads_once_seen(
    signed_in, two_topics, change
):
    first, second = two_topics["first"], two_topics["second"]
    fields = form_fields(open_page(signed_in))
    text_name = f"selgitus-{first.pk}-S{two_topics['send'].pk}"
    if change == "checkbox":
        fields = [(k, v) for k, v in fields if not (k == "kaasa" and v == str(second.pk))]
    elif change == "text":
        fields = replace(fields, text_name, "Minu uus tekst.")
    elif change == "order":
        blocks = [(k, v) for k, v in fields if k == "teema"]
        others = [(k, v) for k, v in fields if k != "teema"]
        fields = list(reversed(blocks)) + others
    elif change == "signers":
        fields = replace(fields, "allkirjastajad", "Mari Maasikas")
    else:
        key = f"S{two_topics['send'].pk}"
        fields = [(k, v) for k, v in fields if not (k == f"sundmus-kaasa-{first.pk}" and v == key)]

    refused = export(signed_in, fields)

    assert refused.status_code == 409
    body = refused.content.decode()
    assert report_draft.PREVIEW_STALE in body
    kept = form_fields(refused)
    # The reader's draft is the one posted, now previewed.
    if change == "text":
        assert values(kept, text_name) == ["Minu uus tekst."]
        assert "Minu uus tekst." in [row[3] for row in preview_rows(refused)]
    if change == "checkbox":
        assert str(second.pk) not in values(kept, "kaasa")
    if change == "order":
        assert values(kept, "teema") == [str(second.pk), str(first.pk)]
    if change == "signers":
        assert values(kept, "allkirjastajad") == ["Mari Maasikas"]

    downloaded = export(signed_in, kept)
    assert downloaded.status_code == 200
    document = Document(io.BytesIO(downloaded.content))
    table_text = texts(report_cells(downloaded))
    if change == "checkbox":
        assert [row[0] for row in table_text] == ["Esimene teema"]
    if change == "order":
        assert [row[0] for row in table_text] == ["Teine teema", "Esimene teema"]
    if change == "signers":
        assert [p.text for p in document.paragraphs if p.text][-1] == "Mari Maasikas"


def test_a_token_is_neither_forged_nor_borrowed(client, two_topics):
    owner, other = factories.UserFactory(), factories.UserFactory()
    client.force_login(owner)
    fields = form_fields(open_page(client))
    client.force_login(other)

    assert export(client, fields).status_code == 409
    assert export(client, replace(fields, "eelvaade_tunnus", "vale")).status_code == 409
    assert export(client, [(k, v) for k, v in fields if k != "eelvaade_tunnus"]).status_code == 409


# ---------------------------------------------------------------------------
# P2 — a topic with no event in the report
# ---------------------------------------------------------------------------


def _add_manual(client, fields, matter):
    return form_fields(post(client, fields, action=f"lisa:{matter.pk}"))


def test_an_eventless_topic_carries_the_readers_explanation(signed_in, two_topics):
    manual = factories.MatterFactory(title="Käsitsi teema")
    counts = (Entry.objects.count(), Submission.objects.count(), ChangeEvent.objects.count())
    before = Matter.objects.values().get(pk=manual.pk)
    fields = _add_manual(signed_in, form_fields(open_page(signed_in)), manual)
    name = f"selgitus-{manual.pk}-teema"
    assert values(fields, name) == [""]

    fields = replace(fields, name, "Rida üks.\r\nRida kaks.")
    # Survives previews, moves and another addition.
    fields = form_fields(post(signed_in, fields, action=f"ules:{manual.pk}"))
    fields = _add_manual(signed_in, fields, factories.MatterFactory(title="Veel üks"))
    previewed = post(signed_in, fields)
    fields = form_fields(previewed)

    assert values(fields, name) == ["Rida üks.\nRida kaks."]
    row = next(row for row in preview_rows(previewed) if row[0] == "Käsitsi teema")
    assert row[1:] == ["", "", "Rida üks.\nRida kaks."]
    exported = texts(report_cells(export(signed_in, fields)))
    assert ["Käsitsi teema", "", "", "Rida üks.\nRida kaks."] in exported
    # Report text only: nothing recorded, nothing changed.
    assert (Entry.objects.count(), Submission.objects.count(), ChangeEvent.objects.count()) == (
        counts
    )
    assert Matter.objects.values().get(pk=manual.pk) == before


def test_excluding_every_event_offers_the_topic_explanation(signed_in, two_topics):
    first = two_topics["first"]
    key = f"S{two_topics['send'].pk}"
    fields = form_fields(open_page(signed_in))
    fields = [(k, v) for k, v in fields if not (k == f"sundmus-kaasa-{first.pk}" and v == key)]
    fields = form_fields(post(signed_in, fields))
    name = f"selgitus-{first.pk}-teema"
    assert values(fields, name) == [""]

    fields = form_fields(post(signed_in, replace(fields, name, "Ainult teema.")))
    rows = texts(report_cells(export(signed_in, fields)))

    assert ["Esimene teema", "", "", "Ainult teema."] in rows
    # Including the event again keeps the text aside, not in a second field.
    again = form_fields(post(signed_in, [*fields, (f"sundmus-kaasa-{first.pk}", key)]))
    assert values(again, name) == ["Ainult teema."]
    exported = texts(report_cells(export(signed_in, again)))
    assert ["Esimene teema", "02.10\nAmetlik arvamus", "", "Esimene tekst."] in exported
    assert all("Ainult teema." not in row[3] for row in exported)


def test_an_eventless_explanation_is_bounded(signed_in, two_topics):
    manual = factories.MatterFactory(title="Liiga pikk")
    fields = _add_manual(signed_in, form_fields(open_page(signed_in)), manual)
    fields = form_fields(
        post(signed_in, replace(fields, f"selgitus-{manual.pk}-teema", "x" * 2001))
    )

    refused = export(signed_in, fields)

    assert refused.status_code == 409
    assert report_draft.TEXT_TOO_LONG in refused.content.decode()


def test_a_new_event_on_a_manual_topic_is_announced_and_its_text_kept(
    signed_in, two_topics, capture_evidence
):
    manual = factories.MatterFactory(title="Hiljem tegevusega")
    fields = _add_manual(signed_in, form_fields(open_page(signed_in)), manual)
    fields = form_fields(post(signed_in, replace(fields, f"selgitus-{manual.pk}-teema", "Oma.")))

    entry(manual, day=date(2026, 10, 20), body="<p>Kohtumine.</p>")
    refused = export(signed_in, fields)

    assert refused.status_code == 409
    assert "Hiljem tegevusega" in refused.content.decode()
    assert values(form_fields(refused), f"selgitus-{manual.pk}-teema") == ["Oma."]


# ---------------------------------------------------------------------------
# P2 — the plan of the report's year
# ---------------------------------------------------------------------------


def test_a_2026_report_opens_with_the_2026_plan(signed_in, two_topics):
    response = export(signed_in, form_fields(open_page(signed_in)))

    document = Document(io.BytesIO(response.content))
    assert document.paragraphs[0].text == plan_text.PLANS[2026].headings[0]


@pytest.mark.parametrize(
    ("period", "message"),
    [
        ({"algus": "1.10.2025", "lopp": "31.10.2025"}, "2025. aasta tegevuskava pole"),
        ({"algus": "1.1.2027", "lopp": "31.1.2027"}, "2027. aasta tegevuskava pole"),
        ({"algus": "1.12.2026", "lopp": "31.1.2027"}, "üle aastavahetuse"),
    ],
)
def test_a_year_without_a_plan_is_said_and_refused_with_the_draft_kept(
    signed_in, capture_evidence, period, message
):
    for day in (date(2025, 10, 6), date(2026, 12, 7), date(2027, 1, 11)):
        sent(factories.MatterFactory(title=f"Teema {day}"), capture_evidence, day=day)
    page = open_page(signed_in, period)
    assert message in page.content.decode()
    fields = form_fields(page)
    topics = values(fields, "teema")
    assert topics

    refused = export(signed_in, fields)

    assert refused.status_code == 409
    assert message in refused.content.decode()
    assert values(form_fields(refused), "teema") == topics
