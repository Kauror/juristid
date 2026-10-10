"""Aggregates over an authorized queryset count records, never join rows.

``matter_visibility_q`` reaches RESTRICTED work through the ``collaborators``
many-to-many, so applying it emits a ``LEFT OUTER JOIN`` on the through table
and one Matter with three collaborators becomes three rows. ``apply`` answers
that with ``.distinct()``, which is enough for ``.count()`` and for the rows a
list renders — and is no help at all inside a ``values().annotate()``, where the
``COUNT`` runs before the outer ``DISTINCT`` is reached.

Every test here is written from the *specialist's* side on purpose. A
DEPARTMENT_HEAD gets ``Q()``, no join and therefore no fan-out, so the person
most likely to review a dashboard is the one person for whom a broken one looks
right. That asymmetry is why these assertions compare the two.
"""

from __future__ import annotations

import pytest

from app.core.enums import Visibility
from app.matters.forms import offered_policy_areas, organisations_by_usage
from app.matters.models import Matter
from tests import factories

pytestmark = pytest.mark.django_db


@pytest.fixture
def shared_world(db, specialist, other_specialist, department_head):
    """One ordinary Matter, and one the whole department collaborates on.

    Three collaborators is not a contrived number: a file that several lawyers
    are working is the normal shape of the work this product exists for.
    """
    stage = factories.StageFactory()
    ministry = factories.OrganisationFactory(name="Näidisministeerium")
    area = factories.PolicyAreaFactory()

    alone = factories.MatterFactory(
        title="Üksi menetletav eelnõu",
        owner=other_specialist,
        stage=stage,
        source_organisations=[ministry],
    )
    alone.policy_areas.add(area)

    shared = factories.MatterFactory(
        title="Ühiselt menetletav eelnõu",
        owner=other_specialist,
        stage=stage,
        source_organisations=[ministry],
    )
    shared.collaborators.add(specialist, other_specialist, department_head)
    shared.policy_areas.add(area)

    return {"alone": alone, "shared": shared, "stage": stage, "area": area}


# -- the pickers -----------------------------------------------------------


def test_policy_area_ordering_is_the_reviewed_order_and_not_usage(db, specialist):
    """The Valdkonnad list no longer reorders itself under the reader.

    Ordering by how often each area is used was a workaround for nine broad
    headings sorted by an admin field. With the twenty-three working labels the
    department itself sequenced, a stable order is learnable and a
    self-rearranging one is not — and an order derived from usage was also a
    channel through which the existence of restricted work could be inferred
    from a checkbox list (app/taxonomy/vocabulary.py, Teema redesign §7.1).
    """
    quiet = factories.PolicyAreaFactory(key="vaikne", name_et="Vaikne valdkond", sort_order=5)
    busy = factories.PolicyAreaFactory(key="kasutatud", name_et="Kasutatud", sort_order=900)
    for _ in range(3):
        factories.MatterFactory(owner=specialist).policy_areas.add(busy)

    ordered = [area.key for area in offered_policy_areas()]

    # Sort order wins, however much the other one is used.
    assert ordered.index(quiet.key) < ordered.index(busy.key)


def test_organisation_ordering_counts_matters_not_collaborator_rows(shared_world, specialist):
    other = factories.OrganisationFactory(name="Teine ministeerium")
    for _ in range(3):
        factories.MatterFactory(owner=specialist, source_organisations=[other])

    ordered = [organisation.name for organisation in organisations_by_usage(specialist)]

    assert ordered.index("Teine ministeerium") < ordered.index("Näidisministeerium")


# -- the rule itself -------------------------------------------------------


def test_the_scoped_counter_survives_a_collaborator_join(shared_world, specialist):
    """The property, stated once, independently of any page that relies on it.

    Written against the queryset rather than a dashboard so it keeps holding
    when somebody adds the next grouped statistic.
    """
    from app.core.authorization import scoped_count

    grouped = (
        Matter.objects.visible_to(specialist)
        .values("owner_id")
        .annotate(total=scoped_count())
        .order_by()
    )
    from_groups = sum(row["total"] for row in grouped)

    assert from_groups == Matter.objects.visible_to(specialist).count()


def test_a_restricted_matter_still_contributes_to_nothing(shared_world, other_specialist):
    """The fix must not have widened anything on its way past authorization."""
    hidden = factories.MatterFactory(
        title="Piiratud eelnõu",
        owner=other_specialist,
        visibility=Visibility.RESTRICTED,
        stage=shared_world["stage"],
    )
    outsider = factories.ReaderFactory()  # not a lawyer: docs/adr/0042

    from app.core.authorization import scoped_count

    rows = (
        Matter.objects.visible_to(outsider)
        .values("stage__label_et")
        .annotate(total=scoped_count())
        .order_by()
    )
    labels = {row["stage__label_et"]: row["total"] for row in rows}

    # Two, not three: the restricted row is absent from the count as well as
    # from the list, which is the property the whole dashboard rests on.
    assert labels.get(shared_world["stage"].label_et, 0) == 2
    assert Matter.objects.visible_to(outsider).filter(pk=hidden.pk).count() == 0
