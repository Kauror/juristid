"""The Riigikogu programme against a fake Riigikogu (ADR 0222–0226).

No test reaches api.riigikogu.ee: the fenced door (`http.get_json`, `http.get_bytes`)
answers from the dictionaries below, and the shared ledger's sleep is a no-op.
"""

from __future__ import annotations

import datetime as dt
import uuid as uuid_module
from typing import Any

import pytest
from django.test import override_settings
from django.utils import timezone

from app.core.enums import Visibility
from app.core.errors import DomainError
from app.register_watch.models import (
    CandidateNotice,
    RegisterNotice,
    TargetOrigin,
    TopicCandidate,
    WatchSource,
    WatchTarget,
)
from app.register_watch.sources import http
from app.register_watch.sources.http import FetchError
from app.riigikogu import (
    agendas,
    amendments,
    bills,
    client,
    cycle,
    invitations,
    responses,
    stenograms,
)
from app.riigikogu.enums import (
    AgendaState,
    DeadlineKind,
    DocumentKind,
    ExtractionStatus,
    FactState,
    InvitationReview,
    KodaRole,
    MentionCategory,
    ResponseCheck,
    RunStatus,
    Stage,
)
from app.riigikogu.models import (
    ApiCall,
    Bill,
    CommitteeAgendaItem,
    DocumentChange,
    ExternalDeadline,
    Invitation,
    ParliamentDocument,
    StageRun,
    StenogramMention,
)
from tests import factories

pytestmark = pytest.mark.django_db

BILL = "bd461960-bd69-460e-8331-b5d438ec8e0e"
OTHER_BILL = "3b32c0cf-cd0b-468e-afc3-0fe9057c2693"
LETTER = "27764765-981b-4f32-9d35-7d0c9b4470f3"
LETTER_FILE = "0f1c5b8e-1111-4c1d-9e1a-2b3c4d5e6f70"
NOTICE = "9a7b6c5d-2222-4c1d-9e1a-2b3c4d5e6f71"
OPINION = "5e4d3c2b-3333-4c1d-9e1a-2b3c4d5e6f72"

LETTER_HTML = (
    '<html><head><meta charset="windows-1252"></head><body><p>Tere! Riigikogu menetluses on '
    "Vabariigi Valitsuse poolt 09.11.26 algatatud äriseadustiku muutmise seaduse eelnõu "
    "( 1082 SE ), mille juhtivkomisjoniks on õiguskomisjon. Teil on võimalus avaldada eelnõu "
    "kohta arvamust ja esitada ettepanekuid õiguskomisjonile hiljemalt 24.11.26 e-posti "
    "aadressil komisjon@example.invalid ning ühtlasi palun andke teada, kas soovite osaleda "
    "oma ettepanekute arutelul.</p></body></html>"
).encode("cp1252")


def bill_row(
    uuid: str = BILL, mark: int = 1082, title: str = "Äriseadustiku muutmise seadus", **extra: Any
) -> dict:
    return {
        "uuid": uuid,
        "mark": mark,
        "draftTypeCode": "SE",
        "membership": 15,
        "title": title,
        "initiated": "2026-11-09",
        "activeDraftStage": "ESIMENE_LUGEMINE",
        "activeDraftStatus": "MENETLUSSE_VOETUD",
        "leadingCommittee": {"name": "Õiguskomisjon"},
        **extra,
    }


def letter_detail(
    uuid: str = LETTER,
    *,
    parties: str,
    title: str,
    file_uuid: str = LETTER_FILE,
    direction: str = "VALJA",
    volume: str = BILL,
) -> dict:
    return {
        "uuid": uuid,
        "title": title,
        "documentType": "letterDocument",
        "direction": {"code": direction},
        "author": parties,
        "authorDate": "2026-11-10",
        "created": "2026-11-10T09:00:00",
        "reference": "1-1/15-1082/1",
        "volume": {"uuid": volume, "volumeType": "eelnou", "reference": "1-1/15-1082"},
        "files": [{"uuid": file_uuid, "fileName": "Kaasamiskiri_(eelnõu_1082_SE).html"}],
    }


