"""Turning source text into canonical records — deterministically or not at all.

Three lookups live here: the owner name, the counterparty organisation, and the
historical status label. All three follow the same rule, and it is the rule the
whole migration rests on:

    an exact, unambiguous match, or nothing.

No fuzzy matching. No "closest name". No creating a user from a first name, and
no creating an Organisation from a spelling variant. The register contains
``MKM`` and ``Majandus- ja Kommunikatsiooniministeerium`` for the same ministry
across different years, and it contains ``Keskkonnaministeerium`` and
``Kliimaministeerium``, which look equally similar and are *not* the same body —
one of those pairs is a rename and the other is a genuine change of remit. A
similarity score cannot tell them apart, and a wrong answer here silently
misattributes a decade of advocacy.

When a lookup fails, the raw text survives on the source reference, the
canonical field is left null, and the run reports what needs a mapping. An
operator supplies the answers through a reviewed mapping file. That file is
*input*, never something this code writes for itself.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from django.contrib.auth import get_user_model
from django.db.models import Q

from app.core.text import normalize_for_matching
from app.organisations.models import Organisation
from app.workflow.models import LegacyStatusMapping, StageVocabulary, resolve_legacy_status


class MappingFileError(Exception):
    """The operator's mapping file cannot be read or does not say what it must."""


@dataclass
class MappingTables:
    """Reviewed answers supplied by an operator, not inferred by the importer.

    Keys are normalised the same way names are, so the file does not have to
    reproduce a source's exact casing and spacing to be useful. Values are
    identifiers — a user's ``upn`` or an Organisation's registry code or name —
    and an entry that does not resolve is an error rather than a silent skip:
    a mapping file with a typo must fail loudly, or it will look like the
    mapping simply had no effect.
    """

    owners: dict[str, str] = field(default_factory=dict)
    organisations: dict[str, str] = field(default_factory=dict)
    record_modes: dict[str, str] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> MappingTables:
        return cls()

    @classmethod
    def load(cls, path: str | Path | None) -> MappingTables:
        if path is None:
            return cls.empty()
        file_path = Path(path)
        if not file_path.exists():
            raise MappingFileError(f"Mapping file not found: {file_path}")

        text = file_path.read_text(encoding="utf-8")
        try:
            raw: dict[str, Any] = (
                json.loads(text) if file_path.suffix == ".json" else tomllib.loads(text)
            )
        except (json.JSONDecodeError, tomllib.TOMLDecodeError) as error:
            raise MappingFileError(f"{file_path.name}: {error}") from error

        return cls(
            owners=_normalised_section(raw, "owners", file_path),
            organisations=_normalised_section(raw, "organisations", file_path),
            record_modes=_normalised_section(raw, "record_modes", file_path),
        )


def _normalised_section(raw: dict[str, Any], key: str, path: Path) -> dict[str, str]:
    section = raw.get(key, {})
    if not isinstance(section, dict):
        raise MappingFileError(f"{path.name}: [{key}] must be a table of source value -> target.")
    return {normalize_for_matching(str(k)): str(v) for k, v in section.items()}


@dataclass(frozen=True)
class Resolution:
    """The outcome of one lookup, with how it was reached."""

    value: Any
    method: str  # "exact" | "alias" | "mapping" | "unresolved" | "ambiguous" | "blank"

    @property
    def resolved(self) -> bool:
        return self.value is not None

    @property
    def needs_mapping(self) -> bool:
        return self.method in {"unresolved", "ambiguous"}


UNRESOLVED = Resolution(value=None, method="unresolved")
BLANK = Resolution(value=None, method="blank")
AMBIGUOUS = Resolution(value=None, method="ambiguous")


def resolve_owner(raw_name: str, mappings: MappingTables) -> Resolution:
    """Find the user a register first name refers to.

    The register writes ``Marko``, and some rows write ``Marko, Katre``. A first
    name is not an identity, so this resolves only when exactly one active user
    matches it outright — and an explicit mapping always wins, because a person
    decided it.
    """
    name = raw_name.strip()
    if not name:
        return BLANK

    user_model = get_user_model()
    normalized = normalize_for_matching(name)

    if (target := mappings.owners.get(normalized)) is not None:
        user = user_model.objects.filter(upn__iexact=target).first()
        if user is None:
            raise MappingFileError(
                f"Mapping file points {name!r} at {target!r}, which is not a known user."
            )
        return Resolution(value=user, method="mapping")

    # Several names in one cell is a shared file, not a person. Left for review.
    if "," in name or ";" in name or "/" in name:
        return UNRESOLVED

    candidates = list(user_model.objects.filter(is_active=True, display_name__iexact=name)[:2])
    if len(candidates) == 1:
        return Resolution(value=candidates[0], method="exact")
    if len(candidates) > 1:
        return AMBIGUOUS
    return UNRESOLVED


def resolve_organisation(raw_name: str, mappings: MappingTables) -> Resolution:
    """Find the institution a source name refers to. Never creates one.

    Conservative normalised comparison — casefolded, diacritic-stripped,
    whitespace-collapsed — is allowed because it changes spelling, not identity.
    Anything beyond that is guessing.
    """
    name = raw_name.strip()
    if not name:
        return BLANK

    normalized = normalize_for_matching(name)

    if (target := mappings.organisations.get(normalized)) is not None:
        organisation = Organisation.objects.filter(
            Q(registry_code=target) | Q(name__iexact=target)
        ).first()
        if organisation is None:
            raise MappingFileError(
                f"Mapping file points {name!r} at {target!r}, which is not a known organisation."
            )
        return Resolution(value=organisation, method="mapping")

    exact = list(Organisation.objects.filter(normalized_name=normalized)[:2])
    if len(exact) == 1:
        return Resolution(value=exact[0], method="exact")
    if len(exact) > 1:
        return AMBIGUOUS

    # A reviewed alias is a person's recorded decision that two names are the
    # same body, which is exactly the evidence similarity matching lacks.
    aliased = list(Organisation.objects.filter(aliases__normalized_alias=normalized).distinct()[:2])
    if len(aliased) == 1:
        return Resolution(value=aliased[0], method="alias")
    if len(aliased) > 1:
        return AMBIGUOUS
    return UNRESOLVED


@dataclass(frozen=True)
class StatusResolution:
    """A historical `Hetkeseis` label, interpreted or explicitly not."""

    raw_label: str
    stage: StageVocabulary | None
    disposition: str
    mapping: LegacyStatusMapping | None

    @property
    def resolved(self) -> bool:
        return self.stage is not None or bool(self.disposition)

    @property
    def is_closure(self) -> bool:
        """The label says Koda stopped, not where the process stands."""
        return bool(self.disposition)


def resolve_status(raw_label: str, era: str) -> StatusResolution:
    """Interpret one status label, era first.

    Delegates to the Stage-0 era-aware mapping, so a label that meant one thing
    in 2024 and another in 2026 stays two facts. An unknown label resolves to
    nothing and **never** creates a StageVocabulary row: a status vocabulary
    that grows itself from whatever a spreadsheet contained is not a controlled
    vocabulary.
    """
    label = raw_label.strip()
    if not label:
        return StatusResolution(raw_label="", stage=None, disposition="", mapping=None)

    mapping = resolve_legacy_status(label, era)
    if mapping is None:
        return StatusResolution(raw_label=label, stage=None, disposition="", mapping=None)
    return StatusResolution(
        raw_label=label,
        stage=mapping.stage,
        disposition=mapping.disposition,
        mapping=mapping,
    )
