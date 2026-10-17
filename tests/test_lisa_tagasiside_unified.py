"""`+ Kaasamine · Lisa tagasiside` is the one door for every position (docs/adr/0170).

PROVENANCE — stated, never inferred (docs/adr/0091 §3.2)
* «Saadeti Kojale» writes `RECEIVED`; «Avaldatud mujal» writes `DISCOVERED`;
* no answer is refused — there is no default, and `LEGACY` is not an answer;
* a linked round fixes the provenance at `RECEIVED`; «Avaldatud mujal» with a
  round is refused, and the page draws it disabled.

MEMBER FLAG — three layers (docs/adr/0095 §4)
* `Liige` saves with «Saadeti Kojale»;
* the form refuses it with «Avaldatud mujal», and so does the service.

ENGAGEMENT REPLIES (docs/adr/0167 §2)
* the first answer to an open round finishes its wait; a later one is kept and
  the closure stays; an identical repeat is refused;
* a standalone position — either provenance — touches no wait.

BOUNDARIES
* received feedback may name nobody (docs/adr/0101); a published position names
  its organisation; text, a link or a file — any one — is a source
  (docs/adr/0084 §3), an empty record is refused; files become evidence;
* a closed Matter, a reader and the gate visitor write nothing;
* a reply to a restricted round is restricted, and search respects it;
* the old doors are gone: routes, chips and panels.
"""

from __future__ import annotations

import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.documents.links import DocumentLink
from app.matters.enums import EngagementKind, ExternalPositionProvenance
from app.matters.forms import (
    FEEDBACK_LINKED_ROUND_IS_RECEIVED,
    FEEDBACK_NEEDS_PROVENANCE,
    FEEDBACK_POSITION_HELP,
)
from app.matters.models import MatterEngagement, MatterExternalPosition
from app.matters.services import (
    EXTERNAL_POSITION_MEMBER_IS_RECEIVED_ONLY,
    EXTERNAL_POSITION_NEEDS_ORGANISATION,
    EXTERNAL_POSITION_NEEDS_SOURCE,
    add_engagement,
    complete_engagement_feedback,
)
from app.matters.workspace import add_matter_external_position
from app.search.services import result_count
from tests import factories
from tests.gate import PASSWORD, apply_shared_gate
from tests.refusals import refused

pytestmark = pytest.mark.django_db

RECEIVED = ExternalPositionProvenance.RECEIVED.value
DISCOVERED = ExternalPositionProvenance.DISCOVERED.value


def _url(matter) -> str:
    return reverse("matters:add_engagement_reply", kwargs={"pk": matter.pk})


def _post(client, matter, data, files=None):
    return client.post(_url(matter), {**data, **(files or {})}, headers={"HX-Request": "true"})


def _round(matter, actor, title: str = "Liikmete küsitlus") -> MatterEngagement:
    return add_engagement(
        matter=matter,
        kind=EngagementKind.OTHER,
        title=title,
        occurred_on=timezone.localdate(),
        actor=actor,
    )


