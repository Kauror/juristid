"""The four `Lisa teemale` panels docs/adr/0095 simplified, and what they kept.

One file per round, as `test_teema_redesign.py` and `test_uus_teema_*` are, because
the subject is a *decision* rather than a model: four panels changed together, each
of them narrowing an earlier ADR, and the thing worth proving is that none of the
capability those ADRs bought was destroyed on the way.

So the assertions come in pairs almost everywhere. A control is gone from the
creation panel **and** the column it wrote is still on the model, still rendered,
still corrected through `Muuda`. A question is no longer asked **and** a POST
carrying its answer reaches nothing — which is the stronger claim of the two, and
the one a stylesheet cannot fake.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from app.core.errors import DomainError
from app.matters.enums import (
    EngagementKind,
    ExternalPositionProvenance,
    WebsiteOverviewStatus,
)
from app.matters.models import MatterExternalPosition, MatterWebsiteOverview
from app.matters.services import (
    EXTERNAL_POSITION_MEMBER_IS_RECEIVED_ONLY,
    WEBSITE_OVERVIEW_NEEDS_LINK,
    add_engagement,
    record_external_position,
)
from app.matters.workspace import add_matter_external_position
from app.organisations.models import Organisation
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.workflow.enums import DatePrecision
from tests import factories

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _detail(client, matter) -> str:
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    return response.content.decode()


def _post(client, name, matter, data=None, **kwargs):
    return client.post(
        reverse("matters:" + name, kwargs={"pk": matter.pk, **kwargs}),
        data or {},
        headers={"HX-Request": "true"},
    )


def _panel(body: str, panel_id: str) -> str:
    """One launcher panel, sliced out of the page by its own id.

    Every one of these panels renders field names another panel also renders —
    `url` and `summary` are on three of them — so a search over the whole page
    answers about whichever came first. The ids are the launcher's own contract
    (`WORKSPACE_PANELS`), which is why the slice is by id rather than by guessing
    where a `<form>` ends.

    Sliced to the *next* panel rather than to the end of the document, because
    «absent from this panel» is the claim in most of this file and a slice
    running to the page's end would find the next panel's controls.
    """
    start = body.index(f'id="{panel_id}"')
    rest = body[start + 1 :]
    following = [
        rest.index(marker)
        for marker in ('id="lisa-', 'id="teema-')
        if marker in rest and rest.index(marker) > 0
    ]
    return rest[: min(following)] if following else rest


def _tag_with(body: str, needle: str) -> str:
    """The one element whose markup carries ``needle``.

    Attribute order in a rendered control is Django's widget template's, so
    asserting `value="x" checked` as a substring tests the template rather than
    the answer being held.
    """
    index = body.index(needle)
    return body[body.rindex("<", 0, index) : body.index(">", index) + 1]


def _picker_chips(body: str) -> str:
    """The picker's real chip row, past its `<noscript>` fallback.

    **The fallback renders the catalogue first**, as ordinary un-hidden chips,
    and it has to: a browser that cannot run the search has no other way to
    reach an institution, so under `quiet` the shortlist is in there too
    (`organisation_picker.html`). It is inert markup in a scripted browser and
    nothing in it reaches the ordinary UX — but it is earlier in the document,
    so a test looking for the *first* control with a given value reads the
    fallback's copy and concludes the chip is painted.

    Every assertion in this file about what a lawyer sees is about the row after
    it.
    """
    return body[body.index("</noscript>") :]


def _chip_for(body: str, value: str) -> str:
    """The `<label class="chip">` wrapping a picker control, not the control.

    `quiet` hides an unchosen institution by putting `hidden` on the **label** —
    the input inside it stays an ordinary checkbox either way, because it has to
    stay postable and stay findable by the search script. So a test asking «is
    this one painted?» has to read the label.
    """
    scoped = _picker_chips(body)
    index = scoped.index(f'value="{value}"')
    start = scoped.rindex("<label", 0, index)
    return scoped[start : scoped.index(">", index) + 1]


def _as_typed(day: dt.date) -> str:
    """One day as `EstonianDateInput` writes it into an **unbound** box.

    `j.n.Y` — no leading zeros — which is not the `dd.mm.yyyy` a person types and
    a bound form hands straight back. Both are accepted on the way in, so a test
    that assumed one shape held for both would pass on the bound path and fail on
    the initial one (`app.core.widgets`).
    """
    return f"{day.day}.{day.month}.{day.year}"


def _sent_file(name: str = "Koja_arvamus_pakendiseadus.pdf") -> SimpleUploadedFile:
    """The letter that went out, named the way a lawyer names one.

    Real PDF bytes under a `.pdf` name: `read_upload` checks the content against
    the extension and refuses a mismatch, so a fixture that lied about either
    would be testing the upload gate rather than this panel.
    """
    return SimpleUploadedFile(name, b"%PDF-1.4 arvamus", content_type="application/pdf")


@pytest.fixture
def ministry(db):
    return Organisation.objects.create(name="Kliimaministeerium")


@pytest.fixture
def committee(db):
    return Organisation.objects.create(name="Riigikogu majanduskomisjon")


@pytest.fixture
def member_company(db):
    return Organisation.objects.create(name="Metallitööstuse Liit")


@pytest.fixture
def closed_matter(normal_matter, specialist):
    from app.matters.services import close_matter
    from app.workflow.enums import Disposition

    close_matter(matter=normal_matter, disposition=Disposition.COMPLETED, actor=specialist)
    normal_matter.refresh_from_db()
    return normal_matter


# ===========================================================================
# Package A — `+ Koja arvamus`
# ===========================================================================


def test_the_panel_no_longer_explains_itself(signed_in, normal_matter):
    body = _panel(_detail(signed_in, normal_matter), "arvamus-koja")

    assert "Registreerib, et Koja arvamus on välja saadetud" not in body
    assert "muutumatu tõendina" not in body


def test_the_addressee_control_is_the_shared_searchable_picker(
    signed_in, normal_matter, ministry, committee
):
    """One line and a `+`, not the catalogue drawn out as a wall of chips.

    `quiet` keeps every institution in the document — the control has to be able
    to come back holding one after a refused save, and the search has to be able
    to reach one without a round trip — so the claim is about what is *painted*,
    which is `data-orgfind-tail` plus `hidden` (docs/adr/0088, docs/adr/0095 §1).
    """
    body = _panel(_detail(signed_in, normal_matter), "arvamus-koja")

    assert 'id="koja-adressaat-valik"' in body
    assert "Otsi või lisa asutus…" in body
    assert "Lisa uus adressaat" in body
    # Not a chip anybody has to scroll past: an unchosen institution is in the
    # document and hidden until it is searched out.
    assert "hidden" in _chip_for(body, str(committee.pk))


def test_one_matter_sender_is_preselected(signed_in, normal_matter, ministry):
    normal_matter.source_organisations.set([ministry])

    body = _panel(_detail(signed_in, normal_matter), "arvamus-koja")

    assert "checked" in _chip_for(body, str(ministry.pk))


def test_every_matter_sender_is_preselected(
    signed_in, normal_matter, ministry, committee, member_company
):
    normal_matter.source_organisations.set([ministry, committee, member_company])

    body = _panel(_detail(signed_in, normal_matter), "arvamus-koja")

    for organisation in (ministry, committee, member_company):
        assert "checked" in _chip_for(body, str(organisation.pk))


def test_a_matter_with_no_sender_opens_with_no_recipient(
    signed_in, normal_matter, ministry, committee
):
    assert not normal_matter.source_organisations.exists()

    body = _panel(_detail(signed_in, normal_matter), "arvamus-koja")

    for organisation in (ministry, committee):
        assert "checked" not in _chip_for(body, str(organisation.pk))


def test_the_suggested_recipient_can_be_removed_and_another_chosen(
    signed_in, normal_matter, ministry, committee
):
    """The default is a suggestion: what the browser posts is what is stored."""
    normal_matter.source_organisations.set([ministry])

    response = _post(
        signed_in,
        "add_koda_opinion",
        normal_matter,
        {"upload": _sent_file(), "sent_on": "14.03.2026", "recipients": [str(committee.pk)]},
    )

    assert response.status_code == 200
    submission = Submission.objects.get(matter=normal_matter)
    assert [row.organisation for row in submission.recipient_rows.all()] == [committee]


def test_several_recipients_are_stored(signed_in, normal_matter, ministry, committee):
    response = _post(
        signed_in,
        "add_koda_opinion",
        normal_matter,
        {
            "upload": _sent_file(),
            "sent_on": "14.03.2026",
            "recipients": [str(ministry.pk), str(committee.pk)],
        },
    )

    assert response.status_code == 200
    submission = Submission.objects.get(matter=normal_matter)
    stored = {row.organisation for row in submission.recipient_rows.all()}
    assert stored == {ministry, committee}


def test_a_recipient_named_through_the_picker_is_resolved_and_stored(
    signed_in, normal_matter, ministry
):
    """`+` names a body the catalogue does not hold, and the save creates it once."""
    response = _post(
        signed_in,
        "add_koda_opinion",
        normal_matter,
        {
            "upload": _sent_file(),
            "sent_on": "14.03.2026",
            "recipients": [str(ministry.pk)],
            "recipient_name": "Rahandusministeerium",
        },
    )

    assert response.status_code == 200
    submission = Submission.objects.get(matter=normal_matter)
    stored = {row.organisation.name for row in submission.recipient_rows.all()}
    assert stored == {"Kliimaministeerium", "Rahandusministeerium"}
    assert Organisation.objects.filter(name="Rahandusministeerium").count() == 1


def test_a_typed_name_alone_satisfies_the_recipient_requirement(signed_in, normal_matter):
    response = _post(
        signed_in,
        "add_koda_opinion",
        normal_matter,
        {"upload": _sent_file(), "sent_on": "14.03.2026", "recipient_name": "Rahandusministeerium"},
    )

    assert response.status_code == 200
    submission = Submission.objects.get(matter=normal_matter)
    assert [row.organisation.name for row in submission.recipient_rows.all()] == [
        "Rahandusministeerium"
    ]


def test_at_least_one_addressee_is_still_required(signed_in, normal_matter):
    response = _post(
        signed_in,
        "add_koda_opinion",
        normal_matter,
        {"upload": _sent_file(), "sent_on": "14.03.2026"},
    )

    assert response.status_code == 400
    assert "Vali vähemalt üks adressaat." in response.content.decode()
    assert not Submission.objects.filter(matter=normal_matter).exists()


def test_a_refused_save_keeps_the_recipients_that_were_posted(
    signed_in, normal_matter, ministry, committee
):
    """And does **not** put back the one somebody removed.

    The whole reason the default is unbound-only. A refusal re-renders what the
    browser sent; reapplying `Saatja` here would take a recipient they had just
    deleted and tick it again under their cursor (docs/adr/0095 §1).
    """
    normal_matter.source_organisations.set([ministry])

    response = _post(
        signed_in,
        "add_koda_opinion",
        normal_matter,
        # No file — a refusal, with the recipient choice already made.
        {"sent_on": "14.03.2026", "recipients": [str(committee.pk)]},
    )
    body = _panel(response.content.decode(), "arvamus-koja")

    assert response.status_code == 400
    assert "checked" in _chip_for(body, str(committee.pk))
    assert "checked" not in _chip_for(body, str(ministry.pk))


def test_recording_an_opinion_does_not_touch_the_matters_senders(
    signed_in, normal_matter, ministry, committee
):
    """`Saatja` and `Adressaat` are two facts, and this save writes one of them."""
    normal_matter.source_organisations.set([ministry])

    response = _post(
        signed_in,
        "add_koda_opinion",
        normal_matter,
        {
            "upload": _sent_file(),
            "sent_on": "14.03.2026",
            "recipients": [str(committee.pk)],
            "recipient_name": "Rahandusministeerium",
        },
    )

    assert response.status_code == 200
    normal_matter.refresh_from_db()
    assert list(normal_matter.source_organisations.all()) == [ministry]


def test_the_panel_asks_for_a_summary_rather_than_a_title(signed_in, normal_matter):
    body = _panel(_detail(signed_in, normal_matter), "arvamus-koja")

    assert 'name="title"' not in body
    assert 'name="summary"' in body
    assert "Kokkuvõte" in body
    # A box with room in it, not a one-line input.
    assert "<textarea" in _tag_with(body, 'name="summary"')


def test_the_summary_is_stored_and_the_title_is_the_filename(signed_in, normal_matter, ministry):
    """The substance and the identity are two columns, and neither is the other.

    The identity is a fact somebody chose — the name they gave the file — rather
    than a headline cut out of the summary's first characters (docs/adr/0095 §2).
    """
    written = "Toetame eelnõu, kuid palume kaheaastast üleminekuaega."

    response = _post(
        signed_in,
        "add_koda_opinion",
        normal_matter,
        {
            "upload": _sent_file("Koja_arvamus_pakendiseadus.pdf"),
            "sent_on": "14.03.2026",
            "recipients": [str(ministry.pk)],
            "summary": written,
        },
    )

    assert response.status_code == 200
    submission = Submission.objects.get(matter=normal_matter)
    assert submission.summary == written
    assert submission.title == "Koja_arvamus_pakendiseadus.pdf"
    assert not submission.title.startswith(written[:10])


def test_the_summary_renders_where_the_opinion_is_read(signed_in, normal_matter, ministry):
    written = "Toetame eelnõu, kuid palume kaheaastast üleminekuaega."

    _post(
        signed_in,
        "add_koda_opinion",
        normal_matter,
        {
            "upload": _sent_file(),
            "sent_on": "14.03.2026",
            "recipients": [str(ministry.pk)],
            "summary": written,
        },
    )
    body = _detail(signed_in, normal_matter)

    assert written in body


def test_the_outbound_register_prints_the_summary_under_the_identity(
    signed_in, normal_matter, ministry
):
    """The cross-Matter register is scanned to answer «which letter was that».

    Since the title on this path is the sent file's own name, a row printing
    only `Koja_arvamus_pakendiseadus.pdf` would be one a reader has to open
    something to understand — which is the one thing docs/adr/0095 §2 must not
    cost. The summary goes *under* the identity rather than in place of it: the
    title is what the register, the document and the evidence all agree this
    record is called.
    """
    written = "Toetame eelnõu, kuid palume kaheaastast üleminekuaega."

    _post(
        signed_in,
        "add_koda_opinion",
        normal_matter,
        {
            "upload": _sent_file(),
            "sent_on": "14.03.2026",
            "recipients": [str(ministry.pk)],
            "summary": written,
        },
    )
    body = signed_in.get(reverse("submissions:sent")).content.decode()

    assert "Koja_arvamus_pakendiseadus.pdf" in body
    assert written in body


def test_the_outbound_register_adds_nothing_to_an_opinion_with_no_summary(
    signed_in, normal_matter, ministry
):
    """Blank is ordinary, and an empty column prints no empty line."""
    _post(
        signed_in,
        "add_koda_opinion",
        normal_matter,
        {"upload": _sent_file(), "sent_on": "14.03.2026", "recipients": [str(ministry.pk)]},
    )
    body = signed_in.get(reverse("submissions:sent")).content.decode()

    assert "Koja_arvamus_pakendiseadus.pdf" in body
    assert 'class="table__sub"' not in body


def test_an_opinion_with_no_summary_reads_exactly_as_it_did(signed_in, normal_matter, ministry):
    """Blank is ordinary, and an empty column adds no punctuation to the row."""
    _post(
        signed_in,
        "add_koda_opinion",
        normal_matter,
        {"upload": _sent_file(), "sent_on": "14.03.2026", "recipients": [str(ministry.pk)]},
    )
    body = _detail(signed_in, normal_matter)

    submission = Submission.objects.get(matter=normal_matter)
    assert submission.summary == ""
    assert "Kliimaministeerium" in body


def test_the_send_invariants_are_unchanged(signed_in, normal_matter, ministry):
    """Evidence, status and the supplied day, none of which this round touched."""
    response = _post(
        signed_in,
        "add_koda_opinion",
        normal_matter,
        {
            "upload": _sent_file(),
            "sent_on": "14.03.2026",
            "recipients": [str(ministry.pk)],
            "summary": "Toetame.",
        },
    )

    assert response.status_code == 200
    submission = Submission.objects.get(matter=normal_matter)
    assert submission.status == SubmissionStatus.SENT
    assert submission.final_version is not None
    # Midnight in Europe/Tallinn, which is 22:00 the previous day in UTC — the
    # anchor `add_matter_koda_opinion` stores at `SentAtPrecision.DATE`.
    assert timezone.localtime(submission.sent_at).date() == dt.date(2026, 3, 14)


def test_the_sending_date_is_still_required_and_still_never_in_the_future(
    signed_in, normal_matter, ministry
):
    tomorrow = timezone.localdate() + dt.timedelta(days=1)

    missing = _post(
        signed_in,
        "add_koda_opinion",
        normal_matter,
        {"upload": _sent_file(), "recipients": [str(ministry.pk)]},
    )
    future = _post(
        signed_in,
        "add_koda_opinion",
        normal_matter,
        {
            "upload": _sent_file(),
            "sent_on": _as_typed(tomorrow),
            "recipients": [str(ministry.pk)],
        },
    )

    assert missing.status_code == 400
    assert "Märgi, mil kuupäeval arvamus välja saadeti." in missing.content.decode()
    assert future.status_code == 400
    assert "ei saa olla tulevikus" in future.content.decode()
    assert not Submission.objects.filter(matter=normal_matter).exists()


# ===========================================================================
# Packages B and C — the two feedback panels
# ===========================================================================


@pytest.mark.parametrize(
    "panel_id",
    ["arvamus-teiste", "arvamus-tagasiside"],
)
def test_neither_feedback_panel_draws_the_catalogue(signed_in, normal_matter, committee, panel_id):
    body = _panel(_detail(signed_in, normal_matter), panel_id)

    assert "Otsi või lisa asutus…" in body
    assert "Lisa uus organisatsioon" in body
    assert "hidden" in _chip_for(body, str(committee.pk))


@pytest.mark.parametrize("panel_id", ["arvamus-teiste", "arvamus-tagasiside"])
def test_neither_feedback_panel_asks_a_precision_or_a_note_or_a_round(
    signed_in, normal_matter, panel_id
):
    body = _panel(_detail(signed_in, normal_matter), panel_id)

    assert "Täpne päev" not in body
    assert "Kvartal" not in body
    assert 'name="position_precision"' not in body
    assert 'name="lawyer_note"' not in body
    assert 'name="engagement"' not in body
    assert "Juristi märkus" not in body
    assert "Seotud kaasamine" not in body


@pytest.mark.parametrize("panel_id", ["arvamus-teiste", "arvamus-tagasiside"])
def test_both_feedback_panels_open_on_today(signed_in, normal_matter, panel_id):
    body = _panel(_detail(signed_in, normal_matter), panel_id)

    assert f'value="{_as_typed(timezone.localdate())}"' in _tag_with(body, 'name="stated_on"')


@pytest.mark.parametrize(
    ("route", "panel_id"),
    [
        ("add_external_position", "arvamus-teiste"),
        ("add_received_feedback", "arvamus-tagasiside"),
    ],
)
def test_a_record_made_here_stores_exact_and_nothing_else(
    signed_in, normal_matter, ministry, route, panel_id
):
    response = _post(
        signed_in,
        route,
        normal_matter,
        {
            "organisation": str(ministry.pk),
            "stated_on": "14.03.2026",
            "summary": "Toetab eelnõu.",
        },
    )

    assert response.status_code == 200
    position = MatterExternalPosition.objects.get(matter=normal_matter)
    assert position.stated_on == dt.date(2026, 3, 14)
    assert position.stated_on_precision == DatePrecision.EXACT.value
    assert position.lawyer_note == ""
    assert position.engagement_id is None
    assert position.source_label == ""


@pytest.mark.parametrize("route", ["add_external_position", "add_received_feedback"])
def test_a_crafted_post_cannot_smuggle_the_retired_answers_in(
    signed_in, normal_matter, ministry, route
):
    """Every removed control is a field the form does not declare.

    So this is not a view choosing to ignore four parameters: they arrive at a
    form that never cleans them, and what reaches the service is the empty
    answer. A POST is what a boundary meets, which is why the assertion is on
    the stored row rather than on the rendered page (docs/adr/0095 §5).
    """
    engagement = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.EMAIL_CAMPAIGN,
        title="liikmed",
        occurred_on=dt.date(2026, 2, 1),
        actor=None,
    )

    response = _post(
        signed_in,
        route,
        normal_matter,
        {
            "organisation": str(ministry.pk),
            "summary": "Toetab eelnõu.",
            "stated_on": "14.03.2026",
            "lawyer_note": "Meie hinnangul on see nõrk argument.",
            "engagement": str(engagement.pk),
            "source_label": "234 ettevõtet",
            "position_precision": DatePrecision.QUARTER.value,
            "position_quarter": "2026-1",
        },
    )

    assert response.status_code == 200
    position = MatterExternalPosition.objects.get(matter=normal_matter)
    assert position.lawyer_note == ""
    assert position.engagement_id is None
    assert position.source_label == ""
    assert position.stated_on_precision == DatePrecision.EXACT.value
    assert position.stated_on == dt.date(2026, 3, 14)


@pytest.mark.parametrize("route", ["add_external_position", "add_received_feedback"])
def test_a_link_and_a_file_are_still_captured(signed_in, normal_matter, ministry, route):
    response = _post(
        signed_in,
        route,
        normal_matter,
        {
            "organisation": str(ministry.pk),
            "url": "https://kliimaministeerium.ee/arvamus",
            "stated_on": "14.03.2026",
            "attachments": [SimpleUploadedFile("seisukoht.pdf", b"%PDF-1.4 x")],
        },
    )

    assert response.status_code == 200
    position = MatterExternalPosition.objects.get(matter=normal_matter)
    assert position.url == "https://kliimaministeerium.ee/arvamus"
    assert position.document_links.count() == 1


@pytest.mark.parametrize("route", ["add_external_position", "add_received_feedback"])
def test_the_source_minimum_still_refuses_an_empty_record(
    signed_in, normal_matter, ministry, route
):
    response = _post(
        signed_in,
        route,
        normal_matter,
        {"organisation": str(ministry.pk), "stated_on": "14.03.2026"},
    )

    assert response.status_code == 400
    assert not MatterExternalPosition.objects.filter(matter=normal_matter).exists()


def test_the_helper_sentence_about_the_three_sources_is_gone(signed_in, normal_matter):
    body = _detail(signed_in, normal_matter)

    assert "vähemalt üks neist on vajalik" not in body


# --- `Allikas`, and what replaced it ---------------------------------------


def test_received_feedback_no_longer_offers_allikas(signed_in, normal_matter):
    body = _panel(_detail(signed_in, normal_matter), "arvamus-tagasiside")

    assert 'name="source_label"' not in body
    assert "Allikas" not in body
    assert "küsitluse" not in body
    assert "jäta organisatsioon valimata" not in body


def test_received_feedback_may_name_nobody(signed_in, normal_matter):
    """**Reversed by docs/adr/0101.**

    This round removed the `Allikas` box and recorded that the authorship rule
    was therefore *met* rather than relaxed: with no label to offer, every
    received position named an organisation. The owner met the other half of
    that in ordinary use — a lawyer writing down what a member said on the
    telephone has neither a catalogue row nor a collection to name, and a panel
    that will not save until one of them exists is what makes people invent one
    (OWNER-01).

    So the record may name nobody, and the absence is the truth rather than a
    gap to fill. What is unchanged is that the record can never be *empty*:
    `EXTERNAL_POSITION_NEEDS_SOURCE` still means a position, a link or a file
    is there, and a `DISCOVERED` opinion still requires its author.
    """
    response = _post(
        signed_in,
        "add_received_feedback",
        normal_matter,
        {"summary": "Toetame eelnõu.", "stated_on": "14.03.2026"},
    )

    assert response.status_code == 200
    position = MatterExternalPosition.objects.get(matter=normal_matter)
    assert position.organisation_id is None
    assert position.source_label == ""
    assert position.summary == "Toetame eelnõu."


def test_a_historical_row_with_a_source_label_still_reads_and_is_still_correctable(
    signed_in, normal_matter, specialist
):
    """The aggregate answers docs/adr/0091 §3.3 built the box for are untouched."""
    position = record_external_position(
        matter=normal_matter,
        organisation=None,
        provenance=ExternalPositionProvenance.RECEIVED.value,
        source_label="234 ettevõtte küsitlus",
        summary="58 vastust, valdavalt toetavad.",
        stated_on=dt.date(2019, 5, 1),
        stated_on_precision=DatePrecision.MONTH.value,
        lawyer_note="Vastuste kvaliteet oli ebaühtlane.",
        actor=specialist,
    )

    body = _detail(signed_in, normal_matter)
    edit = signed_in.get(
        reverse(
            "matters:update_external_position",
            kwargs={"pk": normal_matter.pk, "position_id": position.pk},
        ),
        headers={"HX-Request": "true"},
    )
    edit_body = edit.content.decode()

    assert "234 ettevõtte küsitlus" in body
    assert "Vastuste kvaliteet oli ebaühtlane." in body
    assert "mai 2019" in body
    # `Muuda` still asks every question the creation panel stopped asking.
    assert 'name="source_label"' in edit_body
    assert 'name="lawyer_note"' in edit_body
    assert 'name="engagement"' in edit_body
    assert 'name="position_precision"' in edit_body


def test_a_historical_lawyer_note_is_never_merged_into_the_position(
    signed_in, normal_matter, specialist, ministry
):
    position = record_external_position(
        matter=normal_matter,
        organisation=ministry,
        provenance=ExternalPositionProvenance.RECEIVED.value,
        summary="Toetab eelnõu.",
        lawyer_note="Meie hinnangul on see nõrk argument.",
        actor=specialist,
    )
    position.refresh_from_db()

    assert position.summary == "Toetab eelnõu."
    assert position.lawyer_note == "Meie hinnangul on see nõrk argument."
    assert "nõrk argument" not in position.summary


def test_a_historical_engagement_link_survives(signed_in, normal_matter, specialist, ministry):
    engagement = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.EMAIL_CAMPAIGN,
        title="liikmete küsitlus",
        occurred_on=dt.date(2026, 2, 1),
        actor=specialist,
    )
    position = record_external_position(
        matter=normal_matter,
        organisation=ministry,
        provenance=ExternalPositionProvenance.RECEIVED.value,
        summary="Toetab eelnõu.",
        engagement=engagement,
        actor=specialist,
    )
    position.refresh_from_db()

    assert position.engagement_id == engagement.pk
    assert "liikmete küsitlus" in _detail(signed_in, normal_matter)


# --- `Liige` ---------------------------------------------------------------


def test_the_member_mark_is_offered_on_received_feedback_alone(signed_in, normal_matter):
    body = _detail(signed_in, normal_matter)

    assert 'name="source_is_member"' in _panel(body, "arvamus-tagasiside")
    assert 'name="source_is_member"' not in _panel(body, "arvamus-teiste")
    assert "Liige" in _panel(body, "arvamus-tagasiside")


def test_an_unticked_box_stores_false(signed_in, normal_matter, member_company):
    response = _post(
        signed_in,
        "add_received_feedback",
        normal_matter,
        {
            "organisation": str(member_company.pk),
            "summary": "Toetame eelnõu.",
            "stated_on": "14.03.2026",
        },
    )

    assert response.status_code == 200
    assert MatterExternalPosition.objects.get(matter=normal_matter).source_is_member is False


def test_a_ticked_box_stores_true(signed_in, normal_matter, member_company):
    response = _post(
        signed_in,
        "add_received_feedback",
        normal_matter,
        {
            "organisation": str(member_company.pk),
            "summary": "Toetame eelnõu.",
            "stated_on": "14.03.2026",
            "source_is_member": "on",
        },
    )

    assert response.status_code == 200
    assert MatterExternalPosition.objects.get(matter=normal_matter).source_is_member is True


def test_membership_is_never_inferred_from_anything(signed_in, normal_matter, member_company):
    """Two records, one organisation, two different answers — and the box decides.

    If anything derived this from the organisation, from the name, from a
    registry or from the other feedback on the file, these two rows could not
    disagree (docs/adr/0095 §4).
    """
    _post(
        signed_in,
        "add_received_feedback",
        normal_matter,
        {
            "organisation": str(member_company.pk),
            "summary": "Esimene vastus.",
            "stated_on": "14.03.2026",
            "source_is_member": "on",
        },
    )
    _post(
        signed_in,
        "add_received_feedback",
        normal_matter,
        {
            "organisation": str(member_company.pk),
            "summary": "Teine vastus.",
            "stated_on": "15.03.2026",
        },
    )

    marks = {
        row.summary: row.source_is_member
        for row in MatterExternalPosition.objects.filter(matter=normal_matter)
    }
    assert marks == {"Esimene vastus.": True, "Teine vastus.": False}


def test_a_crafted_member_mark_on_a_discovered_position_is_refused(
    signed_in, normal_matter, ministry
):
    """The panel does not draw it; the service refuses it; the database refuses it."""
    response = _post(
        signed_in,
        "add_external_position",
        normal_matter,
        {
            "organisation": str(ministry.pk),
            "summary": "Toetab eelnõu.",
            "stated_on": "14.03.2026",
            "source_is_member": "on",
        },
    )

    # The field is not on that form at all, so the value reaches nothing and the
    # ordinary save stands — with the mark off.
    assert response.status_code == 200
    assert MatterExternalPosition.objects.get(matter=normal_matter).source_is_member is False


def test_the_service_refuses_a_member_mark_on_a_discovered_position(
    normal_matter, specialist, ministry
):
    with pytest.raises(DomainError) as refusal:
        record_external_position(
            matter=normal_matter,
            organisation=ministry,
            provenance=ExternalPositionProvenance.DISCOVERED.value,
            summary="Toetab eelnõu.",
            source_is_member=True,
            actor=specialist,
        )

    assert str(refusal.value) == EXTERNAL_POSITION_MEMBER_IS_RECEIVED_ONLY
    assert not MatterExternalPosition.objects.filter(matter=normal_matter).exists()


def test_the_database_refuses_a_member_mark_on_a_discovered_position(
    normal_matter, specialist, ministry
):
    position = record_external_position(
        matter=normal_matter,
        organisation=ministry,
        provenance=ExternalPositionProvenance.DISCOVERED.value,
        summary="Toetab eelnõu.",
        actor=specialist,
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        MatterExternalPosition.objects.filter(pk=position.pk).update(source_is_member=True)


def test_a_correction_keeps_the_member_mark_it_was_not_asked_about(
    normal_matter, specialist, member_company
):
    """`Muuda` does not render `Liige`, so a correction must not clear one.

    The mark is absent from the correction service's `proposed` set entirely,
    which is what makes «not asked» mean «not moved» rather than «set to the
    default» — the failure a field added to a form but forgotten in a
    correction's field list always has.
    """
    from app.matters.services import correct_external_position

    position = record_external_position(
        matter=normal_matter,
        organisation=member_company,
        provenance=ExternalPositionProvenance.RECEIVED.value,
        summary="Toetame eelnõu.",
        source_is_member=True,
        actor=specialist,
    )

    corrected = correct_external_position(
        position=position,
        organisation=member_company,
        url="",
        stated_on=dt.date(2026, 3, 14),
        stated_on_precision=DatePrecision.EXACT.value,
        summary="Toetame eelnõu, kuid palume üleminekuaega.",
        lawyer_note="",
        source_label="",
        provenance=None,
        engagement=None,
        actor=specialist,
    )

    assert corrected.source_is_member is True
    assert corrected.summary == "Toetame eelnõu, kuid palume üleminekuaega."


def test_a_correction_moving_provenance_off_received_refuses_a_marked_row(
    normal_matter, specialist, member_company
):
    """An Estonian sentence on the way in, not an `IntegrityError` under two locks.

    Unreachable from `Muuda`, which passes `provenance=None` and never moves it.
    This is the import and correction path that can — and the reason the rule is
    stated in `_external_position_authorship` as well as in the database
    (docs/adr/0095 §4).
    """
    from app.matters.services import correct_external_position

    position = record_external_position(
        matter=normal_matter,
        organisation=member_company,
        provenance=ExternalPositionProvenance.RECEIVED.value,
        summary="Toetame eelnõu.",
        source_is_member=True,
        actor=specialist,
    )

    with pytest.raises(DomainError) as refusal:
        correct_external_position(
            position=position,
            organisation=member_company,
            url="",
            stated_on=None,
            stated_on_precision=DatePrecision.EXACT.value,
            summary="Toetame eelnõu.",
            lawyer_note="",
            source_label="",
            provenance=ExternalPositionProvenance.DISCOVERED.value,
            engagement=None,
            actor=specialist,
        )

    assert str(refusal.value) == EXTERNAL_POSITION_MEMBER_IS_RECEIVED_ONLY
    position.refresh_from_db()
    assert position.provenance == ExternalPositionProvenance.RECEIVED.value
    assert position.source_is_member is True


def test_existing_rows_are_not_backfilled(normal_matter, specialist, member_company):
    """The migration's default is `False`, and `False` is «nobody said»."""
    position = add_matter_external_position(
        matter=normal_matter,
        author=specialist,
        organisation=member_company,
        provenance=ExternalPositionProvenance.RECEIVED.value,
        summary="Toetame eelnõu.",
    ).record

    assert position.source_is_member is False


