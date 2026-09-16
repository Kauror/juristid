"""Retiring a `Hetkeseis` must be safe for the Matters already standing in it.

`StageVocabulary.is_active` is how a stage is withdrawn: the row stays, its
Matters stay, the statistics still count them, and it simply stops being offered
for new work. That is the rule `PolicyArea` and `LegalInstrumentType` follow, and
it is the rule the stage vocabulary's own selector claimed to follow.

It did not follow the other half of it. Every control that could set a stage on
an *existing* Matter — `MatterEditForm`, `MatterFieldForm` and the header's
inline select — was narrowed to the active rows, so a Matter left holding a
since-retired stage was not offered it back, was refused when the value was
posted, and — `Hetkeseis` being optional — had it replaced with NULL by an edit
that only meant to correct the title. A flag on reference data was able to
delete a recorded fact about a file (docs/adr/0032 §Amendment).

Every test here fixes one half of the contract: what a Matter that *holds* the
retired stage may do, and what every other Matter may not.

No stage is retired by this module's existence. `is_active` is flipped inside
each test on a stage the test itself made, which is why none of these tests
reads the seeded vocabulary.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.matters.forms import MatterCreateForm, MatterEditForm, MatterFieldForm
from app.matters.models import Matter
from app.matters.services import change_stage
from app.workflow.selectors import selectable_stages, stage_help_texts, stages_including
from tests import factories

pytestmark = pytest.mark.django_db


RETIRED_KEY = "vana-etapp"
RETIRED_LABEL = "Vana etapp"
RETIRED_HELP = "Kasutusel seni, kuni osakond selle etapi kasutamise lõpetas."


def _retire(stage) -> None:
    stage.is_active = False
    stage.save(update_fields=["is_active"])


@pytest.fixture
def retired_stage(db):
    """A stage a Matter was standing in when the department withdrew it."""
    return factories.StageFactory(key=RETIRED_KEY, label_et=RETIRED_LABEL, help_text=RETIRED_HELP)


@pytest.fixture
def matter_a(specialist, retired_stage):
    """Matter A: held the stage while it was current, and still holds it."""
    matter = factories.MatterFactory(owner=specialist, title="Vana teema")
    change_stage(matter=matter, stage=retired_stage, actor=specialist)
    _retire(retired_stage)
    matter.refresh_from_db()
    return matter


@pytest.fixture
def matter_b(specialist):
    """Matter B: never carried it, so it is none of Matter B's business."""
    return factories.MatterFactory(owner=specialist, title="Muu teema")


def _edit_url(matter) -> str:
    return reverse("matters:matter_edit", kwargs={"pk": matter.pk})


def _body(response) -> str:
    return response.content.decode()


def _offered(form, field: str = "stage") -> list[str]:
    return [str(value) for value, _label in form.fields[field].choices]


# -- the selector ------------------------------------------------------------


def test_the_selector_adds_the_held_stage_and_nothing_else(retired_stage, matter_a):
    """The whole rule, in the one place it is written down.

    Two assertions rather than one, because the defect and its over-correction
    look the same from the Matter that holds the stage: offering it back is
    right, and offering *every* retired stage back would make `is_active`
    decorative.
    """
    other_retired = factories.StageFactory(key="teine-vana", label_et="Teine vana")
    _retire(other_retired)

    offered = list(stages_including(matter_a.stage))
    assert retired_stage in offered
    assert other_retired not in offered
    assert set(selectable_stages()) <= set(offered)


def test_the_selector_without_a_held_stage_is_the_offered_vocabulary(retired_stage):
    """`Uus teema`'s answer, unchanged. A new Matter has nothing to preserve."""
    _retire(retired_stage)
    assert list(stages_including(None)) == list(selectable_stages())


def test_the_selector_keeps_the_reviewed_order(matter_a):
    """Retirement is not a reordering. The department's sequence still holds."""
    offered = list(stages_including(matter_a.stage))
    assert offered == sorted(offered, key=lambda stage: (stage.sort_order, stage.label_et))


# -- 3. the edit form offers it, and says what it is -------------------------


def test_the_edit_form_offers_the_held_stage(matter_a, retired_stage):
    assert str(retired_stage.pk) in _offered(MatterEditForm(matter=matter_a))


def test_the_edit_page_marks_the_held_stage_as_historical(signed_in, matter_a, retired_stage):
    """Offered back, and not as an ordinary current choice.

    The same words the retired Valdkonna chips beside it carry, because a reader
    correcting an old file should not have to learn two vocabularies for one
    idea (templates/matters/matter_edit.html).
    """
    body = _body(signed_in.get(_edit_url(matter_a)))
    assert RETIRED_LABEL in body
    assert "kasutusest väljas" in body
    assert "chip--retired" in body