def _detail(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _form(body: str) -> str:
    start = body.index('id="kaasamine-tagasiside"')
    return body[start : body.index("</form>", start)]


def _pdf(name: str = "liidu-vastus.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(
        name, b"%PDF-1.4\nsunteetiline vastus", content_type="application/pdf"
    )


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def test_saadeti_kojale_records_received_feedback_with_no_round(
    signed_in, normal_matter, organisation
):
    response = _post(
        signed_in,
        normal_matter,
        {
            "organisation": str(organisation.pk),
            "provenance": RECEIVED,
            "summary": "Liige toetab eelnõu muudatusettepanekuga.",
            "stated_on": "03.10.2026",
            "url": "https://example.org/liikme-vastus",
        },
    )

    assert response.status_code == 200
    (position,) = MatterExternalPosition.objects.filter(matter=normal_matter)
    assert position.provenance == RECEIVED
    assert position.organisation_id == organisation.pk
    assert position.engagement_id is None
    assert position.url == "https://example.org/liikme-vastus"
    assert position.stated_on.isoformat() == "2026-10-03"
    assert position.lawyer_note == "" and position.source_label == ""


def test_avaldatud_mujal_records_a_discovered_position(signed_in, normal_matter, organisation):
    response = _post(
        signed_in,
        normal_matter,
        {
            "organisation": str(organisation.pk),
            "provenance": DISCOVERED,
            "summary": "Liit avaldas oma veebilehel vastuseisu eelnõule.",
            "url": "https://example.org/avaldatud",
        },
    )

    assert response.status_code == 200
    (position,) = MatterExternalPosition.objects.filter(matter=normal_matter)
    assert position.provenance == DISCOVERED
    assert position.source_is_member is False
    assert position.engagement_id is None


def test_a_typed_organisation_is_accepted(signed_in, normal_matter):
    response = _post(
        signed_in,
        normal_matter,
        {
            "organisation_name": "Sünteetiline Tööstusliit",
            "provenance": DISCOVERED,
            "summary": "Seisukoht pressiteates.",
        },
    )

    assert response.status_code == 200
    (position,) = MatterExternalPosition.objects.filter(matter=normal_matter)
    assert position.organisation.name == "Sünteetiline Tööstusliit"


@pytest.mark.parametrize("provenance", ["", "LEGACY", "SOMETHING"])
def test_no_stated_provenance_is_refused(signed_in, normal_matter, organisation, provenance):
    data = {"organisation": str(organisation.pk), "summary": "Kuidas see tuli?"}
    if provenance:
        data["provenance"] = provenance

    response = _post(signed_in, normal_matter, data)

    assert response.status_code == 400
    if not provenance:
        assert FEEDBACK_NEEDS_PROVENANCE in response.content.decode()
    assert not MatterExternalPosition.objects.filter(matter=normal_matter).exists()


def test_the_provenance_control_has_no_default(signed_in, normal_matter, specialist):
    _round(normal_matter, specialist)

    form = _form(_detail(signed_in, normal_matter))

    radios = re.findall(r'<input[^>]*name="provenance"[^>]*>', form)
    assert len(radios) == 2
    assert not any("checked" in radio for radio in radios)
    assert not any("disabled" in radio for radio in radios)
    assert "Saadeti Kojale" in form and "Avaldatud mujal" in form
    # One open round is not a guess at the round either: nothing is selected.
    assert not re.search(r'<option value="[0-9a-f-]{36}"[^>]*selected', form)


def test_received_feedback_may_name_nobody(signed_in, normal_matter):
    """An unidentified caller is not an invented organisation (docs/adr/0101)."""
    response = _post(
        signed_in,
        normal_matter,
        {"provenance": RECEIVED, "summary": "Helistaja, kes nime ei öelnud, toetab eelnõu."},
    )

    assert response.status_code == 200
    (position,) = MatterExternalPosition.objects.filter(matter=normal_matter)
    assert position.provenance == RECEIVED
    assert position.organisation_id is None
    assert position.source_label == ""
    assert position.source_is_member is False


def test_received_feedback_with_no_organisation_may_still_be_marked_liige(signed_in, normal_matter):
    """`Liige` is the person's own statement, never derived from a catalogue."""
    response = _post(
        signed_in,
        normal_matter,
        {
            "provenance": RECEIVED,
            "source_is_member": "on",
            "summary": "Liikmesettevõtte töötaja kõne, ettevõtet ei nimetatud.",
        },
    )

    assert response.status_code == 200
    position = MatterExternalPosition.objects.get(matter=normal_matter)
    assert position.organisation_id is None
    assert position.source_is_member is True


def test_a_round_answer_may_name_nobody(signed_in, normal_matter, specialist):
    round_ = _round(normal_matter, specialist)

    response = _post(
        signed_in,
        normal_matter,
        {"engagement": str(round_.pk), "summary": "Anonüümne vastus küsitlusele."},
    )

    assert response.status_code == 200
    position = MatterExternalPosition.objects.get(matter=normal_matter)
    assert position.organisation_id is None
    assert position.engagement_id == round_.pk
    round_.refresh_from_db()
    assert not round_.has_open_feedback_wait


def test_a_published_position_must_name_its_organisation(signed_in, normal_matter):
    response = _post(
        signed_in,
        normal_matter,
        {"provenance": DISCOVERED, "summary": "Kelle seisukoht see on?"},
    )

    assert response.status_code == 400
    assert EXTERNAL_POSITION_NEEDS_ORGANISATION in response.content.decode()
    assert not MatterExternalPosition.objects.exists()


@pytest.mark.parametrize("provenance", [RECEIVED, DISCOVERED])
@pytest.mark.parametrize("source", ["text", "url", "file", "all"])
def test_any_one_source_is_enough(signed_in, normal_matter, organisation, provenance, source):
    """Text, a link or a file — any one, any combination (docs/adr/0084 §3)."""
    data = {"organisation": str(organisation.pk), "provenance": provenance}
    files = {}
    if source in ("text", "all"):
        data["summary"] = "Kirjalik seisukoht."
    if source in ("url", "all"):
        data["url"] = "https://example.org/seisukoht"
    if source in ("file", "all"):
        files["attachments"] = _pdf()

    response = _post(signed_in, normal_matter, data, files)

    assert response.status_code == 200
    position = MatterExternalPosition.objects.get(matter=normal_matter)
    assert position.provenance == provenance
    # Nothing is invented for the boxes left empty.
    assert position.summary == data.get("summary", "")
    assert position.url == data.get("url", "")
    assert DocumentLink.objects.filter(external_position=position).count() == (1 if files else 0)


def test_an_empty_record_is_refused_and_keeps_what_was_typed(signed_in, normal_matter):
    response = _post(
        signed_in,
        normal_matter,
        {
            "provenance": RECEIVED,
            "organisation_name": "Sünteetiline Liit",
            "stated_on": "03.10.2026",
        },
    )

    body = response.content.decode()
    assert response.status_code == 400
    assert EXTERNAL_POSITION_NEEDS_SOURCE in body
    assert 'value="Sünteetiline Liit"' in body
    assert "03.10.2026" in body
    assert not MatterExternalPosition.objects.exists()


def test_the_form_explains_what_the_position_may_hold(signed_in, normal_matter):
    form = _form(_detail(signed_in, normal_matter))

    assert FEEDBACK_POSITION_HELP in form
    assert "Seisukoht" in form and "Organisatsioon" in form
    assert 'name="lawyer_note"' not in form and 'name="source_label"' not in form


# ---------------------------------------------------------------------------
# The member flag
# ---------------------------------------------------------------------------


def test_liige_saves_with_saadeti_kojale(signed_in, normal_matter, organisation):
    response = _post(
        signed_in,
        normal_matter,
        {
            "organisation": str(organisation.pk),
            "provenance": RECEIVED,
            "source_is_member": "on",
            "summary": "Liikmesettevõtte tagasiside.",
        },
    )

    assert response.status_code == 200
    assert MatterExternalPosition.objects.get(matter=normal_matter).source_is_member is True


def test_liige_is_refused_by_the_form_for_avaldatud_mujal(signed_in, normal_matter, organisation):
    response = _post(
        signed_in,
        normal_matter,
        {
            "organisation": str(organisation.pk),
            "provenance": DISCOVERED,
            "source_is_member": "on",
            "summary": "Avaldatud seisukoht.",
        },
    )

    assert response.status_code == 400
    assert EXTERNAL_POSITION_MEMBER_IS_RECEIVED_ONLY in response.content.decode()
    assert not MatterExternalPosition.objects.exists()


def test_liige_is_refused_by_the_service_for_a_discovered_position(
    normal_matter, specialist, organisation
):
    with refused(EXTERNAL_POSITION_MEMBER_IS_RECEIVED_ONLY):
        add_matter_external_position(
            matter=normal_matter,
            author=specialist,
            organisation=organisation,
            provenance=DISCOVERED,
            summary="Teenuse tasandil keelatud.",
            source_is_member=True,
        )
    assert not MatterExternalPosition.objects.exists()


def test_liige_is_drawn_in_the_member_slot_the_stylesheet_hides(signed_in, normal_matter):
    form = _form(_detail(signed_in, normal_matter))

    slot = form[form.index("data-feedback-member") :]
    assert 'name="source_is_member"' in slot[: slot.index("</div>")]


# ---------------------------------------------------------------------------
# Linked rounds
# ---------------------------------------------------------------------------


def test_a_linked_round_is_received_and_its_first_answer_finishes_the_wait(
    signed_in, normal_matter, specialist, organisation
):
    round_ = _round(normal_matter, specialist)
    assert round_.has_open_feedback_wait

    response = _post(
        signed_in,
        normal_matter,
        {
            "organisation": str(organisation.pk),
            "engagement": str(round_.pk),
            "provenance": RECEIVED,
            "summary": "Liikmed toetavad.",
        },
    )

    assert response.status_code == 200
    position = MatterExternalPosition.objects.get(matter=normal_matter)
    assert position.engagement_id == round_.pk
    assert position.provenance == RECEIVED
    round_.refresh_from_db()
    assert not round_.has_open_feedback_wait
    assert round_.feedback_closed_at is not None


def test_a_linked_round_without_a_stated_provenance_is_received(
    signed_in, normal_matter, specialist, organisation
):
    """The page disables «Avaldatud mujal» and checks «Saadeti Kojale»; a script-less
    post that names only the round is the same answer, never a guess."""
    round_ = _round(normal_matter, specialist)

    response = _post(
        signed_in,
        normal_matter,
        {
            "organisation": str(organisation.pk),
            "engagement": str(round_.pk),
            "summary": "Ringi vastus.",
        },
    )

    assert response.status_code == 200
    assert MatterExternalPosition.objects.get(matter=normal_matter).provenance == RECEIVED


def test_avaldatud_mujal_with_a_linked_round_is_refused(
    signed_in, normal_matter, specialist, organisation
):
    round_ = _round(normal_matter, specialist)

    response = _post(
        signed_in,
        normal_matter,
        {
            "organisation": str(organisation.pk),
            "engagement": str(round_.pk),
            "provenance": DISCOVERED,
            "summary": "Ei saa olla avaldatud mujal.",
        },
    )

    assert response.status_code == 400
    assert FEEDBACK_LINKED_ROUND_IS_RECEIVED in response.content.decode()
    assert not MatterExternalPosition.objects.exists()
    round_.refresh_from_db()
    assert round_.has_open_feedback_wait


def test_a_later_answer_is_kept_and_the_closure_stays(
    signed_in, normal_matter, specialist, organisation
):
    round_ = _round(normal_matter, specialist)
    complete_engagement_feedback(engagement=round_, actor=specialist)
    round_.refresh_from_db()
    closed_at = round_.feedback_closed_at

    response = _post(
        signed_in,
        normal_matter,
        {
            "organisation": str(organisation.pk),
            "engagement": str(round_.pk),
            "provenance": RECEIVED,
            "summary": "Hilinenud vastus.",
        },
    )

    assert response.status_code == 200
    round_.refresh_from_db()
    assert round_.feedback_closed_at == closed_at
    assert MatterExternalPosition.objects.filter(engagement=round_).count() == 1


def test_an_identical_repeat_to_a_round_is_refused(
    signed_in, normal_matter, specialist, organisation
):
    round_ = _round(normal_matter, specialist)
    data = {
        "organisation": str(organisation.pk),
        "engagement": str(round_.pk),
        "provenance": RECEIVED,
        "summary": "Sama vastus kaks korda.",
    }

    assert _post(signed_in, normal_matter, data).status_code == 200
    second = _post(signed_in, normal_matter, data)

    assert second.status_code == 400
    assert MatterExternalPosition.objects.filter(engagement=round_).count() == 1


def test_a_round_on_another_matter_is_refused(signed_in, normal_matter, specialist, organisation):
    foreign = _round(factories.MatterFactory(owner=specialist), specialist, "Teise teema ring")

    response = _post(
        signed_in,
        normal_matter,
        {
            "organisation": str(organisation.pk),
            "engagement": str(foreign.pk),
            "provenance": RECEIVED,
            "summary": "Vale ring.",
        },
    )

    assert response.status_code == 400
    assert not MatterExternalPosition.objects.exists()


@pytest.mark.parametrize("provenance", [RECEIVED, DISCOVERED])
def test_a_standalone_position_touches_no_wait(
    signed_in, normal_matter, specialist, organisation, provenance
):
    round_ = _round(normal_matter, specialist)

    response = _post(
        signed_in,
        normal_matter,
        {
            "organisation": str(organisation.pk),
            "provenance": provenance,
            "summary": "Ei ole ringiga seotud.",
        },
    )

    assert response.status_code == 200
    round_.refresh_from_db()
    assert round_.has_open_feedback_wait
    assert round_.feedback_closed_at is None
    assert MatterExternalPosition.objects.get(matter=normal_matter).engagement_id is None


def test_standalone_positions_may_repeat(signed_in, normal_matter, organisation):
    """Duplicate protection belongs to round answers (docs/adr/0167 §2); two
    standalone records of the same view are the person's to make."""
    data = {
        "organisation": str(organisation.pk),
        "provenance": DISCOVERED,
        "summary": "Sama avaldatud seisukoht.",
    }
    assert _post(signed_in, normal_matter, data).status_code == 200
    assert _post(signed_in, normal_matter, data).status_code == 200
    assert MatterExternalPosition.objects.filter(matter=normal_matter).count() == 2


def test_tehtud_on_a_wait_opens_the_form_with_the_round_and_received(
    signed_in, normal_matter, specialist
):
    round_ = _round(normal_matter, specialist)

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}),
        {"ava": "tagasiside", "kaasamine": str(round_.pk)},
    ).content.decode()

    form = _form(body)
    assert re.search(rf'<option value="{round_.pk}"[^>]*selected', form)
    received = re.search(r'<input[^>]*name="provenance"[^>]*value="RECEIVED"[^>]*>', form)
    discovered = re.search(r'<input[^>]*name="provenance"[^>]*value="DISCOVERED"[^>]*>', form)
    assert received and "checked" in received.group(0)
    assert discovered and "disabled" in discovered.group(0)


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provenance", [RECEIVED, DISCOVERED])
def test_files_become_the_positions_evidence(signed_in, normal_matter, organisation, provenance):
    response = _post(
        signed_in,
        normal_matter,
        {"organisation": str(organisation.pk), "provenance": provenance, "summary": "Failiga."},
        {"attachments": _pdf()},
    )

    assert response.status_code == 200
    position = MatterExternalPosition.objects.get(matter=normal_matter)
    link = DocumentLink.objects.get(external_position=position)
    assert link.document.matter_id == normal_matter.pk
    documents = signed_in.get(
        reverse("matters:matter_documents", kwargs={"pk": normal_matter.pk})
    ).content.decode()
    assert "liidu-vastus" in documents


