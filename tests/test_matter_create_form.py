"""Uus teema, redesigned around how a matter actually arrives.

A title, a file, a person, a sender and a date. The controls are visible rather
than collapsed into selects, and the shape of each control is a promise about
the data behind it — radios where the model holds one value, checkboxes where it
holds several. These tests pin the promises, not the pixels.
"""

from __future__ import annotations

import pytest
from django import forms
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.documents.models import Document, DocumentVersion
from app.matters.forms import MatterCreateForm
from app.matters.models import Matter
from tests import factories
from tests import synthetic_corpus as corpus

pytestmark = pytest.mark.django_db

PDF = "application/pdf"
CREATE = reverse("matters:matter_create")


def upload(name: str, content: bytes, content_type: str = PDF) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content, content_type=content_type)


# -- the shape of the controls ----------------------------------------------


def test_the_owner_is_a_single_choice(specialist):
    """`Matter.owner` is one person, so the control must be one person.

    A checkbox list would promise something the model cannot keep.
    """
    form = MatterCreateForm(viewer=specialist)
    assert isinstance(form.fields["owner"].widget, forms.RadioSelect)
    assert not isinstance(form.fields["owner"].widget, forms.CheckboxSelectMultiple)


def test_the_sender_is_a_single_choice(specialist):
    form = MatterCreateForm(viewer=specialist)
    assert isinstance(form.fields["source_organisation"].widget, forms.RadioSelect)


def test_the_policy_areas_are_real_checkboxes(specialist):
    form = MatterCreateForm(viewer=specialist)
    assert isinstance(form.fields["policy_areas"].widget, forms.CheckboxSelectMultiple)


def test_several_policy_areas_can_be_chosen(signed_in, specialist):
    first = factories.PolicyAreaFactory()
    second = factories.PolicyAreaFactory()

    signed_in.post(
        CREATE, {"title": "Kaks valdkonda", "policy_areas": [str(first.pk), str(second.pk)]}
    )

    matter = Matter.objects.get(title="Kaks valdkonda")
    assert set(matter.policy_areas.all()) == {first, second}


# -- ordering ---------------------------------------------------------------


def test_policy_areas_are_ordered_by_how_often_they_are_used(specialist):
    rare = factories.PolicyAreaFactory(name_et="Harv", sort_order=1)
    common = factories.PolicyAreaFactory(name_et="Sage", sort_order=99)

    for _ in range(3):
        factories.MatterFactory(owner=specialist).policy_areas.add(common)
    factories.MatterFactory(owner=specialist).policy_areas.add(rare)

    form = MatterCreateForm(viewer=specialist)
    labels = [str(label) for _, label in form.fields["policy_areas"].choices]
    assert labels.index("Sage") < labels.index("Harv")


def test_restricted_work_does_not_shape_the_order_somebody_else_sees(specialist, other_specialist):
    """An order derived from records is a disclosure about those records."""
    quiet = factories.PolicyAreaFactory(name_et="Vaikne", sort_order=1)
    secret = factories.PolicyAreaFactory(name_et="Salajane", sort_order=2)

    hidden = factories.MatterFactory(owner=other_specialist, visibility=Visibility.RESTRICTED)
    for _ in range(5):
        hidden.policy_areas.add(secret)
    factories.MatterFactory(owner=specialist).policy_areas.add(quiet)

    form = MatterCreateForm(viewer=specialist)
    labels = [str(label) for _, label in form.fields["policy_areas"].choices]
    assert labels.index("Vaikne") < labels.index("Salajane")


def test_the_frequent_senders_are_the_ones_actually_used(specialist):
    common = factories.OrganisationFactory(name="Sage ministeerium")
    rare = factories.OrganisationFactory(name="Harv amet")
    for _ in range(3):
        factories.MatterFactory(owner=specialist, source_organisation=common)
    factories.MatterFactory(owner=specialist, source_organisation=rare)

    form = MatterCreateForm(viewer=specialist)
    assert form.frequent_senders[0] == common


