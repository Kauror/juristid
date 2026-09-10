"""`Uus teema`: the Saatja is the Adressaat until somebody says otherwise.

A file arrives from X and is normally answered to X. Until docs/adr/0069 the
form made a person say so twice — the sender was moved to the front of the
addressee choices and deliberately never chosen — and the reasoning behind that
was about a real risk: guessing a counterparty puts a fact on the register
nobody stated. What it weighed wrongly is the *trade*. The ordinary case became
free and the unusual one costs one correction, made in the open, on a field that
says who it is answering.

Four things this file pins, and the second and third are the ones that make the
first safe:

**The default.** One named sender, nothing said about Adressaat, and the saved
Matter is answered to that sender — with **no JavaScript anywhere in this file**.
Every test here is a POST or a bound form. The browser mirrors the rule live so
the page reads correctly while somebody is filling it in
(`e2e/test_counterparty_selection.py`), but the saved business fact does not
depend on a script having run (task §10).

**The override.** An answer given by a person outranks anything derived, for
ever, and nothing later takes it back — not a sender being added, removed or
re-sorted, and not a refused save re-rendering the form (§6, §7, §11).

**The refusal to guess.** Two senders is a Matter that arrived from two places
and no unambiguous body to answer, so Adressaat stays unanswered rather than
being decided by whichever row the database handed back first (§9).

**The honest retraction.** A default that exists only because of a sender goes
when the sender does. It never stands on nothing (§8).
"""

from __future__ import annotations

import re
import uuid

import pytest
from django.urls import reverse

from app.matters.forms import MatterCreateForm
from app.matters.models import Matter
from app.organisations.models import Organisation, OrganisationType
from tests import factories

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")

#: The Adressaat disclosure's own opening tag, whatever attributes it carries.
_DISCLOSURE = re.compile(r"<details[^>]*data-addressee-disclosure[^>]*>")


@pytest.fixture
def ministry():
    return Organisation.objects.create(
        name="Kliimaministeerium", organisation_type=OrganisationType.OTHER
    )


@pytest.fixture
def committee():
    return Organisation.objects.create(
        name="Riigikogu majanduskomisjon", organisation_type=OrganisationType.OTHER
    )


def _disclosure(page: str) -> str:
    match = _DISCLOSURE.search(page)
    assert match, "the Adressaat disclosure is not on the page at all"
    return match.group(0)


def _summary(page: str) -> str:
    """What the closed disclosure says, as one line of text.

    Read as text rather than as markup because the value sits inside its own
    `<span>` — the browser rewrites that span while somebody is still filling
    the form in — so «Adressaat · Kliimaministeerium» is never a contiguous
    substring of the HTML and asserting on one would be asserting on the
    element boundary rather than on the sentence.
    """
    body = page[page.index(_disclosure(page)) :]
    inner = body[body.index("<summary") : body.index("</summary>")]
    return " ".join(re.sub(r"<[^>]*>", "", inner[inner.index(">") + 1 :]).split())


def _is_checked(page: str, organisation: Organisation) -> bool:
    """Whether the addressee radio for one body came back ticked.

    By value, never by the auto-generated `id` beside it: the option's index in
    the group moves whenever a sender is moved to the front of the shortlist,
    which is exactly what happens on the forms these tests post.
    """
    for tag in re.findall(r"<input[^>]*name=\"addressee_organisation\"[^>]*>", page):
        if f'value="{organisation.pk}"' in tag:
            return "checked" in tag
    raise AssertionError(f"{organisation.name} is not offered as an addressee at all")


def _answer(**data) -> Organisation | None:
    """What a bound `MatterCreateForm` says the addressee is.

    The form rather than a saved Matter wherever the question is only about the
    rule, because a POST drags an owner, a stage, files and a next action
    through the assertion with it. The POSTs below are for the claims that are
    genuinely about what gets *stored*.
    """
    form = MatterCreateForm({"title": "Vastus", **data}, viewer=None)
    assert form.is_valid(), form.errors
    return form.cleaned_data["addressee_organisation"]


# ---------------------------------------------------------------------------
# The disclosure, closed
# ---------------------------------------------------------------------------


def test_a_fresh_form_renders_the_addressee_disclosure_closed(signed_in, ministry):
    """Nothing to do here on arrival, so nothing takes a row.

    Adressaat is answered by the time somebody reaches it on the ordinary visit,
    and a control with nothing left to do does not need a row of chips (§3).
    """
    page = signed_in.get(CREATE).content.decode()

    assert " open" not in _disclosure(page)
    # And it still says what it is, so the fold is not a hiding place.
    assert "Adressaat" in page


