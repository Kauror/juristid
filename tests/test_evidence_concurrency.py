"""The final-evidence invariant holds when two people write at the same time.

`tests/test_evidence_integrity.py` covers the serial case: one writer, one
check, one refusal. That is what DATA-001 closed. What it cannot close is two
writers, because the two operations that can falsify the invariant write two
different rows —

* binding final evidence writes `submissions_submission`;
* relaxing a Matter writes `matters_matter`;

— and before DATA-002 they had no row in common. Under READ COMMITTED each
could take a snapshot in which the other had not committed, pass its own check
against it, and commit. Both service checks and all four `BEFORE UPDATE`
triggers evaluate against exactly such a snapshot, so none of them could see the
other transaction. That is write skew, and no trigger closes it.

What closes it is the lock protocol in `app/matters/locks.py`: both sides
serialise on the Matter row, in one global order

    Matter → Submission → Document

and the side that waited re-reads everything it decided on. These tests force
the bad interleaving rather than hoping for it — a writer holds its transaction
open until `pg_stat_activity` shows the other backend actually blocked on a
lock, so the decision is taken from observed database state, never from elapsed
time.

See docs/adr/0040.
"""

from __future__ import annotations

import threading
import time

import pytest
from django.db import DatabaseError, connection, connections, transaction
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.documents.enums import DocumentRole
from app.documents.integrity import check_evidence
from app.documents.models import Document
from app.documents.services import add_evidence_version, create_document
from app.matters.locks import (
    EVIDENCE_INTEGRITY_LOCK_ORDER,
    lock_matter_for_evidence_integrity,
)
from app.matters.services import set_matter_visibility
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.submissions.services import (
    create_submission,
    mark_submission_sent,
    select_final_evidence,
)
from tests import factories

pytestmark = pytest.mark.django_db(transaction=True)

PDF = b"%PDF-1.4 synthetic final opinion"
MIME = "application/pdf"

#: Long enough that a loaded machine never trips it, short enough that a real
#: deadlock ends the test rather than the session. Nothing is *asserted* from
#: elapsed time; this is only the give-up bound.
TIMEOUT = 30


# ---------------------------------------------------------------------------
# Forcing the interleaving
# ---------------------------------------------------------------------------


class Runner:
    """A callable run on its own connection, with whatever it raised kept."""

    def __init__(self, target: object) -> None:
        self.error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, args=(target,))

    def _run(self, target: object) -> None:
        try:
            target()  # type: ignore[operator]
        except BaseException as error:  # surfaced by join(), not swallowed
            self.error = error
        finally:
            connections.close_all()

    def start(self) -> Runner:
        self._thread.start()
        return self

    def join(self) -> BaseException | None:
        self._thread.join(TIMEOUT)
        assert not self._thread.is_alive(), "a worker never finished; suspect a deadlock"
        return self.error