class FakeRiigikogu:
    """The API as dictionaries, with a record of every call."""

    def __init__(self) -> None:
        self.json: dict[str, Any] = {}
        self.files: dict[str, bytes] = {}
        self.calls: list[tuple[str, dict]] = []
        self.fail: dict[str, FetchError] = {}

    def get_json(self, host: str, path: str, params: dict | None = None) -> Any:
        assert host == client.HOST
        self.calls.append((path, dict(params or {})))
        if path in self.fail:
            raise self.fail[path]
        if path not in self.json:
            raise FetchError(f"{host} vastas 404", status=404)
        answer = self.json[path]
        return answer(params or {}) if callable(answer) else answer

    def get_bytes(self, host: str, path: str, **_: Any) -> bytes:
        self.calls.append((path, {}))
        if path not in self.files:
            raise FetchError(f"{host} vastas 404", status=404)
        return self.files[path]


@pytest.fixture
def api(monkeypatch):
    fake = FakeRiigikogu()
    monkeypatch.setattr(http, "get_json", fake.get_json)
    monkeypatch.setattr(http, "get_bytes", fake.get_bytes)
    monkeypatch.setattr(client, "sleep", lambda seconds: None)
    fake.json[f"/api/volumes/drafts/{BILL}"] = bill_row()
    fake.json[f"/api/volumes/drafts/{OTHER_BILL}"] = bill_row(OTHER_BILL, 990, "Kinnisasja seadus")
    return fake


def with_letter(
    api: FakeRiigikogu,
    *,
    parties: str,
    title: str = "Kaasamiskiri eelnõu 1082 SE kohta",
    html: bytes = LETTER_HTML,
    volume: str = BILL,
) -> None:
    api.json["/api/documents"] = lambda params: {
        "_embedded": {
            "content": [{"uuid": LETTER, "title": title, "created": "2026-11-10T09:00:00"}]
        },
        "page": {"totalPages": 1, "number": 0},
    }
    api.json[f"/api/documents/{LETTER}"] = letter_detail(
        parties=parties, title=title, volume=volume
    )
    api.files[f"/api/files/{LETTER_FILE}/download"] = html


KODA_AMONG = "Finantsinspektsioon, Eesti Kaubandus-Tööstuskoda, Eesti Advokatuur, Notarite Koda"


def discover() -> invitations.Counts:
    return invitations.discover(since=dt.date(2026, 11, 3), until=dt.date(2026, 11, 10))


def teema_on_bill(owner: Any, *, title: str = "Äriseadustiku muudatused", **extra: Any) -> Any:
    matter = factories.MatterFactory(title=title, owner=owner, **extra)
    WatchTarget.objects.create(
        matter=matter,
        source=WatchSource.RIIGIKOGU,
        identifier=BILL,
        label="1082 SE",
        origin=TargetOrigin.LINKED,
    )
    return matter


# -- RK-1: invitations --------------------------------------------------------------------


def test_an_invitation_to_koda_is_found_read_and_its_deadline_kept_with_the_sentence(
    api, specialist
):
    with_letter(api, parties=KODA_AMONG)
    counts = discover()
    assert counts.invitations_new == 1
    invitation = Invitation.objects.get()
    assert invitation.document.kind == DocumentKind.INVITATION
    assert invitation.document.koda_role == KodaRole.ADDRESSEE
    assert invitation.document.bill.uuid == uuid_module.UUID(BILL)
    due = invitations.current_opinion_date(invitation)
    assert (due.due_on, due.extraction) == (dt.date(2026, 11, 24), ExtractionStatus.FOUND)
    assert "hiljemalt 24.11.26" in due.evidence
    # The text itself is not stored: only its digest.
    assert invitation.document.content_hash
    assert not hasattr(invitation.document, "text")


def test_a_letter_that_only_mentions_koda_is_not_an_invitation(api):
    with_letter(
        api,
        parties="Eesti Pank, Eesti Põllumajandus-Kaubanduskoda",
        title="Vastus Eesti Kaubandus-Tööstuskoja pöördumisele",
    )
    counts = discover()
    assert counts.invitations_new == 0
    assert Invitation.objects.count() == 0
    document = ParliamentDocument.objects.get(uuid=LETTER)
    assert document.koda_role == KodaRole.MENTIONED


