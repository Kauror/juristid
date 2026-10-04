"""A file is never less restricted than the record it is evidence for (docs/adr/0137).

`capture_accepted_evidence` creates every record-attached `Document` with the
record's own `visibility_override` when it has one, so a file put on a record
restricted below its Matter is restricted with it. Before this, only an
opinion's working documents (docs/adr/0129 §4) and `+ Lisa fail` on an open
round passed the override; every other Teema panel created the file at the
Matter's visibility. `DocumentLink.visible_to` hid the *relation*, but the file
itself was listed on Dokumendid, counted and found in search by readers who may
not see the record.

Asserted per record kind, through the door a person actually uses:

* **the creation doors** — `PRAEGUNE TEGEVUS` (an `Entry`), `+ Kaasamine`,
  `+ Oluline tähtaeg`, `+ Jõustumine`, `+ Töövõit`, `+ Väline seisukoht` and
  `+ Menetluse areng`. No panel offers a restriction yet (importers, the shell
  and the admin write one), so the record is restricted as its creator returns
  it — the state a later form or an importer produces — and the door is then
  left to decide the file's visibility on its own;
* **the doors onto an existing record** — `Lõpeta kaasamine`, `+ Lisa fail` on
  a round, a development and a position, and `+ Lisa töödokument` on an
  opinion — where the restricted record is the realistic case today.

For each, the file is `RESTRICTED`, absent from a reader's Dokumendid page and
search, adds nothing to the reader's count, and is still there for somebody who
may see the record, so no case can pass by hiding everything. Existing rows are
not rewritten: a file captured before the record was restricted keeps its
visibility (no backfill without the owner's decision).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.documents.models import Document
from app.documents.services import evidence_visibility_override
from app.matters import workspace
from app.matters.enums import EngagementKind
from app.search.services import result_count
from app.workflow.services import set_next_action_for_new_work
from tests import factories

pytestmark = pytest.mark.django_db

#: One distinctive word in every filename, so search answers for this file only.
MARKER_WORD = "Varjatudlisa"


def _day(n: int) -> dt.date:
    return timezone.localdate() + dt.timedelta(days=n)


def _pdf(kind: str) -> SimpleUploadedFile:
    name = f"{MARKER_WORD} {kind}.pdf"
    return SimpleUploadedFile(name, f"%PDF-1.4 {name}".encode(), content_type="application/pdf")


def _restrict(record: Any) -> Any:
    """Restrict ``record`` below its Matter, as an importer or the shell writes it."""
    type(record).objects.filter(pk=record.pk).update(visibility_override=Visibility.RESTRICTED)
    record.refresh_from_db()
    return record


def _restricting(monkeypatch, owner: Any, name: str) -> None:
    """Make ``owner.name`` — a door's record creator — return its record restricted."""
    original = getattr(owner, name)

    def creator(*args: Any, **kwargs: Any) -> Any:
        return _restrict(original(*args, **kwargs))

    monkeypatch.setattr(owner, name, creator)


def _round(matter, actor) -> Any:
    from app.matters.services import add_engagement

    return add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="Liikmete küsitlus",
        occurred_on=_day(-3),
        feedback_deadline=_day(5),
        actor=actor,
    )


def _position(matter, actor, organisation) -> Any:
    from app.matters.services import record_external_position

    return record_external_position(
        matter=matter,
        organisation=organisation,
        stated_on=_day(-1),
        summary="Liidu seisukoht.",
        actor=actor,
    )


def _development(matter, actor) -> Any:
    return workspace.add_procedural_development(
        matter=matter, author=actor, title="Eelnõu läks valitsusse", occurred_on=_day(-2)
    ).record


def _sent_opinion(matter, actor, organisation, *, restricted: bool) -> Any:
    from app.submissions.models import Submission

    submission = workspace.add_matter_koda_opinion(
        matter=matter,
        author=actor,
        upload=SimpleUploadedFile("koda_arvamus.pdf", b"%PDF-1.4 arvamus"),
        recipients=[organisation],
        sent_on=timezone.localdate(),
    ).record
    if restricted:
        # The letter first: the evidence trigger refuses a send restricted above
        # the bytes it stands on (tests/test_opinion_working_documents.py).
        Document.objects.filter(pk=submission.final_version.document_id).update(
            visibility_override=Visibility.RESTRICTED
        )
        Submission.objects.filter(pk=submission.pk).update(
            visibility_override=Visibility.RESTRICTED
        )
        submission.refresh_from_db()
    return submission


# ---------------------------------------------------------------------------
# The doors, one per record kind and route
# ---------------------------------------------------------------------------
#
# Each takes the world and whether the record is restricted, writes one file
# through the door, and returns the documents that door created.

