"""`PRAEGUNE TEGEVUS` and `LISA TEEMALE`, rule by rule.

The reasoning is `docs/adr/0075`. Three claims are asserted here and everything
else follows from them:

1. **Saving the result of the current task completes it.** One operation, one
   button, no `Märgi tehtuks` — and no order in which half of it can happen.
2. **Every other write begins by saying what it is.** Seven choices, seven
   small forms, seven saves, seven endpoints. A refused one refuses itself and
   nothing else.
3. **A file supports an exact record**, not the Matter at large and not a
   guess reconstructed later from a timestamp or a filename.

The domain underneath is unchanged. Where a canonical rule is asserted here it
is because the surface that reaches it moved, not because the rule did.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.core.errors import DomainError
from app.documents.links import DocumentLink
from app.documents.models import Document
from app.intelligence.enums import WorkVictoryStatus
from app.intelligence.models import MatterEffectiveDate, MatterImportantDate, MatterWorkVictory
from app.matters import workspace
from app.matters.models import Entry, MatterEngagement
from app.matters.timeline import matter_timeline
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics
from app.workflow.models import NextAction
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _detail(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _action(matter, actor, *, text: str = "Vaata uus eelnõu versioon üle", days: int = 7):
    return set_next_action(
        matter=matter,
        text=text,
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() + timedelta(days=days),
        actor=actor,
    )


def _pdf(name: str = "koond.pdf", body: bytes = b"%PDF-1.4 sisu") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, body, content_type="application/pdf")


def _post(client, route, matter, payload, files=None):
    data = dict(payload)
    if files:
        data["attachments"] = files
    return client.post(
        reverse(route, kwargs={"pk": matter.pk}),
        data,
        headers={"HX-Request": "true"},
    )


def _finish(client, matter, action, body="<p>Vaatasin versiooni üle.</p>", files=None):
    return _post(
        client,
        "matters:complete_current_action",
        matter,
        {"action_id": str(action.pk), "body": body},
        files=files,
    )


def _zone(body: str) -> str:
    return body[body.index('id="praegune-tegevus"') : body.index('id="ajajoon"')]


# ===========================================================================
# A — the current action renders, and offers exactly one way to finish it
# ===========================================================================


def test_the_current_action_renders_its_own_text_and_date(signed_in, normal_matter, specialist):
    action = _action(normal_matter, specialist)

    zone = _zone(_detail(signed_in, normal_matter))

    assert "Vaata uus eelnõu versioon üle" in zone
    assert action.display_date in zone
    assert "Mida tegid?" in zone
    assert zone.count('class="curact__form"') == 1


def test_there_is_exactly_one_save_and_no_separate_completion_control(
    signed_in, normal_matter, specialist
):
    """**The decision this round exists to make.** Saving the result *is* the
    completion, so there is nothing else on the page that completes."""
    _action(normal_matter, specialist)

    zone = _zone(_detail(signed_in, normal_matter))
    # From the completion form's own opening tag to its own closing one. `Muuda`
    # sits above it with a form of its own, which is a different operation.
    completion = zone[zone.index('class="curact__form"') :]
    completion = completion[: completion.index("</form>")]

    assert completion.count('type="submit"') == 1
    assert "Salvesta" in completion
    for gone in ("Märgi tehtuks", "✓ Tehtud", "Tehtud</button>", "/valmis/"):
        assert gone not in zone, gone


def test_the_action_is_named_by_the_form_rather_than_found_by_the_server(
    signed_in, normal_matter, specialist
):
    action = _action(normal_matter, specialist)

    zone = _zone(_detail(signed_in, normal_matter))

    assert f'name="action_id" value="{action.pk}"' in zone


# ===========================================================================
# B — one save writes the entry, its files and the completion
# ===========================================================================


def test_saving_the_result_writes_one_entry_and_completes_that_action(
    signed_in, normal_matter, specialist
):
    action = _action(normal_matter, specialist)

    response = _finish(signed_in, normal_matter, action)

    assert response.status_code == 200
    entries = list(Entry.objects.filter(matter=normal_matter))
    assert len(entries) == 1
    assert "Vaatasin versiooni üle." in entries[0].body

    action.refresh_from_db()
    assert action.status == ActionStatus.COMPLETED
    assert action.ended_by == specialist
    # **No new step is opened for them.** They may have finished what they
    # needed to do; the next one is a deliberate act (brief §7).
    assert NextAction.objects.filter(matter=normal_matter, status=ActionStatus.OPEN).count() == 0


def test_the_completed_action_is_no_longer_the_current_one_after_a_refresh(
    signed_in, normal_matter, specialist
):
    action = _action(normal_matter, specialist)
    _finish(signed_in, normal_matter, action)

    body = _detail(signed_in, normal_matter)

    assert "Järgmine samm on määramata" in body
    assert "Mida tegid?" not in body
    assert "+ Järgmine tegevus" in body
    # The result is in the chronology.
    assert "Vaatasin versiooni üle." in body


# ===========================================================================
# C — a description is required, and nothing is invented in its place
# ===========================================================================


def test_a_blank_description_is_refused_and_the_action_stays_open(
    signed_in, normal_matter, specialist
):
    action = _action(normal_matter, specialist)

    response = _finish(signed_in, normal_matter, action, body="")

    assert response.status_code == 400
    assert "Kirjelda, mida tegid." in response.content.decode()
    assert not Entry.objects.filter(matter=normal_matter).exists()
    action.refresh_from_db()
    assert action.status == ActionStatus.OPEN


def test_an_empty_rich_text_paragraph_is_a_blank_description(signed_in, normal_matter, specialist):
    """`<p></p>` is a non-empty string and an empty sentence. The editor posts
    one from an untouched box, and accepting it would create an entry with no
    content."""
    action = _action(normal_matter, specialist)

    response = _finish(signed_in, normal_matter, action, body="<p></p>")

    assert response.status_code == 400
    assert not Entry.objects.filter(matter=normal_matter).exists()
    action.refresh_from_db()
    assert action.status == ActionStatus.OPEN


def test_a_file_does_not_stand_in_for_the_description(signed_in, normal_matter, specialist):
    """An upload is supplementary evidence. Filing "koond.pdf" as an account of
    what somebody did is the application putting words in a lawyer's mouth."""
    action = _action(normal_matter, specialist)

    response = _finish(signed_in, normal_matter, action, body="", files=[_pdf()])

    assert response.status_code == 400
    assert not Entry.objects.filter(matter=normal_matter).exists()
    assert not Document.objects.filter(matter=normal_matter).exists()
    action.refresh_from_db()
    assert action.status == ActionStatus.OPEN


