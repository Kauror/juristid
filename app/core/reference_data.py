"""What the reviewed baseline says, what the database holds, and the gap.

Reference data is the product's public vocabulary: the nine policy areas Koda
publishes, and the institutions its work is addressed to. Both are governed —
changed by code review and a migration, never by a text field somebody edits at
2am — and both were, until this module, invisible to an operator until something
downstream behaved oddly. Production reported itself *ready* while holding zero
organisations and zero policy areas, which is the blind spot this closes.

Three verbs, and the split between them is the whole design.

``plan`` reads and decides. It writes nothing, ever, and produces a digest over
exactly the changes it proposes.

``apply`` takes a digest a person read and performs precisely those changes, in
one transaction, refusing if the database moved underneath. It only ever *adds*:
a missing institution, a missing reviewed alias. It never renames, retypes,
re-codes, merges, moves an alias between institutions, or deactivates anything —
because every one of those silently rewrites an identity decision somebody made,
and an additive mistake is recoverable where an overwrite is not.

``verify`` asks whether the baseline is actually intact, and is what deployment
readiness leans on.

**Policy areas are read here and never written here.** They arrive through
``taxonomy/0002_reference_policy_areas``, reviewed as a migration. A second
write path for the same rows would mean two answers to "where did this
vocabulary come from", and the migration would stop being the record.

**Nothing here matches on similarity.** Institutions resolve by normalised exact
name and by reviewed alias, the same comparison
``app.organisations.services`` uses and for the same reason: ``Keskkonna-`` and
``Kliimaministeerium`` score highly against each other and are different bodies
with different remits. Where a name resolves to two rows, this reports
``AMBIGUOUS`` and stops. Deciding which one was meant is a person's job.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction

from app.core.text import normalize_for_matching

# -- outcome vocabularies ---------------------------------------------------
#
# Small closed sets rather than booleans, because "missing" and "conflicting"
# call for opposite operator actions and a boolean would flatten them.

#: A reviewed policy area, measured against the database.
AREA_PRESENT = "PRESENT"
AREA_MISSING = "MISSING"
AREA_CONFLICT = "CONFLICT"

#: A reviewed institution, measured against the database.
ORG_PRESENT = "PRESENT"
ORG_CREATE = "CREATE"
ORG_CONFLICT = "CONFLICT"


class ReferenceDataConflict(Exception):
    """The database disagrees with the reviewed baseline in a way only a person may settle."""


class ReferencePlanChanged(Exception):
    """The plan recomputed at apply time is not the plan that was approved."""


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyAreaFinding:
    key: str
    name: str
    action: str
    detail: str = ""

    def digest_row(self) -> dict[str, Any]:
        return {
            "kind": "policy_area",
            "key": self.key,
            "name": self.name,
            "type": "",
            "aliases": [],
            "action": self.action,
        }


@dataclass(frozen=True)
class OrganisationFinding:
    name: str
    organisation_type: str
    action: str
    #: Reviewed aliases this institution should carry and does not yet. Empty on
    #: a row that is already complete, which is what makes a second apply a no-op.
    aliases_to_add: tuple[str, ...] = field(default_factory=tuple)
    #: Reviewed aliases another institution has already claimed. Never taken —
    #: moving an alias would rewrite an earlier decision — so they are reported
    #: and left where they are.
    aliases_claimed_elsewhere: tuple[str, ...] = field(default_factory=tuple)
    detail: str = ""

    def digest_row(self) -> dict[str, Any]:
        return {
            "kind": "organisation",
            "key": normalize_for_matching(self.name),
            "name": self.name,
            "type": self.organisation_type,
            "aliases": sorted(self.aliases_to_add),
            "action": self.action,
        }


@dataclass(frozen=True)
class ReferencePlan:
    policy_area_version: str
    organisation_version: str
    policy_areas: tuple[PolicyAreaFinding, ...]
    organisations: tuple[OrganisationFinding, ...]

    # -- the numbers an operator reads -------------------------------------

    @property
    def areas_present(self) -> tuple[PolicyAreaFinding, ...]:
        return tuple(f for f in self.policy_areas if f.action == AREA_PRESENT)

    @property
    def areas_missing(self) -> tuple[PolicyAreaFinding, ...]:
        return tuple(f for f in self.policy_areas if f.action == AREA_MISSING)

    @property
    def areas_conflicting(self) -> tuple[PolicyAreaFinding, ...]:
        return tuple(f for f in self.policy_areas if f.action == AREA_CONFLICT)

    @property
    def organisations_present(self) -> tuple[OrganisationFinding, ...]:
        return tuple(f for f in self.organisations if f.action == ORG_PRESENT)

    @property
    def organisations_to_create(self) -> tuple[OrganisationFinding, ...]:
        return tuple(f for f in self.organisations if f.action == ORG_CREATE)

    @property
    def organisations_conflicting(self) -> tuple[OrganisationFinding, ...]:
        return tuple(f for f in self.organisations if f.action == ORG_CONFLICT)

    @property
    def aliases_to_add(self) -> int:
        return sum(len(f.aliases_to_add) for f in self.organisations)

    @property
    def alias_conflicts(self) -> int:
        return sum(len(f.aliases_claimed_elsewhere) for f in self.organisations)

    @property
    def conflicts(self) -> tuple[str, ...]:
        """Every reason a person has to look before anything is applied."""
        reasons = [f"valdkond {f.key}: {f.detail}" for f in self.areas_conflicting]
        reasons += [f"{f.name}: {f.detail}" for f in self.organisations_conflicting]
        reasons += [
            f"{f.name}: nimekuju {alias!r} kuulub juba teisele organisatsioonile"
            for f in self.organisations
            for alias in f.aliases_claimed_elsewhere
        ]
        return tuple(reasons)

    # -- digest ------------------------------------------------------------

    def digest(self) -> str:
        """SHA-256 over exactly what this plan proposes, and nothing else.

        The manifest versions are inside the hash rather than beside it. The
        same database read under a *changed* baseline is a different plan even
        when the proposed rows happen to coincide, and an operator who approved
        a digest under one vocabulary must not be able to spend it under
        another.

        Findings that propose nothing are in the hash too: a plan that says
        "everything is present" and a plan that says "four are missing" have to
        be different digests, or `apply` could be handed the approval from a
        state that no longer holds.
        """
        body = {
            "policy_area_version": self.policy_area_version,
            "organisation_version": self.organisation_version,
            "rows": sorted(
                (
                    row
                    for row in (
                        *(f.digest_row() for f in self.policy_areas),
                        *(f.digest_row() for f in self.organisations),
                    )
                ),
                key=lambda row: (row["kind"], row["key"], row["name"]),
            ),
        }
        encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


def _resolve_policy_areas() -> tuple[PolicyAreaFinding, ...]:
    from app.taxonomy.models import PolicyArea
    from app.taxonomy.reference_data import REFERENCE_POLICY_AREAS_V1

    rows = list(PolicyArea.objects.all())
    by_key: dict[str, list[Any]] = {}
    for row in rows:
        by_key.setdefault(row.key, []).append(row)

    findings: list[PolicyAreaFinding] = []
    for area in REFERENCE_POLICY_AREAS_V1:
        matches = by_key.get(area.key, [])
        if not matches:
            findings.append(PolicyAreaFinding(key=area.key, name=area.name_et, action=AREA_MISSING))
            continue
        # `key` is unique in the schema, so more than one is impossible; the
        # interesting conflicts are a changed name, a deactivated row, and a
        # second row that took the name under a different key.
        current = matches[0]
        if _folded(current.name_et) != _folded(area.name_et):
            findings.append(
                PolicyAreaFinding(
                    key=area.key,
                    name=area.name_et,
                    action=AREA_CONFLICT,
                    detail=(
                        f"andmebaasis on nimi {current.name_et!r}, "
                        f"üle vaadatud nimi on {area.name_et!r}"
                    ),
                )
            )
            continue
        if not current.is_active:
            findings.append(
                PolicyAreaFinding(
                    key=area.key,
                    name=area.name_et,
                    action=AREA_CONFLICT,
                    detail="rida on deaktiveeritud; alusvaldkond peab olema aktiivne",
                )
            )
            continue
        duplicates = [
            other
            for other in rows
            if other.key != area.key
            and other.is_active
            and _folded(other.name_et) == _folded(area.name_et)
        ]
        if duplicates:
            findings.append(
                PolicyAreaFinding(
                    key=area.key,
                    name=area.name_et,
                    action=AREA_CONFLICT,
                    detail=(
                        f"sama nime kannab ka võti {duplicates[0].key!r}; "
                        "nimepõhine sidumine muutub mitmetimõistetavaks"
                    ),
                )
            )
            continue
        findings.append(PolicyAreaFinding(key=area.key, name=area.name_et, action=AREA_PRESENT))
    return tuple(findings)


def _folded(value: str) -> str:
    return " ".join(value.split()).casefold()


def _resolve_organisations() -> tuple[OrganisationFinding, ...]:
    from app.organisations.models import Organisation, OrganisationAlias
    from app.organisations.reference_data import PUBLIC_REFERENCE_ORGANISATIONS

    findings: list[OrganisationFinding] = []
    for entry in PUBLIC_REFERENCE_ORGANISATIONS:
        normalized = normalize_for_matching(entry.name)
        by_name = list(Organisation.objects.filter(normalized_name=normalized)[:3])
        if len(by_name) > 1:
            findings.append(
                OrganisationFinding(
                    name=entry.name,
                    organisation_type=entry.organisation_type,
                    action=ORG_CONFLICT,
                    detail=(
                        f"{len(by_name)} organisatsiooni kannavad seda nime; "
                        "kumba silmas peeti, otsustab inimene"
                    ),
                )
            )
            continue

        organisation = by_name[0] if by_name else None
        if organisation is None:
            # An alias may already point at the institution under a different
            # canonical spelling. Creating a second row beside it would be the
            # duplicate this whole module exists to avoid.
            aliased = list(
                Organisation.objects.filter(aliases__normalized_alias=normalized).distinct()[:3]
            )
            if len(aliased) > 1:
                findings.append(
                    OrganisationFinding(
                        name=entry.name,
                        organisation_type=entry.organisation_type,
                        action=ORG_CONFLICT,
                        detail=(
                            f"see nimi on nimekujuna {len(aliased)} organisatsioonil; "
                            "kumba silmas peeti, otsustab inimene"
                        ),
                    )
                )
                continue
            organisation = aliased[0] if aliased else None

        to_add: list[str] = []
        claimed: list[str] = []
        for alias in entry.aliases:
            alias_norm = normalize_for_matching(alias)
            holders = OrganisationAlias.objects.filter(normalized_alias=alias_norm)
            if organisation is not None:
                if holders.filter(organisation=organisation).exists():
                    continue
                if holders.exclude(organisation=organisation).exists():
                    claimed.append(alias)
                    continue
                to_add.append(alias)
                continue
            # The institution itself is missing. An alias already held by some
            # other row still cannot be taken, so say so now rather than at
            # apply time.
            if holders.exists():
                claimed.append(alias)
            else:
                to_add.append(alias)

        findings.append(
            OrganisationFinding(
                name=entry.name,
                organisation_type=entry.organisation_type,
                action=ORG_PRESENT if organisation is not None else ORG_CREATE,
                aliases_to_add=tuple(to_add),
                aliases_claimed_elsewhere=tuple(claimed),
                detail=(
                    ""
                    if organisation is None or _folded(organisation.name) == _folded(entry.name)
                    else f"leitud nimekuju kaudu reana {organisation.name!r}; nime ei muudeta"
                ),
            )
        )
    return tuple(findings)


def build_reference_plan() -> ReferencePlan:
    """Read everything, decide everything, write nothing."""
    from app.organisations.reference_data import REFERENCE_ORGANISATION_VERSION
    from app.taxonomy.reference_data import REFERENCE_POLICY_AREA_VERSION

    return ReferencePlan(
        policy_area_version=REFERENCE_POLICY_AREA_VERSION,
        organisation_version=REFERENCE_ORGANISATION_VERSION,
        policy_areas=_resolve_policy_areas(),
        organisations=_resolve_organisations(),
    )


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReferenceApplyResult:
    organisations_created: tuple[str, ...]
    aliases_added: tuple[str, ...]
    plan_sha256: str


@transaction.atomic
def apply_reference_plan(*, expected_sha256: str) -> ReferenceApplyResult:
    """Create the missing reviewed institutions and their missing aliases.

    Recomputes the plan inside the transaction and compares it against the
    digest that was approved. That is the whole guard: between the operator
    reading a plan and running this, somebody may have created one of these
    institutions by hand, and applying a stale plan would produce the duplicate
    the plan was written to prevent.

    Refuses outright when the plan reports any conflict. A conflict is a
    question about identity, and there is no partial application of a baseline
    a person has not finished reviewing.
    """
    from app.organisations.models import AliasType, Organisation, OrganisationAlias

    plan = build_reference_plan()
    actual = plan.digest()
    if actual != expected_sha256:
        raise ReferencePlanChanged(
            "The reference-data plan changed since it was approved "
            f"({expected_sha256[:16]}… → {actual[:16]}…). Re-run plan and read it again."
        )
    if plan.conflicts:
        raise ReferenceDataConflict(
            "The reviewed baseline conflicts with what the database holds:\n"
            + "\n".join(f"  - {reason}" for reason in plan.conflicts)
        )

    created: list[str] = []
    aliases: list[str] = []

    for finding in plan.organisations:
        if finding.action == ORG_CREATE:
            organisation = Organisation.objects.create(
                name=finding.name, organisation_type=finding.organisation_type
            )
            created.append(finding.name)
        else:
            # Resolved exactly as the plan did, including through an alias. The
            # existing row's name, type, registry code, validity and predecessor
            # are read and never written.
            organisation = _existing_for(finding.name)
            if organisation is None:  # pragma: no cover - the digest guard precedes this
                raise ReferencePlanChanged(
                    f"{finding.name} disappeared between planning and applying."
                )

        for alias in finding.aliases_to_add:
            OrganisationAlias.objects.create(
                organisation=organisation, alias=alias, alias_type=AliasType.ABBREVIATION
            )
            aliases.append(f"{finding.name} ← {alias}")

    return ReferenceApplyResult(
        organisations_created=tuple(created),
        aliases_added=tuple(aliases),
        plan_sha256=actual,
    )


def _existing_for(name: str) -> Any:
    from app.organisations.models import Organisation

    normalized = normalize_for_matching(name)
    exact = list(Organisation.objects.filter(normalized_name=normalized)[:2])
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None
    aliased = list(Organisation.objects.filter(aliases__normalized_alias=normalized).distinct()[:2])
    return aliased[0] if len(aliased) == 1 else None


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerifyReport:
    problems: tuple[str, ...]
    policy_areas_present: int
    policy_areas_expected: int
    organisations_present: int
    organisations_expected: int

    @property
    def ok(self) -> bool:
        return not self.problems


def verify_reference_data() -> VerifyReport:
    """Is the reviewed baseline actually intact? Read-only.

    Deliberately stricter than ``plan``: a plan describes what *would* be done,
    while this answers the question deployment readiness needs — whether the
    vocabulary a deployed feature depends on is really there and really
    unambiguous. Missing aliases count as problems here, because an alias that
    was reviewed and then lost means the register stops resolving names it used
    to resolve.
    """
    from app.organisations.reference_data import PUBLIC_REFERENCE_ORGANISATIONS
    from app.taxonomy.reference_data import REFERENCE_POLICY_AREAS_V1

    plan = build_reference_plan()
    problems: list[str] = []

    for area in plan.areas_missing:
        problems.append(
            f"Valdkond puudub: {area.name} ({area.key}). "
            "Käivita migratsioonid — vokabulaar tuleb taxonomy/0002 kaudu."
        )
    for area in plan.areas_conflicting:
        problems.append(f"Valdkond {area.key}: {area.detail}")

    for organisation in plan.organisations_to_create:
        problems.append(
            f"Avalik organisatsioon puudub: {organisation.name}. "
            "Käivita: manage.py reference_data plan, seejärel apply."
        )
    for organisation in plan.organisations_conflicting:
        problems.append(f"Organisatsioon {organisation.name}: {organisation.detail}")
    for organisation in plan.organisations:
        for alias in organisation.aliases_to_add:
            problems.append(
                f"Nimekuju puudub: {organisation.name} ← {alias}. "
                "Käivita: manage.py reference_data apply."
            )
        for alias in organisation.aliases_claimed_elsewhere:
            problems.append(
                f"Nimekuju {alias!r} kuulub teisele organisatsioonile kui {organisation.name}. "
                "Nimekuju ei tõsteta automaatselt ümber."
            )

    # One canonical name may only mean one institution inside the reviewed set.
    # A duplicate here would make every exact match in the product ambiguous,
    # and `plan` only sees it from the manifest's side.
    seen: dict[str, str] = {}
    for entry in PUBLIC_REFERENCE_ORGANISATIONS:
        normalized = normalize_for_matching(entry.name)
        if normalized in seen:
            problems.append(
                f"Alusandmestik ise on vastuoluline: {entry.name} ja {seen[normalized]} "
                "taanduvad samale normaliseeritud nimele."
            )
        seen[normalized] = entry.name

    return VerifyReport(
        problems=tuple(problems),
        policy_areas_present=len(plan.areas_present),
        policy_areas_expected=len(REFERENCE_POLICY_AREAS_V1),
        organisations_present=len(plan.organisations_present),
        organisations_expected=len(PUBLIC_REFERENCE_ORGANISATIONS),
    )


def readiness_problems(report: VerifyReport | None = None) -> list[str]:
    """The reference-data half of deployment readiness.

    Separate from `verify_reference_data` only in framing: readiness wants
    sentences an operator can act on without knowing this module exists, which
    is why the message names the command rather than the function.

    Not a Django system check, deliberately. A system check runs in every
    isolated unit test and in every developer's shell, and a hard requirement
    for production reference data there would fail thousands of tests that have
    no business caring. The requirement belongs where the environment is known
    to hold real data.
    """
    from django.conf import settings

    if not settings.REAL_DATA_ALLOWED:
        return []

    if report is None:
        report = verify_reference_data()
    if report.ok:
        return []

    problems: list[str] = []
    missing_areas = report.policy_areas_expected - report.policy_areas_present
    if missing_areas > 0:
        problems.append(
            f"Reference data: {missing_areas} of {report.policy_areas_expected} reviewed "
            "policy areas are missing. The vocabulary arrives with the schema — "
            "run `manage.py migrate`."
        )
    missing_orgs = report.organisations_expected - report.organisations_present
    if missing_orgs > 0:
        problems.append(
            f"Reference data: {missing_orgs} of {report.organisations_expected} reviewed "
            "public organisations are missing. Run `manage.py reference_data plan`, read it, "
            "then `manage.py reference_data apply --expect-plan-sha256 <digest>`."
        )
    remaining = [
        problem
        for problem in report.problems
        if not problem.startswith(("Valdkond puudub", "Avalik organisatsioon puudub"))
    ]
    if remaining:
        problems.append(
            "Reference data: the reviewed baseline does not match the database. "
            "Run `manage.py reference_data verify` for the detail:\n"
            + "\n".join(f"      · {line}" for line in remaining[:5])
            + ("\n      · …" if len(remaining) > 5 else "")
        )
    return problems
