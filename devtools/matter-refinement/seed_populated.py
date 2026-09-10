"""The populated synthetic Matter the approved preview was drawn on.

Development tool, not a management command and not shipped: it exists so the
real page can be captured on the *same* content the approved screenshots show,
which is what makes the side-by-side in `16. COMPARE` meaningful.

It is `app/core/management/commands/seed_matter_refinement_preview.py` from the
approved preview branch (preview/matter-page-refinement @ 4cbc71e), line for
line, with the management-command wrapper replaced by a `__main__` block. The
fixture is the oracle; forking it would have meant comparing two different
Matters and calling the difference a design deviation.

    uv run python devtools/matter-refinement/seed_populated.py

Prints the Matter id. Idempotent; a Matter with history cannot be deleted, so
reseeding means a fresh database.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import django

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()


class _Out:
    def write(self, line: str) -> None:
        print(line)


from datetime import date, timedelta  # noqa: E402
from typing import Any  # noqa: E402

from django.conf import settings  # noqa: E402
from django.core.management.base import BaseCommand, CommandError  # noqa: E402
from django.db import transaction  # noqa: E402
from django.utils import timezone  # noqa: E402

from app.accounts.enums import UserRole  # noqa: E402
from app.accounts.models import User  # noqa: E402
from app.accounts.services import create_synthetic_user  # noqa: E402
from app.core.enums import Visibility  # noqa: E402
from app.documents.enums import DocumentRole  # noqa: E402
from app.documents.services import add_evidence_version, create_document  # noqa: E402
from app.intelligence.enums import EffectiveDateKind  # noqa: E402
from app.intelligence.services import add_effective_date, add_important_date  # noqa: E402
from app.matters.entry_enums import EntryKind  # noqa: E402
from app.matters.enums import EngagementKind, MatterDataClass, MatterOrigin  # noqa: E402
from app.matters.models import Matter, MatterPersonalNote  # noqa: E402
from app.matters.services import add_engagement, add_entry, create_matter  # noqa: E402
from app.organisations.models import Organisation, OrganisationType  # noqa: E402
from app.taxonomy.models import PolicyArea  # noqa: E402
from app.workflow.dates import period_bounds  # noqa: E402
from app.workflow.enums import DatePrecision, Track  # noqa: E402
from app.workflow.models import StageVocabulary  # noqa: E402
from app.workflow.services import complete_next_action, set_next_action  # noqa: E402

#: Long enough to wrap on a real header. A short fixture title hides the one
#: defect this preview exists to catch — what a three-line `h1` does to the band
#: above the tabs (see `seed_e2e_data.OPEN_TITLE`, same reasoning).
TITLE = (
    "Pakendiseaduse ja jäätmeseaduse muutmise seaduse eelnõu väljatöötamiskavatsus "
    "ning tootjavastutuse ülevaatamine"
)

SUMMARY = (
    "Kavatsus laiendab tootjavastutust pakenditele, mida praegu käideldakse "
    "üldise olmejäätme voona. Ettevõtjatele tähendab see uut aruandluskohustust "
    "ja tõenäoliselt kõrgemat taaskasutustasu alates jõustumisest."
)

OWNER_UPN = "eelvaade.vastutaja@example.invalid"
OWNER_NAME = "Mari Näidisjurist"
COLLEAGUE_UPN = "eelvaade.kolleeg@example.invalid"
COLLEAGUE_NAME = "Toomas Näidisjurist"

SENDER = "Näidisministeerium (eelvaade)"
ADDRESSEE = "Näidisamet (eelvaade)"

ATTACHMENT_NAME = "Teataja_04_2026_pakendiseaduse_vtk_lisa_keeleparandused.pdf"
ATTACHMENT_BODY = b"Sunteetiline eelvaate manus. Ei ole Koja dokument.\n"

#: Deliberately overlapping with `TITLE` in the words the suggestion engine
#: actually scores on, and deliberately long enough to wrap inside a 300 px rail
#: column — which is the one thing a rail card of five suggestions has to
#: survive.
NEIGHBOUR_TITLES = (
    "Pakendiseaduse ja jäätmeseaduse muutmine seoses tootjavastutusorganisatsioonide järelevalvega",
    "Koja arvamus pakendimääruse ülevõtmise kohta",
    "Jäätmeseaduse muutmise seaduse eelnõu",
    "ELi pakendi- ja pakendijäätmete määruse ülevõtmine Eesti õigusesse",
    "Kiri ministeeriumile tootjavastutuse laiendamise kohta",
    "Pakendiaktsiisi ja taaskasutustasu mõjuanalüüs",
    "Tootjavastutuse aruandluskohustuse lihtsustamine väiketootjatele",
    "Pakendiregistri andmekoosseisu muutmise määruse eelnõu",
    "Jäätmeseaduse rakendusaktide väljatöötamiskavatsus",
    "Taaskasutustasu määrade ülevaatamine ja tootjavastutus",
)

NOTE_BODY = (
    "Kysida Liinalt, kas tootjavastutuse laiendus katab ka e-kaubanduse "
    "pakendid. Enne komisjoni istungit vaadata ule 2024. aasta arvamus."
)


class Command(BaseCommand):
    help = "Seed one populated synthetic Matter for the Teema refinement preview."

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        if settings.REAL_DATA_ALLOWED:
            raise CommandError(
                "REAL_DATA_ALLOWED is set. The preview fixture must never be mixed "
                "with real departmental data."
            )
        if not settings.DEBUG:
            raise CommandError(
                "seed_matter_refinement_preview only runs in a development environment."
            )

        # Idempotent, and deliberately not re-buildable in place. A Matter that
        # has history cannot be deleted — `ChangeEvent` is append-only and the
        # foreign keys are `PROTECT`, which is the product working correctly —
        # so re-seeding means a fresh database, and the review instructions say
        # so rather than this command pretending otherwise.
        existing = Matter.objects.filter(title=TITLE).first()
        if existing is not None:
            self.stdout.write(str(existing.pk))
            return

        today = timezone.localdate()
        owner = self._person(OWNER_UPN, OWNER_NAME)
        colleague = self._person(COLLEAGUE_UPN, COLLEAGUE_NAME)
        sender = self._organisation(SENDER)
        addressee = self._organisation(ADDRESSEE)

        matter = create_matter(
            title=TITLE,
            actor=owner,
            owner=owner,
            data_class=MatterDataClass.TEST,
            origin=MatterOrigin.NATIVE,
            visibility=Visibility.NORMAL,
            source_organisations=[sender],
            addressee_organisation=addressee,
            track=Track.DOMESTIC,
            received_date=today - timedelta(days=16),
            response_deadline=today + timedelta(days=21),
            brief_summary=SUMMARY,
            stage=self._stage(),
        )
        matter.policy_areas.set(self._policy_areas())

        self._facts(matter, owner, today)
        self._chronology(matter, owner, colleague, today)
        self._next_action(matter, owner, today)
        self._neighbours(matter, owner, sender, addressee)

        MatterPersonalNote.objects.update_or_create(
            matter=matter, author=owner, defaults={"body": NOTE_BODY}
        )

        self.stdout.write(str(matter.pk))

    # -- pieces ------------------------------------------------------------

    def _person(self, upn: str, name: str) -> User:
        person = User.objects.filter(upn=upn).first()
        if person is not None:
            return person
        return create_synthetic_user(upn=upn, display_name=name, role=UserRole.SPECIALIST)

    def _organisation(self, name: str) -> Organisation:
        organisation = Organisation.objects.filter(name=name).first()
        if organisation is not None:
            return organisation
        return Organisation.objects.create(name=name, organisation_type=OrganisationType.MINISTRY)

    def _stage(self) -> Any:
        return StageVocabulary.objects.filter(is_active=True).order_by("sort_order").first()

    #: Two, so the header's `Valdkond` slot has a comma list to wrap, and these
    #: two because they are what a packaging file would actually be filed under
    #: — a fixture whose classification does not match its own title makes every
    #: suggestion reason on the page read as nonsense.
    POLICY_AREAS = ("Keskkond", "ELi õiguse ülevõtmine")

    def _policy_areas(self) -> list[PolicyArea]:
        areas = list(PolicyArea.objects.filter(is_active=True, name_et__in=self.POLICY_AREAS))
        return areas or list(PolicyArea.objects.filter(is_active=True).order_by("name_et")[:2])

    def _neighbours(
        self, matter: Matter, actor: User, sender: Organisation, addressee: Organisation
    ) -> None:
        """Six sibling files, so `Seotud materjalid` has something to suggest.

        The rail card is one of the two things the refinement genuinely moves,
        and its populated state is a list of candidates with a reason under
        each. `app/related_materials/engine.py` computes those from shared
        senders, shared policy areas and overlapping title words — so a Matter
        standing alone in the database produces an empty disclosure, and the
        product owner would be reviewing a control with nothing in it.

        Six rather than five, so the pager the design draws has a reason to
        appear at all.
        """
        areas = list(matter.policy_areas.all())
        for title in NEIGHBOUR_TITLES:
            if Matter.objects.filter(title=title).exists():
                continue
            neighbour = create_matter(
                title=title,
                actor=actor,
                owner=actor,
                data_class=MatterDataClass.TEST,
                origin=MatterOrigin.NATIVE,
                visibility=Visibility.NORMAL,
                source_organisations=[sender],
                addressee_organisation=addressee,
                track=Track.DOMESTIC,
            )
            neighbour.policy_areas.set(areas)

    def _facts(self, matter: Matter, actor: User, today: date) -> None:
        """Three dated obligations at three distances, and one commencement.

        The distances are what Zone B is for — a row 21 days out and a row 36
        days out have to be distinguishable at a glance — and the commencement
        is here because the design's third row carries the qualifier `jõustub`.
        Whether that row genuinely belongs in `Olulised tähtajad` is open
        question Q1 of the handoff and this fixture does not answer it; it makes
        the question visible.
        """
        add_important_date(
            matter=matter,
            title="Kooskõlastusringi lõpp",
            date_value=today + timedelta(days=21),
            period_end=today + timedelta(days=21),
            actor=actor,
        )
        add_important_date(
            matter=matter,
            title="Eelnõu eeldatavasti valitsuses",
            date_value=today + timedelta(days=36),
            period_end=today + timedelta(days=36),
            actor=actor,
        )
        # Recorded to a quarter, because the third row has to be a different
        # *shape* and not just a different number: a date column that only ever
        # holds `30.9.2026` never proves that «I kvartal 2027» fits in it.
        quarter_start, quarter_end = period_bounds(
            date(today.year + 1, 1, 1), DatePrecision.QUARTER
        )
        add_important_date(
            matter=matter,
            title="Riigikogu esimene lugemine",
            date_value=quarter_start,
            period_end=quarter_end,
            date_precision=DatePrecision.QUARTER,
            actor=actor,
        )
        add_effective_date(
            matter=matter,
            kind=EffectiveDateKind.KNOWN_DATE,
            date_value=date(today.year + 1, 1, 1),
            period_end=date(today.year + 1, 1, 1),
            description="Pakendiseaduse muudatused",
            actor=actor,
        )
        add_engagement(
            matter=matter,
            kind=EngagementKind.SURVEY,
            title="liikmetelt kogutud tagasiside tootjavastutuse kohta",
            occurred_on=today - timedelta(days=12),
            actor=actor,
        )

    def _chronology(self, matter: Matter, owner: User, colleague: User, today: date) -> None:
        """Enough history that the spine, the folds and the meta line are real.

        The mix matters more than the count: an entry with an attachment, an
        entry that set a step, a step that was completed, several plain notes,
        and the system run the page folds. Each one renders a different row
        shape, and a chronology of nine identical notes would prove none of them.
        """
        moment = timezone.now()

        def at(days_ago: int, hour: int, minute: int) -> Any:
            day = moment - timedelta(days=days_ago)
            return day.replace(hour=hour, minute=minute, second=0, microsecond=0)

        add_entry(
            matter=matter,
            body="<p>Teema avatud registrist saabunud kirja alusel.</p>",
            author=owner,
            kind=EntryKind.NOTE,
            occurred_at=at(16, 8, 42),
        )
        add_entry(
            matter=matter,
            body=(
                "<p>Telefonikõne ministeeriumi nõunikuga. Kinnitas, et "
                "kooskõlastusring avatakse septembri alguses ja tähtaeg on kolm nädalat.</p>"
            ),
            author=owner,
            kind=EntryKind.CALL,
            occurred_at=at(15, 11, 15),
        )
        add_entry(
            matter=matter,
            body=(
                "<p>Töörühma koosolek. Liikmed tõstatasid e-kaubanduse pakendite "
                "küsimuse, mida väljatöötamiskavatsus ei käsitle.</p>"
            ),
            author=colleague,
            kind=EntryKind.NOTE,
            occurred_at=at(13, 14, 5),
        )
        add_entry(
            matter=matter,
            body="<p>Kiri liikmelt. Palub arvamust taaskasutustasu mõju kohta väiketootjatele.</p>",
            author=owner,
            kind=EntryKind.NOTE,
            occurred_at=at(11, 9, 27),
        )
        self._attachment(matter, owner)
        add_entry(
            matter=matter,
            body="<p>Küsitlus liikmetele saadetud.</p>",
            author=colleague,
            kind=EntryKind.NOTE,
            occurred_at=at(12, 16, 40),
        )
        add_entry(
            matter=matter,
            body="<p>Vastuseid laekunud kolmelt liikmelt, kokkuvõte teeme reedel.</p>",
            author=colleague,
            kind=EntryKind.NOTE,
            occurred_at=at(9, 10, 3),
        )
        add_entry(
            matter=matter,
            body=(
                "<p>Kohtumine ministeeriumis. Kokku lepitud, et Koda esitab arvamuse "
                "kooskõlastusringi lõpuks.</p>"
            ),
            author=owner,
            kind=EntryKind.MEETING,
            occurred_at=at(7, 13, 30),
        )

        # A step that was set and then finished, so the chronology carries both
        # a `Järgmiseks` strip and a `Järgmiseks tehtud` row.
        first = set_next_action(
            matter=matter,
            text="Koguda liikmete tagasiside kokku",
            target_date=today - timedelta(days=4),
            date_precision=DatePrecision.EXACT,
            actor=owner,
        )
        complete_next_action(action=first, actor=owner)

        add_entry(
            matter=matter,
            body="<p>Kokkuvõte tehtud. Kolm liiget tõstavad esile aruandluskoormust.</p>",
            author=owner,
            kind=EntryKind.NOTE,
            occurred_at=at(3, 15, 12),
        )
        add_entry(
            matter=matter,
            body=(
                "<p>Avalik kommentaar Äripäevas tootjavastutuse laiendamise kohta. "
                "Viide saadetud kommunikatsiooni.</p>"
            ),
            author=colleague,
            kind=EntryKind.NOTE,
            occurred_at=at(2, 8, 55),
        )

    def _attachment(self, matter: Matter, actor: User) -> None:
        document = create_document(
            matter=matter,
            title=ATTACHMENT_NAME,
            role=DocumentRole.INCOMING_AUTHORITY,
            created_by=actor,
        )
        add_evidence_version(
            document=document,
            content=ATTACHMENT_BODY,
            original_filename=ATTACHMENT_NAME,
            mime_type="application/pdf",
            uploaded_by=actor,
        )

    def _next_action(self, matter: Matter, actor: User, today: date) -> None:
        """Quarter precision, exactly as the design's sample step carries it.

        It is also what makes the row render the way the design draws it: the
        production row offers `Lükka edasi` only on an exact date, because a day
        added to a period nobody named would be a day nobody chose. So the
        design's two controls — `✓ Tehtud` and `Muuda` — are what a
        quarter-precision step actually shows, not a control that was dropped.
        """
        set_next_action(
            matter=matter,
            text="Koostada Koja arvamus ja saata kooskõlastusringile",
            target_date=date(today.year + 1, 1, 1),
            date_precision=DatePrecision.QUARTER,
            actor=actor,
        )


if __name__ == "__main__":
    Command().handle()
