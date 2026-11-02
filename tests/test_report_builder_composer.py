"""«Majanduspoliitika ülevaade»: the composer, its round trips and the Word file.

The draft lives only in the page's form (docs/adr/0199 §3), so every test
here reads the form back out of the rendered response (`form_fields`),
changes what a person would change, and posts it again. Exports are reopened
with python-docx and compared with the preview, row for row.
"""

from __future__ import annotations

import io
from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from docx import Document
from docx.oxml.ns import qn

from app.accounts.enums import UserRole
from app.audit.models import ChangeEvent
from app.core.authorization import Scope
from app.core.enums import Visibility
from app.matters.entry_enums import EntryKind
from app.matters.models import Entry, Matter
from app.reporting.report_builder import draft as report_draft
from app.submissions.models import Submission
from tests import factories
from tests.gate import PASSWORD, apply_shared_gate
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
SEARCH = "reporting:report_search"
EXPORT = "reporting:report_export"
OCTOBER = {"algus": "1.10.2026", "lopp": "31.10.2026"}


@pytest.fixture
def world(capture_evidence):
    ministry = factories.OrganisationFactory(name="Majandus- ja Kommunikatsiooniministeerium")
    alpha = factories.MatterFactory(title="Alfa seaduse muutmine")
    beta = factories.MatterFactory(title="Beeta määrus")
    gamma = factories.MatterFactory(title="Gamma kontseptsioon")
    send = sent(
        alpha,
        capture_evidence,
        day=date(2026, 10, 2),
        summary="Toetame eelnõu.",
        addressees=(ministry,),
    )
    meeting = entry(beta, day=date(2026, 10, 5), body="<p>Kohtumine ministriga.</p>")
    second = sent(beta, capture_evidence, day=date(2026, 10, 20), summary="Täiendav seisukoht.")
    valja = register(gamma, day=date(2026, 10, 9))
    return {
        "alpha": alpha,
        "beta": beta,
        "gamma": gamma,
        "send": send,
        "meeting": meeting,
        "second": second,
        "valja": valja,
        "ministry": ministry,
    }


def open_page(client, **period):
    response = client.get(reverse(PAGE), period or OCTOBER)
    assert response.status_code == 200
    return response


def post(client, fields, action="eelvaade", route=PREVIEW, **extra):
    return client.post(reverse(route), as_post(fields, toiming=action, **extra))


def order(fields):
    return values(fields, "teema")


def docx_rows(response):
    document = Document(io.BytesIO(response.content))
    table = document.tables[1]  # [0] is the fixed action-plan table
    rows = []
    for row in table.rows[1:]:
        rows.append(["\n".join(p.text for p in cell.paragraphs).strip() for cell in row.cells])
    return document, table, rows


# ---------------------------------------------------------------------------
# Who may use it
# ---------------------------------------------------------------------------


def test_the_page_is_linked_from_the_statistics_overview(signed_in):
    response = signed_in.get(reverse("reporting:overview"))
    assert reverse(PAGE) in response.content.decode()


def test_an_anonymous_visitor_is_sent_to_sign_in(client, settings):
    settings.AUTH_MODE = "none"
    for name, method in ((PAGE, "get"), (SEARCH, "get"), (PREVIEW, "post"), (EXPORT, "post")):
        response = getattr(client, method)(reverse(name))
        assert response.status_code == 302, name
        assert response["Location"].startswith(reverse(settings.LOGIN_URL)), name


def test_an_administrator_without_a_business_role_gets_nothing(client, world):
    client.force_login(factories.AdministratorFactory())
    assert client.get(reverse(PAGE), OCTOBER).status_code == 404
    assert client.get(reverse(SEARCH), {"otsing": "Alfa"}).status_code == 404
    assert client.post(reverse(PREVIEW), OCTOBER).status_code == 404
    response = client.post(reverse(EXPORT), {**OCTOBER, "koostatud": "1"})
    assert response.status_code == 404
    assert b"Alfa" not in response.content


def test_the_shared_gate_without_a_persona_is_sent_to_choose_one(client, settings, world):
    apply_shared_gate(settings)
    assert client.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302
    chooser = reverse("accounts:choose_persona")
    for name, method in ((PAGE, "get"), (SEARCH, "get"), (PREVIEW, "post"), (EXPORT, "post")):
        response = getattr(client, method)(reverse(name))
        assert response.status_code == 302 and response["Location"].startswith(chooser), name


