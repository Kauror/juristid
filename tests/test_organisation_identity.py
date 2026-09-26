"""One institution, one identity, however its name was typed (ENG-045).

Three ways the catalogue used to fill with copies of one body, and the rule
that now closes each:

* **Invisible characters.** Text copied out of Word, a PDF or a web page
  carries soft hyphens, zero-width spaces, byte-order marks and direction
  controls. The matching fold kept every one of them, so «Rahandusministeerium»
  with a soft hyphen in it was a different institution from the one without.
  The organisation key now ignores Unicode's format characters (category Cf) —
  and nothing else: Estonian letters fold exactly as they did, and visible
  punctuation still tells two names apart.
* **Quick-create around the resolver.** «Lisa uus organisatsioon» called the
  creating half of the resolver directly, so two rows under one spelling made a
  third on every click. Every path now resolves the same way: one match is
  reused, two are refused, none is created.
* **Two people at once.** Look-then-insert without a lock made two rows when
  two saves named one new body at the same moment. That half is proved against
  real concurrent transactions in `tests/test_organisation_identity_concurrency.py`.

Nothing here merges an existing duplicate. The read-only report at the end is
how an operator finds them; deciding which row is the institution is theirs.
"""

from __future__ import annotations

import io

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.urls import reverse

from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.core.text import normalize_for_matching
from app.organisations.models import Organisation, OrganisationAlias, OrganisationType
from app.organisations.services import (
    find_matches,
    get_or_create_organisation,
    resolve_organisation_name,
    resolve_recipients,
)
from tests import factories

pytestmark = pytest.mark.django_db

QUICK_CREATE = reverse("organisations:quick_create")

#: The invisible characters the audit measured, each of which used to make one
#: more row for one institution (ENG-045, `abuse/org_dupes.py`).
INVISIBLE = {
    "zero-width-space": "\u200b",
    "soft-hyphen": "\u00ad",
    "byte-order-mark": "\ufeff",
    "right-to-left-override": "\u202e",
    "zero-width-joiner": "\u200d",
    "left-to-right-mark": "\u200e",
    "word-joiner": "\u2060",
}

#: The direction controls that reorder what a reader sees. No business meaning
#: in an institution's name, and stripped from one before it is stored.
BIDI_CONTROLS = "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"


def ministry(name: str = "Rahandusministeerium") -> Organisation:
    return Organisation.objects.create(name=name, organisation_type=OrganisationType.MINISTRY)


def quick_create(client, name: str, **extra: str):
    return client.post(QUICK_CREATE, {"name": name, "target": "source_organisations", **extra})


# ---------------------------------------------------------------------------
# The key: invisible characters are not part of a name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("character", INVISIBLE.values(), ids=INVISIBLE.keys())
@pytest.mark.parametrize(
    "shape",
    ["Rahandus{c}ministeerium", "{c}Rahandusministeerium", "Rahandusministeerium{c}"],
    ids=["inside", "leading", "trailing"],
)
def test_an_invisible_character_does_not_make_a_second_institution(character, shape):
    existing = ministry()

    result = get_or_create_organisation(name=shape.format(c=character))

    assert not result.created
    assert result.organisation.pk == existing.pk
    assert Organisation.objects.count() == 1


@pytest.mark.parametrize("character", INVISIBLE.values(), ids=INVISIBLE.keys())
def test_a_typed_name_carrying_one_resolves_to_the_existing_row(character):
    """The path Uus teema, Muuda teemat, Saatja and the closing recipients use."""
    existing = ministry()

    assert resolve_organisation_name(name=f"Rahandus{character}ministeerium") == existing
    assert Organisation.objects.count() == 1


def test_a_row_stored_with_one_is_found_by_the_plain_spelling():
    """The other side of the same key: the copy-pasted row is the one already
    there, and the lawyer types the name cleanly."""
    pasted = ministry("Rahandus\u00administeerium")

    result = get_or_create_organisation(name="Rahandusministeerium")

    assert not result.created
    assert result.organisation.pk == pasted.pk


def test_an_alias_carrying_one_still_names_its_institution():
    economy = ministry("Majandus- ja Kommunikatsiooniministeerium")
    OrganisationAlias.objects.create(organisation=economy, alias="MKM")

    assert find_matches("M\u200bKM") == [economy]
    assert find_matches("\ufeffMKM") == [economy]


