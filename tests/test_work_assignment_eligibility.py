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
def test_a_crafted_create_post_cannot_name_an_ineligible_owner(
    client, specialist, ineligible, kind
):
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
def test_a_crafted_intake_post_cannot_name_an_ineligible_owner(
    client, specialist, ineligible, kind
):
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


def test_a_new_step_on_a_departed_colleagues_matter_is_refused(client, specialist, former):
    """The correction. A *new* step may not land in a departed colleague's queue.

    An earlier reading of this had the blank field mean "the record speaks", and
    let the step through onto the former owner. That conflates two things: the
    register saying who held a file in 2019, and somebody creating work today.
    Nobody is restating history here — a person is typing a next step — and the
    one place it must not go is the queue nobody opens.

    Refused, and nothing written. A refusal that created the action anyway with
    somebody else's name on it would be the worse outcome of the two.
    """
    matter = factories.MatterFactory(owner=former)
    client.force_login(specialist)

    response = client.post(
        reverse("matters:set_action", kwargs={"pk": matter.pk}),
        {"text": "Ootan vastust", "kind": ActionKind.WAIT},
    )

    assert response.status_code == 400
    assert not NextAction.objects.filter(matter=matter).exists()
    assert "ei ole enam aktiivne osakonna töötaja" in response.content.decode()


def test_an_eligible_responsible_is_accepted(client, specialist, normal_matter, head):
    client.force_login(specialist)

    response = client.post(
        reverse("matters:set_action", kwargs={"pk": normal_matter.pk}),
        {"text": "Koostan vastuse", "kind": ActionKind.WAIT, "responsible": str(head.pk)},
    )

    assert response.status_code == 200
    assert NextAction.objects.get(matter=normal_matter).responsible == head


# =========================================================================
# Järgmiseks — new work, and the owner it may not fall back to
# =========================================================================
#
# `set_next_action` defaults `responsible` to `matter.owner`, which is right for
# an importer recording who an old instruction belonged to and wrong for a
# person creating one now. `set_next_action_for_new_work` is where the two are
# separated; these cases hold both halves of it at once.


def test_an_omitted_responsible_defaults_to_an_owner_who_is_a_specialist(
    client, specialist, normal_matter
):
    """The convenience the composer was built around, untouched."""
    client.force_login(specialist)

    response = client.post(
        reverse("matters:set_action", kwargs={"pk": normal_matter.pk}),
        {"text": "Koostan vastuse", "kind": ActionKind.WAIT},
    )

    assert response.status_code == 200
    assert NextAction.objects.get(matter=normal_matter).responsible == specialist


def test_an_omitted_responsible_defaults_to_an_owner_who_is_the_department_head(
    client, specialist, head
):
    """A DEPARTMENT_HEAD does department work and holds files."""
    matter = factories.MatterFactory(owner=head)
    client.force_login(specialist)

    response = client.post(
        reverse("matters:set_action", kwargs={"pk": matter.pk}),
        {"text": "Koostan vastuse", "kind": ActionKind.WAIT},
    )

    assert response.status_code == 200
    assert NextAction.objects.get(matter=matter).responsible == head


def test_an_omitted_responsible_is_refused_when_the_owner_is_an_administrator(
    client, specialist, administrator
):
    """Not only the inactive shape — every ineligible one.

    An ADMINISTRATOR who was handed a file years ago is still not somebody the
    department gives new work to, and the account is *active*, so a check
    written against `is_active` alone would let this through.
    """
    matter = factories.MatterFactory(owner=administrator)
    client.force_login(specialist)

    response = client.post(
        reverse("matters:set_action", kwargs={"pk": matter.pk}),
        {"text": "Koostan vastuse", "kind": ActionKind.WAIT},
    )

    assert response.status_code == 400
    assert not NextAction.objects.filter(matter=matter).exists()


