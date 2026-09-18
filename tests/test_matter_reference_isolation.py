"""The Matter factory and the real allocator hand out one sequence, not two.

The defect this module exists for
---------------------------------
``MatterFactory.reference_number`` was ``factory.Sequence(lambda n: n + 1)``
beside a fixed ``reference_year = 2026``. That is a **process-wide** counter: it
starts at 0 when pytest imports `tests.factories` and never resets, because
factory_boy knows nothing about test boundaries.

`MatterReferenceSequence` — what `allocate_matter_reference` draws from, and what
`create_matter` therefore draws from — is a **database row**, and the `django_db`
transaction rolls it back to empty before every test.

Two counters, disagreeing about what has been handed out, and disagreeing *as a
function of what ran earlier in the process*. In a test where the factory was the
first Matter-maker of the whole session, `factory.Sequence` yielded ``2026/1``
and the freshly-rolled-back allocator also said ``1``:

    $ pytest "tests/test_intake.py::test_the_seeded_factories_are_untouched_by_intake"
    django.db.utils.IntegrityError: duplicate key value violates unique
    constraint "matters_unique_human_reference"
    DETAIL:  Key (reference_year, reference_number)=(2026, 1) already exists.

Run the same file after any module that made a Matter and it passed, because the
process counter had moved on — which is why the full suite could stay green while
the subset failed. A suite whose result depends on its own execution order is not
a suite that proves anything, and the workaround was spreading: roughly fifteen
call sites had already been hand-pinned to ``reference_year=2099`` with a comment
explaining the collision.

What the fix is, and what it deliberately is not
------------------------------------------------
**It is test-only.** `allocate_matter_reference`, `reserve_matter_reference`,
`MatterReferenceSequence` and the `matters_unique_human_reference` constraint are
untouched. The production allocator remains authoritative, keeps its row lock and
keeps its semantics; §D below is the test that says so.

**It is not a reset, a sleep, a retry or a relaxed constraint.** Resetting
`factory.Sequence` per test would make *every* test start at ``2026/1`` and
collide everywhere; a retry would hide the disagreement rather than end it; and
the uniqueness constraint is the backstop that caught this, so weakening it would
throw away the only thing that worked.

The fix is to stop having two counters. The factory asks the allocator — the
authoritative one, which lives in the database and therefore resets with every
test. After it, the factory and the service cannot hand out the same number,
whatever ran before.

How the order dependence is proved here
---------------------------------------
§A holds two deliberately identical tests, each asserting that the first Matter
it makes is number 1. Both can pass **only** if the counter really does reset
between them; under `factory.Sequence` the second was guaranteed to fail. That is
the defect stated as an assertion, in-process and for a few milliseconds.

It is deliberately not a nested `pytest` subprocess. A child process needs a test
database of its own, so proving it that way costs a second full migration on
every CI shard that collects this file — twice — to establish something the pair
above already establishes. The commands the defect report named were run by hand
against this branch and are recorded here so the next reader does not have to
re-derive them:

    # before the fix                          # after
    pytest tests/test_intake.py -k seeded      FAILED (IntegrityError 2026/1)   PASSED
    pytest tests/test_intake.py                FAILED                            PASSED (27)
    pytest tests/test_matters.py tests/test_intake.py   PASSED (48)               PASSED (48)

The middle row is the one that matters: the same file, the same test, passing or
failing according to nothing but what pytest had already imported.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from app.matters.models import Matter, MatterReferenceSequence
from app.matters.services import allocate_matter_reference, create_matter
from tests import factories

pytestmark = pytest.mark.django_db


# -- §A. the factory and the service share one counter -----------------------


def test_a_factory_matter_and_a_created_matter_do_not_collide(specialist):
    """The failure, reduced to its two lines.

    This is the whole defect: a factory Matter and a service Matter in one test,
    which used to be an `IntegrityError` whenever this module ran early enough.
    """
    made = factories.MatterFactory(owner=specialist)
    created = create_matter(title="Teenuse kaudu", actor=specialist)

    assert (made.reference_year, made.reference_number) != (
        created.reference_year,
        created.reference_number,
    )
    assert Matter.objects.count() == 2


def test_the_factory_draws_from_the_authoritative_sequence(specialist):
    """Not «a number nobody else will use» — *the* next number."""
    made = factories.MatterFactory(owner=specialist, reference_year=2026)

    sequence = MatterReferenceSequence.objects.get(pk=2026)
    assert made.reference_number == sequence.last_number

    created = create_matter(title="Järgmine", actor=specialist)
    assert created.reference_number == made.reference_number + 1


def test_many_factory_matters_are_monotonic_and_unique(specialist):
    numbers = [factories.MatterFactory(owner=specialist).reference_number for _ in range(5)]

    assert numbers == sorted(numbers)
    assert len(set(numbers)) == 5


def test_the_sequence_starts_from_empty_in_every_test(specialist):
    """The property the process-wide counter could not have.

    Whatever ran before this test, the first Matter it makes is number 1 —
    because the counter it draws from is database state and the transaction
    rolled it back. That is what makes the suite order-independent, and it is
    exactly what `factory.Sequence` could not offer.
    """
    made = factories.MatterFactory(owner=specialist, reference_year=2026)

    assert made.reference_number == 1


def test_the_sequence_starts_from_empty_in_every_test_again(specialist):
    """Deliberately identical to the test above, and deliberately after it.

    Two tests asserting `== 1` can both pass only if the counter really does
    reset between them. Under `factory.Sequence` the second one was guaranteed to
    fail, which is the order dependence stated as an assertion rather than as
    prose.
    """
    made = factories.MatterFactory(owner=specialist, reference_year=2026)

    assert made.reference_number == 1


# -- §B. what the factory must still let a caller say ------------------------


def test_an_explicit_reference_number_is_left_exactly_as_given(specialist):
    made = factories.MatterFactory(owner=specialist, reference_year=2099, reference_number=901)

    assert (made.reference_year, made.reference_number) == (2099, 901)
    # And nothing was allocated for a year the caller pinned by hand.
    assert not MatterReferenceSequence.objects.filter(pk=2099).exists()


def test_an_explicit_none_still_refuses(specialist):
    """A year without a number is refused by the database, and still is.

    The reason the factory's default is a sentinel rather than `None`: this
    caller means «give the constraint a year and no number», and a default of
    `None` would make that request indistinguishable from «the factory decides».
    """
    with pytest.raises(IntegrityError), transaction.atomic():
        factories.MatterFactory(reference_year=2026, reference_number=None)


def test_an_archive_matter_gets_no_reference_at_all():
    """A historical register row has no `YYYY_N`, and must not be handed one."""
    archive = factories.ArchiveMatterFactory()

    assert archive.reference_year is None
    assert archive.reference_number is None
    assert not MatterReferenceSequence.objects.exists()


def test_building_a_matter_touches_no_sequence():
    """`build()` makes no database row, so it must draw no number either."""
    built = factories.MatterFactory.build()

    assert built.reference_number is None
    assert not MatterReferenceSequence.objects.exists()


# -- §B2. references this factory did not create -----------------------------


def test_a_factory_matter_does_not_collide_with_a_hand_written_reference(specialist):
    """The other shoe, and the one CI caught.

    The suite creates Matters by three routes and only one of them touches the
    counter. `tests/synthetic_statistics.build_world` writes `2026/1` … `2026/6`
    through `Matter.objects.create` — the model manager, not the service — so
    `MatterReferenceSequence` has never heard of those rows.

    Allocating alone would then hand the next factory Matter `1` and collide with
    a row that is already there: the original defect wearing the other shoe, and
    a fix that only looked at the counter would have swapped one order-dependent
    `IntegrityError` for another.
    """
    Matter.objects.create(
        title="Käsitsi kirjutatud viide",
        reference_year=2026,
        reference_number=1,
        owner=specialist,
    )

    made = factories.MatterFactory(owner=specialist, reference_year=2026)

    assert made.reference_number == 2


def test_the_factory_clears_a_whole_block_of_hand_written_references(specialist):
    """A world holding `2026/1`…`2026/6` — `build_world`'s actual shape."""
    for number in range(1, 7):
        Matter.objects.create(
            title=f"Maailma teema {number}",
            reference_year=2026,
            reference_number=number,
            owner=specialist,
        )

    made = factories.MatterFactory(owner=specialist, reference_year=2026)
    created = create_matter(title="Ja teenuse kaudu", actor=specialist)

    assert made.reference_number == 7
    # And the service, asked next, carries on from there rather than repeating.
    assert created.reference_year == timezone.localdate().year
    assert Matter.objects.filter(reference_year=2026, reference_number=7).count() == 1