def test_a_reader_may_compose_and_download(client, world):
    client.force_login(factories.ReaderFactory())
    fields = form_fields(open_page(client))
    response = post(client, fields, route=EXPORT)
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/vnd.openxmlformats")


def test_every_answer_is_private(signed_in, world):
    fields = form_fields(open_page(signed_in))
    for response in (
        open_page(signed_in),
        post(signed_in, fields),
        signed_in.get(reverse(SEARCH), {"otsing": "Alfa"}),
        post(signed_in, fields, route=EXPORT),
    ):
        assert response["Cache-Control"] == "private, no-store"


# ---------------------------------------------------------------------------
# Proposals and the period
# ---------------------------------------------------------------------------


def test_the_default_period_is_the_previous_calendar_month(signed_in):
    response = signed_in.get(reverse(PAGE))
    start, end = report_draft.default_period(timezone.localdate())
    fields = form_fields(response)
    assert values(fields, "kehtiv_algus") == [start.isoformat()]
    assert values(fields, "kehtiv_lopp") == [end.isoformat()]
    assert end.month != timezone.localdate().month and start.day == 1


def test_the_initial_order_is_earliest_event_then_title(signed_in, world):
    fields = form_fields(open_page(signed_in))
    assert order(fields) == [str(world["alpha"].pk), str(world["beta"].pk), str(world["gamma"].pk)]


def test_an_empty_period_says_so_and_exports_nothing(signed_in, world):
    response = open_page(signed_in, algus="1.1.2025", lopp="31.1.2025")
    assert "ei leitud ühtki saadetud arvamust" in response.content.decode()
    refused = post(signed_in, form_fields(response), route=EXPORT)
    assert refused.status_code == 409
    assert report_draft.NOTHING_SELECTED in refused.content.decode()


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        ("31.10.2026", "1.10.2026", report_draft.PERIOD_BACKWARDS),
        ("1.1.2025", "31.12.2026", report_draft.PERIOD_TOO_LONG),
        ("eile", "täna", report_draft.BAD_PERIOD),
    ],
)
def test_an_impossible_period_keeps_the_draft_and_says_why(signed_in, world, start, end, message):
    fields = form_fields(open_page(signed_in))
    fields = replace(replace(fields, "algus", start), "lopp", end)
    response = post(signed_in, fields, action="rakenda")
    after = form_fields(response)
    assert message in response.content.decode()
    assert order(after) == order(fields)
    assert values(after, "kehtiv_algus") == ["2026-10-01"]


def test_changing_the_period_is_reviewed_before_it_is_applied(signed_in, world, capture_evidence):
    november = factories.MatterFactory(title="Novembri teema")
    entry(november, day=date(2026, 9, 15))
    manual = factories.MatterFactory(title="Käsitsi lisatud teema")
    fields = form_fields(open_page(signed_in))
    fields = form_fields(post(signed_in, fields, action=f"lisa:{manual.pk}"))
    # The reader moves gamma first and writes their own explanation for alpha.
    fields = form_fields(post(signed_in, fields, action=f"ules:{world['gamma'].pk}"))
    fields = form_fields(post(signed_in, fields, action=f"ules:{world['gamma'].pk}"))
    alpha_text = f"selgitus-{world['alpha'].pk}-S{world['send'].pk}"
    fields = replace(fields, alpha_text, "Minu oma tekst.")
    fields = replace(replace(fields, "algus", "15.9.2026"), "lopp", "31.10.2026")

    reviewed = post(signed_in, fields, action="uuenda")
    page = reviewed.content.decode()
    assert "Uus periood: 15.9.2026–31.10.2026" in page and "Novembri teema" in page
    # Nothing moved yet: the draft is still October's.
    assert values(form_fields(reviewed), "kehtiv_algus") == ["2026-10-01"]
    assert str(november.pk) not in order(form_fields(reviewed))

    export_while_pending = post(signed_in, form_fields(reviewed), route=EXPORT)
    assert export_while_pending.status_code == 409
    assert report_draft.PERIOD_PENDING in export_while_pending.content.decode()

    applied = form_fields(post(signed_in, form_fields(reviewed), action="rakenda"))
    assert values(applied, "kehtiv_algus") == ["2026-09-15"]
    assert order(applied) == [
        str(world["gamma"].pk),
        str(world["alpha"].pk),
        str(world["beta"].pk),
        str(manual.pk),
        str(november.pk),
    ]
    assert values(applied, alpha_text) == ["Minu oma tekst."]
    assert values(applied, "lisatud") == [str(manual.pk)]

    # «Loobu» puts the boxes back and changes nothing else.
    moved = replace(replace(applied, "algus", "1.1.2026"), "lopp", "31.1.2026")
    cancelled = form_fields(post(signed_in, moved, action="loobu"))
    assert values(cancelled, "algus") == ["15.9.2026"]
    assert order(cancelled) == order(applied)


