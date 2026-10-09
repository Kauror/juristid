"""`Uus teema` as a lighter intake, with `Õigusakt -> Hetkeseis` guidance (docs/adr/0130).

What this file owns, on the create path and the pages around it:

1. **The order** — Pealkiri; Saatja | Vastutaja; Arvamuse tähtaeg | Menetluse
   link | Saabus; Õigusakt; Valdkonnad; Hetkeseis; Märkmed; Failid; the button.
   Measured on the rendered document, not read off the template.
2. **`Millest teema räägib` left creation, not the record.** Not rendered, not
   bound (a forged key writes nothing), not required — and still written on the
   Teema page and on `Muuda teemat`.
3. **`Nimetus` left the link block, not the record.** A URL alone is a complete
   answer; a historical name survives, is shown, and is carried across a
   correction on `Muuda teemat` unchanged.
4. **Guidance is presentation only.** The keys the script reads are on the
   inputs and the matrix is on the page, and the server accepts an atypical
   combination exactly as it accepts a typical one — no refusal, no rewrite.

The matrix itself is `tests/test_stage_guidance.py`; the browser behaviour —
dimming, union, nothing cleared — is `e2e/test_uus_teema_guided_intake.py`.
"""

from __future__ import annotations

import json
import re

import pytest
from django.urls import reverse

from app.matters.forms import MatterCreateForm, MatterEditForm, ProceduralLinkCreateForm
from app.matters.models import Matter, MatterProceduralLink
from app.matters.services import record_procedural_link
from app.taxonomy.models import LegalInstrumentType, PolicyArea
from app.workflow.models import NextAction, StageVocabulary
from app.workflow.stage_guidance import stage_guidance_payload
from tests import factories

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")
EIS_URL = "https://eelnoud.valitsus.ee/main/mount/docList/8f2c1a30-0000-0000-0000-000000000130"


def page(client, url: str = CREATE) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


def edit_url(matter: Matter) -> str:
    return reverse("matters:matter_edit", kwargs={"pk": matter.pk})


def form_body(body: str) -> str:
    """The `<form>` only, past the `<noscript>` organisation fallback."""
    start = body.index('<form class="form createform"')
    return body[start : body.index("</form>", start)]


def instrument(key: str) -> LegalInstrumentType:
    return LegalInstrumentType.objects.get(key=key)


def stage(key: str) -> StageVocabulary:
    return StageVocabulary.objects.get(key=key)


# ---------------------------------------------------------------------------
# 1 — The order
# ---------------------------------------------------------------------------

#: One marker per section, each unique in the form, in the owner's order —
#: Hetkeseis directly under the Õigusakt that guides it, then Valdkond
#: (docs/adr/0130 §1 and its 2026-10-02 amendment).
ORDER = [
    ('name="title"', "Pealkiri"),
    ('name="sender_name"', "Saatja"),
    ('name="owner"', "Vastutaja"),
    ('name="response_deadline"', "Arvamuse tähtaeg"),
    ('name="menetlus-url"', "Menetluse link"),
    ('name="received_date"', "Saabus"),
    ('name="legal_instruments"', "Õigusakt"),
    ('name="stage"', "Hetkeseis"),
    ('name="policy_areas"', "Valdkond"),
    ('name="notes"', "Märkmed"),
    ('name="files"', "Failid"),
    ("createform__actions", "Salvesta"),
]


def test_the_sections_are_in_the_owners_order(signed_in):
    body = form_body(page(signed_in))

    positions = [(body.index(marker), name) for marker, name in ORDER]

    assert [name for _, name in sorted(positions)] == [name for _, name in ORDER]


@pytest.mark.parametrize(
    ("earlier", "later"),
    [
        ("Saatja", "Vastutaja"),
        ("Arvamuse tähtaeg", "Menetluse link"),
        ("Menetluse link", "Saabus"),
        ("Õigusakt", "Hetkeseis"),
        ("Hetkeseis", "Valdkond"),
        ("Valdkond", "Märkmed"),
        ("Märkmed", "Failid"),
        ("Failid", "Salvesta"),
    ],
)
def test_each_named_pair_is_in_order(signed_in, earlier, later):
    body = form_body(page(signed_in))
    where = {name: body.index(marker) for marker, name in ORDER}

    assert where[earlier] < where[later]


