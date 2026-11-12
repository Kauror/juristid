"""Lovable owns its fields on a synchronised Teema (audit 2026-11-11 TL-01 = LS-04).

Since 11.11.2026 the nightly synchronisation puts Lovable's value back over a
local edit of a source-owned field (`lovable_sync.FIELD_POLICY`, reason
`RESTORED`). Before this, the Teema page offered those edits unchanged and the
work was undone at night without a word. Now, on a synchronised Teema:

* the page draws each source-owned value without its editor, with an `i`;
* every guarded service refuses a *person's* change with a sentence that says
  where the value is changed, and an MCP write is refused when proposed;
* the synchronisation (no actor) still writes, an unchanged value still saves,
  and Juristid-only work — notes, own dates, an answered request — is untouched;
* a native Teema, and one of a retired cohort, is exactly as before.

`app.matters.source_ownership` is the rule; `tests/test_r4_authority.py` keeps
pinning what the night does to a divergence that exists anyway.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from app.audit.models import ChangeEvent
from app.intelligence.models import MatterEffectiveDate
from app.legacy_import import lovable_sync
from app.legacy_import.lovable_pilot import REGISTER_COHORT
from app.legacy_import.lovable_sync_models import (
    LovableSyncCohort,
    LovableSyncControl,
    LovableSyncDecision,
    LovableSyncRun,
    LovableSyncState,
    SyncMode,
    SyncOutcome,
)
from app.matters import source_ownership
from app.matters.source_ownership import (
    GUARD_KEYS,
    POLICY_SURFACES,
    SOURCE_OWNED_NOTICE,
    SOURCE_OWNED_TIP,
    is_source_owned,
    refusal_message,
)
from app.workflow.enums import ActionKind, ActionStatus, DateSemantics, Disposition, Track
from app.workflow.models import NextAction, StageVocabulary
from tests import factories
from tests.refusals import refused

DEADLINE = dt.date(2099, 6, 1)


def _synchronise(matter: Any, cohort: str = REGISTER_COHORT) -> LovableSyncState:
    return LovableSyncState.objects.create(
        matter=matter, source_id=uuid.uuid4(), reference="2099_1", cohort=cohort
    )


def _teema(owner: Any, *, synced: bool) -> Any:
    matter = factories.MatterFactory(
        owner=owner,
        stage=StageVocabulary.objects.get(key="government"),
        received_date=dt.date(2099, 1, 5),
        response_deadline=DEADLINE,
        response_requested_at=timezone.now(),
    )
    factories.NextActionFactory(
        matter=matter, responsible=owner, text="Koostan arvamuse", target_date=DEADLINE
    )
    if synced:
        _synchronise(matter)
    return matter


def _mode(mode: str) -> None:
    LovableSyncControl.objects.update_or_create(pk=1, defaults={"mode": mode})


@pytest.fixture(autouse=True)
def writing_sync(db):
    """The lock is on while the synchronisation follows Lovable (E9): APPLY here;
    the E9 tests switch to COMPARE, OFF and cutover themselves."""
    _mode(SyncMode.APPLY)


@pytest.fixture
def synced(db, specialist):
    return _teema(specialist, synced=True)


@pytest.fixture
def native(db, specialist):
    return _teema(specialist, synced=False)


def _stage(key: str) -> StageVocabulary:
    return StageVocabulary.objects.get(key=key)


# ---------------------------------------------------------------------------
# The rule and its table
# ---------------------------------------------------------------------------


def test_every_field_lovable_owns_has_a_guarded_surface_or_no_editor():
    """Regression: TL-01. A field the sync starts to own cannot stay editable by omission."""
    owned = {
        name
        for name, policy in lovable_sync.FIELD_POLICY.items()
        if policy.policy == lovable_sync.AUTHORITATIVE
    }
    assert owned == set(POLICY_SURFACES)
    assert {key for key in POLICY_SURFACES.values() if key} <= set(GUARD_KEYS)
    for key in GUARD_KEYS:
        assert refusal_message(key).endswith(SOURCE_OWNED_NOTICE)


def test_the_tip_template_says_the_rules_sentence():
    """Regression: TL-01. The `i` is written out in the template; it must say the rule's words."""
    from django.template.loader import render_to_string

    html = render_to_string("components/source_owned_tip.html", {"field": "title"})
    assert SOURCE_OWNED_TIP in html
    assert 'data-source-owned="title"' in html


