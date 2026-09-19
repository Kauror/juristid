"""The owner's second live audit of the Teema screens (docs/adr/0096).

Four corrections, and this file owns what only the server can state about them:
that the two pages draw one control per shared fact, that `Nähtavus` cannot be
reached or forged anywhere in the business UI, and that `Kustuta teema` removes
what it says it removes and refuses in full where it cannot.

Where a rule already has an owner it stays there and is not restated:

* `tests/test_uus_teema_ux_corrections.py` owns the create page's
  classification markup — that every vocabulary is drawn at rest, keeps its
  cardinality and survives a refusal;
* `tests/test_post_qa_read_surfaces.py` owns `Muu valdkond`'s read surface and
  the organisation picker on the edit page;
* `tests/test_oigusakt_field.py` owns `Õigusakt` on both pages;
* `tests/test_authorization.py` owns what Matter visibility *does*, which this
  round does not touch;
* `e2e/test_uus_teema_menus.py` owns what a browser has to measure.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.documents.models import Document, DocumentVersion
from app.matters.deletion import (
    BLOCKED_BY_APPEND_ONLY_CHILD,
    BLOCKED_BY_LEGAL_HOLD,
    BLOCKED_BY_RELATED_MATTER,
    DELETION_REFUSED,
    delete_matter,
    plan_matter_deletion,
)
from app.matters.enums import EngagementKind
from app.matters.forms import MatterCreateForm, MatterEditForm, edit_initial
from app.matters.models import Entry, Matter, MatterEngagement
from app.matters.services import (
    add_entry,
    edit_entry,
    record_engagement,
    set_matter_visibility,
    set_policy_area_other,
    set_tags,
)
from app.organisations.models import Organisation
from app.search.models import SearchDocument
from app.taxonomy.models import PolicyArea
from app.workflow.models import NextAction
from app.workflow.services import set_next_action_for_new_work
from tests import factories

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")
INTAKE = reverse("matters:intake")


def edit_url(matter: Matter) -> str:
    return reverse("matters:matter_edit", kwargs={"pk": matter.pk})


def delete_url(matter: Matter) -> str:
    return reverse("matters:matter_delete", kwargs={"pk": matter.pk})


def edit_payload(matter: Matter, **overrides) -> dict:
    """The whole edit form, as the browser posts it back unchanged."""
    initial = edit_initial(matter)
    payload = {
        "title": initial["title"],
        "brief_summary": initial["brief_summary"] or "",
        "owner": initial["owner"] or "",
        "stage": initial["stage"] or "",
        "track": initial["track"] or "",
        "policy_areas": [str(pk) for pk in initial["policy_areas"]],
        "policy_area_other_selected": "on" if initial["policy_area_other_selected"] else "",
        "policy_area_other": initial["policy_area_other"] or "",
        "legal_instruments": [str(pk) for pk in initial["legal_instruments"]],
        "legal_instrument_other": initial["legal_instrument_other"] or "",
        "source_organisations": [str(pk) for pk in initial["source_organisations"]],
        "sender_name": "",
        "addressee_organisation": initial["addressee_organisation"] or "",
        "addressee_name": "",
        "received_date": "",
        "response_deadline": "",
        "tags": [str(pk) for pk in initial["tags"]],
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# §1 — `Uus teema` is the master
# ---------------------------------------------------------------------------

#: Facts both pages ask about, and the control each of them must draw for it.
#: Not every field on either form: the master's own extras (`Märkmed`, the file
#: row) and the edit page's stated three (`track`, `addressee_organisation`,
#: `tags`) are exempt by decision, and the test below names them so that adding
#: a fourth is a decision somebody makes rather than a drift nobody notices.
SHARED_FACTS = (
    "title",
    "brief_summary",
    "owner",
    "stage",
    "policy_areas",
    "policy_area_other_selected",
    "policy_area_other",
    "legal_instruments",
    "legal_instrument_other",
    "source_organisations",
    "source_organisations_other",
    "sender_name",
    "received_date",
    "response_deadline",
)

CREATE_ONLY = {"notes", "uploads", "suggestion_state"}
EDIT_ONLY = {"track", "addressee_organisation", "addressee_name", "tags"}


def test_every_shared_fact_is_on_both_forms(specialist):
    """The list above is the contract, and both forms satisfy it."""
    create = MatterCreateForm(viewer=specialist).fields
    edit = MatterEditForm(viewer=specialist).fields

    for name in SHARED_FACTS:
        assert name in create, name
        assert name in edit, name


def test_neither_form_has_grown_a_field_the_other_does_not_know_about(specialist):
    """Anything outside the three named sets is drift, and there is none.

    The guard that keeps §1 from decaying the way it decayed before: a field
    added to one page and not the other now fails here, naming itself, rather
    than being discovered by somebody comparing two screenshots a release later
    (docs/adr/0096 §1).
    """
    create = set(MatterCreateForm(viewer=specialist).fields)
    edit = set(MatterEditForm(viewer=specialist).fields)

    assert create - edit == {name for name in CREATE_ONLY if name in create}
    assert edit - create == EDIT_ONLY


def test_the_shared_facts_are_asked_in_the_same_words(specialist):
    """One column, one label. A box a person has to recognise twice is two boxes."""
    create = MatterCreateForm(viewer=specialist).fields
    edit = MatterEditForm(viewer=specialist).fields

    for name in SHARED_FACTS:
        assert create[name].label == edit[name].label, name


def test_the_shared_facts_use_the_same_kind_of_control(specialist):
    """Same widget class, so the two pages cannot be two designs again.

    Two exceptions, both deliberate and both about the *edit* page needing to
    un-answer a question: `owner` and `stage` are `blank=True` there so that
    «Määramata» exists as a chip. The widget class is the same either way, which
    is what this asserts.
    """
    create = MatterCreateForm(viewer=specialist).fields
    edit = MatterEditForm(viewer=specialist).fields

    for name in SHARED_FACTS:
        assert type(create[name].widget) is type(edit[name].widget), name


CLASSIFICATION_PARTIALS = (
    "matters/partials/valdkonnad_field.html",
    "matters/partials/hetkeseis_field.html",
    "matters/partials/oigusakt_field.html",
)


@pytest.mark.parametrize("partial", CLASSIFICATION_PARTIALS)
def test_both_pages_include_the_same_classification_partial(partial):
    """The structural half of §1, read off the templates themselves.

    Not a whole-template comparison — the two pages are legitimately different
    documents — and not an assertion about rendered markup, which would pass on
    two hand-maintained copies that happen to agree today. What is asserted is
    that there is one file and that both pages include it, which is the only
    thing that makes them incapable of drifting.
    """
    from pathlib import Path

    from django.conf import settings

    root = Path(settings.BASE_DIR) / "templates"
    assert (root / partial).exists(), partial
    for page in ("matters/matter_create.html", "matters/matter_edit.html"):
        assert partial in (root / page).read_text(encoding="utf-8"), page


def test_the_edit_page_states_its_own_three_questions_as_a_group(signed_in, specialist):
    """`Menetlusliik`, `Kellele` and `Sildid`, under a heading that says so.

    They are absent from `Uus teema` by decision (docs/adr/0090 §4, §5) and they
    are canonical facts, so the page that corrects a record is where they are
    answered. Below the master's questions and named, so a reader comparing the
    two screens reads the difference as a rule rather than as an inconsistency.
    """
    # A Tag has to exist for `Sildid` to render any control at all — the empty
    # vocabulary renders a sentence instead, which is correct and would make
    # this assertion measure nothing.
    factories.TagFactory(name_et="Kiireloomuline")
    matter = factories.MatterFactory(owner=specialist)
    page = signed_in.get(edit_url(matter)).content.decode()

    heading = page.index("Ainult olemasoleva teema kohta")
    assert page.index('name="response_deadline"') < heading
    for name in ("track", "addressee_organisation", "tags"):
        assert page.index(f'name="{name}"') > heading


def test_the_edit_page_asks_the_master_questions_in_the_master_order(signed_in, specialist):
    """Pealkiri, kokkuvõte, Saabus, inimesed, kolm klassifikatsiooni, tähtaeg."""
    matter = factories.MatterFactory(owner=specialist)
    page = signed_in.get(edit_url(matter)).content.decode()

    positions = [
        page.index('name="title"'),
        page.index('name="brief_summary"'),
        page.index('name="received_date"'),
        page.index('name="owner"'),
        page.index('name="policy_areas"'),
        page.index('name="stage"'),
        page.index('name="legal_instruments"'),
        page.index('name="response_deadline"'),
    ]
    assert positions == sorted(positions)


def test_a_stored_muu_valdkond_is_visible_the_moment_the_edit_page_opens(signed_in, specialist):
    """Ticked chip, open box — server-side, with no scripting involved.

    This is the whole of the answer to post-QA R2-07, which gave this page a
    box and no chip because a chip folded inside a menu could not be read. The
    vocabulary is drawn at rest, so the chip is on the page and says what the
    record holds (docs/adr/0096 §2).
    """
    matter = factories.MatterFactory(owner=specialist)
    set_policy_area_other(matter=matter, value="Ringmajandus", actor=specialist)

    page = signed_in.get(edit_url(matter)).content.decode()

    chip = page[page.index('id="valdkond-muu"') :]
    assert "checked" in chip[: chip.index("</label>")]
    revealed = page[page.index('id="valdkond-muu-tekst"') :]
    assert "hidden" not in revealed[: revealed.index(">")]
    assert 'value="Ringmajandus"' in page


def test_an_unrelated_save_keeps_a_retired_area_and_a_retired_stage(signed_in, specialist):
    """Correcting the title must not silently drop a withdrawn filing.

    The rule ADR 0032 §Amendment states, asserted through the rebuilt page: the
    retired chips are offered back, they arrive ticked, and posting the form
    back unchanged leaves the record exactly as it was.
    """
    retired_area = factories.PolicyAreaFactory(name_et="Kadunud valdkond", is_active=False)
    retired_stage = factories.StageFactory(label_et="Kadunud seis", is_active=False)
    matter = factories.MatterFactory(owner=specialist, stage=retired_stage)
    matter.policy_areas.add(retired_area)

    page = signed_in.get(edit_url(matter)).content.decode()
    assert "Kadunud valdkond · kasutusest väljas" in page
    assert "Kadunud seis · kasutusest väljas" in page

    response = signed_in.post(edit_url(matter), edit_payload(matter, title="Parandatud"))
    assert response.status_code == 302

    matter.refresh_from_db()
    assert matter.title == "Parandatud"
    assert list(matter.policy_areas.all()) == [retired_area]
    assert matter.stage == retired_stage


def test_an_unrelated_save_keeps_a_stored_muu_valdkond(signed_in, specialist):
    """The trap the shared `Muu` chip introduces, and the guard for it.

    Free text belongs to the chip that reveals it, so a form posted back with
    the text and *without* the chip would clear it. The chip arrives ticked on
    a Matter that holds one, so an ordinary correction carries it back.
    """
    matter = factories.MatterFactory(owner=specialist)
    set_policy_area_other(matter=matter, value="Ringmajandus", actor=specialist)

    signed_in.post(edit_url(matter), edit_payload(matter, title="Parandatud pealkiri"))

    matter.refresh_from_db()
    assert matter.title == "Parandatud pealkiri"
    assert matter.policy_area_other == "Ringmajandus"


# ---------------------------------------------------------------------------
# §3 — `Nähtavus` is gone from the ordinary Teema UI
# ---------------------------------------------------------------------------

VISIBILITY_WORDS = ("Nähtavus", "Piiratud nähtavus", "Tavaline")


def test_the_edit_page_draws_no_visibility_control(signed_in, specialist):
    """No label, no chips, no explanation, and no hidden input either."""
    matter = factories.MatterFactory(owner=specialist)
    page = signed_in.get(edit_url(matter)).content.decode()

    assert 'name="visibility"' not in page
    for word in VISIBILITY_WORDS:
        assert word not in page
    assert "RESTRICTED" not in page
    assert "visibility" not in MatterEditForm().fields


def test_the_create_page_and_the_intake_page_draw_none_either(signed_in):
    """The two creation surfaces, for the same reason and with the same proof."""
    for url in (CREATE, INTAKE):
        page = signed_in.get(url).content.decode()
        assert 'name="visibility"' not in page, url
        for word in VISIBILITY_WORDS:
            assert word not in page, (url, word)


def test_the_teema_header_no_longer_offers_visibility(signed_in, specialist):
    """The ⋯ menu carried the product's one Matter-visibility control."""
    matter = factories.MatterFactory(owner=specialist)
    page = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    body = page.content.decode()

    assert 'name="visibility"' not in body
    assert "vali/visibility" not in body


