"""Haldus → Klassifikaatorid over HTTP: who reaches it, what a change records (docs/adr/0187).

The capability matrix first — every kind of caller, every route — then the
pages' own behaviour: a rename through the form, a refused duplicate, a stale
page, the controlled vocabularies offering no form, and the audit row naming
the persona the shared gate chose.
"""

from __future__ import annotations

import pytest
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse

from app.accounts import capabilities
from app.accounts.enums import Capability, UserRole
from app.audit.enums import AuthMethod, ChangeEventType
from app.audit.models import ChangeEvent
from app.core.authorization import (
    has_capability,
    may_assign_work,
    may_write_business_content,
)
from app.organisations import administration as catalogue
from app.organisations.models import AliasType, Organisation, OrganisationAlias
from app.reference_data.access import may_manage_reference_data
from app.reference_data.inventory import REVIEWED_CHANGE_ONLY
from app.taxonomy import administration as vocabulary
from app.taxonomy.models import Tag
from tests import factories
from tests.gate import PASSWORD, apply_shared_gate

pytestmark = pytest.mark.django_db

MANAGE = Capability.MANAGE_REFERENCE_DATA.value


# -- the capability --------------------------------------------------------------------


def test_the_capability_is_in_the_vocabulary_with_estonian_text():
    assert MANAGE == "reference.manage"
    assert MANAGE in capabilities.ALL_CAPABILITIES
    assert MANAGE in capabilities.BUSINESS_CAPABILITIES
    assert MANAGE not in capabilities.ADMINISTRATIVE_CAPABILITIES
    assert MANAGE not in capabilities.REQUIRES_BUSINESS_WRITE
    assert capabilities.TEXT[MANAGE].label == "Klassifikaatorite haldus"
    assert capabilities.DEFAULT_HOLDERS[MANAGE] == frozenset({UserRole.DEPARTMENT_HEAD.value})
    assert capabilities.GRANTABLE_TO_ROLES[MANAGE] == frozenset(
        {UserRole.SPECIALIST.value, UserRole.DEPARTMENT_HEAD.value, UserRole.ADMINISTRATOR.value}
    )


@pytest.mark.parametrize(
    ("make", "holds"),
    [
        (lambda: factories.DepartmentHeadFactory(), True),
        (lambda: factories.UserFactory(), False),
        (lambda: factories.UserFactory(capability_overrides={MANAGE: "allow"}), True),
        (lambda: factories.ReaderFactory(), False),
        (lambda: factories.ReaderFactory(capability_overrides={MANAGE: "allow"}), False),
        (lambda: factories.AdministratorFactory(), False),
        (lambda: factories.AdministratorFactory(capability_overrides={MANAGE: "allow"}), True),
        (lambda: factories.DepartmentHeadFactory(capability_overrides={MANAGE: "deny"}), False),
        (
            lambda: factories.DepartmentHeadFactory(
                is_active=False, capability_overrides={MANAGE: "allow"}
            ),
            False,
        ),
    ],
    ids=[
        "head",
        "specialist",
        "specialist-granted",
        "reader",
        "reader-granted",
        "administrator",
        "administrator-granted",
        "head-denied",
        "inactive-head",
    ],
)
def test_who_holds_reference_manage(make, holds):
    assert may_manage_reference_data(make()) is holds


def test_a_granted_technical_administrator_gains_no_business_write():
    administrator = factories.AdministratorFactory(capability_overrides={MANAGE: "allow"})

    assert may_manage_reference_data(administrator)
    assert not may_write_business_content(administrator)
    assert not may_assign_work(administrator)
    assert not has_capability(administrator, Capability.MANAGE_ACCOUNTS)


def test_unrelated_business_capabilities_do_not_imply_it():
    specialist = factories.UserFactory(
        capability_overrides={
            Capability.DELETE_MATTERS.value: "allow",
            Capability.VIEW_DEPARTMENT_MANAGEMENT.value: "allow",
            Capability.REVIEW_WORK_VICTORIES.value: "allow",
        }
    )

    assert not may_manage_reference_data(specialist)


def test_account_administration_does_not_imply_it():
    specialist = factories.UserFactory(
        capability_overrides={"accounts.manage": "allow", "accounts.delegate": "allow"}
    )

    assert not may_manage_reference_data(specialist)


# -- every route refuses whoever may not manage ----------------------------------------


@pytest.fixture
def world(db):
    organisation = factories.OrganisationFactory(name="Matriitsiamet")
    alias = OrganisationAlias.objects.create(organisation=organisation, alias="MA")
    tag = factories.TagFactory(name_et="Matriitssilt")
    tag_alias = tag.aliases.create(alias="Teine matriitssõna")
    return {
        "organisation": organisation,
        "alias": alias,
        "tag": tag,
        "tag_alias": tag_alias,
        "other_tag": factories.TagFactory(name_et="Sihtsilt"),
    }