def test_the_closed_summary_names_the_body_that_was_answered(signed_in, ministry):
    """«Adressaat · Kliimaministeerium», without opening anything.

    A disclosure that did not say whether the question had been answered would
    make somebody open it every single time to find out, which is the whole cost
    the fold was supposed to remove (§12).
    """
    page = signed_in.post(
        CREATE, {"title": "", "source_organisations": [str(ministry.pk)]}
    ).content.decode()

    assert _summary(page) == "Adressaat · Kliimaministeerium"


def test_an_unanswered_addressee_summarises_as_the_word_alone(signed_in, ministry):
    """`Määramata` is the absence of an answer, so it is not printed as one."""
    page = signed_in.get(CREATE).content.decode()

    assert _summary(page) == "Adressaat"


def test_the_default_does_not_force_the_disclosure_open(signed_in, ministry):
    """A page that unfolded a section because it had answered a question itself
    would be reacting to its own writing (§4)."""
    page = signed_in.post(
        CREATE, {"title": "", "source_organisations": [str(ministry.pk)]}
    ).content.decode()

    assert " open" not in _disclosure(page)


def test_an_error_on_the_addressee_opens_the_disclosure(signed_in, ministry):
    """A refusal nobody can see is a form that appears to have refused for no
    reason (§11 C)."""
    page = signed_in.post(
        CREATE,
        {"title": "Vastus", "addressee_organisation": str(uuid.uuid4())},
    ).content.decode()

    assert " open" in _disclosure(page)


# ---------------------------------------------------------------------------
# One sender, no script
# ---------------------------------------------------------------------------


def test_one_chosen_sender_becomes_the_addressee(ministry):
    assert _answer(source_organisations=[str(ministry.pk)]) == ministry


def test_a_sender_chosen_from_the_long_tail_defaults_it_too(ministry):
    """Both sender controls are two ways into one set, so both feed the default.

    A rule that read only the shortlist would work for the eight frequent bodies
    and silently not work for the rest of the catalogue — which is the half
    somebody opened «Vali nimekirjast» to reach.
    """
    assert _answer(source_organisations_other=[str(ministry.pk)]) == ministry


def test_a_post_with_only_a_sender_saves_a_matter_answered_to_it(signed_in, ministry):
    """The whole of §10, as the assertion that matters: what is on the record.

    No JavaScript ran. This is exactly what a browser with scripting off sends,
    and the business fact is the same either way — the server is authoritative
    and the browser only mirrors it.
    """
    signed_in.post(
        CREATE, {"title": "Vastus ministeeriumile", "source_organisations": [str(ministry.pk)]}
    )

    matter = Matter.objects.get(title="Vastus ministeeriumile")
    assert list(matter.source_organisations.all()) == [ministry]
    assert matter.addressee_organisation == ministry


def test_a_typed_new_sender_becomes_the_typed_addressee_and_one_row(signed_in):
    """The body being named for the first time, used twice, created once.

    There is no `Organisation` row and so no primary key to answer with until
    `Loo teema` runs, so the default is carried by the *name* — and both halves
    resolve through one catalogue inside one transaction, which is what makes
    the answer «one row» rather than «two identical institutions» (§5).
    """
    signed_in.post(CREATE, {"title": "Uue saatjaga", "sender_name": "Eesti Näidisliit"})

    rows = Organisation.objects.filter(name="Eesti Näidisliit")
    assert rows.count() == 1

    matter = Matter.objects.get(title="Uue saatjaga")
    organisation = rows.get()
    assert list(matter.source_organisations.all()) == [organisation]
    assert matter.addressee_organisation == organisation


def test_a_typed_sender_naming_an_existing_body_reuses_that_row(signed_in, ministry):
    """Reuse, not create — and the same row on both relations.

    `resolve_organisation_name` decides this and is not restated here; what is
    asserted is that defaulting the addressee from a typed sender goes through
    it rather than around it (§5).
    """
    signed_in.post(CREATE, {"title": "Vastus", "sender_name": "Kliimaministeerium"})

    assert Organisation.objects.filter(name="Kliimaministeerium").count() == 1
    matter = Matter.objects.get(title="Vastus")
    assert list(matter.source_organisations.all()) == [ministry]
    assert matter.addressee_organisation == ministry