def test_only_a_live_teema_of_a_live_cohort_is_source_owned(db, specialist):
    """Regression: TL-01. Native, deleted and retired-cohort Teemad are never restricted."""
    native = factories.MatterFactory(owner=specialist)
    assert not is_source_owned(native)
    synced = factories.MatterFactory(owner=specialist)
    _synchronise(synced)
    assert is_source_owned(synced)
    retired = factories.MatterFactory(owner=specialist)
    LovableSyncCohort.objects.create(
        key="lovable-retired-test",
        pilot="lovable-retired-test",
        package_sha256="c" * 64,
        expected=1,
        enrolled_at=timezone.now(),
        reason="test",
        retired_at=timezone.now(),
        retired_reason="superseded",
    )
    _synchronise(retired, cohort="lovable-retired-test")
    assert not is_source_owned(retired)


# ---------------------------------------------------------------------------
# The Teema page: a marker and no editor on a synchronised Teema
# ---------------------------------------------------------------------------

#: The Teema page's editors for each source-owned field, as the URLs they post to.
_DETAIL_EDITORS = {
    "owner": ("matters:update_field", {"field": "owner"}),
    "stage": ("matters:update_field", {"field": "stage"}),
    "received": ("matters:update_field", {"field": "received_date"}),
    "next_action": ("matters:complete_current_action", {}),
    "close": ("matters:close_from_workspace", {}),
}