def test_an_organisation_outside_the_frequent_list_is_still_a_valid_answer(signed_in, specialist):
    """The chips are a shortcut, not the vocabulary.

    Narrowing validation to the visible ten would reject a correct answer given
    through the search control.
    """
    for index in range(12):
        factories.MatterFactory(
            owner=specialist,
            source_organisation=factories.OrganisationFactory(name=f"Sage {index}"),
        )
    rare = factories.OrganisationFactory(name="Väga harv amet")

    signed_in.post(CREATE, {"title": "Harva saatjaga", "source_organisation_other": str(rare.pk)})

    assert Matter.objects.get(title="Harva saatjaga").source_organisation == rare


# -- the date ---------------------------------------------------------------


def test_the_arrival_date_starts_at_today(specialist):
    form = MatterCreateForm(viewer=specialist)
    assert form["received_date"].initial == timezone.localdate()


def test_what_somebody_types_always_wins(signed_in):
    """`initial` only fills an unbound form; nothing here overwrites a POST."""
    yesterday = timezone.localdate() - timezone.timedelta(days=1)

    signed_in.post(CREATE, {"title": "Eile saabunud", "received_date": yesterday.isoformat()})

    assert Matter.objects.get(title="Eile saabunud").received_date == yesterday


# -- Muu ---------------------------------------------------------------------


def test_the_free_text_area_is_saved_but_creates_no_taxonomy(signed_in):
    from app.taxonomy.models import PolicyArea

    before = PolicyArea.objects.count()
    signed_in.post(
        CREATE,
        {
            "title": "Muu valdkonnaga",
            "policy_area_other_selected": "on",
            "policy_area_other": "  Kosmoseõigus  ",
        },
    )

    matter = Matter.objects.get(title="Muu valdkonnaga")
    assert matter.policy_area_other == "Kosmoseõigus"
    assert matter.policy_areas.count() == 0
    assert PolicyArea.objects.count() == before


def test_ticking_muu_without_writing_anything_is_refused(signed_in):
    """Re-rendered with the error, which is how this form reports every refusal."""
    response = signed_in.post(
        CREATE, {"title": "Tühi muu", "policy_area_other_selected": "on", "policy_area_other": ""}
    )
    assert not Matter.objects.filter(title="Tühi muu").exists()
    assert response.context["form"].errors["policy_area_other"]


def test_unticking_muu_discards_the_text(signed_in):
    signed_in.post(CREATE, {"title": "Ilma muuta", "policy_area_other": "Ei tohiks salvestuda"})
    assert Matter.objects.get(title="Ilma muuta").policy_area_other == ""


# -- visibility --------------------------------------------------------------


def test_the_visibility_control_is_gone_from_the_form(specialist):
    assert "visibility" not in MatterCreateForm(viewer=specialist).fields


def test_the_page_does_not_offer_it_either(signed_in):
    body = signed_in.get(CREATE).content.decode()
    assert "Nähtavus" not in body
    assert 'name="visibility"' not in body


def test_a_new_matter_is_normal(signed_in):
    signed_in.post(CREATE, {"title": "Tavaline teema"})
    assert Matter.objects.get(title="Tavaline teema").visibility == Visibility.NORMAL


def test_a_posted_visibility_field_is_ignored(signed_in):
    """Decided server-side, so a crafted POST cannot restrict — or unrestrict."""
    signed_in.post(CREATE, {"title": "Sepitsetud", "visibility": Visibility.RESTRICTED})
    assert Matter.objects.get(title="Sepitsetud").visibility == Visibility.NORMAL


# -- files -------------------------------------------------------------------


