"""`workspace.edit_matter` — `Muuda teemat`'s save, without a view (JARCH-001).

The twelve service calls of a whole-record correction used to be composed in
`views.matter_edit`, where their order — the revision guard first, the held
`Õigusakt` read before it is rewritten, the `Menetluse link` inside the same
transaction, `Hetkeseis` last — was a property of one view. These tests call
the use case directly, so any other caller gets the same guarantees:

* every fact is applied, through its own service;
* a copy filled before somebody else's save is refused and writes nothing;
* only a change of `Õigusakt` in this save moves `Koja algatus`, which needs
  the instrument as it stood **before** the save;
* the procedural-link step runs after every other fact and before the stage,
  inside the transaction: a refusal there takes everything back, including a
  newly typed institution.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.matters import workspace
from app.matters.initiative import KODA_PROPOSAL_INSTRUMENT_KEY, is_koda_initiative
from app.matters.models import Matter
from app.matters.services import MATTER_EDIT_CONFLICT, matter_revision_token
from app.organisations.models import Organisation
from app.taxonomy.models import LegalInstrumentType
from app.workflow.enums import Track
from tests import factories
from tests.refusals import refused

pytestmark = pytest.mark.django_db

LINK_REFUSED = "Sünteetiline menetluse lingi keeldumine."


def _instrument(key: str) -> list[LegalInstrumentType]:
    return [LegalInstrumentType.objects.get(key=key)]


def _edit(matter: Matter, actor, **overrides):
    """The whole record as it stands, with ``overrides`` — what the page posts."""
    answers = {
        "expected_revision": matter_revision_token(matter),
        "title": matter.title,
        "brief_summary": matter.brief_summary,
        "owner": matter.owner,
        "source_organisations": list(matter.source_organisations.all()),
        "sender_name": "",
        "legal_instruments": list(matter.legal_instruments.all()),
        "legal_instrument_other": matter.legal_instrument_other,
        "received_date": matter.received_date,
        "response_deadline": matter.response_deadline,
        "policy_areas": list(matter.policy_areas.all()),
        "policy_area_other": matter.policy_area_other,
        "stage": None,
        **overrides,
    }
    workspace.edit_matter(matter=matter, actor=actor, **answers)
    matter.refresh_from_db()
    return matter


def test_every_fact_is_applied(specialist, other_specialist):
    matter = factories.MatterFactory(owner=specialist, title="Vana pealkiri")

    _edit(
        matter,
        specialist,
        title="Uus pealkiri",
        brief_summary="Lühikokkuvõte.",
        owner=other_specialist,
        sender_name="Sünteetiline Saatjaamet",
        policy_area_other="Muu valdkond",
        legal_instrument_other="Muu akt",
    )

    assert matter.title == "Uus pealkiri"
    assert matter.brief_summary == "Lühikokkuvõte."
    assert matter.owner == other_specialist
    assert [org.name for org in matter.source_organisations.all()] == ["Sünteetiline Saatjaamet"]
    assert matter.policy_area_other == "Muu valdkond"
    assert matter.legal_instrument_other == "Muu akt"


def test_a_stale_copy_is_refused_and_writes_nothing(specialist):
    matter = factories.MatterFactory(owner=specialist, title="Puutumata")
    stale = matter_revision_token(matter)
    _edit(matter, specialist, brief_summary="Kellegi teise salvestus.")

    with refused(MATTER_EDIT_CONFLICT):
        _edit(
            matter,
            specialist,
            expected_revision=stale,
            title="Vana koopia",
            sender_name="Sünteetiline Vananenud Amet",
        )

    matter.refresh_from_db()
    assert matter.title == "Puutumata"
    assert not Organisation.objects.filter(name="Sünteetiline Vananenud Amet").exists()


def test_a_change_to_the_proposal_instrument_makes_it_an_initiative(specialist):
    """Proves the held instrument is read before `set_legal_instruments`.

    Read afterwards, the save would compare the new instrument with itself,
    see no change, and leave the track as it was.
    """
    matter = factories.MatterFactory(owner=specialist)
    _edit(matter, specialist, legal_instruments=_instrument("seadus"))
    assert not is_koda_initiative(matter)

    _edit(matter, specialist, legal_instruments=_instrument(KODA_PROPOSAL_INSTRUMENT_KEY))
    assert is_koda_initiative(matter)

    # An unrelated correction leaves the track as it is.
    _edit(matter, specialist, title="Muu parandus")
    assert is_koda_initiative(matter)

    _edit(matter, specialist, legal_instruments=_instrument("seadus"))
    assert not is_koda_initiative(matter)


def test_the_link_step_runs_after_every_other_fact_and_inside_the_save(specialist):
    matter = factories.MatterFactory(owner=specialist, title="Enne")
    seen: list[str] = []

    def save_link() -> None:
        seen.append(Matter.objects.get(pk=matter.pk).title)

    _edit(matter, specialist, title="Pärast", save_procedural_link=save_link)

    assert seen == ["Pärast"]


def test_a_refused_link_takes_the_whole_correction_back(specialist):
    first = factories.OrganisationFactory(name="Rahandusministeerium")
    matter = factories.MatterFactory(
        owner=specialist, title="Puutumata pealkiri", addressee_organisation=first
    )
    organisations = Organisation.objects.count()

    def refuse() -> None:
        raise DomainError(LINK_REFUSED)

    with refused(LINK_REFUSED):
        _edit(
            matter,
            specialist,
            title="Muudetud pealkiri",
            sender_name="Uus tundmatu asutus",
            save_procedural_link=refuse,
        )

    matter.refresh_from_db()
    assert matter.title == "Puutumata pealkiri"
    assert matter.addressee_organisation == first
    assert Organisation.objects.count() == organisations


# --- HR-14: the save works on the row the guard locked -----------------------
#
# `edit_matter` takes the Matter row under `guard_matter_revision` and then
# runs every service on the row it locked, not on the instance the view loaded
# before the transaction. The tests below load a request-time copy, let a
# colleague's save land on the row (`queryset.update`, which leaves the copy
# untouched), and then save through the copy.


def _request_time_copy(matter: Matter) -> Matter:
    return Matter.objects.get(pk=matter.pk)


def test_a_colleagues_track_is_not_overwritten_from_the_stale_copy(specialist):
    """A fact derived from the row is judged on the stored row.

    The copy still says `Koja algatus`; a colleague has since put the Teema on
    another track. Changing `Õigusakt` away from the Chamber proposal unmarks
    an initiative — but this Teema no longer is one, so its track is left
    alone. Judged on the copy, the save cleared the colleague's track.
    """
    matter = factories.MatterFactory(owner=specialist)
    _edit(matter, specialist, legal_instruments=_instrument(KODA_PROPOSAL_INSTRUMENT_KEY))
    assert is_koda_initiative(matter)
    stale = _request_time_copy(matter)
    Matter.objects.filter(pk=matter.pk).update(track=Track.DOMESTIC)

    _edit(stale, specialist, expected_revision=None, legal_instruments=_instrument("seadus"))

    assert Matter.objects.get(pk=matter.pk).track == Track.DOMESTIC
    assert not ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.MATTER_TRACK_CHANGED, payload__to=""
    ).exists()


def test_the_audit_from_value_is_the_stored_one_not_the_copys(specialist):
    """Without a token nothing is refused, and each posted value is applied
    against the stored row: the event names the colleague's title as the one
    replaced, not the copy's."""
    matter = factories.MatterFactory(owner=specialist, title="Vana pealkiri")
    stale = _request_time_copy(matter)
    Matter.objects.filter(pk=matter.pk).update(title="Kolleegi pealkiri")

    _edit(stale, specialist, expected_revision=None, title="Minu pealkiri")

    assert Matter.objects.get(pk=matter.pk).title == "Minu pealkiri"
    event = ChangeEvent.objects.get(matter=matter, event_type=ChangeEventType.MATTER_TITLE_CHANGED)
    assert event.payload["previous"] == "Kolleegi pealkiri"
    assert event.payload["current"] == "Minu pealkiri"