# ===========================================================================
# Package D — `+ Ülevaade / uudis`
# ===========================================================================


def test_the_overview_panel_asks_a_day_and_a_link_and_explains_neither(signed_in, normal_matter):
    body = _panel(_detail(signed_in, normal_matter), "lisa-koduleht")

    assert 'name="published_on"' in body
    assert 'name="url"' in body
    assert ">Link<" in body
    assert "Märgib, et sellest teemast peaks tulema" not in body
    assert "Kui ülevaade või uudis on juba avaldatud" not in body
    assert "Kui kuupäev ei ole teada, jäta tühjaks." not in body
    assert "peab olema avalik http:// või https:// aadress" not in body
    assert "salvestatakse kohe avaldatuna" not in body


def test_the_overview_date_opens_on_today(signed_in, normal_matter):
    body = _panel(_detail(signed_in, normal_matter), "lisa-koduleht")

    assert f'value="{_as_typed(timezone.localdate())}"' in _tag_with(body, 'name="published_on"')


def test_a_link_and_the_offered_day_record_a_publication(signed_in, normal_matter):
    today = timezone.localdate()

    response = _post(
        signed_in,
        "add_website_overview",
        normal_matter,
        {"url": "https://koda.ee/uudis", "published_on": _as_typed(today)},
    )

    assert response.status_code == 200
    overview = MatterWebsiteOverview.objects.get(matter=normal_matter)
    assert overview.status == WebsiteOverviewStatus.PUBLISHED
    assert overview.url == "https://koda.ee/uudis"
    assert overview.published_on == today