def test_reading_the_same_night_twice_writes_nothing_twice(api, specialist):
    teema_on_bill(specialist)
    with_letter(api, parties=KODA_AMONG)
    discover()
    discover()
    assert Invitation.objects.count() == 1
    assert ExternalDeadline.objects.filter(kind=DeadlineKind.OPINION_REQUEST).count() == 1
    notice = RegisterNotice.objects.get()
    assert notice.invitations == 1


def test_a_changed_letter_supersedes_the_old_date_and_keeps_it(api, specialist):
    teema_on_bill(specialist)
    with_letter(api, parties=KODA_AMONG)
    discover()
    api.files[f"/api/files/{LETTER_FILE}/download"] = LETTER_HTML.replace(b"24.11.26", b"28.11.26")
    api.json[f"/api/documents/{LETTER}"]["files"] = [
        {"uuid": LETTER_FILE, "fileName": "Kaasamiskiri.html"}
    ]
    counts = discover()
    assert counts.deadlines_changed == 1
    rows = list(
        ExternalDeadline.objects.filter(kind=DeadlineKind.OPINION_REQUEST).order_by("created_at")
    )
    assert [(r.due_on.day, r.state) for r in rows] == [
        (24, FactState.SUPERSEDED),
        (28, FactState.ACTIVE),
    ]
    assert rows[1].supersedes == rows[0]
    assert DocumentChange.objects.filter(what="content").exists()
    assert RegisterNotice.objects.get().invitations == 2


def test_an_unreadable_letter_is_still_an_invitation_saying_so(api):
    with_letter(api, parties=KODA_AMONG, html=b"")
    discover()
    invitation = Invitation.objects.get()
    due = invitations.current_opinion_date(invitation)
    assert due.extraction == ExtractionStatus.UNREADABLE
    assert due.due_on is None


def test_an_invitation_on_a_followed_bill_tells_the_teema_owner(api, specialist, department_head):
    teema_on_bill(specialist)
    with_letter(api, parties=KODA_AMONG)
    discover()
    assert RegisterNotice.objects.get().recipient == specialist
    assert not CandidateNotice.objects.filter(invitations__gt=0).exists()


def test_an_invitation_on_an_unfollowed_bill_becomes_a_candidate_for_the_head(api, department_head):
    with_letter(api, parties=KODA_AMONG)
    counts = discover()
    invitation = Invitation.objects.get()
    assert counts.unlinked == 1
    candidate = invitation.candidate
    assert candidate is not None
    assert candidate.items.filter(
        source=WatchSource.RIIGIKOGU, external_id=f"draft:{BILL}"
    ).exists()
    assert CandidateNotice.objects.get(recipient=department_head).invitations == 1
    # The model's verdict does not hide it: a WEAK candidate with an invitation is still told.
    candidate.strength = "WEAK"
    candidate.save()
    from app.register_watch import candidates

    assert candidates.worth_telling().filter(pk=candidate.pk).exists()


def test_an_existing_candidate_for_the_bill_is_enriched_not_duplicated(api, department_head):
    from app.register_watch.models import CandidateItem

    existing = TopicCandidate.objects.create(
        title="Äriseadustiku muutmine", match_key="äriseadustiku"
    )
    CandidateItem.objects.create(
        candidate=existing, source=WatchSource.RIIGIKOGU, external_id=f"draft:{BILL}", title="x"
    )
    with_letter(api, parties=KODA_AMONG)
    discover()
    assert Invitation.objects.get().candidate == existing
    assert TopicCandidate.objects.count() == 1


def test_a_teema_without_an_active_owner_falls_back_to_the_head(api, specialist, department_head):
    matter = teema_on_bill(specialist)
    specialist.is_active = False
    specialist.save()
    with_letter(api, parties=KODA_AMONG)
    discover()
    assert not RegisterNotice.objects.exists()
    assert CandidateNotice.objects.get(recipient=department_head).invitations == 1
    assert matter.pk


