"""Two tabs, one whole record, and no silent overwrite.

`tests/test_personal_note_concurrency.py` closed this hole for the two pads that
file no history, and said in as many words what it was deliberately *not*
closing: «No global optimistic locking of canonical business editing — that is a
much larger contract with a much larger surface». Exploratory QA then walked
straight into the surface it named.

`Muuda teemat` and `Muuda kulgu` are the two editors that post an entire current
representation of something. Everything else on a Teema posts one record — a
`Märge`, an opinion, an engagement — and every one of those already carries a
revision token. These two did not, so:

* tab A reassigned a Matter to Martin and saved; tab B, holding the page from
  before that, changed only the summary and saved — and the owner went back to
  Sandra with no warning anywhere (QA-002);
* tab A dated `Valitsuses`; tab B, holding the panel from before that, dated
  `Riigikogus` — and A's date was simply gone (QA-004).

Both are persisted loss of a decision somebody made, and in the Matter's case
the reversal is written to the audit trail as an ordinary reassignment, so the
history does not even read as an accident.

The fix is the mechanism the rest of the module already uses, not a new one:
the rendered form carries the version it was filled from, the save compares it
*under the row lock*, and a save that is behind writes nothing and says so.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from app.matters.models import MatterTimelineStep
from app.matters.services import (
    MATTER_EDIT_CONFLICT,
    TIMELINE_STEPS_CONFLICT,
    MatterEditConflict,
    TimelineStepsConflict,
    assign_matter,
    guard_matter_revision,
    matter_revision_token,
    set_legal_instruments,
    set_matter_title,
    set_policy_areas,
    set_timeline_steps,
    timeline_steps_revision_token,
)
from app.workflow.enums import DatePrecision

pytestmark = pytest.mark.django_db


MATTER_EDIT = "matters:matter_edit"
TIMELINE_STEPS = "matters:timeline_steps"


def _rendered_revision(client, url: str, name: str = "revision") -> str:
    """The token the page hands the browser, read off the rendered markup."""
    body = client.get(url).content.decode()
    field = re.search(rf'<input[^>]*name="{name}"[^>]*>', body)
    assert field is not None, f"no {name} field is rendered by {url}"
    found = re.search(r'value="([^"]*)"', field.group(0))
    return found.group(1) if found else ""


# ---------------------------------------------------------------------------
# The Matter guard, at the seam that owns it
# ---------------------------------------------------------------------------


def test_the_token_moves_when_a_matter_field_changes(normal_matter, specialist):
    before = matter_revision_token(normal_matter)

    set_matter_title(matter=normal_matter, value="Uus pealkiri", actor=specialist)
    normal_matter.refresh_from_db()

    assert matter_revision_token(normal_matter) != before


def test_the_token_moves_for_a_join_table_only_change(normal_matter, specialist, db):
    """`Valdkonnad` writes no scalar column, and must still make a page stale.

    `set_policy_areas` used to write the join table and nothing else, so a
    Matter whose areas somebody had just corrected still claimed it had not
    been touched — and a stale page would have been accepted as current and
    posted the old areas back over them.
    """
    from app.taxonomy.models import PolicyArea

    area = PolicyArea.objects.create(name_et="QA valdkond", key="qa-valdkond")
    before = matter_revision_token(normal_matter)

    set_policy_areas(matter=normal_matter, policy_areas=[area], actor=specialist)
    normal_matter.refresh_from_db()

    assert matter_revision_token(normal_matter) != before


def test_the_token_moves_for_a_legal_instrument_change(normal_matter, specialist, db):
    from app.taxonomy.models import LegalInstrumentType

    instrument = LegalInstrumentType.objects.create(
        label_et="QA õigusakt", key="qa-oigusakt", sort_order=900
    )
    before = matter_revision_token(normal_matter)

    set_legal_instruments(matter=normal_matter, legal_instruments=[instrument], actor=specialist)
    normal_matter.refresh_from_db()

    assert matter_revision_token(normal_matter) != before


def test_the_token_does_not_move_when_content_is_added(normal_matter, specialist):
    """A `Märge` is not an edit of the record the page posts.

    Guarding the whole Matter would be useless if writing a note made every
    open edit page stale: a lawyer would be refused for work they did
    themselves, and would learn to ignore the refusal.
    """
    from app.matters.workspace import add_procedural_development

    before = matter_revision_token(normal_matter)
    add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="Ministeerium saatis uue versiooni",
        occurred_on=None,
        occurred_on_precision=DatePrecision.EXACT.value,
    )
    normal_matter.refresh_from_db()

    assert matter_revision_token(normal_matter) == before


def test_a_stale_matter_revision_is_refused(normal_matter, specialist, other_specialist):
    stale = matter_revision_token(normal_matter)
    assign_matter(matter=normal_matter, owner=other_specialist, actor=specialist)
    normal_matter.refresh_from_db()

    from django.db import transaction

    with pytest.raises(MatterEditConflict) as raised, transaction.atomic():
        guard_matter_revision(matter=normal_matter, expected_revision=stale)

    assert str(raised.value) == MATTER_EDIT_CONFLICT
    # The conflict carries the row as it now stands, so the caller can show the
    # other side rather than only announcing that there is one.
    assert raised.value.current.owner_id == other_specialist.pk


def test_an_absent_matter_revision_is_not_a_conflict(normal_matter):
    """A caller with no token never rendered one, and is not writing over anyone."""
    from django.db import transaction

    with transaction.atomic():
        locked = guard_matter_revision(matter=normal_matter, expected_revision=None)

    assert locked.pk == normal_matter.pk


# ---------------------------------------------------------------------------
# The Matter guard, through the page
# ---------------------------------------------------------------------------


def _edit_payload(matter, revision: str, **overrides) -> dict:
    payload = {
        "revision": revision,
        "title": matter.title,
        "brief_summary": matter.brief_summary,
        "received_date": "",
        "response_deadline": "",
        "policy_area_other": "",
        "legal_instrument_other": "",
        "sender_name": "",
        "menetlus-url": "",
        "menetlus-label": "",
        "menetlus-revision": "",
    }
    if matter.owner_id:
        payload["owner"] = str(matter.owner_id)
    payload.update(overrides)
    return payload


def test_two_tabs_on_muuda_teemat_do_not_clobber_each_other(
    signed_in, normal_matter, specialist, other_specialist
):
    """QA-002, end to end through the page both tabs actually post."""
    url = reverse(MATTER_EDIT, kwargs={"pk": normal_matter.pk})
    shared = _rendered_revision(signed_in, url)
    assert shared, "the edit page renders no revision token"

    # Tab A hands the Matter to a colleague.
    first = signed_in.post(
        url, _edit_payload(normal_matter, shared, owner=str(other_specialist.pk))
    )
    assert first.status_code == 302
    normal_matter.refresh_from_db()
    assert normal_matter.owner_id == other_specialist.pk

    # Tab B still holds the page from before that, and edits only the summary.
    second = signed_in.post(
        url,
        _edit_payload(
            normal_matter, shared, owner=str(specialist.pk), brief_summary="Teisest sakist."
        ),
    )

    assert second.status_code == 409
    body = second.content.decode()
    assert MATTER_EDIT_CONFLICT in body
    # What tab B typed is still on the page, so the two versions can be
    # reconciled by a person rather than by whoever pressed Salvesta last.
    assert "Teisest sakist." in body

    # And the colleague's assignment stands.
    normal_matter.refresh_from_db()
    assert normal_matter.owner_id == other_specialist.pk
    assert normal_matter.brief_summary != "Teisest sakist."


def test_the_refused_page_offers_the_current_token_so_a_second_press_lands(
    signed_in, normal_matter, specialist, other_specialist
):
    """A conflict must be resolvable, not a wall."""
    url = reverse(MATTER_EDIT, kwargs={"pk": normal_matter.pk})
    shared = _rendered_revision(signed_in, url)
    signed_in.post(url, _edit_payload(normal_matter, shared, owner=str(other_specialist.pk)))

    refused = signed_in.post(
        url, _edit_payload(normal_matter, shared, brief_summary="Teisest sakist.")
    )
    assert refused.status_code == 409
    field = re.search(r'<input[^>]*name="revision"[^>]*>', refused.content.decode())
    assert field is not None
    offered = re.search(r'value="([^"]*)"', field.group(0)).group(1)

    normal_matter.refresh_from_db()
    assert offered == matter_revision_token(normal_matter)

    again = signed_in.post(
        url, _edit_payload(normal_matter, offered, brief_summary="Teisest sakist.")
    )
    assert again.status_code == 302
    normal_matter.refresh_from_db()
    assert normal_matter.brief_summary == "Teisest sakist."


def test_a_matter_edit_without_a_token_still_saves(signed_in, normal_matter):
    """Nothing is locked out by the guard's existence."""
    url = reverse(MATTER_EDIT, kwargs={"pk": normal_matter.pk})

    response = signed_in.post(url, _edit_payload(normal_matter, "", title="Ilma märgita"))

    assert response.status_code == 302
    normal_matter.refresh_from_db()
    assert normal_matter.title == "Ilma märgita"


