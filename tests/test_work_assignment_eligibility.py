"""Who current business work may be given to, on every surface that gives it.

`test_department_workers.py` asserts the rule. This suite asserts that every
control which hands out work reads it, and — the half that is easy to get wrong
— that narrowing those controls does not rewrite what old records already say.

The distinction every case here turns on
----------------------------------------
**A chooser** answers *who may be given this now*. It offers current department
workers, and nobody else.

**A record** answers *who has this*. A Matter filed in 2019 may name a colleague
who has since left, and that is a true fact. Correcting its title must not
depend on rewriting it, a report counting it must not drop it, and a filter
chip naming them must not read "tundmatu".

Both must hold at once, and the failure modes are opposite: a chooser that is
too wide hands a file to the administrator account; a chooser that is too narrow
silently clears an owner during an unrelated edit. Neither fails loudly.

Why the crafted POSTs
---------------------
HTML is not the boundary. Two of the fields narrowed here — `NextActionForm`'s
`responsible` and `MatterFieldForm`'s `owner` — are not rendered as a select on
every surface that accepts them, so "the option is not in the template" was
never protection. Each case below posts an identifier a browser would not offer
and asserts the endpoint refuses it.
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.accounts.enums import UserRole
from app.core.enums import Visibility
from app.matters.models import Matter
from app.workflow.enums import ActionKind, DateSemantics
from app.workflow.models import NextAction
from tests import factories

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")


def _inactive(person):
    type(person).objects.filter(pk=person.pk).update(is_active=False)
    person.refresh_from_db()
    return person


@pytest.fixture
def head(db):
    return factories.DepartmentHeadFactory(display_name="Osakonnajuht Näidis")


@pytest.fixture
def reader(db):
    return factories.ReaderFactory(display_name="Lugeja Näidis")


@pytest.fixture
def staff_specialist(db):
    return factories.UserFactory(
        role=UserRole.SPECIALIST, is_staff=True, display_name="Tehniline spetsialist"
    )


@pytest.fixture
def superuser_head(db):
    return factories.UserFactory(
        role=UserRole.DEPARTMENT_HEAD, is_superuser=True, display_name="Juurkasutaja"
    )


@pytest.fixture
def former(db):
    """A colleague who has left, still holding work in the register."""
    return _inactive(factories.UserFactory(display_name="Endine Kolleeg"))


@pytest.fixture
def ineligible(request, administrator, reader, staff_specialist, superuser_head, former):
    """Every shape of account that may not be given new work, one per case."""
    return {
        "administrator": administrator,
        "reader": reader,
        "staff": staff_specialist,
        "superuser": superuser_head,
        "inactive": former,
    }


INELIGIBLE_KINDS = ["administrator", "reader", "staff", "superuser", "inactive"]


# =========================================================================
# Uus teema — the create form
# =========================================================================


def test_the_create_form_offers_the_department_and_nobody_else(
    specialist, head, administrator, reader, staff_specialist, superuser_head, former
):
    from app.matters.forms import MatterCreateForm

    offered = _offered(MatterCreateForm(viewer=specialist), "owner")

    assert specialist in offered
    assert head in offered
    for excluded in (administrator, reader, staff_specialist, superuser_head, former):
        assert excluded not in offered


@pytest.mark.parametrize("kind", INELIGIBLE_KINDS)
def test_a_crafted_create_post_cannot_name_an_ineligible_owner(client, specialist, ineligible, kind):
    """The form refuses, and no Matter is written with that owner.

    Both halves matter. A form that merely dropped the value would create the
    Matter unowned, which looks like success to whoever sent the request.
    """
    client.force_login(specialist)
    target = ineligible[kind]

    response = client.post(CREATE, {"title": "Meisterdatud teema", "owner": str(target.pk)})

    assert response.status_code == 400
    assert not Matter.objects.filter(owner=target).exists()
    assert not Matter.objects.filter(title="Meisterdatud teema").exists()


def test_an_eligible_owner_still_creates_a_matter(client, specialist, head):
    client.force_login(specialist)

    response = client.post(CREATE, {"title": "Tavaline teema", "owner": str(head.pk)})

    assert response.status_code == 302
    assert Matter.objects.get(title="Tavaline teema").owner == head


# =========================================================================
# Saabunud — intake must not be the way around Uus teema
# =========================================================================


def test_the_intake_form_offers_the_department_and_nobody_else(
    specialist, head, administrator, former
):
    from app.matters.forms import IncomingIntakeForm

    offered = _offered(IncomingIntakeForm(), "owner")

    assert specialist in offered
    assert head in offered
    assert administrator not in offered
    assert former not in offered


@pytest.mark.parametrize("kind", INELIGIBLE_KINDS)
def test_a_crafted_intake_post_cannot_name_an_ineligible_owner(client, specialist, ineligible, kind):
    """And leaves nothing behind — no Matter, and no document either.

    Intake writes a Matter and its evidence in one transaction. A refusal that
    let the upload through would leave a titled Matter with files on it and no
    owner, which reads as real work nobody started.
    """
    from app.documents.models import Document

    client.force_login(specialist)
    target = ineligible[kind]
    before = Matter.objects.count()

    response = client.post(
        reverse("matters:intake"),
        {
            "title": "Meisterdatud saabumine",
            "visibility": Visibility.NORMAL,
            "owner": str(target.pk),
            "uploads": [SimpleUploadedFile("eelnou.pdf", b"%PDF-1.4 synthetic")],
        },
    )

    assert response.status_code == 400
    assert Matter.objects.count() == before
    assert not Document.objects.filter(title="eelnou.pdf").exists()


# =========================================================================
# Järgmiseks — the responsible person
# =========================================================================


def test_the_next_action_form_offers_the_department_and_nobody_else(
    specialist, head, administrator, reader, staff_specialist, superuser_head, former
):
    from app.matters.forms import NextActionForm

    offered = _offered(NextActionForm(), "responsible")

    assert specialist in offered
    assert head in offered
    for excluded in (administrator, reader, staff_specialist, superuser_head, former):
        assert excluded not in offered


@pytest.mark.parametrize("kind", INELIGIBLE_KINDS)
def test_a_crafted_next_action_post_cannot_name_an_ineligible_responsible(
    client, specialist, normal_matter, ineligible, kind
):
    """Refused, and not quietly demoted to the Matter owner.

    An explicitly invalid assignment is an error. Falling back to
    `matter.owner` here would turn a rejected instruction into a silently
    different one, and the audit row would record a decision nobody made.
    """
    client.force_login(specialist)
    target = ineligible[kind]

    response = client.post(
        reverse("matters:set_action", kwargs={"pk": normal_matter.pk}),
        {
            "text": "Meisterdatud samm",
            "kind": ActionKind.WAIT,
            "date_semantics": DateSemantics.EXPECTED_AROUND,
            "responsible": str(target.pk),
        },
    )

    assert response.status_code == 400
    assert not NextAction.objects.filter(matter=normal_matter).exists()


def test_an_omitted_responsible_still_falls_back_to_the_matter_owner(
    client, specialist, normal_matter
):
    """The approved default, unchanged.

    Leaving the field blank means *the Matter's Vastutaja*, which is the record
    speaking rather than somebody choosing. It is deliberately untouched here,
    including on a Matter whose owner has left — see the historical case below.
    """
    client.force_login(specialist)

    response = client.post(
        reverse("matters:set_action", kwargs={"pk": normal_matter.pk}),
        {"text": "Ootan vastust", "kind": ActionKind.WAIT},
    )

    assert response.status_code == 200
    assert NextAction.objects.get(matter=normal_matter).responsible == normal_matter.owner


def test_a_step_on_a_departed_colleagues_matter_still_defaults_to_them(client, specialist, former):
    """The record says who holds the file, and the blank field means the record.

    The narrowing is about *choosing* somebody. Nobody chose here, so nothing
    about who owns this Matter is being restated.
    """
    matter = factories.MatterFactory(owner=former)
    client.force_login(specialist)

    response = client.post(
        reverse("matters:set_action", kwargs={"pk": matter.pk}),
        {"text": "Ootan vastust", "kind": ActionKind.WAIT},
    )

    assert response.status_code == 200
    assert NextAction.objects.get(matter=matter).responsible == former


def test_an_eligible_responsible_is_accepted(client, specialist, normal_matter, head):
    client.force_login(specialist)

    response = client.post(
        reverse("matters:set_action", kwargs={"pk": normal_matter.pk}),
        {"text": "Koostan vastuse", "kind": ActionKind.WAIT, "responsible": str(head.pk)},
    )

    assert response.status_code == 200
    assert NextAction.objects.get(matter=normal_matter).responsible == head


# =========================================================================
# Teema muutmine — the edit page, and the owner it must not lose
# =========================================================================


def test_the_edit_form_offers_the_department_when_the_owner_is_one_of_them(
    specialist, head, administrator, former
):
    from app.matters.forms import MatterEditForm

    matter = factories.MatterFactory(owner=specialist)
    offered = _offered(MatterEditForm(matter=matter), "owner")

    assert specialist in offered
    assert head in offered
    assert administrator not in offered
    assert former not in offered


def test_the_edit_form_keeps_an_owner_who_is_no_longer_assignable(specialist, former):
    from app.matters.forms import MatterEditForm

    matter = factories.MatterFactory(owner=former)
    offered = _offered(MatterEditForm(matter=matter), "owner")

    assert former in offered
    assert specialist in offered


def test_an_unrelated_edit_preserves_a_departed_owner_exactly(client, specialist, former):
    """The case this whole union exists for.

    Somebody corrects a title on a file that belonged to a colleague who left.
    The owner must come out the far side unchanged — not cleared, not moved to
    whoever happens to be signed in, and not the reason the save fails.
    """
    matter = factories.MatterFactory(owner=former, title="Vana pealkiri")
    client.force_login(specialist)

    response = client.post(
        reverse("matters:matter_edit", kwargs={"pk": matter.pk}),
        {"title": "Parandatud pealkiri", "owner": str(former.pk), "visibility": Visibility.NORMAL},
    )

    assert response.status_code == 302
    matter.refresh_from_db()
    assert matter.title == "Parandatud pealkiri"
    assert matter.owner == former


@pytest.mark.parametrize("kind", INELIGIBLE_KINDS)
def test_a_crafted_edit_post_cannot_hand_a_matter_to_an_ineligible_account(
    client, specialist, normal_matter, ineligible, kind
):
    client.force_login(specialist)
    target = ineligible[kind]
    before = normal_matter.owner

    response = client.post(
        reverse("matters:matter_edit", kwargs={"pk": normal_matter.pk}),
        {"title": normal_matter.title, "owner": str(target.pk), "visibility": Visibility.NORMAL},
    )

    assert response.status_code == 400
    normal_matter.refresh_from_db()
    assert normal_matter.owner == before


def test_a_matter_held_by_one_departed_colleague_cannot_be_handed_to_another(
    client, specialist, former
):
    """The union keeps *this* record's person, not everybody like them.

    The sharp case, and the one a looser implementation gets wrong: widening the
    queryset to "inactive users too" would make every departed colleague a
    legitimate new choice on any Matter that already had one.
    """
    other_former = _inactive(factories.UserFactory(display_name="Teine Endine"))
    matter = factories.MatterFactory(owner=former)
    client.force_login(specialist)

    response = client.post(
        reverse("matters:matter_edit", kwargs={"pk": matter.pk}),
        {"title": matter.title, "owner": str(other_former.pk), "visibility": Visibility.NORMAL},
    )

    assert response.status_code == 400
    matter.refresh_from_db()
    assert matter.owner == former


def test_a_departed_owner_may_be_replaced_by_a_current_colleague(client, specialist, former, head):
    matter = factories.MatterFactory(owner=former)
    client.force_login(specialist)

    response = client.post(
        reverse("matters:matter_edit", kwargs={"pk": matter.pk}),
        {"title": matter.title, "owner": str(head.pk), "visibility": Visibility.NORMAL},
    )

    assert response.status_code == 302
    matter.refresh_from_db()
    assert matter.owner == head


def test_a_departed_owner_preserved_on_one_matter_is_not_offered_on_another(
    specialist, former, normal_matter
):
    """Preservation is per record, not a licence granted to the whole product."""
    from app.matters.forms import MatterEditForm

    factories.MatterFactory(owner=former)
    offered = _offered(MatterEditForm(matter=normal_matter), "owner")

    assert former not in offered


def test_the_edit_page_can_still_take_an_owner_off_a_matter(client, specialist, normal_matter):
    """`Määramata` is a real answer, and narrowing the list must not remove it."""
    client.force_login(specialist)

    response = client.post(
        reverse("matters:matter_edit", kwargs={"pk": normal_matter.pk}),
        {"title": normal_matter.title, "owner": "", "visibility": Visibility.NORMAL},
    )

    assert response.status_code == 302
    normal_matter.refresh_from_db()
    assert normal_matter.owner is None


# =========================================================================
# The inline owner control on the Teema header — handover in one click
# =========================================================================


@pytest.mark.parametrize("kind", INELIGIBLE_KINDS)
def test_a_crafted_inline_handover_cannot_name_an_ineligible_account(
    client, specialist, normal_matter, ineligible, kind
):
    client.force_login(specialist)
    target = ineligible[kind]
    before = normal_matter.owner

    response = client.post(
        reverse("matters:update_field", kwargs={"pk": normal_matter.pk, "field": "owner"}),
        {"owner": str(target.pk)},
    )

    assert response.status_code == 400
    normal_matter.refresh_from_db()
    assert normal_matter.owner == before


def test_an_inline_handover_to_a_colleague_succeeds(client, specialist, normal_matter, head):
    client.force_login(specialist)

    response = client.post(
        reverse("matters:update_field", kwargs={"pk": normal_matter.pk, "field": "owner"}),
        {"owner": str(head.pk)},
    )

    assert response.status_code == 200
    normal_matter.refresh_from_db()
    assert normal_matter.owner == head


def test_the_inline_control_accepts_the_departed_owner_it_is_displaying(
    client, specialist, former
):
    """Pressing Salvesta having changed nothing must be a save, not a refusal.

    The header renders this Matter's owner as the selected option. A queryset
    narrowed to the current workers alone would make the control refuse the
    value it is showing, which is the one thing a control must never do.
    """
    matter = factories.MatterFactory(owner=former)
    client.force_login(specialist)

    response = client.post(
        reverse("matters:update_field", kwargs={"pk": matter.pk, "field": "owner"}),
        {"owner": str(former.pk)},
    )

    assert response.status_code == 200
    matter.refresh_from_db()
    assert matter.owner == former


def test_an_inline_edit_of_another_field_leaves_a_departed_owner_alone(client, specialist, former):
    """Each inline control posts its own field, and only its own field.

    So changing the Hetkeseis on an old file never carries an owner at all. The
    case exists because the opposite would be an easy thing to introduce — one
    form for the whole header, where an untouched owner is re-submitted on every
    edit and a narrowed population would clear it.
    """
    matter = factories.MatterFactory(owner=former)
    stage = factories.StageFactory()
    client.force_login(specialist)

    response = client.post(
        reverse("matters:update_field", kwargs={"pk": matter.pk, "field": "stage"}),
        {"stage": str(stage.pk)},
    )

    assert response.status_code == 200
    matter.refresh_from_db()
    assert matter.owner == former
    assert matter.stage == stage


def test_the_header_offers_the_departed_owner_it_is_showing(client, specialist, former):
    """The select and the form agree.

    A control offering more than the endpoint accepts is a save that fails on
    submit; one offering less than it accepts drops the value it is displaying.
    """
    matter = factories.MatterFactory(owner=former)
    client.force_login(specialist)

    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))

    offered = set(response.context["owners"])
    assert former in offered
    assert specialist in offered


def test_the_header_does_not_offer_the_administrator_account(client, specialist, administrator):
    matter = factories.MatterFactory(owner=specialist)
    client.force_login(specialist)

    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))

    assert administrator not in set(response.context["owners"])


# =========================================================================
# Filters — a chooser narrows; the rows behind it do not
# =========================================================================


def test_the_register_vastutaja_filter_offers_current_department_workers(
    client, specialist, head, administrator, reader, former
):
    client.force_login(specialist)

    offered = set(client.get(reverse("matters:matter_list")).context["owners"])

    assert specialist in offered
    assert head in offered
    assert administrator not in offered
    assert reader not in offered
    assert former not in offered


def test_a_register_filter_on_a_departed_colleague_still_names_them(client, specialist, former):
    """Otherwise the select reads `Kõik` on a page that is filtering by somebody.

    A saved link, a bookmark or a drill-through from the department table can
    carry a historical owner. The rows it selects are real, so the control has
    to be able to say whose they are.
    """
    factories.MatterFactory(owner=former, title="Endise kolleegi teema")
    client.force_login(specialist)

    response = client.get(reverse("matters:matter_list"), {"vastutaja": str(former.pk)})

    assert former in set(response.context["owners"])
    assert former.get_short_name() in response.content.decode()


def test_a_register_filter_on_a_departed_colleague_still_returns_their_rows(
    client, specialist, former
):
    """The chooser narrowed; the register did not."""
    factories.MatterFactory(owner=former, title="Endise kolleegi teema")
    client.force_login(specialist)

    response = client.get(reverse("matters:matter_list"), {"vastutaja": str(former.pk)})

    assert "Endise kolleegi teema" in response.content.decode()


def test_the_missing_owner_sentinel_does_not_break_the_filter(client, specialist):
    """`?vastutaja=puudub` is not a primary key, and must not be looked up as one."""
    client.force_login(specialist)

    from app.matters import selectors

    response = client.get(reverse("matters:matter_list"), {"vastutaja": selectors.MISSING})

    assert response.status_code == 200


def test_a_malformed_owner_filter_does_not_break_the_page(client, specialist):
    client.force_login(specialist)

    response = client.get(reverse("matters:matter_list"), {"vastutaja": "mitte-uuid"})

    assert response.status_code == 200


def test_the_reporting_vastutaja_filter_offers_current_department_workers(
    client, specialist, head, administrator, reader, former
):
    client.force_login(specialist)

    offered = set(client.get(reverse("reporting:matters")).context["owners"])

    assert specialist in offered
    assert head in offered
    assert administrator not in offered
    assert reader not in offered
    assert former not in offered


def test_a_report_filtered_on_a_departed_colleague_still_names_them(client, specialist, former):
    from app.reporting import context as ctx

    client.force_login(specialist)

    response = client.get(reverse("reporting:matters"), {ctx.PARAM_OWNER: str(former.pk)})

    assert former in set(response.context["owners"])


def test_a_report_chip_names_a_departed_colleague_rather_than_calling_them_unknown(
    client, specialist, former
):
    """Display of stored history, not a new assignment.

    "tundmatu" is reserved for an identifier that names nobody at all. A
    colleague who has left is not unknown; the department knows exactly who
    they are, and the report is about the year they were here.
    """
    from app.reporting import context as ctx

    client.force_login(specialist)

    response = client.get(reverse("reporting:matters"), {ctx.PARAM_OWNER: str(former.pk)})
    values = [chip.value for chip in response.context["chips"]]

    assert former.display_name in values
    assert "tundmatu" not in values


# =========================================================================
# Nothing here writes history
# =========================================================================


def test_narrowing_the_choosers_changes_no_stored_owner(client, specialist, former):
    """A rendering pass is not a migration.

    Opening every surface that lists people leaves the record exactly as it
    was — the guarantee that this branch adds no data migration and repairs no
    existing row.
    """
    matter = factories.MatterFactory(owner=former)
    action = factories.NextActionFactory(
        matter=matter, responsible=former, target_date=timezone.localdate()
    )
    client.force_login(specialist)

    client.get(reverse("matters:matter_list"))
    client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    client.get(reverse("matters:matter_edit", kwargs={"pk": matter.pk}))

    matter.refresh_from_db()
    action.refresh_from_db()
    assert matter.owner == former
    assert action.responsible == former


def _offered(form, field):
    """The people a choice field is offering.

    Read off the field's queryset rather than off its rendered choices, because
    the queryset is what validation accepts. A control whose choices and whose
    queryset differ is exactly the defect these cases are about, and asserting
    the rendered list would leave the half that decides unexamined.
    """
    return set(form.fields[field].queryset)