def test_a_crafted_post_to_the_edit_route_cannot_restrict_a_matter(signed_in, specialist):
    """The security half of the removal, and the reason it is a deleted field.

    A control removed from a template while the form still binds it is an
    accepted POST parameter behind no control at all. `MatterEditForm` has no
    `visibility` field, so this value is not part of the request as far as the
    form is concerned, and the view has nothing to pass on.
    """
    matter = factories.MatterFactory(owner=specialist)
    assert matter.visibility == Visibility.NORMAL

    response = signed_in.post(
        edit_url(matter), edit_payload(matter, visibility=Visibility.RESTRICTED)
    )

    assert response.status_code == 302
    matter.refresh_from_db()
    assert matter.visibility == Visibility.NORMAL


def test_a_crafted_post_to_intake_cannot_restrict_what_it_files(signed_in, specialist):
    """The same rule on the other creation path."""
    response = signed_in.post(
        INTAKE,
        {
            "title": "Saabunud kiri",
            "visibility": Visibility.RESTRICTED,
            "received_date": "1.9.2026",
        },
    )

    assert response.status_code in (200, 302, 400)
    for matter in Matter.objects.filter(title="Saabunud kiri"):
        assert matter.visibility == Visibility.NORMAL


def test_the_inline_endpoint_answers_404_for_visibility(signed_in, specialist):
    """`matters:update_field` is the endpoint the header control posted to."""
    matter = factories.MatterFactory(owner=specialist)
    url = reverse("matters:update_field", kwargs={"pk": matter.pk, "field": "visibility"})

    response = signed_in.post(url, {"visibility": Visibility.RESTRICTED})

    assert response.status_code == 404
    matter.refresh_from_db()
    assert matter.visibility == Visibility.NORMAL


