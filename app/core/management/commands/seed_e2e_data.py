"""A deterministic world for the browser suite.

Named, stable personas and exactly the records the E2E scenarios assert on. The
browser tests have no database access on purpose: if they could query around the
interface, an authorization bug in the interface would not fail them.

Synthetic only, and refuses to run anywhere that claims to hold real data.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from app.accounts.enums import UserRole
from app.accounts.models import User
from app.accounts.services import create_synthetic_user
from app.core.enums import Visibility
from app.matters.entry_enums import EntryKind
from app.matters.enums import DataQualityTier, EngagementKind, MatterOrigin, RecordMode
from app.matters.models import Matter
from app.matters.services import add_engagement, add_entry, create_matter
from app.organisations.models import Organisation, OrganisationType
from app.taxonomy.models import PolicyArea
from app.workflow.enums import ActionKind, DateSemantics, Track
from app.workflow.models import StageVocabulary
from app.workflow.services import set_next_action

PERSONAS = [
    ("sandra@example.invalid", "Sandra Testjurist", UserRole.SPECIALIST, False),
    ("martin@example.invalid", "Martin Testjurist", UserRole.SPECIALIST, False),
    ("juht@example.invalid", "Testosakonnajuht", UserRole.DEPARTMENT_HEAD, False),
    ("admin@example.invalid", "Testadministraator", UserRole.ADMINISTRATOR, True),
    # Since docs/adr/0042 both lawyer roles read the whole department, so a
    # browser test asking what somebody unauthorized sees needs somebody who is.
    ("lugeja@example.invalid", "Testlugeja", UserRole.READER, False),
]

MINISTRY = "Näidisministeerium"
#: A second institution, for the same reason there is a second policy area: a
#: browser regression that proves the sender control accepts several senders
#: cannot prove anything against a list of one (Agent-E brief 51).
PARTNER = "Näidisettevõtete liit"
RESTRICTED_TITLE = "Konfidentsiaalne liikmete tagasiside"
#: Deliberately as long as a real one. Short fixture titles hide the defects
#: that only appear at realistic length — a register row that wraps and loses
#: its rhythm, a work row whose title runs under the chip beside it, a Matter
#: header whose heading takes three lines — and every one of those shipped
#: because the world the browser suite looked at was tidier than the register.
#: The tests that use this find the Matter by this constant, so it may grow.
OPEN_TITLE = (
    "Tavaline avatud teema kõigile nähtav — pakendiseaduse ja sellega seonduvalt "
    "teiste seaduste muutmise seaduse eelnõu väljatöötamiskavatsus"
)
ARCHIVE_TITLE = "Arhiiviteema 2014 sünteetiline registrikirje"

#: The historical world. One page attached to a normal Matter, so the case-file
#: rendering has something to render, and one undecided candidate, so the
#: reconciliation queue is not empty (Stage-2D brief 79).
HISTORICAL_PAGE_TITLE = "Pakendiseaduse muutmise eelnõu 2019"
HISTORICAL_INTRODUCTION = "Ettepaneku eestikeelne variant:"
#: A signed container, not a PDF. Two reasons: it is the commonest thing on a
#: real 2019 page, and it exercises the path where a file is marked unparseable
#: at import time rather than leaving the extraction worker to discover that a
#: synthetic stub is not a readable document (Stage-2D brief 24).
HISTORICAL_FILENAME = "seisukoht-2019.asice"
#: The inline-safe counterpart, on the same page. Two materials rather than one
#: because the Stage-2E.1 rule is a *contrast*: a filename either opens or
#: downloads, and a page holding only one of them cannot show the difference.
#:
#: Plain text on purpose. It is genuinely readable, so the extraction worker
#: succeeds on it and the Andmekvaliteet queue the browser suite asserts is
#: empty stays empty — the trap a `.pdf` stub fell into once already.
HISTORICAL_INLINE_FILENAME = "markmed-2019.txt"
HISTORICAL_INLINE_TEXT = (
    "Sünteetilised märkmed 2019. aasta kooskõlastusringilt.\n"
    "Koda pidas rakendusaega liiga lühikeseks.\n"
)
CANDIDATE_PAGE_TITLE = "Alkoholiaktsiisi töörühma märkmed"

#: The Statistika world. Three records the tabs need in order to say anything
#: at all: something genuinely late, something formally sent, and an
#: unclassified Matter so the *Klassifitseerimata* bucket is not empty
#: (Stage-2E brief 69).
OVERDUE_TITLE = "Tähtaja ületanud sünteetiline teema"
SUBMISSION_TITLE = "Sünteetiline arvamus ministeeriumile"
#: The one canonical opinion in preparation, so the *Arvamusi koostamisel*
#: figure and the list it opens both have something in them.
DRAFT_SUBMISSION_TITLE = "Koostamisel sünteetiline arvamus"
#: A signed container, not a PDF, and for two reasons. It is what a Chamber
#: opinion actually goes out as; and a synthetic stub with a `.pdf` extension is
#: a *broken* PDF, which the extraction worker correctly reports as a failure —
#: putting a false entry in the Andmekvaliteet queue the browser suite asserts
#: is empty. Found by the first CI round.
SUBMISSION_FILENAME = "arvamus-2026.asice"

#: The Osakonna töö world. Four records, each present because the
#: department-head page would otherwise show an honest emptiness that proves
#: nothing: a current file with nobody's name on it, one whose review date has
#: arrived (which is *not* overdue), one carrying no instruction at all, and a
#: former colleague who still owns live work (Stage-2F brief 42, 45).
#: A work victory nobody has decided on yet, standing in for a machine or
#: import proposal so the review controls have something to review.
#:
#: Deliberately free of the words its own status badge uses: a title
#: containing "töövõidu kandidaat" makes every attempt to locate the badge
#: inside the row match the title too.
MACHINE_CANDIDATE = "Registrist leitud ettepaneku arvestamine"

UNASSIGNED_TITLE = "Vastutajata sünteetiline teema"
REVIEW_DUE_TITLE = "Ootamise ülevaatuse aeg on käes"
NO_ACTION_TITLE = "Järgmiseta sünteetiline teema"
FORMER_OWNER_TITLE = "Endise kolleegi avatud teema"

#: A colleague who has left. Inactive on purpose: they must not appear in the
#: persona list, and their open file must still be visible to the head.
FORMER_UPN = "endine@example.invalid"
FORMER_NAME = "Kadri Endine"

#: Two register-backed Matters, so *Arvamusi koostamisel* is a real number in
#: the browser world rather than a zero that any broken filter would also
#: produce. One has a blank VÄLJA cell and belongs in the card; the other has a
#: mark in it and must not, which is what makes the drill-through test able to
#: fail (app/matters/register_filters.py, ADR 0021).
DRAFTING_TITLE = "Koostamisel olev sünteetiline arvamus"
DRAFTING_SENT_TITLE = "Saadetud sünteetiline arvamus"
REGISTER_SNAPSHOT_SHA = hashlib.sha256(b"juristid-e2e-current-register").hexdigest()

#: What the seeded "opinion sent" row holds in VÄLJA. A day and a month with no
#: year — the shape the register writes constantly and the date parser declines
#: to read — so the seeded world exercises "recorded, but not a readable date"
#: rather than the easy case.
SEED_SENT_CELL = "12.03"


#: The archive's synthetic snapshot, and the letters inside it. Invented, like
#: everything else here: no Koda opinion, ministry or filename may appear in a
#: fixture.
#:
#: The digests are **derived from the bytes**, never written down. An archive
#: binary is identified by the hash of what it holds, and the recovery
#: fingerprint verifies stored objects against that recorded hash — so a fixture
#: carrying a decorative SHA would fail the restore rehearsal, which is exactly
#: what it did the first time this was written.
ARCHIVE_SNAPSHOT_SHA = hashlib.sha256(b"juristid-e2e-opinions-archive").hexdigest()
ARCHIVE_LETTERS = [
    (
        "Näidisseaduse muutmise arvamus",
        "Näidisministeerium",
        "2024-04-10",
        "Käesolevaga esitab näidiskoda arvamuse näidisseaduse eelnõu kohta.",
    ),
    (
        "Sidumata näidiskiri",
        "Teine näidisamet",
        "2023-09-01",
        "",
    ),
]


def archive_letter_bytes(index: int) -> bytes:
    """The deterministic content of one seeded archive letter."""
    return b"%PDF-1.4\n% synthetic e2e opinion " + str(index).encode() + b"\n%%EOF\n"


def archive_letter_sha(index: int) -> str:
    """What that letter is identified by, computed the way the importer does."""
    return hashlib.sha256(archive_letter_bytes(index)).hexdigest()


class Command(BaseCommand):
    help = "Create the deterministic synthetic world the Playwright suite asserts against."

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        if settings.REAL_DATA_ALLOWED:
            raise CommandError("REAL_DATA_ALLOWED is set; refusing to seed synthetic data.")

        people = {}
        for upn, name, role, is_staff in PERSONAS:
            person = User.objects.filter(upn=upn).first()
            if person is None:
                person = create_synthetic_user(
                    upn=upn, display_name=name, role=role, is_staff=is_staff
                )
            people[upn] = person

        sandra = people["sandra@example.invalid"]
        martin = people["martin@example.invalid"]

        ministry, _ = Organisation.objects.get_or_create(
            name=MINISTRY, defaults={"organisation_type": OrganisationType.MINISTRY}
        )
        partner, _ = Organisation.objects.get_or_create(
            name=PARTNER, defaults={"organisation_type": OrganisationType.ASSOCIATION}
        )
        # Read, not created. The vocabulary is reference data and arrives with
        # `taxonomy/0002_reference_policy_areas`; a browser world that invented
        # its own `maksundus` beside the canonical `maksud` would put two
        # spellings of one concept into the control it exists to exercise.
        # Nine real areas are also a better test of a multi-select than two.
        area = PolicyArea.objects.get(key="keskkond")
        stage = StageVocabulary.objects.get(key="consultation")

        if Matter.objects.filter(title=RESTRICTED_TITLE).exists():
            self.stdout.write("E2E world already present.")
            return

        # A restricted Matter Sandra owns. Martin must never reach it, and the
        # administrator must not reach it either.
        restricted = create_matter(
            title=RESTRICTED_TITLE,
            actor=sandra,
            owner=sandra,
            stage=stage,
            track=Track.DOMESTIC,
            source_organisations=[ministry],
            visibility=Visibility.RESTRICTED,
        )
        add_entry(
            matter=restricted,
            body="<p>Liikme konfidentsiaalne seisukoht pakendiaktsiisi kohta.</p>",
            author=sandra,
            kind=EntryKind.NOTE,
        )
        # Today, not today + 4, and the reason is the visual baselines.
        #
        # A work item four days out lands in *Sel nädalal* on a Monday and in
        # *Hiljem* on a Thursday, because the ISO week ends on Sunday. Minu töö
        # omits an empty band, so the page was one section shorter on some
        # weekdays than on others and `minu-too.png` went red for a reason that
        # was the calendar rather than the CSS. Ülevaade's *Tähtajad* groups
        # moved the same row between *Sel nädalal* and *Järgmisel* for the same
        # reason.
        #
        # `today` is the one anchor whose band is the same on all seven days:
        # it is always *Täna* on Minu töö and always inside *Sel nädalal* on
        # Ülevaade. A deterministic *Sel nädalal*-band row is not constructible
        # at all — on a Sunday that band is structurally empty — so the world
        # locks the four bands it can and `test_ui_shell` asserts the banding
        # rules in Python, where a weekday can be chosen (Ülevaade QA §5).
        set_next_action(
            matter=restricted,
            text="Konfidentsiaalne järgmine samm",
            kind=ActionKind.DO,
            date_semantics=DateSemantics.DEADLINE,
            target_date=date.today(),
            actor=sandra,
        )

        # A normal Matter, so the register is not empty for Martin.
        visible = create_matter(
            title=OPEN_TITLE,
            actor=martin,
            owner=martin,
            stage=stage,
            track=Track.DOMESTIC,
            # Two senders, because a matter really does arrive from a ministry
            # and an association at once, and the detail page's rendering of a
            # sender *set* has to be exercised by something.
            source_organisations=[ministry, partner],
        )
        visible.policy_areas.add(area)
        # One `Kaasamine`, so the visual baseline shows a populated section
        # rather than only its empty state. Deterministic and dated, and on the
        # Matter the screenshot suite opens — the interactive engagement tests
        # deliberately write to a different one, because a section that grows
        # during the run would make this baseline depend on test order.
        add_engagement(
            matter=visible,
            kind=EngagementKind.WEB_CALL,
            title="Liikmete kaasamiskutse pakendiseaduse eelnõule",
            url="https://www.koda.ee/kaasamine/naidis",
            note="Sünteetiline näidiskirje.",
            occurred_on=date(2026, 5, 12),
            actor=martin,
        )
        add_entry(
            matter=visible,
            body="<p>Avalik sissekanne, mida kõik näevad.</p>",
            author=martin,
            occurred_at=timezone.now() - timedelta(days=1),
        )
        set_next_action(
            matter=visible,
            text="Jälgi menetluse käiku",
            kind=ActionKind.MONITOR,
            date_semantics=DateSemantics.REVIEW_ON,
            target_date=date.today() + timedelta(days=30),
            actor=martin,
        )

        # An archive row, so the register proves FULL and ARCHIVE coexist.
        create_matter(
            title=ARCHIVE_TITLE,
            assign_reference=False,
            record_mode=RecordMode.ARCHIVE,
            origin=MatterOrigin.LEGACY_IMPORT,
            data_quality_tier=DataQualityTier.TIER_3_REGISTER_ARCHIVE,
            source_era="2014",
            reporting_year=2014,
        )

        self._historical_world(visible)
        self._opinion_archive_world(visible, restricted)
        self._statistics_world(visible, martin, ministry)
        self._department_world(sandra, martin, ministry, stage)
        self._intelligence_world(visible, restricted, martin, sandra)
        self.stdout.write(self.style.SUCCESS("E2E world ready."))

    def _opinion_archive_world(self, matter: Matter, restricted: Matter) -> None:
        """Two held archive letters, one of them findable by its contents.

        Built through the ORM and the storage API rather than by running the
        importer, for the reason `_historical_world` gives: the importer has its
        own suite against a synthetic ZIP, and what the browser suite proves is
        that an administrator can find and read a letter while a specialist
        cannot. Neither needs a 105 MB archive mounted.

        Two letters, not one, because the interesting states are a pair: one
        with extracted text and a Matter it is linked to, and one with neither.
        The coverage strip on the browse screen is only meaningful when the two
        differ.

        The filed letter is filed onto **both** a normal and a RESTRICTED
        Matter, which is the state the archive's hardest property needs: an
        administrator may open every letter Koda holds and may not read a
        restricted register entry, so the detail page has to name one of these
        two and neither confirm nor deny anything about the other. A browser is
        the only place that claim can be checked against what is actually on the
        screen (docs/adr/0028).
        """
        from django.core.files.base import ContentFile

        from app.documents.services import evidence_storage
        from app.legacy_import.opinion_archive import OpinionArchiveBatch, OpinionArchiveItem
        from app.legacy_import.opinion_binary import OpinionArchiveBinary, OpinionArchiveText
        from app.legacy_import.opinion_enums import ArchiveLinkBasis, ArchiveTextState
        from app.legacy_import.opinion_links import link_matter
        from app.legacy_import.opinion_search import rebuild_archive_index

        if OpinionArchiveBinary.objects.exists():
            return

        batch = OpinionArchiveBatch.objects.create(
            archive_sha256=ARCHIVE_SNAPSHOT_SHA,
            archive_file_name="Opinions-e2e.zip",
            importer_version="e2e/0",
            started_at=timezone.now(),
        )
        storage = evidence_storage()

        for index, (title, recipient, when, body) in enumerate(ARCHIVE_LETTERS):
            content = archive_letter_bytes(index)
            digest = archive_letter_sha(index)
            key = f"opinion-archive/{digest[:2]}/{digest[2:4]}/{digest}"
            if not storage.exists(key):
                storage.save(key, ContentFile(content))
            binary = OpinionArchiveBinary.objects.create(
                sha256=digest,
                size_bytes=len(content),
                mime_type="application/pdf",
                storage_key=key,
                source_archive_sha256=ARCHIVE_SNAPSHOT_SHA,
                materialized_at=timezone.now(),
            )
            OpinionArchiveItem.objects.create(
                batch=batch,
                archive_sha256=ARCHIVE_SNAPSHOT_SHA,
                archive_relative_path=f"Opinions/{when} - {recipient} - {title}.pdf",
                original_filename=f"{when} - {recipient} - {title}.pdf",
                sha256=digest,
                size_bytes=len(content),
                detected_type="application/pdf",
                filename_date=date.fromisoformat(when),
                filename_recipient=recipient,
                filename_title=title,
                binary=binary,
            )
            if body:
                OpinionArchiveText.objects.create(
                    binary=binary,
                    state=ArchiveTextState.DONE,
                    body=body,
                    page_count=1,
                    characters=len(body),
                    parser="e2e",
                    parser_version="1",
                )
                for target in (matter, restricted):
                    link_matter(
                        binary=binary,
                        matter=target,
                        basis=ArchiveLinkBasis.EXACT_BINARY,
                        note="Sünteetiline e2e seos.",
                    )

        rebuild_archive_index()

    def _intelligence_world(
        self, visible: Matter, restricted: Matter, martin: Any, sandra: Any
    ) -> None:
        """Structured facts for the Jälgimine views and the Matter sections.

        Entirely invented. The department's real lists live in OneNote and are
        emphatically **not** used as fixtures; a later reviewed migration will
        bring them across with their provenance (Stage-2G brief 43, 72).

        One record of each kind that the browser suite has to be able to see,
        plus one of each on the restricted Matter so an authorization test has
        something that must *not* appear.
        """
        from app.intelligence.enums import EffectiveDateKind
        from app.intelligence.services import (
            add_effective_date,
            add_important_date,
            add_work_victory_candidate,
        )
        from app.workflow.dates import bounds_for, quarter_bounds, year_bounds
        from app.workflow.enums import DatePrecision

        if visible.important_dates.exists():
            return

        next_year = date.today().year + 1

        # An exact upcoming date, so the calendar has a day-precision row.
        exact = date.today() + timedelta(days=45)
        exact_start, exact_end = bounds_for(DatePrecision.EXACT, exact_date=exact)
        add_important_date(
            matter=visible,
            title="Eelnõu eeldatav kooskõlastusring",
            actor=martin,
            date_value=exact_start,
            period_end=exact_end,
            date_precision=DatePrecision.EXACT,
        )

        # And an approximate one, because rendering *II kvartal* rather than a
        # manufactured first-of-the-quarter is the property worth a browser test.
        quarter_start, quarter_end = quarter_bounds(next_year, 2)
        add_important_date(
            matter=visible,
            title="Eeldatav VTK avalikustamine",
            date_value=quarter_start,
            period_end=quarter_end,
            date_precision=DatePrecision.QUARTER,
            actor=martin,
        )

        # Two commencements on one Matter: the point of the model.
        add_effective_date(
            matter=visible,
            kind=EffectiveDateKind.KNOWN_DATE,
            date_value=date(next_year, 9, 27),
            period_end=date(next_year, 9, 27),
            description="põhiosa",
            actor=martin,
        )
        add_effective_date(
            matter=visible,
            kind=EffectiveDateKind.KNOWN_DATE,
            date_value=date(next_year + 1, 1, 1),
            period_end=date(next_year + 1, 1, 1),
            description="osad sätted",
            actor=martin,
        )
        add_effective_date(
            matter=visible,
            kind=EffectiveDateKind.GENERAL_ORDER,
            description="rakendusmäärus",
            actor=martin,
        )

        victory_start, victory_end = year_bounds(date.today().year)
        add_work_victory_candidate(
            matter=visible,
            title="Koja ettepanek rakendusaja pikendamiseks võeti arvesse",
            period_date=victory_start,
            period_end=victory_end,
            date_precision=DatePrecision.YEAR,
            actor=martin,
        )
        # A candidate for the review path to act on. A person adding a victory
        # from the Matter page now records a confirmed one, so a proposal
        # awaiting somebody's decision has to come from where proposals
        # actually come from — a machine or an import — for the browser suite
        # to be able to exercise confirming and rejecting one at all.
        add_work_victory_candidate(
            matter=visible,
            title=MACHINE_CANDIDATE,
            period_date=victory_start,
            period_end=victory_end,
            date_precision=DatePrecision.YEAR,
            actor=martin,
        )

        # The restricted counterparts. Nothing about these may appear on the
        # generated pages for anybody but Sandra and the department head.
        add_important_date(
            matter=restricted,
            title="Konfidentsiaalne tähtaeg",
            date_value=exact,
            period_end=exact,
            date_precision=DatePrecision.EXACT,
            actor=sandra,
        )
        add_effective_date(
            matter=restricted,
            kind=EffectiveDateKind.KNOWN_DATE,
            date_value=date(next_year, 9, 27),
            period_end=date(next_year, 9, 27),
            description="konfidentsiaalne jõustumine",
            actor=sandra,
        )
        add_work_victory_candidate(
            matter=restricted,
            title="Konfidentsiaalne töövõidu kandidaat",
            period_date=victory_start,
            period_end=victory_end,
            date_precision=DatePrecision.YEAR,
            actor=sandra,
        )

    def _statistics_world(self, visible: Matter, martin: Any, ministry: Any) -> None:
        """The records the Statistika tabs assert on.

        Kept to three, and each one is there because a tab would otherwise show
        an honest but untestable emptiness: a metric with no records at all
        reports *insufficient data*, which is correct and proves nothing about
        the page (Stage-2E brief 69).
        """
        from app.submissions.services import (
            attach_final_evidence,
            create_submission,
            mark_submission_sent,
        )

        overdue = create_matter(
            title=OVERDUE_TITLE,
            actor=martin,
            owner=martin,
            track=Track.DOMESTIC,
            source_organisations=[ministry],
            received_date=date.today() - timedelta(days=40),
        )
        set_next_action(
            matter=overdue,
            text="Saada arvamus ministeeriumile",
            kind=ActionKind.DO,
            date_semantics=DateSemantics.DEADLINE,
            target_date=date.today() - timedelta(days=3),
            actor=martin,
        )

        submission = create_submission(
            matter=visible,
            title=SUBMISSION_TITLE,
            actor=martin,
            recipients=[ministry],
        )
        attach_final_evidence(
            submission=submission,
            content=bytes([80, 75, 3, 4]) + b" synthetic e2e signed opinion",
            original_filename=SUBMISSION_FILENAME,
            mime_type="application/vnd.etsi.asic-e+zip",
            actor=martin,
        )
        mark_submission_sent(submission=submission, actor=martin, channel="EIS")

        # One opinion still being written, left in DRAFT on purpose. Ülevaade's
        # *N arvamust koostamisel* counts exactly these, and a figure the seeded
        # world can only ever render as nought proves nothing about the link
        # under it (Ülevaade QA §1).
        #
        # On `overdue` rather than on `visible`, which is the Matter every visual
        # scenario opens. The heading that made this matter has since been
        # corrected — the section is *Koja arvamused* now, and a draft under it
        # is described accurately — but the placement stays where it is, because
        # the figure above counts exactly these and a fixture moved for a reason
        # that has gone away is a baseline churned for nothing.
        create_submission(
            matter=overdue,
            title=DRAFT_SUBMISSION_TITLE,
            actor=martin,
            recipients=[ministry],
        )

    def _department_world(self, sandra: Any, martin: Any, ministry: Any, stage: Any) -> None:
        """The states Osakonna töö exists to surface.

        Each one is a distinct operational fact and they must not read alike.
        A review date reached is not a missed deadline; a file with no
        instruction is not a file with a late one; and an open matter owned by
        somebody who has left is an anomaly the head should be shown rather
        than a row to quietly drop (Stage-2F brief 32, 34, 45).
        """
        today = date.today()

        create_matter(
            title=UNASSIGNED_TITLE,
            actor=martin,
            owner=None,
            stage=stage,
            track=Track.DOMESTIC,
            source_organisations=[ministry],
            received_date=today - timedelta(days=3),
            response_deadline=today + timedelta(days=4),
        )

        review_due = create_matter(
            title=REVIEW_DUE_TITLE,
            actor=sandra,
            owner=sandra,
            stage=stage,
            track=Track.DOMESTIC,
            source_organisations=[ministry],
            received_date=today - timedelta(days=30),
        )
        set_next_action(
            matter=review_due,
            text="Ootame ministeeriumi vastust",
            kind=ActionKind.WAIT,
            date_semantics=DateSemantics.REVIEW_ON,
            target_date=today - timedelta(days=1),
            actor=sandra,
        )

        create_matter(
            title=NO_ACTION_TITLE,
            actor=martin,
            owner=martin,
            stage=stage,
            track=Track.DOMESTIC,
            source_organisations=[ministry],
            received_date=today - timedelta(days=6),
        )

        former = User.objects.filter(upn=FORMER_UPN).first()
        if former is None:
            former = create_synthetic_user(
                upn=FORMER_UPN, display_name=FORMER_NAME, role=UserRole.SPECIALIST
            )
            User.objects.filter(pk=former.pk).update(is_active=False)
            former.refresh_from_db()
        create_matter(
            title=FORMER_OWNER_TITLE,
            actor=martin,
            owner=former,
            stage=stage,
            track=Track.DOMESTIC,
            source_organisations=[ministry],
            received_date=today - timedelta(days=9),
        )

        self._drafting_world(martin, stage, ministry, today)

    def _drafting_world(self, actor: Any, stage: Any, ministry: Any, today: date) -> None:
        """Two current register rows: one opinion still being drafted, one sent.

        Built through the ORM rather than through the cutover, for the reason
        the historical world is: the browser suite asks whether a lawyer can
        click a number and land on the rows behind it, and that question does
        not need a workbook to answer.
        """
        from app.legacy_import.current_state import CurrentRegisterState, RegisterCurrency
        from app.legacy_import.models import MatterSourceReference
        from app.legacy_import.register_semantics import opinion_sent_state

        if Matter.objects.filter(title=DRAFTING_TITLE).exists():
            return

        for index, (title, sent_recorded) in enumerate(
            ((DRAFTING_TITLE, False), (DRAFTING_SENT_TITLE, True)), start=1
        ):
            matter = create_matter(
                title=title,
                actor=actor,
                owner=actor,
                stage=stage,
                track=Track.DOMESTIC,
                source_organisations=[ministry],
                received_date=today - timedelta(days=20),
            )
            reference = MatterSourceReference.objects.create(
                matter=matter,
                source_system="EXCEL_REGISTER",
                source_file_name="Naidisregister.xlsx",
                source_snapshot_sha256=REGISTER_SNAPSHOT_SHA,
                source_sheet=str(today.year),
                source_row_number=index,
                source_row_raw={"VALJA": SEED_SENT_CELL if sent_recorded else ""},
                source_title=title,
                source_era=str(today.year),
            )
            CurrentRegisterState.objects.create(
                matter=matter,
                source_reference=reference,
                source_snapshot_sha256=REGISTER_SNAPSHOT_SHA,
                source_sheet=str(today.year),
                source_row_number=index,
                currency=RegisterCurrency.CURRENT,
                status_label="Kooskolastusringil",
                opinion_sent_recorded=sent_recorded,
                # Derived from the same cell as the flag beside it, through the
                # one function that decides it. A check constraint requires the
                # two to agree, and the seed used to set only the flag — so the
                # world it built was one the database refuses, which no unit
                # test could see because none of them seed (ADR 0045).
                opinion_sent_state=opinion_sent_state(
                    SEED_SENT_CELL if sent_recorded else "",
                    # No parsed date: the cell is a day and a month with no
                    # year, which is exactly what the register writes and
                    # exactly what the date parser declines to read.
                    parsed_date=None,
                ),
                owner_raw=actor.get_short_name(),
                owner_resolved=True,
                observed_at=timezone.now(),
            )

    def _historical_world(self, matter: Matter) -> None:
        """A OneNote page, its file, and one decision nobody has made yet.

        Built through the ORM rather than through the importer. The importer has
        its own suite against a synthetic archive; what the browser suite is for
        is whether a lawyer can read a 2019 case file and whether an operator can
        settle a match — and neither of those needs 4 GiB of source material to
        be true (Stage-2D brief 79).
        """
        from app.documents.enums import DocumentRole, ExtractionState, MalwareScanState
        from app.documents.models import DocumentVersion
        from app.documents.services import add_evidence_version, create_document
        from app.legacy_import.source_pages import (
            CandidateClass,
            HistoricalMatchCandidate,
            LegacySourcePage,
            LegacySourceResource,
            LegacySourceResourceImport,
            MatterSourcePage,
            ResourceImportState,
            SourceMatchClass,
            SourceMatchMethod,
            SourcePageRole,
            SourceRelationshipKind,
            SourceSystem,
        )

        if LegacySourcePage.objects.filter(title=HISTORICAL_PAGE_TITLE).exists():
            return

        content = bytes([80, 75, 3, 4]) + b" synthetic e2e ASiC-E container"
        inline_content = HISTORICAL_INLINE_TEXT.encode("utf-8")
        now = timezone.now()
        blocks = [
            {
                "kind": "TEXT",
                "ordinal": 1,
                "text": (
                    "Näidisministeerium saatis eelnõu kooskõlastusringile. "
                    "Koda juhtis tähelepanu rakendusaja pikkusele."
                ),
            },
            {"kind": "TEXT", "ordinal": 2, "text": HISTORICAL_INTRODUCTION},
            {"kind": "FILE_ATTACHMENT", "ordinal": 3, "resource_key": "e2e-resource-1"},
            {
                "kind": "LIST_ITEM",
                "ordinal": 4,
                "depth": 1,
                "text": "Ministeeriumi vastus saabus kuu hiljem.",
            },
            {"kind": "FILE_ATTACHMENT", "ordinal": 5, "resource_key": "e2e-resource-2"},
        ]
        page = LegacySourcePage.objects.create(
            source_system=SourceSystem.ONENOTE_DESKTOP,
            source_page_id="1-e2e0000000000000000000000000001",
            page_key="e2e-page-1",
            source_notebook="Näidiskoja õigusloome",
            source_section="ARHIIV 2019",
            title=HISTORICAL_PAGE_TITLE,
            page_level=2,
            page_order=1,
            page_role=SourcePageRole.MATTER_LIKE,
            role_reason="sünteetiline e2e maailm",
            source_created_at=now,
            source_modified_at=now,
            capture_id="e2e-capture-1",
            source_xml_sha256="0" * 64,
            derived_text=" ".join(str(block.get("text", "")) for block in blocks),
            blocks=blocks,
            links=[{"url": "https://eelnoud.example.invalid/19-0001", "displayText": "EIS"}],
            reading_order_strategy="VISUAL_THEN_XML",
            text_characters=200,
            block_count=len(blocks),
            file_count=2,
            file_bytes=len(content) + len(inline_content),
            first_imported_at=now,
            latest_imported_at=now,
        )
        resource = LegacySourceResource.objects.create(
            source_page=page,
            resource_key="e2e-resource-1",
            original_filename=HISTORICAL_FILENAME,
            resource_kind="FILE_ATTACHMENT",
            source_block_ordinal=3,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            archive_relative_path="resources/e2e-resource-1/original/" + HISTORICAL_FILENAME,
        )
        link = MatterSourcePage.objects.create(
            matter=matter,
            source_page=page,
            relationship_kind=SourceRelationshipKind.PRIMARY,
            match_method=SourceMatchMethod.EXCEL_EXACT_PAGE_ID,
            match_class=SourceMatchClass.EXACT,
            source_audit_reference="exact-matches.csv:e2e",
        )

        document = create_document(
            matter=matter,
            title=HISTORICAL_FILENAME,
            role=DocumentRole.LEGACY_MATERIAL,
            provenance_note="OneNote: ARHIIV 2019 → " + HISTORICAL_PAGE_TITLE,
        )
        version = add_evidence_version(
            document=document,
            content=content,
            original_filename=HISTORICAL_FILENAME,
            mime_type="application/vnd.etsi.asic-e+zip",
            acquired_at=now,
            source_identifier="e2e-page-1/e2e-resource-1",
            malware_scan_state=MalwareScanState.PENDING,
        )
        # Nothing will ever parse a signed container, so it is marked here
        # rather than left PENDING in a queue for ever.
        DocumentVersion.objects.filter(pk=version.pk).update(
            extraction_state=ExtractionState.NOT_APPLICABLE,
            extraction_note="Allkirjastatud ümbrik. Sisu ei avata ega indekseerita.",
        )
        LegacySourceResourceImport.objects.create(
            matter_source_page=link,
            resource=resource,
            document=document,
            document_version=version,
            state=ResourceImportState.IMPORTED,
        )

        # The second material: readable, and therefore openable. Its whole job
        # is to sit beside the signed container so a browser can prove that the
        # two filenames behave differently (Stage-2E.1 brief 11, 13).
        inline_resource = LegacySourceResource.objects.create(
            source_page=page,
            resource_key="e2e-resource-2",
            original_filename=HISTORICAL_INLINE_FILENAME,
            resource_kind="FILE_ATTACHMENT",
            source_block_ordinal=5,
            sha256=hashlib.sha256(inline_content).hexdigest(),
            size_bytes=len(inline_content),
            archive_relative_path=(
                "resources/e2e-resource-2/original/" + HISTORICAL_INLINE_FILENAME
            ),
        )
        inline_document = create_document(
            matter=matter,
            title=HISTORICAL_INLINE_FILENAME,
            role=DocumentRole.LEGACY_MATERIAL,
            provenance_note="OneNote: ARHIIV 2019 → " + HISTORICAL_PAGE_TITLE,
        )
        inline_version = add_evidence_version(
            document=inline_document,
            content=inline_content,
            original_filename=HISTORICAL_INLINE_FILENAME,
            mime_type="text/plain",
            acquired_at=now,
            source_identifier="e2e-page-1/e2e-resource-2",
            malware_scan_state=MalwareScanState.PENDING,
        )
        LegacySourceResourceImport.objects.create(
            matter_source_page=link,
            resource=inline_resource,
            document=inline_document,
            document_version=inline_version,
            state=ResourceImportState.IMPORTED,
        )

        candidate_page = LegacySourcePage.objects.create(
            source_system=SourceSystem.ONENOTE_DESKTOP,
            source_page_id="1-e2e0000000000000000000000000002",
            page_key="e2e-page-2",
            source_notebook="Näidiskoja õigusloome",
            source_section="ARHIIV 2019",
            title=CANDIDATE_PAGE_TITLE,
            page_level=2,
            page_order=2,
            page_role=SourcePageRole.MATTER_LIKE,
            source_created_at=now,
            source_modified_at=now,
            capture_id="e2e-capture-2",
            source_xml_sha256="1" * 64,
            derived_text="Töörühma märkmed alkoholiaktsiisi kohta.",
            blocks=[{"kind": "TEXT", "ordinal": 1, "text": "Töörühma märkmed."}],
            reading_order_strategy="VISUAL_THEN_XML",
            text_characters=40,
            block_count=1,
            first_imported_at=now,
            latest_imported_at=now,
        )
        HistoricalMatchCandidate.objects.create(
            source_page=candidate_page,
            matter=matter,
            excel_reference="2019_44",
            excel_title="Alkoholiaktsiisi eelnõu",
            candidate_class=CandidateClass.REVIEW_REQUIRED,
            score=0.44,
            match_signals="pealkirja osaline kattuvus; sama aasta",
            explanation="review-required.csv:2019_44",
        )
        # A second candidate, in a class nothing decides, and on a *different*
        # page. Two things depend on that: the review-required one above is
        # settled by `test_an_operator_settles_a_match`, so the statistics suite
        # needs one no other test consumes; and that test finds its card by the
        # page title, so a second card carrying the same title makes its
        # locator ambiguous. Two register rows pointing at one page is what a
        # CONFLICT is, so this is where it belongs anyway.
        HistoricalMatchCandidate.objects.create(
            source_page=page,
            matter=matter,
            excel_reference="2019_45",
            excel_title="Alkoholiaktsiisi eelnõu, teine rida",
            candidate_class=CandidateClass.CONFLICT,
            score=0.51,
            match_signals="kaks rida osutavad samale lehele",
            conflicts="2019_44 vs 2019_45",
            explanation="conflicts.csv:2019_45",
        )
