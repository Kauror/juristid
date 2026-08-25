"""The reviewed reference-data baseline: vocabulary, institutions, and the gates.

Two kinds of row live in the same tables — reference data that belongs in a real
deployment, and synthetic props that must never be mistaken for it. Most of what
follows pins that boundary from one side or another: the nine policy areas are
Koda's own published categories, the public institutions are the state's own
names, and the development seed is forbidden from inventing a competing
vocabulary beside either.

The rest pins the two rules that make an operator able to trust the tooling: a
plan writes nothing, and an apply only ever adds.
"""

from __future__ import annotations

import importlib
import json

import pytest
from django.apps import apps as global_apps
from django.core.management import CommandError, call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from app.core.management.commands.seed_dev_data import PROVISIONAL_POLICY_AREAS
from app.core.reference_data import (
    AREA_CONFLICT,
    AREA_MISSING,
    AREA_PRESENT,
    ORG_CREATE,
    ORG_PRESENT,
    ReferenceDataConflict,
    ReferencePlanChanged,
    apply_reference_plan,
    build_reference_plan,
    verify_reference_data,
)
from app.core.text import normalize_for_matching
from app.organisations.models import AliasType, Organisation, OrganisationAlias, OrganisationType
from app.organisations.reference_data import (
    CORE_PUBLIC_INSTITUTIONS,
    MINISTRIES,
    ORGANISATION_SOURCE_VERIFIED_ON,
    PUBLIC_REFERENCE_ORGANISATIONS,
)
from app.taxonomy.models import PolicyArea, Tag
from app.taxonomy.reference_data import (
    POLICY_AREA_SOURCE_PUBLISHER,
    POLICY_AREA_SOURCE_TITLE,
    POLICY_AREA_SOURCE_V1_TITLE,
    POLICY_AREA_SOURCE_V1_URL,
    POLICY_AREA_SOURCE_VERIFIED_ON,
    POLICY_AREA_WITHDRAWAL_SOURCE_TITLE,
    POLICY_AREA_WITHDRAWAL_VERIFIED_ON,
    REFERENCE_POLICY_AREA_KEYS,
    REFERENCE_POLICY_AREA_VERSION,
    REFERENCE_POLICY_AREAS_V1,
    REFERENCE_POLICY_AREAS_V2,
    RETIRED_POLICY_AREA_KEYS_V1,
    RETIRED_POLICY_AREA_KEYS_V2,
)

pytestmark = pytest.mark.django_db

#: Version 1.0's migration, kept addressable so the frozen-baseline guard can
#: also prove the *earlier* baseline was not edited after the fact.
MIGRATION_V1 = importlib.import_module("app.taxonomy.migrations.0002_reference_policy_areas")
MIGRATION = importlib.import_module("app.taxonomy.migrations.0003_working_policy_area_vocabulary")
#: The withdrawal, kept addressable for the same drift guard.
MIGRATION_V3 = importlib.import_module(
    "app.taxonomy.migrations.0004_retire_catchall_and_deadline_areas"
)

#: Everything ``taxonomy/0003`` seeded — the twenty-three the department named
#: in the Teema redesign review, in the order it gave them. Written out rather
#: than derived from the manifest: a test that reads the same tuple it is
#: checking would agree with any edit, including a wrong one.
SEEDED_V2 = [
    ("maksejouetus", "Maksejõuetus", 10),
    ("raamatupidamine", "Raamatupidamine", 20),
    ("intellektuaalomand", "Intellektuaalomand", 30),
    ("toetusmeetmed", "Toetusmeetmed", 40),
    ("koalitsioonilepped", "Koalitsioonilepped", 50),
    ("oigusloome", "Õigusloome", 60),
    ("energeetika", "Energeetika", 70),
    ("riigihanked", "Riigihanked", 80),
    ("haridus", "Haridus", 90),
    ("tarbijakaitse", "Tarbijakaitse", 100),
    ("alkohol-tubakas", "Alkohol, tubakas", 110),
    ("digiteemad", "Digiteemad", 120),
    ("finantsoigus-rahapesu", "Finantsõigus, rahapesu", 130),
    ("ehitus", "Ehitus", 140),
    ("arioigus", "Äriõigus", 150),
    ("valistoojoud", "Välistööjõud", 160),
    ("maksud-ja-toll", "Maksud ja toll", 170),
    ("toosuhted-tookeskkond", "Töösuhted, töökeskkond", 180),
    ("keskkond", "Keskkond", 190),
    ("muud-teemad", "Muud teemad", 200),
    ("eli-oiguse-ulevotmine", "ELi õiguse ülevõtmine", 210),
    ("olulised-tahtajad", "Olulised tähtajad", 220),
    ("arengukavad-strateegiad", "Arengukavad, strateegiad", 230),
]

