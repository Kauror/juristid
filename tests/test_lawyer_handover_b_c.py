# ruff: noqa: F811 - pytest fixtures imported from the sync suites are used as arguments
"""A lawyer leaves, another arrives (docs/adr/0220 §3–§4), on synthetic data.

§4: what a person still holds is shown on their account, switching them off
while they hold open work needs a confirmation, and «Töö ilma aktiivse
vastutajata» lists what switched-off people still hold. §3: a Lovable owner name
pinned by hand is what the night reads first, and «Lovable'i nimed» says who each
name is. Every name is invented.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from app.accounts import admin_session, administration
from app.accounts.enums import UserRole
from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from app.legacy_import import lovable_sync, sync_admin
from app.legacy_import.lovable_sync_models import LovableOwnerAlias, LovableSyncState
from app.matters.held_work import held_work, work_without_active_owner
from app.workflow.enums import ActionStatus
from tests import factories
from tests.admin_session import apply_admin_gate, full_admin, technical_admin, verified
from tests.local_auth import administrator, apply_local_password
from tests.refusals import refused

pytestmark = pytest.mark.django_db


def _actor(user):
    return admin_session._mint(user, via=admin_session.PATH)


def _step(matter, responsible, status=ActionStatus.OPEN):
    import datetime as dt

    return factories.NextActionFactory(
        matter=matter, responsible=responsible, status=status, target_date=dt.date(2099, 1, 15)
    )


@pytest.fixture
def desk():
    """Kati owns two open Teemad (one synchronised from Lovable), a closed one
    and a deleted one, and answers for two live steps."""
    kati = factories.UserFactory(display_name="Kati Näidis", role=UserRole.SPECIALIST)
    lovable = factories.MatterFactory(owner=kati)
    own = factories.MatterFactory(owner=kati)
    factories.MatterFactory(
        owner=kati, is_open=False, disposition="COMPLETED", closed_at=timezone.now()
    )
    factories.MatterFactory(owner=kati, deleted_at=timezone.now())
    LovableSyncState.objects.create(
        matter=lovable,
        source_id="00000000-0000-0000-0000-00000000a001",
        reference="2026_901",
        applied={},
        applied_snapshot_sha256="0" * 64,
        observed={},
        cohort="lovable-register",
    )
    _step(own, kati)
    _step(lovable, kati, ActionStatus.PLANNED)
    return {"kati": kati, "lovable": lovable, "own": own}


# ---------------------------------------------------------------------------
# §4 What a person still holds
# ---------------------------------------------------------------------------


def test_held_work_counts_live_open_teemad_and_live_steps(desk):
    work = held_work(desk["kati"])
    assert (work.teemad, work.lovable_teemad, work.steps) == (2, 1, 2)
    assert work.juristid_teemad == 1
    assert work.sentence() == "2 avatud teemat (neist 1 Lovable'is) ja 2 pooleli sammu"
    assert not held_work(factories.UserFactory()).any


def test_switching_off_a_person_with_open_work_needs_a_confirmation(gate, desk):
    admin, _ = _manager()
    kati = desk["kati"]
    with refused(administration.OPEN_WORK_REMAINS.format(work=held_work(kati).sentence())):
        administration.deactivate_account(actor=_actor(admin), user=kati, reason="lahkus")
    kati.refresh_from_db()
    assert kati.is_active

    administration.deactivate_account(
        actor=_actor(admin), user=kati, reason="lahkus", leave_open_work=True
    )
    kati.refresh_from_db()
    assert not kati.is_active
    event = SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.ACCOUNT_DEACTIVATED, subject_id=str(kati.pk)
    ).get()
    assert (event.detail["open_teemad"], event.detail["open_lovable_teemad"]) == (2, 1)
    assert event.detail["open_steps"] == 2


def test_a_person_with_no_open_work_is_switched_off_as_before(gate):
    admin, _ = _manager()
    leaver = factories.UserFactory()
    factories.MatterFactory(
        owner=leaver, is_open=False, disposition="COMPLETED", closed_at=timezone.now()
    )
    administration.deactivate_account(actor=_actor(admin), user=leaver)
    leaver.refresh_from_db()
    assert not leaver.is_active
    event = SecurityAuditEvent.objects.get(
        event_type=SecurityEventType.ACCOUNT_DEACTIVATED, subject_id=str(leaver.pk)
    )
    assert "open_teemad" not in event.detail


def test_switched_off_people_s_work_is_listed_with_the_unowned_teemad(gate, desk):
    admin, _ = _manager()
    administration.deactivate_account(actor=_actor(admin), user=desk["kati"], leave_open_work=True)
    factories.MatterFactory(owner=None)
    orphaned = work_without_active_owner()
    assert [h.person.pk for h in orphaned.holders] == [desk["kati"].pk]
    assert orphaned.holders[0].work.teemad == 2
    assert orphaned.unowned_teemad == 1
    # An active colleague's work is nobody's business here.
    factories.MatterFactory(owner=factories.UserFactory())
    assert len(work_without_active_owner().holders) == 1


def _manager():
    return full_admin()


@pytest.fixture
def gate(settings):
    return apply_admin_gate(settings)


@pytest.fixture
def local(settings):
    return apply_local_password(settings)


def test_the_account_page_shows_held_work_and_asks_before_switching_off(client, local, desk):
    administrator(client)
    page = client.get(reverse("account_admin:detail", kwargs={"pk": desk["kati"].pk}))
    body = page.content.decode()
    assert page.status_code == 200
    assert "2 avatud teemat (neist 1 Lovable&#x27;is) ja 2 pooleli sammu" in body
    assert 'name="leave_open_work"' in body

    response = client.post(
        reverse("account_admin:deactivate", kwargs={"pk": desk["kati"].pk}), {"reason": "x"}
    )
    assert response.status_code == 302
    desk["kati"].refresh_from_db()
    assert desk["kati"].is_active

    client.post(
        reverse("account_admin:deactivate", kwargs={"pk": desk["kati"].pk}),
        {"reason": "x", "leave_open_work": "on"},
    )
    desk["kati"].refresh_from_db()
    assert not desk["kati"].is_active

    listing = client.get(reverse("account_admin:without_owner"))
    assert listing.status_code == 200
    assert "Kati Näidis" in listing.content.decode()


def test_the_without_owner_page_is_a_404_for_a_colleague(client, local):
    from tests.local_auth import person, sign_in

    colleague, _ = person(role=UserRole.DEPARTMENT_HEAD)
    sign_in(client, colleague)
    assert client.get(reverse("account_admin:without_owner")).status_code == 404


# ---------------------------------------------------------------------------
# §3 Lovable'i nimed
# ---------------------------------------------------------------------------


def test_a_pinned_name_is_read_before_the_learned_pairs_and_the_display_name():
    marko = factories.UserFactory(display_name="Marko Udras", role=UserRole.SPECIALIST)
    other = factories.UserFactory(display_name="Marko", role=UserRole.SPECIALIST)
    resolver = lovable_sync.OwnerResolver([])
    # Without a pin, the exact display name wins.
    assert resolver.explain("Marko").user_id == str(other.pk)
    LovableOwnerAlias.objects.create(name="Marko", name_key="marko", user=marko)
    answer = lovable_sync.OwnerResolver([]).explain("  MARKO ")
    assert (answer.user_id, answer.via) == (str(marko.pk), lovable_sync.VIA_ALIAS)


def test_a_name_pinned_to_a_switched_off_person_is_no_answer():
    leaver = factories.UserFactory(display_name="Ann Näidis", is_active=False)
    LovableOwnerAlias.objects.create(name="Ann", name_key="ann", user=leaver)
    answer = lovable_sync.OwnerResolver([]).explain("Ann")
    assert (answer.user_id, answer.code) == (None, lovable_sync.UNRESOLVED_OWNER)


def test_pinning_refuses_a_person_who_may_not_hold_work_and_is_audited():
    admin = factories.UserFactory()
    reader = factories.UserFactory(role=UserRole.READER)
    with refused(sync_admin.ALIAS_PERSON_REFUSED):
        sync_admin.pin_owner_name(admin, name="Liis", person=reader)
    with refused(sync_admin.ALIAS_NAME_MISSING):
        sync_admin.pin_owner_name(admin, name="  ", person=reader)
    lawyer = factories.UserFactory(display_name="Liisa Näidis", role=UserRole.SPECIALIST)
    alias = sync_admin.pin_owner_name(admin, name=" Liis ", person=lawyer, note="uus jurist")
    assert (alias.name, alias.name_key, alias.user_id) == ("Liis", "liis", lawyer.pk)
    # The same name again moves the pin; one row per name.
    other = factories.UserFactory(display_name="Liis Teine", role=UserRole.SPECIALIST)
    sync_admin.pin_owner_name(admin, name="LIIS", person=other)
    assert LovableOwnerAlias.objects.get().user_id == other.pk
    actions = list(
        SecurityAuditEvent.objects.filter(event_type=SecurityEventType.IMPORT_RUN).values_list(
            "detail__action", flat=True
        )
    )
    assert actions.count("LOVABLE_OWNER_ALIAS_PINNED") == 2
    name = sync_admin.unpin_owner_name(admin, alias_id=alias.pk)
    # The newest spelling is kept as the pin's name.
    assert name == "LIIS" and not LovableOwnerAlias.objects.exists()
    with refused(sync_admin.ALIAS_NOT_FOUND):
        sync_admin.unpin_owner_name(admin, alias_id=None)


def test_owner_names_lists_the_names_of_the_newest_reading_problems_first(world_reading):
    rows, read_at = sync_admin.owner_names()
    assert read_at is not None
    by_name = {row.name: row for row in rows}
    assert by_name["Uus Jurist"].answer.code == lovable_sync.UNRESOLVED_OWNER
    assert by_name["Uus Jurist"].handed_over == 1
    assert by_name["Mari Näidis"].ok and by_name["Mari Näidis"].topics == 2
    assert rows[0].name == "Uus Jurist"


@pytest.fixture
def world_reading():
    from app.legacy_import.lovable_sync_models import LovableSyncRun, LovableSyncSnapshot

    factories.UserFactory(display_name="Mari Näidis", role=UserRole.SPECIALIST)
    run = LovableSyncRun.objects.create(
        run_key="2026-11-23T000000-test",
        trigger="MANUAL",
        mode="COMPARE",
        status="SUCCEEDED",
        started_at=timezone.now(),
        mapping_version="lovable-sync/1",
    )
    LovableSyncSnapshot.objects.create(
        run=run,
        taken_at=timezone.now(),
        sha256="0" * 64,
        document={
            "cohort": {
                "a": {"row": {"vastutaja": "Mari Näidis", "uus_vastutaja": ""}},
                "b": {
                    "row": {"vastutaja": "Mari Näidis", "uus_vastutaja": ""},
                    "responsible_changes": [
                        {"uus_vastutaja": "Uus Jurist", "alates": "2026-11-22"}
                    ],
                },
            }
        },
    )


@pytest.fixture
def signed(client, settings):
    apply_admin_gate(settings)
    user, secret = technical_admin()
    verified(client, user, secret)
    return client


def test_the_names_page_checks_a_typed_name_and_pins_it(signed, world_reading):
    url = reverse("haldus:data_sync_names")
    page = signed.get(url, {"nimi": "Uus Jurist"})
    assert page.status_code == 200
    body = page.content.decode()
    assert "Lovable&#x27;i nimed" in body and "Ei leitud" in body
    lawyer = factories.UserFactory(display_name="Uus Juristi Nimi", role=UserRole.SPECIALIST)
    signed.post(url, {"action": "pin", "name": "Uus Jurist", "person": str(lawyer.pk)})
    assert LovableOwnerAlias.objects.get().user_id == lawyer.pk
    answer = lovable_sync.OwnerResolver([]).explain("uus jurist")
    assert answer.user_id == str(lawyer.pk)
