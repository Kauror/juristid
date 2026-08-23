"""Real business data and development data are told apart, and stay apart.

Three properties are pinned here, and they are the three the feature exists for.

1. **Nothing changes for real work.** Every existing row, every importer and
   every fixture keeps producing REAL without being asked to.
2. **A historical record can never become TEST.** Refused by the service and
   refused again by the database, because the service is not the only thing
   that can write the column.
3. **The purge planner reads and never writes.** It is an inventory of a
   deletion that is deliberately not implemented, so a test that let it write
   one row would be testing a different command.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.documents.enums import DocumentRole
from app.documents.models import Document, DocumentVersion
from app.documents.services import add_evidence_version, create_document, set_legal_hold
from app.matters import purge, selectors
from app.matters.enums import MatterDataClass, MatterOrigin, RecordMode
from app.matters.forms import MatterCreateForm
from app.matters.models import Matter, MatterReferenceSequence
from app.matters.services import (
    create_imported_matter,
    create_matter,
    set_matter_data_class,
)
from app.matters.timeline import TIMELINE_EVENT_TYPES
from app.submissions.enums import SubmissionKind, SubmissionStatus
from app.submissions.models import Submission
from tests import factories

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")
REGISTER = reverse("matters:matter_list")
PLAIN_TEXT = "text/plain"


def _test_matter(**kwargs) -> Matter:
    return factories.MatterFactory(data_class=MatterDataClass.TEST, **kwargs)


def _plan_output(*args: str) -> str:
    out = StringIO()
    call_command("purge_test_data", "--plan", *args, stdout=out, stderr=out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# The migration, and what it does to what is already there
# ---------------------------------------------------------------------------


def test_the_column_arrives_without_a_data_migration():
    """No RunPython, no RunSQL. REAL is the column default and that is enough.

    If this ever needed a backfill it would mean the default had stopped
    classifying existing rows, which is the one thing the migration must not
    get wrong.
    """
    loader = MigrationExecutor(connection).loader
    migration = loader.get_migration("matters", "0007_matter_data_class")
    kinds = {type(operation).__name__ for operation in migration.operations}
    assert "RunPython" not in kinds
    assert "RunSQL" not in kinds
    assert kinds == {"AddField", "AddConstraint"}


def test_a_row_written_without_a_class_is_real():
    """The upgrade path, exercised the way an existing row experiences it.

    A row created with no mention of `data_class` at all is what every table in
    the production database looked like the moment before the migration ran.
    """
    matter = Matter.objects.create(title="Enne migratsiooni loodud")
    matter.refresh_from_db()
    assert matter.data_class == MatterDataClass.REAL


def test_the_reference_sequence_is_untouched_by_the_new_column():
    """The human numbering has nothing to do with the data class.

    Named here because it is the thing a future purge must *not* rewind: gaps
    in `YYYY_N` are valid, and reusing a deleted number would hand a lawyer a
    reference somebody else already has in a filename (Agent-C brief 60).
    """
    assert not hasattr(MatterReferenceSequence, "data_class")


# ---------------------------------------------------------------------------
# Vocabulary and the historical-import guard
# ---------------------------------------------------------------------------


def test_the_database_refuses_a_value_outside_the_vocabulary():
    matter = factories.MatterFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        Matter.objects.filter(pk=matter.pk).update(data_class="MAYBE")


def test_the_database_refuses_test_on_an_imported_row():
    """Not only the service. A bulk update bypasses every Python guard."""
    archive = factories.ArchiveMatterFactory()
    assert archive.origin != MatterOrigin.NATIVE
    with pytest.raises(IntegrityError), transaction.atomic():
        Matter.objects.filter(pk=archive.pk).update(data_class=MatterDataClass.TEST)


def test_the_service_refuses_to_create_an_imported_test_matter():
    with pytest.raises(DomainError):
        create_matter(
            title="Ajalooline",
            origin=MatterOrigin.LEGACY_IMPORT,
            data_class=MatterDataClass.TEST,
            assign_reference=False,
        )


def test_the_service_refuses_to_reclassify_an_imported_row(specialist):
    archive = factories.ArchiveMatterFactory()
    with pytest.raises(DomainError):
        set_matter_data_class(matter=archive, data_class=MatterDataClass.TEST, actor=specialist)
    archive.refresh_from_db()
    assert archive.data_class == MatterDataClass.REAL


def test_an_unknown_class_is_refused_by_the_service(specialist):
    matter = factories.MatterFactory()
    with pytest.raises(DomainError):
        set_matter_data_class(matter=matter, data_class="", actor=specialist)
    with pytest.raises(DomainError):
        set_matter_data_class(matter=matter, data_class="MAYBE", actor=specialist)


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def test_a_matter_created_through_the_service_is_real_by_default():
    matter = create_matter(title="Tavaline töö")
    assert matter.data_class == MatterDataClass.REAL


def test_an_imported_matter_is_real(specialist):
    """The importers were not changed, and must not need to be.

    This is the regression that protects the historical register and the final
    register cutover from the day somebody makes TEST the default by accident
    (Agent-C brief 29).
    """
    matter = create_imported_matter(
        title="Registririda",
        reference_year=2019,
        reference_number=7,
        actor=specialist,
    )
    assert matter.data_class == MatterDataClass.REAL
    assert matter.origin == MatterOrigin.LEGACY_IMPORT


def test_the_create_form_defaults_to_real(specialist):
    form = MatterCreateForm({"title": "Päris teema"}, viewer=specialist)
    assert form.is_valid(), form.errors
    assert form.data_class == MatterDataClass.REAL


def test_the_create_form_reads_the_checkbox(specialist):
    form = MatterCreateForm({"title": "Arendusteema", "is_test_data": "on"}, viewer=specialist)
    assert form.is_valid(), form.errors
    assert form.data_class == MatterDataClass.TEST


def test_creating_through_the_page_without_the_box_gives_real(signed_in):
    signed_in.post(CREATE, {"title": "Päris teema"})
    matter = Matter.objects.get(title="Päris teema")
    assert matter.data_class == MatterDataClass.REAL


def test_creating_through_the_page_with_the_box_gives_a_native_test_matter(signed_in):
    signed_in.post(CREATE, {"title": "Arendusteema", "is_test_data": "on"})
    matter = Matter.objects.get(title="Arendusteema")
    assert matter.data_class == MatterDataClass.TEST
    assert matter.origin == MatterOrigin.NATIVE


def test_a_child_of_a_test_matter_carries_no_flag_of_its_own(specialist):
    """One classification owner. A child is test data when its Matter is.

    Asserted as an absence, because the failure this prevents is a REAL Matter
    holding a TEST submission — a state that must not be representable
    (Agent-C brief 20).
    """
    matter = _test_matter(owner=specialist)
    entry = factories.EntryFactory(matter=matter)
    submission = factories.SubmissionFactory(matter=matter)
    document = create_document(matter=matter, title="Fail", role=DocumentRole.OTHER)

    for record in (entry, submission, document):
        assert not hasattr(record, "data_class")
        assert not hasattr(record, "is_test_data")
    assert matter.is_test_data


def test_the_creation_event_records_the_class_without_a_second_event(specialist):
    matter = create_matter(title="Arendusteema", actor=specialist, data_class=MatterDataClass.TEST)
    created = ChangeEvent.objects.get(matter=matter, event_type=ChangeEventType.MATTER_CREATED)
    assert created.payload["data_class"] == MatterDataClass.TEST
    assert not ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.MATTER_DATA_CLASS_CHANGED
    ).exists()


# ---------------------------------------------------------------------------
# Reclassifying an existing Matter
# ---------------------------------------------------------------------------


def test_marking_a_real_matter_as_test_is_audited(specialist):
    matter = factories.MatterFactory(owner=specialist)
    set_matter_data_class(matter=matter, data_class=MatterDataClass.TEST, actor=specialist)

    matter.refresh_from_db()
    assert matter.data_class == MatterDataClass.TEST
    event = ChangeEvent.objects.get(
        matter=matter, event_type=ChangeEventType.MATTER_DATA_CLASS_CHANGED
    )
    assert event.payload == {"from": MatterDataClass.REAL, "to": MatterDataClass.TEST}
    assert event.actor == specialist


def test_marking_a_test_matter_as_real_works_too(specialist):
    matter = _test_matter(owner=specialist)
    set_matter_data_class(matter=matter, data_class=MatterDataClass.REAL, actor=specialist)

    matter.refresh_from_db()
    assert matter.data_class == MatterDataClass.REAL
    event = ChangeEvent.objects.get(
        matter=matter, event_type=ChangeEventType.MATTER_DATA_CLASS_CHANGED
    )
    assert event.payload == {"from": MatterDataClass.TEST, "to": MatterDataClass.REAL}


def test_reclassifying_to_the_same_value_records_nothing(specialist):
    matter = factories.MatterFactory(owner=specialist)
    set_matter_data_class(matter=matter, data_class=MatterDataClass.REAL, actor=specialist)
    assert not ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.MATTER_DATA_CLASS_CHANGED
    ).exists()


def test_reclassifying_does_not_touch_children(specialist):
    """Testness is derived, so nothing below the Matter is rewritten."""
    matter = factories.MatterFactory(owner=specialist)
    entry = factories.EntryFactory(matter=matter)
    before = entry.updated_at

    set_matter_data_class(matter=matter, data_class=MatterDataClass.TEST, actor=specialist)

    entry.refresh_from_db()
    assert entry.updated_at == before


def test_the_change_is_not_in_the_professional_timeline():
    """Data management is not authored chronology (Agent-C brief 19)."""
    assert ChangeEventType.MATTER_DATA_CLASS_CHANGED not in TIMELINE_EVENT_TYPES


def test_the_endpoint_reclassifies(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    url = reverse("matters:set_data_class", kwargs={"pk": matter.pk})

    response = signed_in.post(url, {"data_class": MatterDataClass.TEST})

    assert response.status_code == 302
    matter.refresh_from_db()
    assert matter.data_class == MatterDataClass.TEST


def test_the_endpoint_refuses_an_imported_row(signed_in, specialist):
    archive = factories.ArchiveMatterFactory(owner=specialist)
    url = reverse("matters:set_data_class", kwargs={"pk": archive.pk})

    signed_in.post(url, {"data_class": MatterDataClass.TEST})

    archive.refresh_from_db()
    assert archive.data_class == MatterDataClass.REAL


def test_an_omitted_value_does_not_silently_become_real(signed_in, specialist):
    matter = _test_matter(owner=specialist)
    url = reverse("matters:set_data_class", kwargs={"pk": matter.pk})

    signed_in.post(url, {})

    matter.refresh_from_db()
    assert matter.data_class == MatterDataClass.TEST


def test_the_endpoint_needs_a_visible_matter(client, specialist, other_specialist):
    hidden = factories.MatterFactory(owner=other_specialist, visibility=Visibility.RESTRICTED)
    client.force_login(specialist)

    response = client.post(
        reverse("matters:set_data_class", kwargs={"pk": hidden.pk}),
        {"data_class": MatterDataClass.TEST},
    )

    assert response.status_code == 404
    hidden.refresh_from_db()
    assert hidden.data_class == MatterDataClass.REAL


def test_the_whole_page_reflects_a_reclassification(signed_in, specialist):
    """The badge, the rail and the row must not be able to disagree.

    A partial swap would leave the header still saying nothing about a Matter
    that had just become development data (Agent-C brief 22).
    """
    matter = factories.MatterFactory(owner=specialist)
    signed_in.post(
        reverse("matters:set_data_class", kwargs={"pk": matter.pk}),
        {"data_class": MatterDataClass.TEST},
        follow=True,
    )

    page = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()
    assert "badge--test" in page
    assert "Testandmed" in page


# ---------------------------------------------------------------------------
# The query vocabulary
# ---------------------------------------------------------------------------


def test_the_helpers_partition_the_population(specialist):
    factories.MatterFactory(owner=specialist)
    factories.MatterFactory(owner=specialist)
    _test_matter(owner=specialist)
    _test_matter(owner=specialist)

    assert Matter.objects.real_data().count() == 2
    assert Matter.objects.test_data().count() == 2
    assert Matter.objects.count() == 4


def test_the_helpers_compose_with_authorization(specialist, other_specialist):
    """Visibility first, class second, and neither can widen the other."""
    factories.MatterFactory(owner=specialist)
    _test_matter(owner=specialist)
    _test_matter(owner=other_specialist, visibility=Visibility.RESTRICTED)

    visible = Matter.objects.visible_to(specialist)
    assert visible.real_data().count() == 1
    assert visible.test_data().count() == 1
    # The restricted test matter belongs to somebody else and stays invisible.
    assert Matter.objects.test_data().count() == 2


def test_visibility_does_not_secretly_exclude_test_data(specialist):
    """A developer must be able to open the record they just created."""
    matter = _test_matter(owner=specialist)
    assert Matter.objects.visible_to(specialist).filter(pk=matter.pk).exists()


def test_the_helpers_compose_with_the_record_mode_filters(specialist):
    factories.MatterFactory(owner=specialist, record_mode=RecordMode.FULL)
    _test_matter(owner=specialist, record_mode=RecordMode.FULL)

    assert Matter.objects.full_records().test_data().count() == 1
    assert Matter.objects.full_records().real_data().count() == 1
    assert Matter.objects.archive_records().test_data().count() == 0


# ---------------------------------------------------------------------------
# The register filter
# ---------------------------------------------------------------------------


def _register_ids(client, **params) -> set:
    response = client.get(REGISTER, params)
    assert response.status_code == 200
    return {matter.pk for matter in response.context["page"].object_list}


def test_the_register_shows_both_classes_by_default(signed_in, specialist):
    real = factories.MatterFactory(owner=specialist)
    test = _test_matter(owner=specialist)
    assert _register_ids(signed_in) == {real.pk, test.pk}


def test_the_register_can_be_narrowed_to_real(signed_in, specialist):
    real = factories.MatterFactory(owner=specialist)
    _test_matter(owner=specialist)
    assert _register_ids(signed_in, andmed=selectors.DATA_CLASS_REAL) == {real.pk}


def test_the_register_can_be_narrowed_to_test(signed_in, specialist):
    factories.MatterFactory(owner=specialist)
    test = _test_matter(owner=specialist)
    assert _register_ids(signed_in, andmed=selectors.DATA_CLASS_TEST) == {test.pk}


def test_koik_restricts_nothing(signed_in, specialist):
    real = factories.MatterFactory(owner=specialist)
    test = _test_matter(owner=specialist)
    assert _register_ids(signed_in, andmed=selectors.DATA_CLASS_ALL) == {real.pk, test.pk}


def test_an_unreadable_value_falls_back_to_the_default(signed_in, specialist):
    real = factories.MatterFactory(owner=specialist)
    test = _test_matter(owner=specialist)
    assert _register_ids(signed_in, andmed="rämps") == {real.pk, test.pk}


def test_the_filter_cannot_widen_visibility(signed_in, specialist, other_specialist):
    """Authorization is applied before the class narrowing, always."""
    factories.MatterFactory(owner=other_specialist, visibility=Visibility.RESTRICTED)
    mine = _test_matter(owner=specialist)
    assert _register_ids(signed_in, andmed=selectors.DATA_CLASS_ALL) == {mine.pk}


def test_the_selection_survives_in_the_url(signed_in, specialist):
    _test_matter(owner=specialist)
    response = signed_in.get(REGISTER, {"andmed": selectors.DATA_CLASS_TEST})
    chips = {chip["name"]: chip["value"] for chip in response.context["active_filters"]}
    assert chips["andmed"] == "Test"
    assert response.context["filters"]["andmed"] == selectors.DATA_CLASS_TEST


def test_the_default_is_not_shown_as_an_active_filter(signed_in, specialist):
    factories.MatterFactory(owner=specialist)
    response = signed_in.get(REGISTER, {"andmed": selectors.DATA_CLASS_ALL})
    assert "andmed" not in {chip["name"] for chip in response.context["active_filters"]}


# ---------------------------------------------------------------------------
# The badge
# ---------------------------------------------------------------------------


def test_a_test_matter_says_so_on_its_page(signed_in, specialist):
    matter = _test_matter(owner=specialist)
    response = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    assert "badge--test" in response.content.decode()


def test_a_real_matter_carries_no_test_marker(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    response = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert "badge--test" not in response.content.decode()


def test_the_badge_does_not_disclose_a_restricted_matter(client, other_specialist, specialist):
    """The badge rides on the detail page and inherits its authorization."""
    hidden = _test_matter(owner=other_specialist, visibility=Visibility.RESTRICTED)
    client.force_login(specialist)
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": hidden.pk}))
    assert response.status_code == 404


def test_the_register_row_carries_the_marker(signed_in, specialist):
    _test_matter(owner=specialist)
    response = signed_in.get(REGISTER, {"andmed": selectors.DATA_CLASS_TEST})
    assert "badge--test" in response.content.decode()


# ---------------------------------------------------------------------------
# The purge plan
# ---------------------------------------------------------------------------


def test_the_plan_writes_nothing(specialist, evidence_root):
    """The property the whole command rests on, asserted as a count."""
    matter = _test_matter(owner=specialist)
    factories.EntryFactory(matter=matter)
    document = create_document(matter=matter, title="Fail", role=DocumentRole.OTHER)
    add_evidence_version(
        document=document,
        content=b"Sunteetiline toend.",
        original_filename="fail.txt",
        mime_type=PLAIN_TEXT,
        uploaded_by=specialist,
    )

    before = {
        model.__name__: model.objects.count()
        for model in (Matter, Document, DocumentVersion, ChangeEvent)
    }
    _plan_output()
    after = {
        model.__name__: model.objects.count()
        for model in (Matter, Document, DocumentVersion, ChangeEvent)
    }
    assert before == after


def test_the_plan_inventories_what_a_test_matter_owns(specialist, evidence_root):
    matter = _test_matter(owner=specialist)
    factories.EntryFactory(matter=matter)
    # A DEADLINE needs a date; the database refuses one without.
    factories.NextActionFactory(
        matter=matter, responsible=specialist, target_date=timezone.localdate()
    )
    factories.SubmissionFactory(matter=matter)
    factories.ImportantDateFactory(matter=matter)
    factories.WorkVictoryFactory(matter=matter)
    document = create_document(matter=matter, title="Fail", role=DocumentRole.OTHER)
    add_evidence_version(
        document=document,
        content=b"Sunteetiline toend.",
        original_filename="fail.txt",
        mime_type=PLAIN_TEXT,
        uploaded_by=specialist,
    )
    # A second, real matter with the same shape. None of it may appear.
    other = factories.MatterFactory(owner=specialist)
    factories.EntryFactory(matter=other)
    factories.SubmissionFactory(matter=other)

    plan = purge.build_purge_plan()

    assert plan.test_matters == 1
    assert plan.count_of("matters.Matter") == 1
    assert plan.count_of("matters.Entry") == 1
    assert plan.count_of("workflow.NextAction") == 1
    assert plan.count_of("submissions.Submission") == 1
    assert plan.count_of("intelligence.MatterImportantDate") == 1
    assert plan.count_of("intelligence.MatterWorkVictory") == 1
    assert plan.count_of("documents.Document") == 1
    assert plan.count_of("documents.DocumentVersion") == 1


def test_the_plan_discovers_relations_rather_than_listing_them(specialist):
    """The inventory is built from Django's metadata, not from a hand list.

    Asserted through a relation this brief never named: the implicit through
    table behind `Matter.policy_areas`. A planner that enumerated tables by
    hand would miss it, and would go on missing whatever is added next month
    (Agent-C brief 33).
    """
    matter = _test_matter(owner=specialist)
    matter.policy_areas.add(factories.PolicyAreaFactory())

    plan = purge.build_purge_plan()
    labels = {group.label for group in plan.owned}
    assert "matters.Matter_policy_areas" in labels


def test_the_plan_reports_evidence_without_touching_the_bytes(specialist, evidence_root):
    matter = _test_matter(owner=specialist)
    document = create_document(matter=matter, title="Fail", role=DocumentRole.OTHER)
    version = add_evidence_version(
        document=document,
        content=b"Sunteetiline toend.",
        original_filename="fail.txt",
        mime_type=PLAIN_TEXT,
        uploaded_by=specialist,
    )

    plan = purge.build_purge_plan()
    summary = next(item for item in plan.evidence if item.label == "DocumentVersion")
    assert summary.objects == 1
    assert summary.distinct_keys == 1
    assert summary.total_bytes == version.size_bytes


def test_the_plan_reports_the_append_only_audit_dependency(specialist):
    matter = create_matter(title="Arendusteema", actor=specialist, data_class=MatterDataClass.TEST)
    assert ChangeEvent.objects.filter(matter=matter).exists()

    plan = purge.build_purge_plan()
    append_only = {group.label for group in plan.append_only_rows}
    assert "audit.ChangeEvent" in append_only
    assert plan.count_of("audit.ChangeEvent") >= 1
    # A dependency, not a defect: audit history alone must not blockade a plan,
    # or the safe verdict would be unreachable for every matter ever created.
    assert not plan.is_blocked

    output = _plan_output()
    assert "deletion-dependency\tappend-only\taudit.ChangeEvent" in output
    assert "audit.ChangeEvent" in output


def test_a_legal_hold_blocks_the_plan(specialist, evidence_root):
    matter = _test_matter(owner=specialist)
    document = create_document(matter=matter, title="Fail", role=DocumentRole.OTHER)
    set_legal_hold(document=document, on=True, reason="Sünteetiline põhjus", actor=specialist)

    plan = purge.build_purge_plan()
    assert plan.is_blocked
    assert any(blocker.category == purge.BLOCKED_BY_LEGAL_HOLD for blocker in plan.blockers)
    assert purge.BLOCKED_BY_LEGAL_HOLD in _plan_output()


def test_a_real_submission_standing_on_test_evidence_blocks_the_plan(specialist, evidence_root):
    """The cross-boundary check, in the shape it actually takes.

    A real Matter's submission is finalised on a version that lives under a
    test Matter. Deleting that version would abort against the PROTECT — and if
    it did not, it would take the real record's evidence with it
    (Agent-C brief 36, 56).
    """
    test_matter = _test_matter(owner=specialist)
    document = create_document(matter=test_matter, title="Fail", role=DocumentRole.OTHER)
    version = add_evidence_version(
        document=document,
        content=b"Sunteetiline toend.",
        original_filename="fail.txt",
        mime_type=PLAIN_TEXT,
        uploaded_by=specialist,
    )

    real_matter = factories.MatterFactory(owner=specialist)
    Submission.objects.create(
        matter=real_matter,
        title="Päris arvamus",
        kind=SubmissionKind.FORMAL_OPINION,
        status=SubmissionStatus.DRAFT,
        final_version=version,
    )

    plan = purge.build_purge_plan()
    assert plan.is_blocked
    blockers = [
        blocker for blocker in plan.blockers if blocker.category == purge.BLOCKED_BY_REAL_REFERENCE
    ]
    assert any(blocker.label == "submissions.Submission.final_version" for blocker in blockers)

    # And the key is never presented as safe to delete.
    output = _plan_output()
    assert purge.BLOCKED_BY_REAL_REFERENCE in output
    assert "SAFE CANDIDATE SET" not in output
    assert version.storage_key not in output


def test_an_invalid_classification_blocks_the_plan(specialist):
    """The database constraint should make this unreachable. Check anyway."""
    archive = factories.ArchiveMatterFactory(owner=specialist)
    # Dropped inside the test transaction, which the test runner rolls back —
    # the constraint is not removed from anything but this one test's view.
    with connection.cursor() as cursor:
        cursor.execute("ALTER TABLE matters_matter DROP CONSTRAINT matters_test_data_is_native")
    Matter.objects.filter(pk=archive.pk).update(data_class=MatterDataClass.TEST)

    plan = purge.build_purge_plan()

    assert plan.is_blocked
    assert any(
        blocker.category == purge.BLOCKED_INVALID_TEST_CLASSIFICATION for blocker in plan.blockers
    )


def test_an_archive_binary_is_never_test_owned(specialist):
    """A link is test-contextual; the evidence it points at is not.

    The archive's binaries belong to the archive. A test Matter that links one
    borrows it, and a purge that treated a borrowed piece of real evidence as
    its own would destroy correspondence nobody can recover
    (Agent-C brief 21, 35, 57).
    """
    from app.legacy_import.opinion_binary import (
        OpinionArchiveBinary,
        OpinionArchiveMatterLink,
    )
    from app.legacy_import.opinion_enums import ArchiveLinkBasis

    matter = _test_matter(owner=specialist)
    binary = OpinionArchiveBinary.objects.create(
        sha256="c" * 64,
        size_bytes=512,
        mime_type="application/pdf",
        storage_key="opinion-archive/cc/cc/" + "c" * 64,
        source_archive_sha256="a" * 64,
        materialized_at=timezone.now(),
    )
    OpinionArchiveMatterLink.objects.create(
        matter=matter,
        binary=binary,
        basis=ArchiveLinkBasis.REVIEWED,
        linked_at=timezone.now(),
    )

    plan = purge.build_purge_plan()
    labels = {group.label for group in plan.owned}

    assert "legacy_import.OpinionArchiveMatterLink" in labels
    assert plan.count_of("legacy_import.OpinionArchiveMatterLink") == 1
    assert "legacy_import.OpinionArchiveBinary" not in labels
    assert not any(item.label == "OpinionArchiveBinary" for item in plan.evidence)
    assert OpinionArchiveBinary.objects.filter(pk=binary.pk).exists()


def test_shared_and_archive_roots_are_unreachable_by_construction():
    """Named in the report so a reader can see they were considered."""
    assert "legacy_import.OpinionArchiveBinary" in purge.NEVER_OWNED
    assert "legacy_import.ImportBatch" in purge.NEVER_OWNED
    assert "matters.MatterReferenceSequence" in purge.NEVER_OWNED


def test_the_plan_uses_the_central_evidence_registry():
    """One definition of "referenced evidence", not a second incompatible one.

    If a new canonical holder of evidence bytes is registered there, the plan
    learns about it at the same moment the pruner does (Agent-C brief 58).
    """
    from app.documents.references import EVIDENCE_REFERENCES

    labels = {reference.label for reference in EVIDENCE_REFERENCES}
    assert labels == {"DocumentVersion", "OpinionArchiveBinary"}


def test_an_empty_plan_is_safe_and_says_nothing_about_real_matters(specialist):
    factories.MatterFactory(owner=specialist)
    output = _plan_output()
    assert "test-matters\t0" in output
    assert purge.BLOCKED_BY_REAL_REFERENCE not in output


def test_a_clean_test_matter_reaches_a_safe_candidate_set(specialist):
    matter = create_matter(title="Arendusteema", actor=specialist, data_class=MatterDataClass.TEST)
    factories.EntryFactory(matter=matter)

    output = _plan_output()
    assert "SAFE CANDIDATE SET" in output
    assert "no deletion is implemented" in output


def test_the_plan_can_be_narrowed_to_one_matter(specialist):
    first = _test_matter(owner=specialist)
    _test_matter(owner=specialist)

    plan = purge.build_purge_plan([str(first.pk)])
    assert plan.test_matters == 1
    assert plan.count_of("matters.Matter") == 1

    by_reference = purge.build_purge_plan([first.display_reference])
    assert by_reference.test_matters == 1


def test_testness_is_never_inferred_from_a_title(specialist):
    factories.MatterFactory(owner=specialist, title="TEST — palun kustuta")
    plan = purge.build_purge_plan()
    assert plan.test_matters == 0


def test_the_plan_is_deterministic(specialist, evidence_root):
    matter = _test_matter(owner=specialist)
    factories.EntryFactory(matter=matter)
    factories.SubmissionFactory(matter=matter)
    document = create_document(matter=matter, title="Fail", role=DocumentRole.OTHER)
    add_evidence_version(
        document=document,
        content=b"Sunteetiline toend.",
        original_filename="fail.txt",
        mime_type=PLAIN_TEXT,
        uploaded_by=specialist,
    )

    assert purge.build_purge_plan() == purge.build_purge_plan()
    assert _plan_output() == _plan_output()


def test_the_command_has_no_destructive_mode():
    """The absence is the decision, so the absence is what is asserted."""
    from app.matters.management.commands import purge_test_data

    parser = purge_test_data.Command().create_parser("manage.py", "purge_test_data")
    options = {action.dest for action in parser._actions}
    assert "plan" in options
    assert "apply" not in options
    assert "delete" not in options
    assert not hasattr(purge_test_data.Command, "_delete")


def test_the_plan_refuses_an_unreadable_matter_reference():
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        _plan_output("--matter", "mitte-uuid")
