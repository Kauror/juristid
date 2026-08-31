"""Uus teema, rebuilt to the approved design.

The round changed the *shape* of the page: no disclosures, paired rows, one
chip control, and two texts a lawyer writes while the file is still in front of
them. What it must not have changed is anything a Matter ends up holding, so
almost every test here asserts the canonical record a POST produces rather than
the markup that produced it — a legibility change that quietly stores something
different is a data bug wearing a UI change.

The structural claims that only a browser can settle — that a tooltip opens on
hover and on focus, that the page does not scroll sideways at 1024 — are in
`e2e/test_matter_form_ux.py`.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from app.accounts.enums import UserRole
from app.core.enums import Visibility
from app.documents.enums import DocumentRole
from app.documents.models import Document, DocumentVersion
from app.matters.forms import MatterCreateForm
from app.matters.models import Matter, MatterPersonalNote
from app.submissions.models import Submission
from app.taxonomy.models import PolicyArea
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics, Track
from app.workflow.models import NextAction
from tests import factories
from tests import synthetic_corpus as corpus

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")


def upload(name: str, content: bytes, content_type: str = "application/pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content, content_type=content_type)


# ---------------------------------------------------------------------------
# The minimal create
# ---------------------------------------------------------------------------


def test_a_title_alone_still_creates_a_matter_and_invents_nothing(signed_in, specialist):
    """The rule the whole page is arranged around (master specification 3.8).

    Every optional field is on screen now rather than behind two disclosures,
    and a form that *shows* a field is not a form that requires one — nor one
    that quietly fills it in. What must not exist afterwards is as much of the
    assertion as what must.
    """
    response = signed_in.post(CREATE, {"title": "Ainult pealkiri"})

    matter = Matter.objects.get(title="Ainult pealkiri")
    assert response.status_code == 302
    assert response["Location"] == reverse("matters:matter_detail", kwargs={"pk": matter.pk})

    # Nothing was invented to fill the layout.
    assert not NextAction.objects.filter(matter=matter).exists()
    assert not Document.objects.filter(matter=matter).exists()
    assert not MatterPersonalNote.objects.filter(matter=matter).exists()
    assert matter.brief_summary == ""
    assert matter.policy_area_other == ""
    assert matter.owner is None
    assert matter.stage is None
    assert matter.track == ""
    assert matter.addressee_organisation is None
    assert list(matter.source_organisations.all()) == []
    assert list(matter.policy_areas.all()) == []
    # And the two the form decides rather than reads.
    assert matter.visibility == Visibility.NORMAL
    assert matter.data_class == "REAL"


def test_the_whole_field_set_the_new_page_posts_is_accepted(signed_in, evidence_root):
    """Everything a browser sends from the rebuilt form, including the empties.

    The page posts more fields than it used to, because nothing is behind a
    closed `<details>` any more: `Saabus` and `Arvamuse tähtaeg` arrive
    prefilled, and every optional select and text box arrives empty. A test
    that posts only a title exercises a POST no browser makes.

    The next-action half is now two blank boxes. The mode chip that started on
    TEEN, the meaning chip that started on Tähtaeg and the date that started on
    today are all gone: a lawyer who wrote nothing about a next step posts
    nothing about one, and the block is silent rather than pre-answered
    (ADR 0052 addendum).
    """
    from django.utils import timezone as tz

    today = tz.localdate().isoformat()
    signed_in.post(
        CREATE,
        {
            "title": "Nagu brauser saadab",
            "brief_summary": "",
            "notes": "",
            "owner": "",
            "received_date": today,
            "response_deadline": today,
            "policy_area_other": "",
            "stage": "",
            "track": "",
            "addressee_organisation": "",
            "next-text": "",
            "next-target_date": "",
            "files": upload("kaaskiri.txt", "Näidiskaaskiri.".encode(), "text/plain"),
        },
    )

    matter = Matter.objects.get(title="Nagu brauser saadab")
    assert DocumentVersion.objects.filter(document__matter=matter).count() == 1
    # The next-action block was on screen and untouched. Nothing was written,
    # and — the part the old prefilled date used to hide — nothing needed to be
    # refused either.
    assert not NextAction.objects.filter(matter=matter).exists()


# ---------------------------------------------------------------------------
# The full create
# ---------------------------------------------------------------------------


def test_a_full_create_stores_exactly_what_was_entered(signed_in, specialist, evidence_root):
    """One POST, one Matter, and every fact on it traceable to a control."""
    stage = factories.StageFactory(label_et="Kooskõlastusringil")
    ministry = factories.OrganisationFactory(name="Kliimaministeerium")
    committee = factories.OrganisationFactory(name="Riigikogu majanduskomisjon")
    area = factories.PolicyAreaFactory(name_et="Pakendid")

    signed_in.post(
        CREATE,
        {
            "title": "Pakendiseaduse muutmise seaduse eelnõu",
            "brief_summary": "Laiendaks tootjavastutust pakendiettevõtetele.",
            "notes": "Helista Tiinale enne kooskõlastusringi lõppu.",
            "owner": specialist.pk,
            "received_date": "24.8.2026",
            "response_deadline": "18.9.2026",
            "source_organisations": [ministry.pk],
            "policy_areas": [area.pk],
            "stage": stage.pk,
            "track": Track.DOMESTIC,
            "addressee_organisation": committee.pk,
            "files": upload("eelnou.pdf", corpus.government_pdf()),
            "next-text": "Loen eelnõu läbi ja koostan liikmete küsitluse",
            "next-target_date": "5.9.2026",
        },
    )

    matter = Matter.objects.get(title="Pakendiseaduse muutmise seaduse eelnõu")
    assert matter.brief_summary == "Laiendaks tootjavastutust pakendiettevõtetele."
    assert matter.owner == specialist
    assert matter.received_date == date(2026, 8, 24)
    assert matter.response_deadline == date(2026, 9, 18)
    assert list(matter.source_organisations.all()) == [ministry]
    assert list(matter.policy_areas.all()) == [area]
    assert matter.stage == stage
    assert matter.track == Track.DOMESTIC
    assert matter.addressee_organisation == committee
    assert matter.visibility == Visibility.NORMAL

    # The private note is a MatterPersonalNote belonging to its author, not a
    # column on the Matter and not an Entry on the timeline.
    note = MatterPersonalNote.objects.get(matter=matter)
    assert note.author == specialist
    assert note.body == "Helista Tiinale enne kooskõlastusringi lõppu."

    version = DocumentVersion.objects.get(document__matter=matter)
    assert version.original_filename == "eelnou.pdf"

    action = NextAction.objects.get(matter=matter)
    # Nobody chose these three. A step created natively is DO / DEADLINE /
    # EXACT, because on this surface the date is the day the work gets done
    # (ADR 0052 §3).
    assert action.kind == ActionKind.DO
    assert action.date_semantics == DateSemantics.DEADLINE
    assert action.date_precision == DatePrecision.EXACT
    assert action.target_date == date(2026, 9, 5)
    # No responsible control on the page; the step inherits the Matter's owner.
    assert action.responsible == specialist


def test_the_summary_is_the_matters_own_field_and_not_an_entry(signed_in, specialist):
    """`Lühikokkuvõte` answers *what is this*.

    Not `position_summary` (what Koda thinks), not `rationale_summary` (why),
    and not the first Entry (what happened on a day). None of the three can be
    made to mean this without corrupting it (Teema redesign §6).
    """
    signed_in.post(
        CREATE,
        {"title": "Kokkuvõttega", "brief_summary": "  Kaks lauset tavakeeles.  "},
    )

    matter = Matter.objects.get(title="Kokkuvõttega")
    assert matter.brief_summary.strip() == "Kaks lauset tavakeeles."
    assert matter.position_summary == ""
    assert matter.rationale_summary == ""
    assert matter.entries.count() == 0


def test_a_blank_note_writes_no_row(signed_in, specialist):
    """A record saying somebody wrote nothing is not worth keeping."""
    signed_in.post(CREATE, {"title": "Ilma märkmeteta", "notes": "   "})
    matter = Matter.objects.get(title="Ilma märkmeteta")
    assert not MatterPersonalNote.objects.filter(matter=matter).exists()


def test_the_note_belongs_to_its_author_and_to_nobody_else(client, specialist, other_specialist):
    """It is scoped by user, never by the Matter's visibility."""
    from app.matters.services import personal_note_for

    client.force_login(specialist)
    client.post(CREATE, {"title": "Minu märkmetega", "notes": "Ainult minule."})

    matter = Matter.objects.get(title="Minu märkmetega")
    assert personal_note_for(matter=matter, author=specialist) == "Ainult minule."
    assert personal_note_for(matter=matter, author=other_specialist) == ""


