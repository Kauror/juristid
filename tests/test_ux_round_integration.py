"""Cross-feature integration of the 2026-10 UX round (#453–#456), through the HTTP routes.

Each stage has its own tests (tests/test_kaasamine_calendar_opinion_deadline.py,
tests/test_lisa_muu.py, tests/test_lisa_tagasiside_unified.py,
tests/test_kohtumine.py). These put the stages **together on one Teema**, in
the order a lawyer meets them, and check what only the combination can show:

1. a normal legislative Matter — Uus teema, `+ Lisa · Arvamuse tähtaeg` (marked
   in `Alusta kaasamist`'s calendar, stage 1), a consultation with a feedback
   deadline, a linked reply (ends the wait), another body's published opinion
   (touches nothing), `Muu` with a file and a link, a meeting with files, and a
   Koja arvamus that answers the deadline — then the chronology, `Dokumendid`,
   search, deadlines and the current work read together;
2. two lawyers on one Teema — a stale `Muu` link correction is a 409 that
   keeps the loser's values, and a reply saved after a colleague finished the
   round is a later answer (closure kept, repeat refused, another author
   accepted);
3. restricted information — a restricted round's reply and file stay hidden
   from a reader on every surface while the Teema's ordinary `Muu` and its file
   stay readable;
4. failure and recovery — each refused save writes nothing at all (no row, no
   document, no organisation, no evidence object), keeps what was typed, and the
   corrected save succeeds;
5. historical compatibility — LEGACY / stored positions and old entries render
   with their labels and are corrected through the existing routes without
   moving provenance, dates, author or evidence; migration 0049's database
   default keeps an insert that omits `url` working.

Synthetic data only. The browser half is e2e/test_ux_round_integration.py.
"""

from __future__ import annotations

import re
import uuid
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.core.dates import format_estonian_date, start_of_local_day
from app.core.enums import Visibility
from app.documents.links import DocumentLink
from app.documents.models import Document, DocumentVersion
from app.matters.engagement_replies import REPLY_ALREADY_RECORDED
from app.matters.entry_enums import EntryKind
from app.matters.enums import (
    EngagementKind,
    ExternalPositionProvenance,
    ResponseDeadlineOutcome,
)
from app.matters.models import (
    EXTERNAL_POSITION_LEGACY_HEADLINE,
    Entry,
    EntryRevision,
    Matter,
    MatterEngagement,
    MatterExternalPosition,
    MatterResponseDeadline,
    MatterStageEpisode,
)
from app.matters.services import (
    EXTERNAL_POSITION_NEEDS_ORGANISATION,
    add_entry,
    engagement_revision_token,
)
from app.matters.timeline import matter_timeline
from app.matters.workspace import add_matter_external_position
from app.organisations.models import Organisation
from app.search.services import result_count
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.workflow.models import MatterPlanStep, NextAction, OpinionFollowUp
from tests import factories
from tests.follow_ups import PDF

pytestmark = pytest.mark.django_db

HX = {"HX-Request": "true"}
RECEIVED = ExternalPositionProvenance.RECEIVED.value
DISCOVERED = ExternalPositionProvenance.DISCOVERED.value
LEGACY = ExternalPositionProvenance.LEGACY.value


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _day(offset: int):
    return timezone.localdate() + timedelta(days=offset)


def _et(day) -> str:
    """The way a person types a day into the Estonian date boxes."""
    return f"{day.day}.{day.month}.{day.year}"


def _pdf(name: str, body: bytes = b"") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, PDF + name.encode() + body, content_type="application/pdf")


def _html_file(name: str = "leht.html") -> SimpleUploadedFile:
    return SimpleUploadedFile(
        name, b"<!doctype html><html><body>ei ole lubatud</body></html>", content_type="text/html"
    )


def _client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _url(name: str, matter, **kwargs) -> str:
    return reverse(f"matters:{name}", kwargs={"pk": matter.pk, **kwargs})


def _get(client, name: str, matter, **kwargs) -> str:
    response = client.get(_url(name, matter, **kwargs))
    assert response.status_code == 200, (name, response.status_code)
    return response.content.decode()


def _detail(client, matter) -> str:
    return _get(client, "matter_detail", matter)


def _post(client, name: str, matter, data, *, files=None, **kwargs):
    payload = dict(data)
    for key, value in (files or {}).items():
        payload[key] = value
    return client.post(_url(name, matter, **kwargs), payload, headers=HX)