# ---------------------------------------------------------------------------
# The rail's steps
# ---------------------------------------------------------------------------


def _step(key: str, hidden: bool = False, occurs_on=None):
    return (key, hidden, occurs_on, DatePrecision.EXACT.value)


def test_the_steps_token_moves_when_a_step_is_stored(normal_matter, specialist):
    import datetime

    before = timeline_steps_revision_token(normal_matter)

    set_timeline_steps(
        matter=normal_matter,
        steps=[_step("valitsus", occurs_on=datetime.date(2026, 10, 1))],
        actor=specialist,
    )

    assert timeline_steps_revision_token(normal_matter) != before


def test_the_steps_token_returns_to_the_default_when_the_row_is_deleted(normal_matter, specialist):
    """The ordinary answer stores no row, which is why the token is a digest.

    `MAX(updated_at)` would go backwards here and `COUNT` would collide; the
    state itself does neither, and a rail restored to its default is honestly
    indistinguishable from one nobody has ever edited.
    """
    import datetime

    empty = timeline_steps_revision_token(normal_matter)
    set_timeline_steps(
        matter=normal_matter,
        steps=[_step("valitsus", occurs_on=datetime.date(2026, 10, 1))],
        actor=specialist,
    )
    assert timeline_steps_revision_token(normal_matter) != empty

    set_timeline_steps(matter=normal_matter, steps=[_step("valitsus")], actor=specialist)

    assert MatterTimelineStep.objects.filter(matter=normal_matter).count() == 0
    assert timeline_steps_revision_token(normal_matter) == empty


