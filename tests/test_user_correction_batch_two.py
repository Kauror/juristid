"""The owner's second user-side correction batch, 30 September 2026 (docs/adr/0121).

`tests/test_user_correction_batch.py` holds the first batch (docs/adr/0120) —
the retired `PRAEGUNE TEGEVUS` sentence, the milestone next step, the create-time
feedback deadline, the document corrections, `Võta tagasi` on the opinion and
`Kustuta` only where it can succeed — and still runs unchanged beside this one
except where this batch deliberately changed a rule (Minu asjad's weekly band).

This module holds what the second batch added or changed:

1. the next step: the Minu asjad portfolio reads the Matter's own step;
2. dates: past, today and future are accepted wherever a person types one
   (the per-record tests are in `tests/test_future_dated_records.py`);
3. `Kaasamine`: `Muuda` edits the fields `+ Kaasamine` asks, and a legacy
   `Link` / `Märkus` survives a save;
4. web addresses without `https://`, through one rule;
5. clean links in Teema käik;
6. several files uploaded together are one Teema käik action;
7. Minu asjad: a broad period is never this week's;
8. `Lõpeta teema → Muu`;
9. `Lõpeta teema → Märgi töövõiduks`, atomically.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.audit.operations import composer_operation
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.core.web_addresses import normalize_web_address, with_web_scheme
from app.documents.enums import DocumentRole
from app.documents.models import Document, DocumentVersion
from app.documents.services import (
    add_evidence_version,
    capture_evidence_on_open_matter,
    create_document,
    link_working_document,
    remove_document,
)
from app.intelligence.forms import EffectiveDateForm, WorkVictoryForm
from app.intelligence.services import add_important_date
from app.matters import services, workspace
from app.matters import work_items as wi
from app.matters.forms import (
    CompactEngagementForm,
    EngagementForm,
)
from app.matters.models import Matter, MatterEngagement, MatterWebsiteOverview
from app.matters.my_work import build_my_work
from app.matters.services import (
    engagement_revision_token,
    normalize_engagement_url,
    normalize_external_position_url,
    normalize_overview_news_url,
    normalize_procedural_link_url,
)
from app.matters.timeline import documents_added_clause, matter_timeline
from app.workflow.dates import period_bounds
from app.workflow.enums import DatePrecision, Disposition
from app.workflow.models import StageVocabulary
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

HX = {"headers": {"HX-Request": "true"}}
REPO = Path(__file__).resolve().parent.parent
PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def _matter_page(client, matter) -> str:
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    return response.content.decode()


# ---------------------------------------------------------------------------
# 1 — one next step, whoever carries it
# ---------------------------------------------------------------------------


def test_the_portfolio_row_reads_the_matters_step_whoever_carries_it(specialist, other_specialist):
    """A Matter whose `Järgmiseks` a colleague carries has a next step on its
    owner's desk too — the one its own page shows — and is not counted as
    «järgmise tegevuseta» by the chip, the strip or the rail."""
    today = timezone.localdate()
    matter = factories.MatterFactory(owner=specialist, title="Kolleegi samm")
    set_next_action(
        matter=matter,
        text="Ireen vaatab üle",
        target_date=today + timedelta(days=3),
        responsible=other_specialist,
        actor=specialist,
    )
    bare = factories.MatterFactory(owner=specialist, title="Ilma sammuta")

    work = build_my_work(specialist, today=today)
    rows = {row.matter.pk: row for row in work.portfolio.all_rows}
    chips = {chip.key: chip for chip in work.portfolio.chips}
    figures = {figure.key: figure for figure in work.seis}

    assert rows[matter.pk].has_action
    assert rows[matter.pk].next_action.text == "Ireen vaatab üle"
    assert not rows[bare.pk].has_action
    assert chips["jargmiseta"].count == figures["no_action"].value == work.quiet_total == 1


def test_the_milestone_due_first_is_the_next_step(signed_in, specialist):
    """A whole-year deadline does not outrank one due tomorrow because its
    stored anchor is 1 January (docs/adr/0121 §1): the one due first wins, on
    the Matter page and on the portfolio row alike."""
    today = timezone.localdate()
    matter = factories.MatterFactory(owner=specialist)
    for title, day, precision in (
        ("Aasta tähtaeg", today, DatePrecision.YEAR),
        ("Kuu tähtaeg", today, DatePrecision.MONTH),
        ("Homne tähtaeg", today + timedelta(days=1), DatePrecision.EXACT),
    ):
        start, end = period_bounds(day, precision)
        if end < today + timedelta(days=1):
            continue
        add_important_date(
            matter=matter,
            title=title,
            date_value=start,
            period_end=end,
            date_precision=precision,
            actor=specialist,
        )

    body = _matter_page(signed_in, matter)
    panel = re.search(r'id="praegune-tegevus".*?</section>', body, re.S).group(0)
    assert "Homne tähtaeg" in panel
    assert "Aasta tähtaeg" not in panel

    row = next(
        row
        for row in build_my_work(specialist, today=today).portfolio.all_rows
        if row.matter.pk == matter.pk
    )
    assert row.next_action.text == "Homne tähtaeg"


def test_a_future_marge_is_not_the_next_step(signed_in, specialist):
    """An informational `Märge` never becomes the next step because of its date."""
    matter = factories.MatterFactory(owner=specialist)
    workspace.add_procedural_development(
        matter=matter,
        author=specialist,
        title="Istung toimub",
        occurred_on=timezone.localdate() + timedelta(days=5),
    )

    body = _matter_page(signed_in, matter)

    assert "Järgmine samm on määramata" in body
    assert "Koja arvamus on saadetud" not in body


# ---------------------------------------------------------------------------
# 2 — dates: past, today and future, at every precision
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_the_marge_panel_saves_yesterday_today_and_tomorrow(signed_in, specialist, offset):
    matter = factories.MatterFactory(owner=specialist)
    day = timezone.localdate() + timedelta(days=offset)

    response = signed_in.post(
        reverse("matters:add_note", kwargs={"pk": matter.pk}),
        {"title": "Kohtumine ministeeriumis", "occurred_on": day.strftime("%d.%m.%Y")},
        **HX,
    )

    assert response.status_code == 200, response.content.decode()[:1500]
    assert matter.procedural_developments.get().occurred_on == day


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_a_next_step_saves_yesterday_today_and_tomorrow(specialist, offset):
    matter = factories.MatterFactory(owner=specialist)
    day = timezone.localdate() + timedelta(days=offset)

    action = set_next_action(matter=matter, text="Samm", target_date=day, actor=specialist)

    assert action.target_date == day


@pytest.mark.parametrize(
    "precision",
    [DatePrecision.EXACT, DatePrecision.MONTH, DatePrecision.QUARTER, DatePrecision.YEAR],
)
@pytest.mark.parametrize("years", [-1, 0, 1])
def test_an_important_deadline_saves_past_and_future_at_every_precision(
    specialist, precision, years
):
    matter = factories.MatterFactory(owner=specialist)
    today = timezone.localdate()
    start, end = period_bounds(today.replace(year=today.year + years), precision)

    record = add_important_date(
        matter=matter,
        title="Tähtaeg",
        date_value=start,
        period_end=end,
        date_precision=precision,
        actor=specialist,
    )

    assert (record.date_value, record.period_end, record.date_precision) == (
        start,
        end,
        precision,
    )


# ---------------------------------------------------------------------------
# 3 — Kaasamine: create and edit ask the same things
# ---------------------------------------------------------------------------

#: What a person is asked on `+ Kaasamine`, by field name. `Veebileht` and
#: `Märkus` joined it with docs/adr/0127 §2, under panel-specific names because
#: the panel keeps Django's default ids on a page that already draws `id_url`.
CREATE_FIELDS = {
    "audience",
    "response_count",
    "occurred_on",
    "feedback_deadline",
    "feedback_received",
    "website_url",
    "smaily_url",
    "alchemer_url",
    "engagement_note",
}
#: The same questions on `Muuda` — `title` is `audience`, `url` is `website_url`
#: and `note` is `engagement_note`, each under its stored name.
EDIT_FIELDS = {
    "title",
    "response_count",
    "occurred_on",
    "feedback_deadline",
    "feedback_received",
    "url",
    "smaily_url",
    "alchemer_url",
    "note",
}


def test_create_and_edit_ask_the_same_questions():
    create = set(CompactEngagementForm().fields) - {"attachments"}
    edit = set(EngagementForm().fields) - {"revision", "clear_occurred_on"}

    assert create == CREATE_FIELDS
    assert edit == EDIT_FIELDS
    # The same words on both surfaces (docs/adr/0127 §2).
    create_labels = {name: field.label for name, field in CompactEngagementForm().fields.items()}
    edit_labels = {name: field.label for name, field in EngagementForm().fields.items()}
    assert create_labels["website_url"] == edit_labels["url"] == "Veebileht"
    assert create_labels["engagement_note"] == edit_labels["note"] == "Märkus"


def _round(matter, actor, **kwargs):
    return services.add_engagement(
        matter=matter,
        kind="OTHER",
        title="liikmed",
        occurred_on=timezone.localdate(),
        actor=actor,
        **kwargs,
    )


def _edit_url(matter, engagement):
    return reverse(
        "matters:update_engagement", kwargs={"pk": matter.pk, "engagement_id": engagement.pk}
    )


def test_the_edit_form_renders_veebileht_and_markus(signed_in, specialist):
    """`Muuda` offers what `+ Kaasamine` asks, filled from the record (docs/adr/0127 §2)."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = _round(
        matter, specialist, url="https://www.koda.ee/hetkel-kasil/x", note="Tööloend."
    )

    html = signed_in.get(_edit_url(matter, engagement), **HX).content.decode()

    assert 'name="smaily_url"' in html and 'name="alchemer_url"' in html
    assert 'name="feedback_deadline"' in html
    assert 'name="url"' in html and 'value="https://www.koda.ee/hetkel-kasil/x"' in html
    assert 'name="note"' in html and "Tööloend.</textarea>" in html
    assert ">Veebileht" in html and ">Märkus" in html