def test_the_edit_page_renders_the_held_stage_already_chosen(signed_in, matter_a, retired_stage):
    """Because that is what the record says, and the page is read before it is saved.

    This is also what makes the next test a real browser's POST rather than a
    hand-written one: the form comes back with the stage ticked, so submitting
    it unchanged carries the stage.
    """
    body = _body(signed_in.get(_edit_url(matter_a)))
    control = body[body.index(f'value="{retired_stage.pk}"') :][:200]
    assert "checked" in control, control


def test_the_held_stage_keeps_its_explanation(matter_a, retired_stage):
    """The department's sentence about the stage does not retire with the flag."""
    form = MatterEditForm(matter=matter_a)
    assert form.stage_help[str(retired_stage.pk)] == RETIRED_HELP
    # And `Uus teema`'s mapping is untouched: it explains what it offers.
    assert str(retired_stage.pk) not in stage_help_texts()


# -- 4 and 5. the Matter keeps it -------------------------------------------


def test_correcting_the_title_does_not_drop_the_held_stage(signed_in, matter_a, retired_stage):
    """The failure this whole arrangement exists to prevent.

    `Hetkeseis` is optional, so a queryset narrowed to the active rows did not
    even refuse loudly: the unchanged value simply failed validation and the
    field cleared, and a lawyer fixing a typo unfiled the Matter's stage.
    """
    signed_in.post(
        _edit_url(matter_a),
        {
            "title": "Parandatud pealkiri",
            "stage": str(retired_stage.pk),
            "visibility": Visibility.NORMAL,
        },
    )

    matter_a.refresh_from_db()
    assert matter_a.title == "Parandatud pealkiri"
    assert matter_a.stage == retired_stage


def test_posting_the_held_stage_back_is_valid(matter_a, retired_stage):
    form = MatterEditForm(
        {
            "title": "Vana teema",
            "stage": str(retired_stage.pk),
            "visibility": Visibility.NORMAL,
        },
        matter=matter_a,
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["stage"] == retired_stage


def test_the_held_stage_can_still_be_deliberately_left_behind(signed_in, matter_a):
    """Preserved is not stuck. Moving off a retired stage is a normal edit."""
    current = factories.StageFactory(key="praegune", label_et="Praegune etapp")

    signed_in.post(
        _edit_url(matter_a),
        {"title": "Vana teema", "stage": str(current.pk), "visibility": Visibility.NORMAL},
    )

    matter_a.refresh_from_db()
    assert matter_a.stage == current


# -- 6 and 7. the header's inline control ------------------------------------


def test_the_header_control_offers_the_held_stage(signed_in, matter_a, retired_stage):
    body = _body(signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter_a.pk})))
    assert f'value="{retired_stage.pk}"' in body
    assert "varasem hetkeseis" in body


def test_the_field_form_accepts_the_held_stage(matter_a, retired_stage):
    """A select offering more than the form accepts is a save that fails on submit."""
    form = MatterFieldForm({"stage": str(retired_stage.pk)}, matter=matter_a)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["stage"] == retired_stage


def test_resaving_the_held_stage_from_the_header_is_a_save(signed_in, matter_a, retired_stage):
    response = signed_in.post(
        reverse("matters:update_field", kwargs={"pk": matter_a.pk, "field": "stage"}),
        {"stage": str(retired_stage.pk)},
    )
    assert response.status_code == 200
    assert "Vigane väärtus." not in _body(response)
    matter_a.refresh_from_db()
    assert matter_a.stage == retired_stage


def test_an_unrelated_inline_edit_does_not_touch_the_held_stage(signed_in, matter_a, retired_stage):
    """One POST carries one field, and the others are left alone."""
    other = factories.UserFactory()
    signed_in.post(
        reverse("matters:update_field", kwargs={"pk": matter_a.pk, "field": "owner"}),
        {"owner": str(other.pk)},
    )
    matter_a.refresh_from_db()
    assert matter_a.stage == retired_stage


# -- 8 and 9. nobody else gains it -------------------------------------------


def test_another_matter_is_not_offered_the_retired_stage(matter_a, matter_b, retired_stage):
    assert str(retired_stage.pk) not in _offered(MatterEditForm(matter=matter_b))


def test_another_matter_cannot_be_given_the_retired_stage(matter_a, matter_b, retired_stage):
    """A crafted POST, not a rendered control. The queryset is the refusal."""
    form = MatterEditForm(
        {
            "title": "Muu teema",
            "stage": str(retired_stage.pk),
            "visibility": Visibility.NORMAL,
        },
        matter=matter_b,
    )
    assert not form.is_valid()
    assert "stage" in form.errors


