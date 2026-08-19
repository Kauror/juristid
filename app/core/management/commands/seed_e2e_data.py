"""A deterministic world for the browser suite.

Named, stable personas and exactly the records the E2E scenarios assert on. The
browser tests have no database access on purpose: if they could query around the
interface, an authorization bug in the interface would not fail them.

Synthetic only, and refuses to run anywhere that claims to hold real data.
"""

from __future__ import annotations

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
from app.matters.enums import DataQualityTier, MatterOrigin, RecordMode
from app.matters.models import Matter
from app.matters.services import add_entry, create_matter
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
]

MINISTRY = "Näidisministeerium"
RESTRICTED_TITLE = "Konfidentsiaalne liikmete tagasiside"
OPEN_TITLE = "Tavaline avatud teema kõigile nähtav"
ARCHIVE_TITLE = "Arhiiviteema 2014 sünteetiline registrikirje"


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
        area, _ = PolicyArea.objects.get_or_create(
            key="keskkond", defaults={"name_et": "Keskkond", "sort_order": 10}
        )
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
            source_organisation=ministry,
            visibility=Visibility.RESTRICTED,
        )
        add_entry(
            matter=restricted,
            body="<p>Liikme konfidentsiaalne seisukoht pakendiaktsiisi kohta.</p>",
            author=sandra,
            kind=EntryKind.NOTE,
        )
        set_next_action(
            matter=restricted,
            text="Konfidentsiaalne järgmine samm",
            kind=ActionKind.DO,
            date_semantics=DateSemantics.DEADLINE,
            target_date=date.today() + timedelta(days=4),
            actor=sandra,
        )

        # A normal Matter, so the register is not empty for Martin.
        visible = create_matter(
            title=OPEN_TITLE,
            actor=martin,
            owner=martin,
            stage=stage,
            track=Track.DOMESTIC,
            source_organisation=ministry,
        )
        visible.policy_areas.add(area)
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

        self.stdout.write(self.style.SUCCESS("E2E world ready."))
