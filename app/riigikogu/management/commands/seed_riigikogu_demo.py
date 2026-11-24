# ruff: noqa: E501  (a fixture of invented sentences, kept one per line for review)
"""A fictional Riigikogu for review screenshots and browser tests (ADR 0224, 0225).

    manage.py seed_riigikogu_demo

Refuses to run where ``APPLICATION_ENVIRONMENT`` is ``production``. Every bill,
letter, sitting and quote is invented (the «demo» prefix on every UUID); the
model analyses come from `MockModel` and are flagged ``mocked``, so every surface
labels them «Näidis». It makes its own two Teemad («Näidisteema: …»), owned by an
existing specialist, so no other Teema of the world it runs in gains a bill.
"""

from __future__ import annotations

import datetime as dt
import uuid as uuid_module
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from app.riigikogu import analysis, outcomes, versions
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
    TextStatus,
)
from app.riigikogu.models import (
    Bill,
    CommitteeAgendaItem,
    ExternalDeadline,
    Invitation,
    ParliamentDocument,
    StageRun,
    StenogramMention,
)

NS = uuid_module.UUID("0d3e0000-0000-4000-8000-000000000000")


def demo_uuid(name: str) -> uuid_module.UUID:
    return uuid_module.uuid5(NS, name)


OLD_TEXT = """§ 1. Väikeettevõtluse aruandluse seaduse muutmine
Seaduse § 4 lõige 2 sõnastatakse järgmiselt: aruanne esitatakse 30 päeva jooksul.
§ 2. Jõustumine
Seadus jõustub 1. jaanuaril 2027."""
NEW_TEXT = """§ 1. Väikeettevõtluse aruandluse seaduse muutmine
Seaduse § 4 lõige 2 sõnastatakse järgmiselt: aruanne esitatakse 60 päeva jooksul.
§ 2. Üleminekusäte
Enne 2027. aastat alustanud ettevõtja esitab esimese aruande 2028. aastal.
§ 3. Jõustumine
Seadus jõustub 1. juulil 2027."""
KODA_OPINION = """Koda teeb ettepaneku pikendada aruande esitamise tähtaega 60 päevani.
Samuti palume lükata seaduse jõustumine vähemalt kuus kuud edasi, et ettevõtjad jõuaksid süsteemid kohandada."""
OTHER_OPINIONS = {
    "Näidisliit": "Toetame aruandluse lihtsustamist. Aruande esitamise tähtaeg võiks olla 60 päeva.",
    "Teine näidisühendus": "Jõustumine 1. jaanuaril 2027 on sobiv, edasilükkamist ei ole vaja.",
}
AMENDMENT_LIST = """1. Muuta eelnõu § 1 ja sõnastada see järgmiselt: aruanne esitatakse 60 päeva jooksul.
Selgitus: Arvestatakse huvirühmade ettepanekut.
Eesti Kaubandus-Tööstuskoda, Näidisliit
Juhtivkomisjon: ARVESTADA TÄIELIKULT
2. Täiendada eelnõu üleminekusättega.
Selgitus: Vajalik ettevõtjatele kohanemisajaks.
Majanduskomisjon
Juhtivkomisjon: ARVESTADA TÄIELIKULT"""


def _positions_answer(prompt: str) -> dict[str, Any]:
    first, second = (str(demo_uuid(f"opinion-{n}")) for n in OTHER_OPINIONS)
    return {
        "issues": [
            {
                "issue": "Aruande esitamise tähtaeg",
                "provision": "§ 1",
                "positions": [
                    {
                        "organisation": "Näidisliit",
                        "stance": "Tähtaeg 60 päeva",
                        "argument": "Aruandluse lihtsustamine",
                        "quote": "Aruande esitamise tähtaeg võiks olla 60 päeva.",
                        "document": first,
                        "relation": "SAME",
                    },
                    {
                        "organisation": "Teine näidisühendus",
                        "stance": "Ei käsitle tähtaega",
                        "quote": "See lause allikas ei esine ja jäetakse välja.",
                        "document": second,
                        "relation": "NO_POSITION",
                    },
                ],
            },
            {
                "issue": "Jõustumise aeg",
                "provision": "§ 2",
                "positions": [
                    {
                        "organisation": "Teine näidisühendus",
                        "stance": "1. jaanuar 2027 sobib",
                        "quote": "Jõustumine 1. jaanuaril 2027 on sobiv, edasilükkamist ei ole vaja.",
                        "document": second,
                        "relation": "DIFFERENT",
                    }
                ],
            },
        ]
    }