@pytest.mark.parametrize(
    "name",
    [
        "Põllumajandus- ja Toiduamet",
        "Õiguskantsleri Kantselei",
        "Tööinspektsioon",
        "ÄRIREGISTER",
        "Žürii ja Šokolaadi Ühendus",
        "Sotsiaalministeeriumi Ülevaade",
    ],
)
def test_estonian_spelling_folds_exactly_as_it_did(name):
    """õ ä ö ü š ž: the organisation key is the matching fold, plus nothing but
    the removal of format characters. `Põllumajandus` still finds `Pollumajandus`."""
    from app.core.text import normalize_organisation_name

    assert normalize_organisation_name(name) == normalize_for_matching(name)


def test_visible_punctuation_still_tells_two_names_apart():
    """Only invisible characters are ignored. A hyphen, a full stop and a comma
    are what somebody wrote, and they are not stripped."""
    from app.core.text import normalize_organisation_name

    assert normalize_organisation_name("Majandus- ja Kommunikatsiooniministeerium") == (
        "majandus- ja kommunikatsiooniministeerium"
    )
    assert normalize_organisation_name("A.S. Näidis, Tartu") == "a.s. naidis, tartu"

    ministry("Majandus- ja Kommunikatsiooniministeerium")
    assert get_or_create_organisation(name="Majandus ja Kommunikatsiooniministeerium").created


def test_the_search_fold_is_not_the_organisation_key():
    """The search projection folds names with `normalize_for_matching`, and its
    semantics belong to `INDEX_VERSION`. The organisation key is a separate
    function precisely so that this fix changes no indexed text."""
    assert normalize_for_matching("Rahandus\u200bministeerium") == "rahandus\u200bministeerium"


# ---------------------------------------------------------------------------
# One resolver: one match reuses, two refuse, none creates
# ---------------------------------------------------------------------------


def test_creating_refuses_rather_than_adding_a_third_row():
    """`find_exact` answers None for two matches as well as for none, and the
    creating half used to read the first as the second."""
    ministry("Kaksik")
    ministry("kaksik")

    with pytest.raises(DomainError, match="sobib mitme organisatsiooniga"):
        get_or_create_organisation(name="Kaksik")

    assert Organisation.objects.count() == 2


def test_two_institutions_sharing_an_alias_are_refused_too():
    first = ministry("Esimene amet")
    second = ministry("Teine amet")
    OrganisationAlias.objects.create(organisation=first, alias="EA")
    OrganisationAlias.objects.create(organisation=second, alias="EA")

    with pytest.raises(DomainError, match="sobib mitme organisatsiooniga"):
        get_or_create_organisation(name="EA")

    assert Organisation.objects.count() == 2


def test_quick_create_refuses_a_name_two_rows_already_carry(signed_in):
    """The measured case: each POST added a row, 2 → 3 → 4, answering 200."""
    ministry("Näidisamet")
    ministry("näidisamet")
    events = ChangeEvent.objects.count()

    for _ in range(2):
        response = quick_create(signed_in, "Näidisamet")
        assert response.status_code == 400
        assert "sobib mitme organisatsiooniga" in response.content.decode()

    assert Organisation.objects.count() == 2
    assert ChangeEvent.objects.count() == events


def test_quick_create_refuses_an_invisible_variant_of_an_ambiguous_name(signed_in):
    ministry("Näidisamet")
    ministry("näidisamet")

    response = quick_create(signed_in, "Näidis\u200bamet")

    assert response.status_code == 400
    assert Organisation.objects.count() == 2


def test_quick_create_reuses_the_one_row_an_invisible_variant_names(signed_in):
    existing = ministry("Näidisamet")

    response = quick_create(signed_in, "Näidis\u00adamet")

    assert response.status_code == 200
    assert "oli juba olemas" in response.content.decode()
    assert list(Organisation.objects.all()) == [existing]


def test_quick_create_refusal_says_nothing_about_any_matter(signed_in, specialist):
    """The refusal names what was typed and nothing else — not a Teema the
    institution is filed on, which may be one the person cannot see."""
    first = ministry("Näidisamet")
    ministry("näidisamet")
    hidden = factories.MatterFactory(
        owner=factories.UserFactory(),
        title="Salajane eelnõu, mida küsija ei näe",
        visibility=Visibility.RESTRICTED,
    )
    hidden.source_organisations.add(first)

    response = quick_create(signed_in, "Näidisamet")
    body = response.content.decode()

    assert response.status_code == 400
    assert hidden.title not in body
    assert str(hidden.pk) not in body


