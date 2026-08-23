"""Reading the OneNote filing structure as PolicyArea, additively and once reviewed.

The rules under test are mostly rules about what *not* to do: do not guess a
mapping, do not create taxonomy, do not remove what a lawyer chose, do not
classify from a background page or a development record.

Every notebook, section and area name below is invented.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.legacy_import.onenote_policy_areas import (
    MappingClass,
    OneNotePolicyAreaRule,
    PolicyAreaPlanChanged,
    UnknownCapture,
    apply_policy_area_plan,
    build_policy_area_plan,
    inventory,
    summary,
)
from app.legacy_import.source_pages import (
    LegacySourcePage,
    MatterSourcePage,
    SourceMatchClass,
    SourceMatchMethod,
    SourcePageRole,
    SourceRelationshipKind,
    SourceSystem,
)
from app.matters.enums import MatterDataClass
from app.taxonomy.models import PolicyArea, Tag
from tests import factories

pytestmark = pytest.mark.django_db

NOTEBOOK = "Näidiskoja õigusloome"
GROUP = "ARHIIV"
TAX_SECTION = "Maksundus ja toll"
UNKNOWN_SECTION = "Muud"


def _page(*, key: str, section: str, group: str = GROUP, system=SourceSystem.ONENOTE_DESKTOP):
    now = timezone.now()
    return LegacySourcePage.objects.create(
        source_system=system,
        source_page_id=f"1-{key}",
        page_key=key,
        source_notebook=NOTEBOOK,
        source_section=section,
        source_section_group=group,
        title=f"Näidisleht {key}",
        page_role=SourcePageRole.MATTER_LIKE,
        capture_id=f"capture-{key}",
        source_xml_sha256=f"{key:0<64}"[:64],
        first_imported_at=now,
        latest_imported_at=now,
    )


def _link(matter, page, kind=SourceRelationshipKind.PRIMARY):
    return MatterSourcePage.objects.create(
        matter=matter,
        source_page=page,
        relationship_kind=kind,
        match_method=SourceMatchMethod.EXCEL_EXACT_PAGE_ID,
        match_class=SourceMatchClass.EXACT,
    )


def _area(name: str, key: str) -> PolicyArea:
    return factories.PolicyAreaFactory(name_et=name, key=key)


# -- mapping classes --------------------------------------------------------


def test_a_section_named_exactly_like_an_area_proposes_that_area():
    """Recognition, not interpretation: somebody named the folder after it."""
    area = _area(TAX_SECTION, "maksundus")
    matter = factories.MatterFactory()
    _link(matter, _page(key="a", section=TAX_SECTION))

    plan = build_policy_area_plan()
    (proposal,) = plan.proposals
    assert proposal.mapping_class == MappingClass.EXACT_NAME
    assert proposal.policy_area_id == area.id
    assert summary(plan)["location_classes"][MappingClass.EXACT_NAME] == 1


def test_case_and_whitespace_do_not_defeat_the_exact_name_rule():
    area = _area(TAX_SECTION, "maksundus")
    matter = factories.MatterFactory()
    _link(matter, _page(key="a", section="  maksundus JA toll  "))

    (proposal,) = build_policy_area_plan().proposals
    assert proposal.policy_area_id == area.id


def test_diacritics_are_meaning_and_are_not_folded():
    """Two Estonian names differing only in a diacritic are two names.

    ``normalize_for_matching`` strips diacritics, which is right for finding an
    organisation somebody typed without õ and wrong for deciding that a folder
    *is* a policy area.
    """
    _area("Töökeskkond", "tookeskkond")
    matter = factories.MatterFactory()
    _link(matter, _page(key="a", section="Tookeskkond"))

    plan = build_policy_area_plan()
    assert plan.proposals == ()
    assert summary(plan)["location_classes"][MappingClass.UNMAPPED] == 1


def test_a_reviewed_alias_maps_a_section_that_is_named_nothing_like_the_area():
    area = _area("Keskkond ja kliima", "keskkond")
    matter = factories.MatterFactory()
    _link(matter, _page(key="a", section="Pakendid ja jäätmed"))

    rules = (
        OneNotePolicyAreaRule(
            rule_id="pakendid-keskkond",
            source_section_group=GROUP,
            source_section="Pakendid ja jäätmed",
            policy_area_key="keskkond",
        ),
    )
    plan = build_policy_area_plan(rules=rules)

    (proposal,) = plan.proposals
    assert proposal.mapping_class == MappingClass.REVIEWED_ALIAS
    assert proposal.rule_id == "pakendid-keskkond"
    assert proposal.policy_area_id == area.id
    assert summary(plan)["reviewed_rules"] == 1


def test_the_mapping_key_is_the_group_and_the_section_together():
    """The same leaf name under two groups is two filing locations.

    A key that ignored the group would file both under whichever one happened to
    be reviewed first.
    """
    area = _area("Keskkond ja kliima", "keskkond")
    first, second = factories.MatterFactory(), factories.MatterFactory()
    _link(first, _page(key="a", section="Üldine", group="ARHIIV keskkond"))
    _link(second, _page(key="b", section="Üldine", group="ARHIIV maksud"))

    rules = (
        OneNotePolicyAreaRule(
            rule_id="keskkond-yldine",
            source_section_group="ARHIIV keskkond",
            source_section="Üldine",
            policy_area_key="keskkond",
        ),
    )
    plan = build_policy_area_plan(rules=rules)

    assert [item.matter_id for item in plan.proposals] == [first.id]
    assert plan.proposals[0].policy_area_id == area.id


def test_an_unknown_section_stays_unmapped():
    """Unmapped is a valid result. Nobody knows what "Muud" meant."""
    _area(TAX_SECTION, "maksundus")
    matter = factories.MatterFactory()
    _link(matter, _page(key="a", section=UNKNOWN_SECTION))

    plan = build_policy_area_plan()
    assert plan.proposals == ()
    figures = summary(plan)
    assert figures["location_classes"][MappingClass.UNMAPPED] == 1
    assert figures["unmapped_locations"] == [f"{GROUP} → {UNKNOWN_SECTION}"]


def test_a_name_shared_by_two_active_areas_is_ambiguous():
    _area("Kattuv nimi", "esimene")
    _area("Kattuv nimi", "teine")
    matter = factories.MatterFactory()
    _link(matter, _page(key="a", section="Kattuv nimi"))

    plan = build_policy_area_plan()
    assert plan.proposals == ()
    assert summary(plan)["location_classes"][MappingClass.AMBIGUOUS] == 1


def test_a_rule_naming_no_active_area_is_a_configuration_error():
    """Not a reason to create the area. A reason to fix the rule."""
    matter = factories.MatterFactory()
    _link(matter, _page(key="a", section="Pakendid ja jäätmed"))
    rules = (
        OneNotePolicyAreaRule(
            rule_id="katkine",
            source_section_group=GROUP,
            source_section="Pakendid ja jäätmed",
            policy_area_key="ei-ole-olemas",
        ),
    )

    plan = build_policy_area_plan(rules=rules)
    assert plan.proposals == ()
    assert summary(plan)["misconfigured_rules"] == ["katkine"]
    assert PolicyArea.objects.filter(key="ei-ole-olemas").exists() is False


def test_an_inactive_area_is_not_a_target():
    factories.PolicyAreaFactory(name_et=TAX_SECTION, key="maksundus", is_active=False)
    matter = factories.MatterFactory()
    _link(matter, _page(key="a", section=TAX_SECTION))

    assert build_policy_area_plan().proposals == ()


# -- eligibility ------------------------------------------------------------


def test_a_background_page_does_not_classify_the_matter():
    """Background material lives in a themed section because of what it is
    *about*, which is not what the Matter is about."""
    _area(TAX_SECTION, "maksundus")
    matter = factories.MatterFactory()
    _link(matter, _page(key="a", section=TAX_SECTION), kind=SourceRelationshipKind.BACKGROUND)

    plan = build_policy_area_plan()
    assert plan.proposals == ()
    assert summary(plan)["background_links_excluded"] == 1


def test_a_related_page_does_classify_the_matter():
    """RELATED is an accepted link a person or a hyperlink established."""
    area = _area(TAX_SECTION, "maksundus")
    matter = factories.MatterFactory()
    _link(matter, _page(key="a", section=TAX_SECTION), kind=SourceRelationshipKind.RELATED)

    (proposal,) = build_policy_area_plan().proposals
    assert proposal.policy_area_id == area.id


def test_a_development_record_gains_no_canonical_classification():
    """A TEST Matter is not the department's history and must not enter it."""
    _area(TAX_SECTION, "maksundus")
    matter = factories.MatterFactory(data_class=MatterDataClass.TEST)
    _link(matter, _page(key="a", section=TAX_SECTION))

    plan = build_policy_area_plan()
    assert plan.proposals == ()
    assert summary(plan)["test_matter_links_excluded"] == 1

    apply_policy_area_plan(plan, expect_plan_sha256=plan.digest)
    assert matter.policy_areas.count() == 0