# ===========================================================================
# D — a refused upload leaves nothing behind
# ===========================================================================


def test_a_refused_upload_leaves_no_entry_no_document_and_an_open_action(
    signed_in, normal_matter, specialist
):
    action = _action(normal_matter, specialist)
    bad = SimpleUploadedFile("arvamus.exe", b"MZ mitte pdf", content_type="application/pdf")

    response = _finish(signed_in, normal_matter, action, files=[bad])

    assert response.status_code == 400
    assert not Entry.objects.filter(matter=normal_matter).exists()
    assert not Document.objects.filter(matter=normal_matter).exists()
    assert not DocumentLink.objects.exists()
    action.refresh_from_db()
    assert action.status == ActionStatus.OPEN


def test_the_second_of_three_files_being_refused_takes_the_whole_save_with_it(
    signed_in, normal_matter, specialist
):
    """Not a fact standing there claiming evidence and holding one file."""
    action = _action(normal_matter, specialist)
    files = [
        _pdf("esimene.pdf"),
        SimpleUploadedFile("teine.exe", b"MZ", content_type="application/pdf"),
        _pdf("kolmas.pdf"),
    ]

    response = _finish(signed_in, normal_matter, action, files=files)

    assert response.status_code == 400
    assert not Entry.objects.filter(matter=normal_matter).exists()
    assert not Document.objects.filter(matter=normal_matter).exists()
    assert not DocumentLink.objects.exists()
    action.refresh_from_db()
    assert action.status == ActionStatus.OPEN