def test_the_head_notice_names_no_teema_so_a_restricted_one_is_not_revealed(
    api, specialist, department_head
):
    teema_on_bill(specialist, visibility=Visibility.RESTRICTED)
    specialist.is_active = False
    specialist.save()
    with_letter(api, parties=KODA_AMONG)
    discover()
    notice = CandidateNotice.objects.get(recipient=department_head)
    assert {f.name for f in notice._meta.fields} >= {"invitations"}
    assert not hasattr(notice, "matter")


# -- external dates are not koda's obligations, and Lovable stays authoritative -------------


def test_adding_the_invitation_date_writes_an_informational_date_never_arvamuse_tahtaeg(
    api, specialist
):
    from app.intelligence.enums import ImportantDateKind, is_informational_milestone

    matter = teema_on_bill(specialist, response_deadline=dt.date(2026, 11, 30))
    with_letter(api, parties=KODA_AMONG)
    discover()
    record = invitations.add_as_date(Invitation.objects.get(), matter, actor=specialist)
    assert record.kind == ImportantDateKind.RK_OPINION_REQUEST
    assert is_informational_milestone(record.kind)
    matter.refresh_from_db()
    # Lovable's `Arvamuse tähtaeg` is untouched.
    assert matter.response_deadline == dt.date(2026, 11, 30)
    assert Invitation.objects.get().review == InvitationReview.DATE_ADDED


def test_an_informational_parliamentary_date_is_never_the_next_step(api, specialist):
    from app.intelligence.enums import INFORMATIONAL_IMPORTANT_DATE_KINDS, ImportantDateKind

    for kind in (
        ImportantDateKind.RK_OPINION_REQUEST,
        ImportantDateKind.RK_AMENDMENTS_DEADLINE,
        ImportantDateKind.RK_COMMITTEE_SITTING,
    ):
        assert kind in INFORMATIONAL_IMPORTANT_DATE_KINDS


def test_a_date_that_needs_checking_is_refused_until_a_person_confirms_it(api, specialist):
    matter = teema_on_bill(specialist)
    with_letter(
        api, parties=KODA_AMONG, html=LETTER_HTML.replace(b"hiljemalt 24.11.26", b"reedeks")
    )
    discover()
    invitation = Invitation.objects.get()
    with pytest.raises(DomainError):
        invitations.add_as_date(invitation, matter, actor=specialist)
    invitations.confirm_date(invitation, actor=specialist, day=dt.date(2026, 11, 27))
    record = invitations.add_as_date(invitation, matter, actor=specialist)
    assert record.date_value == dt.date(2026, 11, 27)
    assert ExternalDeadline.objects.filter(state=FactState.SUPERSEDED).count() == 1


def test_an_invitation_is_decided_once(api, specialist):
    matter = teema_on_bill(specialist)
    with_letter(api, parties=KODA_AMONG)
    discover()
    invitation = Invitation.objects.get()
    invitations.no_action(invitation, actor=specialist)
    with pytest.raises(DomainError):
        invitations.add_as_date(invitation, matter, actor=specialist)


def test_a_reminder_is_the_persons_own_and_writes_no_teema_field(api, specialist):
    from app.notifications.models import PersonalReminder

    matter = teema_on_bill(specialist)
    with_letter(api, parties=KODA_AMONG)
    discover()
    invitations.add_reminder(
        Invitation.objects.get(),
        matter,
        actor=specialist,
        remind_on=timezone.localdate() + dt.timedelta(days=3),
    )
    assert PersonalReminder.objects.get().recipient == specialist
    # No step, no date: the Teema's work is exactly what it was.
    assert not matter.next_actions.exists()
    assert not matter.important_dates.exists()


# -- RK-0: identity and association ---------------------------------------------------------