def test_a_page_from_the_invalidated_graph_export_fails_the_whole_run_closed():
    """Its page-to-content associations were proven wrong; so is its filing."""
    _area(TAX_SECTION, "maksundus")
    matter = factories.MatterFactory()
    _link(
        matter,
        _page(key="a", section=TAX_SECTION, system=SourceSystem.ONENOTE_GRAPH_INVALID),
    )

    with pytest.raises(UnknownCapture):
        build_policy_area_plan()


# -- applying ---------------------------------------------------------------


def test_apply_adds_the_area_and_leaves_the_captured_section_alone():
    area = _area(TAX_SECTION, "maksundus")
    matter = factories.MatterFactory()
    page = _link(matter, _page(key="a", section=TAX_SECTION)).source_page

    plan = build_policy_area_plan()
    result = apply_policy_area_plan(plan, expect_plan_sha256=plan.digest)

    assert result.relations_added == 1
    assert list(matter.policy_areas.all()) == [area]
    page.refresh_from_db()
    assert page.source_section == TAX_SECTION
    assert page.source_section_group == GROUP


def test_enrichment_is_additive_and_never_replaces_a_manual_choice():
    """The modern taxonomy and the 2019 filing cabinet are not the same thing.

    A page that lived in one drawer is not evidence that a lawyer's own choice
    was wrong.
    """
    chosen = _area("Käsitsi valitud valdkond", "kasitsi")
    from_source = _area(TAX_SECTION, "maksundus")
    matter = factories.MatterFactory()
    matter.policy_areas.add(chosen)
    _link(matter, _page(key="a", section=TAX_SECTION))

    plan = build_policy_area_plan()
    apply_policy_area_plan(plan, expect_plan_sha256=plan.digest)

    assert set(matter.policy_areas.all()) == {chosen, from_source}