def test_a_value_already_stored_writes_no_event_even_from_a_stale_copy(specialist):
    """The posted title is what is stored now, so there is nothing to change —
    compared with the copy it looked like a rename and wrote an event."""
    matter = factories.MatterFactory(owner=specialist, title="Vana pealkiri")
    stale = _request_time_copy(matter)
    Matter.objects.filter(pk=matter.pk).update(title="Kolleegi pealkiri")

    _edit(stale, specialist, expected_revision=None, title="Kolleegi pealkiri")

    assert not ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.MATTER_TITLE_CHANGED
    ).exists()


def test_with_a_token_a_copy_older_than_the_stored_row_is_refused(specialist):
    matter = factories.MatterFactory(owner=specialist, title="Vana pealkiri")
    stale = _request_time_copy(matter)
    token = matter_revision_token(stale)
    Matter.objects.filter(pk=matter.pk).update(title="Kolleegi pealkiri", updated_at=timezone.now())

    with refused(MATTER_EDIT_CONFLICT):
        _edit(stale, specialist, expected_revision=token, title="Minu pealkiri")

    assert Matter.objects.get(pk=matter.pk).title == "Kolleegi pealkiri"
    assert not ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.MATTER_TITLE_CHANGED
    ).exists()


def _row_locks(queries: list[dict]) -> list[tuple[str, str]]:
    """``(table, strength)`` of every `SELECT … FOR [NO KEY] UPDATE`, in order."""
    locks = []
    for query in queries:
        sql = query["sql"]
        for strength in ("FOR NO KEY UPDATE", "FOR UPDATE"):
            if f" {strength}" in sql:
                table = sql.split(" FROM ", 1)[1].split()[0].strip('"')
                locks.append((table, strength))
                break
    return locks


def test_the_save_takes_the_same_locks_in_the_same_order(specialist):
    """No new lock path: the Matter row first, at `FOR NO KEY UPDATE`, and the
    same row locks after it as the save took before HR-14 (the deadline writer
    and the dates writer re-take the row they already hold)."""
    matter = factories.MatterFactory(owner=specialist, title="Vana pealkiri")
    stale = _request_time_copy(matter)

    with CaptureQueriesContext(connection) as captured:
        _edit(
            stale,
            specialist,
            title="Uus pealkiri",
            received_date=date(2026, 11, 2),
            response_deadline=date(2026, 11, 30),
        )

    # Measured on main before HR-14 and unchanged by it: seven statements, all
    # on the one Matter row at `FOR NO KEY UPDATE` — the guard first, then the
    # writers that re-take the row they already hold (re-entrant inside the
    # transaction, so they add no wait and no ordering).
    matter_row = (Matter._meta.db_table, "FOR NO KEY UPDATE")
    assert _row_locks(captured.captured_queries) == [matter_row] * 7