def test_a_bill_is_its_uuid_and_a_title_change_keeps_the_tie(api, specialist):
    bill = bills.upsert(bills.facts(bill_row())).bill
    matter = factories.MatterFactory(title="Teema ilma numbrita", owner=specialist)
    bills.link(matter, bill, actor=specialist)
    bills.upsert(bills.facts(bill_row(title="Äriseadustiku ja teiste seaduste muutmise seadus")))
    assert list(bills.teemad_of(Bill.objects.get(uuid=BILL))) == [matter]
    target = WatchTarget.objects.get(matter=matter)
    assert (target.origin, target.linked_by) == (TargetOrigin.LINKED, specialist)


def test_one_teema_may_follow_several_bills_and_a_second_link_is_refused(api, specialist):
    first = bills.upsert(bills.facts(bill_row())).bill
    second = bills.upsert(bills.facts(bill_row(OTHER_BILL, 990))).bill
    matter = factories.MatterFactory(owner=specialist)
    bills.link(matter, first, actor=specialist)
    bills.link(matter, second, actor=specialist)
    assert bills.linked_targets(matter).count() == 2
    with pytest.raises(DomainError):
        bills.link(matter, first, actor=specialist)


def test_the_same_number_in_another_membership_is_a_different_bill(api):
    api.json["/api/volumes/drafts"] = {
        "_embedded": {
            "content": [
                bill_row("11111111-1111-1111-1111-111111111111", membership=10),
                bill_row(BILL, membership=15),
            ]
        }
    }
    found = bills.find(1082, "SE")
    assert found.uuid == uuid_module.UUID(BILL)
    old = bills.find(1082, "SE", membership=10)
    assert old.uuid != found.uuid


def test_proposals_show_every_possible_teema_and_apply_none(api, specialist):
    bill = bills.upsert(bills.facts(bill_row())).bill
    one = factories.MatterFactory(title="Äriseadustiku muutmise eelnõu (1082 SE)", owner=specialist)
    two = factories.MatterFactory(title="Äriseadustiku muutmise seaduse VTK", owner=specialist)
    found = {p.matter.pk for p in bills.proposals(bill, user=specialist)}
    assert one.pk in found
    assert two.pk in found
    assert not bills.teemad_of(bill).exists()


def test_no_teema_is_proposed_to_an_account_that_reads_no_business_content(
    api, specialist, administrator
):
    bill = bills.upsert(bills.facts(bill_row())).bill
    factories.MatterFactory(
        title="Äriseadustiku eelnõu (1082 SE)", owner=specialist, visibility=Visibility.RESTRICTED
    )
    assert bills.proposals(bill, user=specialist)
    assert bills.proposals(bill, user=administrator) == []


# -- RK-2: amendment deadlines and agendas --------------------------------------------------


def test_an_amendment_deadline_is_structured_and_a_move_is_kept_as_history(api):
    api.json["/api/volumes/drafts"] = {
        "_embedded": {"content": [bill_row(amendmentsDeadline="2026-12-02T17:15:00")]},
        "page": {"totalPages": 1},
    }
    amendments.read_window(today=dt.date(2026, 11, 24))
    amendments.read_window(today=dt.date(2026, 11, 24))
    current = ExternalDeadline.objects.get(kind=DeadlineKind.AMENDMENTS, state=FactState.ACTIVE)
    assert (current.due_on, current.due_time, current.extraction) == (
        dt.date(2026, 12, 2),
        dt.time(17, 15),
        ExtractionStatus.STRUCTURED,
    )
    api.json["/api/volumes/drafts"]["_embedded"]["content"] = [
        bill_row(amendmentsDeadline="2026-12-09T17:15:00")
    ]
    counts = amendments.read_window(today=dt.date(2026, 11, 24))
    assert counts.deadlines_moved == 1
    assert ExternalDeadline.objects.filter(kind=DeadlineKind.AMENDMENTS).count() == 2


def _events(items: list[dict], when: str = "2026-11-26T10:00:00") -> list[dict]:
    return [
        {
            "date": when[:10],
            "committeeSittings": [
                {
                    "committee": {"name": "Õiguskomisjon"},
                    "sittings": [
                        {
                            "uuid": str(uuid_module.uuid4()),
                            "sittingDateTime": when,
                            "agendaItems": items,
                        }
                    ],
                }
            ],
        }
    ]