# ---------------------------------------------------------------------------
# Refusals and visibility
# ---------------------------------------------------------------------------


def _payload(organisation) -> dict[str, str]:
    return {
        "organisation": str(organisation.pk),
        "provenance": RECEIVED,
        "summary": "Ei tohi salvestuda.",
    }


def test_a_closed_matter_writes_nothing(signed_in, specialist, organisation):
    closed = factories.MatterFactory(
        owner=specialist, is_open=False, disposition="COMPLETED", closed_at=timezone.now()
    )

    response = _post(signed_in, closed, _payload(organisation))

    assert response.status_code in (400, 409)
    assert not MatterExternalPosition.objects.exists()


def test_a_reader_writes_nothing(normal_matter, reader, organisation):
    client = Client()
    client.force_login(reader)

    response = _post(client, normal_matter, _payload(organisation))

    assert response.status_code == 404
    assert not MatterExternalPosition.objects.exists()


def test_the_gate_visitor_writes_nothing(normal_matter, organisation, settings):
    apply_shared_gate(settings)
    gate = Client()
    assert gate.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302

    response = _post(gate, normal_matter, _payload(organisation))

    assert response.status_code != 200
    assert not MatterExternalPosition.objects.exists()


def test_a_reply_to_a_restricted_round_is_restricted_and_unsearchable_for_a_reader(
    signed_in, specialist, reader, organisation
):
    matter = factories.MatterFactory(owner=specialist, visibility=Visibility.NORMAL)
    round_ = MatterEngagement.objects.create(
        matter=matter,
        kind=EngagementKind.EMAIL_CAMPAIGN,
        title="PIIRATUD-RING-5531",
        created_by=specialist,
        lifecycle_tracked=True,
        visibility_override=Visibility.RESTRICTED,
    )

    response = _post(
        signed_in,
        matter,
        {
            "organisation": str(organisation.pk),
            "engagement": str(round_.pk),
            "provenance": RECEIVED,
            "summary": "PIIRATUD-VASTUS-5531",
        },
        {"attachments": _pdf("piiratud-5531.pdf")},
    )

    assert response.status_code == 200
    position = MatterExternalPosition.objects.get(matter=matter)
    assert position.visibility_override == Visibility.RESTRICTED
    link = DocumentLink.objects.get(external_position=position)
    assert link.document.visibility_override == Visibility.RESTRICTED
    assert result_count(query="PIIRATUD-VASTUS-5531", user=reader) == 0
    assert result_count(query="PIIRATUD-VASTUS-5531", user=specialist) >= 1