def test_a_restricted_matter_keeps_its_visibility_and_still_says_so(
    signed_in, specialist, restricted_matter
):
    """Removing the control changes no record and no authorization.

    The banner that *states* a restricted Matter's visibility is deliberately
    kept: a reader has to know that the file they are looking at is not
    department-wide. What is gone is the control that writes it.
    """
    assert restricted_matter.visibility == Visibility.RESTRICTED

    signed_in.post(edit_url(restricted_matter), edit_payload(restricted_matter))

    restricted_matter.refresh_from_db()
    assert restricted_matter.visibility == Visibility.RESTRICTED

    detail = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": restricted_matter.pk})
    ).content.decode()
    assert "Piiratud nähtavus" in detail


def test_the_service_that_writes_visibility_is_untouched(specialist, normal_matter):
    """The architecture stays. Only the business UI stopped asking."""
    set_matter_visibility(matter=normal_matter, visibility=Visibility.RESTRICTED, actor=specialist)

    normal_matter.refresh_from_db()
    assert normal_matter.visibility == Visibility.RESTRICTED
    assert ChangeEvent.objects.filter(
        matter=normal_matter, event_type=ChangeEventType.MATTER_VISIBILITY_CHANGED
    ).exists()


# ---------------------------------------------------------------------------
# §4 — `Kustuta teema`
# ---------------------------------------------------------------------------


