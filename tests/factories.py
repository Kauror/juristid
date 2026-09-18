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
from app.intelligence.enums import EffectiveDateKind, FactStatus, WorkVictoryStatus
from app.intelligence.models import (
    MatterEffectiveDate,
    MatterImportantDate,
    MatterWorkVictory,
)
from app.legacy_import.models import ImportBatch, MatchMethod, MatterSourceReference
from app.matters.entry_enums import EntryKind
from app.matters.enums import MatterOrigin, RecordMode
from app.matters.models import Entry, Matter
from app.organisations.models import Organisation, OrganisationType
from app.submissions.enums import SubmissionKind, SubmissionStatus
from app.submissions.models import Submission
from app.taxonomy.models import PolicyArea, Tag
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics
from app.workflow.models import NextAction, StageVocabulary

#: «This factory decides the human reference», as distinct from «no reference».
#:
#: Its own object rather than `None`, because `None` is a value a caller passes on
#: purpose — see `MatterFactory._resolve_reference`.
ALLOCATE_REFERENCE: object = object()


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


class ReaderFactory(UserFactory):
    """A colleague who may read the register and change nothing in it."""

    role = UserRole.READER
    display_name = factory.Sequence(lambda n: f"Testlugeja {n}")


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
    #: Left unset, and filled by :meth:`_create` from the **real allocator**.
    #:
    #: This was ``factory.Sequence(lambda n: n + 1)``, and that is the defect.
    #: `factory.Sequence` counts **per process**: it starts at 0 when pytest
    #: imports this module and never resets, because factory_boy knows nothing
    #: about test boundaries. `MatterReferenceSequence` is a **database row**, and
    #: the `django_db` transaction rolls it back to empty before every test. So
    #: the two counters disagree about what has been handed out, and they
    #: disagree *as a function of what ran earlier in the process*.
    #:
    #: Concretely: in a test where this factory is the first Matter-maker of the
    #: whole session, the sequence yields ``2026/1`` — and a service call in the
    #: same test asks the freshly-rolled-back allocator, which also says ``1``.
    #: `matters_unique_human_reference` then fires, and the test fails for a
    #: reason that has nothing to do with what it is testing. Run the same file
    #: after any module that made a Matter and it passes, because the process
    #: counter has moved on. That is a test suite whose result depends on its own
    #: execution order, which is the one property a suite must not have.
    #:
    #: The fix is to stop having two counters. Everything here draws from
    #: `allocate_matter_reference`, which is the authoritative one, takes the row
    #: lock production takes, and lives in the database — so it resets with every
    #: test and can never hand the same number to a factory and to a service.
    #: **Production semantics are untouched**: this calls the allocator, it does
    #: not change it.
    reference_number = ALLOCATE_REFERENCE
    owner = factory.SubFactory(UserFactory)

    @classmethod
    def _resolve_reference(cls, kwargs: dict, *, allocate: bool) -> dict:
        """Turn the :data:`ALLOCATE_REFERENCE` sentinel into an actual number.

        The sentinel is why this is not simply ``reference_number = None``. A
        caller may pass ``reference_number=None`` **deliberately** — to assert
        that the database refuses a year without a number
        (`test_reference_year_and_number_must_be_set_together`) — and a default of
        `None` would make that request indistinguishable from «this factory is
        deciding». So the default is a sentinel nobody passes, an explicit `None`
        stays `None`, and an explicit number is left exactly as given.

        ``allocate`` is false on `build()`: a Matter built without a database must
        still cost no query, so the sentinel resolves to `None` there instead of
        reaching the allocator.
        """
        if kwargs.get("reference_number") is not ALLOCATE_REFERENCE:
            return kwargs
        year = kwargs.get("reference_year")
        if year is None or not allocate:
            # A row with no year gets no number — `ArchiveMatterFactory`, which is
            # what a historical register row looks like, and which must not be
            # handed a reference it never had.
            kwargs["reference_number"] = None
            return kwargs
        from django.db.models import Max

        from app.matters.models import Matter
        from app.matters.services import allocate_matter_reference, reserve_matter_reference

        # **Bring the sequence up to what the table actually holds, first.**
        #
        # Allocating alone is not enough, because the suite creates Matters by
        # three different routes and only one of them touches the counter.
        # `tests/synthetic_statistics.build_world` writes `2026/1` … `2026/6`
        # through `Matter.objects.create` — the model manager, not the service —
        # so `MatterReferenceSequence` has never heard of those rows. A factory
        # Matter built into that world would then be allocated `1` and collide
        # with the world's own first Teema, which is the original defect wearing
        # the other shoe.
        #
        # So the high-water mark is read from the rows themselves and handed to
        # `reserve_matter_reference` — the canonical service whose documented job
        # is «make sure the sequence for this year will never hand out this
        # number again», and which the register import already uses for exactly
        # this. It is idempotent and takes the same row lock as allocation.
        #
        # Reading it from the database rather than from a counter in this process
        # is what keeps the result independent of execution order: whatever the
        # test has created by whatever route, this sees it, and the transaction
        # rolls all of it back together.
        highest = Matter.objects.filter(reference_year=year).aggregate(
            highest=Max("reference_number")
        )["highest"]
        if highest is not None:
            reserve_matter_reference(year, highest)
        _year, number = allocate_matter_reference(year)
        kwargs["reference_number"] = number
        return kwargs

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        return super()._create(model_class, *args, **cls._resolve_reference(kwargs, allocate=True))

    @classmethod
    def _build(cls, model_class, *args, **kwargs):
        return super()._build(model_class, *args, **cls._resolve_reference(kwargs, allocate=False))

    @factory.post_generation
    def source_organisations(obj, create, extracted, **kwargs):
        """`KELLELT`, plural, written after the Matter has a primary key.

        A relation and not a column, so it cannot go through `Meta.model` the
        way every other field on this factory does.
        """
        if create and extracted:
            obj.source_organisations.set(extracted)


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


class ImportantDateFactory(factory.django.DjangoModelFactory):
    """A raw `Oluline tähtaeg`, exact by default.

    Prefer `app.intelligence.services.add_important_date` for anything testing
    behaviour: the service is what validates the period, and a factory that
    bypassed it would let a test assert against a shape the product cannot
    create.
    """

    class Meta:
        model = MatterImportantDate

    matter = factory.SubFactory(MatterFactory)
    title = factory.Sequence(lambda n: f"Naidistahtaeg {n}")
    date_value = factory.LazyFunction(timezone.localdate)
    period_end = factory.LazyAttribute(lambda record: record.date_value)
    date_precision = DatePrecision.EXACT
    status = FactStatus.ACTIVE


class EffectiveDateFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MatterEffectiveDate

    matter = factory.SubFactory(MatterFactory)
    kind = EffectiveDateKind.KNOWN_DATE
    date_value = factory.LazyFunction(timezone.localdate)
    period_end = factory.LazyAttribute(lambda record: record.date_value)
    date_precision = DatePrecision.EXACT
    description = "põhiosa"
    status = FactStatus.ACTIVE


class WorkVictoryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MatterWorkVictory

    matter = factory.SubFactory(MatterFactory)
    status = WorkVictoryStatus.CANDIDATE
    title = factory.Sequence(lambda n: f"Naidistoovoit {n}")
    date_precision = DatePrecision.YEAR