def _routes(world) -> list[tuple[str, str, dict]]:
    org = world["organisation"]
    tag = world["tag"]
    return [
        ("get", reverse("reference_data:index"), {}),
        ("get", reverse("reference_data:organisations"), {}),
        ("get", reverse("reference_data:organisation_duplicates"), {}),
        ("get", reverse("reference_data:organisation", kwargs={"pk": org.pk}), {}),
        ("get", reverse("reference_data:tags"), {}),
        ("get", reverse("reference_data:tag", kwargs={"pk": tag.pk}), {}),
        ("get", reverse("reference_data:vocabulary", kwargs={"key": "valdkonnad"}), {}),
        ("post", reverse("reference_data:organisation_create"), {"name": "Loata loodud amet"}),
        (
            "post",
            reverse("reference_data:organisation_rename", kwargs={"pk": org.pk}),
            {"name": "Loata nimi", "revision": catalogue.organisation_revision(org)},
        ),
        (
            "post",
            reverse("reference_data:organisation_alias_add", kwargs={"pk": org.pk}),
            {"alias": "LOATA", "alias_type": AliasType.SYNONYM},
        ),
        (
            "post",
            reverse(
                "reference_data:organisation_alias_remove",
                kwargs={"pk": org.pk, "alias_pk": world["alias"].pk},
            ),
            {"revision": catalogue.organisation_revision(org)},
        ),
        ("post", reverse("reference_data:tag_create"), {"name": "Loata", "key": "loata"}),
        (
            "post",
            reverse("reference_data:tag_edit", kwargs={"pk": tag.pk}),
            {"name": "Loata silt", "revision": vocabulary.tag_revision(tag)},
        ),
        (
            "post",
            reverse("reference_data:tag_alias_add", kwargs={"pk": tag.pk}),
            {"alias": "loata nimekuju", "revision": vocabulary.tag_revision(tag)},
        ),
        (
            "post",
            reverse(
                "reference_data:tag_alias_remove",
                kwargs={"pk": tag.pk, "alias_pk": world["tag_alias"].pk},
            ),
            {"revision": vocabulary.tag_revision(tag)},
        ),
        (
            "post",
            reverse("reference_data:tag_merge", kwargs={"pk": tag.pk}),
            {"into": str(world["other_tag"].pk), "revision": vocabulary.tag_revision(tag)},
        ),
        (
            "post",
            reverse("reference_data:tag_status", kwargs={"pk": tag.pk}),
            {"tegevus": "aegunuks", "revision": vocabulary.tag_revision(tag)},
        ),
    ]


def _snapshot(world) -> tuple:
    org = Organisation.objects.get(pk=world["organisation"].pk)
    tag = Tag.objects.get(pk=world["tag"].pk)
    return (
        Organisation.objects.count(),
        org.name,
        sorted(org.aliases.values_list("alias", flat=True)),
        Tag.objects.count(),
        tag.name_et,
        tag.is_active,
        tag.merged_into_id,
        sorted(tag.aliases.values_list("alias", flat=True)),
        ChangeEvent.objects.count(),
    )


REFUSED_CALLERS = {
    "specialist without the grant": lambda: factories.UserFactory(),
    "reader": lambda: factories.ReaderFactory(),
    "reader granted it (not grantable)": lambda: factories.ReaderFactory(
        capability_overrides={MANAGE: "allow"}
    ),
    "technical administrator without the grant": lambda: factories.AdministratorFactory(),
    "superuser without the grant": lambda: factories.AdministratorFactory(
        is_superuser=True, is_staff=True
    ),
    "department head denied it": lambda: factories.DepartmentHeadFactory(
        capability_overrides={MANAGE: "deny"}
    ),
    "accounts administrator": lambda: factories.UserFactory(
        capability_overrides={"accounts.manage": "allow", "accounts.delegate": "allow"}
    ),
}


@pytest.mark.parametrize("caller", sorted(REFUSED_CALLERS))
def test_every_route_answers_a_caller_without_the_capability_with_404(client, world, caller):
    client.force_login(REFUSED_CALLERS[caller]())
    before = _snapshot(world)

    for method, url, data in _routes(world):
        response = getattr(client, method)(url, data)
        assert response.status_code == 404, (caller, method, url, response.status_code)

    assert _snapshot(world) == before


def test_an_anonymous_visitor_is_sent_to_sign_in_and_changes_nothing(client, settings, world):
    settings.AUTH_MODE = "none"
    before = _snapshot(world)

    for method, url, data in _routes(world):
        response = getattr(client, method)(url, data)
        assert response.status_code == 302, (method, url)
        assert response["Location"].startswith(reverse(settings.LOGIN_URL))

    assert _snapshot(world) == before