def test_a_chosen_day_is_what_is_stored(signed_in, normal_matter):
    response = _post(
        signed_in,
        "add_website_overview",
        normal_matter,
        {"url": "https://koda.ee/uudis", "published_on": "14.03.2026"},
    )

    assert response.status_code == 200
    assert MatterWebsiteOverview.objects.get(matter=normal_matter).published_on == dt.date(
        2026, 3, 14
    )


def test_a_blank_link_no_longer_files_a_plan(signed_in, normal_matter):
    response = _post(
        signed_in,
        "add_website_overview",
        normal_matter,
        {"published_on": _as_typed(timezone.localdate())},
    )

    assert response.status_code == 400
    assert WEBSITE_OVERVIEW_NEEDS_LINK in response.content.decode()
    assert not MatterWebsiteOverview.objects.filter(matter=normal_matter).exists()


def test_stored_plans_and_cancellations_are_untouched(signed_in, normal_matter, specialist):
    """The domain kept them; only the silent way in from this panel is gone."""
    from app.matters.services import cancel_website_overview, plan_website_overview

    planned = plan_website_overview(matter=normal_matter, actor=specialist)
    cancelled = plan_website_overview(matter=normal_matter, actor=specialist)
    cancel_website_overview(overview=cancelled, actor=specialist)

    body = _detail(signed_in, normal_matter)
    planned.refresh_from_db()
    cancelled.refresh_from_db()

    assert planned.status == WebsiteOverviewStatus.PLANNED
    assert planned.published_on is None
    assert cancelled.status == WebsiteOverviewStatus.CANCELLED
    assert 'id="kodulehe-ulevaated"' in body