AGENDA_ITEM = "5705058b-7679-4d1d-9498-4bcf205d767a"


def _agenda(api, when: str, items: list[dict] | None = None) -> None:
    api.json["/api/events"] = _events(
        items
        if items is not None
        else [
            {
                "uuid": AGENDA_ITEM,
                "title": "Äriseadustiku muutmise seaduse eelnõu (1082 SE I)",
                "relatedDraft": {"uuid": BILL, "mark": 1082},
            },
            {
                "uuid": str(uuid_module.uuid4()),
                "title": "Hasartmängumaksu seaduse muutmise seaduse eelnõu algatamine",
            },
            {"uuid": str(uuid_module.uuid4()), "title": "Info ja muud küsimused"},
        ],
        when,
    )


def test_agenda_items_link_by_the_structured_bill_and_mark_signals_and_routine(api):
    _agenda(api, "2026-11-26T10:00:00")
    now = timezone.make_aware(dt.datetime(2026, 11, 24, 3, 0))
    agendas.read_window(start=dt.date(2026, 11, 24), end=dt.date(2026, 12, 8), now=now)
    linked = CommitteeAgendaItem.objects.get(uuid=AGENDA_ITEM)
    assert linked.bill.uuid == uuid_module.UUID(BILL)
    assert CommitteeAgendaItem.objects.filter(signal=True).count() == 1
    assert CommitteeAgendaItem.objects.filter(routine=True).count() == 1


def test_a_moved_sitting_updates_the_item_and_keeps_the_old_time(api):
    now = timezone.make_aware(dt.datetime(2026, 11, 24, 3, 0))
    _agenda(api, "2026-11-26T10:00:00")
    agendas.read_window(start=dt.date(2026, 11, 24), end=dt.date(2026, 12, 8), now=now)
    _agenda(api, "2026-11-27T14:00:00")
    counts = agendas.read_window(start=dt.date(2026, 11, 24), end=dt.date(2026, 12, 8), now=now)
    item = CommitteeAgendaItem.objects.get(uuid=AGENDA_ITEM)
    assert counts.items_moved == 1
    assert item.state == AgendaState.MOVED
    assert item.previous_sitting_at.hour + 2 >= 10  # the old time is kept (UTC in the database)
    assert CommitteeAgendaItem.objects.filter(uuid=AGENDA_ITEM).count() == 1


def test_one_missing_reading_does_not_cancel_a_sitting_and_a_failed_reading_counts_for_nothing(api):
    now = timezone.make_aware(dt.datetime(2026, 11, 24, 3, 0))
    _agenda(api, "2026-11-26T10:00:00")
    agendas.read_window(start=dt.date(2026, 11, 24), end=dt.date(2026, 12, 8), now=now)
    api.fail["/api/events"] = FetchError("api.riigikogu.ee vastas 503", status=503)
    with pytest.raises(FetchError):
        agendas.read_window(start=dt.date(2026, 11, 24), end=dt.date(2026, 12, 8), now=now)
    assert CommitteeAgendaItem.objects.get(uuid=AGENDA_ITEM).missing_readings == 0
    del api.fail["/api/events"]
    _agenda(api, "2026-11-26T10:00:00", items=[])
    agendas.read_window(start=dt.date(2026, 11, 24), end=dt.date(2026, 12, 8), now=now)
    assert CommitteeAgendaItem.objects.get(uuid=AGENDA_ITEM).state != AgendaState.CANCELLED
    agendas.read_window(start=dt.date(2026, 11, 24), end=dt.date(2026, 12, 8), now=now)
    assert CommitteeAgendaItem.objects.get(uuid=AGENDA_ITEM).state == AgendaState.CANCELLED


# -- RK-4: stenograms and responses ---------------------------------------------------------