def test_a_legacy_link_and_note_survive_an_edit_that_does_not_carry_them(signed_in, specialist):
    """A `Muuda` posted from a row drawn before docs/adr/0127 has no `url` or
    `note` box, so it asked nothing about either — and clears neither."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = _round(
        matter, specialist, url="https://vana.example/kampaania", note="Vana märkus."
    )

    response = signed_in.post(
        _edit_url(matter, engagement),
        {
            "title": "kõik liikmed",
            "response_count": "12",
            "occurred_on": timezone.localdate().strftime("%d.%m.%Y"),
            "feedback_deadline": "",
            "feedback_received": "",
            "smaily_url": "",
            "alchemer_url": "",
            "revision": engagement_revision_token(engagement),
        },
        **HX,
    )

    assert response.status_code == 200, response.content.decode()[:1500]
    engagement.refresh_from_db()
    assert engagement.title == "kõik liikmed"
    assert engagement.response_count == 12
    assert engagement.url == "https://vana.example/kampaania"
    assert engagement.note == "Vana märkus."


def test_a_round_created_with_every_field_reads_back_at_once(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    today = timezone.localdate()

    response = signed_in.post(
        reverse("matters:add_engagement_compact", kwargs={"pk": matter.pk}),
        {
            "audience": "liikmed",
            "response_count": "7",
            "occurred_on": today.strftime("%d.%m.%Y"),
            "feedback_deadline": (today + timedelta(days=7)).strftime("%d.%m.%Y"),
            "feedback_received": "Kolm arvamust.",
            "website_url": "www.koda.ee/hetkel-kasil/juristieksam",
            "smaily_url": "www.sendsmaily.net/kampaania",
            "alchemer_url": "survey.alchemer.eu/s3/123",
            "engagement_note": "  Fail on sihtrühma tööloend, mitte saajate nimekiri.  ",
        },
        **HX,
    )

    assert response.status_code == 200, response.content.decode()[:1500]
    engagement = MatterEngagement.objects.get(matter=matter)
    assert engagement.response_count == 7
    assert engagement.feedback_deadline == today + timedelta(days=7)
    assert engagement.feedback_received == "Kolm arvamust."
    assert engagement.smaily_url == "https://www.sendsmaily.net/kampaania"
    assert engagement.alchemer_url == "https://survey.alchemer.eu/s3/123"
    # `Veebileht` into `url`, through the same rule: the bare host gains its
    # scheme. `Märkus` into `note`, trimmed (docs/adr/0127 §2).
    assert engagement.url == "https://www.koda.ee/hetkel-kasil/juristieksam"
    assert engagement.note == "Fail on sihtrühma tööloend, mitte saajate nimekiri."
    assert engagement.title == "liikmed"
    assert wi.open_feedback_waits(specialist).filter(pk=engagement.pk).exists()


def test_a_round_created_without_a_deadline_is_open_with_no_due_date(signed_in, specialist):
    """docs/adr/0132: the deadline is optional and is not the lifecycle."""
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        reverse("matters:add_engagement_compact", kwargs={"pk": matter.pk}),
        {"audience": "liikmed", "occurred_on": timezone.localdate().strftime("%d.%m.%Y")},
        **HX,
    )

    assert response.status_code == 200
    engagement = MatterEngagement.objects.get(matter=matter)
    assert engagement.feedback_deadline is None
    assert wi.undated_feedback_waits(specialist).filter(pk=engagement.pk).exists()
    assert not wi.dated_feedback_waits(specialist).filter(pk=engagement.pk).exists()


# ---------------------------------------------------------------------------
# 4 — web addresses without https://
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "typed, stored",
    [
        ("www.delfi.ee", "https://www.delfi.ee"),
        ("delfi.ee", "https://delfi.ee"),
        ("www.delfi.ee/uudised", "https://www.delfi.ee/uudised"),
        ("delfi.ee/uudised?id=123", "https://delfi.ee/uudised?id=123"),
        ("  www.example.ee  ", "https://www.example.ee"),
        ("example.ee", "https://example.ee"),
        ("õigus.ee/x", "https://õigus.ee/x"),
        ("delfi.ee:8443/x", "https://delfi.ee:8443/x"),
        ("https://www.delfi.ee", "https://www.delfi.ee"),
        ("http://example.com", "http://example.com"),
        ("HTTPS://WWW.DELFI.EE/X", "HTTPS://WWW.DELFI.EE/X"),
    ],
)
def test_an_address_is_accepted_with_or_without_its_scheme(typed, stored):
    assert normalize_web_address(typed, max_length=1000) == stored
    # Never a second scheme: normalising the stored value changes nothing.
    assert normalize_web_address(stored, max_length=1000) == stored
    assert with_web_scheme(stored) == stored


@pytest.mark.parametrize(
    "typed",
    [
        "kampaania",
        "ei ole aadress",
        "delfi .ee",
        "delfi.ee/uudised on hea",
        "mailto:info@koda.ee",
        "javascript:alert(1)",
        "data:text/html,<b>x</b>",
        "ftp://example.com/x",
        "file:///c:/x",
        "https://ei ole aadress",
        "https:///uudised",
        "koda.ee@example.com/x",
        "192.0.2.10/x",
    ],
)
def test_text_that_is_not_an_address_is_still_refused(typed):
    with pytest.raises(DomainError, match=r"^Link peab "):
        normalize_web_address(typed, max_length=1000)


@pytest.mark.parametrize(
    "normalise",
    [
        normalize_engagement_url,
        normalize_external_position_url,
        normalize_overview_news_url,
        normalize_procedural_link_url,
    ],
)
def test_every_teema_link_family_uses_the_one_rule(normalise):
    assert normalise("www.delfi.ee/uudised") == "https://www.delfi.ee/uudised"
    assert normalise("https://www.delfi.ee") == "https://www.delfi.ee"
    with pytest.raises(DomainError, match=r"(?i)link peab"):
        normalise("see ei ole aadress")


def test_a_working_document_address_uses_the_one_rule(specialist):
    matter = factories.MatterFactory(owner=specialist)

    document = link_working_document(
        matter=matter,
        title="Töödokument",
        web_url="kaubanduskoda.sharepoint.com/sites/oigus/x.docx",
        created_by=specialist,
    )

    assert document.sharepoint_web_url == "https://kaubanduskoda.sharepoint.com/sites/oigus/x.docx"


@pytest.mark.parametrize("form_class", [EffectiveDateForm, WorkVictoryForm])
def test_the_source_address_box_is_text_and_takes_a_bare_host(form_class):
    form = form_class()
    rendered = str(form["source_url"])

    assert 'type="url"' not in rendered
    assert 'inputmode="url"' in rendered
    field = form.fields["source_url"]
    assert field.clean("www.riigiteataja.ee/akt/1") == "https://www.riigiteataja.ee/akt/1"
    with pytest.raises(ValidationError):
        field.clean("pole aadress")


def test_the_overview_panel_stores_a_bare_host_with_https(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        reverse("matters:add_website_overview", kwargs={"pk": matter.pk}),
        {"url": "www.koda.ee/uudised/x", "published_on": ""},
        **HX,
    )

    assert response.status_code == 200, response.content.decode()[:1500]
    assert MatterWebsiteOverview.objects.get(matter=matter).url == "https://www.koda.ee/uudised/x"


# ---------------------------------------------------------------------------
# 5 — clean links in Teema käik
# ---------------------------------------------------------------------------


def test_no_stylesheet_appends_anything_to_a_chronology_link():
    css = (REPO / "static" / "css" / "app.css").read_text(encoding="utf-8")

    assert ".uxtl__link::after" not in css
    assert ".uxtl__link::before" not in css
    # The corrupted escape that printed «97» was a control byte in the file.
    assert not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", css)


def test_a_kaasamine_row_names_its_links_and_nothing_else(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _round(
        matter,
        specialist,
        smaily_url="https://sendsmaily.net/c/97?token=abc",
        alchemer_url="https://survey.alchemer.eu/s3/97",
        response_count=97,
    )

    body = _matter_page(signed_in, matter)
    links = re.findall(r'<a class="uxtl__link"[^>]*>(.*?)</a>', body, re.S)

    visible = [
        re.sub(r'<span\s+class="visually-hidden">.*?</span>', "", text, flags=re.S).strip()
        for text in links
    ]
    assert visible == ["Smaily", "Alchemer"]


def test_an_overview_row_names_its_link_and_keeps_the_address_behind_it(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    workspace.add_matter_website_overview(
        matter=matter,
        author=specialist,
        url="https://www.koda.ee/uudised/pikk-uudise-aadress-mis-ei-mahu-reale",
        published_on=timezone.localdate(),
    )

    body = _matter_page(signed_in, matter)
    anchor = re.search(
        r'<a class="uxtl__link" id="kodulehe-ulevaade-[^"]*-aadress"[^>]*>(.*?)</a>', body, re.S
    )

    assert anchor
    visible = re.sub(
        r'<span\s+class="visually-hidden">.*?</span>', "", anchor.group(1), flags=re.S
    ).strip()
    assert visible == "Ülevaade / uudis"
    assert "koda.ee/uudised/pikk-uudise-aadress" in anchor.group(0)  # title + hidden name


# ---------------------------------------------------------------------------
# 6 — several files uploaded together are one Teema käik action
# ---------------------------------------------------------------------------

CREATE = reverse("matters:matter_create")
STAGE = reverse("matters:intake_stage")
SAABUNUD = reverse("matters:intake")


def _files(count: int) -> list[SimpleUploadedFile]:
    return [
        SimpleUploadedFile(f"fail-{n}.pdf", PDF + str(n).encode(), content_type="application/pdf")
        for n in range(1, count + 1)
    ]


def _upload_rows(matter, user):
    items, _more = matter_timeline(matter=matter, user=user, limit=100)
    return [
        item
        for item in items
        if any(event.event_type == ChangeEventType.EVIDENCE_VERSION_ADDED for event in item.events)
    ]


@pytest.mark.parametrize("count", [1, 2, 6, 11])
def test_one_uus_teema_upload_is_one_row_that_counts_its_files(signed_in, specialist, count):
    response = signed_in.post(CREATE, {"title": f"Mitu faili {count}", "files": _files(count)})
    assert response.status_code == 302, response.status_code
    matter = Matter.objects.get(title=f"Mitu faili {count}")

    rows = _upload_rows(matter, specialist)

    assert len(rows) == 1
    assert rows[0].summary_sentence == documents_added_clause(count)
    assert len(rows[0].files) == count
    # Every document, every version and every per-file audit event is kept.
    assert Document.objects.filter(matter=matter).count() == count
    assert DocumentVersion.objects.filter(document__matter=matter).count() == count
    events = ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.EVIDENCE_VERSION_ADDED
    )
    assert events.count() == count
    assert len({event.operation_id for event in events}) == 1


def test_the_clause_says_how_many():
    assert documents_added_clause(1) == "lisas dokumendi"
    assert documents_added_clause(2) == "lisas 2 dokumenti"
    assert documents_added_clause(20) == "lisas 20 dokumenti"


def test_staged_files_promoted_on_create_are_one_row(signed_in, specialist):
    staged = signed_in.post(STAGE, {"files": _files(4)})
    session = staged.context["intake_session"]

    signed_in.post(CREATE, {"title": "Ettevalmistatud failid", "intake": str(session.pk)})
    matter = Matter.objects.get(title="Ettevalmistatud failid")

    rows = _upload_rows(matter, specialist)
    assert [row.summary_sentence for row in rows] == ["lisas 4 dokumenti"]


def test_saabunud_files_are_one_row(signed_in, specialist):
    signed_in.post(
        SAABUNUD,
        {"title": "Saabunud failid", "visibility": Visibility.NORMAL, "uploads": _files(3)},
    )
    matter = Matter.objects.get(title="Saabunud failid")

    rows = _upload_rows(matter, specialist)
    assert [row.summary_sentence for row in rows] == ["lisas 3 dokumenti"]


def test_a_later_upload_is_a_row_of_its_own(signed_in, specialist):
    signed_in.post(CREATE, {"title": "Kaks korda", "files": _files(3)})
    matter = Matter.objects.get(title="Kaks korda")

    capture_evidence_on_open_matter(
        matter=matter,
        title="hiljem.pdf",
        role=DocumentRole.INCOMING_AUTHORITY,
        content=PDF + b"later",
        original_filename="hiljem.pdf",
        mime_type="application/pdf",
        actor=specialist,
    )

    rows = _upload_rows(matter, specialist)
    assert sorted(row.summary_sentence for row in rows) == ["lisas 3 dokumenti", "lisas dokumendi"]


def test_two_uploads_in_the_same_instant_stay_two_rows(specialist, monkeypatch):
    """Grouping is by the operation, never by the clock: two uploads stamped with
    the very same instant are still two rows."""
    matter = factories.MatterFactory(owner=specialist)
    instant = timezone.now().replace(microsecond=0)
    field = ChangeEvent._meta.get_field("occurred_at")
    monkeypatch.setattr(field, "_get_default", lambda: instant)
    for batch in ("a", "b"):
        with composer_operation():
            for n in range(2):
                document = create_document(
                    matter=matter,
                    title=f"{batch}{n}.pdf",
                    role=DocumentRole.INCOMING_AUTHORITY,
                    created_by=specialist,
                )
                add_evidence_version(
                    document=document,
                    content=PDF + f"{batch}{n}".encode(),
                    original_filename=f"{batch}{n}.pdf",
                    mime_type="application/pdf",
                    uploaded_by=specialist,
                )
    uploads = ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.EVIDENCE_VERSION_ADDED
    )
    assert {event.occurred_at for event in uploads} == {instant}

    rows = _upload_rows(matter, specialist)

    assert [row.summary_sentence for row in rows] == ["lisas 2 dokumenti", "lisas 2 dokumenti"]


def test_a_removed_file_leaves_the_count(signed_in, specialist):
    signed_in.post(CREATE, {"title": "Üks eemaldatakse", "files": _files(3)})
    matter = Matter.objects.get(title="Üks eemaldatakse")
    document = Document.objects.filter(matter=matter).order_by("title").first()

    remove_document(document=document, actor=specialist)

    rows = _upload_rows(matter, specialist)
    assert [row.summary_sentence for row in rows] == ["lisas 2 dokumenti"]
    # The act is still in the audit history, and so are its three uploads.
    assert (
        ChangeEvent.objects.filter(
            matter=matter, event_type=ChangeEventType.EVIDENCE_VERSION_ADDED
        ).count()
        == 3
    )


def test_the_matter_page_prints_one_line_for_the_upload(signed_in, specialist):
    signed_in.post(CREATE, {"title": "Seitse faili", "files": _files(7)})
    matter = Matter.objects.get(title="Seitse faili")

    body = _matter_page(signed_in, matter)

    assert body.count("lisas 7 dokumenti") == 1
    assert "lisas dokumendi" not in body


# ---------------------------------------------------------------------------
# 7 — Minu asjad: a broad period is never this week's
# ---------------------------------------------------------------------------

#: A Tuesday; its ISO week ends on Sunday 4 October, September ends on the
#: Wednesday and 1 October is the Thursday — every representative day of the
#: broad periods below falls inside this week.
TODAY = date(2026, 9, 29)


def _step(owner, title, day, precision=DatePrecision.EXACT, **kwargs):
    matter = factories.MatterFactory(owner=owner, title=title)
    set_next_action(
        matter=matter,
        text=title,
        target_date=period_bounds(day, precision)[0],
        date_precision=precision,
        actor=owner,
        **kwargs,
    )
    return matter


def _band_texts(work, key):
    return [item.text for band in work.bands if band.key == key for item in band.items]


@pytest.fixture
def broad_desk(specialist):
    _step(specialist, "Täpne sel nädalal", date(2026, 10, 2))
    _step(specialist, "Täpne hiljem", date(2026, 11, 20))
    _step(specialist, "September lõpeb sel nädalal", date(2026, 9, 1), DatePrecision.MONTH)
    _step(specialist, "Oktoober algab sel nädalal", date(2026, 10, 1), DatePrecision.MONTH)
    _step(specialist, "III kvartal lõpeb sel nädalal", date(2026, 7, 1), DatePrecision.QUARTER)
    _step(specialist, "Aasta", date(2026, 1, 1), DatePrecision.YEAR)
    _step(
        specialist,
        "Jälgi septembris",
        date(2026, 9, 1),
        DatePrecision.MONTH,
        kind="MONITOR",
        date_semantics="REVIEW_ON",
    )
    return specialist


@pytest.mark.parametrize(
    "title",
    [
        "September lõpeb sel nädalal",
        "Oktoober algab sel nädalal",
        "III kvartal lõpeb sel nädalal",
        "Aasta",
        "Jälgi septembris",
    ],
)
def test_a_broad_period_is_never_this_weeks(broad_desk, title):
    work = build_my_work(broad_desk, today=TODAY)

    assert title not in _band_texts(work, wi.BAND_WEEK)
    assert title not in _band_texts(work, wi.BAND_NEXT_30)
    assert title in _band_texts(work, wi.BAND_LATER)


def test_an_exact_day_this_week_is_still_this_weeks(broad_desk):
    work = build_my_work(broad_desk, today=TODAY)

    assert _band_texts(work, wi.BAND_WEEK) == ["Täpne sel nädalal"]


def test_a_broad_period_keeps_its_words(broad_desk):
    work = build_my_work(broad_desk, today=TODAY)
    items = {item.text: item for band in work.bands for item in band.items}

    assert items["Oktoober algab sel nädalal"].short_date == "oktoober 2026"
    assert items["III kvartal lõpeb sel nädalal"].short_date == "III kvartal 2026"
    assert items["Aasta"].short_date == "2026"


def test_a_broad_period_that_has_ended_is_placed_with_the_past(specialist):
    _step(specialist, "August", date(2026, 8, 1), DatePrecision.MONTH)

    work = build_my_work(specialist, today=TODAY)

    assert "August" in _band_texts(work, wi.BAND_OVERDUE)


def test_every_count_on_the_page_is_its_rows(signed_in, broad_desk):
    """Each band's heading counts exactly the rows under it, and the strip's
    figures are those bands' totals."""
    work = build_my_work(broad_desk, today=TODAY)
    figures = {figure.key: figure.value for figure in work.seis}
    for band in work.bands:
        assert band.count == len(band.preview) + len(band.rest) == len(band.items)
    bands = {band.key: band for band in work.bands}
    assert figures["week"] == bands[wi.BAND_WEEK].total == 1
    assert figures["overdue"] == (bands[wi.BAND_OVERDUE].total if wi.BAND_OVERDUE in bands else 0)