@pytest.fixture
def rich_matter(specialist, capture_evidence, pdf_bytes):
    """A Matter with something of every kind under it.

    Deliberately not an empty record: a deletion tested only against a Matter
    with nothing in it proves that one row can be marked and nothing else. This
    one carries chronology, a step, a consultation, taxonomy, a sender, a
    document with an evidence version and a search projection.
    """
    organisation = factories.OrganisationFactory(name="Kliimaministeerium")
    area = PolicyArea.objects.filter(is_active=True).first()
    tag = factories.TagFactory(name_et="Kiireloomuline")
    matter = factories.MatterFactory(owner=specialist, title="Kustutatav teema")
    matter.source_organisations.add(organisation)
    matter.policy_areas.add(area)
    set_tags(matter=matter, tags=[tag], actor=specialist)

    add_entry(matter=matter, body="Esimene märge", author=specialist)
    add_entry(matter=matter, body="Teine märge", author=specialist)
    set_next_action_for_new_work(
        matter=matter,
        text="Koostan arvamuse",
        target_date=date.today() + timedelta(days=7),
        actor=specialist,
    )
    record_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="Liikmete küsitlus",
        occurred_on=date.today(),
        actor=specialist,
    )
    capture_evidence(matter, pdf_bytes, "eelnou.pdf", "application/pdf")
    return matter