# ===========================================================================
# E — the stale tab
# ===========================================================================


def test_a_stale_action_id_does_not_complete_the_action_that_replaced_it(
    signed_in, normal_matter, specialist
):
    """Tab A shows action X. Tab B replaces it with Y. Tab A presses Salvesta.

    Y must not be completed, and nothing at all may be written: Tab A's author
    is describing work on a task that is no longer anybody's (brief §6).
    """
    stale = _action(normal_matter, specialist, text="Vana ülesanne")
    fresh = _action(normal_matter, specialist, text="Uus ülesanne")
    stale.refresh_from_db()
    assert stale.status == ActionStatus.SUPERSEDED

    response = _finish(signed_in, normal_matter, stale)

    assert response.status_code == 400
    assert workspace.STALE_ACTION_REFUSAL in response.content.decode()
    fresh.refresh_from_db()
    assert fresh.status == ActionStatus.OPEN
    stale.refresh_from_db()
    assert stale.status == ActionStatus.SUPERSEDED
    assert not Entry.objects.filter(matter=normal_matter).exists()


def test_the_refusal_returns_the_fresh_workspace_state(signed_in, normal_matter, specialist):
    stale = _action(normal_matter, specialist, text="Vana ülesanne")
    _action(normal_matter, specialist, text="Uus ülesanne")

    html = _finish(signed_in, normal_matter, stale).content.decode()

    assert "Uus ülesanne" in html
    assert 'id="praegune-tegevus"' in html


def test_an_action_the_reader_may_not_see_is_not_completable(
    client, normal_matter, specialist, reader
):
    """404, not a refusal that confirms the identifier names something."""
    action = _action(normal_matter, specialist)
    client.force_login(reader)

    response = _finish(client, normal_matter, action)

    assert response.status_code in (302, 404)
    action.refresh_from_db()
    assert action.status == ActionStatus.OPEN


# ===========================================================================
# F — a double submit
# ===========================================================================


def test_a_double_submit_produces_exactly_one_completion_and_one_result(
    signed_in, normal_matter, specialist
):
    action = _action(normal_matter, specialist)

    first = _finish(signed_in, normal_matter, action)
    second = _finish(signed_in, normal_matter, action)

    assert first.status_code == 200
    assert second.status_code == 400
    assert Entry.objects.filter(matter=normal_matter).count() == 1
    action.refresh_from_db()
    assert action.status == ActionStatus.COMPLETED


# ===========================================================================
# G — no current action
# ===========================================================================


def test_with_no_current_action_there_is_no_completion_form(signed_in, normal_matter):
    body = _detail(signed_in, normal_matter)

    assert "Järgmine samm on määramata" in body
    assert "Mida tegid?" not in body
    assert 'name="action_id"' not in body
    assert "+ Järgmine tegevus" in body


def test_while_a_step_is_open_the_launcher_offers_muuda_instead(
    signed_in, normal_matter, specialist
):
    """At most one open `NextAction`, and therefore one control for it."""
    _action(normal_matter, specialist)

    body = _detail(signed_in, normal_matter)

    assert "+ Järgmine tegevus" not in body
    assert "Muuda" in _zone(body)
    # Prefilled, because it is an editor rather than a second empty form.
    assert 'value="Vaata uus eelnõu versioon üle"' in body


