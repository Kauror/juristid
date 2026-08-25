"""A Matter may have several senders, and every surface has to mean it.

``KELLELT`` became 0..N in Wave 2. ``KELLELE`` did not: the register's single
counterparty column meant the sender until 2019 and the addressee from 2020, and
the whole import rests on those staying two different facts. So the tests here
come in pairs — one proving the sender side is genuinely plural, one proving the
addressee side did not move with it.

The failure this file exists to catch is the quiet one. A plural control over a
singular store, or a plural store read through ``.first()``, produces a screen
that looks right and a search that silently cannot find half the register.
"""

from __future__ import annotations

import re
import uuid

import pytest
from django.db.models import ProtectedError
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.matters.forms import organisations_by_usage
from app.matters.models import Matter, MatterSourceOrganisation
from app.matters.services import (
    _UNSET,
    create_matter,
    refresh_matter_from_register,
    set_organisations,
)
from tests import factories

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")
REGISTER = reverse("matters:matter_list")


def titles_on(response) -> list[str]:
    """The rows the register actually returned.

    Counting a title in the rendered HTML would be a different assertion: the
    register prints a title in both the row and a `title=` attribute, so a
    substring count is two before any join has duplicated anything.
    """
    return [matter.title for matter in response.context["page"].object_list]


def senders_of(matter: Matter) -> set[str]:
    matter.refresh_from_db()
    return {organisation.name for organisation in matter.source_organisations.all()}


def organisation_events(matter: Matter):
    return ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.MATTER_ORGANISATION_CHANGED
    ).order_by("created_at")


def _metric(viewer, key):
    """One metric over the whole period, for this viewer."""
    import datetime

    from django.utils import timezone

    from app.reporting.context import ReportingContext, parse_period
    from app.reporting.services import compute

    today = datetime.date(2026, 6, 1)
    context = ReportingContext(
        viewer=viewer,
        period=parse_period("koik", today),
        today=today,
        now=timezone.now(),
    )
    return compute(key, context)


# -- creation ---------------------------------------------------------------


def test_a_matter_is_created_with_two_senders(specialist):
    """The service writes the relation after the Matter has a primary key."""
    first = factories.OrganisationFactory(name="Aamet")
    second = factories.OrganisationFactory(name="Bliit")

    matter = create_matter(
        title="Kahe saatjaga teema", actor=specialist, source_organisations=[first, second]
    )

    assert senders_of(matter) == {"Aamet", "Bliit"}
    assert MatterSourceOrganisation.objects.filter(matter=matter).count() == 2
    assert (
        ChangeEvent.objects.filter(matter=matter, event_type=ChangeEventType.MATTER_CREATED).count()
        == 1
    )


def test_creation_collapses_a_repeated_sender(specialist):
    organisation = factories.OrganisationFactory()
    matter = create_matter(
        title="Kaks korda sama",
        actor=specialist,
        source_organisations=[organisation, organisation],
    )
    assert MatterSourceOrganisation.objects.filter(matter=matter).count() == 1


def test_creation_without_senders_leaves_the_set_empty(specialist):
    matter = create_matter(title="Saatjata", actor=specialist)
    assert senders_of(matter) == set()


# -- the update service -----------------------------------------------------


def test_setting_a_reordered_equal_set_writes_nothing(normal_matter, specialist):
    """Ordering is not a business fact, so re-submitting the same set is a no-op.

    Pinned because the obvious implementation — clear the relation and re-add —
    would pass every "the senders are right" assertion while touching the rows,
    bumping ``updated_at`` and filing an audit event saying somebody made a
    change they did not make (Agent-E brief 21, 54).
    """
    first = factories.OrganisationFactory(name="Aamet")
    second = factories.OrganisationFactory(name="Bliit")
    set_organisations(matter=normal_matter, source_organisations=[first, second], actor=specialist)
    normal_matter.refresh_from_db()

    before_updated = normal_matter.updated_at
    before_events = organisation_events(normal_matter).count()
    before_rows = set(
        MatterSourceOrganisation.objects.filter(matter=normal_matter).values_list("pk", flat=True)
    )

    set_organisations(
        matter=normal_matter, source_organisations=[second, first, first], actor=specialist
    )

    normal_matter.refresh_from_db()
    assert normal_matter.updated_at == before_updated
    assert organisation_events(normal_matter).count() == before_events
    assert (
        set(
            MatterSourceOrganisation.objects.filter(matter=normal_matter).values_list(
                "pk", flat=True
            )
        )
        == before_rows
    )


