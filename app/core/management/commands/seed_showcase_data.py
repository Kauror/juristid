"""The six-package showcase world: realistic, synthetic, TEST-classified.

`seed_dev_data` builds a developer's sandbox and `seed_e2e_data` builds the
browser suite's fixture world; neither is a demonstration. This command seeds
the six showcase packages the department opens to *read* — six different lawyer
workflows at realistic text length, not six copies of one populated Matter.

The contract, explicitly:

- **Everything business it writes is `Matter.data_class = TEST`** and invented.
  Reference data (organisations it names) goes through
  `get_or_create_organisation`, the same canonical door the product uses.
- **It cannot touch a business database.** If any non-TEST Matter exists the
  command refuses before writing anything — there is no flag around that rule.
- **Production intent is explicit.** On an instance with `REAL_DATA_ALLOWED`
  set it refuses unless called with `--operator-intent showcase-world`, so the
  one environment where the flag means something cannot be seeded by habit.
  The global protections are untouched (`validate_test_classification`, the
  `matters_test_data_is_native` constraint, `purge_test_data`'s read-only
  stance all stay exactly as they are).
- **Rerunning is safe.** Each package is identified by its canonical titles;
  a package whose Matter already exists is skipped whole, and each package is
  one transaction, so a crashed run leaves no half-package behind.
- **Provenance is on every Matter**: the creation event's payload carries
  ``operation=seed_showcase_data``, the ``seed_version`` and the ``package``
  key, so "which package made this row" stays answerable from the record.
- Current and future dates are relative to `timezone.localdate()`, so the
  showcase reads sensibly whenever it is seeded; history keeps fixed dates
  because history does not age.

Files are tiny synthetic PDFs/texts written here, each carrying a line saying
it is synthetic. No real Koda document is ever used.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from app.accounts.enums import UserRole
from app.accounts.models import User
from app.core.enums import Visibility
from app.documents.services import rename_document
from app.matters import workspace as ws
from app.matters.enums import (
    EngagementKind,
    ExternalPositionProvenance,
    MatterDataClass,
    MatterOrigin,
    ProceduralLinkKind,
    RecordMode,
    ResponseDeadlineChange,
    WebsiteOverviewKind,
)
from app.matters.models import Matter
from app.matters.response_deadlines import (
    change_response_deadline,
    deadline_revision,
    request_response_deadline,
)
from app.matters.services import close_matter, create_matter, set_brief_summary
from app.organisations.services import get_or_create_organisation
from app.related_materials.services import link_related_matters
from app.workflow.enums import DatePrecision, Disposition
from app.workflow.models import StageVocabulary
from app.workflow.services import add_planned_action, set_next_action_for_new_work

SEED_VERSION = "2026-10-08.1"
OPERATOR_INTENT = "showcase-world"
MARK = "SÜNTEETILINE NÄIDIS / TEST DATA — Juristid showcase"

D = dt.date

# The six canonical titles. The first title of each package is its identity:
# a TEST Matter carrying it means the package exists and the rerun skips it.
TITLE_KLIIMA = "NÄIDIS — Kliimakindla majanduse seaduse eelnõu"
TITLE_JAATME = "NÄIDIS — Jäätmereform"
TITLE_TAKS = "NÄIDIS — TAKS (teadus- ja arendustegevuse korralduse seadus)"
TITLE_TAIKS = "NÄIDIS — TAIKS (teadus- ja arendustegevuse ning innovatsiooni korralduse seadus)"
TITLE_TOOTASU = "NÄIDIS — Töötasu arestimise akt"
TITLE_KUTSE = "NÄIDIS — Kutse- ja oskusseaduse eelnõu"
TITLE_PIIRATUD = (
    "NÄIDIS — Piiratud testteema — konfidentsiaalne ettevõtjate tagasiside "
    "maksukorralduse seaduse eelnõu mõjude kohta"
)


# ---------------------------------------------------------------------------
# synthetic files
# ---------------------------------------------------------------------------


def _pdf(name: str, *lines: str) -> SimpleUploadedFile:
    """A tiny but valid one-page PDF whose text says it is synthetic."""
    text = [MARK, *lines]
    ops = ["BT", "/F1 11 Tf", "50 780 Td", "14 TL"]
    for i, line in enumerate(text):
        safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append(("" if i == 0 else "T* ") + f"({safe}) Tj")
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1", "replace")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return SimpleUploadedFile(name, bytes(out), content_type="application/pdf")


def _txt(name: str, *lines: str) -> SimpleUploadedFile:
    body = "\n".join([MARK, *lines]) + "\n"
    return SimpleUploadedFile(name, body.encode("utf-8"), content_type="text/plain")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _stage(key: str) -> StageVocabulary:
    return StageVocabulary.objects.get(key=key)


def _org(name: str) -> Any:
    return get_or_create_organisation(name=name).organisation


def _fresh(matter: Matter) -> Matter:
    return Matter.objects.get(pk=matter.pk)


@dataclass
class World:
    """What one run works with: the people, the clock, the output."""

    owner: User
    restricted_owner: User
    today: dt.date
    log: Callable[[str], None]
    made: list[Matter] = field(default_factory=list)

    def matter(
        self,
        title: str,
        package: str,
        summary: str,
        *,
        owner: User | None = None,
        visibility: str = Visibility.NORMAL,
    ) -> Matter:
        actor = owner or self.owner
        matter = create_matter(
            title=title,
            actor=actor,
            owner=actor,
            assign_reference=False,
            data_class=MatterDataClass.TEST,
            origin=MatterOrigin.NATIVE,
            record_mode=RecordMode.FULL,
            visibility=visibility,
            provenance={
                "operation": "seed_showcase_data",
                "seed_version": SEED_VERSION,
                "package": package,
            },
        )
        set_brief_summary(matter=matter, value=summary, actor=actor)
        self.made.append(matter)
        return matter

    def development(
        self,
        matter: Matter,
        title: str,
        day: dt.date,
        *,
        to: str | None = None,
        note: str = "",
        uploads: tuple[Any, ...] = (),
        author: User | None = None,
    ) -> Any:
        return ws.add_procedural_development(
            matter=matter,
            author=author or self.owner,
            title=title,
            occurred_on=day,
            note=note,
            stage=_stage(to) if to else None,
            uploads=list(uploads),
        )

    def opinion(
        self,
        matter: Matter,
        recipient: Any,
        day: dt.date,
        name: str,
        summary: str,
        **kwargs: Any,
    ) -> Any:
        return ws.add_matter_koda_opinion(
            matter=matter,
            author=self.owner,
            upload=_pdf(name, f"Koja arvamus (näidis), {day.isoformat()}", summary),
            recipients=[recipient],
            sent_on=day,
            summary=summary,
            **kwargs,
        )


# ---------------------------------------------------------------------------
# the six packages
# ---------------------------------------------------------------------------


def _build_kliima(w: World) -> None:
    """P1 — current work: a dated current action, three dated planned rows
    (two of them long enough to wrap), a document, an important date."""
    t = w.today
    m = w.matter(
        TITLE_KLIIMA,
        "P1-kliima",
        "Näidisteema: kliimakindla majanduse seadus (endine kliimaseadus) — VTK, kaks "
        "eelnõu versiooni, valitsus ja Riigikogu 928 SE. Sünteetiline testandmestik.",
    )
    klim = _org("Kliimaministeerium")
    w.development(
        m,
        "Kliimaministeerium saatis kliimakindla majanduse seaduse VTK",
        D(2023, 9, 25),
        to="consultation",
    )
    ws.add_matter_website_overview(
        matter=m,
        author=w.owner,
        published_on=D(2023, 10, 2),
        url="https://www.koda.ee/naidis/kliimaseaduse-vtk",
        title="Kliimaseaduse VTK — anna tagasisidet",
        kind=WebsiteOverviewKind.OVERVIEW.value,
    )
    round_ = ws.add_matter_engagement(
        matter=m,
        author=w.owner,
        audience="Liikmed (e-kiri ja küsitlus)",
        kind=EngagementKind.SURVEY.value,
        occurred_on=D(2024, 8, 12),
        feedback_deadline=D(2024, 9, 2),
        note="Eelnõu esimese versiooni küsitlus.",
    ).record
    ws.add_matter_external_position(
        matter=m,
        author=w.owner,
        organisation=_org("NÄIDIS Energeetikaettevõtete Liit"),
        provenance=ExternalPositionProvenance.RECEIVED.value,
        stated_on=D(2024, 8, 30),
        summary="Toetame eesmärki, kuid vahe-eesmärgid vajavad mõjuanalüüsi.",
        engagement=round_,
        source_is_member=True,
    )
    ws.add_engagement_feedback(
        engagement=round_,
        author=w.owner,
        feedback_received="Vastas mitu liiget; peamine mure vahe-eesmärkide jäikus.",
    )
    w.opinion(
        m,
        klim,
        D(2024, 9, 13),
        "NAIDIS-arvamus-kliimaseadus-2024.pdf",
        "Koja arvamus eelnõu esimesele versioonile.",
    )
    w.development(m, "Eelnõu saadeti valitsusse", D(2026, 5, 19), to="government")
    w.development(
        m, "Riigikogu menetlusse 928 SE; esimene lugemine lõppes", D(2026, 6, 9), to="parliament"
    )
    ws.add_matter_procedural_link(
        matter=m,
        author=w.owner,
        kind=ProceduralLinkKind.RIIGIKOGU.value,
        url="https://www.riigikogu.ee/naidis/928-se",
        label="928 SE (näidis)",
    )
    # An answered deadline in the history: the committee asked, Koda answered.
    change_response_deadline(matter=m, deadline=t - dt.timedelta(days=90), actor=w.owner)
    w.opinion(
        _fresh(m),
        _org("Riigikogu"),
        t - dt.timedelta(days=95),
        "NAIDIS-arvamus-928SE.pdf",
        "Koja ettepanekud 928 SE muutmiseks.",
        answers_deadline=deadline_revision(_fresh(m)),
    )
    ws.add_matter_important_date(
        matter=m,
        author=w.owner,
        title="Muudatusettepanekute tähtaeg (928 SE, teine lugemine)",
        date_value=t + dt.timedelta(days=21),
        period_end=t + dt.timedelta(days=21),
        date_precision=DatePrecision.EXACT.value,
    )
    set_next_action_for_new_work(
        matter=m,
        text="Jälgi teist lugemist ja hinda muudatusettepanekute arvestamist",
        target_date=t + dt.timedelta(days=3),
        actor=w.owner,
    )
    add_planned_action(
        matter=m,
        text=(
            "Koosta liikmetele vahekokkuvõte teise lugemise muudatusettepanekutest ja "
            "sellest, millised Koja ettepanekud on arvestatud, millised osaliselt ning "
            "millised on tagasi lükatud koos komisjoni põhjendustega"
        ),
        target_date=t + dt.timedelta(days=7),
        actor=w.owner,
    )
    add_planned_action(
        matter=m,
        text=(
            "Valmista ette Koja täiendav arvamus juhuks, kui vahe-eesmärkide paindlikkuse "
            "muudatusettepanek teisel lugemisel läbi ei lähe — kaasa eelnevalt "
            "energeetika- ja tööstusettevõtete esindajad ning täpsusta mõjuhinnangu arvud"
        ),
        target_date=t + dt.timedelta(days=14),
        actor=w.owner,
    )
    add_planned_action(
        matter=m,
        text="Osale majanduskomisjoni istungil",
        target_date=t + dt.timedelta(days=21),
        actor=w.owner,
    )


def _build_jaatme(w: World) -> None:
    """P2 — active consultation: published Ülevaade, open Kaasamine with a
    future feedback deadline, Smaily and Alchemer links, an attachment, no
    feedback yet."""
    t = w.today
    m = w.matter(
        TITLE_JAATME,
        "P2-jaatme",
        "Näidisteema: jäätmeseaduse ja pakendiseaduse muutmise eelnõu — käimasolev "
        "liikmete kaasamine, tagasisidet ootame. Sünteetiline testandmestik.",
    )
    w.development(
        m,
        "Kliimaministeerium saatis jäätmereformi eelnõu kooskõlastusringile",
        t - dt.timedelta(days=30),
        to="consultation",
    )
    overview_url = "https://www.koda.ee/naidis/jaatmereform-tagasiside"
    ws.add_matter_website_overview(
        matter=m,
        author=w.owner,
        published_on=t - dt.timedelta(days=7),
        url=overview_url,
        title="Jäätmereformi eelnõu — anna tagasisidet",
        kind=WebsiteOverviewKind.OVERVIEW.value,
    )
    ws.add_matter_engagement(
        matter=m,
        author=w.owner,
        audience="Liikmed (e-kiri ja küsitlus)",
        kind=EngagementKind.SURVEY.value,
        occurred_on=t - dt.timedelta(days=5),
        feedback_deadline=t + dt.timedelta(days=10),
        smaily_url="https://naidis.sendsmaily.net/kampaania/jaatmereform",
        alchemer_url="https://app.alchemer.eu/s3/naidis-jaatmereform",
        url=overview_url,
        note="Küsitlus pakendiaruandluse halduskoormuse kohta.",
        uploads=[
            _pdf(
                "NAIDIS-jaatmereform-eelnou.pdf",
                "Eelnõu tutvustus liikmetele (näidis).",
            )
        ],
        record_overview=True,
    )
    set_next_action_for_new_work(
        matter=m,
        text="Koosta liikmete tagasiside kokkuvõte ja Koja arvamuse eelnõu",
        target_date=t + dt.timedelta(days=12),
        actor=w.owner,
    )


def _build_taks_taiks(w: World) -> None:
    """P3 — long procedure: a closed predecessor superseded by an open
    successor, several stage transitions, history spanning years."""
    t = w.today
    taiks = w.matter(
        TITLE_TAIKS,
        "P3-taks-taiks",
        "Näidisteema: teadus- ja arendustegevuse ning innovatsiooni korralduse seaduse "
        "eelnõu (554 SE) — eelmise koosseisu 773 SE järeltulija. Sünteetiline testandmestik.",
    )
    htm = _org("Haridus- ja Teadusministeerium")
    w.development(taiks, "Uus eelnõu (TAIKS) kooskõlastusringil", D(2024, 6, 12), to="consultation")
    w.opinion(
        taiks,
        htm,
        D(2024, 7, 5),
        "NAIDIS-arvamus-TAIKS.pdf",
        "Koja arvamus uuele eelnõule.",
    )
    w.development(taiks, "Valitsus saatis 554 SE Riigikokku", D(2024, 12, 12), to="government")
    w.development(
        taiks, "Riigikogu menetlusse 554 SE; esimene lugemine", D(2025, 6, 17), to="parliament"
    )
    ws.add_matter_procedural_link(
        matter=taiks,
        author=w.owner,
        kind=ProceduralLinkKind.RIIGIKOGU.value,
        url="https://www.riigikogu.ee/naidis/554-se",
        label="554 SE (näidis)",
    )
    set_next_action_for_new_work(
        matter=taiks,
        text="Jälgi 554 SE teist lugemist ja teavita liikmeid jõustumise ajakavast",
        target_date=t + dt.timedelta(days=14),
        actor=w.owner,
    )

    taks = w.matter(
        TITLE_TAKS,
        "P3-taks-taiks",
        "Näidisteema: teadus- ja arendustegevuse korralduse seaduse eelnõu (773 SE), mis "
        "langes Riigikogu koosseisu vahetudes menetlusest välja. Töö jätkub TAIKS eelnõu "
        "all. Sünteetiline testandmestik.",
    )
    w.development(taks, "HTM küsis ettepanekuid TAKS muutmiseks", D(2020, 4, 30), to="idea")
    w.development(taks, "VTK kooskõlastusringil", D(2021, 6, 14), to="consultation")
    round_ = ws.add_matter_engagement(
        matter=taks,
        author=w.owner,
        audience="Liikmed",
        kind=EngagementKind.OTHER.value,
        occurred_on=D(2021, 6, 17),
        feedback_deadline=D(2021, 7, 2),
    ).record
    ws.add_matter_external_position(
        matter=taks,
        author=w.owner,
        organisation=_org("NÄIDIS IKT ettevõtete liit"),
        provenance=ExternalPositionProvenance.RECEIVED.value,
        stated_on=D(2021, 7, 1),
        summary="TAKS peab hõlmama ka ettevõtete arendustegevust.",
        engagement=round_,
        source_is_member=True,
    )
    ws.add_engagement_feedback(
        engagement=round_, author=w.owner, feedback_received="20 seisukohta."
    )
    w.opinion(taks, htm, D(2021, 7, 28), "NAIDIS-arvamus-VTK.pdf", "Koja arvamus TAKS VTK kohta.")
    w.development(taks, "Riigikogu menetlusse 773 SE", D(2023, 1, 16), to="parliament")
    w.development(
        taks,
        "773 SE langes Riigikogu koosseisu volituste lõppedes menetlusest välja",
        D(2023, 4, 19),
        note="Algatus jätkub uue eelnõuna (TAIKS).",
    )
    link_related_matters(
        matter=taks,
        other=taiks,
        actor=w.owner,
        note="Sama algatus: 773 SE järglane on 554 SE.",
    )
    close_matter(
        matter=_fresh(taks),
        disposition=Disposition.SUPERSEDED,
        actor=w.owner,
        reason="Eelnõu langes menetlusest välja; töö jätkub TAIKS eelnõu all.",
        successor=_fresh(taiks),
    )


def _build_tootasu(w: World) -> None:
    """P4 — the repeat response deadline (#404): deadline A answered by a sent
    opinion, a later request B active and unanswered."""
    t = w.today
    m = w.matter(
        TITLE_TOOTASU,
        "P4-tootasu",
        "Näidisteema: Koja algatus töötasu arestimise korra muutmiseks. Esimene arvamuse "
        "tähtaeg on vastatud; ministeerium küsis muudetud eelnõule uue arvamuse. "
        "Sünteetiline testandmestik.",
    )
    jdm = _org("Justiits- ja Digiministeerium")
    first = set_next_action_for_new_work(
        matter=m,
        text="Saada ministrile ettepanek eraldi VTK koostamiseks",
        actor=w.owner,
    )
    ws.complete_current_action(
        matter=m,
        author=w.owner,
        action_id=first.pk,
        body=(
            "<p>Ettepanek saadetud justiits- ja digiministrile; vastus lubab VTK "
            "järgmise poolaasta jooksul.</p>"
        ),
        uploads=[
            _txt(
                "NAIDIS-taust.txt",
                "Taustamaterjal: probleemi kirjeldus ja varasem kirjavahetus (näidis).",
            )
        ],
    )
    w.development(
        m,
        "Ministeerium avaldas töötasu arestimise eelnõu",
        t - dt.timedelta(days=120),
        to="consultation",
    )
    # Deadline A: requested, then answered by a sent opinion — into history.
    request_response_deadline(matter=m, deadline=t - dt.timedelta(days=45), actor=w.owner)
    w.opinion(
        _fresh(m),
        jdm,
        t - dt.timedelta(days=50),
        "NAIDIS-arvamus-tootasu-A.pdf",
        "Koja arvamus töötasu arestimise eelnõule.",
        answers_deadline=deadline_revision(_fresh(m)),
    )
    # Deadline B: the ministry asks again on the amended bill. The old opinion
    # answered A and must not read as an answer to B.
    request_response_deadline(matter=m, deadline=t + dt.timedelta(days=14), actor=w.owner)
    set_next_action_for_new_work(
        matter=m,
        text="Koosta Koja arvamus muudetud eelnõule",
        target_date=t + dt.timedelta(days=10),
        actor=w.owner,
    )


def _build_kutse(w: World) -> None:
    """P5 — the full dossier: Ülevaade and a separate Uudis, a completed
    Kaasamine with a feedback file, a sent opinion, stage history, a work
    victory, a moved deadline."""
    t = w.today
    m = w.matter(
        TITLE_KUTSE,
        "P5-kutse",
        "Näidisteema: kutse- ja oskusseadus — VTK, kooskõlastusring, valitsus, Riigikogu "
        "973 SE ja komisjoni arvamusepäring. Sünteetiline testandmestik.",
    )
    htm = _org("Haridus- ja Teadusministeerium")
    w.development(m, "Kutse- ja oskusseaduse VTK", D(2025, 1, 7), to="consultation")
    ws.add_matter_website_overview(
        matter=m,
        author=w.owner,
        published_on=D(2026, 2, 25),
        url="https://www.koda.ee/naidis/kutseseadus",
        title="Kutse- ja oskusseaduse eelnõu — anna tagasisidet",
        kind=WebsiteOverviewKind.OVERVIEW.value,
    )
    round_ = ws.add_matter_engagement(
        matter=m,
        author=w.owner,
        audience="24 erialaliitu",
        kind=EngagementKind.OTHER.value,
        occurred_on=D(2026, 2, 25),
        feedback_deadline=D(2026, 3, 12),
    ).record
    ws.add_matter_external_position(
        matter=m,
        author=w.owner,
        organisation=_org("NÄIDIS Ehitusettevõtjate Liit"),
        provenance=ExternalPositionProvenance.RECEIVED.value,
        stated_on=D(2026, 3, 10),
        summary="Esmakutse nõuded peavad jääma proportsionaalseks.",
        engagement=round_,
        source_is_member=True,
    )
    ws.add_engagement_feedback(
        engagement=round_,
        author=w.owner,
        feedback_received="11 seisukohta; koondtabel lisatud.",
        uploads=[
            _pdf(
                "NAIDIS-kutseseadus-tagasiside.pdf",
                "Liikmete tagasiside koondtabel (näidis).",
            )
        ],
    )
    opinion_result = w.opinion(
        m,
        htm,
        D(2026, 3, 13),
        "NAIDIS-arvamus-kutseseadus.pdf",
        "Koja arvamus kutse- ja oskusseaduse eelnõule.",
    )
    # One renamed display title, with the original filename untouched under it.
    for document in opinion_result.documents or []:
        rename_document(
            document=document,
            title="Koja arvamus HTM-ile (kutse- ja oskusseadus)",
            actor=w.owner,
        )
        break
    w.development(
        m, "Valitsus kiitis eelnõu ja määruste kavandid heaks", D(2026, 5, 20), to="government"
    )
    ws.add_matter_work_victory(
        matter=m,
        author=w.owner,
        title="Osa Koja ettepanekutest arvestati (kooskõlastustabel)",
        period_date=D(2026, 1, 1),
        period_end=D(2026, 12, 31),
        date_precision="YEAR",
    )
    w.development(m, "Riigikogu menetlusse 973 SE", D(2026, 6, 18), to="parliament")
    ws.add_matter_website_overview(
        matter=m,
        author=w.owner,
        published_on=t - dt.timedelta(days=25),
        url="https://www.koda.ee/naidis/uudised/kutseseadus-riigikogus",
        title="Riigikogu võttis kutse- ja oskusseaduse eelnõu menetlusse",
        kind=WebsiteOverviewKind.NEWS.value,
    )
    # The committee asked for an opinion, then moved its own date: history
    # carries a MOVED row and the header the current date.
    request_response_deadline(matter=m, deadline=t + dt.timedelta(days=30), actor=w.owner)
    change_response_deadline(
        matter=_fresh(m),
        deadline=t + dt.timedelta(days=37),
        actor=w.owner,
        change=ResponseDeadlineChange.MOVED.value,
    )
    ws.add_matter_important_date(
        matter=m,
        author=w.owner,
        title="Muudatusettepanekute tähtaeg (973 SE)",
        date_value=t + dt.timedelta(days=37),
        period_end=t + dt.timedelta(days=37),
        date_precision=DatePrecision.EXACT.value,
    )
    set_next_action_for_new_work(
        matter=m,
        text="Koosta Koja arvamus majanduskomisjonile",
        target_date=t + dt.timedelta(days=6),
        actor=w.owner,
    )


def _build_piiratud(w: World) -> None:
    """P6 — edge cases: a RESTRICTED Matter with restricted children, a long
    title, an undated current action and a dated planned one."""
    t = w.today
    owner = w.restricted_owner
    m = w.matter(
        TITLE_PIIRATUD,
        "P6-piiratud",
        "Näidisteema (piiratud): ettevõtjate konfidentsiaalne tagasiside maksukorralduse "
        "seaduse eelnõu mõjude kohta sisaldab äriliselt tundlikku teavet üksikute "
        "ettevõtete maksukäitumise ja kavandatavate investeerimisotsuste kohta, mistõttu "
        "on teema nähtavus piiratud menetlusega seotud juristidele. Sünteetiline "
        "testandmestik.",
        owner=owner,
        visibility=Visibility.RESTRICTED,
    )
    w.development(
        m,
        "Rahandusministeerium saatis maksukorralduse eelnõu sihitatud konsultatsioonile",
        t - dt.timedelta(days=10),
        to="consultation",
        author=owner,
    )
    first = set_next_action_for_new_work(
        matter=m,
        text="Kogu ettevõtjate konfidentsiaalne tagasiside kokku",
        actor=owner,
    )
    ws.complete_current_action(
        matter=m,
        author=owner,
        action_id=first.pk,
        body=(
            "<p>Konfidentsiaalne kokkuvõte: kolm ettevõtet kirjeldasid eelnõu mõju oma "
            "maksupositsioonile; materjal on piiratud nähtavusega.</p>"
        ),
        uploads=[
            _pdf(
                "NAIDIS-konfidentsiaalne-tagasiside.pdf",
                "Konfidentsiaalne ettevõtjate tagasiside (näidis, piiratud).",
            )
        ],
    )
    set_next_action_for_new_work(
        matter=m,
        text=(
            "Lepi rahandusministeeriumiga kokku, millises vormis saab konfidentsiaalse "
            "tagasiside kokkuvõtte edastada ilma üksikuid ettevõtteid avaldamata"
        ),
        actor=owner,
    )
    add_planned_action(
        matter=m,
        text="Koosta anonüümitud kokkuvõte ministeeriumile",
        target_date=t + dt.timedelta(days=14),
        actor=owner,
    )
    # A relation from a restricted file to a normal one: the normal page must
    # not leak the restricted side to readers outside the legal team.
    kutse = Matter.objects.filter(title=TITLE_KUTSE, data_class=MatterDataClass.TEST).first()
    if kutse is not None:
        link_related_matters(
            matter=m,
            other=kutse,
            actor=owner,
            note="Seotud: kutsealade maksustamise küsimus.",
        )


@dataclass(frozen=True)
class Package:
    key: str
    titles: tuple[str, ...]
    build: Callable[[World], None]


PACKAGES: tuple[Package, ...] = (
    Package("P1-kliima", (TITLE_KLIIMA,), _build_kliima),
    Package("P2-jaatme", (TITLE_JAATME,), _build_jaatme),
    Package("P3-taks-taiks", (TITLE_TAIKS, TITLE_TAKS), _build_taks_taiks),
    Package("P4-tootasu", (TITLE_TOOTASU,), _build_tootasu),
    Package("P5-kutse", (TITLE_KUTSE,), _build_kutse),
    Package("P6-piiratud", (TITLE_PIIRATUD,), _build_piiratud),
)


class Command(BaseCommand):
    help = (
        "Seed the six-package showcase world (synthetic, TEST-classified). "
        "Refuses on a database holding any non-TEST Matter; on an instance with "
        f"REAL_DATA_ALLOWED it additionally requires --operator-intent {OPERATOR_INTENT}."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--operator-intent",
            default="",
            help=(
                "Required on an instance with REAL_DATA_ALLOWED set: pass "
                f"{OPERATOR_INTENT!r} to state that this instance is meant to hold "
                "the showcase world."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        non_test = Matter.objects.exclude(data_class=MatterDataClass.TEST).count()
        if non_test:
            raise CommandError(
                f"{non_test} non-TEST Matter(s) exist. The showcase seeder never writes "
                "into a business database; there is no flag around this refusal."
            )
        if settings.REAL_DATA_ALLOWED and options["operator_intent"] != OPERATOR_INTENT:
            raise CommandError(
                "REAL_DATA_ALLOWED is set on this instance. Seeding the showcase here "
                f"requires --operator-intent {OPERATOR_INTENT}."
            )

        owner = self._owner()
        restricted_owner = self._restricted_owner(owner)
        world = World(
            owner=owner,
            restricted_owner=restricted_owner,
            today=timezone.localdate(),
            log=lambda msg: self.stdout.write(msg),
        )

        created, skipped = [], []
        for package in PACKAGES:
            existing = Matter.objects.filter(
                title=package.titles[0], data_class=MatterDataClass.TEST
            ).exists()
            if existing:
                skipped.append(package.key)
                self.stdout.write(f"{package.key}: exists, skipped")
                continue
            with transaction.atomic():
                package.build(world)
            created.append(package.key)
            self.stdout.write(f"{package.key}: created")

        for matter in world.made:
            matter = _fresh(matter)
            self.stdout.write(
                f"  {matter.title[:60]} | open={matter.is_open} "
                f"data_class={matter.data_class} visibility={matter.visibility}"
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"seed_version={SEED_VERSION} created={len(created)} skipped={len(skipped)} "
                f"matters_total={Matter.objects.count()}"
            )
        )

    def _owner(self) -> User:
        """Deterministic: the department head by display name, else a specialist."""
        active = User.objects.filter(is_active=True)
        head = active.filter(role=UserRole.DEPARTMENT_HEAD).order_by("display_name", "upn").first()
        if head is not None:
            return head
        specialist = active.filter(role=UserRole.SPECIALIST).order_by("display_name", "upn").first()
        if specialist is None:
            raise CommandError("No active DEPARTMENT_HEAD or SPECIALIST to own the showcase.")
        return specialist

    def _restricted_owner(self, fallback: User) -> User:
        specialist = (
            User.objects.filter(is_active=True, role=UserRole.SPECIALIST)
            .order_by("display_name", "upn")
            .first()
        )
        return specialist or fallback