# ---------------------------------------------------------------------------
# The old doors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["matters:add_received_feedback", "matters:add_external_position"])
def test_the_old_routes_are_gone(name, normal_matter):
    with pytest.raises(NoReverseMatch):
        reverse(name, kwargs={"pk": normal_matter.pk})


@pytest.mark.parametrize("path", ["lisa/tagasiside/", "lisa/valine-seisukoht/"])
def test_a_stale_tab_post_to_an_old_route_writes_nothing(
    signed_in, normal_matter, organisation, path
):
    response = signed_in.post(
        f"/teemad/{normal_matter.pk}/{path}",
        {"organisation": str(organisation.pk), "summary": "Vana vahekaart."},
    )

    assert response.status_code == 404
    assert not MatterExternalPosition.objects.exists()


def test_the_opinion_family_keeps_only_koja_arvamus(signed_in, normal_matter):
    body = _detail(signed_in, normal_matter)

    family = body[body.index('id="lisa-arvamus"') :]
    family = family[: family.index('id="lisa-marge-valik"')]
    assert 'id="arvamus-koja-valik"' in family
    assert re.search(r'<input[^>]*id="arvamus-koja-valik"[^>]*checked', family)
    for gone in (
        "arvamus-tagasiside",
        "arvamus-teiste",
        "Meile saadetud tagasiside",
        "Teiste arvamus",
    ):
        assert gone not in body, gone


