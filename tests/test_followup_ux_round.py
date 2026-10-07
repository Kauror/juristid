"""The follow-up UX round after the October round (owner's brief, 2026-10-07).

A  `+ Ülevaade / uudis` — the link first; the kind and the koda.ee title are
   previewed before the save; the save still decides on its own.
B  PRAEGUNE TEGEVUS — every dated row opens on its date.
D  `+ Lisa` — no `Tavaline`; `Arvamuse tähtaeg` first; refusals reopen it.
E  A new `Arvamuse tähtaeg` after an answered one: a fresh request the old
   opinion does not answer, and never an overwrite of a current one.
G  Tegevused — a planned action's line is its own words.

(C — long rows — and F — the `L` shortcut — are browser behaviour:
e2e/test_followup_ux_round.py.)
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.intelligence.services import add_important_date
from app.matters import publication_title
from app.matters.enums import EngagementKind, ResponseDeadlineOutcome, WebsiteOverviewKind
from app.matters.models import Matter, MatterResponseDeadline, MatterWebsiteOverview
from app.matters.response_deadlines import (
    LEGACY_DISCHARGE_NOTE,
    change_response_deadline,
    deadline_revision,
)
from app.matters.services import add_engagement
from app.matters.timeline import matter_timeline
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.workflow.services import add_planned_action, set_next_action, set_next_action_for_new_work
from tests import factories

pytestmark = pytest.mark.django_db

HX = {"HX-Request": "true"}
OVERVIEW_URL = "https://www.koda.ee/et/meie-moju/hetkel-kasil/avalda-arvamust-naidis"
NEWS_URL = "https://www.koda.ee/et/uudised/koja-hinnangul-naidis"
OTHER_URL = "https://www.mkm.ee/uudised/naidis"


def _day(offset: int):
    return timezone.localdate() + timedelta(days=offset)


def _et(day) -> str:
    return f"{day.day}.{day.month}.{day.year}"


def _detail(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _zone(body: str) -> str:
    return body[body.index('id="praegune-tegevus"') : body.index('id="lisa-teemale"')]


def _text(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


# ---------------------------------------------------------------------------
# A. `+ Ülevaade / uudis`
# ---------------------------------------------------------------------------


def _publication_form(body: str) -> str:
    start = body.index("data-publication-form")
    return body[start : body.index("</form>", start)]


def test_a_the_link_is_the_first_field_then_date_title_kind(signed_in, normal_matter):
    form = _publication_form(_detail(signed_in, normal_matter))

    positions = [
        form.index('name="url"'),
        form.index('name="published_on"'),
        form.index('name="overview_title"'),
        form.index('name="kind"'),
    ]
    assert positions == sorted(positions)
    assert f'value="{_et(timezone.localdate())}"' in form or (
        f'value="{timezone.localdate():%d.%m.%Y}"' in form
    )


def _preview(client, matter, url):
    return client.post(
        reverse("matters:preview_website_overview", kwargs={"pk": matter.pk}),
        {"url": url},
        headers=HX,
    )


@pytest.fixture
def koda_titles(monkeypatch, settings):
    """The fetch switched on and answered locally: the suite never contacts koda.ee."""
    settings.PUBLICATION_TITLE_FETCH = True
    pages = {}

    def fake_fetch(url):
        return pages.get(url, "")

    monkeypatch.setattr(publication_title, "_fetch", fake_fetch)
    return pages


def test_a_a_known_overview_previews_ulevaade_and_its_title(signed_in, normal_matter, koda_titles):
    koda_titles[OVERVIEW_URL] = (
        '<head><meta property="og:title" content="Avalda arvamust &amp; näidis">'
        "<title>Muu | Eesti Kaubandus-Tööstuskoda</title></head>"
    )

    answer = _preview(signed_in, normal_matter, OVERVIEW_URL).json()

    assert answer == {
        "url": OVERVIEW_URL,
        "kind": WebsiteOverviewKind.OVERVIEW,
        "title": "Avalda arvamust & näidis",
        "title_status": "found",
    }


def test_a_a_known_news_item_previews_uudis(signed_in, normal_matter, koda_titles):
    koda_titles[NEWS_URL] = "<title>Koja hinnangul näidis | Eesti Kaubandus-Tööstuskoda</title>"

    answer = _preview(signed_in, normal_matter, NEWS_URL).json()

    assert answer["kind"] == WebsiteOverviewKind.NEWS
    assert answer["title"] == "Koja hinnangul näidis"


def test_a_an_unreadable_koda_page_says_so_and_still_classifies(
    signed_in, normal_matter, koda_titles
):
    answer = _preview(signed_in, normal_matter, NEWS_URL).json()

    assert answer["kind"] == WebsiteOverviewKind.NEWS
    assert answer["title"] == ""
    assert answer["title_status"] == "failed"


def test_a_an_unknown_address_is_neither_fetched_nor_classified(
    signed_in, normal_matter, koda_titles, monkeypatch
):
    def must_not_fetch(url):  # pragma: no cover - the assertion is that it is not called
        raise AssertionError(f"fetched {url}")

    monkeypatch.setattr(publication_title, "_fetch", must_not_fetch)

    answer = _preview(signed_in, normal_matter, OTHER_URL).json()

    assert answer == {"url": OTHER_URL, "kind": "", "title": "", "title_status": ""}


def test_a_the_preview_writes_nothing(signed_in, normal_matter, koda_titles):
    _preview(signed_in, normal_matter, OVERVIEW_URL)

    assert not MatterWebsiteOverview.objects.filter(matter=normal_matter).exists()


def test_a_the_suite_never_fetches(signed_in, normal_matter, monkeypatch):
    """`PUBLICATION_TITLE_FETCH` is off in test settings, so nothing is asked."""
    monkeypatch.setattr(publication_title, "_fetch", lambda url: pytest.fail("fetched"))

    answer = _preview(signed_in, normal_matter, NEWS_URL).json()

    assert answer["kind"] == WebsiteOverviewKind.NEWS
    assert answer["title_status"] == ""


@pytest.mark.parametrize(
    ("url", "fetchable"),
    [
        ("http://koda.ee/et/uudised/x", "https://koda.ee/et/uudised/x"),
        ("https://www.koda.ee/et/uudised/x#osa", "https://www.koda.ee/et/uudised/x"),
        ("https://koda.ee:8443/et/uudised/x", ""),
        ("https://kasutaja:parool@koda.ee/et/uudised/x", ""),
        ("https://koda.ee.example.com/x", ""),
        ("https://example.com/koda.ee", ""),
        ("ftp://koda.ee/x", ""),
        ("file:///etc/passwd", ""),
    ],
)
def test_a_only_https_koda_pages_are_ever_fetched(url, fetchable):
    assert publication_title.fetchable_url(url) == fetchable


def test_a_the_save_still_classifies_on_its_own(signed_in, normal_matter):
    """Whatever the preview said, a koda.ee news address is stored as `Uudis`,
    and an unknown one without a kind is refused."""
    url = reverse("matters:add_website_overview", kwargs={"pk": normal_matter.pk})
    signed_in.post(
        url,
        {"url": NEWS_URL, "kind": WebsiteOverviewKind.OVERVIEW, "overview_title": "Mu pealkiri"},
        headers=HX,
    )
    refused = signed_in.post(url, {"url": OTHER_URL}, headers=HX)

    stored = MatterWebsiteOverview.objects.get(matter=normal_matter)
    assert stored.kind == WebsiteOverviewKind.NEWS
    assert stored.title == "Mu pealkiri"
    assert refused.status_code == 400


# ---------------------------------------------------------------------------
# B. Date first
# ---------------------------------------------------------------------------


def _task(zone: str) -> str:
    start = zone.index('<div class="curact__task">')
    return zone[start : zone.index("curact__donechip", start)]


def test_b_the_current_action_opens_on_its_date(signed_in, specialist, normal_matter):
    set_next_action_for_new_work(
        matter=normal_matter, text="Loe eelnõu läbi", target_date=_day(4), actor=specialist
    )

    task = _text(_task(_zone(_detail(signed_in, normal_matter))))

    assert task.startswith(f"{_et(_day(4))} Loe eelnõu läbi")


def test_b_an_undated_action_opens_on_kuupaev_maaramata(signed_in, specialist, normal_matter):
    set_next_action(matter=normal_matter, text="Helista ministeeriumi", actor=specialist)

    task = _task(_zone(_detail(signed_in, normal_matter)))

    assert "curact__date--unset" in task
    assert _text(task).startswith("Kuupäev määramata Helista ministeeriumi")


def test_b_a_planned_action_opens_on_its_date(signed_in, specialist, normal_matter):
    set_next_action_for_new_work(
        matter=normal_matter, text="Loe eelnõu", target_date=_day(2), actor=specialist
    )
    add_planned_action(
        matter=normal_matter, text="Koosta vastus", target_date=_day(9), actor=specialist
    )

    zone = _zone(_detail(signed_in, normal_matter))
    row = re.search(r'<li class="curact__plannedrow">(.*?)</li>', zone, re.S)

    assert _text(row.group(1)).startswith(f"{_et(_day(9))} Koosta vastus")


def _round(matter, actor, **extra):
    return add_engagement(
        matter=matter,
        kind=EngagementKind.OTHER,
        title="Tööstusettevõtted",
        occurred_on=timezone.localdate(),
        actor=actor,
        **extra,
    )


def _wait(zone: str) -> str:
    return re.search(
        r'<p class="curact__owed curact__owed--wait"[^>]*>(.*?)</p>', zone, re.S
    ).group(1)


def test_b_a_feedback_wait_opens_on_its_deadline(signed_in, specialist, normal_matter):
    _round(normal_matter, specialist, feedback_deadline=_day(5))

    line = _text(_wait(_zone(_detail(signed_in, normal_matter))))

    assert line.startswith(f"{_et(_day(5))} Ootame tagasisidet")
    assert "Tööstusettevõtted" not in line


def test_b_a_wait_without_a_deadline_opens_on_tahtaeg_maaramata(
    signed_in, specialist, normal_matter
):
    from app.matters.models import MatterEngagement

    # A tracked round waiting with no reply-by date (one recorded before the
    # date was asked): the wait row still opens on a date cell.
    engagement = _round(normal_matter, specialist, feedback_deadline=_day(5))
    MatterEngagement.objects.filter(pk=engagement.pk).update(feedback_deadline=None)

    line = _text(_wait(_zone(_detail(signed_in, normal_matter))))

    assert line.startswith("Tähtaeg määramata Ootame tagasisidet")


def test_b_an_upcoming_milestone_opens_on_its_date(signed_in, specialist, normal_matter):
    add_important_date(
        matter=normal_matter,
        title="Ministeeriumi vastuse tähtaeg",
        date_value=_day(13),
        period_end=_day(13),
        actor=specialist,
    )

    zone = _zone(_detail(signed_in, normal_matter))
    start = zone.index('<div class="curact__task">')
    task = _text(zone[start : zone.index("</div>", start)])

    assert task == f"{_et(_day(13))} Oluline tähtaeg Ministeeriumi vastuse tähtaeg"


# ---------------------------------------------------------------------------
# D. `+ Lisa`
# ---------------------------------------------------------------------------


def _lisa(body: str) -> str:
    start = body.index('id="lisa-marge"')
    return (
        body[start : body.index("No fifth chip", start)]
        if "No fifth chip" in body
        else body[start:]
    )


def test_d_lisa_offers_exactly_four_choices_and_no_tavaline(signed_in, normal_matter):
    body = _detail(signed_in, normal_matter)

    chips = re.findall(
        r'<label class="disclosure-chip" for="marge-[a-z-]+-valik">([^<]+)</label>', body
    )

    assert chips == ["Arvamuse tähtaeg", "Oluline tähtaeg", "Jõustumine", "Töövõit"]
    assert "Tavaline" not in _text(body[body.index('id="lisa-marge"') :])
    assert "marge-tavaline" not in body


def test_d_arvamuse_tahtaeg_is_the_default_child(signed_in, normal_matter):
    body = _detail(signed_in, normal_matter)

    pick = re.search(r'<input[^>]*id="marge-arvamuse-tahtaeg-valik"[^>]*>', body, re.S).group(0)
    assert "checked" in pick


def test_d_a_refusal_reopens_the_deadline_child(signed_in, normal_matter):
    response = signed_in.post(
        reverse("matters:add_response_deadline", kwargs={"pk": normal_matter.pk}),
        {"response_deadline_date": ""},
        headers=HX,
    )

    body = response.content.decode()
    assert response.status_code == 400
    assert re.search(r'id="lisa-marge-valik"[^>]*checked', body, re.S)
    assert re.search(r'id="marge-arvamuse-tahtaeg-valik"[^>]*checked', body, re.S)
    assert "Sisesta arvamuse tähtaeg." in body


def test_d_a_refused_important_date_still_reopens_its_own_child(signed_in, normal_matter):
    response = signed_in.post(
        reverse("matters:add_important_date", kwargs={"pk": normal_matter.pk}),
        {"deadline_title": ""},
        headers=HX,
    )

    body = response.content.decode()
    assert response.status_code == 400
    assert re.search(r'id="marge-tahtaeg-valik"[^>]*checked', body, re.S)
    assert not re.search(r'id="marge-arvamuse-tahtaeg-valik"[^>]*checked', body, re.S)


# ---------------------------------------------------------------------------
# E. A new `Arvamuse tähtaeg` after an answered one
# ---------------------------------------------------------------------------


@pytest.fixture
def ministry():
    return factories.OrganisationFactory(name="Sünteetiline ministeerium")


def _opinion(client, matter, organisation, **fields):
    from django.core.files.uploadedfile import SimpleUploadedFile

    payload = {
        "upload": SimpleUploadedFile(
            "arvamus.pdf",
            b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF",
            content_type="application/pdf",
        ),
        "recipients": [str(organisation.pk)],
        "sent_on": _et(timezone.localdate()),
        "summary": "Sünteetiline arvamus.",
    }
    payload.update(fields)
    return client.post(
        reverse("matters:add_koda_opinion", kwargs={"pk": matter.pk}), payload, headers=HX
    )


def _request_again(client, matter, day, revision=None):
    matter.refresh_from_db()
    return client.post(
        reverse("matters:add_response_deadline", kwargs={"pk": matter.pk}),
        {
            "response_deadline_date": _et(day),
            "revision": deadline_revision(matter) if revision is None else revision,
        },
        headers=HX,
    )


def test_e_a_second_request_after_an_answered_one(signed_in, specialist, ministry):
    matter = factories.MatterFactory(owner=specialist)
    change_response_deadline(matter=matter, deadline=_day(10), actor=specialist)
    matter.refresh_from_db()
    first_requested_at = matter.response_requested_at
    matter.refresh_from_db()
    assert (
        _opinion(
            signed_in, matter, ministry, vastab_tahtajale=deadline_revision(matter)
        ).status_code
        == 200
    )
    first_opinion = Submission.objects.get(matter=matter, status=SubmissionStatus.SENT)
    (answered,) = MatterResponseDeadline.objects.filter(matter=matter)
    assert answered.outcome == ResponseDeadlineOutcome.ANSWERED
    assert answered.submission_id == first_opinion.pk
    assert answered.deadline == _day(10)

    response = _request_again(signed_in, matter, _day(40))

    assert response.status_code == 200
    matter.refresh_from_db()
    assert matter.response_deadline == _day(40)
    assert matter.response_requested_at is not None
    assert matter.response_requested_at > first_requested_at
    # The earlier request is history, still answered by the earlier opinion.
    (still,) = MatterResponseDeadline.objects.filter(matter=matter)
    assert still.pk == answered.pk and still.submission_id == first_opinion.pk
    # The old opinion does not answer the new request: it is outstanding.
    from app.matters.work_items import response_obligation_of

    assert response_obligation_of(matter, specialist).is_outstanding

    body = _detail(signed_in, matter)
    header = body[: body.index('id="praegune-tegevus"')]
    assert f"Arvamuse tähtaeg {_et(_day(40))}" in _text(header)
    card = body[body.index('id="koja-arvamus"') :]
    card = card[: card.index("</div>\n", card.index("railcard__label"))]
    assert "arvamus.pdf" in card
    assert _et(_day(40)) not in card

    # The next opinion answers the new request, through the existing flow.
    matter.refresh_from_db()
    _opinion(signed_in, matter, ministry, vastab_tahtajale=deadline_revision(matter))
    matter.refresh_from_db()
    assert matter.response_deadline is None
    rows = MatterResponseDeadline.objects.filter(matter=matter).order_by("ended_at")
    assert [row.deadline for row in rows] == [_day(10), _day(40)]
    assert rows[1].submission_id != first_opinion.pk
    assert rows[1].outcome == ResponseDeadlineOutcome.ANSWERED


def test_e_a_current_request_is_never_overwritten(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    change_response_deadline(matter=matter, deadline=_day(10), actor=specialist)

    body = _detail(signed_in, matter)
    panel = body[body.index('id="marge-arvamuse-tahtaeg"') :]
    panel = panel[: panel.index('id="marge-tahtaeg-valik"')]
    assert "data-current-response-deadline" in panel
    assert _et(_day(10)) in _text(panel)
    assert "add_response_deadline" not in panel and "arvamuse-tahtaeg/" not in panel

    response = _request_again(signed_in, matter, _day(30))

    assert response.status_code == 400
    assert "Teemal on juba praegune arvamuse tähtaeg" in response.content.decode()
    matter.refresh_from_db()
    assert matter.response_deadline == _day(10)
    assert not MatterResponseDeadline.objects.filter(matter=matter).exists()


def test_e_a_stale_tab_is_refused_without_writing(signed_in, specialist):
    """A tab drawn while a deadline was current, after it was ended elsewhere."""
    matter = factories.MatterFactory(owner=specialist)
    change_response_deadline(matter=matter, deadline=_day(10), actor=specialist)
    matter.refresh_from_db()
    stale = deadline_revision(matter)
    change_response_deadline(
        matter=matter,
        deadline=None,
        actor=specialist,
        previous_outcome=ResponseDeadlineOutcome.CANCELLED,
    )
    events = ChangeEvent.objects.filter(matter=matter).count()

    response = _request_again(signed_in, matter, _day(30), revision=stale)

    assert response.status_code == 400
    assert "vahepeal muutunud" in response.content.decode()
    matter.refresh_from_db()
    assert matter.response_deadline is None
    assert ChangeEvent.objects.filter(matter=matter).count() == events


def test_e_a_discharged_legacy_deadline_gives_way_to_the_new_request(signed_in, specialist):
    """A pre-tracking deadline (no request time) that a sent opinion discharged
    reads «lõpetatud»; the new request ends it as answered, in words, with no
    opinion linked by inference."""
    from app.documents.enums import DocumentRole
    from app.documents.services import add_evidence_version

    matter = factories.MatterFactory(owner=specialist, response_deadline=_day(-20))
    document = factories.DocumentFactory(matter=matter, role=DocumentRole.KODA_SUBMISSION_FINAL)
    final = add_evidence_version(
        document=document,
        content=b"%PDF-1.4 arvamus",
        original_filename="Koja_arvamus.pdf",
        mime_type="application/pdf",
    )
    factories.SubmissionFactory(
        matter=matter,
        title="Koja arvamus",
        status=SubmissionStatus.SENT,
        sent_at=timezone.now(),
        final_version=final,
    )
    assert Matter.objects.get(pk=matter.pk).response_requested_at is None

    response = _request_again(signed_in, matter, _day(15))

    assert response.status_code == 200
    matter.refresh_from_db()
    assert matter.response_deadline == _day(15)
    assert matter.response_requested_at is not None
    (ended,) = MatterResponseDeadline.objects.filter(matter=matter)
    assert ended.deadline == _day(-20)
    assert ended.outcome == ResponseDeadlineOutcome.ANSWERED
    assert ended.submission_id is None
    assert ended.note == LEGACY_DISCHARGE_NOTE
    assert ended.next_deadline == _day(15)


def test_e_a_closed_matter_takes_no_new_request(signed_in, specialist):
    from app.matters.services import close_matter

    matter = factories.MatterFactory(owner=specialist)
    close_matter(matter=matter, disposition="COMPLETED", actor=specialist)

    response = _request_again(signed_in, matter, _day(15))

    assert response.status_code == 400
    assert Matter.objects.get(pk=matter.pk).response_deadline is None


# ---------------------------------------------------------------------------
# G. Tegevused
# ---------------------------------------------------------------------------


def _step_rows(matter, user):
    rows, _more = matter_timeline(matter=matter, user=user)
    return [row for row in rows if row.next_step is not None and row.entry is None]


def test_g_a_planned_action_reads_as_its_own_words(signed_in, specialist, normal_matter):
    set_next_action_for_new_work(
        matter=normal_matter, text="Loe eelnõu", target_date=_day(2), actor=specialist
    )
    add_planned_action(
        matter=normal_matter,
        text="Koosta vastus ministeeriumile",
        target_date=_day(9),
        actor=specialist,
    )

    rows = _step_rows(normal_matter, specialist)
    planned = [row for row in rows if "Koosta vastus" in row.summary_sentence]

    assert len(planned) == 1
    assert planned[0].summary_sentence.startswith("Koosta vastus ministeeriumile")
    assert planned[0].step_only
    body = _detail(signed_in, normal_matter)
    chronology = body[body.index('id="ajajoon"') :]
    assert "Planeeritud tegevus –" not in chronology
    assert "Koosta vastus ministeeriumile" in chronology


def test_g_jargmine_samm_is_unchanged():
    from app.matters.timeline import _verbs_for

    current = ChangeEvent(
        event_type=ChangeEventType.NEXT_ACTION_SET, summary="Loe eelnõu", payload={}
    )
    planned = ChangeEvent(
        event_type=ChangeEventType.NEXT_ACTION_SET,
        summary="Koosta vastus",
        payload={"planned": True},
    )

    assert _verbs_for(None, [current]) == ("Järgmine samm – Loe eelnõu",)
    assert _verbs_for(None, [planned]) == ("Koosta vastus",)


def test_g_a_step_with_no_words_is_not_a_step_line(specialist, normal_matter):
    """The bare clause is not a step line, prefix or no prefix."""
    from app.matters.timeline import STEP_SET_CLAUSE, TimelineItem

    event = ChangeEvent(event_type=ChangeEventType.NEXT_ACTION_SET, payload={})
    item = TimelineItem(
        occurred_at=timezone.now(),
        created_at=timezone.now(),
        sort_key="x",
        item_type="event",
        event=event,
        summary_verbs=(STEP_SET_CLAUSE,),
        next_step=object(),  # type: ignore[arg-type]
    )

    assert not item.step_only