#: The two the product owner withdrew on 2026-08-25, written out here for the
#: same reason as the list above. `Olulised tähtajad` is a watch list and not a
#: subject area; `Muud teemad` is the free-text `Muu` affordance a second time.
WITHDRAWN_V2 = ["muud-teemad", "olulised-tahtajad"]

#: What may be chosen today: the seeded twenty-three less the withdrawn two,
#: still in the department's order and still carrying its gaps at 200 and 220.
#: Those gaps are the point — the sequence is the one the department reviewed,
#: not a ranking to be closed up.
EXPECTED = [row for row in SEEDED_V2 if row[0] not in WITHDRAWN_V2]

#: The four names that appear in both the retired nine and the working list.
#: Their rows carry over untouched — same key, same primary key, same relations
#: — which is the whole reason those four are not in
#: `RETIRED_POLICY_AREA_KEYS_V1`.
CARRIED_OVER = ["energeetika", "riigihanked", "arioigus", "keskkond"]

#: The provisional keys that are *not* also canonical ones. `keskkond` and
#: `energeetika` appear in both lists and are the same concept under the same
#: key, so only the three that differ can tell a synthetic vocabulary from a
#: reviewed one — and a database holding both `maksundus` and `maksud` is the
#: exact failure this guards.
PROVISIONAL_KEYS = {key for key, _ in PROVISIONAL_POLICY_AREAS} - set(REFERENCE_POLICY_AREA_KEYS)


# ---------------------------------------------------------------------------
# The vocabulary itself
# ---------------------------------------------------------------------------


def test_every_seeded_working_area_is_still_a_row():
    """All twenty-three, including the two no longer offered.

    Withdrawal is `is_active`, never a delete: the Matters filed under
    `Muud teemad` still show it, and the statistics still count them
    (`taxonomy/0004`, Uus teema redesign §7.2).
    """
    rows = list(PolicyArea.objects.filter(key__in=[key for key, *_ in SEEDED_V2]))
    assert {row.key for row in rows} == {key for key, *_ in SEEDED_V2}
    assert len(rows) == 23


def test_the_offered_vocabulary_is_exactly_the_twenty_one_in_order():
    """One governed list, and it is what every surface offers.

    The set *and* the order: the department sequenced these, and a control that
    rearranged them would be answering a question nobody asked. Twenty-one plus
    the free-text `Muu` affordance is the twenty-two the approved design names
    — and `Muu` is not in this list because it is not a `PolicyArea` at all
    (app/taxonomy/vocabulary.py, Uus teema redesign §7).
    """
    from app.taxonomy.vocabulary import selectable_policy_areas

    assert len(EXPECTED) == 21
    assert [area.name_et for area in selectable_policy_areas()] == [
        name for _key, name, _order in EXPECTED
    ]


def test_the_two_withdrawn_areas_are_no_longer_offered_anywhere():
    """`Olulised tähtajad` was a watch list; `Muud teemad` was `Muu` twice.

    Neither is a subject area, and both stop being offered together — but only
    stop being *offered*. The row, the relations and the description are all
    still there (`taxonomy/0004`).
    """
    from app.taxonomy.vocabulary import policy_area_choices, selectable_policy_areas

    withdrawn = PolicyArea.objects.filter(key__in=WITHDRAWN_V2)
    assert withdrawn.count() == len(WITHDRAWN_V2)
    assert not withdrawn.filter(is_active=True).exists()

    offered = {area.key for area in selectable_policy_areas()}
    assert offered.isdisjoint(set(WITHDRAWN_V2))
    assert {key for key, _label in policy_area_choices()}.isdisjoint(set(WITHDRAWN_V2))


def test_nothing_remapped_muud_teemad_onto_muu():
    """The fuzzy migration this arrangement exists to refuse.

    `Muud teemad` and `Muu` mean the same thing to a reader, which is exactly
    why the temptation is there — and exactly why acting on it would rewrite
    somebody else's filing on a coincidence of wording. There is no `Muu`
    PolicyArea to remap onto, and the free-text column the affordance writes is
    not something a migration can fill truthfully (Uus teema redesign §7.2).
    """
    assert not PolicyArea.objects.filter(name_et__iexact="Muu").exists()
    catchall = PolicyArea.objects.get(key="muud-teemad")
    assert catchall.name_et == "Muud teemad"
    assert catchall.description


