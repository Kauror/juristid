"""Which institutions the organisation key calls one, and which keys are stale.

Read-only, always. It answers the question an operator has before anything is
merged — *which rows would a person have to look at?* — and it stops there.
Merging is a decision about a decade of filing (`app.organisations.services`
module docstring), so it is never taken here, and the database is not given a
unique constraint on the name either: that is the step after a person has
merged what this reports (ENG-045).

Three findings:

* **By name.** Two or more institutions whose names fold to one key. Typing
  that name is refused as ambiguous until one of them is merged away.
* **By alias.** A key that more than one institution answers to through a
  recorded alias — or through an alias on one and the name of another. The
  resolver refuses the first; the second makes the alias unreachable, because
  a name match wins.
* **Stale keys.** Rows whose stored key the current fold would not produce:
  written under the earlier fold and not yet recomputed. The organisations
  migration recomputes them; an alias the migration had to leave alone (it
  would have collided with its own institution's other spelling) stays here.

Every institution is listed with how many records point at it, so whoever
merges can see which row carries the filing.
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from django.db.models import Count

from app.core.text import normalize_organisation_name
from app.organisations.models import Organisation, OrganisationAlias


@dataclass(frozen=True)
class CollidingOrganisation:
    pk: Any
    name: str
    #: ``"Model.field" -> count`` for every relation that points at the row.
    references: dict[str, int]

    @property
    def reference_total(self) -> int:
        return sum(self.references.values())


@dataclass(frozen=True)
class Collision:
    key: str
    organisations: tuple[CollidingOrganisation, ...]


@dataclass(frozen=True)
class StaleKey:
    kind: str
    pk: Any
    text: str
    stored: str
    expected: str


@dataclass
class IdentityReport:
    organisations: int = 0
    aliases: int = 0
    by_name: list[Collision] = field(default_factory=list)
    by_alias: list[Collision] = field(default_factory=list)
    stale: list[StaleKey] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.by_name or self.by_alias or self.stale)


def visible(text: str) -> str:
    """``text`` with every invisible format character written out as ``<U+XXXX>``.

    Two rows that print identically are exactly what this report is about, so it
    must not print them identically.
    """
    return "".join(f"<U+{ord(ch):04X}>" if unicodedata.category(ch) == "Cf" else ch for ch in text)


def _references(pks: set[Any]) -> dict[Any, dict[str, int]]:
    """How many rows of every other table point at each institution.

    Read from the model graph rather than listed by hand, so a relation added
    later is counted without anybody remembering to add it here. The
    institution's own aliases are part of it rather than references to it.
    """
    counts: dict[Any, dict[str, int]] = {pk: {} for pk in pks}
    if not pks:
        return counts
    for relation in Organisation._meta.related_objects:
        if relation.related_model is OrganisationAlias:
            continue
        # A many-to-many with a model of its own in between is counted once,
        # through that model's foreign key, which is a relation of its own.
        through = getattr(relation, "through", None)
        if relation.many_to_many and through is not None and not through._meta.auto_created:
            continue
        model = relation.related_model
        path = relation.field.name
        label = f"{model.__name__}.{path}"
        rows = (
            model._base_manager.filter(**{f"{path}__in": pks})
            .values(path)
            .annotate(total=Count("pk", distinct=True))
            .order_by()
        )
        for row in rows:
            counts[row[path]][label] = counts[row[path]].get(label, 0) + row["total"]
    return counts


def _collisions(groups: dict[str, set[Any]], names: dict[Any, str]) -> list[Collision]:
    colliding = {key: pks for key, pks in groups.items() if len(pks) > 1}
    references = _references({pk for pks in colliding.values() for pk in pks})
    return [
        Collision(
            key=key,
            organisations=tuple(
                CollidingOrganisation(pk=pk, name=names[pk], references=references[pk])
                for pk in sorted(pks, key=str)
            ),
        )
        for key, pks in sorted(colliding.items())
    ]


def identity_report() -> IdentityReport:
    """Everything the organisation key would call one institution. Writes nothing."""
    report = IdentityReport()
    names: dict[Any, str] = {}
    by_name: dict[str, set[Any]] = defaultdict(set)

    for pk, name, stored in Organisation.objects.values_list(
        "pk", "name", "normalized_name"
    ).iterator():
        report.organisations += 1
        names[pk] = name
        expected = normalize_organisation_name(name)
        by_name[expected].add(pk)
        if stored != expected:
            report.stale.append(StaleKey("Organisation", pk, name, stored, expected))

    by_alias: dict[str, set[Any]] = defaultdict(set)
    for pk, organisation_id, alias, stored in OrganisationAlias.objects.values_list(
        "pk", "organisation_id", "alias", "normalized_alias"
    ).iterator():
        report.aliases += 1
        expected = normalize_organisation_name(alias)
        by_alias[expected].add(organisation_id)
        if stored != expected:
            report.stale.append(StaleKey("OrganisationAlias", pk, alias, stored, expected))

    # An alias key is a collision when more than one institution answers to it,
    # counting an institution whose *name* folds to the same key.
    alias_groups = {key: holders | by_name.get(key, set()) for key, holders in by_alias.items()}

    report.by_name = _collisions(by_name, names)
    report.by_alias = _collisions(alias_groups, names)
    return report