def test_saatja_and_vastutaja_share_one_row_and_the_three_arrival_facts_another(signed_in):
    """Side by side is the stylesheet's; sharing a grid row is the markup's."""
    body = form_body(page(signed_in))

    sender_row = re.search(
        r'<div class="[^"]*createform__pair--sender[^"]*">(.*?)name="response_deadline"',
        body,
        re.S,
    )
    assert sender_row is not None
    assert 'name="sender_name"' in sender_row.group(1)
    assert 'name="owner"' in sender_row.group(1)

    start = body.index("createform__trio--dates")
    row = body[start : body.index('name="legal_instruments"')]
    for marker in ('name="response_deadline"', 'name="menetlus-url"', 'name="received_date"'):
        assert marker in row


def test_files_are_the_last_section_before_the_button(signed_in):
    body = form_body(page(signed_in))
    files = body.index('id="failid"')
    actions = body.index("createform__actions")

    between = body[files:actions]
    for later_field in ('name="notes"', 'name="stage"', 'name="title"'):
        assert later_field not in between


# ---------------------------------------------------------------------------
# 2 — `Millest teema räägib`: off the create form, on the record
# ---------------------------------------------------------------------------


def test_millest_teema_raagib_is_not_on_uus_teema(signed_in):
    body = page(signed_in)

    assert "Millest teema räägib" not in body
    assert 'name="brief_summary"' not in body
    assert 'id="id_brief_summary"' not in body
    assert "brief_summary" not in MatterCreateForm().fields


def test_the_similar_matters_panel_no_longer_listens_for_it(signed_in):
    region = page(signed_in).split('id="sarnased-teemad"', 1)[1].split("</div>", 1)[0]

    assert "#id_brief_summary" not in region
    assert "brief_summary" not in region


def test_a_matter_is_created_without_a_summary(signed_in, specialist):
    response = signed_in.post(CREATE, {"title": "Ilma kokkuvõtteta"})

    assert response.status_code == 302
    matter = Matter.objects.get(title="Ilma kokkuvõtteta")
    assert matter.brief_summary == ""


def test_a_forged_summary_is_not_written_from_uus_teema(signed_in):
    """The form has no field to bind it to, so the key is simply not read."""
    signed_in.post(CREATE, {"title": "Võltsitud kokkuvõte", "brief_summary": "Sisse smugeldatud."})

    assert Matter.objects.get(title="Võltsitud kokkuvõte").brief_summary == ""


def test_the_summary_is_still_a_matter_field_written_on_muuda_teemat(signed_in, specialist):
    signed_in.post(CREATE, {"title": "Kirjeldan hiljem", "owner": specialist.pk})
    matter = Matter.objects.get(title="Kirjeldan hiljem")

    edit = page(signed_in, edit_url(matter))
    assert "Millest teema räägib" in edit
    assert 'name="brief_summary"' in edit
    assert "brief_summary" in MatterEditForm.base_fields

    response = signed_in.post(
        edit_url(matter),
        {"title": matter.title, "owner": specialist.pk, "brief_summary": "Nüüd kirjeldatud."},
    )

    assert response.status_code == 302
    matter.refresh_from_db()
    assert matter.brief_summary == "Nüüd kirjeldatud."


def test_an_existing_summary_is_not_touched_by_this_change(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist, brief_summary="Ajalooline kokkuvõte.")

    assert "Ajalooline kokkuvõte." in page(signed_in, edit_url(matter))
    matter.refresh_from_db()
    assert matter.brief_summary == "Ajalooline kokkuvõte."


# ---------------------------------------------------------------------------
# 3 — `Nimetus`: off the link block, on the record
# ---------------------------------------------------------------------------


def test_nimetus_is_not_on_uus_teema(signed_in):
    body = page(signed_in)
    block = body[body.index('id="menetluse-link"') : body.index('name="received_date"')]

    assert "Nimetus" not in block
    assert 'name="menetlus-label"' not in body
    assert "label" not in ProceduralLinkCreateForm().fields