def test_a_real_sender_change_moves_updated_at(normal_matter, specialist):
    """``.set()`` writes the join table and nothing else (brief 22)."""
    first = factories.OrganisationFactory()
    second = factories.OrganisationFactory()
    set_organisations(matter=normal_matter, source_organisations=[first], actor=specialist)
    normal_matter.refresh_from_db()
    before = normal_matter.updated_at

    set_organisations(matter=normal_matter, source_organisations=[first, second], actor=specialist)

    normal_matter.refresh_from_db()
    assert normal_matter.updated_at > before


def test_clearing_the_senders_is_one_change(normal_matter, specialist):
    first = factories.OrganisationFactory(name="Aamet")
    second = factories.OrganisationFactory(name="Bliit")
    set_organisations(matter=normal_matter, source_organisations=[first, second], actor=specialist)
    normal_matter.refresh_from_db()
    before_updated = normal_matter.updated_at
    before_events = organisation_events(normal_matter).count()

    set_organisations(matter=normal_matter, source_organisations=[], actor=specialist)

    normal_matter.refresh_from_db()
    assert senders_of(normal_matter) == set()
    assert organisation_events(normal_matter).count() == before_events + 1
    assert normal_matter.updated_at > before_updated


def test_unset_leaves_the_senders_alone(normal_matter, specialist):
    """``_UNSET`` and ``[]`` are different instructions and must stay different."""
    organisation = factories.OrganisationFactory(name="Aamet")
    set_organisations(matter=normal_matter, source_organisations=[organisation], actor=specialist)

    set_organisations(
        matter=normal_matter,
        addressee_organisation=factories.OrganisationFactory(),
        actor=specialist,
    )

    assert senders_of(normal_matter) == {"Aamet"}


def test_the_audit_event_carries_both_sets_in_full(normal_matter, specialist):
    """Never "from the first sender": the payload has to be the whole set."""
    start = factories.OrganisationFactory(name="Aamet")
    landing_a = factories.OrganisationFactory(name="Bliit")
    landing_b = factories.OrganisationFactory(name="Camet")
    set_organisations(matter=normal_matter, source_organisations=[start], actor=specialist)

    set_organisations(
        matter=normal_matter, source_organisations=[landing_b, landing_a], actor=specialist
    )

    event = organisation_events(normal_matter).last()
    assert event.payload["source_from"]["names"] == ["Aamet"]
    # Sorted by name, so the same set always writes the same payload however the
    # caller happened to order it.
    assert event.payload["source_to"]["names"] == ["Bliit", "Camet"]
    assert event.payload["source_to"]["ids"] == [str(landing_a.pk), str(landing_b.pk)]
    assert "addressee_from" not in event.payload


def test_changing_both_directions_raises_one_event(normal_matter, specialist):
    """One coherent organisation history, not two competing ones (brief 24)."""
    sender = factories.OrganisationFactory(name="Aamet")
    addressee = factories.OrganisationFactory(name="Bliit")

    set_organisations(
        matter=normal_matter,
        source_organisations=[sender],
        addressee_organisation=addressee,
        actor=specialist,
    )

    events = organisation_events(normal_matter)
    assert events.count() == 1
    payload = events.get().payload
    assert payload["source_to"]["names"] == ["Aamet"]
    assert payload["addressee_to"] == "Bliit"
    normal_matter.refresh_from_db()
    assert normal_matter.addressee_organisation == addressee


def test_the_addressee_is_still_one_organisation(normal_matter, specialist):
    """The asymmetry is deliberate and this is where it is pinned."""
    first = factories.OrganisationFactory()
    second = factories.OrganisationFactory()
    set_organisations(matter=normal_matter, addressee_organisation=first, actor=specialist)
    set_organisations(matter=normal_matter, addressee_organisation=second, actor=specialist)

    normal_matter.refresh_from_db()
    assert normal_matter.addressee_organisation == second
    assert not hasattr(Matter, "addressee_organisations")


# -- the register refresh ---------------------------------------------------