def test_a_sender_that_is_not_a_real_body_defaults_nothing(signed_in):
    """The default runs in `__init__`, before validation, so it meets raw input.

    A POST naming an institution that does not exist is refused — and refused
    with an error, not with a 500 from a UUID that reached a queryset.
    """
    response = signed_in.post(
        CREATE, {"title": "Väljamõeldud", "source_organisations": [str(uuid.uuid4())]}
    )

    assert response.status_code == 400
    assert not Matter.objects.filter(title="Väljamõeldud").exists()
    assert Organisation.objects.count() == 0


# ---------------------------------------------------------------------------
# An answer given by a person
# ---------------------------------------------------------------------------


def test_a_chosen_addressee_outranks_the_sender(ministry, committee):
    """The example from the brief: answered to the committee, not the ministry."""
    assert (
        _answer(
            source_organisations=[str(ministry.pk)],
            addressee_organisation=str(committee.pk),
        )
        == committee
    )


def test_a_typed_addressee_outranks_the_sender(signed_in, ministry):
    """The other half of the override, on the control that has no primary key."""
    signed_in.post(
        CREATE,
        {
            "title": "Vastus",
            "source_organisations": [str(ministry.pk)],
            "addressee_name": "Eesti Näidisliit",
        },
    )

    matter = Matter.objects.get(title="Vastus")
    assert list(matter.source_organisations.all()) == [ministry]
    assert matter.addressee_organisation is not None
    assert matter.addressee_organisation.name == "Eesti Näidisliit"


def test_a_deliberate_maaramata_beside_a_sender_stays_unanswered(signed_in, ministry):
    """The state the default would otherwise make unreachable.

    `Määramata` is a real radio with an empty value, and an empty value is what a
    form that was never touched posts too — so without something saying which of
    the two happened, "I do not know who we will answer" is a sentence the person
    cannot say once a sender is chosen. `addressee_is_manual` is that something,
    and it is a hidden field rather than an inference because chip order, chip
    position and which value is checked are all things this feature legitimately
    changes (§6, app/matters/forms.py).
    """
    signed_in.post(
        CREATE,
        {
            "title": "Adressaadita",
            "source_organisations": [str(ministry.pk)],
            "addressee_organisation": "",
            "addressee_is_manual": "1",
        },
    )

    matter = Matter.objects.get(title="Adressaadita")
    assert list(matter.source_organisations.all()) == [ministry]
    assert matter.addressee_organisation is None


def test_without_that_marker_the_same_post_is_answered(signed_in, ministry):
    """The other side of the test above, and the reason it is not redundant.

    An empty `addressee_organisation` with no marker is a form nobody answered —
    which is precisely the no-JavaScript POST §10 is about. If both cases came
    back unanswered the marker would be doing nothing; if both came back
    answered it would be doing harm.
    """
    signed_in.post(
        CREATE,
        {
            "title": "Ilma märgita",
            "source_organisations": [str(ministry.pk)],
            "addressee_organisation": "",
        },
    )

    assert Matter.objects.get(title="Ilma märgita").addressee_organisation == ministry


# ---------------------------------------------------------------------------
# When Saatja changes
# ---------------------------------------------------------------------------


def test_changing_the_sender_moves_an_automatic_addressee(ministry, committee):
    """Sender A answered A; sender B, with nothing said about Adressaat, answers B.

    Stated as two independent bound forms, because that is what the server
    actually sees: it has no memory of the previous state of an open form. The
    browser watches the change happen and reaches the same answer live
    (`e2e/test_counterparty_selection.py`, §7).
    """
    assert _answer(source_organisations=[str(ministry.pk)]) == ministry
    assert _answer(source_organisations=[str(committee.pk)]) == committee


def test_changing_the_sender_does_not_move_a_chosen_addressee(ministry, committee):
    """The same change, after somebody has answered. Their answer stands (§7)."""
    assert (
        _answer(
            source_organisations=[str(committee.pk)],
            addressee_organisation=str(ministry.pk),
        )
        == ministry
    )


def test_removing_the_sender_removes_the_answer_it_supplied(ministry):
    """A default that exists only because of a sender goes when the sender does.

    Left behind, it would be a counterparty on the record that nothing on the
    page still supports — and the person would have no reason to look at a
    folded field to find it (§8).
    """
    assert _answer(source_organisations=[str(ministry.pk)]) == ministry
    assert _answer() is None


# ---------------------------------------------------------------------------
# Several senders: no unambiguous body, so no guess
# ---------------------------------------------------------------------------