def test_a_link_is_saved_from_its_address_alone(signed_in):
    response = signed_in.post(CREATE, {"title": "Ainult aadress", "menetlus-url": EIS_URL})

    assert response.status_code == 302
    link = MatterProceduralLink.objects.get(matter__title="Ainult aadress")
    assert link.url == EIS_URL
    # Nothing fabricated in its place — no title read off the address.
    assert link.label == ""


def test_a_forged_name_is_not_written_from_uus_teema(signed_in):
    signed_in.post(
        CREATE,
        {"title": "Võltsitud nimetus", "menetlus-url": EIS_URL, "menetlus-label": "Eelnõu 1 SE"},
    )

    assert MatterProceduralLink.objects.get(matter__title="Võltsitud nimetus").label == ""


def test_a_url_only_link_renders_on_the_teema_page(signed_in):
    signed_in.post(CREATE, {"title": "Lingiga teema", "menetlus-url": EIS_URL})
    matter = Matter.objects.get(title="Lingiga teema")

    body = page(signed_in, reverse("matters:matter_detail", kwargs={"pk": matter.pk}))

    assert EIS_URL in body


def _historical_link(matter, actor):
    return record_procedural_link(
        matter=matter,
        kind="EIS",
        url=EIS_URL,
        label="Eelnõu 123 SE",
        actor=actor,
    )


