"""Turning source text into canonical records — deterministically or not at all.

Three lookups live here: the owner name, the counterparty organisation, and the
historical status label. All three follow the same rule, and it is the rule the
whole migration rests on:

    an exact, unambiguous match, or nothing.

No fuzzy matching. No "closest name". No creating a user from a name, and
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
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from django.db.models import Q

from app.accounts.models import User
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


#: How an owner lookup succeeded. Recorded on every assignment the backfill
#: makes, so a reviewer can tell an attested mapping from an inference the code
#: drew for itself.
METHOD_MAPPING = "mapping"
METHOD_EXACT = "exact"
METHOD_GIVEN_NAME = "given_name"

#: Every way an owner lookup can fail, kept apart because they need different
#: answers. ``multi_person`` needs a decision about who actually holds the file;
#: ``ambiguous`` needs a mapping line; ``unresolved`` may just be somebody who
#: never had an account.
METHOD_BLANK = "blank"
METHOD_UNRESOLVED = "unresolved"
METHOD_AMBIGUOUS = "ambiguous"
METHOD_MULTI_PERSON = "multi_person"

#: Owner methods that mean a person was identified beyond doubt.
DETERMINISTIC_OWNER_METHODS: frozenset[str] = frozenset(
    {METHOD_MAPPING, METHOD_EXACT, METHOD_GIVEN_NAME}
)


@dataclass(frozen=True)
class Resolution:
    """The outcome of one lookup, with how it was reached."""

    value: Any
    method: str  # the METHOD_* constants above; "alias" belongs to organisations

    @property
    def resolved(self) -> bool:
        return self.value is not None

    @property
    def needs_mapping(self) -> bool:
        return self.method in {METHOD_UNRESOLVED, METHOD_AMBIGUOUS, METHOD_MULTI_PERSON}


UNRESOLVED = Resolution(value=None, method=METHOD_UNRESOLVED)
BLANK = Resolution(value=None, method=METHOD_BLANK)
AMBIGUOUS = Resolution(value=None, method=METHOD_AMBIGUOUS)
MULTI_PERSON = Resolution(value=None, method=METHOD_MULTI_PERSON)

#: Punctuation that means the cell names more than one person. The register
#: writes ``Kadri, Mart`` and ``Kadri / Mart`` for a file two people shared.
_MULTI_PERSON_MARKERS: tuple[str, ...] = (",", ";", "/", "&", "+")

#: Conjunctions, compared as whole normalised tokens. No Estonian personal name
#: is the single token ``ja``, so this cannot swallow a real name — and a cell
#: reading ``Kadri ja Mart`` is exactly as shared as ``Kadri, Mart``.
_MULTI_PERSON_WORDS: frozenset[str] = frozenset({"ja", "ning", "and"})


@dataclass(frozen=True)
class KnownPeople:
    """Every user an owner lookup may resolve to, indexed for exact comparison.

    Loaded once per run rather than queried per row: the department is a handful
    of people and the register is thousands of rows, so the whole table costs
    less than one query per row — and it makes the ambiguity test exact rather
    than a ``LIMIT 2`` that cannot tell two candidates from twenty.

    **Inactive users are included, deliberately.** A 2014 file was owned by
    whoever owned it, and that person may have left years ago; refusing to name
    them would leave part of the archive ownerless while the source says
    otherwise. They stay out of persona selection and out of ordinary owner
    choice — a former colleague may be named in history and must not be offered
    as somebody to hand new work to (Stage-2F brief 6).

    Ambiguity is judged against *everyone* for the same reason: a departed
    ``Kadri`` makes a present-day ``Kadri`` ambiguous, and looking at only the
    active half would turn that unsafe match into a confident one.
    """

    by_full_name: dict[str, tuple[User, ...]]
    by_given_name: dict[str, tuple[User, ...]]
    by_upn: dict[str, User]

    @classmethod
    def load(cls) -> KnownPeople:
        return cls.of(User.objects.all())

    @classmethod
    def of(cls, users: Iterable[User]) -> KnownPeople:
        full: dict[str, list[User]] = {}
        given: dict[str, list[User]] = {}
        upns: dict[str, User] = {}
        for user in users:
            upns[user.upn.strip().casefold()] = user
            key = normalize_for_matching(user.display_name)
            if not key:
                continue
            full.setdefault(key, []).append(user)
            given.setdefault(key.split(" ")[0], []).append(user)
        return cls(
            by_full_name={key: tuple(value) for key, value in full.items()},
            by_given_name={key: tuple(value) for key, value in given.items()},
            by_upn=upns,
        )


def names_more_than_one_person(raw_name: str) -> bool:
    """Whether one source cell unmistakably names several people."""
    if any(marker in raw_name for marker in _MULTI_PERSON_MARKERS):
        return True
    tokens = normalize_for_matching(raw_name).split(" ")
    return any(token in _MULTI_PERSON_WORDS for token in tokens)


def resolve_owner(
    raw_name: str, mappings: MappingTables, people: KnownPeople | None = None
) -> Resolution:
    """Find the user a register owner cell refers to. Deterministically or not.

    The register's ``VASTUTAJA`` column holds a **first name** — ``Kadri`` — and
    an account holds a full one — ``Kadri Näidis``. The first version of this
    function compared the two for equality, so the register's commonest shape
    matched nothing at all and current matters imported with no owner even
    though the source named one on almost every row (Stage-2F brief 3).

    Three ways in, in order, and nothing else:

    1. a reviewed mapping, because a person decided it;
    2. the whole display name, compared after normalisation;
    3. a lone given name, **only** where exactly one known person carries it.

    There is no fourth. No edit distance, no closest match, no surname guessed
    from an initial, no account invented from a name, and no cell naming two
    people resolved to one of them: misattributing somebody else's advocacy is
    not the kind of error a reviewer catches afterwards.
    """
    name = raw_name.strip()
    if not name:
        return BLANK

    directory = people if people is not None else KnownPeople.load()
    normalized = normalize_for_matching(name)

    # 1. A reviewed answer outranks everything, including the multi-person
    #    refusal below: an operator who writes down who ``Kadri / Mart`` means
    #    has made exactly the decision this code refuses to make for itself.
    if (target := mappings.owners.get(normalized)) is not None:
        user = directory.by_upn.get(target.strip().casefold())
        if user is None:
            raise MappingFileError(
                f"Mapping file points {name!r} at {target!r}, which is not a known user."
            )
        return Resolution(value=user, method=METHOD_MAPPING)

    # 2. Several names in one cell is a shared file, not a person.
    if names_more_than_one_person(name):
        return MULTI_PERSON

    # 3. The whole name, normalised. Casing, spacing and diacritics may differ;
    #    identity may not.
    exact = directory.by_full_name.get(normalized, ())
    if len(exact) == 1:
        return Resolution(value=exact[0], method=METHOD_EXACT)
    if len(exact) > 1:
        return AMBIGUOUS

    # 4. One personal-name token, and exactly one person carrying it. Two
    #    colleagues whose names begin alike make this unanswerable, and
    #    unanswerable is the right answer.
    if " " in normalized:
        return UNRESOLVED
    candidates = directory.by_given_name.get(normalized, ())
    if len(candidates) == 1:
        return Resolution(value=candidates[0], method=METHOD_GIVEN_NAME)
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