def test_the_retired_areas_keep_their_rows_and_stop_being_offered():
    """Deactivated, never remapped and never deleted.

    "Maksud" is not "Maksud ja toll" and "Haridus ja ettevõtlikkus" is not
    "Haridus". There is no reviewed equivalence between any retired label and
    any new one, so nothing guesses at one — the rows stay, the relations stay,
    and only `is_active` moves (Teema redesign §7.2).
    """
    retired = PolicyArea.objects.filter(key__in=RETIRED_POLICY_AREA_KEYS_V1)

    assert retired.count() == len(RETIRED_POLICY_AREA_KEYS_V1)
    assert not retired.filter(is_active=True).exists()

    offered = {area.key for area in PolicyArea.objects.filter(is_active=True)}
    assert offered.isdisjoint(set(RETIRED_POLICY_AREA_KEYS_V1))


def test_the_four_shared_names_keep_their_identity():
    """The rows every existing relation points at are not touched.

    Including the seventy-one applied from the OneNote filing structure: they
    hang off these primary keys, and a migration that had deleted and recreated
    the rows would have taken all of them with it.
    """
    for key in CARRIED_OVER:
        area = PolicyArea.objects.get(key=key)
        assert area.is_active
        assert key not in RETIRED_POLICY_AREA_KEYS_V1


def test_no_retired_area_shares_a_name_with_a_working_one():
    """The refusal that makes "no remapping" checkable rather than asserted.

    If a retired label and a new one ever spelled the same thing, somebody would
    eventually merge them — and that merge would be the fuzzy migration this
    whole arrangement exists to refuse.
    """
    working = {name.casefold() for _key, name, _order in EXPECTED}
    retired = {
        area.name_et.casefold()
        for area in PolicyArea.objects.filter(key__in=RETIRED_POLICY_AREA_KEYS_V1)
    }
    assert working & retired == set()


def test_names_and_order_are_exactly_the_reviewed_ones():
    rows = {row.key: row for row in PolicyArea.objects.all()}
    for key, name, sort_order in EXPECTED:
        assert rows[key].name_et == name
        assert rows[key].sort_order == sort_order


def test_every_reviewed_area_is_active():
    keys = [key for key, *_ in EXPECTED]
    assert PolicyArea.objects.filter(key__in=keys, is_active=True).count() == len(EXPECTED)


def test_every_reviewed_area_carries_a_definition():
    """Boundaries, so two people classify the same Matter the same way."""
    for area in REFERENCE_POLICY_AREAS_V1:
        assert area.description.strip()


def test_the_manifest_and_the_frozen_migration_baseline_agree():
    """The drift guard.

    The migration keeps its own literal copy, because a historical migration
    that imported today's manifest would replay as something different every
    time the manifest was edited. This is what stops the two from parting: the
    next vocabulary change is a new manifest entry *and* a new migration.
    """
    frozen = [tuple(row) for row in MIGRATION.BASELINE]
    seeded = [
        (area.key, area.name_et, area.description, area.sort_order)
        for area in REFERENCE_POLICY_AREAS_V2
    ]
    assert frozen == seeded

    # And the retired list the migration acts on is the manifest's, not a
    # second opinion about which of the nine carried over.
    assert list(MIGRATION.RETIRED) == list(RETIRED_POLICY_AREA_KEYS_V1)


def test_the_withdrawal_migration_and_the_manifest_agree():
    """The same drift guard, one migration later.

    `taxonomy/0004` carries its own frozen copy of the two withdrawn keys, and
    the manifest that stops offering them is a separate edit. If the two ever
    disagreed, a fresh database would offer a label a migrated one does not —
    which is the drift the whole arrangement exists to make impossible.
    """
    assert list(MIGRATION_V3.WITHDRAWN) == list(RETIRED_POLICY_AREA_KEYS_V2)

    # The offered manifest is exactly the seeded one minus those two, in the
    # order it already had. Derived rather than retyped, and checked rather
    # than assumed.
    offered = [area.key for area in REFERENCE_POLICY_AREAS_V1]
    assert offered == [
        area.key
        for area in REFERENCE_POLICY_AREAS_V2
        if area.key not in RETIRED_POLICY_AREA_KEYS_V2
    ]
    assert set(offered).isdisjoint(set(RETIRED_POLICY_AREA_KEYS_V2))


def test_the_withdrawal_refuses_a_row_somebody_has_renamed():
    """Deactivating on a key alone would overrule an edit without saying so."""
    PolicyArea.objects.filter(key="muud-teemad").update(name_et="Muud küsimused")

    with pytest.raises(RuntimeError, match="Muud küsimused"):
        MIGRATION_V3.retire(global_apps, None)