# ---------------------------------------------------------------------------
# Valdkonnad — the approved twenty-two
# ---------------------------------------------------------------------------


#: What a lawyer may pick today, in the department's order. Twenty-one governed
#: areas and the free-text `Muu` affordance, which is not a PolicyArea at all.
APPROVED = [
    "Maksejõuetus",
    "Raamatupidamine",
    "Intellektuaalomand",
    "Toetusmeetmed",
    "Koalitsioonilepped",
    "Õigusloome",
    "Energeetika",
    "Riigihanked",
    "Haridus",
    "Tarbijakaitse",
    "Alkohol, tubakas",
    "Digiteemad",
    "Finantsõigus, rahapesu",
    "Ehitus",
    "Äriõigus",
    "Välistööjõud",
    "Maksud ja toll",
    "Töösuhted, töökeskkond",
    "Keskkond",
    "ELi õiguse ülevõtmine",
    "Arengukavad, strateegiad",
]


def test_the_form_offers_exactly_the_approved_twenty_two(signed_in, specialist):
    """Twenty-one chips plus `Muu`, and `Muu` is not one of the twenty-one.

    Written out longhand rather than read from the manifest the form reads: a
    test that consults the same source it is checking agrees with any edit,
    including a wrong one.
    """
    offered = [
        str(label)
        for _value, label in MatterCreateForm(viewer=specialist).fields["policy_areas"].choices
    ]
    assert offered == APPROVED

    # And `Muu` is the free-text affordance beside them, on the same row.
    assert MatterCreateForm(viewer=specialist).fields["policy_area_other_selected"].label == "Muu"

    body = signed_in.get(CREATE).content.decode()
    assert body.count('name="policy_areas"') == len(APPROVED)
    assert 'name="policy_area_other_selected"' in body