def _imported(specialist, **kwargs):
    from app.matters.enums import MatterOrigin, RecordMode

    return factories.MatterFactory(
        owner=specialist,
        origin=MatterOrigin.LEGACY_IMPORT,
        record_mode=RecordMode.FULL,
        **kwargs,
    )


def test_the_register_replaces_the_sender_set_rather_than_adding_to_it(specialist):
    """The authority rule the singular field had, kept (brief 27, 61)."""
    old = factories.OrganisationFactory(name="Aamet")
    fresh = factories.OrganisationFactory(name="Bliit")
    matter = _imported(specialist, source_organisations=[old])

    refresh_matter_from_register(matter=matter, source_organisations=[fresh], actor=specialist)

    assert senders_of(matter) == {"Bliit"}


def test_the_register_refresh_distinguishes_unset_from_empty(specialist):
    organisation = factories.OrganisationFactory(name="Aamet")
    matter = _imported(specialist, source_organisations=[organisation])

    refresh_matter_from_register(
        matter=matter, source_organisations=_UNSET, received_date=None, actor=specialist
    )
    assert senders_of(matter) == {"Aamet"}

    refresh_matter_from_register(matter=matter, source_organisations=[], actor=specialist)
    assert senders_of(matter) == set()


def test_the_register_refresh_files_one_refresh_event(specialist):
    """Not a second organisation-change event beside it (brief 26)."""
    fresh = factories.OrganisationFactory(name="Bliit")
    matter = _imported(specialist)

    _, changed = refresh_matter_from_register(
        matter=matter, source_organisations=[fresh], actor=specialist
    )

    assert changed["source_organisations"] == {"from": [], "to": [str(fresh.pk)]}
    assert organisation_events(matter).count() == 0
    assert (
        ChangeEvent.objects.filter(
            matter=matter, event_type=ChangeEventType.MATTER_SOURCE_FIELDS_REFRESHED
        ).count()
        == 1
    )


def test_an_unchanged_sender_set_makes_the_refresh_a_no_op(specialist):
    organisation = factories.OrganisationFactory()
    matter = _imported(specialist, source_organisations=[organisation])

    _, changed = refresh_matter_from_register(
        matter=matter, source_organisations=[organisation], actor=specialist
    )
    assert changed == {}


# -- the create form and the detail page ------------------------------------


def test_the_create_form_saves_every_ticked_sender(signed_in):
    first = factories.OrganisationFactory(name="Aamet")
    second = factories.OrganisationFactory(name="Bliit")

    signed_in.post(
        CREATE,
        {"title": "Kaks saatjat", "source_organisations": [str(first.pk), str(second.pk)]},
    )

    assert senders_of(Matter.objects.get(title="Kaks saatjat")) == {"Aamet", "Bliit"}


def test_the_frequent_and_long_tail_controls_are_unioned(signed_in, specialist):
    """Neither list is privileged; the canonical answer is the union (brief 31)."""
    frequent = factories.OrganisationFactory(name="Aamet")
    for _ in range(3):
        factories.MatterFactory(owner=specialist, source_organisations=[frequent])
    rare = factories.OrganisationFactory(name="Zamet")

    signed_in.post(
        CREATE,
        {
            "title": "Sage ja harv",
            "source_organisations": [str(frequent.pk)],
            "source_organisations_other": [str(rare.pk)],
        },
    )

    assert senders_of(Matter.objects.get(title="Sage ja harv")) == {"Aamet", "Zamet"}


def test_an_organisation_ticked_in_both_controls_appears_once(signed_in):
    organisation = factories.OrganisationFactory()

    signed_in.post(
        CREATE,
        {
            "title": "Kaks korda",
            "source_organisations": [str(organisation.pk)],
            "source_organisations_other": [str(organisation.pk)],
        },
    )

    matter = Matter.objects.get(title="Kaks korda")
    assert MatterSourceOrganisation.objects.filter(matter=matter).count() == 1


def test_the_form_refuses_an_organisation_that_does_not_exist(signed_in):
    """Creating a Matter must not be a way to create institutions (brief 32)."""
    from app.organisations.models import Organisation

    response = signed_in.post(
        CREATE, {"title": "Väljamõeldud", "source_organisations": [str(uuid.uuid4())]}
    )

    assert response.status_code == 400
    assert not Matter.objects.filter(title="Väljamõeldud").exists()
    assert Organisation.objects.count() == 0