Door = Callable[[SimpleNamespace, bool], list[Document]]


def _entry_by_completing_the_step(w: SimpleNamespace, restricted: bool) -> list[Document]:
    action = set_next_action_for_new_work(matter=w.matter, text="Loe eelnõu", actor=w.author)
    if restricted:
        _restricting(w.monkeypatch, workspace, "add_entry")
    return workspace.complete_current_action(
        matter=w.matter,
        author=w.author,
        action_id=action.pk,
        body="Lugesin eelnõu läbi.",
        uploads=[_pdf("märge")],
    ).documents


def _new_engagement(w: SimpleNamespace, restricted: bool) -> list[Document]:
    if restricted:
        _restricting(w.monkeypatch, workspace, "add_engagement")
    return workspace.add_matter_engagement(
        matter=w.matter, author=w.author, audience="Liikmed", uploads=[_pdf("kaasamine")]
    ).documents


def _finishing_an_engagement(w: SimpleNamespace, restricted: bool) -> list[Document]:
    engagement = _round(w.matter, w.author)
    if restricted:
        _restrict(engagement)
    workspace.add_engagement_feedback(
        engagement=engagement,
        author=w.author,
        feedback_received="Vastas kolm liiget.",
        uploads=[_pdf("tagasiside")],
    )
    # This door returns the round, not its files: read them off the round.
    return list(Document.objects.filter(links__engagement=engagement))


def _file_on_an_open_engagement(w: SimpleNamespace, restricted: bool) -> list[Document]:
    engagement = _round(w.matter, w.author)
    if restricted:
        _restrict(engagement)
    return workspace.add_engagement_evidence(
        engagement=engagement, author=w.author, uploads=[_pdf("voor")]
    ).documents


def _intelligence_door(service: str, door: Callable[..., Any], **fields: Any) -> Door:
    def write(w: SimpleNamespace, restricted: bool) -> list[Document]:
        from app.intelligence import services as intelligence

        if restricted:
            _restricting(w.monkeypatch, intelligence, service)
        return door(matter=w.matter, author=w.author, uploads=[_pdf(service)], **fields).documents

    return write


_important_date = _intelligence_door(
    "add_important_date",
    workspace.add_matter_important_date,
    title="Avaliku konsultatsiooni lõpp",
    date_value=_day(10),
    period_end=_day(10),
    date_precision="EXACT",
)
_effective_date = _intelligence_door(
    "add_effective_date",
    workspace.add_matter_effective_date,
    description="Seadus jõustub",
    date_value=_day(30),
    period_end=_day(30),
    date_precision="EXACT",
)
_work_victory = _intelligence_door(
    "add_confirmed_work_victory",
    workspace.add_matter_work_victory,
    title="Tähtaeg pikenes",
    period_date=dt.date(2026, 1, 1),
    period_end=dt.date(2026, 12, 31),
    date_precision="YEAR",
)


def _new_external_position(w: SimpleNamespace, restricted: bool) -> list[Document]:
    from app.matters import services as matter_services

    if restricted:
        _restricting(w.monkeypatch, matter_services, "record_external_position")
    return workspace.add_matter_external_position(
        matter=w.matter,
        author=w.author,
        organisation=w.organisation,
        stated_on=_day(-1),
        uploads=[_pdf("seisukoht")],
    ).documents


def _file_on_an_external_position(w: SimpleNamespace, restricted: bool) -> list[Document]:
    position = _position(w.matter, w.author, w.organisation)
    if restricted:
        _restrict(position)
    return workspace.add_external_position_evidence(
        position=position, author=w.author, uploads=[_pdf("seisukoha lisa")]
    ).documents


def _new_procedural_development(w: SimpleNamespace, restricted: bool) -> list[Document]:
    from app.matters import services as matter_services

    if restricted:
        _restricting(w.monkeypatch, matter_services, "record_procedural_development")
    return workspace.add_procedural_development(
        matter=w.matter,
        author=w.author,
        title="Ministeerium saatis uue versiooni",
        occurred_on=_day(-1),
        uploads=[_pdf("areng")],
    ).documents


def _file_on_a_procedural_development(w: SimpleNamespace, restricted: bool) -> list[Document]:
    development = _development(w.matter, w.author)
    if restricted:
        _restrict(development)
    return workspace.add_development_evidence(
        development=development, author=w.author, uploads=[_pdf("arengu lisa")]
    ).documents


def _working_document_on_an_opinion(w: SimpleNamespace, restricted: bool) -> list[Document]:
    submission = _sent_opinion(w.matter, w.author, w.organisation, restricted=restricted)
    return workspace.add_opinion_working_documents(
        submission=submission, author=w.author, uploads=[_pdf("töödokument")]
    ).documents


