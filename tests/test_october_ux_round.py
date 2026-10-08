"""The October UX / workflow round (owner's brief, 2026-10-07).

A  Teema andmed — `Õigusakt` stacked like `Saatja`.
B  PRAEGUNE TEGEVUS — planned rows before `+ Lisa tegevus`.
C  Uus teema — ticked `Sarnased teemad` are linked in the same save.
D  Uploads — a display title typed before the save becomes `Document.title`.
F  KOJA ARVAMUS — the deadline until an opinion exists, never after.
G  LISA TEEMALE — Ülevaade/uudis, Kaasamine, Arvamus/tagasiside, Lisa.
H  Ülevaade vs Uudis — stored, read from koda.ee's paths, asked otherwise.
I  Kaasamine — `Ülevaate link` prefilled with the newest Ülevaade; a save
   records the Ülevaade once; news refused.
J  Alusta kaasamist takes files.
K  The waiting round under PRAEGUNE TEGEVUS — day, Smaily, Alchemer, Tehtud.
L  Lisa tagasiside finishes the round.
M  Dokumendid — no Roll, rename inside `⋯`, the two tooltips; since
   2026-10-08 `⋯` is a floating menu and the rename is its `Muuda nime`.

(E — drag and drop — is a browser behaviour: e2e/test_upload_queue.py.)
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from app.core.middleware import UploadTitlesMiddleware
from app.documents.enums import DocumentRole
from app.documents.links import DocumentLink
from app.documents.models import Document
from app.documents.services import add_evidence_version
from app.matters.enums import EngagementKind, WebsiteOverviewKind
from app.matters.models import Matter, MatterExternalPosition, MatterWebsiteOverview
from app.matters.publication_kind import classify_publication_url
from app.matters.services import (
    add_engagement,
    close_matter,
    plan_website_overview,
    publish_website_overview,
    set_legal_instruments,
)
from app.related_materials.models import MatterRelation
from app.submissions.enums import SubmissionStatus
from app.taxonomy.models import LegalInstrumentType
from app.workflow.enums import Disposition
from app.workflow.services import add_planned_action, set_next_action_for_new_work
from tests import factories

pytestmark = pytest.mark.django_db

HX = {"HX-Request": "true"}
OVERVIEW_URL = "https://www.koda.ee/et/meie-moju/hetkel-kasil/avalda-arvamust-naidis"
NEWER_OVERVIEW_URL = "https://www.koda.ee/et/meie-moju/hetkel-kasil/teine-naidis"
NEWS_URL = "https://www.koda.ee/et/uudised/koja-hinnangul-naidis"


def _detail(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _zone(body: str) -> str:
    return body[body.index('id="praegune-tegevus"') : body.index('id="lisa-teemale"')]


def _day(offset: int):
    return timezone.localdate() + timedelta(days=offset)


def _pdf(name: str, body: bytes = b"%PDF-1.4\nnaidis") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, body, content_type="application/pdf")


def _overview(matter, actor, url, *, kind="", title=""):
    planned = plan_website_overview(matter=matter, actor=actor, kind=kind)
    return publish_website_overview(
        overview=planned, url=url, published_on=None, actor=actor, title=title, kind=kind or None
    )


def _round(matter, actor, **extra):
    return add_engagement(
        matter=matter,
        kind=EngagementKind.OTHER,
        title="Tööstusettevõtted",
        occurred_on=timezone.localdate(),
        actor=actor,
        **extra,
    )


# ---------------------------------------------------------------------------
# A. Teema andmed
# ---------------------------------------------------------------------------


def test_a_oigusakt_is_stacked_like_saatja(client, specialist, normal_matter):
    client.force_login(specialist)
    set_legal_instruments(
        matter=normal_matter,
        legal_instruments=[LegalInstrumentType.objects.get(key="seadus")],
        actor=specialist,
    )

    rail = _detail(client, normal_matter)
    row = re.search(
        r'<div class="railcard__row railcard__row--stacked">\s*'
        r'<span class="railcard__key">Õigusakt</span>',
        rail,
    )
    assert row, "Õigusakt is not a stacked row"


# ---------------------------------------------------------------------------
# B. Planned rows before `+ Lisa tegevus`
# ---------------------------------------------------------------------------


def test_b_planned_rows_come_before_the_add_control(client, specialist, normal_matter):
    client.force_login(specialist)
    set_next_action_for_new_work(
        matter=normal_matter, text="Loe eelnõu", target_date=_day(3), actor=specialist
    )
    add_planned_action(
        matter=normal_matter, text="Helista ministeeriumile", target_date=_day(9), actor=specialist
    )

    zone = _zone(_detail(client, normal_matter))

    assert zone.index("Loe eelnõu") < zone.index("Helista ministeeriumile")
    assert zone.index("Helista ministeeriumile") < zone.index('id="lisa-planeeritud"')
    assert "Planeeritud tegevused</h3>" not in zone


# ---------------------------------------------------------------------------
# C. Similar Matters linked at creation
# ---------------------------------------------------------------------------


def _relations_of(matter) -> set:
    return {
        relation.matter_b_id if relation.matter_a_id == matter.pk else relation.matter_a_id
        for relation in MatterRelation.objects.filter(matter_a=matter)
        | MatterRelation.objects.filter(matter_b=matter)
    }


def test_c_ticked_suggestions_are_linked_with_the_new_matter(client, specialist):
    client.force_login(specialist)
    first = factories.MatterFactory(owner=specialist, title="Pakendiseaduse eelnõu A")
    second = factories.MatterFactory(owner=specialist, title="Pakendiseaduse eelnõu B")
    untouched = factories.MatterFactory(owner=specialist, title="Pakendiseaduse eelnõu C")

    client.post(
        reverse("matters:matter_create"),
        {
            "title": "Uus pakendiseaduse teema",
            "seo_teemaga": [str(first.pk), str(second.pk), str(first.pk)],
        },
    )

    created = Matter.objects.get(title="Uus pakendiseaduse teema")
    assert _relations_of(created) == {first.pk, second.pk}
    assert untouched.pk not in _relations_of(created)
    assert MatterRelation.objects.count() == 2


def test_c_one_ticked_suggestion_is_linked(client, specialist):
    client.force_login(specialist)
    other = factories.MatterFactory(owner=specialist, title="Varasem teema")

    client.post(
        reverse("matters:matter_create"), {"title": "Üks seos", "seo_teemaga": [str(other.pk)]}
    )

    assert _relations_of(Matter.objects.get(title="Üks seos")) == {other.pk}


def test_c_nothing_ticked_links_nothing(client, specialist):
    client.force_login(specialist)
    factories.MatterFactory(owner=specialist, title="Varasem teema")

    client.post(reverse("matters:matter_create"), {"title": "Ilma seosteta"})

    assert Matter.objects.filter(title="Ilma seosteta").exists()
    assert MatterRelation.objects.count() == 0


@pytest.mark.parametrize("value", ["01a00000-0000-7000-8000-000000000000", "ei-ole-uuid"])
def test_c_a_link_that_cannot_be_made_refuses_the_whole_save(client, specialist, value):
    """A ticked value that is not a Matter this reader may see — gone, or not an
    id at all — refuses the save: no Teema, no link, nothing half-written."""
    client.force_login(specialist)

    response = client.post(
        reverse("matters:matter_create"),
        {"title": "Ei tohi tekkida", "seo_teemaga": [value]},
    )

    assert response.status_code in (200, 400)
    assert not Matter.objects.filter(title="Ei tohi tekkida").exists()
    assert MatterRelation.objects.count() == 0


def test_c_suggestion_cards_are_unticked_by_default_and_keep_a_tick(client, specialist):
    client.force_login(specialist)
    earlier = factories.MatterFactory(owner=specialist, title="Jäätmeseaduse muutmine")

    url = reverse("related_materials:draft_suggestions")
    plain = client.post(url, {"title": "Jäätmeseaduse muutmine 2027"}).content.decode()
    ticked = client.post(
        url, {"title": "Jäätmeseaduse muutmine 2027", "seo_teemaga": [str(earlier.pk)]}
    ).content.decode()

    if f'value="{earlier.pk}"' not in plain:
        pytest.skip("the engine did not suggest the earlier Matter for this title")
    box = re.search(rf'<input type="checkbox" name="seo_teemaga" value="{earlier.pk}"[^>]*>', plain)
    assert box and "checked" not in box.group(0)
    box = re.search(
        rf'<input type="checkbox" name="seo_teemaga" value="{earlier.pk}"[^>]*>', ticked
    )
    assert box and "checked" in box.group(0)


# ---------------------------------------------------------------------------
# D. Display titles before the save
# ---------------------------------------------------------------------------


def test_d_the_middleware_pairs_titles_with_files_in_order():
    first, second = _pdf("a.pdf"), _pdf("b.pdf")
    request = RequestFactory().post(
        "/x/", {"attachments": [first, second], "attachments__pealkiri": ["Esimene", "Teine"]}
    )
    seen = {}

    def view(req):
        seen["titles"] = [
            getattr(upload, "display_title", None) for upload in req.FILES.getlist("attachments")
        ]
        return None

    UploadTitlesMiddleware(view)(request)

    assert seen["titles"] == ["Esimene", "Teine"]


def test_d_a_count_that_does_not_match_is_ignored():
    request = RequestFactory().post(
        "/x/", {"attachments": [_pdf("a.pdf"), _pdf("b.pdf")], "attachments__pealkiri": ["Üks"]}
    )
    seen = {}

    def view(req):
        seen["titles"] = [
            getattr(upload, "display_title", None) for upload in req.FILES.getlist("attachments")
        ]

    UploadTitlesMiddleware(view)(request)

    assert seen["titles"] == [None, None]


def _start_round(client, matter, **data):
    payload = {
        "audience": "Liikmed (sünteetiline)",
        "occurred_on": timezone.localdate().strftime("%d.%m.%Y"),
        **data,
    }
    return client.post(
        reverse("matters:add_engagement_compact", kwargs={"pk": matter.pk}), payload, headers=HX
    )


def test_d_titles_typed_before_the_save_become_the_document_titles(
    client, specialist, normal_matter
):
    client.force_login(specialist)

    _start_round(
        client,
        normal_matter,
        attachments=[_pdf("10-13_Fookus_04.10_Heli.pdf"), _pdf("kutse.pdf", b"%PDF-1.4\nteine")],
        attachments__pealkiri=["Fookusgrupi kokkuvõte 4. oktoober", "  "],
    )

    documents = {
        doc.current_version.original_filename: doc.title
        for doc in Document.objects.filter(matter=normal_matter)
    }
    assert documents == {
        "10-13_Fookus_04.10_Heli.pdf": "Fookusgrupi kokkuvõte 4. oktoober",
        "kutse.pdf": "kutse.pdf",
    }


def test_d_uus_teema_files_take_their_typed_titles(client, specialist):
    client.force_login(specialist)

    client.post(
        reverse("matters:matter_create"),
        {
            "title": "Failiga teema",
            "files": [_pdf("skann_0412.pdf")],
            "files__pealkiri": ["Ministeeriumi kiri"],
        },
    )

    document = Document.objects.get(matter__title="Failiga teema")
    assert document.title == "Ministeeriumi kiri"
    assert document.current_version.original_filename == "skann_0412.pdf"


def test_d_a_staged_row_shows_its_title_as_text_and_edits_it_only_on_request(client, specialist):
    """Uus teema's staged row: the filename as text and a ✎, not a box.

    The markup is the shared upload queue's (static/js/ux.js `openTitleEdit`):
    the posted title is a hidden input, the ✎ is the only way to a box, and
    no text box is rendered until somebody asks for one (owner's round,
    2026-10-08). The posted name is unchanged, so `Loo teema` still files the
    file under the title it carries.
    """
    client.force_login(specialist)
    staged = client.post(reverse("matters:intake_stage"), {"files": [_pdf("skann_0412.pdf")]})
    session = staged.context["intake_session"]
    body = staged.content.decode()
    row = re.search(r'<li class="dropzone__file" data-intake-file="([^"]+)">(.*?)</li>', body, re.S)
    assert row, "no staged row"
    file_id, markup = row.group(1), row.group(2)

    assert 'type="text"' not in markup
    assert re.search(
        rf'<input type="hidden" name="intake_title__{file_id}" value="skann_0412.pdf"\s+'
        r"data-title-edit-value data-staged-title>",
        markup,
    )
    assert re.search(
        r'<span class="titleedit__text" data-title-edit-text>skann_0412.pdf</span>', markup
    )
    pencil = re.search(r"<button [^>]*data-title-edit-open[^>]*>✎</button>", markup)
    assert pencil
    assert 'type="button"' in pencil.group(0)
    assert 'aria-label="Muuda pealkirja: skann_0412.pdf"' in pencil.group(0)
    assert 'title="Muuda pealkirja"' in pencil.group(0)
    assert re.search(r"data-title-edit-original hidden>skann_0412.pdf</span>", markup)

    client.post(
        reverse("matters:matter_create"),
        {
            "title": "Lavastatud failiga teema",
            "intake": str(session.pk),
            f"intake_title__{file_id}": "Ministeeriumi kiri",
        },
    )

    document = Document.objects.get(matter__title="Lavastatud failiga teema")
    assert document.title == "Ministeeriumi kiri"
    assert document.current_version.original_filename == "skann_0412.pdf"


# ---------------------------------------------------------------------------
# F. KOJA ARVAMUS
# ---------------------------------------------------------------------------


def _rail_card(body: str) -> str:
    card = body[body.index('id="koja-arvamus"') :]
    return card[: card.index("</div>\n", card.index("railcard__label"))]


def test_f_no_opinion_and_a_deadline_shows_the_deadline(client, specialist):
    client.force_login(specialist)
    deadline = _day(20)
    matter = factories.MatterFactory(owner=specialist, response_deadline=deadline)

    card = _rail_card(_detail(client, matter))

    assert "Arvamuse tähtaeg" in card
    assert f"{deadline.day}.{deadline.month}.{deadline.year}" in card
    assert "Puudub" not in card


def test_f_no_opinion_and_no_deadline_is_puudub(client, specialist, normal_matter):
    client.force_login(specialist)

    card = _rail_card(_detail(client, normal_matter))

    assert "Puudub" in card
    assert "Arvamuse tähtaeg" not in card


def _sent_opinion(matter, *, with_file: bool):
    final = None
    if with_file:
        document = factories.DocumentFactory(matter=matter, role=DocumentRole.KODA_SUBMISSION_FINAL)
        final = add_evidence_version(
            document=document,
            content=b"%PDF-1.4\narvamus",
            original_filename="Koja_arvamus.pdf",
            mime_type="application/pdf",
        )
    return factories.SubmissionFactory(
        matter=matter,
        title="Koja arvamus",
        status=SubmissionStatus.SENT,
        sent_at=timezone.now(),
        final_version=final,
    )


@pytest.mark.parametrize("with_file", [True])
def test_f_once_an_opinion_exists_a_new_deadline_stays_off_the_card(client, specialist, with_file):
    client.force_login(specialist)
    matter = factories.MatterFactory(owner=specialist, response_deadline=_day(-30))
    _sent_opinion(matter, with_file=with_file)
    Matter.objects.filter(pk=matter.pk).update(response_deadline=_day(25))

    body = _detail(client, matter)
    card = _rail_card(body)

    assert "Arvamuse tähtaeg" not in card
    if with_file:
        assert "Koja_arvamus.pdf" in card
    header = body[: body.index('id="praegune-tegevus"')]
    assert "Arvamuse tähtaeg" in header


# ---------------------------------------------------------------------------
# G. LISA TEEMALE order
# ---------------------------------------------------------------------------


def test_g_the_launchers_read_in_the_owners_order(client, specialist, normal_matter):
    client.force_login(specialist)

    body = _detail(client, normal_matter)
    zone = body[body.index('id="lisa-teemale"') :]
    launchers = re.findall(
        r'<label class="disclosure-chip" for="lisa-[a-z]+-valik">([^<]+)</label>', zone
    )

    assert launchers == [
        "+ Ülevaade / uudis",
        "+ Kaasamine",
        "+ Arvamus / tagasiside",
        "+ Lisa",
    ]


# ---------------------------------------------------------------------------
# H. Ülevaade vs Uudis
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        (OVERVIEW_URL, WebsiteOverviewKind.OVERVIEW),
        ("https://koda.ee/et/meie-moju/hetkel-kasil", WebsiteOverviewKind.OVERVIEW),
        ("https://www.koda.ee/en/current-drafts/a-draft", WebsiteOverviewKind.OVERVIEW),
        (NEWS_URL, WebsiteOverviewKind.NEWS),
        ("https://www.koda.ee/en/news/some-news", WebsiteOverviewKind.NEWS),
        ("https://www.koda.ee/ru/novosti/nashi-novosti", WebsiteOverviewKind.NEWS),
        ("https://www.koda.ee/uudised/vana-aadress", WebsiteOverviewKind.NEWS),
        ("https://koda.ee/meie-moju/hetkel-kasil/vana", WebsiteOverviewKind.OVERVIEW),
        ("https://www.koda.ee/hetkel-kasil/x", ""),
        ("https://www.koda.ee/et/meie-moju", ""),
        ("https://www.err.ee/uudised/x", ""),
        ("", ""),
    ],
)
def test_h_koda_paths_are_classified(url, kind):
    assert classify_publication_url(url) == kind


def _add_publication(client, matter, url, **extra):
    return client.post(
        reverse("matters:add_website_overview", kwargs={"pk": matter.pk}),
        {"url": url, "published_on": timezone.localdate().strftime("%d.%m.%Y"), **extra},
        headers=HX,
    )


def test_h_a_known_address_stores_its_kind(client, specialist, normal_matter):
    client.force_login(specialist)

    _add_publication(client, normal_matter, OVERVIEW_URL)
    _add_publication(client, normal_matter, NEWS_URL, kind="OVERVIEW")

    kinds = dict(MatterWebsiteOverview.objects.values_list("url", "kind"))
    assert kinds == {OVERVIEW_URL: "OVERVIEW", NEWS_URL: "NEWS"}


def test_h_an_unknown_address_needs_an_explicit_kind(client, specialist, normal_matter):
    client.force_login(specialist)
    unknown = "https://www.example.org/artikkel"

    refused = _add_publication(client, normal_matter, unknown)
    assert "Vali, kas see on ülevaade või uudis." in refused.content.decode()
    assert not MatterWebsiteOverview.objects.exists()

    _add_publication(client, normal_matter, unknown, kind="NEWS")
    assert MatterWebsiteOverview.objects.get().kind == "NEWS"


def test_h_the_chronology_names_the_kind(client, specialist, normal_matter):
    client.force_login(specialist)
    _overview(normal_matter, specialist, OVERVIEW_URL, kind="OVERVIEW", title="Hetkel käsil")
    _overview(normal_matter, specialist, NEWS_URL, kind="NEWS", title="Koja hinnang")

    body = _detail(client, normal_matter)

    assert "Ülevaade – Hetkel käsil" in body
    assert "Uudis – Koja hinnang" in body


# ---------------------------------------------------------------------------
# I. Kaasamine and the Ülevaade
# ---------------------------------------------------------------------------


def _start_form_url_value(body: str) -> str:
    form = body[body.index('id="kaasamine-alusta"') :]
    found = re.search(r'<input[^>]*name="website_url"[^>]*>', form)
    assert found
    value = re.search(r'value="([^"]*)"', found.group(0))
    return value.group(1) if value else ""


def test_i_the_newest_overview_is_prefilled_and_news_never(client, specialist, normal_matter):
    client.force_login(specialist)
    _overview(normal_matter, specialist, OVERVIEW_URL, kind="OVERVIEW")
    _overview(normal_matter, specialist, NEWER_OVERVIEW_URL, kind="OVERVIEW")
    _overview(normal_matter, specialist, NEWS_URL, kind="NEWS")

    assert _start_form_url_value(_detail(client, normal_matter)) == NEWER_OVERVIEW_URL


def test_i_no_overview_leaves_the_link_empty(client, specialist, normal_matter):
    client.force_login(specialist)
    _overview(normal_matter, specialist, NEWS_URL, kind="NEWS")

    assert _start_form_url_value(_detail(client, normal_matter)) == ""


def test_i_a_typed_overview_link_records_the_overview_once(client, specialist, normal_matter):
    client.force_login(specialist)

    _start_round(client, normal_matter, website_url=OVERVIEW_URL)
    _start_round(client, normal_matter, website_url=OVERVIEW_URL + "/")

    overviews = MatterWebsiteOverview.objects.filter(matter=normal_matter)
    assert overviews.count() == 1
    assert overviews.get().kind == "OVERVIEW"
    assert normal_matter.engagements.count() == 2


def test_i_an_existing_overview_is_reused(client, specialist, normal_matter):
    client.force_login(specialist)
    existing = _overview(normal_matter, specialist, OVERVIEW_URL, kind="OVERVIEW")

    _start_round(client, normal_matter, website_url=OVERVIEW_URL)

    assert list(MatterWebsiteOverview.objects.filter(matter=normal_matter)) == [existing]


def test_i_a_news_address_is_refused_as_an_overview_link(client, specialist, normal_matter):
    client.force_login(specialist)

    response = _start_round(client, normal_matter, website_url=NEWS_URL)

    assert "See on uudise aadress." in response.content.decode()
    assert not normal_matter.engagements.exists()
    assert not MatterWebsiteOverview.objects.exists()


# ---------------------------------------------------------------------------
# J. Files on Alusta kaasamist
# ---------------------------------------------------------------------------


def test_j_starting_a_round_files_its_files_on_the_round(client, specialist, normal_matter):
    client.force_login(specialist)

    _start_round(
        client,
        normal_matter,
        attachments=[_pdf("kutse.pdf")],
        attachments__pealkiri=["Kutse liikmetele"],
    )

    engagement = normal_matter.engagements.get()
    link = DocumentLink.objects.get(document__matter=normal_matter)
    assert link.engagement_id == engagement.pk
    assert link.document.title == "Kutse liikmetele"
    assert not MatterExternalPosition.objects.exists()


# ---------------------------------------------------------------------------
# K. The waiting round under PRAEGUNE TEGEVUS
# ---------------------------------------------------------------------------


def test_k_the_wait_row_is_operational(client, specialist, normal_matter):
    client.force_login(specialist)
    engagement = _round(
        normal_matter,
        specialist,
        feedback_deadline=_day(5),
        smaily_url="https://sendsmaily.net/naidis",
        alchemer_url="https://survey.alchemer.eu/naidis",
    )

    zone = _zone(_detail(client, normal_matter))
    row = re.search(r'<p class="curact__owed curact__owed--wait"[^>]*>(.*?)</p>', zone, re.S)

    assert row
    line = row.group(1)
    assert "Tööstusettevõtted" not in line
    assert 'href="https://sendsmaily.net/naidis"' in line and ">Smaily<" in line
    assert 'href="https://survey.alchemer.eu/naidis"' in line and ">Alchemer<" in line
    assert f'data-open-feedback="{engagement.pk}"' in line and ">Tehtud<" in line


def test_k_absent_links_draw_no_dead_labels(client, specialist, normal_matter):
    client.force_login(specialist)
    _round(normal_matter, specialist, feedback_deadline=_day(5))

    zone = _zone(_detail(client, normal_matter))
    row = re.search(r'<p class="curact__owed curact__owed--wait"[^>]*>(.*?)</p>', zone, re.S)

    assert row and "Smaily" not in row.group(1) and "Alchemer" not in row.group(1)


# ---------------------------------------------------------------------------
# L. Lisa tagasiside finishes the round
# ---------------------------------------------------------------------------


def test_l_saving_feedback_records_it_files_it_and_closes_the_round(
    client, specialist, normal_matter
):
    client.force_login(specialist)
    engagement = _round(normal_matter, specialist, feedback_deadline=_day(5))

    response = client.post(
        reverse("matters:add_engagement_reply", kwargs={"pk": normal_matter.pk}),
        {
            "engagement": str(engagement.pk),
            "summary": "Liikmed toetavad.",
            "stated_on": timezone.localdate().strftime("%d.%m.%Y"),
            "attachments": [_pdf("vastus.pdf")],
            "attachments__pealkiri": ["Liikmete vastus"],
        },
        headers=HX,
    )

    assert response.status_code == 200
    engagement.refresh_from_db()
    assert engagement.feedback_closed_at is not None
    position = MatterExternalPosition.objects.get(engagement=engagement)
    assert position.summary == "Liikmed toetavad."
    assert Document.objects.get(matter=normal_matter).title == "Liikmete vastus"
    assert 'data-feedback-wait="' not in _zone(response.content.decode())


# ---------------------------------------------------------------------------
# M. Dokumendid
# ---------------------------------------------------------------------------


def test_m_dokumendid_is_simpler(client, specialist, normal_matter):
    client.force_login(specialist)
    document = factories.DocumentFactory(
        matter=normal_matter, role=DocumentRole.INCOMING_AUTHORITY, title="Ministeeriumi kiri"
    )
    add_evidence_version(
        document=document,
        content=b"%PDF-1.4\nkiri",
        original_filename="kiri.pdf",
        mime_type="application/pdf",
    )

    body = client.get(
        reverse("matters:matter_documents", kwargs={"pk": normal_matter.pk})
    ).content.decode()
    table = body[body.index('class="table doctable"') :]
    table = table[: table.index("</table>")]

    assert '<th scope="col">Roll</th>' not in table
    assert "Saabunud ametlik dokument" not in table
    assert "Roll — kõik" not in body
    assert '<details class="docrename"' not in body
    # The tooltip is the one word; the accessible name names the row (ENG-098).
    assert 'title="Tõmba alla" aria-label="Tõmba alla Ministeeriumi kiri"' in table
    assert 'title="Muuda" aria-label="Muuda Ministeeriumi kiri">⋯</summary>' in table
    menu = table[table.index(f'id="muuda-pealkiri-{document.pk}"') :]
    assert reverse("documents:rename", kwargs={"pk": document.pk}) in menu


def test_m_rename_from_the_menu_still_works(client, specialist, normal_matter):
    client.force_login(specialist)
    document = factories.DocumentFactory(matter=normal_matter, title="vana.pdf")
    add_evidence_version(
        document=document,
        content=b"%PDF-1.4\nx",
        original_filename="vana.pdf",
        mime_type="application/pdf",
    )

    client.post(
        reverse("documents:rename", kwargs={"pk": document.pk}),
        {"title": "Uus pealkiri", "tagasi": "dokumendid"},
    )

    document.refresh_from_db()
    assert document.title == "Uus pealkiri"
    assert document.current_version.original_filename == "vana.pdf"


def test_m_the_menu_floats_and_renames_behind_a_command(client, specialist, normal_matter):
    """`⋯` is a floating menu whose first command is `Muuda nime` (2026-10-08).

    The browser half — the row does not grow, the panel stays on screen, Escape
    and a click outside close it — is e2e/test_document_menu.py. This pins the
    markup that contract hangs on: the shared popover attributes, a fixed panel,
    and the rename form behind a command rather than open the moment `⋯` is.
    """
    client.force_login(specialist)
    document = factories.DocumentFactory(
        matter=normal_matter, role=DocumentRole.INCOMING_AUTHORITY, title="Ministeeriumi kiri"
    )
    add_evidence_version(
        document=document,
        content=b"%PDF-1.4\nkiri",
        original_filename="kiri.pdf",
        mime_type="application/pdf",
    )

    body = client.get(
        reverse("matters:matter_documents", kwargs={"pk": normal_matter.pk})
    ).content.decode()

    assert f'id="muuda-pealkiri-{document.pk}" data-uxpopover>' in body
    menu = body[body.index(f'id="muuda-pealkiri-{document.pk}"') :]
    menu = menu[: menu.index("Dokumendi leht") + len("Dokumendi leht")]
    assert '<div class="opinionmenu__body" data-uxfloat="fit">' in menu
    assert '<details class="docmenu__rename" data-docrename>' in menu
    command = menu.index('<summary class="opinionmenu__item">Muuda nime</summary>')
    form = menu.index(reverse("documents:rename", kwargs={"pk": document.pk}))
    assert command < form < menu.index("Dokumendi leht")

    css = (Path(settings.BASE_DIR) / "static" / "css" / "app.css").read_text(encoding="utf-8")
    panel = css[css.index("\n.opinionmenu__body {") :]
    panel = panel[: panel.index("}")]
    assert "position: fixed;" in panel
    assert "overflow-y: auto;" in panel


def test_m_a_reader_gets_the_menu_without_the_rename(client, specialist, normal_matter):
    """Closed teema: `⋯` still opens, with the document's page and no `Muuda nime`."""
    client.force_login(specialist)
    document = factories.DocumentFactory(matter=normal_matter, title="Suletud kiri")
    add_evidence_version(
        document=document,
        content=b"%PDF-1.4\nsuletud",
        original_filename="suletud.pdf",
        mime_type="application/pdf",
    )
    close_matter(matter=normal_matter, disposition=Disposition.COMPLETED, actor=specialist)

    body = client.get(
        reverse("matters:matter_documents", kwargs={"pk": normal_matter.pk})
    ).content.decode()

    menu = body[body.index(f'id="muuda-pealkiri-{document.pk}"') :]
    menu = menu[: menu.index("Dokumendi leht")]
    assert "Muuda nime" not in menu
    assert reverse("documents:rename", kwargs={"pk": document.pk}) not in menu