def test_the_plan_finds_what_is_under_the_matter(rich_matter):
    """The read that the confirmation page is drawn from."""
    plan = plan_matter_deletion(rich_matter)

    assert not plan.is_blocked
    assert plan.count_of("matters.Entry") == 2
    assert plan.count_of("workflow.NextAction") == 1
    assert plan.count_of("matters.MatterEngagement") == 1
    assert plan.count_of("documents.Document") == 1
    assert plan.count_of("documents.DocumentVersion") == 1
    assert len(plan.evidence_keys) == 1


def test_the_plan_never_reaches_shared_reference_data(rich_matter):
    """Ownership runs one way, so an Organisation is out of the set.

    Asserted as the absence of the labels rather than as a filter, because the
    guarantee is structural: the walk follows reverse relations only and never
    follows a foreign key outwards.
    """
    plan = plan_matter_deletion(rich_matter)
    labels = {group.label for group in plan.owned}

    for shared in (
        "organisations.Organisation",
        "taxonomy.PolicyArea",
        "taxonomy.Tag",
        "taxonomy.LegalInstrumentType",
        "workflow.StageVocabulary",
        "accounts.User",
    ):
        assert shared not in labels


def test_the_audit_history_is_retained_rather_than_removed(rich_matter):
    """The one thing the tombstone keeps, and it is named as kept."""
    plan = plan_matter_deletion(rich_matter)

    assert "audit.ChangeEvent" in {group.label for group in plan.retained}
    assert "audit.ChangeEvent" not in {group.label for group in plan.owned}


def test_deleting_removes_every_owned_business_row(rich_matter, specialist):
    """The operation, measured on the rows themselves."""
    matter_id = rich_matter.pk
    organisation_count = Organisation.objects.count()
    area_count = PolicyArea.objects.count()

    delete_matter(matter=rich_matter, actor=specialist)

    assert not Entry.objects.filter(matter_id=matter_id).exists()
    assert not NextAction.objects.filter(matter_id=matter_id).exists()
    assert not MatterEngagement.objects.filter(matter_id=matter_id).exists()
    assert not Document.objects.filter(matter_id=matter_id).exists()
    assert not DocumentVersion.objects.filter(document__matter_id=matter_id).exists()
    assert not SearchDocument.objects.filter(matter_id=matter_id).exists()
    # And shared reference data is exactly where it was.
    assert Organisation.objects.count() == organisation_count
    assert PolicyArea.objects.count() == area_count


def test_the_matter_is_gone_from_every_ordinary_query(rich_matter, specialist):
    """The tombstone is invisible through the default manager, not through luck."""
    matter_id = rich_matter.pk

    delete_matter(matter=rich_matter, actor=specialist)

    assert not Matter.objects.filter(pk=matter_id).exists()
    assert not Matter.objects.visible_to(specialist).filter(pk=matter_id).exists()
    assert Matter.all_objects.filter(pk=matter_id).exists()


def test_the_deletion_is_audited_on_the_row_it_keeps(rich_matter, specialist):
    """`MATTER_DELETED`, pointing at the tombstone — which is why it survives."""
    matter_id = rich_matter.pk

    delete_matter(matter=rich_matter, actor=specialist)

    event = ChangeEvent.objects.get(matter_id=matter_id, event_type=ChangeEventType.MATTER_DELETED)
    assert event.actor == specialist
    assert event.payload["title"] == "Kustutatav teema"
    assert event.payload["rows"]["matters.Entry"] == 2