def test_two_senders_leave_the_addressee_unanswered(ministry, committee):
    """A Matter that arrived from two places has no unambiguous body to answer.

    The browser knows something the server does not — which of the two was
    chosen *first* — and keeps that seed. A POST is a set with no order in it, so
    the deterministic reading of a set of two is «no default», and inventing one
    from `ORDER BY name` would make the answer depend on a body's spelling
    (§9, app/matters/forms.py `_default_addressee`).
    """
    assert _answer(source_organisations=[str(ministry.pk), str(committee.pk)]) is None


def test_the_same_body_ticked_twice_is_still_one_sender(ministry):
    """The two sender controls are two ways into one set, and a body reached
    through both is one body — so this is the unambiguous case, not the
    ambiguous one."""
    assert (
        _answer(
            source_organisations=[str(ministry.pk)],
            source_organisations_other=[str(ministry.pk)],
        )
        == ministry
    )


def test_a_ticked_sender_beside_a_typed_one_leaves_it_unanswered(signed_in, ministry):
    """Two senders, one of which has no row yet, is still two senders.

    `resolve_source_organisations` unions them — «Kliimaministeerium» ticked and
    «Eesti Näidisliit» typed is a Matter that arrived from both — so there is no
    single body to answer and nothing is guessed.
    """
    signed_in.post(
        CREATE,
        {
            "title": "Kahe saatjaga",
            "source_organisations": [str(ministry.pk)],
            "sender_name": "Eesti Näidisliit",
        },
    )

    matter = Matter.objects.get(title="Kahe saatjaga")
    assert {item.name for item in matter.source_organisations.all()} == {
        "Kliimaministeerium",
        "Eesti Näidisliit",
    }
    assert matter.addressee_organisation is None


# ---------------------------------------------------------------------------
# A refused save
# ---------------------------------------------------------------------------


def test_a_refused_save_comes_back_with_the_automatic_answer_visible(signed_in, ministry):
    """The refusal must not cost the person the answer the form had reached.

    And *visible* rather than merely stored: the derived value is written into
    the bound data, so the radio comes back ticked and the summary says so. A
    server that only decided this at save time would show an empty Adressaat
    beside a chosen sender and then file the Matter answered anyway
    (§11 A, app/matters/forms.py).
    """
    page = signed_in.post(
        CREATE, {"title": "", "source_organisations": [str(ministry.pk)]}
    ).content.decode()

    assert _is_checked(page, ministry)
    assert _summary(page) == "Adressaat · Kliimaministeerium"


def test_a_refused_save_keeps_a_chosen_addressee_exactly(signed_in, ministry, committee):
    """And the override survives the round trip unchanged (§11 B)."""
    page = signed_in.post(
        CREATE,
        {
            "title": "",
            "source_organisations": [str(ministry.pk)],
            "addressee_organisation": str(committee.pk),
            "addressee_is_manual": "1",
        },
    ).content.decode()

    assert _summary(page) == "Adressaat · Riigikogu majanduskomisjon"
    assert _is_checked(page, committee)
    assert not _is_checked(page, ministry)
    # The marker itself comes back, so the browser knows on load that this
    # answer is somebody's rather than the page's own.
    assert 'name="addressee_is_manual" value="1"' in page


def test_a_refused_save_creates_no_institution_from_the_default(signed_in):
    """One transaction, so a late refusal takes the derived resolution with it.

    A typed sender defaults the typed addressee, and both are resolved inside
    the save — so a save that fails afterwards must leave the catalogue exactly
    as it found it, on the derived half as much as on the stated one.
    """
    before = Organisation.objects.count()

    response = signed_in.post(CREATE, {"title": "", "sender_name": "Eesti Näidisliit"})

    assert response.status_code in (200, 400)
    assert Organisation.objects.count() == before
    assert not Organisation.objects.filter(name="Eesti Näidisliit").exists()


# ---------------------------------------------------------------------------
# What did not change
# ---------------------------------------------------------------------------


def test_muuda_teemat_does_not_default_its_addressee(signed_in, specialist, ministry):
    """Scope, stated as a test so it stays that way.

    `Muuda teemat` is a person correcting a record that already exists, and its
    radio group always carries the addressee the Matter already has. Deriving
    one there would be the edit form quietly rewriting a fact nobody touched —
    a different question from filling in a blank on a capture form (task §1).
    """
    matter = factories.MatterFactory(owner=specialist, title="Muudetav")

    signed_in.post(
        reverse("matters:matter_edit", args=[matter.pk]),
        {
            "title": matter.title,
            "brief_summary": matter.brief_summary,
            "visibility": matter.visibility,
            "source_organisations": [str(ministry.pk)],
        },
    )

    matter.refresh_from_db()
    assert list(matter.source_organisations.all()) == [ministry]
    assert matter.addressee_organisation is None