def test_a_reader_still_cannot_reach_quick_create(client, reader):
    client.force_login(reader)

    assert quick_create(client, "Uus amet").status_code == 404
    assert not Organisation.objects.exists()


def test_the_registry_code_still_reuses_its_institution():
    first = get_or_create_organisation(name="Näidis AS", registry_code="12345678")
    second = get_or_create_organisation(name="Näidis Aktsiaselts", registry_code="12345678")

    assert not second.created
    assert second.organisation.pk == first.organisation.pk


def test_several_typed_recipients_resolve_through_the_same_rule():
    existing = ministry()

    resolved = resolve_recipients(
        chosen=[], typed_names=["Rahandus\u200bministeerium", "Uus komisjon", "uus  komisjon"]
    )

    assert resolved[0] == existing
    assert [organisation.name for organisation in resolved[1:]] == ["Uus komisjon"]
    assert Organisation.objects.count() == 2


# ---------------------------------------------------------------------------
# Direction controls are not stored in a name
# ---------------------------------------------------------------------------


def test_quick_create_stores_no_direction_control(signed_in):
    response = quick_create(signed_in, "\u202eNäidis\u2066amet\u2069\u200f")

    assert response.status_code == 200
    assert Organisation.objects.get().name == "Näidisamet"


def test_a_typed_name_is_stored_without_them():
    organisation = resolve_organisation_name(name="Uus\u202d amet\u202c")

    assert organisation is not None
    assert organisation.name == "Uus amet"


@pytest.mark.parametrize(
    "control", list(BIDI_CONTROLS), ids=[f"U+{ord(c):04X}" for c in BIDI_CONTROLS]
)
def test_every_direction_control_is_stripped_from_a_name_and_an_alias(control):
    organisation = ministry(f"Näidis{control}amet")
    alias = OrganisationAlias.objects.create(organisation=organisation, alias=f"N{control}A")

    organisation.refresh_from_db()
    alias.refresh_from_db()
    assert organisation.name == "Näidisamet"
    assert alias.alias == "NA"


def test_the_refusal_does_not_echo_a_direction_control():
    ministry("Kaksik")
    ministry("kaksik")

    with pytest.raises(DomainError) as refusal:
        resolve_organisation_name(name="Kak\u202esik")

    assert "\u202e" not in str(refusal.value)
    assert "«Kaksik»" in str(refusal.value)


def test_other_invisible_characters_and_free_text_are_left_as_written():
    """Only the direction controls are removed from what is stored. A soft
    hyphen is somebody's text, merely not part of the identity; and free text —
    the notes — is not a governed name at all."""
    organisation = ministry("Näidis\u00adamet")
    organisation.notes = "Märkus \u202etagurpidi\u202c ja \u200bmuu"
    organisation.save()

    organisation.refresh_from_db()
    assert organisation.name == "Näidis\u00adamet"
    assert organisation.notes == "Märkus \u202etagurpidi\u202c ja \u200bmuu"


# ---------------------------------------------------------------------------
# The read-only duplicate report
# ---------------------------------------------------------------------------


def run_report() -> tuple[int, str]:
    out = io.StringIO()
    try:
        call_command("check_organisation_duplicates", stdout=out)
    except SystemExit as exit_:
        return int(exit_.code or 0), out.getvalue()
    return 0, out.getvalue()


def test_the_report_is_quiet_and_succeeds_on_a_clean_catalogue():
    ministry()
    ministry("Kliimaministeerium")

    code, output = run_report()

    assert code == 0
    assert "no colliding organisations" in output.lower()


def test_the_report_lists_every_row_that_collides_by_name(specialist):
    plain = ministry("Rahandusministeerium")
    pasted = Organisation.objects.bulk_create(
        [Organisation(name="Rahandus\u200bministeerium", normalized_name="rahandusministeerium")]
    )[0]
    lowered = ministry("rahandusministeerium")
    matter = factories.MatterFactory(owner=specialist)
    matter.source_organisations.add(plain)

    code, output = run_report()

    assert code == 1
    for organisation in (plain, pasted, lowered):
        assert str(organisation.pk) in output
    assert "3 organisations" in output
    # Two rows that print alike must not be printed alike.
    assert "Rahandus<U+200B>ministeerium" in output
    # Whoever merges needs to see which row carries the filing.
    plain_line = next(line for line in output.splitlines() if str(plain.pk) in line)
    assert "references: 1" in plain_line
    assert "MatterSourceOrganisation.organisation 1" in plain_line


