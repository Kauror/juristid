"""Reading the OneNote filing structure as canonical ``PolicyArea``, once reviewed.

``LegacySourcePage.source_section`` is where a lawyer filed something in 2019.
Stage 2D kept it verbatim and refused to treat it as taxonomy, and that decision
stands: the section stays exactly as captured, this module never writes to it,
and the display of a page's original location is unchanged. What is added here
is a *separate* interpretation laid on top — the same shape as
``LegacyStatusMapping``, which interprets a historical ``HETKESEIS`` without
editing it.

Why the section is not simply the area
--------------------------------------
The two classifications were built for different jobs at different times.
OneNote sections are exclusive folders one page can sit in; ``PolicyArea`` is a
small reporting vocabulary a Matter can carry several of. Some sections are not
subjects at all — a drawer called ``ARHIIV``, a catch-all called ``Muud``, a
year. Copying section names into taxonomy would produce a chooser full of
filing-cabinet labels and a statistic nobody could defend.

So a mapping is a reviewed statement, and it enters production the only way a
reviewed statement can: through code review of :data:`REVIEWED_ALIAS_RULES`.
There is deliberately **no admin table** of mappings in this phase — an
unreviewed row in a database is exactly the guess this module exists to prevent
(brief 39).

The one automatic rule
----------------------
A section whose normalised name is *exactly* one active area's canonical
Estonian name proposes that area. That is not interpretation, it is recognition:
somebody named the folder after the area. It is still counted and reported
separately from the reviewed aliases, because "we recognised the name" and "a
person decided these mean the same thing" are different levels of evidence and a
report that merged them would hide which one carried the corpus (brief 40).

Normalisation is whitespace and case, and nothing else. In particular diacritics
are **not** folded: ``app.core.text.normalize_for_matching`` strips them, which
is right for finding an organisation somebody typed without õ and wrong here,
where two Estonian area names differing only in a diacritic are two areas.

What it refuses
---------------
No fuzzy matching. No taxonomy created. No ``Tag`` created — ``PolicyArea`` and
``Tag`` are separate dimensions and a source section is neither by default. No
existing area removed, ever. No classification from an unaccepted match
candidate, and none from a ``BACKGROUND`` page: background material lives in a
themed section because of what it is *about*, which is not the same as what the
Matter is about (brief 41–48).
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.db import transaction

from app.legacy_import.source_pages import (
    LegacySourcePage,
    MatterSourcePage,
    SourceRelationshipKind,
    SourceSystem,
)
from app.matters.enums import MatterDataClass
from app.matters.models import Matter
from app.matters.services import add_source_derived_policy_areas
from app.taxonomy.models import PolicyArea

#: Bumped when a rule changes meaning or the registry gains an entry. Recorded
#: in the plan digest and the audit provenance, so a Matter classified under
#: version 1.0 stays distinguishable from one classified under 1.1 (brief 70).
ONENOTE_POLICY_AREA_MAPPING_VERSION = "1.0"

#: The capture authority. ``ONENOTE_GRAPH_INVALID`` exists in the vocabulary
#: precisely so a row from the discredited Graph export can be recognised and
#: refused rather than quietly classified; its page-to-content associations were
#: proven wrong (Stage-2D brief 4).
AUTHORITATIVE_SOURCE_SYSTEM = SourceSystem.ONENOTE_DESKTOP

#: Which links may classify a Matter. ``BACKGROUND`` is absent and its absence
#: is the decision: a background page filed under *Maksud ja toll* says the
#: material is about tax, not that the Matter is (brief 42, 88).
CLASSIFYING_RELATIONSHIPS: tuple[str, ...] = (
    SourceRelationshipKind.PRIMARY.value,
    SourceRelationshipKind.RELATED.value,
)


class MappingClass:
    EXACT_NAME = "EXACT_NAME"
    REVIEWED_ALIAS = "REVIEWED_ALIAS"
    UNMAPPED = "UNMAPPED"
    AMBIGUOUS = "AMBIGUOUS"
    #: A reviewed rule naming an area that does not exist or is inactive. A
    #: configuration error, not a data finding, and never a reason to create the
    #: area (brief 46).
    MISSING_TARGET = "MISSING_TARGET"


MAPPING_CLASSES: tuple[str, ...] = (
    MappingClass.EXACT_NAME,
    MappingClass.REVIEWED_ALIAS,
    MappingClass.AMBIGUOUS,
    MappingClass.MISSING_TARGET,
    MappingClass.UNMAPPED,
)


class UnknownCapture(Exception):
    """Pages from a source system that may not classify anything. Fail closed."""


@dataclass(frozen=True)
class OneNotePolicyAreaRule:
    """One reviewed statement that a filing location means a policy area.

    Keyed on the **pair** — section group and section — rather than the section
    name alone. The corpus is a notebook of grouped sections and the same leaf
    name can appear under two groups; a key that ignored the group would file
    both under whichever one was reviewed first. An empty ``source_section_group``
    matches only pages that genuinely have none.
    """

    rule_id: str
    source_section_group: str
    source_section: str
    policy_area_key: str
    note: str = ""

    @property
    def location(self) -> tuple[str, str]:
        return normalise_location(self.source_section_group, self.source_section)


def normalise_location(section_group: str, section: str) -> tuple[str, str]:
    """Whitespace and case only. Diacritics are meaning here, not noise."""
    return (section_group or "").strip().casefold(), (section or "").strip().casefold()


#: The reviewed mappings, and there are none yet.
#:
#: That is the honest state, not an oversight. Writing rules requires the
#: inventory of what the corpus actually contains — which sections exist, under
#: which groups, carrying how many Matters — and the ``inventory`` mode of
#: ``onenote_policy_area_enrichment`` produces exactly that against the real
#: database. Until a person has read it and decided what ``Muud``, ``Üldine``,
#: ``ARHIIV`` and ``EL`` were used for, guessing would classify a decade of work
#: under areas nobody chose. Unmapped is a valid result (brief 41).
REVIEWED_ALIAS_RULES: tuple[OneNotePolicyAreaRule, ...] = ()


def rules_by_location(
    rules: tuple[OneNotePolicyAreaRule, ...] | None = None,
) -> dict[tuple[str, str], OneNotePolicyAreaRule]:
    return {rule.location: rule for rule in (REVIEWED_ALIAS_RULES if rules is None else rules)}


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SectionMapping:
    """What one filing location resolves to."""

    section_group: str
    section: str
    mapping_class: str
    rule_id: str = ""
    policy_area_id: UUID | None = None
    policy_area_key: str = ""
    detail: str = ""


@dataclass(frozen=True)
class LinkProposal:
    """One Matter, one accepted page, one area the page's location proposes."""

    matter_id: UUID
    matter_reference: str
    source_page_id: UUID
    page_key: str
    section_group: str
    section: str
    mapping_class: str
    rule_id: str
    policy_area_id: UUID
    policy_area_key: str
    already_present: bool

    def digest_row(self) -> dict[str, Any]:
        return {
            "matter_id": str(self.matter_id),
            "source_page_id": str(self.source_page_id),
            "section_group": self.section_group,
            "section": self.section,
            "rule_id": self.rule_id,
            "mapping_class": self.mapping_class,
            "policy_area_id": str(self.policy_area_id),
        }