def test_the_evidence_object_is_no_longer_referenced(rich_matter, specialist, evidence_root):
    """The bytes go through the established pruner, and the row goes now."""
    plan = plan_matter_deletion(rich_matter)
    assert plan.evidence_keys

    delete_matter(matter=rich_matter, actor=specialist)

    from app.documents.references import referenced_storage_keys

    for key in plan.evidence_keys:
        assert key not in set(referenced_storage_keys())


def test_a_second_deletion_is_not_an_error(rich_matter, specialist):
    """The stale second tab, which must not be a 500."""
    delete_matter(matter=rich_matter, actor=specialist)
    plan = delete_matter(matter=rich_matter, actor=specialist)

    assert not plan.is_blocked


# -- refusals ---------------------------------------------------------------


def test_a_legal_hold_refuses_the_whole_deletion(rich_matter, specialist):
    """A hold outlives a deletion request, and refusal is atomic."""
    Document.objects.filter(matter=rich_matter).update(legal_hold=True)

    plan = plan_matter_deletion(rich_matter)
    assert BLOCKED_BY_LEGAL_HOLD in {blocker.code for blocker in plan.blockers}

    with pytest.raises(DomainError) as error:
        delete_matter(matter=rich_matter, actor=specialist)
    assert DELETION_REFUSED in str(error.value)

    # Nothing partly disappeared.
    assert Matter.objects.filter(pk=rich_matter.pk).exists()
    assert Entry.objects.filter(matter=rich_matter).count() == 2
    assert Document.objects.filter(matter=rich_matter).count() == 1


def test_a_corrected_entry_refuses_the_deletion(rich_matter, specialist):
    """The residue this design refuses to leave behind.

    `EntryRevision` is append-only in the database and hangs off `Entry` under
    `CASCADE`, so the entry cannot be removed. Keeping it under the tombstone
    would be keeping an ordinary business record to make a deletion look
    complete, so the whole deletion refuses instead and says why
    (docs/adr/0096 §4.4).
    """
    entry = Entry.objects.filter(matter=rich_matter).first()
    edit_entry(entry=entry, body="Parandatud märge", actor=specialist)

    plan = plan_matter_deletion(rich_matter)
    assert BLOCKED_BY_APPEND_ONLY_CHILD in {blocker.code for blocker in plan.blockers}

    with pytest.raises(DomainError):
        delete_matter(matter=rich_matter, actor=specialist)
    assert Entry.objects.filter(matter=rich_matter).count() == 2


def test_a_successor_matter_refuses_the_deletion(rich_matter, specialist):
    """Another record depends on this one, so the answer is no.

    Never a cascade: the successor is somebody else's Teema and deleting it to
    satisfy this request would be the mistake the whole walk is built not to
    make.
    """
    successor = factories.MatterFactory(owner=specialist, superseded_by=rich_matter)

    plan = plan_matter_deletion(rich_matter)
    assert BLOCKED_BY_RELATED_MATTER in {blocker.code for blocker in plan.blockers}

    with pytest.raises(DomainError):
        delete_matter(matter=rich_matter, actor=specialist)
    assert Matter.objects.filter(pk=successor.pk).exists()
    assert Matter.objects.filter(pk=rich_matter.pk).exists()


# -- the surface ------------------------------------------------------------


def test_the_edit_page_offers_deletion_apart_from_salvesta(signed_in, specialist):
    """Visible, destructive, and outside the form that saves."""
    matter = factories.MatterFactory(owner=specialist)
    page = signed_in.get(edit_url(matter)).content.decode()

    assert "Kustuta teema" in page
    assert delete_url(matter) in page
    assert "dangerzone" in page
    # Outside the editing form: the delete affordance follows `</form>`.
    assert page.index("dangerzone") > page.rindex("</form>")


def test_a_get_cannot_delete(signed_in, specialist, rich_matter):
    """A link that deleted would be deleted by a prefetch."""
    response = signed_in.get(delete_url(rich_matter))

    assert response.status_code == 200
    assert "Kustuta teema" in response.content.decode()
    assert Matter.objects.filter(pk=rich_matter.pk).exists()
    assert Entry.objects.filter(matter=rich_matter).count() == 2