def test_version_ones_frozen_baseline_is_still_its_own():
    """The earlier migration must keep describing the earlier vocabulary.

    It runs first on every fresh database, and if somebody "updated" it to the
    new manifest then version 1.0 would never have existed — and the four rows
    that carried over would be created by whichever migration ran first, which
    is exactly the ambiguity the frozen copies exist to prevent.
    """
    assert len(MIGRATION_V1.BASELINE) == 9
    assert {key for key, *_ in MIGRATION_V1.BASELINE} == {
        "maksud",
        "toojoud",
        "keskkond",
        "energeetika",
        "halduskoormus",
        "aus-konkurents",
        "arioigus",
        "riigihanked",
        "haridus-ettevotlikkus",
    }


def test_the_business_source_is_recorded():
    """Where the twenty-three came from, checkable by a reviewer, not by code.

    Nothing at runtime reads a source: changing this vocabulary is a code change
    with a migration behind it, reviewed like any other. The provenance lives
    here and in the ADR so a reviewer can check the claim.
    """
    assert POLICY_AREA_SOURCE_PUBLISHER.startswith("Eesti Kaubandus-Tööstuskoda")
    assert "Teema redesign" in POLICY_AREA_SOURCE_TITLE
    assert POLICY_AREA_SOURCE_VERIFIED_ON == "2026-08-24"
    assert REFERENCE_POLICY_AREA_VERSION == "3.0"

    # The withdrawal keeps its own trail. The list still came from the Teema
    # redesign review on the 24th; stopping two of its labels being offered was
    # a separate decision on the 25th, and conflating the two dates would make
    # the earlier one unanswerable.
    assert "Uus teema" in POLICY_AREA_WITHDRAWAL_SOURCE_TITLE
    assert POLICY_AREA_WITHDRAWAL_VERIFIED_ON == "2026-08-25"


def test_the_earlier_vocabularys_provenance_is_kept():
    """Version 1.0's nine are still in the database; where they came from must
    still be answerable."""
    assert "Millega tegeleme" in POLICY_AREA_SOURCE_V1_TITLE
    assert POLICY_AREA_SOURCE_V1_URL.startswith("https://www.koda.ee/")


def test_no_tag_vocabulary_is_seeded():
    """Tags are a separate governed concept and theirs is not reviewed yet."""
    assert Tag.objects.count() == 0


def test_no_provisional_key_reaches_a_migrated_database():
    assert not PolicyArea.objects.filter(key__in=PROVISIONAL_KEYS).exists()


# ---------------------------------------------------------------------------
# The migration
# ---------------------------------------------------------------------------


def test_seeding_again_creates_nothing():
    before = PolicyArea.objects.count()
    MIGRATION.seed(global_apps, None)
    assert PolicyArea.objects.count() == before


def test_a_renamed_key_fails_closed():
    """Never silently rename: every Matter filed under it would move."""
    PolicyArea.objects.filter(key="maksud-ja-toll").update(name_et="Maksundus")

    with pytest.raises(RuntimeError) as failure:
        MIGRATION.seed(global_apps, None)
    assert "maksud-ja-toll" in str(failure.value)


def test_a_name_taken_under_another_key_fails_closed():
    """Two active areas sharing a name make every name-based match ambiguous."""
    PolicyArea.objects.filter(key="riigihanked").delete()
    PolicyArea.objects.create(key="hanked", name_et="Riigihanked", sort_order=95)

    with pytest.raises(RuntimeError) as failure:
        MIGRATION.seed(global_apps, None)
    assert "hanked" in str(failure.value)


def _migrate_taxonomy_to(target: tuple[str, str]) -> None:
    """Move the taxonomy app to one migration, inside the test transaction.

    ``SET CONSTRAINTS ALL IMMEDIATE`` first, for the reason
    `test_multiple_senders_migration` documents: Django's deferred foreign keys
    leave pending trigger events that make PostgreSQL refuse to alter the table.
    """
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([target])


def test_reverse_removes_only_pristine_unused_rows():
    _migrate_taxonomy_to(("taxonomy", "0001_initial"))
    try:
        assert not PolicyArea.objects.filter(key__in=REFERENCE_POLICY_AREA_KEYS).exists()
    finally:
        _migrate_taxonomy_to(("taxonomy", "0004_retire_catchall_and_deadline_areas"))
    assert PolicyArea.objects.filter(key__in=REFERENCE_POLICY_AREA_KEYS).count() == len(
        REFERENCE_POLICY_AREA_KEYS
    )


def test_reverse_puts_the_retired_areas_back_on_the_offered_list():
    """Rolling the code back means version 1.0's vocabulary is offered again.

    The rows were never deleted, so this is one flag going the other way — and
    it has to, or a rollback would leave the older code offering nothing where
    it used to offer nine.
    """
    MIGRATION.unseed(global_apps, None)
    try:
        assert PolicyArea.objects.filter(
            key__in=RETIRED_POLICY_AREA_KEYS_V1, is_active=True
        ).count() == len(RETIRED_POLICY_AREA_KEYS_V1)
    finally:
        MIGRATION.seed(global_apps, None)