@dataclass(frozen=True)
class PolicyAreaPlan:
    mapping_version: str
    capture_sha256: str
    #: How many reviewed alias rules this plan was computed with. On the plan
    #: rather than read from the module constant, so a report can never claim a
    #: rule count the plan did not actually use.
    reviewed_rules: int
    proposals: tuple[LinkProposal, ...]
    sections: tuple[SectionMapping, ...]
    considered_links: int
    considered_matters: int
    background_links: int
    test_matter_links: int

    @property
    def additions(self) -> tuple[LinkProposal, ...]:
        """Proposals that would actually write something."""
        return tuple(item for item in self.proposals if not item.already_present)

    @property
    def digest(self) -> str:
        """Deterministic over the whole proposal set, additions and all.

        Sorted before hashing, and the mapping version is inside it: the same
        pages read under a changed registry are a different plan even when the
        resulting areas happen to coincide.
        """
        body = {
            "mapping_version": self.mapping_version,
            "capture_sha256": self.capture_sha256,
            "proposals": sorted(
                (item.digest_row() for item in self.proposals),
                key=lambda row: (row["matter_id"], row["source_page_id"], row["policy_area_id"]),
            ),
        }
        encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def capture_identity(pages: list[LegacySourcePage]) -> str:
    """Which captured page versions this plan read.

    The archive has no single capture id — each page carries its own, because
    each page was captured in its own right — so the honest identity of a run is
    a fingerprint of the exact set of captures it looked at. Two plans over the
    same pages agree; a plan made after a re-capture does not, and the apply
    refuses it (brief 92).
    """
    body = sorted({(page.page_key, page.capture_id, page.source_xml_sha256) for page in pages})
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _resolve_sections(
    locations: list[tuple[str, str]],
    rules: dict[tuple[str, str], OneNotePolicyAreaRule],
) -> dict[tuple[str, str], SectionMapping]:
    """Every distinct filing location, resolved once.

    Reviewed aliases are consulted before the automatic name rule. A person who
    has decided that a section means something outranks a coincidence of
    spelling, which is the only ordering that lets a rule *correct* the
    automatic behaviour rather than merely add to it.
    """
    areas = list(PolicyArea.objects.filter(is_active=True))
    by_name: dict[str, list[PolicyArea]] = {}
    for area in areas:
        by_name.setdefault(area.name_et.strip().casefold(), []).append(area)
    by_key = {area.key: area for area in areas}

    resolved: dict[tuple[str, str], SectionMapping] = {}
    for group, section in locations:
        key = normalise_location(group, section)
        if key in resolved:
            # Two captured spellings of one filing location — a trailing space,
            # a capital. They are the same drawer and resolve once; the first in
            # sorted order supplies the wording the report shows.
            continue
        rule = rules.get(key)
        if rule is not None:
            target = by_key.get(rule.policy_area_key)
            if target is None:
                resolved[key] = SectionMapping(
                    section_group=group,
                    section=section,
                    mapping_class=MappingClass.MISSING_TARGET,
                    rule_id=rule.rule_id,
                    policy_area_key=rule.policy_area_key,
                    detail="Reviewed rule names no active PolicyArea.",
                )
                continue
            resolved[key] = SectionMapping(
                section_group=group,
                section=section,
                mapping_class=MappingClass.REVIEWED_ALIAS,
                rule_id=rule.rule_id,
                policy_area_id=target.id,
                policy_area_key=target.key,
            )
            continue

        candidates = by_name.get(key[1], [])
        if len(candidates) == 1:
            matched = candidates[0]
            resolved[key] = SectionMapping(
                section_group=group,
                section=section,
                mapping_class=MappingClass.EXACT_NAME,
                rule_id=f"exact-name:{matched.key}",
                policy_area_id=matched.id,
                policy_area_key=matched.key,
            )
        elif len(candidates) > 1:
            resolved[key] = SectionMapping(
                section_group=group,
                section=section,
                mapping_class=MappingClass.AMBIGUOUS,
                detail=f"{len(candidates)} active areas share this name.",
            )
        else:
            resolved[key] = SectionMapping(
                section_group=group,
                section=section,
                mapping_class=MappingClass.UNMAPPED,
            )
    return resolved