def test_the_confirmation_names_the_teema_and_what_is_inside_it(signed_in, rich_matter):
    """Pressing the button is an answer about *this* record."""
    page = signed_in.get(delete_url(rich_matter)).content.decode()

    assert "Kustutatav teema" in page
    assert "2 sissekannet" in page
    assert "Seda ei saa tagasi võtta." in page
    assert "Loobu" in page


def test_cancelling_writes_nothing(signed_in, rich_matter):
    """`Loobu` is a link back, so the GET is the whole of it."""
    before = ChangeEvent.objects.count()
    signed_in.get(delete_url(rich_matter))

    assert ChangeEvent.objects.count() == before
    assert Matter.objects.filter(pk=rich_matter.pk).exists()


def test_the_post_deletes_and_lands_on_the_register(signed_in, rich_matter):
    """And never on the address that now answers 404."""
    matter_id = rich_matter.pk

    response = signed_in.post(delete_url(rich_matter), follow=True)

    assert response.redirect_chain[-1][0] == reverse("matters:matter_list")
    assert "Teema kustutati." in response.content.decode()
    assert not Matter.objects.filter(pk=matter_id).exists()


def test_the_old_address_no_longer_opens(signed_in, rich_matter):
    """404, not a tombstone rendered as a Matter."""
    detail = reverse("matters:matter_detail", kwargs={"pk": rich_matter.pk})
    signed_in.post(delete_url(rich_matter))

    assert signed_in.get(detail).status_code == 404
    assert signed_in.get(edit_url(rich_matter)).status_code == 404


def test_a_deleted_matter_is_absent_from_the_work_surfaces(signed_in, rich_matter, specialist):
    """Minu asjad, the register and the department page all read one manager."""
    signed_in.post(delete_url(rich_matter))

    for url in (
        reverse("matters:my_work"),
        reverse("matters:matter_list"),
        reverse("matters:department"),
    ):
        assert "Kustutatav teema" not in signed_in.get(url).content.decode(), url


def test_a_deleted_matter_is_absent_from_search(signed_in, rich_matter):
    """Immediately, and not at the next full rebuild."""
    signed_in.post(delete_url(rich_matter))

    page = signed_in.get(reverse("matters:matter_list"), {"otsing": "Kustutatav"})
    assert "Kustutatav teema" not in page.content.decode()


def test_a_blocked_matter_is_offered_no_final_button(signed_in, rich_matter, specialist):
    """The page says why, and the button that cannot work is not drawn."""
    Document.objects.filter(matter=rich_matter).update(legal_hold=True)

    page = signed_in.get(delete_url(rich_matter)).content.decode()

    assert "Seda teemat ei saa praegu kustutada." in page
    assert "säilitamiskohustus" in page
    assert "button--danger" not in page


def test_a_blocked_matter_refuses_the_post_too(signed_in, rich_matter):
    """The page is presentation; `delete_matter` is the boundary."""
    Document.objects.filter(matter=rich_matter).update(legal_hold=True)

    response = signed_in.post(delete_url(rich_matter))

    assert response.status_code == 400
    assert Matter.objects.filter(pk=rich_matter.pk).exists()
    assert Entry.objects.filter(matter=rich_matter).count() == 2


def test_the_post_needs_a_token(specialist, rich_matter):
    """CSRF, like every other write in the product."""
    from django.test import Client

    enforcing = Client(enforce_csrf_checks=True)
    enforcing.force_login(specialist)

    response = enforcing.post(delete_url(rich_matter))

    assert response.status_code == 403
    assert Matter.objects.filter(pk=rich_matter.pk).exists()
    assert Entry.objects.filter(matter=rich_matter).count() == 2


@pytest.mark.parametrize("role", ["reader", "administrator"])
def test_a_role_without_business_write_is_refused(client, request, rich_matter, role):
    """Hiding the button is presentation; the route is the security.

    Both roles are outside `ROLES_WITH_BUSINESS_WRITE` by decision — reading
    without editing is the whole of READER, and technical administration is not
    business authorship (app/core/authorization.py).
    """
    user = request.getfixturevalue(role)
    client.force_login(user)

    response = client.post(delete_url(rich_matter))

    assert response.status_code in (403, 404)
    assert Matter.objects.filter(pk=rich_matter.pk).exists()
    assert Entry.objects.filter(matter=rich_matter).count() == 2