def test_another_matters_inline_control_refuses_it(signed_in, matter_a, matter_b, retired_stage):
    response = signed_in.post(
        reverse("matters:update_field", kwargs={"pk": matter_b.pk, "field": "stage"}),
        {"stage": str(retired_stage.pk)},
    )
    assert response.status_code == 400
    matter_b.refresh_from_db()
    assert matter_b.stage is None


def test_a_new_matter_cannot_select_the_retired_stage(matter_a, specialist, retired_stage):
    """`Uus teema` reads the active vocabulary and goes on reading it."""
    assert str(retired_stage.pk) not in _offered(MatterCreateForm(viewer=specialist))

    form = MatterCreateForm(
        {"title": "Uus teema", "stage": str(retired_stage.pk)}, viewer=specialist
    )
    assert not form.is_valid()
    assert "stage" in form.errors


def test_a_crafted_create_post_does_not_reach_the_database(signed_in, matter_a, retired_stage):
    response = signed_in.post(
        reverse("matters:matter_create"),
        {"title": "Salakaval teema", "stage": str(retired_stage.pk)},
    )
    assert response.status_code == 400
    assert not Matter.objects.filter(title="Salakaval teema").exists()


# -- 10. the register still finds what carries it ----------------------------


def test_the_register_filter_still_finds_the_historical_matter(signed_in, matter_a, matter_b):
    """`?hetkeseis=` addresses the stage by key and never by `is_active`.

    A Matter that becomes unfindable the moment its stage is withdrawn is a
    Matter nobody can audit, which is the opposite of what retiring reference
    data is for.
    """
    body = _body(signed_in.get(reverse("matters:matter_list"), {"hetkeseis": RETIRED_KEY}))
    assert "Vana teema" in body
    assert "Muu teema" not in body


def test_the_applied_filter_still_names_the_retired_stage(signed_in, matter_a):
    """And the chip above the table says which stage, not which UUID."""
    body = _body(signed_in.get(reverse("matters:matter_list"), {"hetkeseis": RETIRED_KEY}))
    assert RETIRED_LABEL in body


# -- 11 and 12. nothing else moved -------------------------------------------


def test_the_audit_trail_of_the_original_change_is_intact(matter_a, retired_stage):
    """Retiring a stage rewrites no history. The event says what happened then."""
    event = ChangeEvent.objects.get(event_type=ChangeEventType.MATTER_STAGE_CHANGED)
    assert event.payload["to_label"] == RETIRED_LABEL
    assert event.payload["from_label"] is None


def test_moving_off_a_retired_stage_records_both_labels(matter_a, retired_stage, specialist):
    current = factories.StageFactory(key="praegune", label_et="Praegune etapp")
    change_stage(matter=matter_a, stage=current, actor=specialist)

    event = ChangeEvent.objects.filter(event_type=ChangeEventType.MATTER_STAGE_CHANGED).order_by(
        "-created_at"
    )[0]
    assert event.payload["from_label"] == RETIRED_LABEL
    assert event.payload["to_label"] == "Praegune etapp"


def test_an_active_stage_behaves_exactly_as_before(signed_in, specialist):
    """The control every Matter uses, on a vocabulary nobody has retired.

    Offered on both forms, accepted by both, and carrying none of the retired
    marking — so the preservation above cannot be read as a licence to mark
    ordinary work as historical.
    """
    stage = factories.StageFactory(key="kehtiv", label_et="Kehtiv etapp")
    matter = factories.MatterFactory(owner=specialist, title="Tavaline teema")

    assert str(stage.pk) in _offered(MatterEditForm(matter=matter))
    assert str(stage.pk) in _offered(MatterCreateForm(viewer=specialist))
    assert MatterFieldForm({"stage": str(stage.pk)}, matter=matter).is_valid()

    signed_in.post(
        _edit_url(matter),
        {"title": "Tavaline teema", "stage": str(stage.pk), "visibility": Visibility.NORMAL},
    )
    matter.refresh_from_db()
    assert matter.stage == stage

    body = _body(signed_in.get(_edit_url(matter)))
    assert "Kehtiv etapp" in body
    assert "kasutusest väljas" not in body


def test_a_matter_with_no_stage_is_unaffected(specialist, retired_stage):
    """The commonest Matter of all: nothing held, nothing to preserve."""
    _retire(retired_stage)
    matter = factories.MatterFactory(owner=specialist)

    offered = _offered(MatterEditForm(matter=matter))
    assert str(retired_stage.pk) not in offered
    assert offered == ["", *(str(stage.pk) for stage in selectable_stages())]