def build_policy_area_plan(
    *, rules: tuple[OneNotePolicyAreaRule, ...] | None = None
) -> PolicyAreaPlan:
    """Read the corpus and decide everything. Writes nothing."""
    links = list(
        MatterSourcePage.objects.select_related("source_page", "matter").order_by(
            "matter_id", "source_page_id"
        )
    )

    foreign = {
        link.source_page.source_system
        for link in links
        if link.source_page.source_system != AUTHORITATIVE_SOURCE_SYSTEM
    }
    if foreign:
        # Fail closed. A page from the invalidated Graph export attached its
        # files to the wrong pages; its filing location is no better evidence.
        raise UnknownCapture(
            "Matter-linked source pages come from more than the authoritative "
            f"desktop capture: {', '.join(sorted(foreign))}."
        )

    background = [link for link in links if link.relationship_kind not in CLASSIFYING_RELATIONSHIPS]
    classifying = [link for link in links if link.relationship_kind in CLASSIFYING_RELATIONSHIPS]
    test_links = [link for link in classifying if link.matter.data_class == MatterDataClass.TEST]
    eligible = [link for link in classifying if link.matter.data_class != MatterDataClass.TEST]

    locations = sorted(
        {
            (link.source_page.source_section_group, link.source_page.source_section)
            for link in eligible
        }
    )
    resolved = _resolve_sections(locations, rules_by_location(rules))

    matter_ids = {link.matter_id for link in eligible}
    present: dict[UUID, set[UUID]] = {matter_id: set() for matter_id in matter_ids}
    for matter_id, area_id in Matter.policy_areas.through.objects.filter(
        matter_id__in=matter_ids
    ).values_list("matter_id", "policyarea_id"):
        present[matter_id].add(area_id)

    proposals: list[LinkProposal] = []
    for link in eligible:
        page = link.source_page
        mapping = resolved[normalise_location(page.source_section_group, page.source_section)]
        if mapping.policy_area_id is None:
            continue
        proposals.append(
            LinkProposal(
                matter_id=link.matter_id,
                matter_reference=link.matter.display_reference,
                source_page_id=page.id,
                page_key=page.page_key,
                section_group=page.source_section_group,
                section=page.source_section,
                mapping_class=mapping.mapping_class,
                rule_id=mapping.rule_id,
                policy_area_id=mapping.policy_area_id,
                policy_area_key=mapping.policy_area_key,
                already_present=mapping.policy_area_id in present[link.matter_id],
            )
        )

    return PolicyAreaPlan(
        mapping_version=ONENOTE_POLICY_AREA_MAPPING_VERSION,
        capture_sha256=capture_identity([link.source_page for link in eligible]),
        reviewed_rules=len(rules if rules is not None else REVIEWED_ALIAS_RULES),
        proposals=tuple(proposals),
        # Keyed by the *normalised* location, so this iterates the resolver's
        # own keys rather than the raw spellings that produced them.
        sections=tuple(resolved[key] for key in sorted(resolved)),
        considered_links=len(eligible),
        considered_matters=len(matter_ids),
        background_links=len(background),
        test_matter_links=len(test_links),
    )