def wait_until_blocked(*, backends: int = 1) -> None:
    """Return once `backends` connections are waiting on a lock, from a third one.

    This is the barrier that makes these tests deterministic. The alternative —
    give the other thread a moment and hope — would pass whether or not the lock
    existed. Here the state waited for is the very thing under test: a backend
    in `wait_event_type = 'Lock'` is one a row lock stopped.
    """
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT count(*) FROM pg_stat_activity
                 WHERE datname = current_database()
                   AND wait_event_type = 'Lock'
                   AND pid <> pg_backend_pid()
                """
            )
            if cursor.fetchone()[0] >= backends:
                return
        # A polling interval, not a synchronisation primitive: the loop exits on
        # observed lock state, and every assertion below is about that state.
        time.sleep(0.02)
    raise AssertionError(f"no backend ever blocked on a lock; expected {backends}")


def _evidence(matter: object, actor: object, *, override: str = "", title: str = "Tõend"):
    document = create_document(
        matter=matter,
        title=title,
        role=DocumentRole.KODA_SUBMISSION_FINAL,
        created_by=actor,
        visibility_override=override,
    )
    version = add_evidence_version(
        document=document,
        content=PDF,
        original_filename="fail.pdf",
        mime_type=MIME,
        uploaded_by=actor,
    )
    return document, version


def _restricted_world(specialist: object):
    """A RESTRICTED Matter, a submission that insists on staying restricted, and
    evidence carrying no restriction of its own.

    Valid exactly as long as the Matter stays RESTRICTED, which is what makes it
    the shape both races are about.
    """
    matter = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    submission = create_submission(
        matter=matter,
        title="Tundlik arvamus",
        actor=specialist,
        visibility_override=Visibility.RESTRICTED,
    )
    document, version = _evidence(matter, specialist)
    assert document.visibility_override == ""
    return matter, submission, document, version


# ---------------------------------------------------------------------------
# A. The bind holds; the relaxation must wait for it and then refuse
# ---------------------------------------------------------------------------


def test_relaxing_a_matter_waits_for_an_in_flight_evidence_attachment(specialist):
    """The original DATA-002 reproduction, now the regression that guards it.

    On the DATA-001 branch this committed both transactions and left a
    RESTRICTED submission whose evidence read NORMAL. The refusal below is only
    reachable by a transaction that waited for the bind and then looked again —
    at the moment the relaxation first tried, the pointer it refuses over was
    not committed anywhere it could see.
    """
    matter, submission, _document, version = _restricted_world(specialist)

    bound = threading.Event()
    release = threading.Event()

    def bind_and_hold() -> None:
        with transaction.atomic():
            select_final_evidence(submission=submission, version=version, actor=specialist)
            bound.set()
            assert release.wait(TIMEOUT)

    def relax() -> None:
        assert bound.wait(TIMEOUT)
        set_matter_visibility(matter=matter, visibility=Visibility.NORMAL, actor=specialist)

    binder = Runner(bind_and_hold).start()
    relaxer = Runner(relax).start()

    wait_until_blocked()
    release.set()

    assert binder.join() is None
    error = relaxer.join()
    assert isinstance(error, DomainError), f"the relaxation was not refused: {error!r}"

    matter.refresh_from_db()
    stored = Submission.objects.get(pk=submission.pk)
    assert matter.visibility == Visibility.RESTRICTED
    assert stored.final_version_id == version.pk
    assert check_evidence(verify_sha=False).findings == []


# ---------------------------------------------------------------------------
# B. The relaxation holds; the bind must wait for it and then refuse
# ---------------------------------------------------------------------------


def test_attaching_evidence_waits_for_an_in_flight_matter_relaxation(specialist):
    """The inverse ordering, which is the half a Matter-side trigger cannot reach.

    Nothing relies on the Matter when it is relaxed, so the relaxation is
    legitimate and every check on that side passes. It is the *bind* that
    becomes illegal, and only because of a write it could not see when it
    started.
    """
    matter, submission, _document, version = _restricted_world(specialist)

    relaxed = threading.Event()
    release = threading.Event()

    def relax_and_hold() -> None:
        with transaction.atomic():
            set_matter_visibility(matter=matter, visibility=Visibility.NORMAL, actor=specialist)
            relaxed.set()
            assert release.wait(TIMEOUT)

    def bind() -> None:
        assert relaxed.wait(TIMEOUT)
        select_final_evidence(submission=submission, version=version, actor=specialist)

    relaxer = Runner(relax_and_hold).start()
    binder = Runner(bind).start()

    wait_until_blocked()
    release.set()

    assert relaxer.join() is None
    error = binder.join()
    assert isinstance(error, DomainError), f"the bind was not refused: {error!r}"

    matter.refresh_from_db()
    stored = Submission.objects.get(pk=submission.pk)
    assert matter.visibility == Visibility.NORMAL
    assert stored.final_version_id is None
    assert check_evidence(verify_sha=False).findings == []


def test_sending_holds_the_matter_lock_too(specialist):
    """Sending re-checks the evidence, so it is a third writer on the same rule.

    Once evidence is bound the relaxation is refused outright — which is the
    DATA-001 serial case — and the send itself still works.
    """
    matter, submission, _document, version = _restricted_world(specialist)
    select_final_evidence(submission=submission, version=version, actor=specialist)

    with pytest.raises(DomainError):
        set_matter_visibility(matter=matter, visibility=Visibility.NORMAL, actor=specialist)

    submission.refresh_from_db()
    mark_submission_sent(submission=submission, actor=specialist)
    submission.refresh_from_db()
    assert submission.final_version_id == version.pk
    assert check_evidence(verify_sha=False).findings == []


# ---------------------------------------------------------------------------
# C. Serialising must not invent refusals
# ---------------------------------------------------------------------------


def test_tightening_a_matter_and_a_valid_bind_both_succeed(specialist):
    """A lock that refused valid work would be a worse bug than the one it fixes.

    Tightening raises both sides of the comparison together, so it can never
    strand evidence. The two writers still take turns — and both commit.
    """
    matter = factories.MatterFactory(owner=specialist, visibility=Visibility.NORMAL)
    submission = create_submission(matter=matter, title="Tavaline arvamus", actor=specialist)
    _document, version = _evidence(matter, specialist)

    bound = threading.Event()
    release = threading.Event()

    def bind_and_hold() -> None:
        with transaction.atomic():
            select_final_evidence(submission=submission, version=version, actor=specialist)
            bound.set()
            assert release.wait(TIMEOUT)

    def tighten() -> None:
        assert bound.wait(TIMEOUT)
        set_matter_visibility(matter=matter, visibility=Visibility.RESTRICTED, actor=specialist)

    binder = Runner(bind_and_hold).start()
    tightener = Runner(tighten).start()

    wait_until_blocked()
    release.set()

    assert binder.join() is None
    assert tightener.join() is None

    matter.refresh_from_db()
    stored = Submission.objects.get(pk=submission.pk)
    assert matter.visibility == Visibility.RESTRICTED
    assert stored.final_version_id == version.pk
    assert check_evidence(verify_sha=False).findings == []


def test_equally_restricted_evidence_binds_under_a_concurrent_relaxation(specialist):
    """Evidence carrying its own RESTRICTED override survives the Matter moving.

    The waiter re-evaluates, finds the state still legal, and commits. The lock
    decides who goes first, not who is allowed.
    """
    matter = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    submission = create_submission(
        matter=matter,
        title="Tundlik arvamus",
        actor=specialist,
        visibility_override=Visibility.RESTRICTED,
    )
    _document, version = _evidence(matter, specialist, override=Visibility.RESTRICTED)

    relaxed = threading.Event()
    release = threading.Event()

    def relax_and_hold() -> None:
        with transaction.atomic():
            set_matter_visibility(matter=matter, visibility=Visibility.NORMAL, actor=specialist)
            relaxed.set()
            assert release.wait(TIMEOUT)

    def bind() -> None:
        assert relaxed.wait(TIMEOUT)
        select_final_evidence(submission=submission, version=version, actor=specialist)

    relaxer = Runner(relax_and_hold).start()
    binder = Runner(bind).start()

    wait_until_blocked()
    release.set()

    assert relaxer.join() is None
    assert binder.join() is None

    stored = Submission.objects.get(pk=submission.pk)
    assert stored.final_version_id == version.pk
    assert stored.effective_visibility == Visibility.RESTRICTED
    assert check_evidence(verify_sha=False).findings == []


# ---------------------------------------------------------------------------
# D. Lock order and lock strength
# ---------------------------------------------------------------------------


def test_the_documented_lock_order_is_matter_then_submission_then_document():
    assert EVIDENCE_INTEGRITY_LOCK_ORDER == (
        "matters_matter",
        "submissions_submission",
        "documents_document",
    )


def test_the_matter_lock_does_not_block_writers_that_only_reference_the_matter(specialist):
    """Lock *strength* is what keeps `Document → Matter` out of the graph.

    Every Document, Submission and audit event carries a `matter_id`, so writing
    one takes `FOR KEY SHARE` on the Matter. `FOR UPDATE` here would conflict
    with that, putting a Matter wait in the path of a transaction that already
    holds a Document lock — the exact cycle the ordering rule exists to prevent.
    `FOR NO KEY UPDATE` conflicts with the visibility `UPDATE` it must serialise
    against, and with nothing else.
    """
    matter = factories.MatterFactory(owner=specialist)
    held = threading.Event()
    release = threading.Event()
    created = threading.Event()

    def hold_the_matter_lock() -> None:
        with transaction.atomic():
            lock_matter_for_evidence_integrity(matter.pk)
            held.set()
            assert release.wait(TIMEOUT)

    def write_a_child_row() -> None:
        assert held.wait(TIMEOUT)
        create_document(matter=matter, title="Kõrvaline", created_by=specialist)
        created.set()

    holder = Runner(hold_the_matter_lock).start()
    writer = Runner(write_a_child_row).start()

    assert created.wait(TIMEOUT), "the Matter lock blocked an insert that only references it"
    release.set()
    assert holder.join() is None
    assert writer.join() is None


def test_binding_evidence_does_not_deadlock_against_a_search_rebuild(specialist):
    """The same strength rule, one level down, where it is a cycle not a wait.

    A targeted search refresh runs from `post_save` inside the binding
    transaction and asks for the rebuild gate's shared side. A rebuild holds
    that gate exclusively and inserts `SearchDocument` rows carrying
    `submission_id`, which takes `FOR KEY SHARE` on the Submission the binder is
    holding. Under `FOR UPDATE` those conflict and the two deadlock — the
    rebuild waiting for the row, the binder waiting for the gate.

    Forced from the gate side, because that is the only ordering in which the
    two requests overlap: the binder's row lock and its gate request are
    adjacent statements, so the rebuild has to already hold the gate.
    """
    from app.search.indexing import _hold_off_refreshes

    matter = factories.MatterFactory(owner=specialist)
    submission = create_submission(matter=matter, title="Arvamus", actor=specialist)
    _document, version = _evidence(matter, specialist)

    gate_held = threading.Event()
    reach_for_the_row = threading.Event()

    def hold_the_rebuild_gate() -> None:
        with transaction.atomic():
            _hold_off_refreshes()
            gate_held.set()
            assert reach_for_the_row.wait(TIMEOUT)
            with connection.cursor() as cursor:
                # Exactly what re-inserting this submission's projection row
                # takes on it.
                cursor.execute(
                    "SELECT id FROM submissions_submission WHERE id = %s FOR KEY SHARE",
                    [str(submission.pk)],
                )

    def bind() -> None:
        assert gate_held.wait(TIMEOUT)
        select_final_evidence(submission=submission, version=version, actor=specialist)

    rebuilder = Runner(hold_the_rebuild_gate).start()
    assert gate_held.wait(TIMEOUT)
    binder = Runner(bind).start()

    # Observed from this thread, not from either transaction: PostgreSQL caches
    # a statistics snapshot for the length of a transaction, so a holder polling
    # `pg_stat_activity` about its own waiter would read the same stale rows
    # until it committed.
    wait_until_blocked()
    reach_for_the_row.set()

    assert rebuilder.join() is None, "the rebuild lost a deadlock against the binder"
    assert binder.join() is None, "the binder lost a deadlock against the rebuild"
    assert Submission.objects.get(pk=submission.pk).final_version_id == version.pk


def test_sending_does_not_deadlock_against_a_search_rebuild(specialist, monkeypatch):
    """The same cycle as above, on the third writer of the rule, forced from the
    send side rather than the gate side.

    `mark_submission_sent` held the Submission `FOR UPDATE` until 2026-08-27,
    and that is the one mode of the four that conflicts with the `FOR KEY SHARE`
    a rebuild takes on the row while re-inserting its projection. The send's own
    `post_save` refresh then asked for the rebuild gate the rebuild was holding,
    which closed the cycle — and PostgreSQL resolved it by killing the send.
    A person pressing *Saada* got a database error because a rebuild happened to
    be running, which the automatic worker in SEARCH-001 would have made an
    ordinary occurrence rather than a rare one.

    Forced from the send side here, because that is the ordering the defect
    needs: the send has to be holding its row lock before the rebuild reaches
    for it. The pause is inside `check_evidence_is_usable`, which runs after
    both locks are taken and before the save that triggers the refresh.
    """
    from app.search.indexing import rebuild_all
    from app.submissions import services as submission_services

    matter = factories.MatterFactory(owner=specialist)
    submission = create_submission(matter=matter, title="Arvamus", actor=specialist)
    _document, version = _evidence(matter, specialist)
    select_final_evidence(submission=submission, version=version, actor=specialist)
    submission.refresh_from_db()

    holding = threading.Event()
    rebuilt = threading.Event()
    real_check = submission_services.check_evidence_is_usable

    def hold_the_row_then_check(**kwargs: object) -> None:
        holding.set()
        assert rebuilt.wait(TIMEOUT)
        return real_check(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(submission_services, "check_evidence_is_usable", hold_the_row_then_check)

    def send() -> None:
        mark_submission_sent(submission=submission, actor=specialist)

    def rebuild() -> None:
        assert holding.wait(TIMEOUT)
        rebuild_all()
        rebuilt.set()

    sender = Runner(send).start()
    rebuilder = Runner(rebuild).start()

    assert rebuilder.join() is None, "the rebuild lost a deadlock against the sender"
    assert sender.join() is None, "the sender lost a deadlock against the rebuild"

    stored = Submission.objects.get(pk=submission.pk)
    assert stored.status == SubmissionStatus.SENT
    assert stored.sent_at is not None


def test_two_sends_still_take_turns(specialist):
    """What the row lock is actually for, at the weaker strength.

    `FOR NO KEY UPDATE` conflicts with itself, so the check-and-write is still
    serialised: the second sender waits, re-reads the row it waited for, and
    finds the status its predecessor committed. One send, one timestamp, one
    audit event — the property the lock exists to hold, kept while the mode it
    holds it in stopped standing in the projection's way.
    """
    matter = factories.MatterFactory(owner=specialist)
    submission = create_submission(matter=matter, title="Arvamus", actor=specialist)
    _document, version = _evidence(matter, specialist)
    select_final_evidence(submission=submission, version=version, actor=specialist)

    start = threading.Barrier(2, timeout=TIMEOUT)
    outcomes: dict[str, BaseException | None] = {}

    def send(tag: str):
        def run() -> None:
            start.wait()
            try:
                mark_submission_sent(
                    submission=Submission.objects.get(pk=submission.pk), actor=specialist
                )
                outcomes[tag] = None
            except BaseException as error:  # recorded, not swallowed
                outcomes[tag] = error

        return run

    first = Runner(send("A")).start()
    second = Runner(send("B")).start()
    first.join()
    second.join()

    succeeded = [tag for tag, error in outcomes.items() if error is None]
    refused = [error for error in outcomes.values() if error is not None]
    assert len(succeeded) == 1, f"expected exactly one send to win, got {succeeded}"
    assert all(isinstance(error, DomainError) for error in refused), (
        f"the loser must get a refusal in words, not a database error: {refused}"
    )

    stored = Submission.objects.get(pk=submission.pk)
    assert stored.status == SubmissionStatus.SENT
    assert stored.sent_at is not None
    assert (
        ChangeEvent.objects.filter(
            event_type=ChangeEventType.SUBMISSION_SENT, object_id=str(submission.pk)
        ).count()
        == 1
    )


def _forced_round(specialist, *, bind_first: bool) -> BaseException | None:
    """One fully synchronised collision, and whatever the follower raised.

    The leader holds its transaction open until the follower is observably
    blocked, so which of the two got there first is decided by the test rather
    than by the scheduler.
    """
    matter, submission, _document, version = _restricted_world(specialist)
    first_holds = threading.Event()
    release = threading.Event()

    def bind() -> None:
        select_final_evidence(submission=submission, version=version, actor=specialist)

    def relax() -> None:
        set_matter_visibility(matter=matter, visibility=Visibility.NORMAL, actor=specialist)

    first, second = (bind, relax) if bind_first else (relax, bind)

    def hold_first() -> None:
        with transaction.atomic():
            first()
            first_holds.set()
            assert release.wait(TIMEOUT)

    def then_second() -> None:
        assert first_holds.wait(TIMEOUT)
        second()

    leader = Runner(hold_first).start()
    follower = Runner(then_second).start()
    wait_until_blocked()
    release.set()

    assert leader.join() is None
    return follower.join()


@pytest.mark.parametrize("bind_first", [True, False, True, False])
def test_opposite_orderings_of_the_two_writers_never_deadlock(specialist, bind_first):
    """Both hot paths, both ways round, with the interleaving forced each time.

    A lock-order cycle would show up here as `DeadlockDetected` rather than as a
    refusal. Bounded and fully synchronised: no round depends on timing, so this
    is a regression test rather than a stress test.
    """
    error = _forced_round(specialist, bind_first=bind_first)
    assert isinstance(error, DomainError), f"expected a refusal, got {error!r}"
    assert check_evidence(verify_sha=False).findings == []


# ---------------------------------------------------------------------------
# E. The transaction boundary the lock protocol depends on
# ---------------------------------------------------------------------------


def test_the_matter_lock_refuses_to_be_taken_outside_a_transaction(specialist):
    """A lock released before the check it protects is not a lock protocol."""
    from django.db.transaction import TransactionManagementError

    matter = factories.MatterFactory(owner=specialist)
    with pytest.raises(TransactionManagementError):
        lock_matter_for_evidence_integrity(matter.pk)


# ---------------------------------------------------------------------------
# F. Reparenting the evidence document
# ---------------------------------------------------------------------------


def test_a_reparent_cannot_move_relied_upon_final_evidence_out_of_its_matter(specialist):
    """`QuerySet.update` writes no Submission, so only the database can refuse it."""
    matter = factories.MatterFactory(owner=specialist)
    submission = create_submission(matter=matter, title="Arvamus", actor=specialist)
    document, version = _evidence(matter, specialist)
    select_final_evidence(submission=submission, version=version, actor=specialist)
    submission.refresh_from_db()
    mark_submission_sent(submission=submission, actor=specialist)

    elsewhere = factories.MatterFactory(owner=specialist)
    with pytest.raises(DatabaseError), transaction.atomic():
        Document.objects.filter(pk=document.pk).update(matter=elsewhere)

    document.refresh_from_db()
    assert document.matter_id == matter.pk
    assert check_evidence(verify_sha=False).findings == []


def test_a_document_no_submission_relies_on_can_still_be_reparented(specialist):
    """Refusal is scoped to relied-upon evidence, not to reparenting as such."""
    matter = factories.MatterFactory(owner=specialist)
    document, _version = _evidence(matter, specialist)

    elsewhere = factories.MatterFactory(owner=specialist)
    Document.objects.filter(pk=document.pk).update(matter=elsewhere)

    document.refresh_from_db()
    assert document.matter_id == elsewhere.pk


def test_a_document_that_is_not_the_final_evidence_can_still_be_reparented(specialist):
    """Two documents in one Matter; only the one relied upon is pinned."""
    matter = factories.MatterFactory(owner=specialist)
    submission = create_submission(matter=matter, title="Arvamus", actor=specialist)
    _relied_upon, version = _evidence(matter, specialist, title="Lõplik")
    select_final_evidence(submission=submission, version=version, actor=specialist)
    bystander, _unused = _evidence(matter, specialist, title="Taust")

    elsewhere = factories.MatterFactory(owner=specialist)
    Document.objects.filter(pk=bystander.pk).update(matter=elsewhere)

    bystander.refresh_from_db()
    assert bystander.matter_id == elsewhere.pk
    assert check_evidence(verify_sha=False).findings == []


def test_updating_a_relied_upon_document_within_its_own_matter_stays_legal(specialist):
    """The trigger watches `matter_id`, and only when it actually changes."""
    matter = factories.MatterFactory(owner=specialist)
    submission = create_submission(matter=matter, title="Arvamus", actor=specialist)
    document, version = _evidence(matter, specialist)
    select_final_evidence(submission=submission, version=version, actor=specialist)

    Document.objects.filter(pk=document.pk).update(title="Uus pealkiri")
    Document.objects.filter(pk=document.pk).update(matter=matter)

    document.refresh_from_db()
    assert document.title == "Uus pealkiri"
    assert document.matter_id == matter.pk


def test_a_reparent_waits_for_an_in_flight_bind_and_is_then_refused(specialist):
    """The same write skew, one level down, and closed the same way.

    When the bind started, nothing relied on the document, so the reparent's own
    trigger would have found nothing to refuse. It is refused because the bind
    holds the document's row, so the reparent waits and PostgreSQL re-fires the
    trigger against a database that now contains the pointer.
    """
    matter = factories.MatterFactory(owner=specialist)
    submission = create_submission(matter=matter, title="Arvamus", actor=specialist)
    document, version = _evidence(matter, specialist)
    elsewhere = factories.MatterFactory(owner=specialist)

    bound = threading.Event()
    release = threading.Event()

    def bind_and_hold() -> None:
        with transaction.atomic():
            select_final_evidence(submission=submission, version=version, actor=specialist)
            bound.set()
            assert release.wait(TIMEOUT)

    def reparent() -> None:
        assert bound.wait(TIMEOUT)
        with transaction.atomic():
            Document.objects.filter(pk=document.pk).update(matter=elsewhere)

    binder = Runner(bind_and_hold).start()
    mover = Runner(reparent).start()

    wait_until_blocked()
    release.set()

    assert binder.join() is None
    error = mover.join()
    assert isinstance(error, DatabaseError), f"the reparent was not refused: {error!r}"

    document.refresh_from_db()
    assert document.matter_id == matter.pk
    assert check_evidence(verify_sha=False).findings == []


# ---------------------------------------------------------------------------
# G. Deletion semantics are unchanged
# ---------------------------------------------------------------------------


def test_deleting_a_matter_holding_final_evidence_is_still_refused(specialist):
    """`Submission.matter` is CASCADE and stays that way (docs/adr/0040).

    What refuses the delete is `Document.matter` and `ChangeEvent.matter` being
    PROTECT. The reparent trigger added here does not touch deletion — it fires
    on `UPDATE OF matter_id` — so the incidental protection is exactly as strong
    as it was. This test exists so that a later change to either side has to
    notice.
    """
    from django.db.models import ProtectedError

    matter = factories.MatterFactory(owner=specialist)
    submission = create_submission(matter=matter, title="Arvamus", actor=specialist)
    _document, version = _evidence(matter, specialist)
    select_final_evidence(submission=submission, version=version, actor=specialist)
    submission.refresh_from_db()
    mark_submission_sent(submission=submission, actor=specialist)

    with pytest.raises(ProtectedError):
        matter.delete()


# ---------------------------------------------------------------------------
# H. Scope: what the new locks must not reach
# ---------------------------------------------------------------------------


def test_reading_a_matter_page_takes_no_row_locks(client, specialist):
    """Serialisation is for writers. A reader queued behind one would be a new
    performance problem in the name of an integrity fix."""
    matter = factories.MatterFactory(owner=specialist)
    submission = create_submission(matter=matter, title="Arvamus", actor=specialist)
    _document, version = _evidence(matter, specialist)
    select_final_evidence(submission=submission, version=version, actor=specialist)

    client.force_login(specialist)
    with CaptureQueriesContext(connection) as captured:
        response = client.get(reverse("matters:matter_position", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    locking = [query["sql"] for query in captured.captured_queries if "FOR UPDATE" in query["sql"]]
    locking += [
        query["sql"] for query in captured.captured_queries if "FOR NO KEY UPDATE" in query["sql"]
    ]
    assert locking == []


def test_writers_on_unrelated_matters_do_not_wait_for_each_other(specialist):
    """The lock is one row wide. Two people editing different Matters is the
    common case, and it must stay concurrent."""
    held = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    mine = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    theirs = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)

    def hold_mine() -> None:
        with transaction.atomic():
            lock_matter_for_evidence_integrity(mine.pk)
            held.set()
            assert release.wait(TIMEOUT)

    def relax_theirs() -> None:
        assert held.wait(TIMEOUT)
        set_matter_visibility(matter=theirs, visibility=Visibility.NORMAL, actor=specialist)
        finished.set()

    holder = Runner(hold_mine).start()
    other = Runner(relax_theirs).start()

    assert finished.wait(TIMEOUT), "an unrelated Matter's writer was blocked"
    release.set()
    assert holder.join() is None
    assert other.join() is None


def test_the_matter_lock_reads_exactly_one_row(specialist):
    """Bounded by primary key: no scan of all Matters, no table-level lock."""
    factories.MatterFactory(owner=specialist)
    matter = factories.MatterFactory(owner=specialist)
    factories.MatterFactory(owner=specialist)

    with transaction.atomic(), CaptureQueriesContext(connection) as captured:
        lock_matter_for_evidence_integrity(matter.pk)

    assert len(captured.captured_queries) == 1
    sql = captured.captured_queries[0]["sql"]
    assert "FOR NO KEY UPDATE" in sql
    assert '"id" = ' in sql


# ---------------------------------------------------------------------------
# I. The detector stays DATA-001's
# ---------------------------------------------------------------------------


def test_data_002_adds_no_second_integrity_detector():
    """DATA-001 owns `foreign-final-evidence` and `evidence-less-restricted`.

    A second detector would give an operator two answers to one question, and
    the runbook names this one.
    """
    from app.documents import integrity

    assert integrity.FOREIGN_FINAL_EVIDENCE == "foreign-final-evidence"
    assert integrity.EVIDENCE_LESS_RESTRICTED == "evidence-less-restricted"
    assert not [
        name for name in dir(integrity) if name.startswith("_check_") and "reparent" in name.lower()
    ]


def test_the_reparent_trigger_exists_and_watches_matter_id(db):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT pg_get_triggerdef(oid)
              FROM pg_trigger
             WHERE tgname = 'documents_relied_upon_evidence_stays_in_matter'
            """
        )
        row = cursor.fetchone()
    assert row is not None, "the DATA-002 trigger is missing"
    assert "UPDATE OF matter_id" in row[0]