def test_the_composer_refuses_a_new_step_on_a_departed_colleagues_matter(
    client, specialist, former
):
    """The other native path, and the one that reaches this most often.

    The composer never sends a `responsible` at all — the field is not on that
    surface — so every step it creates takes the fallback. A guard placed only
    on `NextActionForm` would have left this open.
    """
    matter = factories.MatterFactory(owner=former)
    client.force_login(specialist)

    response = client.post(
        reverse("matters:compose", kwargs={"pk": matter.pk}),
        {"body": "<p>Ootan ministeeriumi vastust.</p>", "next_kind": ActionKind.WAIT},
    )

    assert response.status_code == 400
    assert not NextAction.objects.filter(matter=matter).exists()


def test_the_composer_still_creates_a_step_when_the_owner_is_assignable(
    client, specialist, normal_matter
):
    """The same path, unchanged, on an ordinary Matter."""
    client.force_login(specialist)

    response = client.post(
        reverse("matters:compose", kwargs={"pk": normal_matter.pk}),
        {"body": "<p>Ootan ministeeriumi vastust.</p>", "next_kind": ActionKind.WAIT},
    )

    assert response.status_code == 200
    assert NextAction.objects.get(matter=normal_matter).responsible == specialist


def test_a_matter_with_no_owner_at_all_still_takes_a_step(client, specialist):
    """Not the case this correction is about, and deliberately not changed.

    An unowned Matter has nobody to fall back *to*. The step is stored with no
    responsible person, exactly as it was before — refusing it would retire
    working behaviour under cover of a fix.
    """
    matter = factories.MatterFactory(owner=None)
    client.force_login(specialist)

    response = client.post(
        reverse("matters:set_action", kwargs={"pk": matter.pk}),
        {"text": "Ootan vastust", "kind": ActionKind.WAIT},
    )

    assert response.status_code == 200
    assert NextAction.objects.get(matter=matter).responsible is None


def test_the_historical_service_still_records_a_departed_responsible(former):
    """The boundary is native work; import is on the other side of it.

    An enrichment run reconstructing a 2019 instruction says who it belonged to,
    and that person left in 2021. `set_next_action` — the service the importers
    call — must go on accepting them, or the archive starts attributing old
    instructions to whoever is here now.
    """
    from app.workflow.services import set_next_action

    matter = factories.MatterFactory(owner=former)

    action = set_next_action(
        matter=matter,
        text="Ootame ministeeriumi seisukohta",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.EXPECTED_AROUND,
        responsible=former,
        provenance={"source": "register", "era": "2019"},
    )

    assert action.responsible == former


def test_the_historical_service_still_falls_back_to_a_departed_owner(former):
    """The same, with nothing named at all.

    An imported row that names no responsible person means *the owner*, and the
    owner of a 2019 file may well have left. The native wrapper refuses this;
    the service underneath it must not, or every such row would import blank.
    """
    from app.workflow.services import set_next_action

    matter = factories.MatterFactory(owner=former)

    action = set_next_action(
        matter=matter,
        text="Ootame ministeeriumi seisukohta",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.EXPECTED_AROUND,
        provenance={"source": "register", "era": "2019"},
    )

    assert action.responsible == former


def test_the_native_wrapper_refuses_an_explicitly_ineligible_person(specialist, administrator):
    """The service boundary, not only the form's.

    The forms narrow their querysets, so a browser cannot send this. The wrapper
    refuses it anyway: a second native caller added next year inherits the rule
    instead of having to remember it.
    """
    from app.core.errors import DomainError
    from app.workflow.services import set_next_action_for_new_work

    matter = factories.MatterFactory(owner=specialist)

    with pytest.raises(DomainError):
        set_next_action_for_new_work(
            matter=matter,
            text="Meisterdatud samm",
            kind=ActionKind.WAIT,
            date_semantics=DateSemantics.EXPECTED_AROUND,
            responsible=administrator,
        )

    assert not NextAction.objects.filter(matter=matter).exists()


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