def test_stenogram_mentions_are_found_deterministically_and_bilateral_chambers_are_not_koda(api):
    bills.upsert(bills.facts(bill_row()))
    api.json["/api/steno/verbatims"] = [
        {
            "date": "2026-11-18T10:00:00",
            "title": "XV Riigikogu täiskogu istung",
            "link": "https://stenogrammid.riigikogu.ee/202611181000",
            "agendaItems": [
                {
                    "agendaItemUuid": str(uuid_module.uuid4()),
                    "title": "Äriseadustiku muutmise seaduse eelnõu (1082 SE) esimene lugemine",
                    "events": [
                        {
                            "type": "SPEECH",
                            "uuid": str(uuid_module.uuid4()),
                            "speaker": "Minister X",
                            "text": "Kaubandus-tööstuskoja ettepanek meile tuli. Ameerika Kaubanduskoda on teist meelt. Eesti Põllumajandus-Kaubanduskoda samuti.",
                        }
                    ],
                },
                {
                    "agendaItemUuid": "Infotund",
                    "title": "Infotund",
                    "events": [
                        {
                            "type": "SPEECH",
                            "uuid": str(uuid_module.uuid4()),
                            "speaker": "Liige Y",
                            "text": "Kaubanduskoja peadirektor ütles nii.",
                        }
                    ],
                },
            ],
        }
    ]
    counts = stenograms.read_days(start=dt.date(2026, 11, 18), end=dt.date(2026, 11, 18))
    assert counts.mentions_new == 2
    assert (
        StenogramMention.objects.get(category=MentionCategory.LEGISLATIVE).speaker == "Minister X"
    )
    assert StenogramMention.objects.filter(category=MentionCategory.OTHER).count() == 1
    stenograms.read_days(start=dt.date(2026, 11, 18), end=dt.date(2026, 11, 18))
    assert StenogramMention.objects.count() == 2


def test_after_the_deadline_a_missing_answer_needs_checking_never_not_answered(api, specialist):
    with_letter(api, parties=KODA_AMONG)
    discover()
    api.json[f"/api/volumes/drafts/{BILL}"] = {**bill_row(), "opinions": []}
    responses.check(today=dt.date(2026, 12, 1))
    invitation = Invitation.objects.get()
    assert invitation.response == ResponseCheck.NEEDS_CHECK
    assert ResponseCheck.NEEDS_CHECK.label == "Kutsele vastamine vajab kontrollimist"
    responses.settle(
        invitation,
        actor=specialist,
        answer=ResponseCheck.JOINT,
        note="Ühispöördumine Tööandjate Keskliiduga",
    )
    assert Invitation.objects.get().response == ResponseCheck.JOINT


def test_kodas_registered_opinion_settles_the_check(api):
    with_letter(api, parties=KODA_AMONG)
    discover()
    api.json[f"/api/volumes/drafts/{BILL}"] = {
        **bill_row(),
        "opinions": [
            {
                "uuid": OPINION,
                "title": "Eesti Kaubandus-Tööstuskoja arvamus eelnõu 1082 SE kohta",
                "date": "2026-11-20",
            }
        ],
    }
    responses.check(today=dt.date(2026, 12, 1))
    invitation = Invitation.objects.get()
    assert invitation.response == ResponseCheck.FOUND
    assert invitation.response_document.uuid == uuid_module.UUID(OPINION)


# -- the cycle ----------------------------------------------------------------------------


def test_the_cycle_does_nothing_but_record_disabled_when_switched_off(api):
    with override_settings(RIIGIKOGU_MONITORING=False, REGISTER_WATCH_FETCH=True):
        runs = cycle.run(today=dt.date(2026, 11, 24))
    assert {r.status for r in runs} == {RunStatus.DISABLED}
    assert api.calls == []


def test_one_failing_stage_does_not_stop_the_others_and_never_moves_its_checkpoint(api):
    api.fail["/api/documents"] = FetchError("api.riigikogu.ee vastas 503", status=503)
    api.json["/api/volumes/drafts"] = {"_embedded": {"content": []}, "page": {"totalPages": 1}}
    _agenda(api, "2026-11-26T10:00:00")
    api.json["/api/steno/verbatims"] = []
    with override_settings(RIIGIKOGU_MONITORING=True, REGISTER_WATCH_FETCH=True):
        runs = {r.stage: r for r in cycle.run(today=dt.date(2026, 11, 24))}
    assert runs[Stage.INVITATIONS].status == RunStatus.FAILED
    assert runs[Stage.AGENDAS].status == RunStatus.SUCCEEDED
    assert runs[Stage.ANALYSIS].status == RunStatus.DISABLED
    assert cycle.checkpoint(Stage.INVITATIONS) is None
    assert cycle.checkpoint(Stage.AGENDAS) is not None