def test_a_hand_written_reference_above_the_counter_is_not_handed_out_again(specialist):
    """The high-water mark is read from the rows, not from what was allocated."""
    factories.MatterFactory(owner=specialist, reference_year=2026)
    Matter.objects.create(
        title="Kaugel ees",
        reference_year=2026,
        reference_number=500,
        owner=specialist,
    )

    later = factories.MatterFactory(owner=specialist, reference_year=2026)

    assert later.reference_number == 501


# -- §C. the factory does not reach into another year ------------------------


def test_each_year_has_its_own_counter(specialist):
    first = factories.MatterFactory(owner=specialist, reference_year=2026)
    other = factories.MatterFactory(owner=specialist, reference_year=2027)

    assert first.reference_number == 1
    assert other.reference_number == 1
    assert MatterReferenceSequence.objects.get(pk=2026).last_number == 1
    assert MatterReferenceSequence.objects.get(pk=2027).last_number == 1


# -- §D. production semantics are unchanged ----------------------------------


def test_the_allocator_itself_behaves_exactly_as_before(specialist):
    """The service this fix leans on is the service it leaves alone.

    Monotonic, per-year, and the number it returns is the number it stored.
    Nothing here is about the factory; it is the guard that says the test-only
    fix stayed test-only.
    """
    assert allocate_matter_reference(2031) == (2031, 1)
    assert allocate_matter_reference(2031) == (2031, 2)
    assert allocate_matter_reference(2032) == (2032, 1)
    assert MatterReferenceSequence.objects.get(pk=2031).last_number == 2


def test_the_uniqueness_constraint_is_still_the_backstop(specialist):
    """Weakening it was never an option: it is what caught this in the first place."""
    factories.MatterFactory(owner=specialist, reference_year=2098, reference_number=1)

    with pytest.raises(IntegrityError), transaction.atomic():
        factories.MatterFactory(owner=specialist, reference_year=2098, reference_number=1)