def _page(client: Any, name: str, matter: Any) -> str:
    response = client.get(reverse(name, kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    return response.content.decode()


def _url(name: str, matter: Any, **kwargs: Any) -> str:
    return reverse(name, kwargs={"pk": matter.pk, **kwargs})


def test_the_teema_page_marks_lovables_fields_and_offers_no_editor(signed_in, synced, native):
    """Regression: TL-01. Every source-owned field on the Teema page: marker, no editor."""
    page = _page(signed_in, "matters:matter_detail", synced)
    assert escape(SOURCE_OWNED_NOTICE) in page
    for key in ("title", "opinion_deadline", *_DETAIL_EDITORS):
        assert f'data-source-owned="{key}"' in page, key
    for key, (name, kwargs) in _DETAIL_EDITORS.items():
        assert _url(name, synced, **kwargs) not in page, key
    assert 'name="response_deadline"' not in page
    # «Muuda» and the stage moves of `✓ Tehtud` are behind the same toolbar.
    assert _url("matters:set_action", synced) not in page

    page = _page(signed_in, "matters:matter_detail", native)
    assert escape(SOURCE_OWNED_NOTICE) not in page
    # The header's date editor; on a locked Teema only «Lõpeta tähtaeg» as answered (E5).
    assert 'name="response_deadline"' in page
    assert "data-source-owned=" not in page
    for key, (name, kwargs) in _DETAIL_EDITORS.items():
        assert _url(name, native, **kwargs) in page, key


def test_a_synchronised_teema_with_no_step_says_lovable_sets_it(signed_in, synced, native):
    """Regression: TL-01. A blank Lovable `järgmiseks` is not Juristid's to fill: no composer."""
    for matter in (synced, native):
        NextAction.objects.filter(matter=matter).update(status=ActionStatus.CANCELLED)
    page = _page(signed_in, "matters:matter_detail", synced)
    assert escape(source_ownership.NO_SOURCE_STEP_NOTE) in page
    assert 'data-source-owned="next_action"' in page
    assert _url("matters:set_action", synced) not in page
    assert _url("matters:set_action", native) in _page(signed_in, "matters:matter_detail", native)


def test_a_closed_synchronised_teema_is_not_offered_ava_uuesti(signed_in, specialist):
    """Regression: TL-01. «Ava uuesti» is Lovable's on a synchronised Teema."""
    from app.matters.services import close_matter

    synced, native = (_teema(specialist, synced=flag) for flag in (True, False))
    for matter in (synced, native):
        close_matter(matter=matter, disposition=Disposition.OTHER, follow_ups_confirmed=True)
    page = _page(signed_in, "matters:matter_detail", synced)
    assert 'data-source-owned="reopen"' in page
    assert _url("matters:reopen", synced) not in page
    assert _url("matters:reopen", native) in _page(signed_in, "matters:matter_detail", native)


def test_muuda_teemat_shows_lovables_fields_disabled(signed_in, synced, native):
    """Regression: TL-01. `Muuda teemat`: each source-owned field marked and disabled."""
    import re

    page = _page(signed_in, "matters:matter_edit", synced)
    for key in ("title", "owner", "stage", "received", "opinion_deadline", "legal_act"):
        assert f'data-source-owned="{key}"' in page, key
    for name in ("title", "received_date", "response_deadline"):
        tag = re.search(rf'<input[^>]*name="{name}"[^>]*>', page)
        assert tag and "disabled" in tag.group(0), name
    owner_radio = re.search(r'<input[^>]*name="owner"[^>]*>', page)
    assert owner_radio and "disabled" in owner_radio.group(0)

    page = _page(signed_in, "matters:matter_edit", native)
    assert "data-source-owned=" not in page
    tag = re.search(r'<input[^>]*name="title"[^>]*>', page)
    assert tag and "disabled" not in tag.group(0)


def test_muuda_teemat_still_saves_juristids_own_fields(signed_in, synced, other_specialist):
    """Regression: TL-01. A crafted value for a disabled field is ignored; the summary saves."""
    from app.matters.forms import edit_initial

    data = {k: v for k, v in edit_initial(synced).items() if v is not None}
    data.update(
        {
            "title": "Juristi oma pealkiri",
            "owner": str(other_specialist.pk),
            "brief_summary": "Juristide kokkuvõte",
            "received_date": "",
            "response_deadline": "",
        }
    )
    data["policy_areas"] = []
    data["legal_instruments"] = [str(v) for v in data.get("legal_instruments") or []]
    response = signed_in.post(_url("matters:matter_edit", synced), data)
    assert response.status_code == 302, response.content.decode()[:2000]
    synced.refresh_from_db()
    assert synced.brief_summary == "Juristide kokkuvõte"
    assert synced.title.startswith("Näidisteema")
    assert synced.owner_id != other_specialist.pk
    assert (synced.received_date, synced.response_deadline) == (dt.date(2099, 1, 5), DEADLINE)


def test_the_header_editor_refuses_with_the_reason(signed_in, synced, other_specialist):
    """Regression: TL-01. A crafted inline POST is refused with the source sentence, not a 404."""
    from app.matters.services import matter_field_revision

    before = ChangeEvent.objects.filter(matter=synced).count()
    response = signed_in.post(
        _url("matters:update_field", synced, field="owner"),
        {"owner": str(other_specialist.pk), "revision": matter_field_revision(synced, "owner")},
    )
    assert response.status_code == 400
    assert escape(refusal_message("owner")) in response.content.decode()
    synced.refresh_from_db()
    assert synced.owner_id != other_specialist.pk
    assert ChangeEvent.objects.filter(matter=synced).count() == before


def test_a_reader_is_refused_exactly_as_before(client, reader, synced, native, other_specialist):
    """Regression: TL-01. Authorization is unchanged: a reader is refused on both alike."""
    client.force_login(reader)
    statuses = {
        client.post(
            _url("matters:update_field", matter, field="owner"),
            {"owner": str(other_specialist.pk)},
        ).status_code
        for matter in (synced, native)
    }
    assert len(statuses) == 1 and statuses != {200}


# ---------------------------------------------------------------------------
# The services: a person's change is refused, field by field
# ---------------------------------------------------------------------------


def test_title_owner_received_track_and_legal_act_are_refused(synced, specialist, other_specialist):
    """Regression: TL-01. The scalar register fields, each through its own service."""
    from app.matters.services import (
        assign_matter,
        change_track,
        set_legal_instruments,
        set_matter_dates,
        set_matter_title,
    )

    with refused(refusal_message("title")):
        set_matter_title(matter=synced, value="Uus", actor=specialist)
    with refused(refusal_message("owner")):
        assign_matter(matter=synced, owner=other_specialist, actor=specialist)
    with refused(refusal_message("received")):
        set_matter_dates(matter=synced, received_date=dt.date(2099, 1, 6), actor=specialist)
    with refused(refusal_message("track")):
        change_track(matter=synced, track=Track.KODA_INITIATIVE, actor=specialist)
    act = synced._meta.get_field("legal_instruments").related_model._default_manager.first()
    with refused(refusal_message("legal_act")):
        set_legal_instruments(matter=synced, legal_instruments=[act], actor=specialist)


def test_stage_close_and_reopen_are_refused(synced, specialist):
    """Regression: TL-01. Hetkeseis, «Lõpeta teema» and «Ava uuesti» are Lovable's lifecycle."""
    from app.matters.services import change_stage, close_matter, reopen_matter_into_stage
    from app.matters.workspace import close_matter_from_workspace

    with refused(refusal_message("stage")):
        change_stage(matter=synced, stage=_stage("parliament"), actor=specialist)
    with refused(refusal_message("close")):
        close_matter_from_workspace(
            matter=synced, author=specialist, disposition=Disposition.MONITORING_STOPPED
        )
    successor = factories.MatterFactory(owner=specialist)
    with refused(refusal_message("close")):
        close_matter(
            matter=synced,
            disposition=Disposition.SUPERSEDED,
            successor=successor,
            actor=specialist,
            follow_ups_confirmed=True,
        )
    # Closed by the night (no actor), it is not reopened by a person.
    close_matter(matter=synced, disposition=Disposition.OTHER, follow_ups_confirmed=True)
    with refused(refusal_message("reopen")):
        reopen_matter_into_stage(matter=synced, stage=_stage("parliament"), actor=specialist)
    synced.refresh_from_db()
    assert not synced.is_open


def test_the_current_step_is_refused_on_every_door(synced, specialist):
    """Regression: TL-01. Composer, «Muuda», «Nihuta», «✓ Tehtud», «Vaatasin üle»."""
    from app.matters.date_shift import SHIFT_KEYS, shift_action_date
    from app.matters.workspace import add_unnamed_step, complete_current_action
    from app.workflow.services import acknowledge_review, set_next_action_for_new_work

    current = NextAction.objects.get(matter=synced, status=ActionStatus.OPEN)
    with refused(refusal_message("next_action")):
        set_next_action_for_new_work(matter=synced, text="Helistan", actor=specialist)
    with refused(refusal_message("next_action")):
        complete_current_action(
            matter=synced, author=specialist, action_id=current.pk, body="Tehtud."
        )
    with refused(refusal_message("next_action")):
        shift_action_date(
            matter=synced,
            action_id=current.pk,
            actor=specialist,
            shift=SHIFT_KEYS[0],
            source_date=DEADLINE,
        )
    NextAction.objects.filter(pk=current.pk).update(
        kind=ActionKind.WAIT, date_semantics=DateSemantics.REVIEW_ON
    )
    current.refresh_from_db()
    with refused(refusal_message("next_action")):
        acknowledge_review(action=current, actor=specialist, next_review_date=dt.date(2099, 7, 1))
    # A blank Lovable step is still Lovable's: no step is made current here.
    NextAction.objects.filter(pk=current.pk).update(status=ActionStatus.CANCELLED)
    with refused(refusal_message("next_action")):
        add_unnamed_step(matter=synced, actor=specialist, text="Helistan")


def test_the_opinion_deadline_is_refused_but_an_answer_is_not(synced, specialist):
    """Regression: TL-01. Setting, moving or withdrawing is Lovable's; answering is Juristid's."""
    from app.matters.enums import ResponseDeadlineChange, ResponseDeadlineOutcome
    from app.matters.response_deadlines import (
        change_response_deadline,
        request_response_deadline,
        resolve_response_deadline,
    )

    with refused(refusal_message("opinion_deadline")):
        change_response_deadline(
            matter=synced,
            deadline=dt.date(2099, 7, 1),
            change=ResponseDeadlineChange.MOVED,
            actor=specialist,
        )
    with refused(refusal_message("opinion_deadline")):
        change_response_deadline(matter=synced, deadline=None, actor=specialist)
    with refused(refusal_message("opinion_deadline")):
        resolve_response_deadline(
            matter=synced, outcome=ResponseDeadlineOutcome.CANCELLED, actor=specialist
        )
    # Answered in Juristid: the night holds it (ANSWERED_IN_JURISTID), never undoes it.
    resolve_response_deadline(
        matter=synced, outcome=ResponseDeadlineOutcome.NOT_ANSWERING, actor=specialist
    )
    synced.refresh_from_db()
    assert synced.response_deadline is None
    with refused(refusal_message("opinion_deadline")):
        request_response_deadline(matter=synced, deadline=dt.date(2099, 8, 1), actor=specialist)


def test_the_chronology_marks_lovables_commencement(signed_in, synced):
    """Regression: TL-01. A Lovable commencement: an `i`, no `Kustuta`; a person's own keeps it."""
    lovable = factories.EffectiveDateFactory(
        matter=synced, source_text=lovable_sync.LOVABLE_COMMENCEMENT_MARK
    )
    own = factories.EffectiveDateFactory(matter=synced, description="oma")
    page = _page(signed_in, "matters:matter_detail", synced)
    assert 'data-source-owned="commencement"' in page
    remove = "matters:remove_record"
    assert (
        reverse(remove, kwargs={"pk": synced.pk, "kind": "joustumine", "record_id": lovable.pk})
        not in page
    )
    assert (
        reverse(remove, kwargs={"pk": synced.pk, "kind": "joustumine", "record_id": own.pk}) in page
    )


def test_lovables_commencement_is_refused_and_a_persons_own_is_not(synced, specialist):
    """Regression: TL-01. Only the commencements that came from Lovable are its."""
    from app.intelligence.services import (
        add_important_date,
        cancel_effective_date,
        cancel_important_date,
        update_effective_date,
    )
    from app.matters.removal import remove_matter_record

    lovable = factories.EffectiveDateFactory(
        matter=synced, source_text=lovable_sync.LOVABLE_COMMENCEMENT_MARK
    )
    with refused(refusal_message("commencement")):
        update_effective_date(
            record=lovable,
            kind=lovable.kind,
            date_value=dt.date(2099, 9, 1),
            period_end=dt.date(2099, 9, 1),
            date_precision="EXACT",
            description=lovable.description,
            actor=specialist,
        )
    with refused(refusal_message("commencement")):
        cancel_effective_date(record=lovable, actor=specialist)
    with refused(refusal_message("commencement")):
        remove_matter_record(
            matter_id=synced.pk, kind_key="joustumine", record_id=lovable.pk, actor=specialist
        )
    assert MatterEffectiveDate.objects.get(pk=lovable.pk).status == "ACTIVE"
    own = add_important_date(
        matter=synced,
        title="Eeldatav jõustumine",
        date_value=dt.date(2099, 10, 1),
        period_end=dt.date(2099, 10, 1),
        date_precision="EXACT",
        kind="ESTIMATED_COMMENCEMENT",
        actor=specialist,
    )
    cancel_important_date(record=own, actor=specialist)


def test_the_sync_and_an_unchanged_value_still_write(synced, specialist):
    """Regression: TL-01. No actor (the night) writes; the same value again is no change."""
    from app.matters.services import assign_matter, change_stage, set_matter_title

    set_matter_title(matter=synced, value="Lovable'i pealkiri", actor=None)
    change_stage(matter=synced, stage=_stage("parliament"), actor=None)
    synced.refresh_from_db()
    assert (synced.title, synced.stage.key) == ("Lovable'i pealkiri", "parliament")
    set_matter_title(matter=synced, value="Lovable'i pealkiri", actor=specialist)
    assign_matter(matter=synced, owner=synced.owner, actor=specialist)
    change_stage(matter=synced, stage=_stage("parliament"), actor=specialist)


def test_juristid_only_work_stays_editable(synced, specialist):
    """Regression: TL-01. Notes, the summary and planned work are not Lovable's."""
    from app.matters.services import add_entry, set_brief_summary
    from app.matters.workspace import add_unnamed_step

    add_entry(matter=synced, body="<p>Märkus</p>", author=specialist)
    set_brief_summary(matter=synced, value="Kokkuvõte", actor=specialist)
    planned = add_unnamed_step(
        matter=synced, actor=specialist, text="Valmistan ette", target_date=dt.date(2099, 5, 1)
    )
    assert planned.status == ActionStatus.PLANNED


def test_a_native_teema_is_unrestricted(native, specialist, other_specialist):
    """Regression: TL-01. The same calls on a native Teema save as before."""
    from app.matters.services import assign_matter, change_stage, set_matter_title
    from app.workflow.services import set_next_action_for_new_work

    set_matter_title(matter=native, value="Uus", actor=specialist)
    assign_matter(matter=native, owner=other_specialist, actor=specialist)
    change_stage(matter=native, stage=_stage("parliament"), actor=specialist)
    set_next_action_for_new_work(matter=native, text="Helistan", actor=specialist)


# ---------------------------------------------------------------------------
# MCP writes: refused when proposed, in the same words
# ---------------------------------------------------------------------------


def test_mcp_writes_to_lovables_fields_are_refused_when_proposed(
    synced, specialist, other_specialist
):
    """Regression: TL-01. The MCP path obeys the same guard, before anybody approves."""
    from tests.test_mcp_write_operations_support import refusal

    matter = str(synced.pk)
    current = NextAction.objects.get(matter=synced, status=ActionStatus.OPEN)
    cases = {
        "title": ("update_matter", {"title": "Uus pealkiri"}),
        "owner": (
            "assign_matter",
            {"action": "assign", "responsible_person": other_specialist.display_name},
        ),
        "next_action": ("set_next_action", {"mode": "new", "text": "Helistan", "kind": "do"}),
        "opinion_deadline": (
            "response_deadline",
            {"mode": "move", "deadline": "2099-07-01"},
        ),
    }
    for key, (name, arguments) in cases.items():
        found = refusal(name, specialist, matter=matter, **arguments)
        assert (found.code, str(found)) == ("domain_rule", refusal_message(key)), name
    found = refusal(
        "complete_action",
        specialist,
        matter=matter,
        action_id=str(current.pk),
        what_was_done="Tehtud.",
    )
    assert (found.code, str(found)) == ("domain_rule", refusal_message("next_action"))


# ---------------------------------------------------------------------------
# Review round 12.11: the mode, open holds, every completion door, the answer
# ---------------------------------------------------------------------------


def test_the_lock_follows_lovable_in_compare_and_apply_only():
    """Regression: TL-01/E9. The owner's rule (12.11): COMPARE and APPLY lock, OFF does not."""
    assert set(source_ownership.FOLLOWING_MODES) == {SyncMode.COMPARE, SyncMode.APPLY}
    assert set(source_ownership.FOLLOWING_MODES) <= set(SyncMode.values)


def test_a_persons_edit_is_refused_while_the_sync_compares(
    signed_in, synced, specialist, other_specialist
):
    """Regression: TL-01/E9. In COMPARE Lovable is still the source: read-only and marked."""
    from app.matters.services import (
        assign_matter,
        change_stage,
        matter_field_revision,
        set_matter_title,
    )
    from tests.test_mcp_write_operations_support import refusal

    _mode(SyncMode.COMPARE)
    assert is_source_owned(synced)
    page = _page(signed_in, "matters:matter_detail", synced)
    assert 'data-source-owned="owner"' in page
    assert _url("matters:update_field", synced, field="owner") not in page
    with refused(refusal_message("title")):
        set_matter_title(matter=synced, value="Juristi pealkiri", actor=specialist)
    with refused(refusal_message("owner")):
        assign_matter(matter=synced, owner=other_specialist, actor=specialist)
    with refused(refusal_message("stage")):
        change_stage(matter=synced, stage=_stage("parliament"), actor=specialist)
    response = signed_in.post(
        _url("matters:update_field", synced, field="owner"),
        {"owner": str(other_specialist.pk), "revision": matter_field_revision(synced, "owner")},
    )
    assert response.status_code == 400
    assert escape(refusal_message("owner")) in response.content.decode()
    found = refusal("update_matter", specialist, matter=str(synced.pk), title="Uus pealkiri")
    assert (found.code, str(found)) == ("domain_rule", refusal_message("title"))
    synced.refresh_from_db()
    assert (synced.owner, synced.stage.key) == (specialist, "government")
    assert synced.title != "Juristi pealkiri"


def test_while_comparing_the_sync_writes_and_a_held_field_stays_editable(
    synced, specialist, other_specialist
):
    """Regression: TL-01/E9. In COMPARE no actor is refused, and E11 holds still free a field."""
    from app.matters.services import assign_matter, set_matter_title

    _mode(SyncMode.COMPARE)
    set_matter_title(matter=synced, value="Lovable'i pealkiri", actor=None)
    synced.refresh_from_db()
    assert synced.title == "Lovable'i pealkiri"
    _hold(synced, "owner")
    assert source_ownership.ownership(synced).held == {"owner"}
    assign_matter(matter=synced, owner=other_specialist, actor=specialist)
    with refused(refusal_message("title")):
        set_matter_title(matter=synced, value="Juristi pealkiri", actor=specialist)


@pytest.mark.parametrize("state", ["paused", "cut_over"])
def test_nothing_is_locked_when_paused_or_after_cutover(state, signed_in, synced, specialist):
    """TL-01 (E9). OFF — never activated or paused — and cutover lock nothing (E9's exception)."""
    from app.matters.services import set_matter_title

    if state == "paused":
        _mode(SyncMode.OFF)
    else:
        lovable_sync.cut_over(reason="test")
    page = _page(signed_in, "matters:matter_detail", synced)
    assert "data-source-owned=" not in page
    assert _url("matters:update_field", synced, field="owner") in page
    set_matter_title(matter=synced, value="Juristi pealkiri", actor=specialist)
    if state == "paused":
        _mode(SyncMode.COMPARE)
        with refused(refusal_message("title")):
            set_matter_title(matter=synced, value="Teine", actor=specialist)


def _hold(matter: Any, field: str, reason: str = lovable_sync.UNRESOLVED_OWNER) -> None:
    state = LovableSyncState.objects.get(matter=matter)
    run = LovableSyncRun.objects.create(
        run_key=f"test-{uuid.uuid4().hex[:12]}",
        trigger="MANUAL",
        mode=SyncMode.APPLY,
        started_at=timezone.now(),
        mapping_version="test",
    )
    LovableSyncDecision.objects.create(
        run=run, state=state, field=field, outcome=SyncOutcome.REVIEW_REQUIRED, reason=reason
    )
    LovableSyncState.objects.filter(pk=state.pk).update(observed_run=run)


def test_a_field_under_an_open_hold_stays_editable_and_says_why(
    signed_in, synced, specialist, other_specialist
):
    """Regression: TL-01 (E11). The night never restores over a technical hold."""
    from app.matters.services import assign_matter, set_matter_title

    _hold(synced, "owner")
    page = _page(signed_in, "matters:matter_detail", synced)
    assert "data-source-held" in page and "vastutaja" in page
    assert 'data-source-owned="owner"' not in page
    assert _url("matters:update_field", synced, field="owner") in page
    assign_matter(matter=synced, owner=other_specialist, actor=specialist)
    with refused(refusal_message("title")):
        set_matter_title(matter=synced, value="Uus", actor=specialist)
    # A hold that is not a technical one frees nothing.
    _hold(synced, "title", reason="SOMETHING_ELSE")
    with refused(refusal_message("title")):
        set_matter_title(matter=synced, value="Uus", actor=specialist)


def test_every_completion_door_refuses_lovables_step(synced, specialist, organisation):
    """Regression: TL-01. The tick box on a round and on an opinion, and the core services."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from app.matters.enums import EngagementKind
    from app.matters.services import add_engagement
    from app.matters.workspace import add_engagement_feedback, add_matter_koda_opinion
    from app.submissions.models import Submission
    from app.workflow.services import cancel_next_action, complete_next_action

    step = NextAction.objects.get(matter=synced, status=ActionStatus.OPEN)
    today = timezone.localdate()
    engagement = add_engagement(
        matter=synced,
        kind=EngagementKind.SURVEY,
        title="liikmed",
        occurred_on=today - dt.timedelta(days=1),
        feedback_deadline=today + dt.timedelta(days=7),
        actor=specialist,
    )
    with refused(refusal_message("next_action")):
        add_engagement_feedback(
            engagement=engagement, author=specialist, complete_action_id=step.pk
        )
    with refused(refusal_message("next_action")):
        add_matter_koda_opinion(
            matter=synced,
            author=specialist,
            upload=SimpleUploadedFile("Koja_arvamus.pdf", b"%PDF-1.4 arvamus"),
            recipients=[organisation],
            sent_on=today,
            complete_action_id=step.pk,
        )
    assert not Submission.objects.filter(matter=synced).exists()
    with refused(refusal_message("next_action")):
        complete_next_action(action=step, actor=specialist)
    with refused(refusal_message("next_action")):
        cancel_next_action(action=step, actor=specialist)
    # Unticked, both are Juristid's own and save.
    engagement.refresh_from_db()
    add_engagement_feedback(engagement=engagement, author=specialist)
    add_matter_koda_opinion(
        matter=synced,
        author=specialist,
        upload=SimpleUploadedFile("Koja_arvamus.pdf", b"%PDF-1.4 arvamus"),
        recipients=[organisation],
        sent_on=today,
    )
    step.refresh_from_db()
    assert step.status == ActionStatus.OPEN


def test_a_planned_row_is_not_promoted_into_lovables_place(synced, specialist):
    """Regression: TL-01 (E12). A person's planned row stays planned on a locked Teema."""
    from app.workflow.services import promote_next_planned_action

    NextAction.objects.filter(matter=synced).update(status=ActionStatus.CANCELLED)
    planned = factories.NextActionFactory(
        matter=synced, status=ActionStatus.PLANNED, target_date=dt.date(2099, 5, 1)
    )
    assert promote_next_planned_action(matter=synced, actor=specialist) is None
    planned.refresh_from_db()
    assert planned.status == ActionStatus.PLANNED
    assert promote_next_planned_action(matter=synced, actor=None) is not None


def test_the_tick_box_is_not_offered_on_a_locked_teema(signed_in, synced, native):
    """Regression: TL-01. «Märgi praegune tegevus tehtuks» is not drawn on a locked Teema."""
    box = "id_koja_arvamus_tegevus_tehtud"
    assert box not in _page(signed_in, "matters:matter_detail", synced)
    assert box in _page(signed_in, "matters:matter_detail", native)


def test_a_lawyer_records_koda_ei_esita_arvamust(signed_in, synced):
    """Regression: TL-01 (E5). «Lõpeta tähtaeg» stays, limited to answered or declined."""
    from app.matters.response_deadlines import deadline_revision

    page = _page(signed_in, "matters:matter_detail", synced)
    assert "data-deadline-answer" in page
    answer = page[page.index("data-deadline-answer") :]
    answer = answer[: answer.index("</details>")]
    assert 'value="NOT_ANSWERING"' in answer and 'value="CANCELLED"' not in answer
    assert 'name="response_deadline"' not in answer
    response = signed_in.post(
        _url("matters:response_deadline", synced),
        {
            "tegevus": "lopeta",
            "previous_outcome": "NOT_ANSWERING",
            "revision": deadline_revision(synced),
        },
    )
    assert response.status_code == 200, response.content.decode()[:500]
    synced.refresh_from_db()
    assert synced.response_deadline is None


def test_the_legal_instruments_other_text_is_lovables_too(synced, specialist):
    """Regression: TL-01. The Õigusakt «Muu» text is part of the Õigusakt."""
    from app.matters.services import set_legal_instrument_other

    with refused(refusal_message("legal_act")):
        set_legal_instrument_other(matter=synced, value="Muu akt", actor=specialist)


def test_a_title_longer_than_the_cap_is_no_change(synced, specialist):
    """Regression: TL-01. A stored title only the cap would shorten is not refused as a change."""
    from app.matters.models import Matter
    from app.matters.services import set_matter_title

    long = "P" * 1200
    Matter.objects.filter(pk=synced.pk).update(title=long)
    synced.refresh_from_db()
    set_matter_title(matter=synced, value=long, actor=specialist)