@pytest.mark.parametrize("withdrawn", ["Muud teemad", "Olulised tähtajad"])
def test_the_withdrawn_labels_are_not_offered_anywhere_on_the_page(
    signed_in, specialist, withdrawn
):
    """`Olulised tähtajad` is a watch list, not a subject area. `Muud teemad`
    is `Muu` a second time (Uus teema redesign §7)."""
    offered = [
        str(label)
        for _value, label in MatterCreateForm(viewer=specialist).fields["policy_areas"].choices
    ]
    assert withdrawn not in offered

    body = signed_in.get(CREATE).content.decode()
    assert withdrawn not in body


def test_a_matter_already_filed_under_a_withdrawn_label_keeps_it(signed_in, specialist):
    """Withdrawal stops the label being offered. It changes no record.

    The row, the relation and the classification all stay, and the Teema header
    offers it back under its "varasem valdkond" note so that correcting one
    field on an old Matter cannot silently drop its filing
    (`taxonomy/0004`, Uus teema redesign §7.2).
    """
    catchall = PolicyArea.objects.get(key="muud-teemad")
    matter = factories.MatterFactory(owner=specialist)
    matter.policy_areas.add(catchall)

    assert not catchall.is_active
    assert list(matter.policy_areas.all()) == [catchall]

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()
    assert "Muud teemad" in body
    assert "varasem valdkond" in body


def test_editing_another_field_does_not_drop_a_withdrawn_classification(signed_in, specialist):
    """The failure this whole arrangement exists to prevent.

    A form whose queryset held only the current twenty-one would refuse — or
    worse, silently discard — a save that merely left a retired area ticked.
    """
    from app.matters.forms import MatterEditForm

    catchall = PolicyArea.objects.get(key="muud-teemad")
    matter = factories.MatterFactory(owner=specialist, title="Vana teema")
    matter.policy_areas.add(catchall)

    form = MatterEditForm(
        {"title": "Vana teema, parandatud", "policy_areas": [str(catchall.pk)]},
        matter=matter,
    )
    assert form.is_valid(), form.errors
    assert list(form.cleaned_data["policy_areas"]) == [catchall]

    # And the control offers it back rather than pretending it is not there.
    offered = [
        str(label) for _value, label in MatterEditForm(matter=matter).fields["policy_areas"].choices
    ]
    assert "Muud teemad" in offered


def test_the_free_text_muu_still_creates_no_taxonomy(signed_in):
    """`Muu` writes a column, never a row (Stage-2E.1 brief 20)."""
    before = PolicyArea.objects.count()
    signed_in.post(
        CREATE,
        {
            "title": "Muu valdkonnaga",
            "policy_area_other_selected": "on",
            "policy_area_other": "Ringmajandus",
        },
    )

    matter = Matter.objects.get(title="Muu valdkonnaga")
    assert matter.policy_area_other == "Ringmajandus"
    assert matter.policy_areas.count() == 0
    assert PolicyArea.objects.count() == before