def test_the_report_lists_institutions_that_share_an_alias():
    first = ministry("Esimene amet")
    second = ministry("Teine amet")
    OrganisationAlias.objects.create(organisation=first, alias="EA")
    OrganisationAlias.objects.create(organisation=second, alias="E\u200bA")

    code, output = run_report()

    assert code == 1
    assert str(first.pk) in output
    assert str(second.pk) in output


def test_the_report_counts_keys_stored_under_the_old_fold():
    """A row whose stored key the current fold would not produce — what the key
    migration exists to recompute — is counted, not repaired."""
    Organisation.objects.bulk_create(
        [Organisation(name="Vana\u200b amet", normalized_name="vana\u200b amet")]
    )

    code, output = run_report()

    assert "stale key" in output.lower()
    assert Organisation.objects.get().normalized_name == "vana\u200b amet"
    assert code == 1


def test_the_report_changes_nothing():
    ministry("Kaksik")
    ministry("kaksik")
    before = list(
        Organisation.objects.order_by("pk").values_list(
            "pk", "name", "normalized_name", "updated_at"
        )
    )

    run_report()

    after = list(
        Organisation.objects.order_by("pk").values_list(
            "pk", "name", "normalized_name", "updated_at"
        )
    )
    assert after == before


# ---------------------------------------------------------------------------
# The stored keys: recomputed by migration, both ways, over rows
# ---------------------------------------------------------------------------

BEFORE_KEYS = ("organisations", "0001_initial")
AFTER_KEYS = ("organisations", "0002_recompute_organisation_keys")


def migrate_organisations_to(target: tuple[str, str]) -> None:
    """The real executor, inside the test transaction (see
    `tests/test_multiple_senders_migration.py` for why the constraints go
    immediate first)."""
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([target])


def stored_keys() -> dict[str, str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT name, normalized_name FROM organisations_organisation")
        return dict(cursor.fetchall())


def stored_alias_keys() -> dict[str, str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT alias, normalized_alias FROM organisations_organisationalias")
        return dict(cursor.fetchall())


@pytest.fixture
def restore_schema():
    yield
    migrate_organisations_to(AFTER_KEYS)


def test_the_key_migration_recomputes_the_derived_columns_both_ways(restore_schema):
    pasted = ministry("Rahandus\u00administeerium")
    OrganisationAlias.objects.create(organisation=pasted, alias="R\u200bM")
    ministry("Kliimaministeerium")
    before = dict(Organisation.objects.values_list("pk", "updated_at"))

    migrate_organisations_to(BEFORE_KEYS)

    # Backwards: exactly the keys the old fold wrote, format characters kept.
    assert stored_keys() == {
        "Rahandus\u00administeerium": "rahandus\u00administeerium",
        "Kliimaministeerium": "kliimaministeerium",
    }
    assert stored_alias_keys() == {"R\u200bM": "r\u200bm"}

    migrate_organisations_to(AFTER_KEYS)

    # Forwards: the organisation key. Names, rows and timestamps untouched —
    # a recompute of a derived column, not a rename and not a merge.
    assert stored_keys() == {
        "Rahandus\u00administeerium": "rahandusministeerium",
        "Kliimaministeerium": "kliimaministeerium",
    }
    assert stored_alias_keys() == {"R\u200bM": "rm"}
    assert dict(Organisation.objects.values_list("pk", "updated_at")) == before
    assert find_matches("Rahandusministeerium") == [pasted]


def test_the_key_migration_leaves_an_alias_alone_rather_than_collide(restore_schema):
    """One institution may hold `MKM` and a pasted `MKM` with a zero-width space
    as two aliases. Under the new key they are one, and the unique alias
    constraint would stop the migration; the pasted one keeps its old key
    instead — its institution is still found through the clean one."""
    migrate_organisations_to(BEFORE_KEYS)
    economy = ministry("Majandusministeerium")
    OrganisationAlias.objects.bulk_create(
        [
            OrganisationAlias(organisation=economy, alias="MKM", normalized_alias="mkm"),
            OrganisationAlias(
                organisation=economy, alias="MKM\u200b", normalized_alias="mkm\u200b"
            ),
        ]
    )

    migrate_organisations_to(AFTER_KEYS)

    assert stored_alias_keys() == {"MKM": "mkm", "MKM\u200b": "mkm\u200b"}
    assert find_matches("MKM\u200b") == [economy]