def test_the_rendered_band_heading_matches_its_rendered_rows(signed_in, specialist):
    today = timezone.localdate()
    for n in range(3):
        _step(
            specialist,
            f"Kuu samm {n}",
            (today.replace(day=1) + timedelta(days=40)).replace(day=1),
            DatePrecision.MONTH,
        )
    _step(specialist, "Täna", today)

    body = signed_in.get(reverse("matters:my_work")).content.decode()

    for key in (wi.BAND_WEEK, wi.BAND_LATER):
        section = re.search(rf'<section[^>]*id="{key}".*?</section>', body, re.S)
        assert section, key
        heading = re.search(r'class="workband__count">(\d+)<', section.group(0))
        rows = re.findall(r'class="workrow2[ "]', section.group(0))
        assert heading and int(heading.group(1)) == len(rows), key


# ---------------------------------------------------------------------------
# 8 and 9 — `Lõpeta teema` is retired (docs/adr/0131 §11)
# ---------------------------------------------------------------------------
#
# The panel these sections drove — four outcomes, «Muu», `Märgi töövõiduks` —
# is gone: a Matter ends when its `Hetkeseis` says so, and a win is recorded from
# `+ Märge → Töövõit`. What still holds of section 8 is that a closure recorded
# as «Muu» reads back on the banner, and that reopening works — now into a stage
# (tests/test_stage_episodes.py owns the rest).


def test_a_muu_closure_reads_back_and_reopens_into_a_stage(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    services.close_matter(
        matter=matter, disposition=Disposition.OTHER.value, actor=specialist, reason="Teine põhjus."
    )

    body = _matter_page(signed_in, matter)
    banner = re.search(r'<div class="banner banner--closed">.*?</div>', body, re.S).group(0)
    assert "Muu" in banner and "Teine põhjus." in banner

    idea = StageVocabulary.objects.get(key="idea")
    signed_in.post(reverse("matters:reopen", kwargs={"pk": matter.pk}), {"stage": str(idea.pk)})
    matter.refresh_from_db()
    assert matter.is_open and matter.disposition == ""
    assert matter.stage == idea
