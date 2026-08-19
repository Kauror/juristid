"""Synthetic factories.

Every value here is invented. No Koda, member or otherwise confidential data
may appear in fixtures (master specification 5.3, 23.5).
"""

from __future__ import annotations

import factory
from django.utils import timezone

from app.accounts.enums import UserRole
from app.accounts.models import User
from app.core.enums import Visibility
from app.documents.enums import DocumentRole
from app.documents.models import Document
from app.legacy_import.models import ImportBatch, MatchMethod, MatterSourceReference
from app.matters.entry_enums import EntryKind
from app.matters.enums import MatterOrigin, RecordMode
from app.matters.models import Entry, Matter
from app.organisations.models import Organisation, OrganisationType
from app.submissions.enums import SubmissionKind, SubmissionStatus
from app.submissions.models import Submission
from app.taxonomy.models import PolicyArea, Tag
from app.workflow.enums import ActionKind, ActionStatus, DateSemantics
from app.workflow.models import NextAction, StageVocabulary


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        django_get_or_create = ("upn",)
        skip_postgeneration_save = True

    upn = factory.Sequence(lambda n: f"kasutaja{n}@example.invalid")
    display_name = factory.Sequence(lambda n: f"Testkasutaja {n}")
    role = UserRole.SPECIALIST
    is_synthetic = True
    is_active = True


class DepartmentHeadFactory(UserFactory):
    role = UserRole.DEPARTMENT_HEAD
    display_name = factory.Sequence(lambda n: f"Testosakonnajuht {n}")


class AdministratorFactory(UserFactory):
    role = UserRole.ADMINISTRATOR
    is_staff = True
    display_name = factory.Sequence(lambda n: f"Testadministraator {n}")


class OrganisationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Organisation

    name = factory.Sequence(lambda n: f"Näidisorganisatsioon {n}")
    organisation_type = OrganisationType.MINISTRY


class PolicyAreaFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PolicyArea

    key = factory.Sequence(lambda n: f"valdkond-{n}")
    name_et = factory.Sequence(lambda n: f"Valdkond {n}")


class TagFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Tag

    key = factory.Sequence(lambda n: f"silt-{n}")
    name_et = factory.Sequence(lambda n: f"Silt {n}")


class StageFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = StageVocabulary

    key = factory.Sequence(lambda n: f"etapp-{n}")
    label_et = factory.Sequence(lambda n: f"Etapp {n}")
    is_provisional = True


class MatterFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Matter

    title = factory.Sequence(lambda n: f"Näidisteema {n}")
    record_mode = RecordMode.FULL
    origin = MatterOrigin.NATIVE
    visibility = Visibility.NORMAL
    reference_year = 2026
    reference_number = factory.Sequence(lambda n: n + 1)
    owner = factory.SubFactory(UserFactory)


class ArchiveMatterFactory(MatterFactory):
    """A historical register row: modern fields deliberately absent."""

    record_mode = RecordMode.ARCHIVE
    origin = MatterOrigin.LEGACY_IMPORT
    reference_year = None
    reference_number = None
    owner = None
    received_date = None
    response_deadline = None
    stage = None


class DocumentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Document

    matter = factory.SubFactory(MatterFactory)
    title = factory.Sequence(lambda n: f"Näidisdokument {n}")
    role = DocumentRole.INCOMING_AUTHORITY


class ImportBatchFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ImportBatch

    source_system = "EXCEL_TOOD_EELNOUDEGA"
    importer_version = "0.0.0-test"
    contract_version = "test"
    started_at = factory.LazyFunction(timezone.now)


class MatterSourceReferenceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MatterSourceReference

    matter = factory.SubFactory(MatterFactory)
    import_batch = factory.SubFactory(ImportBatchFactory)
    source_system = "EXCEL_TOOD_EELNOUDEGA"
    source_sheet = "2019"
    source_row_number = 12
    match_method = MatchMethod.REFERENCE_TOKEN


class EntryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Entry

    matter = factory.SubFactory(MatterFactory)
    author = factory.SubFactory(UserFactory)
    kind = EntryKind.NOTE
    occurred_at = factory.LazyFunction(timezone.now)
    body = "<p>Sünteetiline sissekanne.</p>"


class NextActionFactory(factory.django.DjangoModelFactory):
    """A raw open action. Prefer the service for anything testing behaviour."""

    class Meta:
        model = NextAction

    matter = factory.SubFactory(MatterFactory)
    text = factory.Sequence(lambda n: f"Naidistegevus {n}")
    kind = ActionKind.DO
    date_semantics = DateSemantics.DEADLINE
    status = ActionStatus.OPEN


class SubmissionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Submission

    matter = factory.SubFactory(MatterFactory)
    title = factory.Sequence(lambda n: f"Naidisarvamus {n}")
    kind = SubmissionKind.FORMAL_OPINION
    status = SubmissionStatus.DRAFT