def test_the_feedback_form_is_drawn_without_a_round(signed_in, normal_matter):
    form = _form(_detail(signed_in, normal_matter))

    assert "Kaasamist ei ole" not in form
    assert 'name="engagement"' not in form
    assert 'name="provenance"' in form


# ---------------------------------------------------------------------------
# Independent review of #455 — regressions
# ---------------------------------------------------------------------------


def test_the_same_words_from_another_organisation_are_another_answer(
    signed_in, normal_matter, specialist, organisation
):
    """P1-1: the repeat check compares the author, which this form now names."""
    other = factories.OrganisationFactory(name="Teine Sünteetiline Liit")
    round_ = _round(normal_matter, specialist)
    base = {"engagement": str(round_.pk), "provenance": RECEIVED, "summary": "Toetame."}

    first = _post(signed_in, normal_matter, {**base, "organisation": str(organisation.pk)})
    second = _post(signed_in, normal_matter, {**base, "organisation": str(other.pk)})
    anonymous = _post(signed_in, normal_matter, base)
    repeat = _post(signed_in, normal_matter, {**base, "organisation": str(other.pk)})

    assert (first.status_code, second.status_code, anonymous.status_code) == (200, 200, 200)
    assert repeat.status_code == 400
    assert MatterExternalPosition.objects.filter(engagement=round_).count() == 3


