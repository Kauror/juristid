"""«Lovable'i nimed» puts the names that answer for open work first (ADR 0220 §3), synthetic data.

The page reads the newest stored Lovable reading only. A name is a current owner
when it is the operative owner («Uus vastutaja» over «Vastutaja») of a topic
Lovable counts as ongoing (`lovable_sync.source_activity`: operational, not
finished, not continued elsewhere). Every other name — owners of finished,
historical or continued topics, names only a responsible-change row mentions,
pins no topic carries — is kept whole in a folded section. Every name is invented.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from django.urls import reverse
from django.utils import timezone

from app.accounts.enums import UserRole
from app.legacy_import import lovable_sync, sync_admin
from app.legacy_import.lovable_sync_models import (
    LovableOwnerAlias,
    LovableSyncRun,
    LovableSyncSnapshot,
)
from tests import factories
from tests.admin_session import apply_admin_gate, technical_admin, verified

pytestmark = pytest.mark.django_db


def _topic(
    n: int,
    owner: str,
    *,
    new_owner: str = "",
    stage: str = "Riigikogus",
    operational: bool = True,
    changes: list[dict[str, Any]] | None = None,
    continued: bool = False,
) -> tuple[str, dict[str, Any]]:
    topic_id = f"00000000-0000-0000-0000-{n:012d}"
    bundle = {
        "row": {
            "id": topic_id,
            "nr": f"2026_{n}",
            "vastutaja": owner,
            "uus_vastutaja": new_owner,
            "hetkeseis": stage,
            "operatiivne_ulatus": operational,
        },
        "responsible_changes": changes or [],
        "stage_links": [{"prev": topic_id, "next": "00000000-0000-0000-0000-999999999999"}]
        if continued
        else [],
    }
    return topic_id, bundle


def _reading(
    *topics: tuple[str, dict[str, Any]], at: dt.datetime | None = None, key: str = "a"
) -> None:
    moment = at or timezone.now()
    run = LovableSyncRun.objects.create(
        run_key=f"2026-11-23T00000{key}-test",
        trigger="MANUAL",
        mode="COMPARE",
        status="SUCCEEDED",
        started_at=moment,
        mapping_version="lovable-sync/1",
    )
    LovableSyncSnapshot.objects.create(
        run=run, taken_at=moment, sha256="0" * 64, document={"cohort": dict(topics)}
    )


@pytest.fixture
def lawyers():
    return {
        "mari": factories.UserFactory(display_name="Mari Näidis", role=UserRole.SPECIALIST),
        "jaan": factories.UserFactory(display_name="Jaan Näidis", role=UserRole.SPECIALIST),
    }


def _by_name(rows):
    return {row.name: row for row in rows}


def test_open_owners_come_first_with_their_open_topics(lawyers):
    _reading(
        _topic(1, "Mari Näidis"),
        _topic(2, "Mari Näidis"),
        _topic(3, "Mari Näidis", stage="jõustunud"),  # finished: counted, not open
        _topic(4, "Jaan Näidis"),
    )
    names = sync_admin.owner_names()
    current = _by_name(names.current)
    assert set(current) == {"Mari Näidis", "Jaan Näidis"}
    assert (current["Mari Näidis"].open_topics, current["Mari Näidis"].topics) == (2, 3)
    assert [row.name for row in names.current] == [
        "Mari Näidis",
        "Jaan Näidis",
    ]  # more open work first
    assert names.other == [] and names.problems == 0


def test_an_unresolved_owner_of_open_work_is_first_and_is_the_only_problem(lawyers):
    _reading(
        _topic(1, "Mari Näidis"),
        _topic(2, "Mari Näidis"),
        _topic(3, "Uus Jurist"),  # open, no Juristid person: needs action
        _topic(4, "Lahkunud Kolleeg", operational=False),  # historical only
        _topic(5, "Endine Jurist", stage="rohkem ei tegele"),  # finished only
    )
    names = sync_admin.owner_names()
    assert names.current[0].name == "Uus Jurist"
    assert names.current[0].answer.code == lovable_sync.UNRESOLVED_OWNER
    assert names.problems == 1
    other = _by_name(names.other)
    # Historical and finished names stay, with their own answer, but are no problem.
    assert set(other) == {"Lahkunud Kolleeg", "Endine Jurist"}
    assert all(not row.ok and not row.current for row in other.values())


def test_operational_scope_alone_is_not_open_work(lawyers):
    """`operatiivne_ulatus` is one condition of «ongoing», not all of it: a
    finished or continued operational topic is not open (`source_activity`)."""
    _reading(
        _topic(1, "Mari Näidis", stage="jõustunud"),
        _topic(2, "Jaan Näidis", continued=True),
        _topic(3, "Mari Näidis", operational=False),
    )
    names = sync_admin.owner_names()
    assert names.current == [] and names.problems == 0
    assert {row.name for row in names.other} == {"Mari Näidis", "Jaan Näidis"}


def test_a_name_only_a_responsible_change_mentions_is_no_current_owner(lawyers):
    _reading(
        _topic(
            1,
            "Mari Näidis",
            changes=[
                {
                    "vana_vastutaja": "Kadi Näidis",
                    "uus_vastutaja": "Mari Näidis",
                    "alates": "2026-10-01",
                },
                {
                    "vana_vastutaja": "Mari Näidis",
                    "uus_vastutaja": "Kadi Näidis",
                    "alates": "2026-09-01",
                },
            ],
        ),
    )
    names = sync_admin.owner_names()
    assert [row.name for row in names.current] == ["Mari Näidis"]
    kadi = _by_name(names.other)["Kadi Näidis"]
    assert (kadi.topics, kadi.open_topics, kadi.handed_over) == (0, 0, 1)
    assert names.problems == 0


def test_the_new_owner_of_a_handed_over_topic_is_the_current_one(lawyers):
    _reading(_topic(1, "Mari Näidis", new_owner="Jaan Näidis"))
    names = sync_admin.owner_names()
    assert [row.name for row in names.current] == ["Jaan Näidis"]
    assert "Mari Näidis" not in _by_name(names.rows)


def test_pinned_names_keep_their_place_by_open_work(lawyers):
    LovableOwnerAlias.objects.create(name="Mari", name_key="mari", user=lawyers["mari"])
    LovableOwnerAlias.objects.create(name="Tulevane", name_key="tulevane", user=lawyers["jaan"])
    _reading(_topic(1, "Mari"))
    names = sync_admin.owner_names()
    mari = _by_name(names.current)["Mari"]
    assert (mari.answer.via, mari.person_name) == (lovable_sync.VIA_ALIAS, "Mari Näidis")
    # A pin no topic carries yet (a colleague prepared before the hand-over) is kept.
    future = _by_name(names.other)["Tulevane"]
    assert future.ok and future.alias is not None and future.topics == 0


def test_the_newest_reading_decides_open_and_closed(lawyers):
    earlier = timezone.now() - dt.timedelta(days=1)
    _reading(_topic(1, "Uus Jurist"), at=earlier, key="a")
    assert sync_admin.owner_names().problems == 1
    # Lovable finished the topic: the next reading has it closed.
    _reading(_topic(1, "Uus Jurist", stage="jõustunud"), key="b")
    names = sync_admin.owner_names()
    assert names.problems == 0 and names.current == []
    assert _by_name(names.other)["Uus Jurist"].topics == 1
    # …and reopened: open again, a problem again.
    _reading(_topic(1, "Uus Jurist"), at=timezone.now() + dt.timedelta(minutes=1), key="c")
    assert sync_admin.owner_names().problems == 1


def test_no_reading_shows_nothing_and_counts_nothing():
    names = sync_admin.owner_names()
    assert (names.current, names.other, names.problems, names.read_at) == ([], [], 0, None)


@pytest.fixture
def signed(client, settings):
    apply_admin_gate(settings)
    user, secret = technical_admin()
    verified(client, user, secret)
    return client


def test_the_page_shows_open_owners_and_folds_the_rest(signed, lawyers):
    _reading(
        _topic(1, "Mari Näidis"),
        _topic(2, "Uus Jurist"),
        _topic(3, "Lahkunud Kolleeg", operational=False),
        _topic(4, "Teine Lahkunu", stage="jõustunud"),
    )
    body = signed.get(reverse("haldus:data_sync_names")).content.decode()
    current = body.index("Avatud teemade vastutajad")
    folded = body.index("Ajaloolised ja muud nimed (2)")
    assert current < body.index("Uus Jurist") < folded
    assert folded < body.index("Lahkunud Kolleeg")
    assert (
        '<details class="accordion dsync__history" id="ajaloolised-nimed">' in body
    )  # shut by default
    assert "1 nime avatud teemadel ei leia inimest" in body
    # The check and the pin forms are still there.
    assert "Kontrolli nime" in body and "Seo nimi inimesega" in body


def test_the_page_is_a_404_for_a_lawyer_and_the_department_head(client, settings, lawyers):
    from tests.admin_session import choose, pass_gate

    apply_admin_gate(settings)
    for persona in (lawyers["mari"], factories.DepartmentHeadFactory()):
        pass_gate(client)
        choose(client, persona)
        url = reverse("haldus:data_sync_names")
        assert client.get(url).status_code == 404
        assert (
            client.post(url, {"action": "pin", "name": "X", "person": str(persona.pk)}).status_code
            == 404
        )
    assert not LovableOwnerAlias.objects.exists()