def test_the_inline_control_accepts_the_departed_owner_it_is_displaying(client, specialist, former):
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
    """Nobody in this world owns anything, so the union adds nothing.

    Which makes it the case for the other half of the rule: a current worker
    with no current work is still on the list — filtering to a colleague and
    getting an honest empty page is an answer — while an account that exists
    but owns nothing is not put there by existing.
    """
    client.force_login(specialist)

    offered = set(client.get(reverse("matters:matter_list")).context["owners"])

    assert specialist in offered
    assert head in offered
    assert administrator not in offered
    assert reader not in offered
    assert former not in offered


def test_the_register_filter_offers_a_departed_colleague_who_owns_visible_work(
    client, specialist, former
):
    """The correction, and the case the narrowing broke.

    Somebody leaves holding seventeen unhandled files. Those files are exactly
    what a colleague comes to this control looking for, and the earlier reading
    — current workers, plus whoever the URL already named — left the departed
    owner reachable only by somebody who already knew their UUID. A filter
    describes the work that exists; it does not hand any out.
    """
    factories.MatterFactory(owner=former, title="Endise kolleegi teema")
    client.force_login(specialist)

    offered = set(client.get(reverse("matters:matter_list")).context["owners"])

    assert former in offered


def test_the_register_filter_does_not_name_an_owner_of_work_this_reader_cannot_see(
    client, specialist, former
):
    """The boundary the union is drawn inside.

    `former` owns one RESTRICTED Matter and nothing else. A plain SPECIALIST who
    is neither its owner nor a collaborator may not read it — and an option in a
    dropdown is a name on a page. Offering it would disclose the person, the
    fact that they hold something, and that it is something this reader is not
    allowed to open.
    """
    factories.MatterFactory(owner=former, visibility=Visibility.RESTRICTED)
    client.force_login(specialist)

    response = client.get(reverse("matters:matter_list"))

    assert former not in set(response.context["owners"])
    assert former.display_name not in response.content.decode()


def test_the_register_filter_names_that_owner_to_a_reader_who_may_see_the_work(
    client, head, former
):
    """The same world, a reader who is allowed it.

    A DEPARTMENT_HEAD sees restricted material by role, so the same Matter is
    part of their authorized population and its owner belongs in their filter.
    The option list follows authorization rather than a second rule of its own.
    """
    factories.MatterFactory(owner=former, visibility=Visibility.RESTRICTED)
    client.force_login(head)

    offered = set(client.get(reverse("matters:matter_list")).context["owners"])

    assert former in offered


def test_the_register_filter_offers_an_administrator_who_genuinely_owns_a_matter(
    client, specialist, administrator
):
    """A data fact, and not a promotion.

    The register says this account holds a file, so the control that describes
    the register has to be able to say so. It changes nothing about assignment:
    the chooser on the same page still refuses them, which the header and edit
    cases above assert.
    """
    factories.MatterFactory(owner=administrator)
    client.force_login(specialist)

    offered = set(client.get(reverse("matters:matter_list")).context["owners"])

    assert administrator in offered


def test_choosing_an_owner_does_not_reduce_the_register_filter_to_that_owner(
    client, specialist, head, former
):
    """Read before the register's own `vastutaja` filter, deliberately.

    Derived from the already-filtered population, the select would offer one
    name — the one selected — and there would be no way back to anybody else
    without editing the URL.
    """
    factories.MatterFactory(owner=former)
    client.force_login(specialist)

    response = client.get(reverse("matters:matter_list"), {"vastutaja": str(former.pk)})
    offered = set(response.context["owners"])

    assert former in offered
    assert specialist in offered
    assert head in offered


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
    """Same shape as the register, on an empty reported population."""
    client.force_login(specialist)

    offered = set(client.get(reverse("reporting:matters")).context["owners"])

    assert specialist in offered
    assert head in offered
    assert administrator not in offered
    assert reader not in offered
    assert former not in offered