def test_the_liige_mark_makes_another_answer(signed_in, normal_matter, specialist):
    round_ = _round(normal_matter, specialist)
    base = {"engagement": str(round_.pk), "provenance": RECEIVED, "summary": "Ettepanekuid ei ole."}

    assert _post(signed_in, normal_matter, base).status_code == 200
    assert _post(signed_in, normal_matter, {**base, "source_is_member": "on"}).status_code == 200
    assert _post(signed_in, normal_matter, {**base, "source_is_member": "on"}).status_code == 400


def test_the_service_refuses_a_published_position_tied_to_a_round(
    normal_matter, specialist, organisation
):
    """P2-1: the rule lives in the service, not only in the creation form."""
    from app.matters.services import EXTERNAL_POSITION_ROUND_ANSWER_IS_RECEIVED

    round_ = _round(normal_matter, specialist)
    with refused(EXTERNAL_POSITION_ROUND_ANSWER_IS_RECEIVED):
        add_matter_external_position(
            matter=normal_matter,
            author=specialist,
            organisation=organisation,
            provenance=DISCOVERED,
            summary="Avaldatud seisukoht.",
            engagement=round_,
        )
    assert not MatterExternalPosition.objects.exists()


def test_a_correction_cannot_tie_a_published_position_to_a_round(
    signed_in, normal_matter, specialist, organisation
):
    from app.matters.services import (
        EXTERNAL_POSITION_ROUND_ANSWER_IS_RECEIVED,
        correct_external_position,
    )

    round_ = _round(normal_matter, specialist)
    position = add_matter_external_position(
        matter=normal_matter,
        author=specialist,
        organisation=organisation,
        provenance=DISCOVERED,
        summary="Avaldatud seisukoht.",
    ).record

    with refused(EXTERNAL_POSITION_ROUND_ANSWER_IS_RECEIVED):
        correct_external_position(
            position=position,
            organisation=organisation,
            url="",
            stated_on=None,
            stated_on_precision="EXACT",
            summary="Avaldatud seisukoht.",
            engagement=round_,
            actor=specialist,
        )

    position.refresh_from_db()
    round_.refresh_from_db()
    assert position.engagement_id is None
    assert round_.has_open_feedback_wait