def test_the_shared_gate_without_a_persona_changes_nothing(settings, world):
    apply_shared_gate(settings)
    client = Client()
    assert client.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302
    before = _snapshot(world)

    for method, url, data in _routes(world):
        response = getattr(client, method)(url, data)
        assert response.status_code in (302, 404), (method, url, response.status_code)

    assert _snapshot(world) == before


@pytest.mark.parametrize(
    "make",
    [
        lambda: factories.DepartmentHeadFactory(),
        lambda: factories.UserFactory(capability_overrides={MANAGE: "allow"}),
        lambda: factories.AdministratorFactory(capability_overrides={MANAGE: "allow"}),
    ],
    ids=["head", "granted-specialist", "granted-administrator"],
)
def test_every_page_answers_a_holder(client, world, make):
    client.force_login(make())

    for method, url, _ in _routes(world):
        if method == "get":
            assert client.get(url).status_code == 200, url


# -- the pages -------------------------------------------------------------------------


def test_a_rename_through_the_page_keeps_the_old_name_and_audits_the_actor(client, department_head):
    body = factories.OrganisationFactory(name="Vana Ametinimi")
    client.force_login(department_head)

    response = client.post(
        reverse("reference_data:organisation_rename", kwargs={"pk": body.pk}),
        {
            "name": "Uus Ametinimi",
            "keep_old_name": "on",
            "revision": catalogue.organisation_revision(body),
        },
    )

    assert response.status_code == 302
    body.refresh_from_db()
    assert body.name == "Uus Ametinimi"
    assert body.aliases.get().alias_type == AliasType.HISTORICAL_NAME
    event = ChangeEvent.objects.get(event_type=ChangeEventType.ORGANISATION_RENAMED)
    assert event.actor == department_head
    # The search on the catalogue page finds it by the name it used to have.
    page = client.get(reverse("reference_data:organisations"), {"q": "vana ametinimi"})
    assert "Uus Ametinimi" in page.content.decode()


def test_a_duplicate_through_the_page_is_a_400_naming_the_existing_row(client, department_head):
    factories.OrganisationFactory(name="Olemasolev Amet")
    client.force_login(department_head)

    response = client.post(
        reverse("reference_data:organisation_create"), {"name": "olemasolev amet"}
    )

    assert response.status_code == 400
    assert "«Olemasolev Amet»" in response.content.decode()
    assert Organisation.objects.filter(normalized_name="olemasolev amet").count() == 1


def test_a_stale_page_is_a_400_and_writes_nothing(client, department_head):
    tag = factories.TagFactory(name_et="Sõnastikusõna")
    client.force_login(department_head)

    response = client.post(
        reverse("reference_data:tag_edit", kwargs={"pk": tag.pk}),
        {"name": "Uus sõna", "revision": "2000-01-01T00:00:00+00:00"},
    )

    assert response.status_code == 400
    assert vocabulary.STALE in response.content.decode()
    tag.refresh_from_db()
    assert tag.name_et == "Sõnastikusõna"


def test_choosing_a_merge_target_opens_a_confirmation_and_merges_nothing(client):
    old = factories.TagFactory(name_et="Kinnitatav")
    new = factories.TagFactory(name_et="Sihtsõna")
    factories.MatterFactory().tags.add(old)
    url = reverse("reference_data:tag_merge", kwargs={"pk": old.pk})
    query = {"into": str(new.pk), "revision": vocabulary.tag_revision(old)}

    client.force_login(factories.DepartmentHeadFactory())
    page = client.get(url, query)

    body = page.content.decode()
    assert page.status_code == 200
    assert "Liida «Kinnitatav» sildiga «Sihtsõna»" in body
    assert "Teemasid sildiga «Kinnitatav»" in body
    old.refresh_from_db()
    assert old.merged_into is None and old.is_active

    client.force_login(factories.AdministratorFactory(capability_overrides={MANAGE: "allow"}))
    hidden = client.get(url, query).content.decode()
    assert "Liida «Kinnitatav» sildiga «Sihtsõna»" in hidden
    assert "Teemasid sildiga" not in hidden


def test_the_tag_page_no_longer_merges_in_one_click(client, department_head):
    tag = factories.TagFactory(name_et="Ühe klõpsuga")
    client.force_login(department_head)

    body = client.get(reverse("reference_data:tag", kwargs={"pk": tag.pk})).content.decode()

    action = reverse("reference_data:tag_merge", kwargs={"pk": tag.pk})
    assert f'method="get" action="{action}"' in body
    assert f'method="post" action="{action}"' not in body