def test_a_stored_publication_with_an_unknown_day_keeps_it_unknown(normal_matter, specialist):
    from app.matters.services import plan_website_overview, publish_website_overview

    overview = publish_website_overview(
        overview=plan_website_overview(matter=normal_matter, actor=specialist),
        url="https://koda.ee/vana",
        published_on=None,
        actor=specialist,
    )
    overview.refresh_from_db()

    assert overview.status == WebsiteOverviewStatus.PUBLISHED
    assert overview.published_on is None


# ===========================================================================
# Cross-cutting
# ===========================================================================


def test_a_closed_matter_refuses_every_one_of_the_four_panels(signed_in, closed_matter, ministry):
    refusals = [
        _post(
            signed_in,
            "add_koda_opinion",
            closed_matter,
            {"upload": _sent_file(), "sent_on": "14.03.2026", "recipients": [str(ministry.pk)]},
        ),
        _post(
            signed_in,
            "add_external_position",
            closed_matter,
            {"organisation": str(ministry.pk), "summary": "Toetab."},
        ),
        _post(
            signed_in,
            "add_received_feedback",
            closed_matter,
            {"organisation": str(ministry.pk), "summary": "Toetame."},
        ),
        _post(
            signed_in,
            "add_website_overview",
            closed_matter,
            {"url": "https://koda.ee/uudis", "published_on": "14.03.2026"},
        ),
    ]

    assert [response.status_code for response in refusals] == [400, 400, 400, 400]
    assert not Submission.objects.filter(matter=closed_matter).exists()
    assert not MatterExternalPosition.objects.filter(matter=closed_matter).exists()
    assert not MatterWebsiteOverview.objects.filter(matter=closed_matter).exists()


def test_an_opinion_summary_is_findable_in_search(normal_matter, specialist, ministry):
    """The contract `INDEX_VERSION` moved for.

    The descriptive text a lawyer writes about a sent opinion used to go into
    `title`, which the projection indexes in the identity tier. It goes into
    `summary` now, so the projection reads that too — otherwise this round would
    have quietly retired a search that works today (docs/adr/0095 §2).
    """
    from app.search.indexing import rebuild_all
    from app.search.models import SearchSourceKind
    from app.search.services import search

    submission = factories.SubmissionFactory(
        matter=normal_matter,
        title="Koja_arvamus_pakendiseadus.pdf",
        summary="Palume pikemat üleminekuaega pakendiaktsiisile.",
    )
    submission.recipient_rows.create(organisation=ministry)
    rebuild_all()

    hits = search(query="üleminekuaega", user=specialist)

    assert any(row.source_kind == SearchSourceKind.SUBMISSION for row in hits)