# ---------------------------------------------------------------------------
# Choices survive the round trip
# ---------------------------------------------------------------------------


def test_selection_removal_edits_and_additions_survive_previews(signed_in, world):
    manual = factories.MatterFactory(title="Delta käsitsi")
    fields = form_fields(open_page(signed_in))
    beta = world["beta"].pk
    fields = [(k, v) for k, v in fields if not (k == "kaasa" and v == str(beta))]
    text_name = f"selgitus-{world['alpha'].pk}-S{world['send'].pk}"
    fields = replace(fields, text_name, "Rida üks.\nRida kaks – õüäö ŠŽ.")
    fields = form_fields(post(signed_in, fields, action=f"lisa:{manual.pk}"))
    fields = form_fields(post(signed_in, fields, action=f"eemalda:{world['gamma'].pk}"))
    for _ in range(2):
        fields = form_fields(post(signed_in, fields))

    assert order(fields) == [str(world["alpha"].pk), str(beta), str(manual.pk)]
    assert str(beta) not in values(fields, "kaasa")
    assert values(fields, text_name) == ["Rida üks.\nRida kaks – õüäö ŠŽ."]
    rows = preview_rows(post(signed_in, fields))
    assert [row[0] for row in rows] == ["Alfa seaduse muutmine", "Delta käsitsi"]
    assert rows[0][3] == "Rida üks.\nRida kaks – õüäö ŠŽ."
    # The hand-added topic has no event, so no date was made up for it.
    assert rows[1][1:] == ["", "", ""]


def test_an_excluded_event_is_left_out_and_its_topic_stays(signed_in, world):
    fields = form_fields(open_page(signed_in))
    beta = world["beta"].pk
    meeting_key = f"E{world['meeting'].pk}"
    fields = [(k, v) for k, v in fields if not (k == f"sundmus-kaasa-{beta}" and v == meeting_key)]
    rows = preview_rows(post(signed_in, fields))
    beta_rows = [row for row in rows if row[0] == "Beeta määrus"]
    assert [row[1].split("\n")[0] for row in beta_rows] == ["20.10"]


def test_up_down_and_drag_produce_the_same_order(signed_in, world):
    fields = form_fields(open_page(signed_in))
    alpha, beta, gamma = (str(world[name].pk) for name in ("alpha", "beta", "gamma"))

    buttons = form_fields(post(signed_in, fields, action=f"alla:{alpha}"))
    buttons = form_fields(post(signed_in, buttons, action=f"alla:{alpha}"))
    buttons = form_fields(post(signed_in, buttons, action=f"ules:{gamma}"))

    # A drag moves the block's `teema` input; the browser posts that order.
    dragged_order = [gamma, beta, alpha]
    others = [(k, v) for k, v in fields if k != "teema"]
    dragged = form_fields(post(signed_in, others + [("teema", value) for value in dragged_order]))

    assert order(buttons) == order(dragged) == dragged_order
    assert preview_rows(post(signed_in, buttons)) == preview_rows(post(signed_in, dragged))
    # Moving past either end changes nothing.
    assert order(form_fields(post(signed_in, buttons, action=f"ules:{gamma}"))) == dragged_order


def test_a_topic_is_added_once(signed_in, world):
    fields = form_fields(open_page(signed_in))
    response = post(signed_in, fields, action=f"lisa:{world['alpha'].pk}")
    assert report_draft.ALREADY_ADDED in response.content.decode()
    assert order(form_fields(response)).count(str(world["alpha"].pk)) == 1

    duplicated = [*fields, ("teema", str(world["alpha"].pk))]
    merged = post(signed_in, duplicated)
    assert report_draft.DUPLICATE_MATTER in merged.content.decode()
    assert order(form_fields(merged)) == order(fields)