def test_a_stale_steps_revision_is_refused_and_writes_nothing(normal_matter, specialist):
    """QA-004, at the seam that owns it."""
    import datetime

    stale = timeline_steps_revision_token(normal_matter)
    set_timeline_steps(
        matter=normal_matter,
        steps=[_step("valitsus", occurs_on=datetime.date(2026, 10, 1))],
        actor=specialist,
        expected_revision=stale,
    )

    with pytest.raises(TimelineStepsConflict) as raised:
        set_timeline_steps(
            matter=normal_matter,
            steps=[_step("riigikogu", occurs_on=datetime.date(2026, 10, 2))],
            actor=specialist,
            expected_revision=stale,
        )

    assert str(raised.value) == TIMELINE_STEPS_CONFLICT
    stored = {row.phase_key: row.occurs_on for row in MatterTimelineStep.objects.all()}
    assert stored == {"valitsus": datetime.date(2026, 10, 1)}


def test_an_absent_steps_revision_is_not_a_conflict(normal_matter, specialist):
    import datetime

    set_timeline_steps(
        matter=normal_matter,
        steps=[_step("valitsus", occurs_on=datetime.date(2026, 10, 1))],
        actor=specialist,
    )

    set_timeline_steps(
        matter=normal_matter,
        steps=[_step("riigikogu", occurs_on=datetime.date(2026, 10, 2))],
        actor=specialist,
        expected_revision=None,
    )

    assert MatterTimelineStep.objects.filter(matter=normal_matter).count() == 2


def test_a_refused_steps_panel_is_retargeted_to_its_own_slot(signed_in, normal_matter, specialist):
    """A refusal must not replace the page with a bare form.

    The form targets `#teema-vaade`, because a *saved* panel changes the rail
    and everything the rail says. A refused one re-renders the panel, and
    swapping that into the same target takes the rail, the chronology and the
    launcher with it — which is what the 400 path had been doing since it was
    written.
    """
    url = reverse(TIMELINE_STEPS, kwargs={"pk": normal_matter.pk})

    response = signed_in.post(
        url,
        {"revision": "definitely-not-the-stored-one", "kooskolastus__shown": "on"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 409
    assert response.headers["HX-Retarget"] == "#menetluse-kulg-muuda"
    assert response.headers["HX-Reswap"] == "innerHTML"
    assert TIMELINE_STEPS_CONFLICT in response.content.decode()