def summary(plan: PolicyAreaPlan) -> dict[str, Any]:
    """Aggregates only: counts, section names and area keys.

    Section names are the department's own filing vocabulary rather than the
    content of anybody's file, and an operator cannot review a mapping without
    seeing which locations went unmapped. Matter titles do not appear.
    """
    classes = Counter(section.mapping_class for section in plan.sections)
    matters_with_additions = {item.matter_id for item in plan.additions}
    return {
        "mapping_version": plan.mapping_version,
        "capture_sha256": plan.capture_sha256,
        "plan_sha256": plan.digest,
        "reviewed_rules": plan.reviewed_rules,
        "matters_considered": plan.considered_matters,
        "links_considered": plan.considered_links,
        "background_links_excluded": plan.background_links,
        "test_matter_links_excluded": plan.test_matter_links,
        "distinct_locations": len(plan.sections),
        "location_classes": {name: classes.get(name, 0) for name in MAPPING_CLASSES},
        "proposals": len(plan.proposals),
        "already_present": len(plan.proposals) - len(plan.additions),
        # Distinct Matter↔area pairs. Two accepted pages under the same mapped
        # location propose the same area twice; that is one relation.
        "new_relations": len({(item.matter_id, item.policy_area_id) for item in plan.additions}),
        "matters_with_additions": len(matters_with_additions),
        "unmapped_locations": sorted(
            f"{section.section_group} → {section.section}".strip(" →")
            for section in plan.sections
            if section.mapping_class == MappingClass.UNMAPPED
        ),
        "ambiguous_locations": sorted(
            f"{section.section_group} → {section.section}".strip(" →")
            for section in plan.sections
            if section.mapping_class == MappingClass.AMBIGUOUS
        ),
        "misconfigured_rules": sorted(
            section.rule_id
            for section in plan.sections
            if section.mapping_class == MappingClass.MISSING_TARGET
        ),
    }