def test_a_malformed_or_unknown_identifier_is_dropped_with_a_notice(signed_in, world):
    fields = [*form_fields(open_page(signed_in)), ("teema", "ei-ole-uuid")]
    response = post(signed_in, fields)
    assert report_draft.BAD_MATTER_ID in response.content.decode()
    assert order(form_fields(response)) == [str(world[n].pk) for n in ("alpha", "beta", "gamma")]

    stranger = post(signed_in, fields[:-1], action=f"lisa:{factories.MatterFactory.build().pk}")
    assert report_draft.NOT_FOUND in stranger.content.decode()


def test_a_too_long_explanation_is_kept_and_only_blocks_the_export(signed_in, world):
    fields = form_fields(open_page(signed_in))
    name = f"selgitus-{world['alpha'].pk}-S{world['send'].pk}"
    long_text = "õ" * (report_draft.TEXT_LIMIT + 5)
    fields = replace(fields, name, long_text)
    response = post(signed_in, fields, route=EXPORT)
    assert response.status_code == 409
    assert values(form_fields(response), name) == [long_text]
    assert order(form_fields(response)) == order(fields)


def test_the_search_finds_readable_topics_and_carries_only_the_term(signed_in, world):
    response = signed_in.get(reverse(SEARCH), {"otsing": "Beeta"})
    page = response.content.decode()
    assert "Beeta määrus" in page and f"lisa:{world['beta'].pk}" in page
    assert "Alfa" not in page
    assert "Kirjuta vähemalt" in signed_in.get(reverse(SEARCH), {"otsing": "B"}).content.decode()


# ---------------------------------------------------------------------------
# Export: revalidation, parity and the document itself
# ---------------------------------------------------------------------------


def test_the_export_matches_the_preview_row_for_row(signed_in, world):
    fields = form_fields(open_page(signed_in))
    preview = preview_rows(post(signed_in, fields))
    response = post(signed_in, fields, route=EXPORT)

    assert response.status_code == 200
    assert response["Content-Disposition"].startswith(
        'attachment; filename="Majanduspoliitika_ulevaade_2026-10.docx"'
    )
    document, table, rows = docx_rows(response)
    # The same rows; the preview's kind and source labels stay on the page.
    assert rows == [[row[0], row[1].split("\n")[0], *row[2:]] for row in preview]
    assert preview[3][1] == "09.10\nExceli VÄLJA-kuupäev\nleht 2026, rida 14"
    assert rows == [
        [
            "Alfa seaduse muutmine",
            "02.10",
            "Majandus- ja Kommunikatsiooniministeerium",
            "Toetame eelnõu.",
        ],
        ["Beeta määrus", "05.10", "", "Kohtumine ministriga."],
        ["Beeta määrus", "20.10", "", "Täiendav seisukoht."],
        ["Gamma kontseptsioon", "09.10", "", ""],
    ]
    texts = [p.text for p in document.paragraphs if p.text]
    # The department's usual report: the action-plan opening, «Täitmine»,
    # the table, then the day it was made and the signers.
    assert texts[:3] == [
        "Poliitikakujundamise – ja õigusosakonna tegevuskava 2026",
        "1. valdkond – osalemine poliitikakujundamises",
        "Tegevused ja eesmärgid",
    ]
    assert texts[3] == "Täitmine: 01.10.2026 – 31.10.2026"
    assert texts[4:] == [f"{timezone.localdate():%d.%m.%Y}", "Marko Udras, Mait Palts"]
    assert document.tables[0].rows[1].cells[0].text == "Tegevuse kirjeldus:"
    assert [cell.text for cell in table.rows[0].cells] == [
        "Teema",
        "Kuupäev",
        "Kellele",
        "Selgitus",
    ]


def test_the_document_is_a4_estonian_with_a_repeating_header(signed_in, world):
    response = post(signed_in, form_fields(open_page(signed_in)), route=EXPORT)
    document, table, _ = docx_rows(response)
    section = document.sections[0]
    assert (round(section.page_width.cm, 1), round(section.page_height.cm, 1)) == (21.0, 29.7)
    assert section.page_width < section.page_height
    header_pr = table.rows[0]._tr.trPr
    assert header_pr.find(qn("w:tblHeader")) is not None
    assert not [
        row
        for row in table.rows
        if row._tr.trPr is not None and row._tr.trPr.find(qn("w:cantSplit")) is not None
    ]
    lang = document.styles.element.find(qn("w:docDefaults")).find(".//" + qn("w:lang"))
    assert lang.get(qn("w:val")) == "et-EE"
    assert document.core_properties.language == "et-EE"
    assert round(section.left_margin.cm, 1) == 2.5
    assert document.styles["Normal"].font.name == "Times New Roman"
    # Every row carries its own topic: nothing is merged.
    assert [row.cells[0].text for row in table.rows[2:4]] == ["Beeta määrus"] * 2
    assert "w:vMerge" not in table._tbl.xml