def test_a_historical_link_on_a_published_position_stays_correctable(
    normal_matter, specialist, organisation
):
    """A row that already holds a round keeps it; only a new link is refused."""
    from app.matters.services import correct_external_position

    round_ = _round(normal_matter, specialist)
    position = add_matter_external_position(
        matter=normal_matter,
        author=specialist,
        organisation=organisation,
        provenance=DISCOVERED,
        summary="Vana kirje.",
    ).record
    MatterExternalPosition.objects.filter(pk=position.pk).update(engagement=round_)
    position.refresh_from_db()

    corrected = correct_external_position(
        position=position,
        organisation=organisation,
        url="",
        stated_on=None,
        stated_on_precision="EXACT",
        summary="Vana kirje, parandatud.",
        engagement=round_,
        actor=specialist,
    )

    assert corrected.engagement_id == round_.pk
    assert corrected.summary == "Vana kirje, parandatud."


def test_a_refused_round_answer_is_drawn_received_and_never_checked_and_disabled(
    signed_in, normal_matter, specialist, organisation
):
    """P2-2: a bound refusal draws no checked-and-disabled radio and no hidden Liige."""
    round_ = _round(normal_matter, specialist)

    response = _post(
        signed_in,
        normal_matter,
        {
            "organisation": str(organisation.pk),
            "engagement": str(round_.pk),
            "provenance": DISCOVERED,
            "source_is_member": "on",
            "summary": "Vale kombinatsioon.",
        },
    )

    assert response.status_code == 400
    form = _form(response.content.decode())
    received = re.search(r'<input[^>]*name="provenance"[^>]*value="RECEIVED"[^>]*>', form)
    discovered = re.search(r'<input[^>]*name="provenance"[^>]*value="DISCOVERED"[^>]*>', form)
    assert received and "checked" in received.group(0) and "disabled" not in received.group(0)
    assert discovered and "disabled" in discovered.group(0)
    assert "checked" not in discovered.group(0)


def test_a_hidden_liige_is_not_redrawn_ticked(signed_in, normal_matter):
    response = _post(
        signed_in,
        normal_matter,
        {"provenance": DISCOVERED, "source_is_member": "on", "summary": "Ilma organisatsioonita."},
    )

    assert response.status_code == 400
    member = re.search(
        r'<input[^>]*name="source_is_member"[^>]*>', _form(response.content.decode())
    )
    assert member and "checked" not in member.group(0)


def test_a_foreign_round_id_does_not_lock_the_redrawn_provenance(
    signed_in, normal_matter, specialist, organisation
):
    _round(normal_matter, specialist)
    foreign = _round(factories.MatterFactory(owner=specialist), specialist, "Võõras ring")

    response = _post(
        signed_in,
        normal_matter,
        {
            "organisation": str(organisation.pk),
            "engagement": str(foreign.pk),
            "provenance": DISCOVERED,
            "summary": "Vale ring.",
        },
    )

    assert response.status_code == 400
    discovered = re.search(
        r'<input[^>]*name="provenance"[^>]*value="DISCOVERED"[^>]*>',
        _form(response.content.decode()),
    )
    assert discovered and "disabled" not in discovered.group(0)
    assert "checked" in discovered.group(0)