def test_reverse_never_takes_a_classified_area_with_it(specialist):
    """A rollback of the code must not delete how work was filed."""
    from app.matters.services import create_matter

    matter = create_matter(title="Aktsiisimuudatus", actor=specialist)
    area = PolicyArea.objects.get(key="maksud-ja-toll")
    matter.policy_areas.add(area)

    MIGRATION.unseed(global_apps, None)
    try:
        assert PolicyArea.objects.filter(key="maksud-ja-toll").exists()
        assert matter.policy_areas.filter(key="maksud-ja-toll").exists()
    finally:
        MIGRATION.seed(global_apps, None)


def test_reverse_leaves_a_row_somebody_edited():
    PolicyArea.objects.filter(key="ehitus").update(description="Meie oma sõnastus.")

    MIGRATION.unseed(global_apps, None)
    try:
        assert PolicyArea.objects.filter(key="ehitus").exists()
    finally:
        MIGRATION.seed(global_apps, None)


# ---------------------------------------------------------------------------
# The development seed
# ---------------------------------------------------------------------------


def test_the_development_seed_does_not_create_a_second_vocabulary(settings):
    """Synthetic Matters, real classification.

    The rehearsal environment exists to show whether the work surfaces make
    sense, and it cannot do that while reporting on five categories nobody
    reviewed. `seed_dev_data` now reads the migration-seeded areas the way it
    already read the stage vocabulary.
    """
    settings.DEBUG = True
    settings.REAL_DATA_ALLOWED = False

    call_command("seed_dev_data", verbosity=0)

    assert not PolicyArea.objects.filter(key__in=PROVISIONAL_KEYS).exists()
    # The nine version-1.0 rows are still here — four active, five retired —
    # beside the nineteen the working vocabulary added.
    assert PolicyArea.objects.count() == 28


def test_the_development_seed_classifies_with_the_canonical_areas(settings):
    from app.matters.models import Matter

    settings.DEBUG = True
    settings.REAL_DATA_ALLOWED = False

    call_command("seed_dev_data", verbosity=0)

    used = set(
        Matter.objects.filter(policy_areas__isnull=False).values_list(
            "policy_areas__key", flat=True
        )
    )
    assert used
    assert used <= set(REFERENCE_POLICY_AREA_KEYS)


# ---------------------------------------------------------------------------
# The public institution manifest
# ---------------------------------------------------------------------------


def test_every_manifest_row_is_a_real_public_type():
    government = {
        OrganisationType.MINISTRY,
        OrganisationType.AUTHORITY,
        OrganisationType.PARLIAMENT,
        OrganisationType.GOVERNMENT,
        OrganisationType.EU_INSTITUTION,
    }
    for entry in PUBLIC_REFERENCE_ORGANISATIONS:
        assert entry.name.strip()
        assert entry.organisation_type in OrganisationType.values
        assert entry.organisation_type in government, entry.name


def test_no_company_or_association_is_in_the_public_baseline():
    """Private and sector bodies arrive through ordinary work, not a seed."""
    types = {entry.organisation_type for entry in PUBLIC_REFERENCE_ORGANISATIONS}
    assert OrganisationType.COMPANY not in types
    assert OrganisationType.ASSOCIATION not in types
    assert OrganisationType.CHAMBER not in types


def test_the_eleven_ministries_are_still_represented():
    names = {entry.name for entry in PUBLIC_REFERENCE_ORGANISATIONS}
    assert len(MINISTRIES) == 11
    assert {entry.name for entry in MINISTRIES} <= names
    assert ORGANISATION_SOURCE_VERIFIED_ON == "2026-08-23"


def test_the_core_public_set_stays_small():
    """Not a public-sector directory. Coverage decides what else is needed."""
    assert len(CORE_PUBLIC_INSTITUTIONS) == 4
    assert {entry.name for entry in CORE_PUBLIC_INSTITUTIONS} == {
        "Riigikogu",
        "Vabariigi Valitsus",
        "Euroopa Komisjon",
        "Euroopa Parlament",
    }


def test_no_two_manifest_rows_normalise_to_the_same_name():
    seen: dict[str, str] = {}
    for entry in PUBLIC_REFERENCE_ORGANISATIONS:
        normalized = normalize_for_matching(entry.name)
        assert normalized not in seen, f"{entry.name} vs {seen.get(normalized)}"
        seen[normalized] = entry.name


