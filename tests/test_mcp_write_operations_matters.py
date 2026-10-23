"""MCP writes on the Matter itself: `create_matter`, `update_matter`, `assign_matter`.

Each operation is proved the same way: `prepare` writes nothing and previews
exactly what will change; the commit goes through the canonical service and
leaves the same records and `ChangeEvent`s the web door leaves; the refusals
are the web path's (READER, an administrator, no `work.assign`, an invisible or
closed Matter); and a concurrent change makes the stored state stale.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.models import Matter, MatterAssignmentNotice
from app.matters.services import assign_matter, close_matter, set_matter_title
from app.mcp_writes.operations import REGISTRY
from app.workflow.models import MatterPlanStep, NextAction
from tests import factories
from tests.test_mcp_write_operations_support import (
    change,
    commit,
    execute_refusal,
    is_stale,
    prepare,
    preview_text,
    public_origin,  # noqa: F401
    refusal,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("public_origin")]


def _no_assign(**extra: Any) -> Any:
    from app.accounts.enums import UserRole

    return factories.UserFactory(
        role=UserRole.SPECIALIST, capability_overrides={"work.assign": "deny"}, **extra
    )


# ---------------------------------------------------------------------------
# create_matter
# ---------------------------------------------------------------------------


def test_create_prepare_writes_nothing_and_previews_the_new_teema(specialist):
    matters, events = Matter.objects.count(), ChangeEvent.objects.count()
    prepared = prepare(
        "create_matter",
        specialist,
        title="  Pakendiseaduse   muutmine ",
        brief_summary="Mõjutab pakendiettevõtjaid.",
    )
    assert (Matter.objects.count(), ChangeEvent.objects.count()) == (matters, events)
    assert prepared.canonical == {
        "title": "Pakendiseaduse muutmine",
        "brief_summary": "Mõjutab pakendiettevõtjaid.",
        "owner_id": str(specialist.pk),
    }
    assert change(prepared, "owner").proposed == specialist.display_name
    text = preview_text(prepared)
    for promise in (
        "Arvamuse tähtaega ei määrata",
        "Hetkeseisu ei määrata",
        "Järgmist tegevust ei looda",
    ):
        assert promise in text
    assert prepared.target_matter_id is None


def test_create_executes_file_new_matter_once(specialist):
    prepared = prepare("create_matter", specialist, title="Uus eelnõu", brief_summary="Kokkuvõte.")
    executed = commit("create_matter", specialist, prepared)
    matter = Matter.objects.get(pk=executed.record_id)
    assert matter.title == "Uus eelnõu"
    assert matter.brief_summary == "Kokkuvõte."
    assert matter.owner == specialist
    assert matter.display_reference == executed.matter_reference
    # Nothing invented: no deadline, no stage, no received date, no step, no outcome.
    assert matter.response_deadline is None
    assert matter.stage_id is None
    assert matter.received_date is None
    assert matter.disposition == ""
    assert matter.is_open
    assert not NextAction.objects.filter(matter=matter).exists()
    # The standard plan, as `Uus teema` seeds it — suggestions, not tasks.
    assert MatterPlanStep.objects.filter(matter=matter).exists()
    assert (
        ChangeEvent.objects.filter(
            matter=matter, event_type=ChangeEventType.MATTER_CREATED, actor=specialist
        ).count()
        == 1
    )
    # One's own Teema is no «Uus asi».
    assert not MatterAssignmentNotice.objects.filter(matter=matter).exists()
    assert Matter.objects.filter(title="Uus eelnõu").count() == 1
    assert executed.url.startswith("https://juristid.example.test/")


def test_create_for_a_colleague_needs_work_assign(specialist, other_specialist):
    prepared = prepare(
        "create_matter",
        specialist,
        title="Kolleegi teema",
        responsible_person=other_specialist.display_name,
    )
    assert prepared.canonical["owner_id"] == str(other_specialist.pk)
    executed = commit("create_matter", specialist, prepared)
    matter = Matter.objects.get(pk=executed.record_id)
    assert matter.owner == other_specialist
    assert (
        MatterAssignmentNotice.objects.filter(matter=matter, recipient=other_specialist).count()
        == 1
    )

    limited = _no_assign()
    refused = refusal(
        "create_matter", limited, title="Teema", responsible_person=other_specialist.display_name
    )
    assert refused.code == "forbidden"
    # Naming oneself is capture, not assignment.
    own = prepare("create_matter", limited, title="Teema", responsible_person=limited.display_name)
    assert own.canonical["owner_id"] == str(limited.pk)


def test_create_resolves_people_by_name_only_among_assignable_colleagues(specialist, administrator):
    assert refusal(
        "create_matter", specialist, title="T", responsible_person="Keegi Puudub"
    ).code == ("invalid_argument")
    # An administrator is no department worker; work cannot be given to them.
    assert (
        refusal(
            "create_matter", specialist, title="T", responsible_person=administrator.display_name
        ).code
        == "invalid_argument"
    )
    factories.UserFactory(display_name="Mari Kask")
    factories.UserFactory(display_name="Mari Tamm")
    ambiguous = refusal("create_matter", specialist, title="T", responsible_person="Mari")
    assert ambiguous.code == "missing_information"
    assert "Mari Kask" in ambiguous.message and "Mari Tamm" in ambiguous.message


@pytest.mark.parametrize("who", ["reader", "administrator"])
def test_create_refuses_non_writers(request, who):
    user = request.getfixturevalue(who)
    assert refusal("create_matter", user, title="Teema").code == "forbidden"


def test_create_warns_about_a_same_title_open_matter(specialist):
    existing = factories.MatterFactory(owner=specialist, title="Tulumaksuseaduse muutmine")
    prepared = prepare("create_matter", specialist, title="tulumaksuseaduse  muutmine")
    assert existing.display_reference in " ".join(prepared.preview.warnings)


def test_create_state_goes_stale_when_the_owner_stops_being_assignable(
    specialist, other_specialist
):
    prepared = prepare(
        "create_matter", specialist, title="Teema", responsible_person=other_specialist.display_name
    )
    other_specialist.is_active = False
    other_specialist.save(update_fields=["is_active"])
    assert is_stale("create_matter", specialist, prepared)
    assert execute_refusal("create_matter", specialist, prepared).code == "domain_rule"
    assert not Matter.objects.filter(title="Teema").exists()


def test_create_arguments_are_bounded():
    model = REGISTRY["create_matter"].arguments
    with pytest.raises(ValidationError):
        model.model_validate({"title": "x" * 1001})
    with pytest.raises(ValidationError):
        model.model_validate({"title": ""})
    with pytest.raises(ValidationError):
        model.model_validate({"title": "T", "response_deadline": "2026-12-01"})
    with pytest.raises(ValidationError):
        model.model_validate({"title": "T", "brief_summary": "x" * 8001})


# ---------------------------------------------------------------------------
# update_matter
# ---------------------------------------------------------------------------


def test_update_previews_current_and_proposed_and_writes_nothing(specialist, normal_matter):
    events = ChangeEvent.objects.count()
    prepared = prepare(
        "update_matter",
        specialist,
        matter=normal_matter.display_reference,
        title="Uus pealkiri",
        brief_summary="Uus kokkuvõte.",
    )
    assert ChangeEvent.objects.count() == events
    title = change(prepared, "title")
    assert (title.current, title.proposed) == (normal_matter.title, "Uus pealkiri")
    assert change(prepared, "brief_summary").proposed == "Uus kokkuvõte."
    assert prepared.state["title"] == normal_matter.title
    assert prepared.target_matter_id == normal_matter.pk


def test_update_executes_the_title_and_summary_services(specialist, normal_matter):
    old = normal_matter.title
    prepared = prepare(
        "update_matter",
        specialist,
        matter=str(normal_matter.pk),
        title="Uus",
        brief_summary="Kokku.",
    )
    commit("update_matter", specialist, prepared)
    normal_matter.refresh_from_db()
    assert (normal_matter.title, normal_matter.brief_summary) == ("Uus", "Kokku.")
    renamed = ChangeEvent.objects.get(
        matter=normal_matter, event_type=ChangeEventType.MATTER_TITLE_CHANGED
    )
    assert renamed.payload == {"previous": old, "current": "Uus"}
    assert (
        ChangeEvent.objects.filter(
            matter=normal_matter, event_type=ChangeEventType.MATTER_BRIEF_SUMMARY_SET
        ).count()
        == 1
    )


def test_update_can_clear_the_summary(specialist):
    matter = factories.MatterFactory(owner=specialist, brief_summary="Vana")
    prepared = prepare("update_matter", specialist, matter=str(matter.pk), clear_brief_summary=True)
    commit("update_matter", specialist, prepared)
    matter.refresh_from_db()
    assert matter.brief_summary == ""


def test_update_refuses_no_change_and_contradictions(specialist, normal_matter):
    assert (
        refusal(
            "update_matter", specialist, matter=str(normal_matter.pk), title=normal_matter.title
        ).code
        == "invalid_argument"
    )
    model = REGISTRY["update_matter"].arguments
    with pytest.raises(ValidationError):
        model.model_validate({"matter": "2026_1"})
    with pytest.raises(ValidationError):
        model.model_validate(
            {"matter": "2026_1", "brief_summary": "x", "clear_brief_summary": True}
        )


def test_update_goes_stale_on_a_concurrent_rename(specialist, normal_matter):
    prepared = prepare("update_matter", specialist, matter=str(normal_matter.pk), title="Minu")
    set_matter_title(matter=normal_matter, value="Kolleegi", actor=specialist)
    assert is_stale("update_matter", specialist, prepared)


def test_update_refuses_reader_admin_invisible_and_closed(
    specialist, reader, administrator, normal_matter
):
    assert (
        refusal("update_matter", reader, matter=str(normal_matter.pk), title="X").code
        == "forbidden"
    )
    assert (
        refusal("update_matter", administrator, matter=str(normal_matter.pk), title="X").code
        == "forbidden"
    )
    assert refusal("update_matter", specialist, matter="2026_999999", title="X").code == "not_found"
    import uuid

    assert (
        refusal("update_matter", specialist, matter=str(uuid.uuid4()), title="X").code
        == "not_found"
    )
    close_matter(matter=normal_matter, disposition="OTHER", actor=specialist)
    assert (
        refusal("update_matter", specialist, matter=str(normal_matter.pk), title="X").code
        == "domain_rule"
    )


def test_execute_rechecks_the_writer(specialist, normal_matter):
    prepared = prepare("update_matter", specialist, matter=str(normal_matter.pk), title="Uus")
    from app.accounts.enums import UserRole

    specialist.role = UserRole.READER
    specialist.save(update_fields=["role"])
    assert execute_refusal("update_matter", specialist, prepared).code == "forbidden"
    normal_matter.refresh_from_db()
    assert normal_matter.title != "Uus"


def test_execute_refuses_a_matter_closed_since_prepare(specialist, normal_matter):
    prepared = prepare("update_matter", specialist, matter=str(normal_matter.pk), title="Uus")
    close_matter(matter=normal_matter, disposition="OTHER", actor=specialist)
    assert is_stale("update_matter", specialist, prepared)
    assert execute_refusal("update_matter", specialist, prepared).code == "domain_rule"


def test_an_archive_record_is_not_written(specialist):
    archived = factories.ArchiveMatterFactory(owner=specialist)
    assert refusal("update_matter", specialist, matter=str(archived.pk), title="X").code in (
        "domain_rule",
        "not_found",
    )


# ---------------------------------------------------------------------------
# assign_matter
# ---------------------------------------------------------------------------


def test_assign_previews_current_owner_and_moves_the_following_step(specialist, other_specialist):
    matter = factories.MatterFactory(owner=specialist)
    step = factories.NextActionFactory(matter=matter, responsible=specialist)
    prepared = prepare(
        "assign_matter",
        specialist,
        matter=matter.display_reference,
        action="assign",
        responsible_person=other_specialist.display_name,
    )
    owner = change(prepared, "owner")
    assert (owner.current, owner.proposed) == (
        specialist.display_name,
        other_specialist.display_name,
    )
    assert prepared.state["owner_id"] == str(specialist.pk)
    commit("assign_matter", specialist, prepared)
    matter.refresh_from_db()
    step.refresh_from_db()
    assert matter.owner == other_specialist
    assert step.responsible == other_specialist
    assert (
        ChangeEvent.objects.filter(
            matter=matter, event_type=ChangeEventType.MATTER_ASSIGNED
        ).count()
        == 1
    )
    assert (
        MatterAssignmentNotice.objects.filter(matter=matter, recipient=other_specialist).count()
        == 1
    )


def test_assign_can_clear_as_the_header_can(specialist):
    matter = factories.MatterFactory(owner=specialist)
    prepared = prepare("assign_matter", specialist, matter=str(matter.pk), action="clear")
    assert change(prepared, "owner").proposed is None
    commit("assign_matter", specialist, prepared)
    matter.refresh_from_db()
    assert matter.owner is None


def test_assign_needs_work_assign(other_specialist):
    limited = _no_assign()
    matter = factories.MatterFactory(owner=limited)
    assert (
        refusal(
            "assign_matter",
            limited,
            matter=str(matter.pk),
            action="assign",
            responsible_person=other_specialist.display_name,
        ).code
        == "forbidden"
    )
    assert (
        refusal("assign_matter", limited, matter=str(matter.pk), action="clear").code == "forbidden"
    )


def test_assign_refuses_the_same_owner_and_a_closed_matter(specialist, normal_matter):
    assert (
        refusal(
            "assign_matter",
            specialist,
            matter=str(normal_matter.pk),
            action="assign",
            responsible_person=specialist.display_name,
        ).code
        == "invalid_argument"
    )
    close_matter(matter=normal_matter, disposition="OTHER", actor=specialist)
    assert refusal(
        "assign_matter", specialist, matter=str(normal_matter.pk), action="clear"
    ).code == ("domain_rule")


def test_assign_goes_stale_when_somebody_else_reassigns_first(
    specialist, other_specialist, department_head
):
    matter = factories.MatterFactory(owner=specialist)
    prepared = prepare(
        "assign_matter",
        specialist,
        matter=str(matter.pk),
        action="assign",
        responsible_person=other_specialist.display_name,
    )
    assign_matter(matter=matter, owner=department_head, actor=department_head)
    assert is_stale("assign_matter", specialist, prepared)


def test_assign_arguments_are_explicit():
    model = REGISTRY["assign_matter"].arguments
    with pytest.raises(ValidationError):
        model.model_validate({"matter": "2026_1", "action": "assign"})
    with pytest.raises(ValidationError):
        model.model_validate({"matter": "2026_1", "action": "clear", "responsible_person": "X"})
    with pytest.raises(ValidationError):
        model.model_validate(
            {"matter": "2026_1", "action": "assign", "responsible_person": "x" * 201}
        )


def test_a_deleted_matter_is_not_found_at_prepare_and_at_execute(specialist):
    from django.utils import timezone

    matter = factories.MatterFactory(owner=specialist)
    prepared = prepare("update_matter", specialist, matter=str(matter.pk), title="Uus")
    Matter.all_objects.filter(pk=matter.pk).update(deleted_at=timezone.now())
    assert (
        refusal("update_matter", specialist, matter=str(matter.pk), title="X").code == "not_found"
    )
    from app.mcp_writes.operations.base import OperationRefused

    with pytest.raises(OperationRefused) as locked:
        REGISTRY["update_matter"].lock(canonical=prepared.canonical)
    assert locked.value.code == "not_found"
    assert execute_refusal("update_matter", specialist, prepared).code == "not_found"