# ---------------------------------------------------------------------------
# Hetkeseis, and the explanation each chip carries
# ---------------------------------------------------------------------------


def test_every_offered_stage_carries_the_departments_own_explanation(signed_in):
    """The text is reference data on the row, not a sentence in a template.

    A template spelling the same words would be a second copy nobody updates
    when the department rewords the first (`workflow/0006`).
    """
    from app.workflow.selectors import selectable_stages, stage_help_texts

    texts = stage_help_texts()
    stages = list(selectable_stages())
    assert stages
    assert {str(stage.pk) for stage in stages} == set(texts)

    consultation = next(stage for stage in stages if stage.key == "consultation")
    assert texts[str(consultation.pk)].startswith("Seaduse või määruse eelnõu kooskõlastusringile")


def test_the_page_renders_one_tooltip_per_explained_stage(signed_in):
    """One bubble per chip, and each radio points at its own.

    `aria-describedby` rather than `title=`, which a keyboard user never sees
    and a screen reader may or may not announce.
    """
    import re

    from app.workflow.selectors import stage_help_texts

    body = signed_in.get(CREATE).content.decode()
    bubbles = re.findall(r'<span class="stagehelp" role="tooltip"\s+id="([^"]+)"', body)
    assert len(bubbles) == len(stage_help_texts())

    for bubble_id in bubbles:
        assert f'aria-describedby="{bubble_id}"' in body

    # The named blank option explains nothing and points at nothing.
    blank = re.search(r'<input[^>]*name="stage"[^>]*value=""[^>]*>', body)
    assert blank, "the Määramata option is not rendered"
    assert "aria-describedby" not in blank.group(0)


def test_the_supplied_muu_text_is_the_one_the_source_gives(signed_in):
    """Flagged, not fixed.

    The business text supplied for `muu` is word for word the text supplied for
    `ELi menetluses`. That is very likely a slip in the source document, but
    which of the two is wrong is a product decision and not a migration's to
    make — so the duplication ships visibly and is named in ADR 0032. This test
    exists so that resolving it is a deliberate edit rather than a silent one.
    """
    from app.workflow.models import StageVocabulary

    other = StageVocabulary.objects.get(key="other")
    eu = StageVocabulary.objects.get(key="eu_procedure")
    assert other.help_text == eu.help_text


def test_rohkem_pole_tegevusi_plaanis_is_still_not_a_stage(signed_in):
    """The eleventh workbook value, and the one that is not a Hetkeseis.

    It says Koda stopped working on the file, which is a closure reason. It was
    supplied with a description alongside the ten stage texts, and adopting it
    as an eleventh chip would merge two different questions into one column
    (`workflow/0004`, master specification 3.4).
    """
    from app.workflow.models import LegacyStatusMapping, StageVocabulary

    assert not StageVocabulary.objects.filter(label_et__icontains="rohkem pole").exists()
    mapping = LegacyStatusMapping.objects.get(raw_label="rohkem pole tegevusi plaanis")
    assert mapping.stage is None
    assert mapping.disposition == "MONITORING_STOPPED"

    body = signed_in.get(CREATE).content.decode()
    assert "rohkem pole tegevusi plaanis" not in body


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def test_a_file_arriving_with_a_matter_is_evidence_and_not_a_submission(signed_in, evidence_root):
    """An uploaded creation file is ordinary incoming evidence.

    Sending an opinion is a deliberate act on its own surface. A form that
    classified every attachment as a sent opinion would manufacture an outbound
    record nobody made (master specification 8, ADR 0011).
    """
    signed_in.post(
        CREATE,
        {"title": "Tõendiga", "files": upload("kaaskiri.pdf", corpus.government_pdf())},
    )

    matter = Matter.objects.get(title="Tõendiga")
    document = Document.objects.get(matter=matter)
    assert document.role == DocumentRole.INCOMING_AUTHORITY
    assert not Submission.objects.filter(matter=matter).exists()


def test_the_stored_version_is_immutable_evidence(signed_in, evidence_root):
    """Captured through the ordinary services: one Document, one version, a
    digest over the exact bytes."""
    signed_in.post(
        CREATE,
        {"title": "Muutumatu", "files": upload("kaaskiri.pdf", corpus.government_pdf())},
    )

    version = DocumentVersion.objects.get(document__matter__title="Muutumatu")
    assert version.sha256
    assert version.size_bytes == len(corpus.government_pdf())
    assert DocumentVersion.objects.filter(document=version.document).count() == 1


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


