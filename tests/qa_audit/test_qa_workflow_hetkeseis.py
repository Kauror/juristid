"""Independent QA 2026-10-14 — workflow area: Hetkeseis from the header (ADR 0131, 0156, 0128).

Header changes through `update_field` (the one canonical writer), checked against
`MatterStageEpisode` rows and `ChangeEvent`s — including an imported Teema whose
stage predates periods, where nothing may be displayed that the DB cannot substantiate.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.enums import StageEpisodeOrigin
from app.matters.models import Matter, MatterStageEpisode
from app.matters.services import create_matter, matter_field_revision
from app.matters.stage_episodes import period_label
from app.workflow.models import StageVocabulary
from tests import factories

pytestmark = pytest.mark.django_db


def stage(key: str) -> StageVocabulary:
    return StageVocabulary.objects.get(key=key)


def choose(client, matter, key, **extra):
    matter.refresh_from_db()
    return client.post(
        reverse("matters:update_field", kwargs={"pk": matter.pk, "field": "stage"}),
        {"stage": str(stage(key).pk), "revision": matter_field_revision(matter, "stage"), **extra},
        HTTP_HX_REQUEST="true",
    )


def episodes(matter):
    return list(MatterStageEpisode.objects.filter(matter=matter).order_by("sequence"))


def test_a_b_a_makes_three_periods_with_one_current(signed_in, specialist):
    matter = create_matter(
        title="QA edasi-tagasi", actor=specialist, owner=specialist, stage=stage("consultation")
    )

    for key in ("government", "consultation"):
        assert choose(signed_in, matter, key).status_code == 200

    rows = episodes(matter)
    assert [row.stage.key for row in rows] == ["consultation", "government", "consultation"]
    assert [row.is_current for row in rows] == [False, False, True]
    assert all(row.ended_at is not None for row in rows[:2])
    assert rows[0].ended_at <= rows[1].started_at <= rows[1].ended_at <= rows[2].started_at
    events = ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.MATTER_STAGE_CHANGED
    ).order_by("occurred_at" if hasattr(ChangeEvent, "occurred_at") else "created_at")
    assert events.count() == 2
    assert [e.stage_episode_id for e in events] == [rows[1].pk, rows[2].pk]


def test_the_same_stage_again_writes_nothing(signed_in, specialist):
    matter = create_matter(
        title="QA sama hetkeseis", actor=specialist, owner=specialist, stage=stage("government")
    )
    before = ChangeEvent.objects.filter(matter=matter).count()

    assert choose(signed_in, matter, "government").status_code == 200

    assert ChangeEvent.objects.filter(matter=matter).count() == before
    assert len(episodes(matter)) == 1


def test_an_imported_stage_without_periods_is_not_given_an_invented_start(signed_in, specialist):
    """A register import: `Matter.stage` set, no `MatterStageEpisode` at all."""
    matter = factories.MatterFactory(owner=specialist, stage=stage("parliament"))
    assert episodes(matter) == []

    page = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert page.status_code == 200
    assert "alates" not in page.content.decode().split('id="teema-hetkeseis"', 1)[1][:2000]

    assert choose(signed_in, matter, "awaiting_entry").status_code == 200

    rows = episodes(matter)
    # The old stage got no period with an invented start; the new one is RECORDED now.
    assert [row.stage.key for row in rows] == ["awaiting_entry"]
    assert rows[0].origin == StageEpisodeOrigin.RECORDED and rows[0].is_current
    event = ChangeEvent.objects.get(matter=matter, event_type=ChangeEventType.MATTER_STAGE_CHANGED)
    assert event.payload["from_key"] == "parliament"
    assert event.stage_episode_id == rows[0].pk


def test_a_carried_over_period_prints_no_start(specialist):
    from app.matters.services import ensure_current_stage_episode

    matter = factories.MatterFactory(owner=specialist, stage=stage("government"))
    episode = ensure_current_stage_episode(matter=matter)

    assert episode.origin == StageEpisodeOrigin.CARRIED_OVER
    assert episode.started_at is None
    assert period_label(episode) == ""


def test_a_reader_cannot_move_the_stage(client, reader, specialist):
    matter = create_matter(
        title="QA lugeja hetkeseis", actor=specialist, owner=specialist, stage=stage("idea")
    )
    client.force_login(reader)

    response = choose(client, matter, "government")

    assert response.status_code in (403, 404)
    matter.refresh_from_db()
    assert matter.stage.key == "idea"
    assert len(episodes(matter)) == 1


def test_a_closed_matter_refuses_a_header_stage_change(signed_in, specialist):
    matter = create_matter(
        title="QA suletud hetkeseis", actor=specialist, owner=specialist, stage=stage("idea")
    )
    assert choose(signed_in, matter, "in_force").status_code == 200
    matter.refresh_from_db()
    assert not matter.is_open
    count = len(episodes(matter))

    response = choose(signed_in, matter, "government")

    assert response.status_code >= 400
    matter.refresh_from_db()
    assert matter.stage.key == "in_force" and not matter.is_open
    assert len(episodes(matter)) == count


def test_vastutaja_change_moves_the_open_step_and_planned_ones(signed_in, specialist):
    import datetime as dt

    from django.utils import timezone

    from app.workflow.services import add_planned_action, set_next_action

    head = factories.DepartmentHeadFactory()
    new_owner = factories.UserFactory()
    matter = create_matter(
        title="QA vastutaja vahetus", actor=specialist, owner=specialist, stage=stage("idea")
    )
    step = set_next_action(matter=matter, text="Praegune", actor=specialist)
    planned = add_planned_action(
        matter=matter,
        text="Hiljem",
        target_date=timezone.localdate() + dt.timedelta(days=3),
        actor=specialist,
    )
    signed_in.force_login(head)

    response = signed_in.post(
        reverse("matters:update_field", kwargs={"pk": matter.pk, "field": "owner"}),
        {"owner": str(new_owner.pk)},
    )

    assert response.status_code == 200
    matter.refresh_from_db()
    step.refresh_from_db()
    planned.refresh_from_db()
    assert matter.owner == new_owner
    assert step.responsible == new_owner and planned.responsible == new_owner
    event = ChangeEvent.objects.get(matter=matter, event_type=ChangeEventType.MATTER_ASSIGNED)
    assert event.payload["next_action_moved"] == str(step.pk)
    assert isinstance(matter, Matter)