def test_no_alias_is_claimed_by_two_manifest_rows():
    """An alias is an identity decision, and matching trusts it absolutely."""
    seen: dict[str, str] = {}
    for entry in PUBLIC_REFERENCE_ORGANISATIONS:
        for alias in entry.aliases:
            normalized = normalize_for_matching(alias)
            assert normalized not in seen, f"{alias}: {entry.name} vs {seen.get(normalized)}"
            seen[normalized] = entry.name


def test_no_alias_collides_with_a_canonical_name():
    canonical = {normalize_for_matching(e.name) for e in PUBLIC_REFERENCE_ORGANISATIONS}
    for entry in PUBLIC_REFERENCE_ORGANISATIONS:
        for alias in entry.aliases:
            assert normalize_for_matching(alias) not in canonical


def test_the_commission_carries_no_abbreviation():
    """`EK` means Komisjon, Kohus and Kontrollikoda alike in Estonian.

    Recorded as a test rather than a comment because the omission is the
    decision: one wrong `EK` would file a Commission consultation under the
    Court, and an alias is never re-examined once matching starts trusting it.
    """
    commission = next(e for e in CORE_PUBLIC_INSTITUTIONS if e.name == "Euroopa Komisjon")
    assert commission.aliases == ()


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


def test_a_plan_on_an_empty_database_proposes_every_institution():
    plan = build_reference_plan()

    assert len(plan.organisations) == len(PUBLIC_REFERENCE_ORGANISATIONS)
    assert len(plan.organisations_to_create) == len(PUBLIC_REFERENCE_ORGANISATIONS)
    assert not plan.organisations_present
    # Policy areas are already there: they came with the schema.
    assert len(plan.areas_present) == 23
    assert not plan.areas_missing


def test_a_plan_writes_nothing():
    before_orgs = Organisation.objects.count()
    before_areas = PolicyArea.objects.count()
    before_aliases = OrganisationAlias.objects.count()

    build_reference_plan()

    assert Organisation.objects.count() == before_orgs
    assert PolicyArea.objects.count() == before_areas
    assert OrganisationAlias.objects.count() == before_aliases


def test_the_plan_and_its_digest_are_deterministic():
    first = build_reference_plan()
    second = build_reference_plan()
    assert first.digest() == second.digest()
    assert len(first.digest()) == 64


def test_the_digest_changes_when_the_database_does():
    before = build_reference_plan().digest()
    Organisation.objects.create(name="Riigikogu", organisation_type=OrganisationType.PARLIAMENT)
    assert build_reference_plan().digest() != before


def test_the_plan_command_reports_the_digest(capsys):
    call_command("reference_data", "plan")
    out = capsys.readouterr().out
    assert build_reference_plan().digest() in out
    assert "nothing was written" in out
    assert "not managed by this baseline" in out


def test_the_plan_command_json_names_tags_as_unmanaged(capsys):
    call_command("reference_data", "plan", "--json")
    # The command prints its closing lines after the object; read only the object.
    out = capsys.readouterr().out
    payload = json.loads(out[: out.rindex("}") + 1])
    assert payload["tags"] == "not managed by this baseline"
    assert payload["policy_areas"]["present"] == 23
    assert payload["plan_sha256"] == build_reference_plan().digest()


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


def test_apply_creates_the_reviewed_institutions():
    plan = build_reference_plan()
    result = apply_reference_plan(expected_sha256=plan.digest())

    assert len(result.organisations_created) == len(PUBLIC_REFERENCE_ORGANISATIONS)
    assert Organisation.objects.count() == len(PUBLIC_REFERENCE_ORGANISATIONS)
    assert Organisation.objects.filter(name="Riigikogu").exists()
    assert (
        Organisation.objects.get(name="Vabariigi Valitsus").organisation_type
        == OrganisationType.GOVERNMENT
    )


def test_apply_adds_the_reviewed_aliases():
    apply_reference_plan(expected_sha256=build_reference_plan().digest())

    ministry = Organisation.objects.get(name="Majandus- ja Kommunikatsiooniministeerium")
    assert ministry.aliases.filter(alias="MKM").exists()
    valitsus = Organisation.objects.get(name="Vabariigi Valitsus")
    assert valitsus.aliases.filter(alias="Valitsus").exists()


def test_a_second_apply_creates_nothing():
    apply_reference_plan(expected_sha256=build_reference_plan().digest())
    organisations = Organisation.objects.count()
    aliases = OrganisationAlias.objects.count()

    second = apply_reference_plan(expected_sha256=build_reference_plan().digest())

    assert second.organisations_created == ()
    assert second.aliases_added == ()
    assert Organisation.objects.count() == organisations
    assert OrganisationAlias.objects.count() == aliases


def test_apply_refuses_a_digest_that_no_longer_holds():
    stale = build_reference_plan().digest()
    Organisation.objects.create(name="Riigikogu", organisation_type=OrganisationType.PARLIAMENT)

    with pytest.raises(ReferencePlanChanged):
        apply_reference_plan(expected_sha256=stale)