def test_several_pages_may_add_several_areas():
    """``policy_areas`` is genuinely many-to-many; no primary is chosen."""
    tax = _area(TAX_SECTION, "maksundus")
    env = _area("Keskkond ja kliima", "keskkond")
    matter = factories.MatterFactory()
    _link(matter, _page(key="a", section=TAX_SECTION))
    _link(matter, _page(key="b", section="Keskkond ja kliima"), SourceRelationshipKind.RELATED)

    plan = build_policy_area_plan()
    result = apply_policy_area_plan(plan, expect_plan_sha256=plan.digest)

    assert result.relations_added == 2
    assert set(matter.policy_areas.all()) == {tax, env}


def test_two_pages_in_the_same_drawer_propose_one_relation():
    """Two accepted pages filed in the same place are not two additions.

    They propose the same area twice, and counting it twice would report a
    number the database never held.
    """
    area = _area(TAX_SECTION, "maksundus")
    matter = factories.MatterFactory()
    _link(matter, _page(key="a", section=TAX_SECTION))
    _link(matter, _page(key="b", section=TAX_SECTION), SourceRelationshipKind.RELATED)

    plan = build_policy_area_plan()
    assert len(plan.proposals) == 2
    assert summary(plan)["new_relations"] == 1

    result = apply_policy_area_plan(plan, expect_plan_sha256=plan.digest)
    assert result.relations_added == 1
    assert list(matter.policy_areas.all()) == [area]

    event = ChangeEvent.objects.get(matter=matter, event_type=ChangeEventType.IMPORT_APPLIED)
    assert event.payload["policy_area_keys"] == ["maksundus"]


def test_nothing_creates_taxonomy_or_tags():
    _area(TAX_SECTION, "maksundus")
    matter = factories.MatterFactory()
    _link(matter, _page(key="a", section=TAX_SECTION))
    _link(matter, _page(key="b", section=UNKNOWN_SECTION), SourceRelationshipKind.RELATED)

    before_areas = PolicyArea.objects.count()
    plan = build_policy_area_plan()
    apply_policy_area_plan(plan, expect_plan_sha256=plan.digest)

    assert PolicyArea.objects.count() == before_areas
    assert Tag.objects.count() == 0