def inventory() -> list[dict[str, Any]]:
    """What the corpus actually holds, so mappings can be written from evidence.

    The read that has to happen before :data:`REVIEWED_ALIAS_RULES` gains a
    single entry. It states no opinion: one row per filing location, how many
    accepted classifying links sit under it, and whether an active area already
    carries that exact name.
    """
    counts: Counter[tuple[str, str]] = Counter()
    matters: dict[tuple[str, str], set[UUID]] = {}
    for link in MatterSourcePage.objects.select_related("source_page").filter(
        relationship_kind__in=CLASSIFYING_RELATIONSHIPS
    ):
        page = link.source_page
        key = (page.source_section_group, page.source_section)
        counts[key] += 1
        matters.setdefault(key, set()).add(link.matter_id)

    names = {area.name_et.strip().casefold() for area in PolicyArea.objects.filter(is_active=True)}
    return [
        {
            "section_group": group,
            "section": section,
            "links": counts[(group, section)],
            "matters": len(matters[(group, section)]),
            "matches_active_area_name": section.strip().casefold() in names,
        }
        for group, section in sorted(counts)
    ]


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyAreaApplyResult:
    matters_changed: int
    relations_added: int
    mapping_version: str
    plan_sha256: str


class PolicyAreaPlanChanged(Exception):
    """The corpus, the taxonomy or the mappings moved. Nothing was written."""


@transaction.atomic
def apply_policy_area_plan(
    plan: PolicyAreaPlan, *, expect_plan_sha256: str, actor: Any = None
) -> PolicyAreaApplyResult:
    """Add the missing relations, or nothing at all.

    Additive by construction: the service is only capable of adding, and this
    passes it the areas one Matter's accepted pages propose. A Matter that
    already carries an area gains nothing and raises no event, which is what
    makes a second run a genuine no-op (brief 35, 45, 51).
    """
    expected = (expect_plan_sha256 or "").strip().lower()
    if plan.digest != expected:
        raise PolicyAreaPlanChanged(
            f"Plan digest {plan.digest[:16]}… does not match the approved "
            f"{expected[:16] or '(none)'}…. Nothing was written."
        )

    by_matter: dict[UUID, list[LinkProposal]] = {}
    for item in plan.additions:
        by_matter.setdefault(item.matter_id, []).append(item)

    areas = {
        area.id: area
        for area in PolicyArea.objects.filter(
            id__in={item.policy_area_id for item in plan.additions}, is_active=True
        )
    }

    matters_changed = 0
    relations_added = 0
    for matter_id, items in sorted(by_matter.items(), key=lambda pair: str(pair[0])):
        missing_target = [item for item in items if item.policy_area_id not in areas]
        if missing_target:
            raise PolicyAreaPlanChanged(
                f"{items[0].matter_reference}: a proposed PolicyArea is gone or "
                "inactive. Nothing was written."
            )
        matter = Matter.objects.select_for_update().get(pk=matter_id)
        added = add_source_derived_policy_areas(
            matter=matter,
            policy_areas=[areas[item.policy_area_id] for item in items],
            actor=actor,
            provenance={
                "source": "ONENOTE_SECTION",
                "mapping_version": plan.mapping_version,
                "capture_sha256": plan.capture_sha256,
                "rules": sorted({item.rule_id for item in items}),
                "source_page_ids": sorted({str(item.source_page_id) for item in items}),
            },
        )
        if added:
            matters_changed += 1
            relations_added += len(added)

    return PolicyAreaApplyResult(
        matters_changed=matters_changed,
        relations_added=relations_added,
        mapping_version=plan.mapping_version,
        plan_sha256=plan.digest,
    )