def test_the_signers_are_the_readers_to_change(signed_in, world):
    fields = replace(form_fields(open_page(signed_in)), "allkirjastajad", "  Mari   Maasikas ")
    preview = post(signed_in, fields)
    assert values(form_fields(preview), "allkirjastajad") == ["Mari Maasikas"]

    document, _, _ = docx_rows(post(signed_in, form_fields(preview), route=EXPORT))

    assert [p.text for p in document.paragraphs if p.text][-1] == "Mari Maasikas"


def test_a_long_report_flows_over_pages_with_its_text_intact(signed_in, capture_evidence):
    paragraph = "Kaubandus-Tööstuskoda leiab, et õigusselgus ja ettevõtjate halduskoormus ŠŽ. " * 24
    matters = []
    for index in range(30):
        matter = factories.MatterFactory(title=f"Pikk teema {index:02d} – jõustumine")
        sent(
            matter,
            capture_evidence,
            day=date(2026, 10, 1 + index),
            summary=paragraph[:1990],
            title=f"A{index}",
        )
        matters.append(matter)
    response = post(signed_in, form_fields(open_page(signed_in)), route=EXPORT)
    assert response.status_code == 200
    _, table, rows = docx_rows(response)
    assert len(rows) == 30
    assert [row[0] for row in rows] == [m.title for m in matters]
    assert all(row[3] == paragraph[:1990].strip() for row in rows)
    assert table.rows[0]._tr.trPr.find(qn("w:tblHeader")) is not None


def test_a_hand_added_topic_outside_the_period_has_no_date(signed_in, world, capture_evidence):
    later = factories.MatterFactory(title="Aasta pärast")
    sent(later, capture_evidence, day=date(2027, 1, 5))
    fields = form_fields(open_page(signed_in))
    fields = form_fields(post(signed_in, fields, action=f"lisa:{later.pk}"))
    _, _, rows = docx_rows(post(signed_in, fields, route=EXPORT))
    assert rows[-1] == ["Aasta pärast", "", "", ""]


def test_an_edited_source_stops_the_export_until_it_is_reviewed(signed_in, world):
    fields = form_fields(open_page(signed_in))
    Submission.objects.filter(pk=world["second"].pk).update(summary="Muudetud kokkuvõte.")

    refused = post(signed_in, fields, route=EXPORT)
    assert refused.status_code == 409
    page = refused.content.decode()
    assert "allikaandmed on muutunud" in page and "Beeta määrus" in page
    reviewed = form_fields(refused)
    assert order(reviewed) == order(fields)
    assert values(reviewed, f"selgitus-{world['beta'].pk}-S{world['second'].pk}") == [
        "Muudetud kokkuvõte."
    ]

    _, _, rows = docx_rows(post(signed_in, reviewed, route=EXPORT))
    assert rows[2][3] == "Muudetud kokkuvõte."


def test_an_edited_source_never_overwrites_the_readers_own_text(signed_in, world):
    fields = form_fields(open_page(signed_in))
    name = f"selgitus-{world['alpha'].pk}-S{world['send'].pk}"
    fields = replace(fields, name, "Kirjutasin ise.")
    Submission.objects.filter(pk=world["send"].pk).update(summary="Allikas muutus.")
    refused = post(signed_in, fields, route=EXPORT)
    assert refused.status_code == 409
    assert values(form_fields(refused), name) == ["Kirjutasin ise."]


def test_a_revoked_role_gets_no_file(client, world):
    user = factories.UserFactory()
    client.force_login(user)
    fields = form_fields(open_page(client))
    type(user).objects.filter(pk=user.pk).update(role=UserRole.ADMINISTRATOR)
    response = post(client, fields, route=EXPORT)
    assert response.status_code == 404
    assert not response.content.startswith(b"PK")