def test_the_references_panel_prints_no_model_keys(client, department_head, specialist):
    body = factories.OrganisationFactory(name="Viidete amet")
    factories.MatterFactory(owner=specialist, addressee_organisation=body)
    client.force_login(department_head)

    page = client.get(reverse("reference_data:organisation", kwargs={"pk": body.pk}))

    html = page.content.decode()
    assert "Teemad, kus see on adressaat" in html
    assert "Matter.addressee_organisation" not in html
    assert "app." not in html.split('id="org-viited"')[1].split("</section>")[0]


def test_a_merge_through_the_page_retires_the_tag(client, department_head):
    old = factories.TagFactory(name_et="Vananenud")
    new = factories.TagFactory(name_et="Kehtiv")
    client.force_login(department_head)

    response = client.post(
        reverse("reference_data:tag_merge", kwargs={"pk": old.pk}),
        {"into": str(new.pk), "revision": vocabulary.tag_revision(old)},
    )

    assert response.status_code == 302
    old.refresh_from_db()
    assert old.merged_into == new and not old.is_active
    # And the merged page offers no further edit.
    page = client.get(reverse("reference_data:tag", kwargs={"pk": old.pk})).content.decode()
    assert reverse("reference_data:tag_edit", kwargs={"pk": old.pk}) not in page


def test_a_refused_alias_removal_says_why(client, department_head):
    body = factories.OrganisationFactory(name="Nimetatud amet")
    alias = OrganisationAlias.objects.create(
        organisation=body, alias="Endine amet", alias_type=AliasType.HISTORICAL_NAME
    )
    client.force_login(department_head)

    response = client.post(
        reverse(
            "reference_data:organisation_alias_remove",
            kwargs={"pk": body.pk, "alias_pk": alias.pk},
        ),
        {"revision": catalogue.organisation_revision(body)},
    )

    assert response.status_code == 302
    assert [str(m) for m in get_messages(response.wsgi_request)] == [catalogue.HISTORICAL_NAME_KEPT]
    assert OrganisationAlias.objects.filter(pk=alias.pk).exists()


def test_an_alias_of_another_organisation_is_not_reachable_through_this_one(
    client, department_head
):
    body = factories.OrganisationFactory(name="Esimene amet")
    other = factories.OrganisationFactory(name="Teine amet")
    alias = OrganisationAlias.objects.create(organisation=other, alias="TA")
    client.force_login(department_head)

    response = client.post(
        reverse(
            "reference_data:organisation_alias_remove",
            kwargs={"pk": body.pk, "alias_pk": alias.pk},
        ),
        {"revision": catalogue.organisation_revision(other)},
    )

    assert response.status_code == 404
    assert OrganisationAlias.objects.filter(pk=alias.pk).exists()


@pytest.mark.parametrize("key", ["valdkonnad", "oigusaktid", "etapid"])
def test_controlled_vocabularies_are_read_only_and_say_how_to_change_them(
    client, department_head, key
):
    factories.StageFactory()
    client.force_login(department_head)

    response = client.get(reverse("reference_data:vocabulary", kwargs={"key": key}))

    body = response.content.decode()
    assert response.status_code == 200
    assert REVIEWED_CHANGE_ONLY in body
    # No form of its own: as many POST forms as the overview, which has none
    # either — whatever the layout itself carries (sign-out) is in both.
    overview = client.get(reverse("reference_data:index")).content.decode()
    assert body.count('method="post"') == overview.count('method="post"')


def test_an_unknown_vocabulary_is_a_404(client, department_head):
    client.force_login(department_head)

    assert (
        client.get(reverse("reference_data:vocabulary", kwargs={"key": "tundmatu"})).status_code
        == 404
    )


def test_usage_counts_are_shown_only_to_somebody_who_may_read_teemad(client):
    tag = factories.TagFactory(name_et="Loendatav")
    matter = factories.MatterFactory()
    matter.tags.add(tag)
    url = reverse("reference_data:tag", kwargs={"pk": tag.pk})

    client.force_login(factories.DepartmentHeadFactory())
    assert "Teemasid" in client.get(url).content.decode()

    client.force_login(factories.AdministratorFactory(capability_overrides={MANAGE: "allow"}))
    assert "Teemasid" not in client.get(url).content.decode()


def test_a_shared_gate_persona_change_is_attributed_to_that_persona(settings):
    apply_shared_gate(settings)
    head = factories.DepartmentHeadFactory(display_name="Katrin Näidisjuht")
    client = Client()
    assert client.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302
    assert client.post(reverse("accounts:act_as"), {"user_id": str(head.pk)}).status_code == 302

    response = client.post(
        reverse("reference_data:tag_create"), {"name": "Ühisparooli silt", "key": "uhisparool"}
    )

    assert response.status_code == 302
    event = ChangeEvent.objects.get(event_type=ChangeEventType.TAG_CREATED)
    assert event.actor == head
    assert event.auth_method == AuthMethod.SHARED_GATE
    assert event.acting_persona_id == head.pk
    assert event.authenticated_principal_id is None
