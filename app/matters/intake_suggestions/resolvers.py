"""The catalogues the rules resolve against, loaded once per analysis.

Organisation names and aliases come from the reference data the department
maintains, policy areas from the governed vocabulary. Both are read here and
handed to the pure analysis as plain values, so the rule engine itself never
queries anything and an evaluation run can hand it a frozen catalogue.

Nothing is created and nothing is merged. An organisation is recognised only
by its exact canonical name or a recorded alias, with an Estonian case
ending allowed on the end (`textscan.organisation_pattern`); a policy area
only by the stable ``key`` a rule names, and only if that key is offered
today. A rule keyed on an area that has since been retired is reported as a
diagnostic and ignored — never mapped onto whatever replaced it
(app/taxonomy/reference_data.py, docs/adr/0029).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.matters.intake_suggestions import vocabulary as vocab
from app.matters.intake_suggestions.textscan import OrganisationPattern, organisation_pattern
from app.organisations.models import AliasType, Organisation, OrganisationAlias, OrganisationType
from app.taxonomy.models import PolicyArea
from app.taxonomy.vocabulary import selectable_policy_areas


@dataclass(frozen=True)
class OrganisationEntry:
    id: Any
    name: str
    organisation_type: str

    @property
    def is_chamber(self) -> bool:
        """Koda itself. It receives ministry letters; it does not send them."""
        return self.organisation_type == OrganisationType.CHAMBER


@dataclass(frozen=True)
class OrganisationCatalogue:
    patterns: tuple[OrganisationPattern, ...]
    entries: dict[Any, OrganisationEntry]

    @classmethod
    def empty(cls) -> OrganisationCatalogue:
        return cls(patterns=(), entries={})


def load_organisation_catalogue() -> OrganisationCatalogue:
    """Every organisation and alias, compiled for scanning. Two queries."""
    entries: dict[Any, OrganisationEntry] = {}
    patterns: list[OrganisationPattern] = []
    rows = Organisation.objects.order_by("name").values_list("id", "name", "organisation_type")
    for pk, name, organisation_type in rows:
        entries[pk] = OrganisationEntry(id=pk, name=name, organisation_type=organisation_type)
        pattern = organisation_pattern(
            organisation_id=pk,
            display=name,
            form=name,
            is_abbreviation=len(name.replace(".", "")) <= vocab.ABBREVIATION_LENGTH,
        )
        if pattern is not None:
            patterns.append(pattern)
    aliases = OrganisationAlias.objects.order_by("alias").values_list(
        "organisation_id", "alias", "alias_type"
    )
    for organisation_id, alias, alias_type in aliases:
        entry = entries.get(organisation_id)
        if entry is None:
            continue
        pattern = organisation_pattern(
            organisation_id=organisation_id,
            display=entry.name,
            form=alias,
            is_abbreviation=(
                alias_type == AliasType.ABBREVIATION
                or len(alias.replace(".", "")) <= vocab.ABBREVIATION_LENGTH
            ),
        )
        if pattern is not None:
            patterns.append(pattern)
    # Longest forms first, so «Majandus- ja Kommunikatsiooniministeerium»
    # is matched as itself before «Kommunikatsiooniministeerium» could be.
    patterns.sort(key=lambda pattern: (-len(pattern.form), pattern.display))
    return OrganisationCatalogue(patterns=tuple(patterns), entries=entries)


def load_policy_areas() -> dict[str, PolicyArea]:
    """The offered Valdkonnad by stable key. One query."""
    return {area.key: area for area in selectable_policy_areas()}


def rule_diagnostics(policy_areas: dict[str, PolicyArea]) -> tuple[str, ...]:
    """Rules that name an area the vocabulary no longer offers.

    A retired key is a maintenance fact, not a runtime error: the rule is
    simply inert until somebody re-keys it, and the panel says nothing about
    that area rather than guessing which current one was meant.
    """
    missing = sorted(key for key in vocab.AREA_RULES if key not in policy_areas)
    return tuple(f"Valdkonna reegel viitab võtmele, mida ei pakuta: {key}" for key in missing)