def test_a_topic_that_became_unreadable_is_not_exported_or_named(client, world, monkeypatch):
    user = factories.UserFactory()
    client.force_login(user)
    fields = form_fields(open_page(client))
    Matter.objects.filter(pk=world["beta"].pk).update(visibility=Visibility.RESTRICTED)
    _narrow(monkeypatch)

    refused = post(client, fields, route=EXPORT)
    assert refused.status_code == 409
    page = refused.content.decode()
    assert report_draft.UNAVAILABLE in page
    assert "Beeta" not in page and "Täiendav seisukoht" not in page
    assert str(world["beta"].pk) not in order(form_fields(refused))
    _, _, rows = docx_rows(post(client, form_fields(refused), route=EXPORT))
    assert [row[0] for row in rows] == ["Alfa seaduse muutmine", "Gamma kontseptsioon"]


def _narrow(monkeypatch):
    import app.legacy_import.current_state as current_state
    import app.matters.models as matter_models
    import app.submissions.models as submission_models

    def narrow(user):
        return Scope(
            user=user,
            is_authenticated=True,
            sees_restricted_by_role=False,
            break_glass_grant_id=None,
        )

    for module in (matter_models, submission_models, current_state):
        monkeypatch.setattr(module, "scope_for_user", narrow)


def test_a_restricted_child_never_reaches_the_page_or_the_file(
    client, world, capture_evidence, monkeypatch
):
    owner = factories.UserFactory()
    sent(
        world["alpha"],
        capture_evidence,
        day=date(2026, 10, 25),
        restricted=True,
        title="PIIRATUD-ARVAMUS",
        summary="PIIRATUD-SISU",
    )
    only_hidden = factories.MatterFactory(title="Ainult piiratud", owner=owner)
    entry(only_hidden, day=date(2026, 10, 6), restricted=True, body="<p>PIIRATUD-KOHTUMINE</p>")
    _narrow(monkeypatch)
    client.force_login(factories.UserFactory())

    response = open_page(client)
    page = response.content.decode()
    assert "PIIRATUD" not in page and "Ainult piiratud" not in page
    # Counts do not betray it either: three topics, as before it existed.
    assert "Valitud 3 teemat 3-st" in page
    export = post(client, form_fields(response), route=EXPORT)
    assert b"PIIRATUD" not in export.content
    document = Document(io.BytesIO(export.content))
    assert all("PIIRATUD" not in cell.text for row in document.tables[1].rows for cell in row.cells)


def test_composing_and_exporting_changes_no_canonical_record(signed_in, world):
    tracked = [world["send"], world["second"], world["meeting"], world["alpha"], world["valja"]]
    before = {
        (type(r).__name__, r.pk): type(r)
        .objects.filter(pk=r.pk)
        .values_list("updated_at", flat=True)
        .get()
        for r in tracked
    }
    events = ChangeEvent.objects.count()
    entries = list(Entry.objects.values_list("pk", "body", "edit_count"))

    fields = form_fields(open_page(signed_in))
    fields = replace(
        fields, f"selgitus-{world['alpha'].pk}-S{world['send'].pk}", "Uus tekst ainult ülevaatesse."
    )
    fields = form_fields(post(signed_in, fields, action=f"alla:{world['alpha'].pk}"))
    assert post(signed_in, fields, route=EXPORT).status_code == 200

    after = {
        (type(r).__name__, r.pk): type(r)
        .objects.filter(pk=r.pk)
        .values_list("updated_at", flat=True)
        .get()
        for r in tracked
    }
    assert after == before
    assert ChangeEvent.objects.count() == events
    assert list(Entry.objects.values_list("pk", "body", "edit_count")) == entries
    assert Submission.objects.get(pk=world["send"].pk).summary == "Toetame eelnõu."


def test_a_planned_meeting_is_not_proposed_on_the_page(signed_in):
    matter = factories.MatterFactory(title="Tulevane kohtumine")
    today = timezone.localdate()
    entry(matter, day=today + timedelta(days=2), kind=EntryKind.MEETING)
    response = open_page(
        signed_in,
        algus=f"{today.day}.{today.month}.{today.year}",
        lopp=_estonian(today + timedelta(days=20)),
    )
    assert "Tulevane kohtumine" not in response.content.decode()


def _estonian(day: date) -> str:
    return f"{day.day}.{day.month}.{day.year}"