DOORS: dict[str, Door] = {
    "entry-completing-the-step": _entry_by_completing_the_step,
    "engagement-new": _new_engagement,
    "engagement-finished": _finishing_an_engagement,
    "engagement-add-file": _file_on_an_open_engagement,
    "important-date-new": _important_date,
    "effective-date-new": _effective_date,
    "work-victory-new": _work_victory,
    "external-position-new": _new_external_position,
    "external-position-add-file": _file_on_an_external_position,
    "procedural-development-new": _new_procedural_development,
    "procedural-development-add-file": _file_on_a_procedural_development,
    "opinion-working-document": _working_document_on_an_opinion,
}


@pytest.fixture
def world(monkeypatch, specialist, organisation) -> SimpleNamespace:
    return SimpleNamespace(
        matter=factories.MatterFactory(owner=specialist),
        author=specialist,
        organisation=organisation,
        monkeypatch=monkeypatch,
    )


def _dokumendid(client, user, matter) -> str:
    client.force_login(user)
    return client.get(
        reverse("matters:matter_documents", kwargs={"pk": matter.pk})
    ).content.decode()


def _readers_count(reader, matter) -> int:
    return Document.objects.visible_to(reader).filter(matter=matter).count()


# ---------------------------------------------------------------------------
# The rule, per door
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("door", DOORS.values(), ids=DOORS.keys())
def test_a_file_on_a_restricted_record_is_restricted_with_it(
    door, world, client, specialist, reader
):
    before = _readers_count(reader, world.matter)

    (document,) = door(world, True)

    assert document.visibility_override == Visibility.RESTRICTED
    assert document.matter.visibility == Visibility.NORMAL

    # Invisible to a reader who may not see the record: not listed, not counted,
    # not found.
    assert not Document.objects.visible_to(reader).filter(pk=document.pk).exists()
    assert document.title not in _dokumendid(client, reader, world.matter)
    assert _readers_count(reader, world.matter) == before
    assert result_count(query=MARKER_WORD, user=reader) == 0

    # And still there for somebody who may see it.
    assert document.title in _dokumendid(client, specialist, world.matter)
    assert result_count(query=MARKER_WORD, user=specialist) >= 1


@pytest.mark.parametrize("door", DOORS.values(), ids=DOORS.keys())
def test_a_file_on_an_unrestricted_record_keeps_the_matters_visibility(door, world, client, reader):
    """The other half: nothing is restricted that was not restricted before."""
    (document,) = door(world, False)

    assert document.visibility_override == ""
    assert document.title in _dokumendid(client, reader, world.matter)
    assert result_count(query=MARKER_WORD, user=reader) >= 1


# ---------------------------------------------------------------------------
# What the rule does not do
# ---------------------------------------------------------------------------


def test_an_existing_file_is_not_rewritten_when_its_record_is_restricted(world, reader):
    """No backfill: rows captured before the record was restricted keep theirs.

    Rewriting existing documents is the owner's decision (docs/adr/0137 §3); the
    rule decides only what a new file is created with.
    """
    earlier = workspace.add_procedural_development(
        matter=world.matter,
        author=world.author,
        title="Samm",
        occurred_on=_day(-2),
        uploads=[_pdf("varasem")],
    )
    development = _restrict(earlier.record)

    (later,) = workspace.add_development_evidence(
        development=development, author=world.author, uploads=[_pdf("hilisem")]
    ).documents

    (first,) = earlier.documents
    first.refresh_from_db()
    assert first.visibility_override == ""
    assert later.visibility_override == Visibility.RESTRICTED


def test_a_restricted_matters_files_are_left_to_the_matter(specialist):
    """A record with no override of its own adds nothing: the Matter decides."""
    matter = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)

    (document,) = workspace.add_matter_engagement(
        matter=matter, author=specialist, audience="Liikmed", uploads=[_pdf("kaasamine")]
    ).documents

    assert document.visibility_override == ""
    assert document.effective_visibility == Visibility.RESTRICTED


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        (SimpleNamespace(visibility_override=""), ""),
        (SimpleNamespace(visibility_override=Visibility.NORMAL.value), ""),
        (SimpleNamespace(visibility_override=Visibility.RESTRICTED.value), "RESTRICTED"),
        (SimpleNamespace(visibility_override=None), ""),
        (object(), ""),
    ],
    ids=["empty", "normal", "restricted", "null", "no-override-at-all"],
)
def test_the_override_a_new_file_is_created_with(record, expected):
    assert evidence_visibility_override(record) == expected