def test_the_detail_page_shows_every_sender(signed_in, specialist):
    """The rendered summary, not merely the names appearing somewhere.

    Every organisation's name is on this page anyway — the editor lists the
    whole reference table — so asserting "the name is present" would pass
    against a page that showed only the first sender (Agent-E brief 33).
    """
    first = factories.OrganisationFactory(name="Esimene saatja")
    second = factories.OrganisationFactory(name="Teine saatja")
    factories.OrganisationFactory(name="Kolmas asutus")
    matter = factories.MatterFactory(owner=specialist, source_organisations=[first, second])

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()

    assert "Esimene saatja, Teine saatja" in body
    assert "Kolmas asutus," not in body


def test_the_detail_page_survives_a_matter_with_no_sender(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    response = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert response.status_code == 200


# -- inline editing ---------------------------------------------------------


def _update_senders(client, matter: Matter, organisations: list) -> object:
    return client.post(
        reverse("matters:update_field", kwargs={"pk": matter.pk, "field": "source_organisations"}),
        {"source_organisations": [str(organisation.pk) for organisation in organisations]},
    )


def test_a_sender_can_be_added_removed_and_replaced(signed_in, specialist):
    first = factories.OrganisationFactory(name="Aamet")
    second = factories.OrganisationFactory(name="Bliit")
    third = factories.OrganisationFactory(name="Camet")
    matter = factories.MatterFactory(owner=specialist, source_organisations=[first])

    assert _update_senders(signed_in, matter, [first, second]).status_code == 200
    assert senders_of(matter) == {"Aamet", "Bliit"}

    _update_senders(signed_in, matter, [second])
    assert senders_of(matter) == {"Bliit"}

    _update_senders(signed_in, matter, [third])
    assert senders_of(matter) == {"Camet"}


def test_an_empty_inline_post_clears_every_sender(signed_in, specialist):
    """An HTML form omits unticked boxes, so this is how "none" arrives."""
    organisation = factories.OrganisationFactory()
    matter = factories.MatterFactory(owner=specialist, source_organisations=[organisation])

    assert _update_senders(signed_in, matter, []).status_code == 200
    assert senders_of(matter) == set()


def test_the_inline_editor_offers_checkboxes(signed_in, specialist):
    organisation = factories.OrganisationFactory(name="Aamet")
    matter = factories.MatterFactory(owner=specialist, source_organisations=[organisation])

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()

    inputs = re.findall(r"<input[^>]*name=\"source_organisations\"[^>]*>", body)
    assert inputs, "the header offers no sender control at all"
    assert all('type="checkbox"' in tag for tag in inputs)
    # And emphatically not the single-choice control it replaced.
    assert not re.search(r"<select[^>]*name=\"source_organisations\"", body)
    assert sum("checked" in tag for tag in inputs) == 1


# -- search -----------------------------------------------------------------


def test_a_matter_is_found_through_either_sender(signed_in, specialist):
    from app.search.indexing import indexable_matters, refresh_matters
    from app.search.services import search_matters

    alpha = factories.OrganisationFactory(name="Alfaministeerium")
    beta = factories.OrganisationFactory(name="Beetaliit")
    matter = factories.MatterFactory(
        title="Kahe saatjaga otsitav teema",
        owner=specialist,
        source_organisations=[alpha, beta],
    )
    refresh_matters(indexable_matters().filter(pk=matter.pk))

    for term in ("Alfaministeerium", "Beetaliit"):
        hits = [hit.matter.pk for hit in search_matters(query=term, user=specialist)]
        assert hits.count(matter.pk) == 1, term


def test_the_projection_does_not_depend_on_join_order(specialist):
    """Two rebuilds of an unchanged Matter must produce identical text.

    Word order taken from the join table would make every rebuild look like a
    content change and defeat the change detection the index rests on (brief 41).
    """
    from app.search.indexing import indexable_matters, indexed_text_for

    alpha = factories.OrganisationFactory(name="Zetaministeerium")
    beta = factories.OrganisationFactory(name="Alfaliit")
    matter = factories.MatterFactory(owner=specialist, source_organisations=[alpha, beta])

    first = indexed_text_for(indexable_matters().get(pk=matter.pk))
    # Rewriting the relation in the opposite order changes the join table's
    # natural order and must change nothing here.
    matter.source_organisations.clear()
    matter.source_organisations.set([beta, alpha])
    second = indexed_text_for(indexable_matters().get(pk=matter.pk))

    assert first == second
    assert "Alfaliit" in first["alias_text"]
    assert "Zetaministeerium" in first["alias_text"]


# -- filters ----------------------------------------------------------------


def test_filtering_by_any_sender_finds_the_matter_once(signed_in, specialist):
    first = factories.OrganisationFactory(name="Aamet")
    second = factories.OrganisationFactory(name="Bliit")
    factories.MatterFactory(
        title="Kahe saatjaga", owner=specialist, source_organisations=[first, second]
    )

    for organisation in (first, second):
        response = signed_in.get(REGISTER, {"saatja": str(organisation.pk), "olek": "koik"})
        assert titles_on(response) == ["Kahe saatjaga"], organisation.name


def test_the_convenience_filter_does_not_duplicate_a_multi_sender_matter(signed_in, specialist):
    """``asutus`` ORs across a many-to-many and a column; the row is still one."""
    organisation = factories.OrganisationFactory()
    other = factories.OrganisationFactory()
    factories.MatterFactory(
        title="Mõlemat pidi",
        owner=specialist,
        source_organisations=[organisation, other],
        addressee_organisation=organisation,
    )

    response = signed_in.get(REGISTER, {"asutus": str(organisation.pk), "olek": "koik"})
    assert titles_on(response) == ["Mõlemat pidi"]


def test_matters_without_any_sender_are_still_findable(signed_in, specialist):
    from app.matters import selectors

    factories.MatterFactory(title="Saatjata teema", owner=specialist)
    factories.MatterFactory(
        title="Saatjaga teema",
        owner=specialist,
        source_organisations=[factories.OrganisationFactory()],
    )

    response = signed_in.get(REGISTER, {"saatja": selectors.MISSING, "olek": "koik"})
    assert titles_on(response) == ["Saatjata teema"]


# -- usage ranking ----------------------------------------------------------


def test_usage_counts_distinct_matters_per_sender(specialist):
    """A Matter with two senders counts once for each (brief 36, 59)."""
    first = factories.OrganisationFactory(name="Aamet")
    second = factories.OrganisationFactory(name="Bliit")

    both = factories.MatterFactory(owner=specialist, source_organisations=[first, second])
    factories.MatterFactory(owner=specialist, source_organisations=[first])
    # Collaborators and policy areas add their own joins. Neither may inflate a
    # sender's count, which is the failure the department head never sees.
    both.collaborators.add(factories.UserFactory(), factories.UserFactory())
    both.policy_areas.add(factories.PolicyAreaFactory(), factories.PolicyAreaFactory())

    ranked = organisations_by_usage(specialist)
    assert ranked[0] == first
    assert second in ranked


def test_a_hidden_matter_does_not_rank_its_sender(specialist, other_specialist):
    """Ordering derived from records nobody can see would disclose them."""
    hidden_sender = factories.OrganisationFactory(name="Salajane saatja")
    open_sender = factories.OrganisationFactory(name="Avalik saatja")

    for _ in range(4):
        factories.MatterFactory(
            owner=other_specialist,
            visibility=Visibility.RESTRICTED,
            source_organisations=[hidden_sender],
        )
    factories.MatterFactory(owner=specialist, source_organisations=[open_sender])

    ranked = organisations_by_usage(specialist)
    assert ranked[0] == open_sender


# -- reporting --------------------------------------------------------------


def test_the_sender_metric_counts_matters_not_relations(specialist):
    from app.reporting import metric_catalogue as keys

    first = factories.OrganisationFactory(name="Aamet")
    second = factories.OrganisationFactory(name="Bliit")
    factories.MatterFactory(
        owner=specialist, reporting_year=2026, source_organisations=[first, second]
    )
    factories.MatterFactory(owner=specialist, reporting_year=2026, source_organisations=[first])

    result = _metric(specialist, keys.MATTERS_BY_SOURCE_ORGANISATION)

    # Two matters have a sender, and that is the headline — not the three
    # relations behind it.
    assert result.value == 2
    segments = {segment.label: segment.value for segment in result.segments}
    assert segments == {"Aamet": 2, "Bliit": 1}
    assert sum(segments.values()) > result.value
    assert any("mitu saatjat" in note for note in result.notes)


def test_the_csv_export_lists_every_sender(signed_in, specialist):
    first = factories.OrganisationFactory(name="Zetaministeerium")
    second = factories.OrganisationFactory(name="Alfaliit")
    factories.MatterFactory(
        title="Ekspordi teema",
        owner=specialist,
        reporting_year=2026,
        source_organisations=[first, second],
    )

    response = signed_in.get(reverse("reporting:export", kwargs={"slug": "teemad"}))
    body = b"".join(response.streaming_content).decode("utf-8-sig")

    assert "Alfaliit; Zetaministeerium" in body


# -- data classification ----------------------------------------------------


def test_sender_cardinality_does_not_affect_the_data_class(specialist):
    """TEST-ness comes from the Matter, never from how many senders it has."""
    from app.matters.enums import MatterDataClass

    matter = create_matter(
        title="Testandmete teema",
        actor=specialist,
        data_class=MatterDataClass.TEST,
        source_organisations=[factories.OrganisationFactory(), factories.OrganisationFactory()],
    )

    assert matter.data_class == MatterDataClass.TEST
    assert matter.source_organisations.count() == 2
    assert matter not in Matter.objects.real_data()


def test_the_purge_planner_owns_the_join_rows_and_not_the_organisations(specialist):
    """The relation is Matter-owned; the reference data is not (brief 69)."""
    from app.matters.enums import MatterDataClass
    from app.matters.purge import build_purge_plan

    organisation = factories.OrganisationFactory()
    create_matter(
        title="Testandmete teema",
        actor=specialist,
        data_class=MatterDataClass.TEST,
        source_organisations=[organisation],
    )

    plan = build_purge_plan([])

    assert plan.count_of(MatterSourceOrganisation._meta.label) == 1
    labels = {group.label for group in plan.owned}
    assert "organisations.Organisation" not in labels
    assert not any(blocker.label == "organisations.Organisation" for blocker in plan.blockers)


# -- query cost -------------------------------------------------------------


def test_rendering_many_senders_costs_no_query_per_row(
    signed_in, specialist, django_assert_max_num_queries
):
    """Plural rendering must be one prefetch, not one query per Matter.

    The failure mode is invisible on a fixture with three rows and ruinous on a
    register with years in it, which is why the assertion is a budget rather
    than a count (Agent-E brief 38, 80).
    """
    for index in range(20):
        factories.MatterFactory(
            title=f"Teema {index}",
            owner=specialist,
            source_organisations=[
                factories.OrganisationFactory(),
                factories.OrganisationFactory(),
            ],
        )

    with django_assert_max_num_queries(25):
        response = signed_in.get(REGISTER, {"olek": "koik"})
        response.content.decode()


def test_the_department_overview_prefetches_its_senders(
    signed_in, specialist, django_assert_max_num_queries
):
    """A ceiling, and what it is a ceiling on.

    The property is that the cost does not scale with rows: fifteen Matters with
    two senders each would add at least fifteen queries the moment the prefetch
    is lost, which is an order of magnitude past any headroom here.

    The number moved from 40 when the QA round gave the page two more figures —
    *Arvamusi koostamisel*, and the honest Matter total behind *Vajab
    sekkumist*, which is a union of four populations and cannot be read off the
    capped row list. Every population the page had already resolved is reused
    rather than re-scoped (ADR 0033, `overview.Populations`).
    """
    for index in range(15):
        factories.MatterFactory(
            title=f"Ulevaate teema {index}",
            owner=specialist,
            source_organisations=[
                factories.OrganisationFactory(),
                factories.OrganisationFactory(),
            ],
        )

    with django_assert_max_num_queries(44):
        response = signed_in.get(reverse("matters:overview"))
        response.content.decode()


# -- integrity --------------------------------------------------------------


def test_an_organisation_is_protected_while_it_is_a_sender(specialist):
    """The invariant the removed PROTECT foreign key gave (brief 73, 76)."""
    organisation = factories.OrganisationFactory()
    matter = factories.MatterFactory(owner=specialist, source_organisations=[organisation])

    with pytest.raises(ProtectedError):
        organisation.delete()

    set_organisations(matter=matter, source_organisations=[], actor=specialist)
    organisation.delete()
