"""Synthetic development data.

Everything created here is invented. No Koda, member or otherwise confidential
data may be used in local development before the Secure Pilot Gate, so this
command refuses to run in an environment that claims to hold real data.

The reference data below is PROVISIONAL. The real `Hetkeseis` vocabulary,
policy areas and tag seed come out of the Stage-0 workshop with the department
head and lawyers, and land as a reviewed data migration then. Only the tag
concepts named in the master specification are used as examples here.
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
from app.documents.enums import DocumentRole
from app.documents.services import add_evidence_version, create_document
from app.legacy_import.models import (
    ImportBatch,
    MatchMethod,
    MatterSourceReference,
    ReconciliationStatus,
)
from app.matters.entry_enums import EntryKind
from app.matters.enums import DataQualityTier, MatterOrigin, RecordMode, TagAssignmentSource
from app.matters.models import Matter, TagAssignment
from app.matters.services import add_entry, create_matter
from app.organisations.models import AliasType, Organisation, OrganisationAlias, OrganisationType
from app.submissions.enums import SubmissionKind
from app.submissions.services import (
    attach_final_evidence,
    create_submission,
    mark_submission_sent,
)
from app.taxonomy.models import PolicyArea, Tag, TagAlias
from app.workflow.enums import ActionKind, DateSemantics, Track
from app.workflow.models import StageVocabulary
from app.workflow.services import set_next_action

# The stage vocabulary is seeded by workflow/0004 from the live workbook and is
# not re-invented here. Development data uses the same rows production will.
SEEDED_STAGE_KEYS = ["consultation", "government", "parliament", "in_force", "eu_procedure"]

PROVISIONAL_POLICY_AREAS = [
    ("maksundus", "Maksundus"),
    ("tooeigus", "Tööõigus"),
    ("ettevotluskeskkond", "Ettevõtluskeskkond"),
    ("keskkond", "Keskkond"),
    ("energeetika", "Energeetika"),
]

# Tag concepts named in the master specification (11.2).
EXAMPLE_TAGS = [
    ("halduskoormus", "Halduskoormus", ["bürokraatia", "aruandluskoormus"]),
    ("kaibemaks", "Käibemaks", ["KM", "kaibemaks"]),
    ("vke", "VKE", ["väikeettevõtja", "SME"]),
    ("ai", "AI", ["tehisintellekt"]),
    ("kestlikkusaruandlus", "Kestlikkusaruandlus", ["CSRD", "ESG aruandlus"]),
]

SYNTHETIC_USERS = [
    ("jurist1@example.invalid", "Testjurist Üks", UserRole.SPECIALIST, False),
    ("jurist2@example.invalid", "Testjurist Kaks", UserRole.SPECIALIST, False),
    ("juht@example.invalid", "Testosakonnajuht", UserRole.DEPARTMENT_HEAD, False),
    ("admin@example.invalid", "Testadministraator", UserRole.ADMINISTRATOR, True),
]

SYNTHETIC_ORGANISATIONS = [
    ("Näidisministeerium", OrganisationType.MINISTRY, ["Endine Näidisministeerium"]),
    ("Näidisamet", OrganisationType.AUTHORITY, ["NA"]),
    ("Näidis AS", OrganisationType.COMPANY, []),
]


class Command(BaseCommand):
    help = "Create synthetic development data. Never run this against real data."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--matters",
            type=int,
            default=6,
            help="How many synthetic Matters to create (default 6).",
        )

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        if settings.REAL_DATA_ALLOWED:
            raise CommandError(
                "REAL_DATA_ALLOWED is set. Synthetic seed data must never be mixed "
                "with real departmental data."
            )
        if not settings.DEBUG:
            raise CommandError("seed_dev_data only runs in a development environment.")

        stages = self._seed_stages()
        policy_areas = self._seed_policy_areas()
        tags = self._seed_tags()
        users = self._seed_users()
        organisations = self._seed_organisations()

        created = self._seed_matters(
            count=options["matters"],
            stages=stages,
            policy_areas=policy_areas,
            tags=tags,
            users=users,
            organisations=organisations,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Synthetic data ready: {len(users)} users, {len(organisations)} organisations, "
                f"{created} Matters."
            )
        )

    # -- reference data ----------------------------------------------------

    def _seed_stages(self) -> list[StageVocabulary]:
        """Read the migration-seeded vocabulary; never create a parallel one."""
        stages = list(
            StageVocabulary.objects.filter(key__in=SEEDED_STAGE_KEYS).order_by("sort_order")
        )
        if not stages:  # pragma: no cover - only if migrations have not run
            raise CommandError(
                "Stage vocabulary is missing. Run migrations before seeding development data."
            )
        return stages

    def _seed_policy_areas(self) -> list[PolicyArea]:
        areas = []
        for index, (key, name) in enumerate(PROVISIONAL_POLICY_AREAS):
            area, _ = PolicyArea.objects.get_or_create(
                key=key, defaults={"name_et": name, "sort_order": (index + 1) * 10}
            )
            areas.append(area)
        return areas

    def _seed_tags(self) -> list[Tag]:
        tags = []
        for key, name, aliases in EXAMPLE_TAGS:
            tag, _ = Tag.objects.get_or_create(key=key, defaults={"name_et": name})
            for alias in aliases:
                TagAlias.objects.get_or_create(tag=tag, alias=alias)
            tags.append(tag)
        return tags

    # -- actors ------------------------------------------------------------

    def _seed_users(self) -> list[User]:
        users = []
        for upn, name, role, is_staff in SYNTHETIC_USERS:
            existing = User.objects.filter(upn=upn).first()
            if existing is None:
                existing = create_synthetic_user(
                    upn=upn, display_name=name, role=role, is_staff=is_staff
                )
            users.append(existing)
        return users

    def _seed_organisations(self) -> list[Organisation]:
        organisations = []
        for name, org_type, aliases in SYNTHETIC_ORGANISATIONS:
            organisation, _ = Organisation.objects.get_or_create(
                name=name, defaults={"organisation_type": org_type}
            )
            for alias in aliases:
                OrganisationAlias.objects.get_or_create(
                    organisation=organisation,
                    alias=alias,
                    defaults={"alias_type": AliasType.HISTORICAL_NAME},
                )
            organisations.append(organisation)
        return organisations

    # -- matters -----------------------------------------------------------

    def _seed_matters(
        self,
        *,
        count: int,
        stages: list[StageVocabulary],
        policy_areas: list[PolicyArea],
        tags: list[Tag],
        users: list[User],
        organisations: list[Organisation],
    ) -> int:
        if Matter.objects.exists():
            self.stdout.write("Matters already present; leaving them untouched.")
            return Matter.objects.count()

        ministry, authority, company = organisations
        lawyer_one, lawyer_two, head, _admin = users
        created = 0

        for index in range(count):
            owner = lawyer_one if index % 2 == 0 else lawyer_two
            restricted = index == count - 1
            matter = create_matter(
                title=f"Näidisteema {index + 1}: sünteetiline eelnõu menetlus",
                actor=head,
                owner=owner,
                stage=stages[index % len(stages)],
                track=Track.DOMESTIC,
                source_organisation=ministry,
                addressee_organisation=authority if index % 3 == 0 else None,
                received_date=date.today() - timedelta(days=30 - index),
                response_deadline=date.today() + timedelta(days=14 + index),
                visibility=Visibility.RESTRICTED if restricted else Visibility.NORMAL,
            )
            matter.policy_areas.add(policy_areas[index % len(policy_areas)])
            TagAssignment.objects.create(
                matter=matter,
                tag=tags[index % len(tags)],
                source=TagAssignmentSource.MANUAL,
                confirmed_by=owner,
                confirmed_at=timezone.now(),
            )

            document = create_document(
                matter=matter,
                title=f"Saabunud kiri {index + 1}",
                role=DocumentRole.INCOMING_AUTHORITY,
                created_by=owner,
            )
            add_evidence_version(
                document=document,
                content=f"Sünteetiline tõend teemale {matter.display_reference}.".encode(),
                original_filename=f"saabunud-{index + 1}.txt",
                mime_type="text/plain",
                uploaded_by=owner,
            )

            self._seed_matter_work(matter=matter, owner=owner, index=index, ministry=ministry)
            created += 1

        self._seed_archive_matters(company=company)
        return created

    def _seed_matter_work(
        self, *, matter: Matter, owner: User, index: int, ministry: Organisation
    ) -> None:
        """Give each Matter a plausible day of work.

        The mix is deliberate: DO, WAIT and MONITOR with all three date
        meanings, plus one Matter left without a next action so the attention
        panel has something real to report.
        """
        add_entry(
            matter=matter,
            body=(
                "<p>Saabus ministeeriumi kiri. Esialgne hinnang: mõju liikmetele on "
                "märkimisväärne, halduskoormus kasvab.</p>"
            ),
            author=owner,
            kind=EntryKind.NOTE,
            occurred_at=timezone.now() - timedelta(days=6),
        )

        if index % 3 == 0:
            add_entry(
                matter=matter,
                body=(
                    "<p>Kohtumine ministeeriumiga. Ministeerium lubas saata järgmise "
                    "nädala jooksul uue sõnastuse.</p>"
                ),
                author=owner,
                kind=EntryKind.MEETING,
                organisation=ministry,
                occurred_at=timezone.now() - timedelta(days=2),
            )

        plan = [
            (ActionKind.DO, DateSemantics.DEADLINE, "Koosta ja saada koja arvamus", 5),
            (ActionKind.WAIT, DateSemantics.REVIEW_ON, "Ootan ministeeriumi uut sõnastust", 9),
            (ActionKind.DO, DateSemantics.DEADLINE, "Vasta Riigikogu komisjonile", -3),
            (ActionKind.MONITOR, DateSemantics.REVIEW_ON, "Jälgi rakendusaktide koostamist", 21),
            (
                ActionKind.WAIT,
                DateSemantics.EXPECTED_AROUND,
                "Eelnõu jõuab eeldatavasti valitsusse",
                40,
            ),
        ]
        # One Matter deliberately has no next action, so Tähelepanu is not empty.
        if index % 6 != 5:
            kind, semantics, text, offset = plan[index % len(plan)]
            set_next_action(
                matter=matter,
                text=text,
                kind=kind,
                date_semantics=semantics,
                target_date=date.today() + timedelta(days=offset),
                actor=owner,
            )

        if index % 4 == 0:
            submission = create_submission(
                matter=matter,
                title=f"Koja arvamus eelnõule {matter.display_reference}",
                kind=SubmissionKind.FORMAL_OPINION,
                actor=owner,
                recipients=[ministry],
                channel="EIS",
            )
            attach_final_evidence(
                submission=submission,
                content=b"%PDF-1.4 synthetic final opinion",
                original_filename=f"koja-arvamus-{index + 1}.pdf",
                mime_type="application/pdf",
                actor=owner,
            )
            submission.refresh_from_db()
            mark_submission_sent(submission=submission, actor=owner)

    def _seed_archive_matters(self, *, company: Organisation) -> None:
        """Archive rows: verbatim provenance, no invented modern fields."""
        batch = ImportBatch.objects.create(
            source_system="EXCEL_TOOD_EELNOUDEGA",
            source_file_name="synthetic-workbook.xlsx",
            source_snapshot_sha256="0" * 64,
            importer_version="0.0.0-synthetic",
            contract_version="draft",
            started_at=timezone.now(),
            finished_at=timezone.now(),
            source_row_count=2,
            created_matter_count=2,
            matched_count=2,
            reconciliation_status=ReconciliationStatus.COMPLETED,
            notes="Sünteetiline näidispartii arenduskeskkonnale.",
        )

        for index, year in enumerate((2014, 2019)):
            matter = create_matter(
                title=f"Arhiiviteema {year}: sünteetiline registrikirje",
                assign_reference=False,
                record_mode=RecordMode.ARCHIVE,
                origin=MatterOrigin.LEGACY_IMPORT,
                data_quality_tier=DataQualityTier.TIER_3_REGISTER_ARCHIVE,
                source_era=str(year),
                reporting_year=year,
                source_organisation=company if index else None,
            )
            MatterSourceReference.objects.create(
                matter=matter,
                import_batch=batch,
                source_system="EXCEL_TOOD_EELNOUDEGA",
                source_file_name="synthetic-workbook.xlsx",
                source_sheet=str(year),
                source_row_number=index + 2,
                source_row_raw={
                    "NR": f"{year}_{index + 1}",
                    "PEALKIRI": f"Arhiiviteema {year}",
                    # Anomalies are preserved, not cleaned.
                    "KELLELT" if year < 2020 else "KELLELE": "Näidisministeerium",
                    "SAABUS": "43831" if year == 2019 else "",
                    "KÜSITUD": "0",
                    "VASTAS": "3",
                },
                source_title=f"Arhiiviteema {year}",
                source_date_raw="43831" if year == 2019 else "",
                match_method=MatchMethod.REFERENCE_TOKEN,
                match_confidence="0.900",
            )