def test_the_title_is_escaped_on_the_confirmation_page(signed_in, specialist):
    """The record's own name, rendered as text.

    Worth its own line because this page prints a value somebody typed beside a
    destructive button, which is the one place where a title that looked like
    markup would matter.
    """
    matter = factories.MatterFactory(owner=specialist, title="<script>x</script> eelnõu")

    page = signed_in.get(delete_url(matter)).content.decode()

    assert "<script>x</script>" not in page
    assert "&lt;script&gt;" in page


def test_nothing_in_the_product_calls_a_bare_matter_delete():
    """The shortcut this module exists instead of.

    `matter.delete()` raises `ProtectedError` on any Matter with audit history,
    which is all of them — so a caller that reached for it would be a 500 in
    production rather than a deletion. Asserted over the source, because the
    failure it guards against is one nobody would write a passing test for.
    """
    from pathlib import Path

    from django.conf import settings

    # The two modules whose *subject* is deletion, and which therefore name the
    # shortcut in prose in order to explain why they do not take it.
    documented = {"deletion.py", "purge.py"}
    shortcut = re.compile(r"^[^#]*(\bmatter\.delete\(\)|Matter\.objects\b[^\n]*\.delete\(\))")

    offenders = []
    for path in (Path(settings.BASE_DIR) / "app").rglob("*.py"):
        if path.name in documented:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if shortcut.search(line):
                offenders.append(f"{path}:{number}")
    assert not offenders, offenders


def test_a_deletion_leaves_no_integrity_finding_worse_than_an_unreferenced_object(
    rich_matter, specialist, evidence_root
):
    """What the store may say afterwards, and what it may not.

    **May**: `orphan-object`, once per evidence key the deletion released. Being
    unreferenced is the whole point — the row that claimed the bytes is gone —
    and it is the state `prune_orphaned_evidence` exists to collect. Deleting
    the bytes inside the transaction would be the unsafe direction, because a
    rollback would then claim evidence that no longer exists.

    Under a test transaction `transaction.on_commit` never fires, so what this
    measures is the state *before* the best-effort cleanup and before the
    pruner: the worst case, deliberately.

    **May not**: anything else. A `missing-object` would mean a surviving row
    pointing at bytes this deletion took; a link or version finding would mean
    a half-removed graph. Both are the failures the topological order exists to
    prevent, and neither appears.
    """
    from app.documents.integrity import check_evidence

    plan = plan_matter_deletion(rich_matter)
    released = set(plan.evidence_keys)
    assert released

    delete_matter(matter=rich_matter, actor=specialist)

    report = check_evidence(verify_sha=True, scan_storage=True)
    for finding in report.findings:
        assert finding.kind == "orphan-object", finding
        assert finding.subject in released, finding
    assert {finding.subject for finding in report.findings} == released


def test_the_search_projection_still_counts_what_the_register_counts(rich_matter, specialist):
    """The comparison `check_search_integrity` makes, after a deletion.

    Both halves read `Matter.objects`, so a tombstone that had kept its search
    row would show up here as the two numbers disagreeing — which is exactly
    how «a deleted Teema disappears from search at once» would fail quietly.
    """
    delete_matter(matter=rich_matter, actor=specialist)

    projected = SearchDocument.objects.filter(source_kind="MATTER").count()
    assert projected == Matter.objects.count()


def test_the_admin_can_still_see_a_tombstone(rich_matter, specialist, client, superuser):
    """The one place a deleted Matter stays reachable, and it is read-only.

    `Matter.objects` closes every business surface; administration is where
    somebody asks what happened to a record the audit trail names, and a row
    the admin could not open would be a dead end (docs/adr/0096 §4.2).
    """
    from django.contrib.admin.sites import site

    from app.core.admin import MatterAdmin

    delete_matter(matter=rich_matter, actor=specialist)

    admin = MatterAdmin(Matter, site)
    assert admin.get_queryset(None).filter(pk=rich_matter.pk).exists()
    for name in ("deleted_at", "deleted_by"):
        assert name in admin.readonly_fields