def test_the_addition_is_audited_with_its_mapping_provenance():
    _area(TAX_SECTION, "maksundus")
    matter = factories.MatterFactory()
    page = _link(matter, _page(key="a", section=TAX_SECTION)).source_page

    plan = build_policy_area_plan()
    apply_policy_area_plan(plan, expect_plan_sha256=plan.digest)

    event = ChangeEvent.objects.get(matter=matter, event_type=ChangeEventType.IMPORT_APPLIED)
    assert event.actor is None
    assert event.payload["policy_area_keys"] == ["maksundus"]
    provenance = event.payload["provenance"]
    assert provenance["source"] == "ONENOTE_SECTION"
    assert provenance["mapping_version"] == plan.mapping_version
    assert provenance["capture_sha256"] == plan.capture_sha256
    assert provenance["source_page_ids"] == [str(page.id)]


def test_a_second_apply_changes_nothing_and_raises_no_second_event():
    _area(TAX_SECTION, "maksundus")
    matter = factories.MatterFactory()
    _link(matter, _page(key="a", section=TAX_SECTION))

    first = build_policy_area_plan()
    apply_policy_area_plan(first, expect_plan_sha256=first.digest)

    second = build_policy_area_plan()
    assert second.additions == ()
    assert summary(second)["already_present"] == 1

    result = apply_policy_area_plan(second, expect_plan_sha256=second.digest)
    assert result.relations_added == 0
    assert matter.policy_areas.count() == 1
    assert (
        ChangeEvent.objects.filter(matter=matter, event_type=ChangeEventType.IMPORT_APPLIED).count()
        == 1
    )


# -- the digest -------------------------------------------------------------


def test_the_plan_digest_is_stable_across_identical_runs():
    _area(TAX_SECTION, "maksundus")
    matter = factories.MatterFactory()
    _link(matter, _page(key="a", section=TAX_SECTION))
    assert build_policy_area_plan().digest == build_policy_area_plan().digest


def test_the_digest_moves_when_the_mapping_registry_does():
    """The same pages read under different rules are a different plan."""
    _area("Keskkond ja kliima", "keskkond")
    matter = factories.MatterFactory()
    _link(matter, _page(key="a", section="Pakendid ja jäätmed"))

    without = build_policy_area_plan().digest
    with_rule = build_policy_area_plan(
        rules=(
            OneNotePolicyAreaRule(
                rule_id="pakendid-keskkond",
                source_section_group=GROUP,
                source_section="Pakendid ja jäätmed",
                policy_area_key="keskkond",
            ),
        )
    ).digest
    assert without != with_rule


def test_a_wrong_digest_writes_nothing():
    _area(TAX_SECTION, "maksundus")
    matter = factories.MatterFactory()
    _link(matter, _page(key="a", section=TAX_SECTION))

    plan = build_policy_area_plan()
    with pytest.raises(PolicyAreaPlanChanged):
        apply_policy_area_plan(plan, expect_plan_sha256="0" * 64)
    assert matter.policy_areas.count() == 0


# -- the inventory ----------------------------------------------------------


def test_the_inventory_states_no_opinion():
    """The read that has to happen before a single mapping is written."""
    _area(TAX_SECTION, "maksundus")
    matter = factories.MatterFactory()
    _link(matter, _page(key="a", section=TAX_SECTION))
    _link(matter, _page(key="b", section=UNKNOWN_SECTION), SourceRelationshipKind.RELATED)

    rows = {row["section"]: row for row in inventory()}
    assert rows[TAX_SECTION]["matches_active_area_name"] is True
    assert rows[UNKNOWN_SECTION]["matches_active_area_name"] is False
    assert rows[TAX_SECTION]["matters"] == 1


# -- the command ------------------------------------------------------------


def test_the_plan_command_writes_nothing_and_prints_its_digest():
    from io import StringIO

    from django.core.management import call_command

    _area(TAX_SECTION, "maksundus")
    matter = factories.MatterFactory()
    _link(matter, _page(key="a", section=TAX_SECTION))
    plan = build_policy_area_plan()

    out = StringIO()
    call_command("onenote_policy_area_enrichment", "plan", stdout=out)

    assert plan.digest in out.getvalue()
    assert matter.policy_areas.count() == 0


def test_the_inventory_command_names_the_locations_and_no_mapping():
    from io import StringIO

    from django.core.management import call_command

    matter = factories.MatterFactory()
    _link(matter, _page(key="a", section=UNKNOWN_SECTION))

    out = StringIO()
    call_command("onenote_policy_area_enrichment", "inventory", stdout=out)
    printed = out.getvalue()

    assert UNKNOWN_SECTION in printed
    assert "REVIEWED_ALIAS_RULES" in printed