def test_a_historical_name_is_still_shown(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _historical_link(matter, specialist)

    body = page(signed_in, reverse("matters:matter_detail", kwargs={"pk": matter.pk}))

    assert "Eelnõu 123 SE" in body


def test_correcting_the_address_on_muuda_teemat_keeps_the_stored_name(signed_in, specialist):
    """An absent `Nimetus` box must never read as «empty it»."""
    matter = factories.MatterFactory(owner=specialist)
    link = _historical_link(matter, specialist)

    edit = page(signed_in, edit_url(matter))
    assert 'name="menetlus-label"' not in edit
    revision = re.search(r'name="menetlus-revision" value="([^"]+)"', edit)
    assert revision is not None

    response = signed_in.post(
        edit_url(matter),
        {
            "title": matter.title,
            "owner": specialist.pk,
            "menetlus-url": "https://eelnoud.valitsus.ee/uus-aadress",
            "menetlus-revision": revision.group(1),
        },
    )

    assert response.status_code == 302
    link.refresh_from_db()
    assert link.url == "https://eelnoud.valitsus.ee/uus-aadress"
    assert link.label == "Eelnõu 123 SE"
    assert link.kind == "EIS"


def test_an_unrelated_save_on_muuda_teemat_keeps_the_stored_name(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    link = _historical_link(matter, specialist)
    edit = page(signed_in, edit_url(matter))
    revision = re.search(r'name="menetlus-revision" value="([^"]+)"', edit)
    assert revision is not None

    signed_in.post(
        edit_url(matter),
        {
            "title": "Uus pealkiri",
            "owner": specialist.pk,
            "menetlus-url": EIS_URL,
            "menetlus-revision": revision.group(1),
        },
    )

    link.refresh_from_db()
    assert link.label == "Eelnõu 123 SE"


def test_the_teema_page_paranda_still_offers_the_historical_name(signed_in, specialist):
    """The one place a stored name can still be corrected deliberately."""
    matter = factories.MatterFactory(owner=specialist)
    link = _historical_link(matter, specialist)

    body = page(signed_in, reverse("matters:matter_detail", kwargs={"pk": matter.pk}))

    assert f'id="id_menetluse_link_{link.pk}_label"' in body


# ---------------------------------------------------------------------------
# 4 — Guidance is presentation, never validation
# ---------------------------------------------------------------------------


def test_the_inputs_carry_stable_keys_not_labels(signed_in):
    body = form_body(page(signed_in))

    for key in ("vtk", "seadus", "direktiiv", "el-maarus"):
        assert f'data-instrument-key="{key}"' in body
    for key in ("idea", "parliament", "eu_procedure", "other"):
        assert f'data-stage-key="{key}"' in body
    # `Määramata` carries no key, which is how it is never dimmed.
    maaramata = re.search(r'<input[^>]*name="stage"[^>]*value=""[^>]*>', body)
    assert maaramata is not None
    assert "data-stage-key" not in maaramata.group(0)


def test_the_page_carries_the_matrix_once(signed_in):
    body = page(signed_in)
    found = re.search(
        r'<script id="hetkeseis-juhis" type="application/json">(.*?)</script>', body, re.S
    )

    assert found is not None
    assert json.loads(found.group(1)) == stage_guidance_payload()
    assert body.count('id="hetkeseis-juhis"') == 1


def test_nothing_is_dimmed_or_disabled_by_the_server(signed_in):
    """The server draws every chip the same; dimming is the browser's, on a class."""
    body = form_body(page(signed_in))

    assert "chip--atypical" not in body
    for tag in re.findall(r'<input[^>]*name="stage"[^>]*>', body):
        assert "disabled" not in tag
        assert 'tabindex="-1"' not in tag


def test_valdkonnad_carry_no_guidance_hooks(signed_in):
    body = form_body(page(signed_in))

    for tag in re.findall(r'<input[^>]*name="policy_areas"[^>]*>', body):
        assert "data-instrument-key" not in tag
        assert "data-stage-key" not in tag


def test_the_edit_page_draws_the_same_guidance(signed_in, specialist):
    """`Muuda teemat` draws it too since 2026-10-09 (docs/adr/0130 §8, amended; F5).

    The same matrix, serialised the same way, on the same row wrapper — one
    copy in Python, read by one script. Nothing is dimmed by the server: the
    page arrives with no `chip--atypical`, as `Uus teema` does.
    """
    matter = factories.MatterFactory(owner=specialist)

    body = page(signed_in, edit_url(matter))
    assert 'data-stage-guidance="hetkeseis-juhis"' in body
    found = re.search(
        r'<script id="hetkeseis-juhis" type="application/json">(.*?)</script>', body, re.S
    )
    assert found
    assert json.loads(found.group(1)) == stage_guidance_payload()
    assert "chip--atypical" not in body


def test_the_edit_page_keeps_a_closed_files_stage_out_of_the_guidance(signed_in, specialist):
    """A closed file's stage is stated, not offered — nothing there to dim (RULE-03)."""
    from app.matters.services import close_matter
    from app.workflow.enums import Disposition

    matter = factories.MatterFactory(
        owner=specialist, stage=StageVocabulary.objects.get(key="parliament")
    )
    close_matter(matter=matter, disposition=Disposition.OTHER, actor=specialist)
    matter.refresh_from_db()
    assert not matter.is_open

    body = page(signed_in, edit_url(matter))
    row = body.split('data-stage-guidance="hetkeseis-juhis"', 1)[1].split("</fieldset>", 1)[0]
    assert 'type="radio"' not in row
    assert f'type="hidden" name="stage" value="{matter.stage_id}"' in row


@pytest.mark.parametrize(
    ("instruments", "stage_key"),
    [
        (["eli-konsultatsioon"], "parliament"),
        (["seadus"], "eu_procedure"),
        (["vtk"], "in_force"),
        (["el-maarus"], "awaiting_transposition"),
    ],
)
def test_an_atypical_combination_is_accepted_and_stored_as_given(
    signed_in, specialist, instruments, stage_key
):
    title = f"Ebatavaline {stage_key}"
    response = signed_in.post(
        CREATE,
        {
            "title": title,
            "legal_instruments": [instrument(key).pk for key in instruments],
            "stage": stage(stage_key).pk,
        },
    )

    assert response.status_code == 302, response.content.decode()[:2000]
    matter = Matter.objects.get(title=title)
    assert matter.stage == stage(stage_key)
    assert sorted(matter.legal_instruments.values_list("key", flat=True)) == sorted(instruments)
    # Neither inferred nor rewritten.
    assert matter.track == ""


def test_the_form_refuses_no_combination(specialist):
    for instrument_row in LegalInstrumentType.objects.filter(is_active=True):
        for stage_row in StageVocabulary.objects.filter(is_active=True):
            form = MatterCreateForm(
                data={
                    "title": "Kombinatsioon",
                    "legal_instruments": [instrument_row.pk],
                    # `Muu siseriiklik` / `Muu ELi dokument` ask what it is;
                    # that rule is unrelated to the stage and stays.
                    "legal_instrument_other": "Muu akt",
                    "stage": stage_row.pk,
                },
                viewer=specialist,
            )
            assert form.is_valid(), (instrument_row.key, stage_row.key, form.errors)


# ---------------------------------------------------------------------------
# 5 — An ordinary creation is still one ordinary Matter
# ---------------------------------------------------------------------------


def test_a_full_creation_writes_one_matter_with_every_answer(signed_in, specialist):
    area = PolicyArea.objects.filter(is_active=True).order_by("pk").first()
    assert area is not None
    before = Matter.objects.count()

    response = signed_in.post(
        CREATE,
        {
            "title": "Täielik uus teema",
            "owner": specialist.pk,
            "response_deadline": "30.10.2026",
            "received_date": "1.10.2026",
            "menetlus-url": EIS_URL,
            "legal_instruments": [instrument("seadus").pk],
            "policy_areas": [area.pk],
            "stage": stage("consultation").pk,
            "notes": "Minu märge.",
        },
    )

    assert response.status_code == 302
    assert Matter.objects.count() == before + 1
    matter = Matter.objects.get(title="Täielik uus teema")
    assert matter.owner == specialist
    assert matter.response_deadline.isoformat() == "2026-10-30"
    assert matter.received_date.isoformat() == "2026-10-01"
    assert matter.stage == stage("consultation")
    assert list(matter.policy_areas.all()) == [area]
    assert matter.brief_summary == ""
    assert MatterProceduralLink.objects.filter(matter=matter, url=EIS_URL, label="").count() == 1
    # The deadline is the obligation only, and the faint plan is the path
    # (docs/adr/0133 §8): no step is made from it.
    assert NextAction.objects.filter(matter=matter).count() == 0
    assert matter.plan_steps.count() == 5


# ---------------------------------------------------------------------------
# 6 — «Valdkond», in the singular, and still several values
#     (docs/adr/0130, amendment of 2026-10-02)
# ---------------------------------------------------------------------------


def _valdkond_legend(body: str) -> str:
    """The text of the `<legend>` that heads the `policy_areas` group."""
    before = body[: body.index('name="policy_areas"')]
    start = before.rindex("<legend")
    legend = body[start : body.index("</legend>", start)]
    text = re.sub(r"<[^>]+>", " ", legend)
    return " ".join(text.split())


def test_the_heading_is_valdkond_in_the_singular(signed_in):
    body = form_body(page(signed_in))

    assert _valdkond_legend(body) == "Valdkond"
    assert "Valdkonnad" not in body


def test_valdkond_is_still_a_multi_select(signed_in, specialist):
    field = MatterCreateForm(viewer=specialist).fields["policy_areas"]
    body = form_body(page(signed_in))

    assert field.__class__.__name__ == "ModelMultipleChoiceField"
    assert 'data-chipcount-for="policy_areas"' in body
    assert body.count('type="checkbox" name="policy_areas"') == (
        PolicyArea.objects.filter(is_active=True).count()
    )


def test_several_valdkond_values_are_saved(signed_in):
    areas = list(PolicyArea.objects.filter(is_active=True).order_by("pk")[:3])
    assert len(areas) == 3

    response = signed_in.post(
        CREATE,
        {"title": "Kolm valdkonda", "policy_areas": [area.pk for area in areas]},
    )

    assert response.status_code == 302
    matter = Matter.objects.get(title="Kolm valdkonda")
    assert sorted(matter.policy_areas.values_list("pk", flat=True)) == sorted(
        area.pk for area in areas
    )


def test_muuda_teemat_now_says_valdkond_too(signed_in, specialist):
    """The edit page was decided on 2026-10-02: one fact, one word (docs/adr/0131 §14)."""
    matter = factories.MatterFactory(owner=specialist)

    assert _valdkond_legend(page(signed_in, edit_url(matter))) == "Valdkond"


def test_the_saabunud_form_ends_with_its_buttons(signed_in):
    """The registration form's footer note went with Uus teema's (docs/adr/0144 §7)."""
    body = signed_in.get(reverse("matters:intake")).content.decode()
    actions = body[body.index('class="createform__actions"') :]
    actions = actions[: actions.index("</div>")]

    assert ">Salvesta<" in actions
    assert "Ülejäänud andmed" not in body
    assert "createform__note" not in body
