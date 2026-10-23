"""The writer role's write allow-list is what the canonical services actually write.

`app.mcp_writes.db_role.WRITE_ALLOWLIST` is not a guess. Every canonical
service an approved MCP operation calls (CONTRACT §4: `create_matter`,
`set_matter_title`, `set_brief_summary`, `assign_matter`, `add_matter_other`,
`add_matter_meeting`, `record_procedural_link`, `set_next_action`,
`change_current_action`, `complete_current_action`, `request_response_deadline`,
`change_response_deadline`, `add_important_date`) is run here twice:

1. **Under the writer role itself.** The test transaction creates the role with
   `role_sql` (roles are transactional in PostgreSQL, so it is rolled back with
   everything else and never exists outside this test), `SET ROLE`s to it, and
   runs the service — `on_commit` callbacks included, which is where the
   search-freshness and notice work lands. PostgreSQL refuses any INSERT,
   UPDATE, DELETE or row lock the allow-list does not grant, so a service that
   writes a table nobody listed fails here, with the table's name.
2. **Under a statement recorder**, which notes every table each service
   inserts into, updates, deletes from or locks. The union must be the
   allow-list (minus the gateway's own tables, which the services never touch),
   so an entry no service needs is noticed and removed rather than kept "just
   in case".

Synthetic data only, one database transaction per test, no committed role.
"""

from __future__ import annotations

import datetime as dt
import re
import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from django.db import connection
from django.utils import timezone

from app.mcp_writes import db_role
from tests import factories

pytestmark = pytest.mark.django_db

_WRITE = re.compile(r'^\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+"?([a-z0-9_]+)"?', re.IGNORECASE)
_VERB = re.compile(r"^\s*(INSERT|UPDATE|DELETE)", re.IGNORECASE)
_LOCKED_FROM = re.compile(r'\bFROM\s+"?([a-z0-9_]+)"?', re.IGNORECASE)
_LOCK = re.compile(r"\bFOR\s+(?:NO\s+KEY\s+)?UPDATE\b", re.IGNORECASE)


class Recorder:
    """Every table a block of code writes or row-locks, by privilege."""

    def __init__(self) -> None:
        self.seen: dict[str, set[str]] = {}

    def __call__(self, execute: Callable[..., Any], sql: str, params: Any, many: bool, ctx: Any):
        text = str(sql)
        found = _WRITE.match(text)
        if found:
            verb = _VERB.match(text)
            assert verb is not None
            self.seen.setdefault(found.group(1), set()).add(verb.group(1).upper())
        elif _LOCK.search(text):
            for table in _LOCKED_FROM.findall(text.split(" FOR ")[0]):
                # A row lock needs UPDATE on the table (PostgreSQL's rule for
                # FOR UPDATE / FOR NO KEY UPDATE).
                self.seen.setdefault(table, set()).add("UPDATE")
        return execute(sql, params, many, ctx)


@contextmanager
def as_writer_role(allowlist: dict[str, tuple[str, ...]] | None = None) -> Iterator[str]:
    """The writer role, created inside this test's transaction, as the session's role."""
    role = f"juristid_mcp_writer_t{secrets.token_hex(4)}"
    owner = connection.settings_dict["USER"]
    database = connection.settings_dict["NAME"]
    with connection.cursor() as cursor:
        statements = db_role.role_sql(
            role=role,
            owner=owner,
            database=database,
            allowlist=db_role.existing_allowlist(cursor, allowlist),
            withheld=db_role.withheld_existing(cursor),
        )
        for statement in statements:
            cursor.execute(statement)
        cursor.execute(f'SET ROLE "{role}"')
    try:
        yield role
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE")


def _day(offset: int) -> dt.date:
    return timezone.localdate() + dt.timedelta(days=offset)


# -- the scenarios: each sets up as the owner and returns what to run ------------
#
# The setup runs before the role is assumed (it is the world the operation finds),
# the returned callable is the operation's canonical call(s).


def _create_matter() -> Callable[[], Any]:
    from app.matters.services import create_matter

    actor = factories.UserFactory()
    colleague = factories.UserFactory()
    return lambda: create_matter(
        title="Tulumaksuseaduse muutmise eelnõu", actor=actor, owner=colleague
    )


def _create_matter_first_of_its_year() -> Callable[[], Any]:
    """The first Matter of a year creates that year's reference counter."""
    from app.matters.services import create_matter

    actor = factories.UserFactory()
    return lambda: create_matter(title="Aasta esimene teema", actor=actor, reference_year=2090)


def _create_matter_with_summary() -> Callable[[], Any]:
    from app.matters.services import create_matter, set_brief_summary

    actor = factories.UserFactory()

    def run() -> Any:
        matter = create_matter(title="Uus teema", actor=actor, owner=actor)
        return set_brief_summary(matter=matter, value="Lühikokkuvõte.", actor=actor)

    return run


def _update_matter() -> Callable[[], Any]:
    from app.matters.services import set_brief_summary, set_matter_title

    actor = factories.UserFactory()
    matter = factories.MatterFactory(owner=actor)

    def run() -> Any:
        set_matter_title(matter=matter, value="Uus pealkiri", actor=actor)
        return set_brief_summary(matter=matter, value="Uus kokkuvõte.", actor=actor)

    return run