def _versions_answer(prompt: str) -> dict[str, Any]:
    return {
        "changes": [
            {
                "provision": "§ 1.",
                "explanation": "Aruande tähtaeg pikeneb 30 päevalt 60 päevale.",
                "relevance": "Vastab koja ettepanekule.",
            },
            {
                "provision": "§ 2.",
                "explanation": "Jõustumissätte asemele tuli üleminekusäte.",
                "relevance": "Annab ettevõtjatele kohanemisaega.",
                "uncertain": True,
            },
            {
                "provision": "§ 9.",
                "explanation": "Seda sätet võrdlus ei leidnud; selgitus jäetakse välja.",
            },
        ]
    }


def _outcomes_answer(prompt: str) -> dict[str, Any]:
    return {
        "proposals": [
            {
                "proposal": "Lükata seaduse jõustumine vähemalt kuus kuud edasi",
                "provision": "§ 3",
                "outcome": "REFLECTED",
                "quote": "Seadus jõustub 1. juulil 2027.",
                "document": str(demo_uuid("text-2")),
                "explanation": "Jõustumine nihkus 1. jaanuarilt 1. juulile 2027.",
            }
        ]
    }


class Command(BaseCommand):
    help = "Loob ülevaatuseks väljamõeldud Riigikogu andmed (näidis, mitte päris andmed)."

    def handle(self, *args: Any, **options: Any) -> None:
        if getattr(settings, "APPLICATION_ENVIRONMENT", "") == "production":
            raise CommandError("Näidisandmeid ei looda tootmises.")
        from app.register_watch.models import TargetOrigin, WatchSource, WatchTarget

        matters = self._teemad()
        now = timezone.now()
        today = timezone.localdate()
        with transaction.atomic():
            bill = self._bill(
                "aruandlus",
                1201,
                "Väikeettevõtluse aruandluse seaduse muutmise seadus",
                "Majanduskomisjon",
                now,
                today + dt.timedelta(days=6),
            )
            other = self._bill(
                "energia",
                1202,
                "Näidisenergia seaduse muutmise seadus",
                "Keskkonnakomisjon",
                now,
                today + dt.timedelta(days=11),
            )
            free = self._bill(
                "vaba", 1203, "Näidistarbimise seaduse muutmise seadus", "Õiguskomisjon", now, None
            )
            for matter, tied in ((matters[0], bill), (matters[1], other)):
                WatchTarget.objects.update_or_create(
                    matter=matter,
                    source=WatchSource.RIIGIKOGU,
                    identifier=str(tied.uuid),
                    defaults={
                        "label": tied.label,
                        "origin": TargetOrigin.LINKED,
                        "url": tied.url,
                        "active": True,
                    },
                )
            # Invitations: verified, needs checking, changed, unlinked, earlier.
            self._invitation(
                "kutse-1",
                bill,
                "Majanduskomisjon",
                now,
                today - dt.timedelta(days=2),
                today + dt.timedelta(days=5),
                ExtractionStatus.FOUND,
                f"Kui Teil on ettepanekuid, palun esitage need majanduskomisjonile hiljemalt {(today + dt.timedelta(days=5)):%d.%m.%Y}.",
            )
            self._invitation(
                "kutse-2",
                other,
                "Keskkonnakomisjon",
                now,
                today - dt.timedelta(days=1),
                None,
                ExtractionStatus.CHECK,
                "Palume vastata reedeks.",
                doubt="tähtajaks on nädalapäev («reedeks»), mitte kuupäev",
            )
            changed = self._invitation(
                "kutse-3",
                other,
                "Keskkonnakomisjon",
                now,
                today - dt.timedelta(days=9),
                today + dt.timedelta(days=2),
                ExtractionStatus.FOUND,
                "Palume arvamust hiljemalt 20.11.2026.",
            )
            self._move(changed, today + dt.timedelta(days=9), "Pikendame varasemat tähtaega.")
            self._invitation(
                "kutse-4",
                free,
                "Õiguskomisjon",
                now,
                today - dt.timedelta(days=1),
                today + dt.timedelta(days=12),
                ExtractionStatus.FOUND,
                f"Teil on võimalus avaldada arvamust hiljemalt {(today + dt.timedelta(days=12)):%d.%m.%Y}.",
                unlinked=True,
            )
            past = self._invitation(
                "kutse-5",
                bill,
                "Majanduskomisjon",
                now,
                today - dt.timedelta(days=40),
                today - dt.timedelta(days=20),
                ExtractionStatus.FOUND,
                "Palume arvamust hiljemalt varasemal kuupäeval.",
            )
            past.review = InvitationReview.NO_ACTION
            past.response = ResponseCheck.NEEDS_CHECK
            past.decided_at = now - dt.timedelta(days=30)
            past.save()
            # Committee agendas.
            for name, item_bill, title, days, signal in (
                (
                    "istung-1",
                    bill,
                    "Väikeettevõtluse aruandluse seaduse muutmise seaduse eelnõu (1201 SE) II lugemiseks ettevalmistamine",
                    3,
                    False,
                ),
                (
                    "istung-2",
                    other,
                    "Näidisenergia seaduse muutmise seaduse eelnõu (1202 SE)",
                    8,
                    False,
                ),
                (
                    "istung-3",
                    None,
                    "Näidismaksu seaduse muutmise seaduse eelnõu algatamine",
                    5,
                    True,
                ),
                ("istung-4", None, "Info ja muud küsimused", 5, False),
            ):
                CommitteeAgendaItem.objects.update_or_create(
                    uuid=demo_uuid(name),
                    defaults={
                        "committee": "Majanduskomisjon"
                        if item_bill is not other
                        else "Keskkonnakomisjon",
                        "sitting_at": now + dt.timedelta(days=days),
                        "title": title,
                        "bill": item_bill,
                        "bill_mark": item_bill.label if item_bill else "",
                        "signal": signal,
                        "routine": name == "istung-4",
                        "state": AgendaState.MOVED if name == "istung-2" else AgendaState.SCHEDULED,
                        "previous_sitting_at": now + dt.timedelta(days=days - 1)
                        if name == "istung-2"
                        else None,
                        "first_seen_at": now,
                        "last_seen_at": now,
                    },
                )
            # Mentions.
            StenogramMention.objects.update_or_create(
                key="demo:mention-1",
                defaults={
                    "sitting_on": today - dt.timedelta(days=3),
                    "sitting_title": "Täiskogu istung",
                    "agenda_title": "Väikeettevõtluse aruandluse seaduse muutmise seaduse eelnõu (1201 SE) esimene lugemine",
                    "speaker": "Näidisminister",
                    "quote": "Kaubanduskoja ettepanek tähtaja pikendamiseks on komisjonis arutusel.",
                    "context": "",
                    "category": MentionCategory.LEGISLATIVE,
                    "bill": bill,
                    "url": "https://stenogrammid.riigikogu.ee/",
                },
            )
            StenogramMention.objects.update_or_create(
                key="demo:mention-2",
                defaults={
                    "sitting_on": today - dt.timedelta(days=6),
                    "sitting_title": "Infotund",
                    "agenda_title": "Infotund",
                    "speaker": "Näidisliige",
                    "quote": "Kaubanduskoja hinnangul on maksukoormus kasvanud.",
                    "context": "",
                    "category": MentionCategory.OTHER,
                    "url": "https://stenogrammid.riigikogu.ee/",
                },
            )
            for n in OTHER_OPINIONS:
                ParliamentDocument.objects.update_or_create(
                    uuid=demo_uuid(f"opinion-{n}"),
                    defaults={
                        "kind": DocumentKind.OTHER_OPINION,
                        "title": f"{n} arvamus eelnõu 1201 SE kohta",
                        "bill": bill,
                        "registered_on": today - dt.timedelta(days=10),
                        "first_seen_at": now,
                        "last_seen_at": now,
                        "text_status": TextStatus.READ,
                    },
                )
            for stage in Stage.values:
                StageRun.objects.create(
                    cycle=demo_uuid("tsykkel"),
                    stage=stage,
                    status=RunStatus.DISABLED
                    if stage == Stage.ANALYSIS
                    else (RunStatus.PARTIAL if stage == Stage.PROTOCOLS else RunStatus.SUCCEEDED),
                    started_at=now,
                    finished_at=now,
                    window_start=today - dt.timedelta(days=7),
                    window_end=today,
                    counts={"näidis": True},
                    errors=[{"viga": "Näidis: üks protokoll oli skaneeritud PDF"}]
                    if stage == Stage.PROTOCOLS
                    else [],
                    requests=12,
                )
        self._analyses(bill)
        self.stdout.write("Riigikogu näidisandmed loodud (väljamõeldud, mudel simuleeritud).")

    def _teemad(self) -> list[Any]:
        from django.contrib.auth import get_user_model

        from app.core.authorization import may_write_business_content
        from app.matters.models import Matter
        from app.matters.services import create_matter

        users = get_user_model().objects.filter(is_active=True).order_by("date_joined", "pk")
        owner = next((u for u in users if may_write_business_content(u)), None)
        if owner is None:
            raise CommandError("Vaja on vähemalt üht juristi (näiteks seed_e2e_data).")
        found = []
        for title in ("Näidisteema: väikeettevõtluse aruandlus", "Näidisteema: näidisenergia"):
            matter = Matter.objects.filter(title=title, deleted_at__isnull=True).first()
            if matter is None:
                matter = create_matter(title=title, owner=owner, actor=owner)
            found.append(matter)
        return found

    def _bill(
        self, name: str, mark: int, title: str, committee: str, now: Any, deadline: dt.date | None
    ) -> Bill:
        from zoneinfo import ZoneInfo

        moment = (
            dt.datetime.combine(deadline, dt.time(17, 15), tzinfo=ZoneInfo("Europe/Tallinn"))
            if deadline
            else None
        )
        bill, _ = Bill.objects.update_or_create(
            uuid=demo_uuid(f"bill-{name}"),
            defaults={
                "mark": mark,
                "type_code": "SE",
                "membership": 15,
                "title": title,
                "url": "https://www.riigikogu.ee/tegevus/eelnoud/",
                "leading_committee": committee,
                "amendments_deadline": moment,
                "fetched_at": now,
            },
        )
        if deadline:
            ExternalDeadline.objects.filter(bill=bill, kind=DeadlineKind.AMENDMENTS).delete()
            ExternalDeadline.objects.create(
                kind=DeadlineKind.AMENDMENTS,
                bill=bill,
                due_on=deadline,
                due_time=dt.time(17, 15),
                extraction=ExtractionStatus.STRUCTURED,
                source_url=bill.url,
            )
        return bill

    def _invitation(
        self,
        name: str,
        bill: Bill,
        committee: str,
        now: Any,
        letter: dt.date,
        due: dt.date | None,
        status: str,
        evidence: str,
        *,
        doubt: str = "",
        unlinked: bool = False,
    ) -> Invitation:
        document, _ = ParliamentDocument.objects.update_or_create(
            uuid=demo_uuid(name),
            defaults={
                "kind": DocumentKind.INVITATION,
                "koda_role": KodaRole.ADDRESSEE,
                "title": f"Kaasamiskiri eelnõu {bill.label} kohta",
                "direction": "VALJA",
                "parties": "Eesti Kaubandus-Tööstuskoda, Näidisliit",
                "bill": bill,
                "letter_date": letter,
                "registered_on": letter,
                "url": "https://api.riigikogu.ee/",
                "file_uuid": demo_uuid(f"{name}-file"),
                "text_status": TextStatus.READ,
                "first_seen_at": now,
                "last_seen_at": now,
            },
        )
        ExternalDeadline.objects.filter(document=document).delete()
        ExternalDeadline.objects.create(
            kind=DeadlineKind.OPINION_REQUEST,
            bill=bill,
            document=document,
            due_on=due,
            extraction=status,
            evidence=evidence,
            doubt=doubt,
            source_url=f"https://api.riigikogu.ee/api/files/{demo_uuid(name + '-file')}/download",
        )
        invitation, _ = Invitation.objects.update_or_create(
            document=document,
            defaults={
                "committee": committee,
                "review": InvitationReview.NEW,
                "response": ResponseCheck.NOT_DUE,
            },
        )
        if unlinked:
            from app.register_watch.models import CandidateItem, TopicCandidate

            candidate, _ = TopicCandidate.objects.get_or_create(
                title=bill.title, defaults={"match_key": "näidistarbimise"}
            )
            CandidateItem.objects.get_or_create(
                source="RIIGIKOGU",
                external_id=f"draft:{bill.uuid}",
                defaults={"candidate": candidate, "title": bill.title, "url": bill.url},
            )
            invitation.candidate = candidate
            invitation.save()
        return invitation

    def _move(self, invitation: Invitation, new_day: dt.date, evidence: str) -> None:
        current = ExternalDeadline.objects.get(document=invitation.document, state=FactState.ACTIVE)
        current.state = FactState.SUPERSEDED
        current.save()
        ExternalDeadline.objects.create(
            kind=DeadlineKind.OPINION_REQUEST,
            bill=current.bill,
            document=invitation.document,
            due_on=new_day,
            extraction=ExtractionStatus.FOUND,
            evidence=evidence,
            supersedes=current,
            source_url=current.source_url,
        )

    def _analyses(self, bill: Bill) -> None:
        sources = [
            analysis.Source(str(demo_uuid(f"opinion-{n}")), text, f"demo-{n}", n)
            for n, text in OTHER_OPINIONS.items()
        ]
        koda = analysis.Source(
            str(demo_uuid("koda-opinion")), KODA_OPINION, "demo-koda", "Koja arvamus"
        )
        analysis.run(
            "POSITIONS", [koda, *sources], bill=bill, model=analysis.MockModel(_positions_answer)
        )
        old = analysis.Source(str(demo_uuid("text-1")), OLD_TEXT, "demo-old", "1201 SE I lugemine")
        new = analysis.Source(str(demo_uuid("text-2")), NEW_TEXT, "demo-new", "1201 SE II lugemine")
        versions.explain(bill=bill, old=old, new=new, model=analysis.MockModel(_versions_answer))
        listing = analysis.Source(
            str(demo_uuid("mede")), AMENDMENT_LIST, "demo-mede", "Muudatusettepanekute loetelu"
        )
        outcomes.analyse(
            bill=bill,
            koda_opinion=koda,
            revised_text=new,
            amendment_list=listing,
            stage="II lugemiseks",
            model=analysis.MockModel(_outcomes_answer),
        )