def test_the_next_window_overlaps_the_last_successful_one(api):
    StageRun.objects.create(
        cycle=uuid_module.uuid4(),
        stage=Stage.INVITATIONS,
        status=RunStatus.SUCCEEDED,
        started_at=timezone.now(),
        window_start=dt.date(2026, 11, 10),
        window_end=dt.date(2026, 11, 20),
    )
    start, end = cycle.window(
        Stage.INVITATIONS, today=dt.date(2026, 11, 24), first_days=30, overlap=cycle.OVERLAP
    )
    assert (start, end) == (dt.date(2026, 11, 13), dt.date(2026, 11, 24))


def test_health_never_shows_a_failed_or_unrun_stage_as_ok(api):
    with override_settings(RIIGIKOGU_MONITORING=True, REGISTER_WATCH_FETCH=True):
        states = {h.stage: h.state for h in cycle.health()}
        assert states[Stage.INVITATIONS] == "never"
        StageRun.objects.create(
            cycle=uuid_module.uuid4(),
            stage=Stage.INVITATIONS,
            status=RunStatus.FAILED,
            started_at=timezone.now(),
        )
        states = {h.stage: h.state for h in cycle.health()}
        assert states[Stage.INVITATIONS] == "failed"
        assert states[Stage.ANALYSIS] == "disabled"


def test_an_unexpected_page_shape_is_an_error_not_an_empty_source():
    with pytest.raises(FetchError):
        client.rows({"unexpected": True})
    assert client.rows({"page": {"totalElements": 0}}) == []


# -- the shared ledger and 429 ------------------------------------------------------------


def test_429_waits_for_retry_after_and_counts_it(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(client, "sleep", waits.append)
    monkeypatch.setattr(client, "_wait_turn", lambda path: None)
    answers = [FetchError("api.riigikogu.ee vastas 429", status=429, retry_after=7.0), {"ok": True}]

    def fake(host, path, params=None):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(http, "get_json", fake)
    budget = client.Budget(limit=5)
    assert client.get_json("/api/x", budget=budget) == {"ok": True}
    assert waits == [7.0]
    assert (budget.spent, budget.rate_limited) == (2, 1)


def test_a_404_is_the_answer_and_is_not_retried(monkeypatch):
    monkeypatch.setattr(client, "_wait_turn", lambda path: None)
    calls = []

    def fake(host, path, params=None):
        calls.append(path)
        raise FetchError("api.riigikogu.ee vastas 404", status=404)

    monkeypatch.setattr(http, "get_json", fake)
    with pytest.raises(FetchError):
        client.get_json("/api/x")
    assert len(calls) == 1


def test_the_budget_stops_a_stage(monkeypatch):
    monkeypatch.setattr(client, "_wait_turn", lambda path: None)
    monkeypatch.setattr(http, "get_json", lambda host, path, params=None: {})
    budget = client.Budget(limit=1)
    client.get_json("/a", budget=budget)
    with pytest.raises(client.BudgetExceeded):
        client.get_json("/b", budget=budget)


def test_the_ledger_spaces_calls_and_limits_one_path_per_minute(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(client, "sleep", waits.append)
    now = timezone.now()
    for second in range(client.PER_PATH_PER_MINUTE):
        ApiCall.objects.create(path="/api/events", at=now - dt.timedelta(seconds=50 - second))
    ApiCall.objects.create(path="/api/other", at=now - dt.timedelta(seconds=0.2))

    clock = iter([now, now + dt.timedelta(seconds=12)])
    monkeypatch.setattr(client.timezone, "now", lambda: next(clock))
    client._wait_turn("/api/events")
    assert waits and waits[0] >= 9  # until the oldest of the minute's eleven calls is a minute old