def _text(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


class _FormFields(HTMLParser):
    """What a browser would submit from one drawn form, file inputs aside."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fields: list[tuple[str, str]] = []
        self._textarea: tuple[str, list[str]] | None = None
        self._select: str | None = None
        self._select_first: str | None = None
        self._select_chosen = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        name = attributes.get("name")
        if tag == "input" and name and "disabled" not in attributes:
            kind = (attributes.get("type") or "text").lower()
            if kind in ("file", "submit", "button", "reset"):
                return
            if kind in ("checkbox", "radio"):
                if "checked" in attributes:
                    self.fields.append((name, attributes.get("value") or "on"))
                return
            self.fields.append((name, attributes.get("value") or ""))
        elif tag == "textarea" and name and "disabled" not in attributes:
            self._textarea = (name, [])
        elif tag == "select" and name and "disabled" not in attributes:
            self._select, self._select_first, self._select_chosen = name, None, False
        elif tag == "option" and self._select is not None:
            value = attributes.get("value") or ""
            if self._select_first is None:
                self._select_first = value
            if "selected" in attributes:
                self.fields.append((self._select, value))
                self._select_chosen = True

    def handle_data(self, data):
        if self._textarea is not None:
            self._textarea[1].append(data)

    def handle_endtag(self, tag):
        if tag == "textarea" and self._textarea is not None:
            self.fields.append((self._textarea[0], "".join(self._textarea[1])))
            self._textarea = None
        elif tag == "select" and self._select is not None:
            if not self._select_chosen and self._select_first is not None:
                self.fields.append((self._select, self._select_first))
            self._select = None


def _drawn_form(html: str, action: str) -> dict[str, list[str]]:
    """The fields of the form posting to ``action``, as the page drew them."""
    match = re.search(rf'(?:hx-post|action)="{re.escape(action)}"', html)
    assert match, f"no form posting to {action}"
    start = html.rindex("<form", 0, match.start())
    parser = _FormFields()
    parser.feed(html[start : html.index("</form>", start)])
    values: dict[str, list[str]] = {}
    for name, value in parser.fields:
        values.setdefault(name, []).append(value)
    return values


def _feedback_box(html: str) -> str:
    match = re.search(r'<input[^>]*name="feedback_deadline"[^>]*>', html)
    assert match, "the Tagasisidet ootame kuni box is on the page"
    return match.group(0)


def _reply_form(html: str) -> str:
    start = html.index('id="kaasamine-tagasiside"')
    return html[start : html.index("</form>", start)]


def _workflow(matter) -> dict[str, object]:
    """Every piece of current-work state a capture could move, read fresh."""
    matter = Matter.objects.get(pk=matter.pk)
    return {
        "stage": matter.stage_id,
        "is_open": matter.is_open,
        "disposition": matter.disposition,
        "response_deadline": matter.response_deadline,
        "response_requested_at": matter.response_requested_at,
        "stage_episodes": MatterStageEpisode.objects.filter(matter=matter).count(),
        "deadline_rows": list(
            MatterResponseDeadline.objects.filter(matter=matter).values_list("pk", "outcome")
        ),
        "actions": sorted(
            NextAction.objects.filter(matter=matter).values_list(
                "pk", "status", "text", "target_date"
            )
        ),
        "plan": sorted(
            MatterPlanStep.objects.filter(matter=matter).values_list("pk", "state", "completed_at")
        ),
        "rounds": sorted(
            MatterEngagement.objects.filter(matter=matter).values_list(
                "pk", "feedback_closed_at", "feedback_deadline"
            )
        ),
        "follow_ups": OpinionFollowUp.objects.filter(submission__matter=matter).count(),
        "submissions": Submission.objects.filter(matter=matter).count(),
    }


def _evidence_files() -> set[str]:
    """Every object in the evidence store, as POSIX paths (Windows-safe)."""
    root = Path(settings.EVIDENCE_ROOT)
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


def _referenced_keys() -> set[str]:
    return {
        Path(key).as_posix()
        for key in DocumentVersion.objects.values_list("storage_key", flat=True)
    }


def _timeline_row(matter, user, predicate):
    items, _ = matter_timeline(matter=matter, user=user, limit=200)
    matches = [(index, item) for index, item in enumerate(items) if predicate(item)]
    assert len(matches) == 1, [item.item_type for item in items]
    return matches[0]


def _is_record(record):
    return lambda item: item.record is not None and item.record.pk == record.pk


def _is_entry(entry):
    return lambda item: item.entry is not None and item.entry.pk == entry.pk


# ---------------------------------------------------------------------------
# Scenario 1 — a normal legislative Matter, end to end
# ---------------------------------------------------------------------------

TITLE = "UXINT eelnõu sünteetiline integratsioon"
ROUND_AUDIENCE = "Liikmed UXINTRINGKUTSE"
REPLY_TEXT = "UXINTLIIKMEVASTUS liikmed toetavad eelnõud."
PUBLISHED_TEXT = "UXINTAVALIKSEISUKOHT teine liit on eelnõu vastu."
MUU_TEXT = "UXINTTAUSTMATERJAL ministeeriumi analüüs."
MUU_URL = "https://www.example.ee/uxint/taust"
MEETING_TEXT = "UXINTKOHTUMINE arutasime ministeeriumiga ajakava."
OPINION_TEXT = "UXINTKOJAARVAMUS Koda toetab muudatustega."


def test_scenario_1_a_normal_legislative_matter_end_to_end(specialist):
    lawyer = _client(specialist)
    member = factories.OrganisationFactory(name="Sünteetiline Liikmesliit UXINT")
    other_body = factories.OrganisationFactory(name="Sünteetiline Teine Liit UXINT")
    ministry = factories.OrganisationFactory(name="Sünteetiline Ministeerium UXINT")

    # -- Uus teema -----------------------------------------------------------
    created = lawyer.post(reverse("matters:matter_create"), {"title": TITLE})
    assert created.status_code == 302
    matter = Matter.objects.get(title=TITLE)
    assert matter.response_deadline is None

    # -- `+ Lisa · Arvamuse tähtaeg` -----------------------------------------
    opinion_due = _day(30)
    response = _post(
        lawyer, "add_response_deadline", matter, {"response_deadline_date": _et(opinion_due)}
    )
    assert response.status_code == 200
    matter.refresh_from_db()
    assert matter.response_deadline == opinion_due
    assert matter.response_requested_at is not None

    # -- stage 1: the feedback calendar marks it (information only) ---------
    box = _feedback_box(_detail(lawyer, matter))
    assert f'data-datepicker-mark="{format_estonian_date(opinion_due)}"' in box
    assert 'data-datepicker-mark-label="Arvamuse tähtaeg"' in box
    assert 'value=""' in box

    # -- `+ Kaasamine · Alusta kaasamist`, with a feedback deadline ------------
    round_day, feedback_due = _day(-20), _day(10)
    response = _post(
        lawyer,
        "add_engagement_compact",
        matter,
        {
            "audience": ROUND_AUDIENCE,
            "occurred_on": _et(round_day),
            "feedback_deadline": _et(feedback_due),
        },
        files={"attachments": [_pdf("uxint-kutse.pdf")]},
    )
    assert response.status_code == 200
    round_ = MatterEngagement.objects.get(matter=matter)
    assert round_.has_open_feedback_wait
    assert round_.feedback_deadline == feedback_due
    assert round_.occurred_on == round_day
    # The wait is in current work, and the Arvamuse tähtaeg is still current.
    page = _detail(lawyer, matter)
    waits = page[page.index('id="praegune-ootused"') :]
    assert f'data-feedback-wait="{round_.pk}"' in waits[: waits.index("</div>")]

    # -- `Lisa tagasiside`, linked to the round: the wait ends ----------------
    form = _reply_form(page)
    assert f'<option value="{round_.pk}"' in form
    assert not re.search(rf'<option value="{round_.pk}"[^>]*selected', form)
    reply_day = _day(-15)
    response = _post(
        lawyer,
        "add_engagement_reply",
        matter,
        {
            "organisation": str(member.pk),
            "engagement": str(round_.pk),
            "provenance": RECEIVED,
            "summary": REPLY_TEXT,
            "stated_on": _et(reply_day),
        },
        files={"attachments": [_pdf("uxint-liikme-vastus.pdf")]},
    )
    assert response.status_code == 200
    reply = MatterExternalPosition.objects.get(matter=matter, engagement=round_)
    assert reply.provenance == RECEIVED and reply.stated_on == reply_day
    round_.refresh_from_db()
    assert not round_.has_open_feedback_wait
    assert round_.feedback_closed_by_id == specialist.pk
    # The Arvamuse tähtaeg is not answered by a member's reply.
    matter.refresh_from_db()
    assert matter.response_deadline == opinion_due

    before = _workflow(matter)

    # -- another body's independent published opinion: nothing moves ----------
    published_day = _day(-12)
    response = _post(
        lawyer,
        "add_engagement_reply",
        matter,
        {
            "organisation": str(other_body.pk),
            "provenance": DISCOVERED,
            "summary": PUBLISHED_TEXT,
            "url": "https://example.org/uxint/avalik",
            "stated_on": _et(published_day),
        },
        files={"attachments": [_pdf("uxint-teise-liidu-seisukoht.pdf")]},
    )
    assert response.status_code == 200
    published = MatterExternalPosition.objects.get(matter=matter, provenance=DISCOVERED)
    assert published.engagement_id is None
    assert _workflow(matter) == before

    # -- `+ Lisa · Muu` with a file and a link --------------------------------
    muu_day = _day(-10)
    response = _post(
        lawyer,
        "add_other",
        matter,
        {"description": MUU_TEXT, "url": MUU_URL, "occurred_on": _et(muu_day)},
        files={"attachments": [_pdf("uxint-taust.pdf")]},
    )
    assert response.status_code == 200
    muu = Entry.objects.get(matter=matter, kind=EntryKind.OTHER)
    assert muu.url == MUU_URL
    assert muu.occurred_at == start_of_local_day(muu_day)
    assert _workflow(matter) == before

    # -- `Kohtumine / arutelu` with notes and files ---------------------------
    meeting_day = _day(-7)
    response = _post(
        lawyer,
        "add_meeting",
        matter,
        {"summary": MEETING_TEXT, "occurred_on": _et(meeting_day)},
        files={"attachments": [_pdf("uxint-memo.pdf"), _pdf("uxint-esitlus.pdf")]},
    )
    assert response.status_code == 200
    meeting = Entry.objects.get(matter=matter, kind=EntryKind.MEETING)
    assert meeting.occurred_at == start_of_local_day(meeting_day)
    assert _workflow(matter) == before

    # -- `+ Koja arvamus`, answering the Arvamuse tähtaeg ---------------------
    page = _detail(lawyer, matter)
    answers = re.search(r'<input[^>]*name="vastab_tahtajale"[^>]*value="([^"]+)"', page)
    assert answers, "the opinion form offers to answer the current Arvamuse tähtaeg"
    # The finished round is not offered as a wait to finish.
    assert f'value="{round_.pk}:' not in page
    once = re.search(r'<input type="hidden" name="salvestus" value="([^"]*)"', page)
    opinion_day = _day(-2)
    response = _post(
        lawyer,
        "add_koda_opinion",
        matter,
        {
            "recipients": [str(ministry.pk)],
            "sent_on": _et(opinion_day),
            "summary": OPINION_TEXT,
            "vastab_tahtajale": answers.group(1),
            **({"salvestus": once.group(1)} if once else {}),
        },
        files={"upload": _pdf("uxint-koja-arvamus.pdf")},
    )
    assert response.status_code == 200, _text(response.content.decode())[:600]
    opinion = Submission.objects.get(matter=matter, status=SubmissionStatus.SENT)

    # -- deadlines -------------------------------------------------------------
    matter.refresh_from_db()
    assert matter.response_deadline is None
    (answered,) = MatterResponseDeadline.objects.filter(matter=matter)
    assert answered.deadline == opinion_due
    assert answered.outcome == ResponseDeadlineOutcome.ANSWERED
    assert answered.submission_id == opinion.pk
    # The round's own feedback deadline is history, its closure untouched.
    round_.refresh_from_db()
    assert round_.feedback_deadline == feedback_due
    assert round_.feedback_closed_by_id == specialist.pk

    # -- the chronology: every record, on its own day, newest first ----------
    rows = {
        "opinion": _timeline_row(
            matter, specialist, lambda item: getattr(item.record, "pk", None) == opinion.pk
        ),
        "meeting": _timeline_row(matter, specialist, _is_entry(meeting)),
        "muu": _timeline_row(matter, specialist, _is_entry(muu)),
        "published": _timeline_row(matter, specialist, _is_record(published)),
        "reply": _timeline_row(matter, specialist, _is_record(reply)),
        "round": _timeline_row(matter, specialist, _is_record(round_)),
    }
    expected_days = {
        "opinion": opinion_day,
        "meeting": meeting_day,
        "muu": muu_day,
        "published": published_day,
        "reply": reply_day,
        "round": round_day,
    }
    for key, (_, item) in rows.items():
        assert timezone.localtime(item.occurred_at).date() == expected_days[key], key
    order = [key for key, _ in sorted(rows.items(), key=lambda pair: pair[1][0])]
    assert order == ["opinion", "meeting", "muu", "published", "reply", "round"], order

    # The timeline page draws them in that order, each with its label and day.
    timeline = _get(lawyer, "timeline_page", matter)
    positions = [
        timeline.index(text)
        for text in (OPINION_TEXT, MEETING_TEXT, MUU_TEXT, PUBLISHED_TEXT, REPLY_TEXT)
    ]
    assert positions == sorted(positions), positions
    for entry, label, day in ((muu, "Muu", muu_day), (meeting, "Kohtumine", meeting_day)):
        row = timeline[timeline.index(f'id="sissekanne-{entry.pk}"') :]
        row = row[: row.index("</article>")]
        assert f">{label}</span>" in row
        assert f">{_et(day)}</time>" in row
    assert 'href="https://www.example.ee/uxint/taust"' in timeline
    text = _text(timeline)
    assert "Meile saadetud tagasiside" in text and "Teiste arvamus" in text

    # -- Dokumendid lists every file --------------------------------------------
    documents = _get(lawyer, "matter_documents", matter)
    for name in (
        "uxint-kutse.pdf",
        "uxint-liikme-vastus.pdf",
        "uxint-teise-liidu-seisukoht.pdf",
        "uxint-taust.pdf",
        "uxint-memo.pdf",
        "uxint-esitlus.pdf",
        "uxint-koja-arvamus.pdf",
    ):
        assert name in documents, name
    assert Document.objects.filter(matter=matter).count() == 7

    # -- search finds each record's own words for an authorized lawyer ------
    for token in (
        "UXINTRINGKUTSE",
        "UXINTLIIKMEVASTUS",
        "UXINTAVALIKSEISUKOHT",
        "UXINTTAUSTMATERJAL",
        "UXINTKOHTUMINE",
        "UXINTKOJAARVAMUS",
    ):
        assert result_count(query=token, user=specialist) >= 1, token

    # -- the current work: Muu, the meeting and the published view moved nothing
    # (each save above was compared with `before`); the opinion, saved with no
    # `Uus hetkeseis`, left the Hetkeseis where it was.
    after = _workflow(matter)
    assert after["stage"] == before["stage"]
    assert after["stage_episodes"] == before["stage_episodes"]
    assert after["rounds"] == before["rounds"]


# ---------------------------------------------------------------------------
# Scenario 2 — two lawyers on one Teema
# ---------------------------------------------------------------------------


def test_scenario_2a_a_stale_muu_link_correction_is_a_conflict_that_keeps_both_sides(
    specialist, other_specialist
):
    a, b = _client(specialist), _client(other_specialist)
    matter = factories.MatterFactory(owner=specialist)
    assert (
        _post(
            a,
            "add_other",
            matter,
            {
                "description": "Ühine taustamaterjal.",
                "url": "https://example.ee/algne",
                "occurred_on": _et(_day(-3)),
            },
        ).status_code
        == 200
    )
    entry = Entry.objects.get(matter=matter, kind=EntryKind.OTHER)
    edit = _url("edit_entry", matter, entry_id=entry.pk)

    # Both open `Muuda` on the same version.
    b_form = _drawn_form(_get(b, "edit_entry", matter, entry_id=entry.pk), edit)
    a_form = _drawn_form(_get(a, "edit_entry", matter, entry_id=entry.pk), edit)
    assert a_form["revision"] == b_form["revision"]
    assert a_form["url"] == ["https://example.ee/algne"]

    # A corrects the link and saves first.
    a_form["url"] = ["https://example.ee/a-parandus"]
    assert a.post(edit, a_form, headers=HX).status_code == 200

    # B saves the outdated form with their own wording and link.
    b_form["url"] = ["https://example.ee/b-parandus"]
    b_form["body"] = ["B sõnastus, mis jääb salvestamata."]
    response = b.post(edit, b_form, headers=HX)

    assert response.status_code == 409
    body = response.content.decode()
    # B's typed values are still in the boxes, the token is the one B opened with.
    assert "B sõnastus, mis jääb salvestamata." in body
    url_box = re.search(rf'<input[^>]*id="id_sissekanne_{entry.pk}_url"[^>]*>', body).group(0)
    assert 'value="https://example.ee/b-parandus"' in url_box
    assert f'value="{b_form["revision"][0]}"' in body
    # A's version is shown to compare, as text.
    assert "data-conflict-url>https://example.ee/a-parandus</p>" in body
    # The database holds A's value; the history holds the original, by A.
    entry.refresh_from_db()
    assert entry.url == "https://example.ee/a-parandus"
    assert "Ühine taustamaterjal." in entry.body
    assert entry.edit_count == 1
    (revision,) = EntryRevision.objects.filter(entry=entry)
    assert revision.url == "https://example.ee/algne"
    assert revision.edited_by == specialist


def test_scenario_2b_a_reply_saved_after_a_colleague_finished_the_round_is_a_later_answer(
    specialist, other_specialist
):
    a, b = _client(specialist), _client(other_specialist)
    member = factories.OrganisationFactory(name="Sünteetiline Liige UXINT-2")
    another = factories.OrganisationFactory(name="Sünteetiline Teine Liige UXINT-2")
    matter = factories.MatterFactory(owner=specialist)
    assert (
        _post(
            a,
            "add_engagement_compact",
            matter,
            {
                "audience": "Liikmed UXINT-2",
                "occurred_on": _et(_day(-5)),
                "feedback_deadline": _et(_day(5)),
            },
        ).status_code
        == 200
    )
    round_ = MatterEngagement.objects.get(matter=matter)
    assert round_.has_open_feedback_wait

    # B opens `Lisa tagasiside` while the round is open: it is offered.
    form = _reply_form(_detail(b, matter))
    assert f'<option value="{round_.pk}"' in form

    # A finishes the round through `Lõpeta kaasamine`.
    finished = a.post(
        _url("complete_engagement_feedback", matter, engagement_id=round_.pk),
        {"revision": engagement_revision_token(round_)},
        headers=HX,
    )
    assert finished.status_code == 200
    round_.refresh_from_db()
    closed_at, closed_by = round_.feedback_closed_at, round_.feedback_closed_by_id
    assert closed_at is not None and closed_by == specialist.pk

    # B saves the linked reply from the form drawn before.
    reply = {
        "organisation": str(member.pk),
        "engagement": str(round_.pk),
        "provenance": RECEIVED,
        "summary": "Toetame, kuid palume üleminekuaega.",
        "stated_on": _et(_day(-1)),
    }
    assert _post(b, "add_engagement_reply", matter, reply).status_code == 200

    (position,) = MatterExternalPosition.objects.filter(matter=matter)
    assert position.engagement_id == round_.pk
    assert position.provenance == RECEIVED
    assert position.created_by == other_specialist
    round_.refresh_from_db()
    # Recorded as a later answer: the closure — when and by whom — stays.
    assert (round_.feedback_closed_at, round_.feedback_closed_by_id) == (closed_at, closed_by)

    # An identical repeat is refused and writes nothing.
    repeat = _post(b, "add_engagement_reply", matter, reply)
    assert repeat.status_code == 400
    assert REPLY_ALREADY_RECORDED in repeat.content.decode()
    assert MatterExternalPosition.objects.filter(engagement=round_).count() == 1

    # The same words from another organisation are another answer.
    other = _post(b, "add_engagement_reply", matter, {**reply, "organisation": str(another.pk)})
    assert other.status_code == 200
    assert MatterExternalPosition.objects.filter(engagement=round_).count() == 2
    round_.refresh_from_db()
    assert (round_.feedback_closed_at, round_.feedback_closed_by_id) == (closed_at, closed_by)


# ---------------------------------------------------------------------------
# Scenario 3 — restricted information on an unrestricted Teema
# ---------------------------------------------------------------------------

S3_ROUND = "UXINTPIIRATUDRING"
S3_REPLY = "UXINTPIIRATUDVASTUS"
S3_REPLY_FILE = "uxint-piiratud-vastus.pdf"
S3_MUU = "UXINTAVALIKMUU"
S3_MUU_FILE = "uxint-avalik-muu.pdf"


@pytest.fixture
def restricted_world(db):
    owner = factories.UserFactory(display_name="Omanik UXINT")
    people = {
        "owner": owner,
        "head": factories.DepartmentHeadFactory(),
        "reader": factories.ReaderFactory(),
        "administrator": factories.AdministratorFactory(),
    }
    matter = factories.MatterFactory(owner=owner, visibility=Visibility.NORMAL)
    round_ = MatterEngagement.objects.create(
        matter=matter,
        kind=EngagementKind.EMAIL_CAMPAIGN,
        title=S3_ROUND,
        created_by=owner,
        lifecycle_tracked=True,
        visibility_override=Visibility.RESTRICTED,
    )
    lawyer = _client(owner)
    response = _post(
        lawyer,
        "add_engagement_reply",
        matter,
        {"engagement": str(round_.pk), "provenance": RECEIVED, "summary": S3_REPLY},
        files={"attachments": [_pdf(S3_REPLY_FILE)]},
    )
    assert response.status_code == 200
    response = _post(
        lawyer,
        "add_other",
        matter,
        {"description": S3_MUU, "occurred_on": _et(_day(-1))},
        files={"attachments": [_pdf(S3_MUU_FILE)]},
    )
    assert response.status_code == 200
    reply = MatterExternalPosition.objects.get(matter=matter)
    reply_version = DocumentVersion.objects.get(document__links__external_position=reply)
    muu = Entry.objects.get(matter=matter, kind=EntryKind.OTHER)
    muu_version = DocumentVersion.objects.get(document__links__entry=muu)
    return {
        **people,
        "matter": matter,
        "round": round_,
        "reply": reply,
        "reply_version": reply_version,
        "muu": muu,
        "muu_version": muu_version,
    }


def _surfaces(matter) -> dict[str, str]:
    return {
        name: _url(name, matter)
        for name in ("matter_detail", "timeline_page", "matter_documents", "matter_changes")
    }


def _file_routes(version) -> dict[str, str]:
    return {
        "download": reverse("documents:download", kwargs={"pk": version.pk}),
        "open": reverse("documents:open", kwargs={"pk": version.pk}),
        "detail": reverse("documents:document_detail", kwargs={"pk": version.document_id}),
    }


def test_scenario_3_the_stored_shape(restricted_world):
    reply = restricted_world["reply"]
    assert reply.visibility_override == Visibility.RESTRICTED
    assert restricted_world["reply_version"].document.visibility_override == Visibility.RESTRICTED
    assert restricted_world["muu"].visibility_override in ("", None)
    assert restricted_world["muu_version"].document.visibility_override in ("", None)


def test_scenario_3_an_outsider_sees_neither_the_muu_nor_the_restricted_round(restricted_world):
    """Was asked of a READER and an administrator. Since docs/adr/0181 the READER
    sees everything (below); an administrator without a grant sees nothing."""
    world = restricted_world
    client = _client(world["administrator"])

    for name, url in _surfaces(world["matter"]).items():
        response = client.get(url)
        assert response.status_code == 404, name
        body = response.content.decode()
        for hidden in (S3_ROUND, S3_REPLY, S3_REPLY_FILE, S3_MUU, S3_MUU_FILE):
            assert hidden not in body, (name, hidden)

    for version in (world["reply_version"], world["muu_version"]):
        for name, url in _file_routes(version).items():
            assert client.get(url).status_code == 404, name

    for hidden in (S3_ROUND, S3_REPLY, "uxint-piiratud-vastus", S3_MUU):
        assert result_count(query=hidden, user=world["administrator"]) == 0, hidden


@pytest.mark.parametrize("persona", ["owner", "head", "reader"])
def test_scenario_3_every_business_reader_sees_everything(restricted_world, persona):
    """The READER joined the owner and the head in docs/adr/0181."""
    world = restricted_world
    user = world[persona]
    client = _client(user)

    detail = client.get(_url("matter_detail", world["matter"])).content.decode()
    assert S3_REPLY in detail and S3_MUU in detail
    timeline = client.get(_url("timeline_page", world["matter"])).content.decode()
    assert S3_REPLY in timeline and S3_ROUND in timeline
    documents = client.get(_url("matter_documents", world["matter"])).content.decode()
    assert S3_REPLY_FILE in documents and S3_MUU_FILE in documents
    for version in (world["reply_version"], world["muu_version"]):
        assert client.get(_file_routes(version)["download"]).status_code == 200
    for token in (S3_REPLY, S3_MUU):
        assert result_count(query=token, user=user) >= 1, token


# ---------------------------------------------------------------------------
# Scenario 4 — failure and recovery
# ---------------------------------------------------------------------------


def _nothing_written(matter, *, organisations: int, evidence: set[str], state) -> None:
    assert not Entry.objects.filter(matter=matter).exists()
    assert not MatterExternalPosition.objects.filter(matter=matter).exists()
    assert not Document.objects.filter(matter=matter).exists()
    assert Organisation.objects.count() == organisations
    assert _evidence_files() == evidence
    assert _evidence_files() <= _referenced_keys()
    assert _workflow(matter) == state


@pytest.fixture
def failing_world(specialist):
    matter = factories.MatterFactory(owner=specialist, response_deadline=_day(20))
    lawyer = _client(specialist)
    _post(
        lawyer,
        "add_engagement_compact",
        matter,
        {
            "audience": "Liikmed UXINT-4",
            "occurred_on": _et(_day(-2)),
            "feedback_deadline": _et(_day(7)),
        },
    )
    round_ = MatterEngagement.objects.get(matter=matter)
    assert round_.has_open_feedback_wait
    factories.NextActionFactory(matter=matter)
    return {"matter": matter, "lawyer": lawyer, "round": round_}


@pytest.mark.parametrize(
    ("route", "data", "files", "typed", "error"),
    [
        pytest.param(
            "add_engagement_reply",
            {"provenance": DISCOVERED, "summary": "Avaldatud, kuid kelle oma?"},
            None,
            ["Avaldatud, kuid kelle oma?"],
            EXTERNAL_POSITION_NEEDS_ORGANISATION,
            id="feedback-discovered-without-organisation",
        ),
        pytest.param(
            "add_other",
            {
                "description": "Paroolilinkiga taust.",
                "url": "https://kasutaja:parool@example.ee/taust",
                "occurred_on": "1.10.2026",
            },
            None,
            ["Paroolilinkiga taust.", 'value="https://kasutaja:parool@example.ee/taust"'],
            "Link ei tohi sisaldada kasutajanime ega parooli.",
            id="muu-url-with-credentials",
        ),
        pytest.param(
            "add_other",
            {
                "description": "Skriptilink taust.",
                "url": "javascript:alert(1)",
                "occurred_on": "1.10.2026",
            },
            None,
            ["Skriptilink taust."],
            "http:// või https://",
            id="muu-url-javascript",
        ),
        pytest.param(
            "add_other",
            {"description": "HTML-failiga taust.", "occurred_on": "1.10.2026"},
            "html",
            ["HTML-failiga taust.", "1.10.2026"],
            None,
            id="muu-html-file",
        ),
        pytest.param(
            "add_meeting",
            {"summary": "HTML-failiga kohtumine.", "occurred_on": "2.10.2026"},
            "html",
            ["HTML-failiga kohtumine.", "2.10.2026"],
            None,
            id="meeting-html-file",
        ),
        pytest.param(
            "add_engagement_reply",
            {
                "organisation_name": "Täiesti Uus Sünteetiline Liit UXINT",
                "provenance": RECEIVED,
                "summary": "HTML-failiga tagasiside.",
            },
            "html",
            ["HTML-failiga tagasiside.", 'value="Täiesti Uus Sünteetiline Liit UXINT"'],
            None,
            id="feedback-html-file-with-a-new-organisation",
        ),
    ],
)
def test_scenario_4_a_refused_save_writes_nothing_and_a_corrected_one_succeeds(
    failing_world, route, data, files, typed, error
):
    matter, lawyer = failing_world["matter"], failing_world["lawyer"]
    organisations = Organisation.objects.count()
    evidence = _evidence_files()
    state = _workflow(matter)
    uploads = {"attachments": [_pdf("hea-uxint.pdf"), _html_file()]} if files else None

    response = _post(lawyer, route, matter, data, files=uploads)

    assert response.status_code == 400
    body = response.content.decode()
    for value in typed:
        assert value in body, value
    if error:
        assert error in body
    _nothing_written(matter, organisations=organisations, evidence=evidence, state=state)

    # The corrected resubmission succeeds.
    fixed = dict(data)
    if route == "add_engagement_reply" and fixed["provenance"] == DISCOVERED:
        fixed["organisation_name"] = "Parandatud Sünteetiline Liit UXINT"
    if "url" in fixed:
        fixed["url"] = "https://example.ee/parandatud"
    response = _post(lawyer, route, matter, fixed, files={"attachments": [_pdf("hea-uxint.pdf")]})
    assert response.status_code == 200, _text(response.content.decode())[:600]
    assert Document.objects.filter(matter=matter).count() == 1
    assert _evidence_files() <= _referenced_keys()
    # Still no task, wait or deadline moved by the save itself.
    after = _workflow(matter)
    assert after["actions"] == state["actions"]
    assert after["rounds"] == state["rounds"]
    assert after["response_deadline"] == state["response_deadline"]


# ---------------------------------------------------------------------------
# Scenario 5 — historical compatibility
# ---------------------------------------------------------------------------


@pytest.fixture
def historical_world(specialist, other_specialist):
    """Records shaped as the file holds them from before this round."""
    matter = factories.MatterFactory(owner=specialist)
    body = factories.OrganisationFactory(name="Ajalooline Sünteetiline Liit UXINT")
    round_ = MatterEngagement.objects.create(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="Ajalooline ring UXINT",
        created_by=other_specialist,
        lifecycle_tracked=True,
    )
    legacy = add_matter_external_position(
        matter=matter,
        author=other_specialist,
        organisation=body,
        provenance=RECEIVED,
        summary="UXINTLEGACY vana seisukoht.",
        stated_on=_day(-400),
        engagement=round_,
        uploads=[_pdf("uxint-legacy.pdf")],
    ).record
    MatterExternalPosition.objects.filter(pk=legacy.pk).update(provenance=LEGACY)
    discovered = add_matter_external_position(
        matter=matter,
        author=other_specialist,
        organisation=body,
        provenance=DISCOVERED,
        summary="UXINTDISCOVERED avaldatud seisukoht.",
        stated_on=_day(-300),
        uploads=[_pdf("uxint-discovered.pdf")],
    ).record
    received = add_matter_external_position(
        matter=matter,
        author=other_specialist,
        organisation=body,
        provenance=RECEIVED,
        summary="UXINTRECEIVED saadetud tagasiside.",
        stated_on=_day(-200),
    ).record
    note = add_entry(
        matter=matter,
        body="UXINTNOTE vana märkus.",
        author=other_specialist,
        occurred_at=start_of_local_day(_day(-350)),
    )
    other = Entry.objects.create(
        matter=matter,
        kind=EntryKind.OTHER,
        body="UXINTOTHER vana muu kirje.",
        author=other_specialist,
        occurred_at=start_of_local_day(_day(-250)),
    )
    for record in (legacy, discovered, received, note, other):
        record.refresh_from_db()
    return {
        "matter": matter,
        "round": round_,
        "legacy": legacy,
        "discovered": discovered,
        "received": received,
        "note": note,
        "other": other,
    }


def _position_row(html: str, position) -> str:
    marker = html.find(f'id="valine-seisukoht-{position.pk}')
    assert marker >= 0, f"no row for {position.pk}"
    row = html[html.rindex("<article", 0, marker) :]
    return row[: row.index("</article>")]


def test_scenario_5_historical_records_render_with_their_labels(signed_in, historical_world):
    world = historical_world
    for page in (
        _detail(signed_in, world["matter"]),
        _get(signed_in, "timeline_page", world["matter"]),
    ):
        text = _text(page)
        for words in (
            "UXINTLEGACY",
            "UXINTDISCOVERED",
            "UXINTRECEIVED",
            "UXINTNOTE",
            "UXINTOTHER",
        ):
            assert words in text, words
        assert _text(_position_row(page, world["legacy"])).count(EXTERNAL_POSITION_LEGACY_HEADLINE)
        assert "Teiste arvamus" in _text(_position_row(page, world["discovered"]))
        assert "Meile saadetud tagasiside" in _text(_position_row(page, world["received"]))
        other_row = page[page.index(f'id="sissekanne-{world["other"].pk}"') :]
        other_row = other_row[: other_row.index("</article>")]
        assert ">Muu</span>" in other_row
        assert "uxtl__link" not in other_row
    assert world["legacy"].provenance == LEGACY


@pytest.mark.parametrize("which", ["legacy", "discovered"])
def test_scenario_5_a_historical_position_is_corrected_without_moving_its_facts(
    signed_in, historical_world, which
):
    world = historical_world
    matter, position = world["matter"], world[which]
    facts = (
        position.provenance,
        position.stated_on,
        position.stated_on_precision,
        position.created_by_id,
        position.organisation_id,
        position.engagement_id,
        position.created_at,
    )
    evidence = set(
        DocumentLink.objects.filter(external_position=position).values_list(
            "document_id", flat=True
        )
    )
    assert evidence
    round_closure = MatterEngagement.objects.values_list(
        "feedback_closed_at", "feedback_closed_by_id"
    ).get(pk=world["round"].pk)
    action = _url("update_external_position", matter, position_id=position.pk)
    drawn = _drawn_form(
        _get(signed_in, "update_external_position", matter, position_id=position.pk), action
    )
    drawn["summary"] = [f"{position.summary} Parandatud."]

    response = signed_in.post(action, drawn, headers=HX)

    assert response.status_code == 200, _text(response.content.decode())[:600]
    position.refresh_from_db()
    assert position.summary.endswith("Parandatud.")
    assert (
        position.provenance,
        position.stated_on,
        position.stated_on_precision,
        position.created_by_id,
        position.organisation_id,
        position.engagement_id,
        position.created_at,
    ) == facts
    assert (
        set(
            DocumentLink.objects.filter(external_position=position).values_list(
                "document_id", flat=True
            )
        )
        == evidence
    )
    # The round the LEGACY reply answered keeps its closure.
    assert (
        MatterEngagement.objects.values_list("feedback_closed_at", "feedback_closed_by_id").get(
            pk=world["round"].pk
        )
        == round_closure
    )


@pytest.mark.parametrize("which", ["note", "other"])
def test_scenario_5_an_old_entry_is_corrected_through_edit_entry(
    signed_in, historical_world, which
):
    world = historical_world
    matter, entry = world["matter"], world[which]
    facts = (entry.kind, entry.occurred_at, entry.author_id, entry.created_at, entry.url)
    assert entry.url == ""
    action = _url("edit_entry", matter, entry_id=entry.pk)
    drawn = _drawn_form(_get(signed_in, "edit_entry", matter, entry_id=entry.pk), action)
    # Only a Muu's correction offers the link box; an old one is still a Muu.
    assert ("url" in drawn) == (which == "other")
    drawn["body"] = ["Parandatud ajalooline kirje UXINT."]

    response = signed_in.post(action, drawn, headers=HX)

    assert response.status_code == 200, _text(response.content.decode())[:600]
    entry.refresh_from_db()
    assert "Parandatud ajalooline kirje UXINT." in entry.body
    assert (entry.kind, entry.occurred_at, entry.author_id, entry.created_at, entry.url) == facts
    (revision,) = EntryRevision.objects.filter(entry=entry)
    assert revision.url == ""


def test_scenario_5_an_insert_that_omits_url_stores_the_empty_default(historical_world):
    """Migration 0049's `db_default=""`: the release still serving during
    `migrate`, and a code-only rollback, insert entries without naming `url`."""
    source = historical_world["other"]
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'matters_entry' ORDER BY ordinal_position"
        )
        columns = [name for (name,) in cursor.fetchall() if name not in ("id", "url")]
        new_id = uuid.uuid4()
        listed = ", ".join(columns)
        cursor.execute(
            f"INSERT INTO matters_entry (id, {listed})"  # noqa: S608 — names from the catalogue
            f" SELECT %s, {listed} FROM matters_entry WHERE id = %s",
            [new_id, source.pk],
        )
    copied = Entry.objects.get(pk=new_id)
    assert copied.url == ""
    assert copied.kind == EntryKind.OTHER
    # Every row written by the earlier code path holds the empty default too.
    assert set(Entry.objects.values_list("url", flat=True)) == {""}