def test_no_control_on_the_page_is_a_native_date_input(signed_in):
    """The whole `mm/dd/yyyy` class of defect, asserted on the rendered page.

    A native date input takes its format from the *browser's* locale, so a
    US-English Chrome showed `mm/dd/yyyy` on an Estonian form and read
    `7.9.2026` as the 9th of July (app/core/widgets.py).
    """
    body = signed_in.get(CREATE).content.decode()
    assert 'type="date"' not in body
    assert "mm/dd/yyyy" not in body
    assert body.count("pp.kk.aaaa") >= 1


def test_a_typed_estonian_date_is_stored_as_a_real_date(signed_in):
    signed_in.post(
        CREATE,
        {
            "title": "Eesti kuupäevaga",
            "received_date": "7.9.2026",
            "response_deadline": "23.8.2026",
        },
    )
    matter = Matter.objects.get(title="Eesti kuupäevaga")
    assert matter.received_date == date(2026, 9, 7)
    assert matter.response_deadline == date(2026, 8, 23)


def test_a_refused_save_gives_back_every_date_that_was_typed(signed_in):
    """No browser can refill a file input, which is exactly why everything else
    has to come back."""
    response = signed_in.post(
        CREATE,
        {
            "title": "",
            "received_date": "7.9.2026",
            "response_deadline": "23.8.2026",
            "brief_summary": "Mida see teema tähendab.",
            "notes": "Isiklik meeldetuletus.",
        },
    )

    assert response.status_code == 400
    form = response.context["form"]
    assert form.errors["title"]
    assert form["received_date"].value() == "7.9.2026"
    assert form["response_deadline"].value() == "23.8.2026"
    assert form["brief_summary"].value() == "Mida see teema tähendab."
    assert form["notes"].value() == "Isiklik meeldetuletus."

    body = response.content.decode()
    assert "Mida see teema tähendab." in body
    assert "Isiklik meeldetuletus." in body


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def test_a_reader_cannot_open_the_create_page(client):
    """A READER may read the register and change nothing in it.

    404 rather than 403, matching every other refusal in this module: a reader
    who may not write is not told which surfaces exist for those who may.
    """
    reader = factories.UserFactory(role=UserRole.READER)
    client.force_login(reader)

    assert client.get(CREATE).status_code == 404


def test_a_reader_cannot_create_by_posting_directly(client):
    reader = factories.UserFactory(role=UserRole.READER)
    client.force_login(reader)

    response = client.post(CREATE, {"title": "Lugeja teema"})

    assert response.status_code == 404
    assert not Matter.objects.filter(title="Lugeja teema").exists()


def test_a_reader_is_not_offered_the_button_either(client):
    """A page that offers a control it knows will fail is a page lying to
    somebody. The route refuses regardless; this only hides the door."""
    reader = factories.UserFactory(role=UserRole.READER)
    client.force_login(reader)

    body = client.get(reverse("matters:matter_list")).content.decode()
    assert "topbar__cta" not in body
    assert CREATE not in body


def test_a_department_head_may_still_create(client, department_head):
    client.force_login(department_head)
    client.post(CREATE, {"title": "Osakonnajuhi teema"})
    assert Matter.objects.filter(title="Osakonnajuhi teema").exists()


def test_the_form_still_creates_no_organisation(signed_in):
    """Reference data is edited deliberately, under its own surface — including
    from the Adressaat control, which is new to this page as chips
    (master specification 14.7, ADR 0025)."""
    from app.organisations.models import Organisation

    before = Organisation.objects.count()
    signed_in.post(
        CREATE,
        {"title": "Tundmatu adressaadiga", "addressee_organisation": "Uus Ministeerium"},
    )

    assert Organisation.objects.count() == before
    # A name where a primary key belongs is refused, not resolved.
    assert not Matter.objects.filter(title="Tundmatu adressaadiga").exists()


def test_a_posted_visibility_is_still_ignored(signed_in):
    """Decided server-side, so a crafted POST cannot restrict — or unrestrict."""
    signed_in.post(CREATE, {"title": "Sepitsetud", "visibility": Visibility.RESTRICTED})
    assert Matter.objects.get(title="Sepitsetud").visibility == Visibility.NORMAL