def _assign_matter() -> Callable[[], Any]:
    from app.matters.services import assign_matter

    actor = factories.DepartmentHeadFactory()
    first = factories.UserFactory()
    second = factories.UserFactory()
    matter = factories.MatterFactory(owner=first)

    def run() -> Any:
        assign_matter(matter=matter, owner=second, actor=actor)
        return assign_matter(matter=matter, owner=None, actor=actor)

    return run


def _add_matter_note() -> Callable[[], Any]:
    from app.matters.workspace import add_matter_other

    actor = factories.UserFactory()
    matter = factories.MatterFactory(owner=actor)
    return lambda: add_matter_other(
        matter=matter,
        author=actor,
        description="Ministeeriumi esindaja helistas.",
        occurred_on=_day(-1),
        url="https://www.riigikogu.ee/tegevus/eelnoud/",
        once_token=secrets.token_hex(16),
    )


def _record_meeting() -> Callable[[], Any]:
    from app.matters.workspace import add_matter_meeting

    actor = factories.UserFactory()
    matter = factories.MatterFactory(owner=actor)
    return lambda: add_matter_meeting(
        matter=matter,
        author=actor,
        summary="Kohtumine ministeeriumiga.",
        occurred_on=_day(0),
        once_token=secrets.token_hex(16),
    )


def _add_procedural_link() -> Callable[[], Any]:
    from app.matters.enums import ProceduralLinkKind
    from app.matters.services import record_procedural_link

    actor = factories.UserFactory()
    matter = factories.MatterFactory(owner=actor)
    return lambda: record_procedural_link(
        matter=matter,
        kind=ProceduralLinkKind.EIS,
        url="https://eelnoud.valitsus.ee/main/mount/docList/8f2c1a30-0000-0000-0000-00000000a001",
        label="EIS toimik",
        actor=actor,
    )


def _set_next_action() -> Callable[[], Any]:
    from app.workflow.enums import ActionKind, DateSemantics
    from app.workflow.services import set_next_action

    actor = factories.UserFactory()
    matter = factories.MatterFactory(owner=actor, stage=None)

    def run() -> Any:
        set_next_action(
            matter=matter,
            text="Koosta arvamus",
            kind=ActionKind.DO,
            date_semantics=DateSemantics.DEADLINE,
            target_date=_day(5),
            actor=actor,
        )
        return set_next_action(
            matter=matter,
            text="Ootame ministeeriumi vastust",
            kind=ActionKind.WAIT,
            date_semantics=DateSemantics.REVIEW_ON,
            target_date=_day(7),
            actor=actor,
        )

    return run


def _change_current_action() -> Callable[[], Any]:
    from app.matters.workspace import change_current_action
    from app.workflow.services import set_next_action_for_new_work

    actor = factories.UserFactory()
    matter = factories.MatterFactory(owner=actor, stage=None)
    action = set_next_action_for_new_work(matter=matter, text="Loe läbi", actor=actor)
    return lambda: change_current_action(
        matter=matter,
        actor=actor,
        action_id=action.pk,
        text="Loe läbi ja kommenteeri",
        target_date=_day(7),
    )


def _complete_current_action() -> Callable[[], Any]:
    from app.matters.workspace import complete_current_action
    from app.workflow.services import set_next_action_for_new_work

    actor = factories.UserFactory()
    matter = factories.MatterFactory(owner=actor, stage=None)
    action = set_next_action_for_new_work(matter=matter, text="Loe läbi", actor=actor)
    return lambda: complete_current_action(
        matter=matter,
        author=actor,
        action_id=action.pk,
        body="<p>Lugesin läbi.</p>",
        next_text="Saada kommentaarid",
        next_date=_day(3),
    )


def _response_deadline() -> Callable[[], Any]:
    from app.matters.enums import ResponseDeadlineChange
    from app.matters.response_deadlines import (
        change_response_deadline,
        deadline_revision,
        request_response_deadline,
    )

    actor = factories.UserFactory()
    matter = factories.MatterFactory(owner=actor)

    def run() -> Any:
        request_response_deadline(
            matter=matter,
            deadline=_day(10),
            actor=actor,
            expected_revision=deadline_revision(matter),
        )
        matter.refresh_from_db()
        return change_response_deadline(
            matter=matter,
            deadline=_day(14),
            actor=actor,
            change=ResponseDeadlineChange.MOVED,
            expected_revision=deadline_revision(matter),
        )

    return run


def _response_deadline_replaced() -> Callable[[], Any]:
    from app.matters.enums import ResponseDeadlineChange, ResponseDeadlineOutcome
    from app.matters.response_deadlines import (
        change_response_deadline,
        deadline_revision,
        request_response_deadline,
    )

    actor = factories.UserFactory()
    matter = factories.MatterFactory(owner=actor)
    request_response_deadline(
        matter=matter, deadline=_day(10), actor=actor, expected_revision=deadline_revision(matter)
    )
    matter.refresh_from_db()
    return lambda: change_response_deadline(
        matter=matter,
        deadline=_day(14),
        actor=actor,
        change=ResponseDeadlineChange.REPLACED,
        previous_outcome=ResponseDeadlineOutcome.SUPERSEDED,
        expected_revision=deadline_revision(matter),
    )