def test_muuda_supersedes_rather_than_completes(signed_in, normal_matter, specialist):
    action = _action(normal_matter, specialist)

    response = signed_in.post(
        reverse("matters:set_action", kwargs={"pk": normal_matter.pk}),
        {"text": "Hoopis midagi muud", "target_date": "20.10.2026"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    action.refresh_from_db()
    assert action.status == ActionStatus.SUPERSEDED
    assert action.status != ActionStatus.COMPLETED
    current = NextAction.objects.get(matter=normal_matter, status=ActionStatus.OPEN)
    assert current.text == "Hoopis midagi muud"
    # Changing the task is not recording that it was done.
    assert not Entry.objects.filter(matter=normal_matter).exists()


# ===========================================================================
# LISA TEEMALE — seven intentions, seven saves
# ===========================================================================


def test_the_launcher_offers_its_choices_and_opens_none_of_them(signed_in, normal_matter):
    body = _detail(signed_in, normal_matter)
    zone = body[body.index('id="lisa-teemale"') : body.index('id="ajajoon"')]

    for chip in (
        "+ Märge",
        "+ Järgmine tegevus",
        "+ Kaasamine",
        "+ Oluline tähtaeg",
        "+ Jõustumine",
        "+ Töövõit",
        "+ Lõpeta teema",
    ):
        assert chip in zone, chip
    assert 'cx-panel" open' not in zone
    # Seven operations, seven saves. There is no shared one left.
    assert zone.count('type="submit"') == 7
    assert "composer__actions" not in zone


def test_a_marge_writes_an_entry_and_leaves_the_current_step_alone(
    signed_in, normal_matter, specialist
):
    """«Ministeerium helistas» while the task that is open stays open."""
    action = _action(normal_matter, specialist)

    response = _post(
        signed_in,
        "matters:add_note",
        normal_matter,
        {"body": "<p>Ministeerium helistas.</p>"},
    )

    assert response.status_code == 200
    assert Entry.objects.filter(matter=normal_matter).count() == 1
    action.refresh_from_db()
    assert action.status == ActionStatus.OPEN
    assert NextAction.objects.filter(matter=normal_matter).count() == 1


def test_a_next_action_is_created_with_what_and_when_and_nothing_else(signed_in, normal_matter):
    response = signed_in.post(
        reverse("matters:set_action", kwargs={"pk": normal_matter.pk}),
        {"text": "Kontrollida, kas ministeerium vastas", "target_date": "20.10.2026"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    action = NextAction.objects.get(matter=normal_matter)
    assert action.text == "Kontrollida, kas ministeerium vastas"
    assert action.kind == ActionKind.DO
    assert action.date_semantics == DateSemantics.DEADLINE
    assert action.date_precision == DatePrecision.EXACT


def test_a_next_action_without_a_date_is_refused_on_the_date(signed_in, normal_matter):
    response = signed_in.post(
        reverse("matters:set_action", kwargs={"pk": normal_matter.pk}),
        {"text": "Kontrollida", "target_date": ""},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 400
    assert "Vali järgmise tegevuse kuupäev" in response.content.decode()
    assert not NextAction.objects.filter(matter=normal_matter).exists()


def test_an_engagement_keeps_null_and_zero_apart(signed_in, normal_matter):
    """Blank means *nobody counted*; `0` means *nobody answered*. Two facts."""
    _post(
        signed_in,
        "matters:add_engagement_compact",
        normal_matter,
        {"kind": "SURVEY", "audience": "Liikmed", "response_count": ""},
    )
    _post(
        signed_in,
        "matters:add_engagement_compact",
        normal_matter,
        {"kind": "MEETING", "audience": "Töögrupp", "response_count": "0"},
    )

    uncounted = MatterEngagement.objects.get(title="Liikmed")
    counted = MatterEngagement.objects.get(title="Töögrupp")
    assert uncounted.response_count is None
    assert counted.response_count == 0


def test_an_important_date_creates_no_next_action(signed_in, normal_matter):
    response = _post(
        signed_in,
        "matters:add_important_date",
        normal_matter,
        {
            "deadline_title": "Kooskõlastusringi lõpp",
            "deadline_date": "30.09.2026",
            "deadline_precision": "EXACT",
        },
    )

    assert response.status_code == 200
    record = MatterImportantDate.objects.get(matter=normal_matter)
    assert record.title == "Kooskõlastusringi lõpp"
    assert not NextAction.objects.filter(matter=normal_matter).exists()


def test_a_commencement_is_a_canonical_effective_date(signed_in, normal_matter):
    response = _post(
        signed_in,
        "matters:add_effective_date",
        normal_matter,
        {"effective_title": "Pakendiseaduse muudatused", "effective_on": "1.1.2027"},
    )

    assert response.status_code == 200
    record = MatterEffectiveDate.objects.get(matter=normal_matter)
    assert record.description == "Pakendiseaduse muudatused"
    assert record.date_precision == DatePrecision.EXACT


def test_a_work_victory_is_confirmed_and_closes_nothing(signed_in, normal_matter, specialist):
    action = _action(normal_matter, specialist)

    response = _post(
        signed_in,
        "matters:add_work_victory",
        normal_matter,
        {"victory_change": "Üleminekuaeg pikendati"},
    )

    assert response.status_code == 200
    victory = MatterWorkVictory.objects.get(matter=normal_matter)
    assert victory.status == WorkVictoryStatus.CONFIRMED
    normal_matter.refresh_from_db()
    assert normal_matter.is_open
    action.refresh_from_db()
    assert action.status == ActionStatus.OPEN


def test_closing_writes_a_closure_and_fabricates_nothing(signed_in, normal_matter, specialist):
    action = _action(normal_matter, specialist)

    response = _post(
        signed_in,
        "matters:close_from_workspace",
        normal_matter,
        {"disposition": "INITIATIVE_WITHDRAWN", "closing_words": "Menetlus lõppes."},
    )

    assert response.status_code == 200
    normal_matter.refresh_from_db()
    assert not normal_matter.is_open
    assert normal_matter.disposition_reason == "Menetlus lõppes."
    # Nothing invented from a closure.
    assert not MatterWorkVictory.objects.filter(matter=normal_matter).exists()
    assert not normal_matter.submissions.exists()
    assert not Document.objects.filter(matter=normal_matter).exists()
    # The open step is ended through the closure's own domain logic.
    action.refresh_from_db()
    assert action.status == ActionStatus.CANCELLED


def test_one_invalid_form_changes_nothing_in_another_unopened_operation(signed_in, normal_matter):
    """A refused Töövõit is a refused Töövõit. Under the composer's shared save
    it could take a note down with it, and an opened-but-unanswered panel could
    be written by a save meant for something else (docs/adr/0075 §2)."""
    response = _post(
        signed_in,
        "matters:add_work_victory",
        normal_matter,
        {
            "victory_change": "",
            # Values belonging to other operations, posted at this endpoint.
            "body": "<p>Marge, mida keegi ei palunud.</p>",
            "disposition": "INITIATIVE_WITHDRAWN",
            "audience": "Liikmed",
            "effective_title": "Midagi",
            "effective_on": "1.1.2027",
        },
    )

    assert response.status_code == 400
    assert not MatterWorkVictory.objects.filter(matter=normal_matter).exists()
    assert not Entry.objects.filter(matter=normal_matter).exists()
    assert not MatterEngagement.objects.filter(matter=normal_matter).exists()
    assert not MatterEffectiveDate.objects.filter(matter=normal_matter).exists()
    normal_matter.refresh_from_db()
    assert normal_matter.is_open


def test_a_valid_save_writes_only_its_own_record(signed_in, normal_matter):
    """The other half of the same rule: a Töövõit save carrying somebody's
    stray closure and note fields writes a Töövõit and nothing else."""
    response = _post(
        signed_in,
        "matters:add_work_victory",
        normal_matter,
        {
            "victory_change": "Üleminekuaeg pikendati",
            "body": "<p>Marge, mida keegi ei palunud.</p>",
            "disposition": "INITIATIVE_WITHDRAWN",
            "audience": "Liikmed",
        },
    )

    assert response.status_code == 200
    assert MatterWorkVictory.objects.filter(matter=normal_matter).count() == 1
    assert not Entry.objects.filter(matter=normal_matter).exists()
    assert not MatterEngagement.objects.filter(matter=normal_matter).exists()
    normal_matter.refresh_from_db()
    assert normal_matter.is_open


def test_a_closed_matter_offers_no_workspace_and_stays_readable(
    signed_in, normal_matter, specialist
):
    from app.matters.services import add_entry, close_matter

    add_entry(matter=normal_matter, body="<p>Varasem töö.</p>", author=specialist)
    close_matter(matter=normal_matter, disposition="INITIATIVE_WITHDRAWN", actor=specialist)

    body = _detail(signed_in, normal_matter)

    assert "Mida tegid?" not in body
    assert 'id="lisa-teemale"' not in body
    assert "teema on suletud" in body
    assert "Varasem töö." in body, "the chronology still reads"


# ===========================================================================
# FILES — this file belongs to THIS record
# ===========================================================================


def _links_for(**target) -> list[DocumentLink]:
    return list(DocumentLink.objects.filter(**target).select_related("document"))


def _names(links) -> list[str]:
    return sorted(link.document.title for link in links)


def test_a_current_action_result_carries_its_files_and_they_belong_to_the_entry(
    signed_in, normal_matter, specialist
):
    action = _action(normal_matter, specialist)

    _finish(
        signed_in,
        normal_matter,
        action,
        files=[_pdf("kiri.pdf"), _pdf("lisa.pdf", b"%PDF-1.4 teine")],
    )

    entry = Entry.objects.get(matter=normal_matter)
    links = _links_for(entry=entry)
    assert _names(links) == ["kiri.pdf", "lisa.pdf"]
    # Ordinary evidence: the documents are on the Matter and readable from
    # Dokumendid like any other.
    assert Document.objects.filter(matter=normal_matter).count() == 2
    for link in links:
        assert link.document.current_version is not None


def test_a_marge_carries_its_own_files(signed_in, normal_matter):
    _post(
        signed_in,
        "matters:add_note",
        normal_matter,
        {"body": "<p>Ministeerium helistas.</p>"},
        files=[_pdf("teade.pdf")],
    )

    entry = Entry.objects.get(matter=normal_matter)
    assert _names(_links_for(entry=entry)) == ["teade.pdf"]


def test_an_engagement_carries_its_replies(signed_in, normal_matter):
    _post(
        signed_in,
        "matters:add_engagement_compact",
        normal_matter,
        {"kind": "SURVEY", "audience": "Liikmed", "response_count": "2"},
        files=[_pdf("vastus1.pdf"), _pdf("vastus2.pdf", b"%PDF-1.4 kaks")],
    )

    engagement = MatterEngagement.objects.get(matter=normal_matter)
    assert _names(_links_for(engagement=engagement)) == ["vastus1.pdf", "vastus2.pdf"]


def test_an_important_date_carries_the_letter_that_announced_it(signed_in, normal_matter):
    _post(
        signed_in,
        "matters:add_important_date",
        normal_matter,
        {
            "deadline_title": "Kooskõlastusringi lõpp",
            "deadline_date": "30.09.2026",
            "deadline_precision": "EXACT",
        },
        files=[_pdf("teade.pdf")],
    )

    record = MatterImportantDate.objects.get(matter=normal_matter)
    assert _names(_links_for(important_date=record)) == ["teade.pdf"]


def test_a_commencement_carries_the_act(signed_in, normal_matter):
    _post(
        signed_in,
        "matters:add_effective_date",
        normal_matter,
        {"effective_title": "Pakendiseaduse muudatused", "effective_on": "1.1.2027"},
        files=[_pdf("seadus.pdf")],
    )

    record = MatterEffectiveDate.objects.get(matter=normal_matter)
    assert _names(_links_for(effective_date=record)) == ["seadus.pdf"]


def test_a_work_victory_carries_its_evidence(signed_in, normal_matter):
    _post(
        signed_in,
        "matters:add_work_victory",
        normal_matter,
        {"victory_change": "Üleminekuaeg pikendati"},
        files=[_pdf("toend.pdf")],
    )

    victory = MatterWorkVictory.objects.get(matter=normal_matter)
    assert _names(_links_for(work_victory=victory)) == ["toend.pdf"]


def test_two_facts_seconds_apart_with_the_same_filename_keep_their_own_file(
    signed_in, normal_matter
):
    """The adversarial case the whole link table exists for.

    Two work victories recorded moments apart, each with a file called
    `toend.pdf`. Nothing about a timestamp, an ordering or a filename can tell
    them apart afterwards — the relationship has to have been written down
    (docs/adr/0075 §5).
    """
    _post(
        signed_in,
        "matters:add_work_victory",
        normal_matter,
        {"victory_change": "Esimene võit"},
        files=[_pdf("toend.pdf", b"%PDF-1.4 esimene")],
    )
    _post(
        signed_in,
        "matters:add_work_victory",
        normal_matter,
        {"victory_change": "Teine võit"},
        files=[_pdf("toend.pdf", b"%PDF-1.4 teine")],
    )

    first = MatterWorkVictory.objects.get(matter=normal_matter, title="Esimene võit")
    second = MatterWorkVictory.objects.get(matter=normal_matter, title="Teine võit")
    first_links = _links_for(work_victory=first)
    second_links = _links_for(work_victory=second)

    assert len(first_links) == 1
    assert len(second_links) == 1
    assert first_links[0].document_id != second_links[0].document_id
    assert first_links[0].document.current_version.sha256 != (
        second_links[0].document.current_version.sha256
    )


def test_two_files_with_the_same_name_on_one_fact_are_two_documents(signed_in, normal_matter):
    _post(
        signed_in,
        "matters:add_note",
        normal_matter,
        {"body": "<p>Kaks faili.</p>"},
        files=[_pdf("sama.pdf", b"%PDF-1.4 a"), _pdf("sama.pdf", b"%PDF-1.4 b")],
    )

    entry = Entry.objects.get(matter=normal_matter)
    links = _links_for(entry=entry)
    assert len(links) == 2
    assert len({link.document_id for link in links}) == 2


def test_a_document_cannot_be_linked_to_a_record_on_another_matter(specialist):
    """The refusal that keeps a link inside one file. Enforced at the one
    service that creates links, because a CHECK constraint sees a single row
    and cannot follow a foreign key (docs/adr/0075 §6)."""
    from app.documents.services import create_document, link_document_to_record
    from app.matters.services import add_entry

    matter_a = factories.MatterFactory(owner=specialist)
    matter_b = factories.MatterFactory(owner=specialist)
    document = create_document(matter=matter_a, title="a.pdf", created_by=specialist)
    entry_on_b = add_entry(matter=matter_b, body="<p>B teema.</p>", author=specialist)

    with pytest.raises(DomainError):
        link_document_to_record(document=document, record=entry_on_b, actor=specialist)

    assert not DocumentLink.objects.exists()


def test_linking_the_same_pair_twice_writes_one_row(specialist):
    from app.documents.services import create_document, link_document_to_record
    from app.matters.services import add_entry

    matter = factories.MatterFactory(owner=specialist)
    document = create_document(matter=matter, title="a.pdf", created_by=specialist)
    entry = add_entry(matter=matter, body="<p>Sisu.</p>", author=specialist)

    link_document_to_record(document=document, record=entry, actor=specialist)
    link_document_to_record(document=document, record=entry, actor=specialist)

    assert DocumentLink.objects.filter(entry=entry).count() == 1


def test_a_link_may_not_name_two_records_or_none(specialist):
    """The database refuses it, whatever a future caller believes."""
    from django.db import IntegrityError, transaction

    from app.documents.services import create_document
    from app.matters.services import add_engagement, add_entry

    matter = factories.MatterFactory(owner=specialist)
    document = create_document(matter=matter, title="a.pdf", created_by=specialist)
    entry = add_entry(matter=matter, body="<p>Sisu.</p>", author=specialist)
    engagement = add_engagement(matter=matter, kind="SURVEY", title="Liikmed", actor=specialist)

    with pytest.raises(IntegrityError), transaction.atomic():
        DocumentLink.objects.create(document=document)

    with pytest.raises(IntegrityError), transaction.atomic():
        DocumentLink.objects.create(document=document, entry=entry, engagement=engagement)


# ===========================================================================
# The chronology shows associated files, and adds no row for them
# ===========================================================================


def test_a_fact_file_reads_on_the_facts_own_row_and_adds_none(signed_in, normal_matter, specialist):
    """A file is not a chronology event. Two PDFs on a Töövõit is one act and
    one row, with the files under it (brief §25)."""
    before, _ = matter_timeline(matter=normal_matter, user=specialist)
    baseline = len(before)

    _post(
        signed_in,
        "matters:add_work_victory",
        normal_matter,
        {"victory_change": "Üleminekuaeg pikendati"},
        files=[_pdf("toend.pdf"), _pdf("teine.pdf", b"%PDF-1.4 kaks")],
    )

    items, _ = matter_timeline(matter=normal_matter, user=specialist)
    assert len(items) == baseline + 1, "one row for one act, whatever it carried"

    row = next(item for item in items if item.milestone and item.milestone.what == "Töövõit")
    assert sorted(file.label for file in row.files) == ["teine.pdf", "toend.pdf"]
    # And no row of its own for the evidence.
    assert not [item for item in items if item.summary_verbs == ("lisas dokumendi",)]


def test_the_associated_file_is_a_link_on_the_rendered_row(signed_in, normal_matter, specialist):
    _post(
        signed_in,
        "matters:add_work_victory",
        normal_matter,
        {"victory_change": "Üleminekuaeg pikendati"},
        files=[_pdf("toend.pdf")],
    )

    body = _detail(signed_in, normal_matter)
    chronology = body[body.index('id="ajalugu-loend"') :]

    assert 'class="uxtl__file"' in chronology
    assert "toend.pdf" in chronology


def test_an_entry_file_is_not_printed_twice_on_its_own_row(signed_in, normal_matter, specialist):
    """A note's attachment is both an evidence event on the note's operation
    and a link to the note. One line, one link."""
    action = _action(normal_matter, specialist)
    _finish(signed_in, normal_matter, action, files=[_pdf("kiri.pdf")])

    items, _ = matter_timeline(matter=normal_matter, user=specialist)
    row = next(item for item in items if item.is_entry)

    assert [file.label for file in row.files] == ["kiri.pdf"]


def test_a_restricted_document_contributes_no_link_to_a_reader_who_may_not_see_it(
    signed_in, normal_matter, specialist, reader
):
    """The link is readable only when **both** of its ends are."""
    from app.core.enums import Visibility

    _post(
        signed_in,
        "matters:add_work_victory",
        normal_matter,
        {"victory_change": "Üleminekuaeg pikendati"},
        files=[_pdf("saladus.pdf")],
    )
    document = Document.objects.get(matter=normal_matter)
    document.visibility_override = Visibility.RESTRICTED
    document.save(update_fields=["visibility_override"])

    items, _ = matter_timeline(matter=normal_matter, user=reader)
    row = next(item for item in items if item.milestone and item.milestone.what == "Töövõit")

    assert row.files == ()
    # And the author still sees it.
    mine, _ = matter_timeline(matter=normal_matter, user=specialist)
    theirs = next(item for item in mine if item.milestone and item.milestone.what == "Töövõit")
    assert [file.label for file in theirs.files] == ["saladus.pdf"]


def test_the_rendered_workspace_contains_no_duplicate_element_id(
    signed_in, normal_matter, specialist
):
    """**The guard `tests/test_ui_contract` structurally cannot be.**

    That one reads literal `id="…"` out of each template separately, which is
    the right check for markup somebody wrote. These ids are *generated*: eight
    forms render on one page and two of them call a field `body` while six call
    one `attachments`, so Django produced `id_body` twice and `id_attachments`
    six times — invalid HTML, a `<label for>` pointing at the wrong control, and
    `getElementById` answering whichever came first. The browser lane found it
    on a strict-mode locator; this is the assertion that keeps it found.
    """
    import re

    _action(normal_matter, specialist)
    body = _detail(signed_in, normal_matter)

    identifiers = re.findall(r'\sid="([^"{}]+)"', body)
    duplicates = sorted({value for value in identifiers if identifiers.count(value) > 1})
    assert not duplicates, f"the Teema page renders duplicate ids: {duplicates}"