def test_apply_refuses_without_a_digest():
    with pytest.raises(CommandError) as failure:
        call_command("reference_data", "apply")
    assert "expect-plan-sha256" in str(failure.value)


def test_apply_never_renames_or_retypes_an_existing_row():
    """The rule the whole module is built around: additive, never corrective."""
    existing = Organisation.objects.create(
        name="Riigikogu",
        organisation_type=OrganisationType.OTHER,
        registry_code="12345",
        notes="Kellegi käsitsi tehtud otsus.",
    )

    apply_reference_plan(expected_sha256=build_reference_plan().digest())

    existing.refresh_from_db()
    assert existing.name == "Riigikogu"
    assert existing.organisation_type == OrganisationType.OTHER
    assert existing.registry_code == "12345"
    assert existing.notes == "Kellegi käsitsi tehtud otsus."
    assert Organisation.objects.filter(name="Riigikogu").count() == 1


def test_apply_refuses_when_an_alias_belongs_to_another_institution():
    """Moving an alias would rewrite somebody's earlier identity decision."""
    other = Organisation.objects.create(
        name="Muu asutus", organisation_type=OrganisationType.AUTHORITY
    )
    OrganisationAlias.objects.create(
        organisation=other, alias="MKM", alias_type=AliasType.ABBREVIATION
    )

    plan = build_reference_plan()
    assert plan.alias_conflicts == 1

    with pytest.raises(ReferenceDataConflict) as failure:
        apply_reference_plan(expected_sha256=plan.digest())
    assert "MKM" in str(failure.value)

    # And it stayed where it was.
    assert OrganisationAlias.objects.get(alias="MKM").organisation_id == other.id


def test_apply_refuses_when_a_name_means_two_rows():
    Organisation.objects.create(name="Riigikogu", organisation_type=OrganisationType.PARLIAMENT)
    Organisation.objects.create(name="riigikogu", organisation_type=OrganisationType.OTHER)

    plan = build_reference_plan()
    conflicting = [f.name for f in plan.organisations_conflicting]
    assert "Riigikogu" in conflicting

    with pytest.raises(ReferenceDataConflict):
        apply_reference_plan(expected_sha256=plan.digest())


def test_apply_never_creates_a_policy_area():
    """The vocabulary has one write path, and it is the migration."""
    PolicyArea.objects.filter(key="maksud-ja-toll").delete()

    plan = build_reference_plan()
    assert [f.key for f in plan.areas_missing] == ["maksud-ja-toll"]

    apply_reference_plan(expected_sha256=plan.digest())

    assert not PolicyArea.objects.filter(key="maksud-ja-toll").exists()


def test_apply_never_creates_a_tag():
    apply_reference_plan(expected_sha256=build_reference_plan().digest())
    assert Tag.objects.count() == 0


def test_apply_writes_no_matter_relationships(specialist):
    from app.matters.services import create_matter

    matter = create_matter(title="Eelnõu", actor=specialist)

    apply_reference_plan(expected_sha256=build_reference_plan().digest())

    matter.refresh_from_db()
    assert matter.source_organisations.count() == 0
    assert matter.addressee_organisation_id is None
    assert matter.policy_areas.count() == 0


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


def test_verify_passes_on_a_complete_baseline():
    apply_reference_plan(expected_sha256=build_reference_plan().digest())
    report = verify_reference_data()
    assert report.ok, report.problems
    assert report.policy_areas_present == 23
    assert report.organisations_present == len(PUBLIC_REFERENCE_ORGANISATIONS)


def test_verify_detects_a_missing_policy_area():
    apply_reference_plan(expected_sha256=build_reference_plan().digest())
    PolicyArea.objects.filter(key="keskkond").delete()

    report = verify_reference_data()

    assert not report.ok
    assert any("Keskkond" in problem for problem in report.problems)
    assert any("migratsioon" in problem for problem in report.problems)


def test_verify_detects_a_deactivated_policy_area():
    apply_reference_plan(expected_sha256=build_reference_plan().digest())
    PolicyArea.objects.filter(key="keskkond").update(is_active=False)

    report = verify_reference_data()

    assert not report.ok
    assert any("deaktiveeritud" in problem for problem in report.problems)


def test_verify_detects_a_missing_organisation():
    apply_reference_plan(expected_sha256=build_reference_plan().digest())
    Organisation.objects.filter(name="Riigikogu").delete()

    report = verify_reference_data()

    assert not report.ok
    assert any("Riigikogu" in problem for problem in report.problems)
    assert any("reference_data" in problem for problem in report.problems)