def test_a_matter_can_be_created_with_one_file(signed_in, evidence_root):
    signed_in.post(
        CREATE, {"title": "Ühe failiga", "files": upload("kaaskiri.pdf", corpus.government_pdf())}
    )

    matter = Matter.objects.get(title="Ühe failiga")
    document = Document.objects.get(matter=matter)
    version = DocumentVersion.objects.get(document=document)
    assert version.original_filename == "kaaskiri.pdf"
    assert version.size_bytes > 0


def test_several_files_arrive_together(signed_in, evidence_root):
    signed_in.post(
        CREATE,
        {
            "title": "Mitme failiga",
            "files": [
                upload("kaaskiri.pdf", corpus.government_pdf()),
                upload("lisa.pdf", corpus.government_pdf()),
            ],
        },
    )

    matter = Matter.objects.get(title="Mitme failiga")
    assert Document.objects.filter(matter=matter).count() == 2
    assert DocumentVersion.objects.filter(document__matter=matter).count() == 2


def test_one_bad_file_leaves_no_half_made_matter(signed_in, evidence_root):
    """The failure this atomicity exists for.

    A Matter created with three of four files, and an error about the fourth, is
    worse than no Matter — the reader has to work out what is missing.
    """
    response = signed_in.post(
        CREATE,
        {
            "title": "Vigase failiga",
            "files": [
                upload("hea.pdf", corpus.government_pdf()),
                upload("paha.exe", b"MZ", content_type="application/x-msdownload"),
            ],
        },
    )

    assert response.status_code == 400
    assert not Matter.objects.filter(title="Vigase failiga").exists()
    assert not Document.objects.filter(title="hea.pdf").exists()


def test_a_matter_still_needs_only_a_title(signed_in):
    signed_in.post(CREATE, {"title": "Ainult pealkiri"})
    matter = Matter.objects.get(title="Ainult pealkiri")
    assert Document.objects.filter(matter=matter).count() == 0


def test_a_plain_text_file_is_accepted_like_any_other(signed_in, evidence_root):
    """`.txt` has no content signature, so nothing can reject it on its bytes."""
    signed_in.post(
        CREATE,
        {
            "title": "Tekstifailiga",
            "files": upload("kaaskiri.txt", "Näidiskaaskiri.".encode(), "text/plain"),
        },
    )

    matter = Matter.objects.get(title="Tekstifailiga")
    assert DocumentVersion.objects.get(document__matter=matter).original_filename == "kaaskiri.txt"


def test_the_form_reports_a_refused_file_instead_of_silently_staying_put(signed_in, evidence_root):
    """Whatever the reason, the reader must be told which file and why."""
    response = signed_in.post(
        CREATE,
        {
            "title": "Keelatud laiendiga",
            "files": upload("paha.exe", b"MZ", "application/x-msdownload"),
        },
    )

    assert response.status_code == 400
    messages = [str(m) for m in response.context["messages"]]
    assert any("laiend" in text.lower() for text in messages), messages


def test_the_whole_field_set_a_browser_sends_is_accepted(signed_in, evidence_root):
    """Everything the rendered page posts, including the empty NextAction block.

    The browser submits every field on the form — the prefilled date, the
    NextAction selects with their initial values, the unticked "Muu". A test
    that posts only a title exercises a POST no browser ever makes.
    """
    from django.utils import timezone as tz

    signed_in.post(
        CREATE,
        {
            "title": "Nagu brauser saadab",
            "owner": "",
            "received_date": tz.localdate().isoformat(),
            "source_organisation": "",
            "source_organisation_other": "",
            "policy_area_other": "",
            "stage": "",
            "track": "",
            "addressee_organisation": "",
            "response_deadline": "",
            "next-text": "",
            "next-kind": "DO",
            "next-date_semantics": "DEADLINE",
            "next-target_date": "",
            "next-responsible": "",
            "files": upload("kaaskiri.txt", "Näidiskaaskiri.".encode(), "text/plain"),
        },
    )

    matter = Matter.objects.get(title="Nagu brauser saadab")
    assert DocumentVersion.objects.filter(document__matter=matter).count() == 1