def test_the_reporting_filter_offers_a_departed_colleague_who_owns_reported_work(
    client, specialist, former
):
    """A report about the year somebody was here has to be able to name them."""
    factories.MatterFactory(owner=former)
    client.force_login(specialist)

    offered = set(client.get(reverse("reporting:matters")).context["owners"])

    assert former in offered


def test_the_reporting_filter_does_not_name_an_owner_of_unreadable_work(client, specialist, former):
    """The reported population is `visible_to` narrowed by `real_data`.

    Both halves bound the option list. This case is the authorization half; the
    one below is the data-class half.
    """
    factories.MatterFactory(owner=former, visibility=Visibility.RESTRICTED)
    client.force_login(specialist)

    response = client.get(reverse("reporting:matters"))

    assert former not in set(response.context["owners"])
    assert former.display_name not in response.content.decode()


def test_the_reporting_filter_does_not_name_an_owner_of_development_records_only(
    client, specialist, former
):
    """Statistics are real data, so the filter above them is too.

    A colleague whose only remaining Matter is a TEST record is not part of what
    this page reports, and offering them would put a name on a control that
    cannot change a single figure on the page.
    """
    from app.matters.enums import MatterDataClass

    factories.MatterFactory(owner=former, data_class=MatterDataClass.TEST)
    client.force_login(specialist)

    offered = set(client.get(reverse("reporting:matters")).context["owners"])

    assert former not in offered


def test_a_report_count_does_not_drop_because_an_owner_left(client, specialist, former):
    """The rows are untouched. Only the chooser was ever narrowed.

    The point of separating the two rules: the departed colleague's Matter still
    counts, still aggregates, and is still reachable through the filter that now
    names them.
    """
    from app.reporting import context as ctx

    factories.MatterFactory(owner=former)
    client.force_login(specialist)

    unfiltered = client.get(reverse("reporting:matters"))
    filtered = client.get(reverse("reporting:matters"), {ctx.PARAM_OWNER: str(former.pk)})

    assert unfiltered.status_code == 200
    assert filtered.status_code == 200
    assert former in set(filtered.context["owners"])


def test_choosing_an_owner_does_not_reduce_the_reporting_filter_to_that_owner(
    client, specialist, head, former
):
    """Read from `reporting_population`, which is the context before its own
    owner filter — the same pre-filter boundary the register uses."""
    from app.reporting import context as ctx

    factories.MatterFactory(owner=former)
    client.force_login(specialist)

    response = client.get(reverse("reporting:matters"), {ctx.PARAM_OWNER: str(former.pk)})
    offered = set(response.context["owners"])

    assert former in offered
    assert specialist in offered
    assert head in offered


def test_a_report_filtered_on_a_departed_colleague_still_names_them(client, specialist, former):
    """And names them because the reported population says so, not because the
    URL did. The Matter is what puts them in the select; a bare identifier in
    the address bar would not, and must not (see the leak case below).
    """
    from app.reporting import context as ctx

    factories.MatterFactory(owner=former)
    client.force_login(specialist)

    response = client.get(reverse("reporting:matters"), {ctx.PARAM_OWNER: str(former.pk)})

    assert former in set(response.context["owners"])


def test_a_report_chip_names_a_departed_colleague_rather_than_calling_them_unknown(
    client, specialist, former
):
    """Display of stored history, not a new assignment.

    "tundmatu" is reserved for an identifier that names nobody *this reader can
    see*. A colleague who has left is not unknown; the department knows exactly
    who they are, and the report is about the year they were here.

    The Matter is what makes that true, and it is now created here. The test
    used to assert the name from a bare identifier with no work behind it — the
    case the sibling test above calls out as the one that "must not" name
    anybody, and the leak case it says is "below". That case exists now
    (tests/test_child_projection_visibility.py) and this one is its other half:
    represented work, truthfully named (AUTH-003).
    """
    from app.reporting import context as ctx

    factories.MatterFactory(owner=former)
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