def _add_important_date() -> Callable[[], Any]:
    from app.intelligence.services import add_important_date
    from app.workflow.enums import DatePrecision

    actor = factories.UserFactory()
    matter = factories.MatterFactory(owner=actor)
    return lambda: add_important_date(
        matter=matter,
        actor=actor,
        title="Jõustumine",
        date_value=_day(30),
        period_end=_day(30),
        date_precision=DatePrecision.EXACT,
    )


SCENARIOS: dict[str, Callable[[], Callable[[], Any]]] = {
    "create_matter": _create_matter,
    "create_matter_first_of_its_year": _create_matter_first_of_its_year,
    "create_matter_with_summary": _create_matter_with_summary,
    "update_matter": _update_matter,
    "assign_matter": _assign_matter,
    "add_matter_note": _add_matter_note,
    "record_meeting": _record_meeting,
    "add_procedural_link": _add_procedural_link,
    "set_next_action": _set_next_action,
    "change_current_action": _change_current_action,
    "complete_action": _complete_current_action,
    "response_deadline": _response_deadline,
    "response_deadline_replaced": _response_deadline_replaced,
    "add_important_date": _add_important_date,
}


def _record(scenario: str, django_capture_on_commit_callbacks: Any) -> dict[str, set[str]]:
    run = SCENARIOS[scenario]()
    recorder = Recorder()
    with connection.execute_wrapper(recorder):
        with django_capture_on_commit_callbacks(execute=True):
            run()
    return recorder.seen


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_the_operation_runs_under_the_writer_role(scenario, django_capture_on_commit_callbacks):
    """PostgreSQL itself is the judge: a write the allow-list does not grant is refused."""
    run = SCENARIOS[scenario]()
    with as_writer_role(), django_capture_on_commit_callbacks(execute=True):
        run()


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_every_table_the_operation_writes_is_allow_listed_for_that_privilege(
    scenario, django_capture_on_commit_callbacks
):
    seen = _record(scenario, django_capture_on_commit_callbacks)
    assert seen, "the recorder saw no write at all, so it proves nothing"
    for table, verbs in seen.items():
        granted = set(db_role.WRITE_ALLOWLIST.get(table, ()))
        assert verbs <= granted, f"{scenario} needs {sorted(verbs - granted)} on {table}"


def test_the_allowlist_holds_nothing_the_operations_do_not_need(
    django_capture_on_commit_callbacks,
):
    """Least privilege in the other direction: every grant is used by some operation."""
    used: dict[str, set[str]] = {}
    for scenario in SCENARIOS:
        for table, verbs in _record(scenario, django_capture_on_commit_callbacks).items():
            used.setdefault(table, set()).update(verbs)
    for table, verbs in {**db_role.GATEWAY_WRITES, **PROVEN_BY_THE_OPERATION_SUITES}.items():
        used.setdefault(table, set()).update(verbs)
    for table, granted in db_role.WRITE_ALLOWLIST.items():
        unused = set(granted) - used.get(table, set())
        assert not unused, f"{table}: {sorted(unused)} granted but no operation uses it"


#: Grants whose proof is the operations' own suites rather than a scenario
#: here: the plan the operation seeds beside `create_matter`, a plan-derived
#: step and an opinion follow-up check completed by `complete_action`. Agent B's
#: tests record them (`WRITE_TABLES`) and
#: tests/test_mcp_writer_role_runs_operations.py runs those tests as the role.
PROVEN_BY_THE_OPERATION_SUITES: dict[str, tuple[str, ...]] = {
    "workflow_matterplanstep": ("INSERT", "UPDATE"),
    "workflow_opinionfollowup": ("UPDATE",),
}


def test_the_grants_proven_elsewhere_are_recorded_by_the_operation_suites():
    support = pytest.importorskip(
        "tests.test_mcp_write_operations_support",
        reason="INTEGRATION: agent B's operation suites are not on this branch",
    )
    recorded = set().union(*support.WRITE_TABLES.values())
    assert set(PROVEN_BY_THE_OPERATION_SUITES) <= recorded
    granted = set(db_role.WRITE_ALLOWLIST) | {"audit_changeevent"}
    assert recorded <= granted, sorted(recorded - granted)


def test_the_role_run_bites_when_a_grant_is_missing(django_capture_on_commit_callbacks):
    """The control: without `matters_entry`, a note is refused by PostgreSQL itself."""
    from django.db.utils import ProgrammingError

    narrowed = {k: v for k, v in db_role.WRITE_ALLOWLIST.items() if k != "matters_entry"}
    run = SCENARIOS["add_matter_note"]()
    with pytest.raises(ProgrammingError, match="permission denied for table matters_entry"):
        with as_writer_role(narrowed), django_capture_on_commit_callbacks(execute=True):
            run()