def test_verify_detects_a_missing_alias():
    apply_reference_plan(expected_sha256=build_reference_plan().digest())
    OrganisationAlias.objects.filter(alias="MKM").delete()

    report = verify_reference_data()

    assert not report.ok
    assert any("MKM" in problem for problem in report.problems)


def test_verify_detects_an_alias_owned_by_the_wrong_institution():
    apply_reference_plan(expected_sha256=build_reference_plan().digest())
    other = Organisation.objects.create(
        name="Muu asutus", organisation_type=OrganisationType.AUTHORITY
    )
    OrganisationAlias.objects.filter(alias="MKM").delete()
    OrganisationAlias.objects.create(
        organisation=other, alias="MKM", alias_type=AliasType.ABBREVIATION
    )

    report = verify_reference_data()

    assert not report.ok
    assert any("MKM" in problem for problem in report.problems)


def test_the_verify_command_exits_non_zero_on_a_broken_baseline():
    with pytest.raises(CommandError) as failure:
        call_command("reference_data", "verify")
    assert "not intact" in str(failure.value)


def test_the_verify_command_reports_a_complete_baseline(capsys):
    apply_reference_plan(expected_sha256=build_reference_plan().digest())
    call_command("reference_data", "verify")
    out = capsys.readouterr().out
    assert "complete" in out
    assert "Tags" in out


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def test_a_seeded_institution_is_findable_by_name_and_by_alias():
    """Through the existing matching. No new search engine, no fuzzy step."""
    from app.organisations.services import find_exact

    apply_reference_plan(expected_sha256=build_reference_plan().digest())

    assert find_exact("Rahandusministeerium") is not None
    assert find_exact("  rahandusMINISTEERIUM ") is not None
    assert find_exact("RM").name == "Rahandusministeerium"
    assert find_exact("Valitsus").name == "Vabariigi Valitsus"
    assert find_exact("Mitte-olemasolev amet") is None


# ---------------------------------------------------------------------------
# The findings vocabulary
# ---------------------------------------------------------------------------


def test_finding_actions_stay_a_closed_set():
    plan = build_reference_plan()
    assert {f.action for f in plan.policy_areas} <= {
        AREA_PRESENT,
        AREA_MISSING,
        AREA_CONFLICT,
    }
    assert {f.action for f in plan.organisations} <= {ORG_PRESENT, ORG_CREATE}


# ---------------------------------------------------------------------------
# The OneNote enrichment, once a real vocabulary exists
# ---------------------------------------------------------------------------


def test_no_reviewed_onenote_alias_rule_exists_yet():
    """The evidence for one does not exist until the real areas are deployed.

    Production currently reports 24 filing locations, all UNMAPPED — not because
    the mapping engine is broken but because there was nothing to map onto. The
    inventory is rerun after this lands, and only then can a person say which
    sections need a reviewed alias.
    """
    from app.legacy_import.onenote_policy_areas import REVIEWED_ALIAS_RULES

    assert REVIEWED_ALIAS_RULES == ()


def test_a_section_named_after_a_canonical_area_now_resolves_by_exact_name():
    """The whole point of seeding the vocabulary, measured from G's side."""
    from django.utils import timezone

    from app.legacy_import.onenote_policy_areas import (
        MappingClass,
        build_policy_area_plan,
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
    from tests import factories

    now = timezone.now()
    page = LegacySourcePage.objects.create(
        source_system=SourceSystem.ONENOTE_DESKTOP,
        source_page_id="1-riigihanked",
        page_key="riigihanked",
        source_notebook="Näidiskoja õigusloome",
        source_section="Riigihanked",
        source_section_group="ARHIIV",
        title="Näidisleht",
        page_role=SourcePageRole.MATTER_LIKE,
        capture_id="capture-riigihanked",
        source_xml_sha256="1" * 64,
        first_imported_at=now,
        latest_imported_at=now,
    )
    matter = factories.MatterFactory()
    MatterSourcePage.objects.create(
        matter=matter,
        source_page=page,
        relationship_kind=SourceRelationshipKind.PRIMARY,
        match_method=SourceMatchMethod.EXCEL_EXACT_PAGE_ID,
        match_class=SourceMatchClass.EXACT,
    )

    plan = build_policy_area_plan()
    (proposal,) = plan.proposals

    assert proposal.mapping_class == MappingClass.EXACT_NAME
    assert proposal.policy_area_id == PolicyArea.objects.get(key="riigihanked").id


def test_seeding_the_vocabulary_does_not_apply_anything_to_onenote():
    """A plan is still only a plan; no Matter gained an area from the seed."""
    from app.matters.models import Matter

    assert not Matter.objects.filter(policy_areas__isnull=False).exists()
