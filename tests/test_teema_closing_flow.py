"""The approved `Lõpeta teema` redesign, rule by rule.

The closing section used to ask nine questions, four of which the composer had
already answered. `Tulemus` asked for the closing narrative immediately below
the box that had just taken it; `Lõpparvamuse pealkiri` asked for the name of
the file that was open; `Mida Koda saavutas?` and `Töövõidu selgitus` asked for
the narrative twice more. `Lõpparvamus` offered a picker over versions already
uploaded, which is not where the sent PDF is at the moment somebody closes a
file. `Saaja` was a fixed checkbox list, so an opinion going to seven political
parties could not be recorded at all without creating seven institutions
somewhere else first.

What is asserted here is the shape of the replacement — six questions, one
narrative, one save — and that none of the canonical rules underneath it moved:
a SENT `Submission` still needs its exact final evidence, a work victory still
goes through the manual door that already existed, and a commencement date is
still a `MatterEffectiveDate` rather than a column borrowed from the victory's
reporting period.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.core.errors import DomainError
from app.documents.enums import DocumentRole
from app.documents.models import Document
from app.intelligence.enums import EffectiveDateKind, FactStatus, WorkVictoryStatus
from app.intelligence.models import MatterEffectiveDate, MatterWorkVictory
from app.intelligence.services import add_effective_date
from app.matters.forms import CLOSURE_CHOICES, ComposerForm
from app.matters.models import Entry
from app.matters.services import compose_update
from app.organisations.models import AliasType, Organisation, OrganisationAlias, OrganisationType
from app.submissions.enums import RecipientRole, SentAtPrecision, SubmissionStatus
from app.submissions.models import Submission, SubmissionRecipient
from app.workflow.enums import DatePrecision, Disposition
from tests import factories

pytestmark = pytest.mark.django_db


SEVEN_PARTIES = [
    "Näidiserakond Alpha",
    "Näidiserakond Beeta",
    "Näidiserakond Gamma",
    "Näidiserakond Delta",
    "Näidiserakond Epsilon",
    "Näidiserakond Zeeta",
    "Näidiserakond Eeta",
]


def _pdf(name: str = "Koja_arvamus.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"%PDF-1.4 test", content_type="application/pdf")


def _composer_html(client, matter) -> str:
    url = reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    return client.get(url).content.decode()


def _post(client, matter, **fields):
    """One composer save, with the fields every POST carries filled in."""
    payload = {
        "body": "",
        "kind": "NOTE",
        "attachment_role": DocumentRole.OTHER,
        "next_kind": "",
        "next_date": "",
        "next_precision": DatePrecision.EXACT,
        "deadline_title": "",
        "deadline_date": "",
        "deadline_precision": DatePrecision.EXACT,
    }
    payload.update(fields)
    return client.post(
        reverse("matters:compose", kwargs={"pk": matter.pk}),
        payload,
        headers={"HX-Request": "true"},
    )


# ---------------------------------------------------------------------------
# §15, §19 — what the closing form asks, and what it no longer asks
# ---------------------------------------------------------------------------


REMOVED_FIELDS = [
    "closure_reason",
    "successor",
    "final_version",
    "final_title",
    "final_channel",
    "final_reference",
    "victory_title",
    "victory_detail",
]


@pytest.mark.parametrize("name", REMOVED_FIELDS)
def test_the_closing_form_no_longer_declares_a_retired_field(name):
    """A field removed from the template but left on the form still validates,
    still binds, and comes back the moment somebody re-renders it. The form is
    where the removal has to be true."""
    assert name not in ComposerForm().fields


@pytest.mark.parametrize("name", REMOVED_FIELDS)
def test_the_closing_section_no_longer_renders_a_retired_field(signed_in, normal_matter, name):
    assert f'name="{name}"' not in _composer_html(signed_in, normal_matter)


def test_the_closing_section_asks_the_six_approved_questions(
    signed_in, normal_matter, organisation
):
    body = _composer_html(signed_in, normal_matter)

    assert "Lõpeta see teema" in body
    # Põhjus, kept and not renamed.
    assert 'name="disposition"' in body
    assert ">Põhjus<" in body
    # Lõpparvamus, uploaded directly rather than picked from what is already here.
    assert 'name="final_file"' in body
    assert 'type="file"' in body
    assert "Lõpparvamus" in body
    # Saatmise kuupäev.
    assert 'name="final_sent_on"' in body
    assert "Saatmise kuupäev" in body
    # Saaja: a shortlist and a repeatable Muu that searches the catalogue.
    assert 'name="final_recipients"' in body
    assert 'name="final_recipient_names"' in body
    assert "data-recipient-add" in body
    assert "koostaja-saajakataloog" in body
    # Töövõit: an explicit two-way decision.
    assert 'name="work_victory"' in body
    assert 'value="JAH"' in body
    assert 'value="EI"' in body
    # Jõustumise kuupäev, gated on that decision.
    assert 'name="victory_effective_on"' in body
    assert "data-victory-date" in body


def test_the_commencement_date_is_gated_on_the_answer_being_jah(signed_in, normal_matter):
    """Gated by markup rather than by script, so a refused save that *did* say
    Jah comes back with the box open and its error where the reader is."""
    body = _composer_html(signed_in, normal_matter)
    panel = body[body.index("data-victory-date") : body.index("data-victory-date") + 200]
    assert "hidden" in panel

    form = ComposerForm(
        {
            "body": "Võit.",
            "close_matter": "on",
            "disposition": Disposition.COMPLETED,
            "work_victory": "JAH",
        },
        matter=normal_matter,
    )
    assert not form.is_valid()
    assert form["work_victory"].value() == "JAH"


def test_the_muu_box_searches_the_existing_catalogue(signed_in, normal_matter):
    """`Muu` is not a free-text dump: a body somebody added last month must be
    findable rather than created a second time (§7C)."""
    organisation = factories.OrganisationFactory(name="Näidisministeerium Üks")
    OrganisationAlias.objects.create(
        organisation=organisation, alias="NMÜ", alias_type=AliasType.ABBREVIATION
    )

    body = _composer_html(signed_in, normal_matter)

    assert "Näidisministeerium Üks" in body
    assert 'value="NMÜ"' in body


def test_the_disposition_that_needs_a_successor_is_not_offered():
    """`Jätkub teise teema all` is the one closure reason whose truth depends on
    a second record, and this workflow does not ask for one. It is withdrawn
    from the offer rather than posted with a null successor (§3)."""
    offered = {value for value, _label in CLOSURE_CHOICES}

    assert Disposition.SUPERSEDED.value not in offered
    assert Disposition.COMPLETED.value in offered
    # And the domain capability is untouched, ready for an operation that asks.
    assert hasattr(factories.MatterFactory.build(), "superseded_by")


def test_the_domain_still_refuses_a_successor_on_the_wrong_disposition(normal_matter, specialist):
    """Withdrawing a choice from one form must not have relaxed the rule."""
    from app.matters.services import close_matter

    successor = factories.MatterFactory(owner=specialist)
    with pytest.raises(DomainError):
        close_matter(
            matter=normal_matter,
            disposition=Disposition.COMPLETED,
            successor=successor,
            actor=specialist,
        )


# ---------------------------------------------------------------------------
# §2, §20 — one narrative, and where it lands
# ---------------------------------------------------------------------------


def test_the_body_is_the_entry_and_the_closure_reason_both(signed_in, normal_matter):
    response = _post(
        signed_in,
        normal_matter,
        body="<p>Menetlus lõppes; arvamus on esitatud.</p>",
        close_matter="on",
        disposition=Disposition.COMPLETED,
        work_victory="EI",
    )

    assert response.status_code == 200, response.content.decode()[:2000]
    normal_matter.refresh_from_db()
    assert not normal_matter.is_open
    # The entry keeps the markup somebody typed…
    entry = Entry.objects.get(matter=normal_matter)
    assert "Menetlus lõppes" in entry.body
    # …and the banner sentence is the plain-text form of that same narrative,
    # not a second box asking the same question.
    assert normal_matter.disposition_reason == "Menetlus lõppes; arvamus on esitatud."


def test_an_ordinary_save_is_untouched_by_the_closing_redesign(signed_in, normal_matter):
    response = _post(signed_in, normal_matter, body="<p>Helistasin ministeeriumi.</p>")

    assert response.status_code == 200
    normal_matter.refresh_from_db()
    assert normal_matter.is_open
    assert normal_matter.disposition_reason == ""
    assert Entry.objects.filter(matter=normal_matter).count() == 1


# ---------------------------------------------------------------------------
# §4, §5, §6, §21 — the final opinion, uploaded here
# ---------------------------------------------------------------------------


def _close_with_opinion(client, matter, **overrides):
    fields = {
        "body": "<p>Arvamus saadeti välja.</p>",
        "close_matter": "on",
        "disposition": Disposition.RESPONSE_COMPLETE,
        "final_file": _pdf(),
        "final_sent_on": "12.08.2026",
        "work_victory": "EI",
    }
    fields.update(overrides)
    return _post(client, matter, **fields)


def test_a_direct_upload_becomes_the_canonical_sent_opinion(signed_in, normal_matter, organisation):
    response = _close_with_opinion(
        signed_in, normal_matter, final_recipients=[str(organisation.pk)]
    )
    assert response.status_code == 200, response.content.decode()[:3000]

    document = Document.objects.get(matter=normal_matter)
    assert document.role == DocumentRole.KODA_SUBMISSION_FINAL
    version = document.versions.get()

    submission = Submission.objects.get(matter=normal_matter)
    assert submission.status == SubmissionStatus.SENT
    assert submission.final_version == version
    # The day somebody chose, stored as a day (§6).
    assert timezone.localtime(submission.sent_at).date() == date(2026, 8, 12)
    assert submission.sent_at_precision == SentAtPrecision.DATE
    # Titled internally from the Matter; nobody retyped it (§5).
    assert submission.title == normal_matter.title
    # Nothing this flow does not ask for was invented (§8, §9).
    assert submission.channel == ""
    assert submission.reference == ""
    # The evidence is created on this Matter, so it can never be broader than
    # the Matter or the Submission (§18).
    assert document.matter == normal_matter
    assert document.effective_visibility == normal_matter.visibility


def test_the_final_opinion_is_optional(signed_in, normal_matter):
    response = _post(
        signed_in,
        normal_matter,
        body="<p>Koda ei tegele edasi.</p>",
        close_matter="on",
        disposition=Disposition.MONITORING_STOPPED,
        work_victory="EI",
    )

    assert response.status_code == 200
    normal_matter.refresh_from_db()
    assert not normal_matter.is_open
    assert not Submission.objects.filter(matter=normal_matter).exists()
    assert not Document.objects.filter(matter=normal_matter).exists()


@pytest.mark.parametrize("missing", ["final_sent_on", "final_recipients"])
def test_an_uploaded_opinion_needs_its_date_and_its_recipients(
    normal_matter, organisation, missing
):
    data = {
        "body": "Arvamus saadeti.",
        "close_matter": "on",
        "disposition": Disposition.RESPONSE_COMPLETE,
        "final_sent_on": "12.08.2026",
        "final_recipients": [str(organisation.pk)],
        "work_victory": "EI",
    }
    data.pop(missing)
    form = ComposerForm(data, {"final_file": _pdf()}, matter=normal_matter)

    assert not form.is_valid()
    assert missing in form.errors


def test_recipients_and_a_date_cannot_manufacture_a_sent_opinion(
    signed_in, normal_matter, organisation
):
    """A date and an addressee are not evidence. Without the file that went out
    there is nothing to mark sent, and the refusal names the file."""
    response = _post(
        signed_in,
        normal_matter,
        body="<p>Arvamus saadeti.</p>",
        close_matter="on",
        disposition=Disposition.RESPONSE_COMPLETE,
        final_sent_on="12.08.2026",
        final_recipients=[str(organisation.pk)],
        work_victory="EI",
    )

    assert response.status_code == 400
    assert "Lae saadetud fail" in response.content.decode()
    normal_matter.refresh_from_db()
    assert normal_matter.is_open
    assert not Submission.objects.filter(matter=normal_matter).exists()


def test_a_refused_closure_creates_no_canonical_record(signed_in, normal_matter, organisation):
    """Validation runs before the service, so a refusal writes nothing at all."""
    response = _close_with_opinion(
        signed_in,
        normal_matter,
        final_recipients=[str(organisation.pk)],
        final_sent_on="",
    )

    assert response.status_code == 400
    normal_matter.refresh_from_db()
    assert normal_matter.is_open
    assert not Document.objects.filter(matter=normal_matter).exists()
    assert not Submission.objects.filter(matter=normal_matter).exists()
    assert not Entry.objects.filter(matter=normal_matter).exists()


# ---------------------------------------------------------------------------
# §7, §22 — several recipients, existing and new
# ---------------------------------------------------------------------------


def test_seven_new_recipients_land_in_one_save(signed_in, normal_matter):
    """The case the fixed checkbox list could not record at all."""
    before = Organisation.objects.count()

    response = _close_with_opinion(signed_in, normal_matter, final_recipient_names=SEVEN_PARTIES)
    assert response.status_code == 200, response.content.decode()[:3000]

    submission = Submission.objects.get(matter=normal_matter)
    rows = SubmissionRecipient.objects.filter(submission=submission)
    assert rows.count() == 7
    assert {row.role for row in rows} == {RecipientRole.ADDRESSEE}
    assert Organisation.objects.count() == before + 7

    created = Organisation.objects.filter(name__in=SEVEN_PARTIES)
    assert created.count() == 7
    # Named exactly as typed, and classified as nothing the person did not say.
    assert {organisation.organisation_type for organisation in created} == {OrganisationType.OTHER}
    # And reusable: the next composer's `Muu` box finds all seven.
    catalogue = ComposerForm(matter=normal_matter).recipient_catalogue
    assert set(SEVEN_PARTIES) <= set(catalogue)


def test_an_existing_name_is_reused_rather_than_duplicated(signed_in, normal_matter):
    existing = factories.OrganisationFactory(name="Näidisministeerium Kaks")
    before = Organisation.objects.count()

    _close_with_opinion(signed_in, normal_matter, final_recipient_names=["Näidisministeerium Kaks"])

    assert Organisation.objects.count() == before
    submission = Submission.objects.get(matter=normal_matter)
    assert list(submission.recipients.all()) == [existing]


def test_an_existing_alias_resolves_to_its_organisation(signed_in, normal_matter):
    """An alias match is somebody's recorded decision, not a fuzzy guess."""
    existing = factories.OrganisationFactory(name="Näidisministeerium Kolm")
    OrganisationAlias.objects.create(
        organisation=existing, alias="NMK", alias_type=AliasType.ABBREVIATION
    )
    before = Organisation.objects.count()

    _close_with_opinion(signed_in, normal_matter, final_recipient_names=["NMK"])

    assert Organisation.objects.count() == before
    submission = Submission.objects.get(matter=normal_matter)
    assert list(submission.recipients.all()) == [existing]


def test_the_same_name_typed_twice_is_one_recipient(signed_in, normal_matter):
    _close_with_opinion(
        signed_in,
        normal_matter,
        final_recipient_names=["Näidiserakond Eeta", "  Näidiserakond Eeta  "],
    )

    submission = Submission.objects.get(matter=normal_matter)
    assert SubmissionRecipient.objects.filter(submission=submission).count() == 1
    assert Organisation.objects.filter(name="Näidiserakond Eeta").count() == 1


def test_a_shortlist_choice_and_the_same_typed_name_are_one_recipient(signed_in, normal_matter):
    existing = factories.OrganisationFactory(name="Näidisministeerium Neli")

    _close_with_opinion(
        signed_in,
        normal_matter,
        final_recipients=[str(existing.pk)],
        final_recipient_names=["Näidisministeerium Neli"],
    )

    submission = Submission.objects.get(matter=normal_matter)
    rows = SubmissionRecipient.objects.filter(submission=submission)
    assert rows.count() == 1
    assert rows.get().organisation == existing


def test_similar_names_are_never_merged(signed_in, normal_matter):
    """`Keskkonnaministeerium` and `Kliimaministeerium` score highly against each
    other and are different institutions. Only exact normalised identity reuses."""
    factories.OrganisationFactory(name="Näidisministeerium Viis")

    _close_with_opinion(
        signed_in, normal_matter, final_recipient_names=["Näidisministeerium Viisteist"]
    )

    submission = Submission.objects.get(matter=normal_matter)
    assert SubmissionRecipient.objects.filter(submission=submission).count() == 1
    assert Organisation.objects.filter(name="Näidisministeerium Viisteist").exists()
    assert Organisation.objects.filter(name="Näidisministeerium Viis").exists()


def test_an_ambiguous_name_is_refused_rather_than_guessed(normal_matter):
    """Two institutions under one spelling is a question for a person. Picking
    one files the letter against a body nobody named; creating a third makes the
    ambiguity permanent (§7D)."""
    Organisation.objects.create(name="Näidiskogu", organisation_type=OrganisationType.OTHER)
    Organisation.objects.create(name="Näidiskogu", organisation_type=OrganisationType.COMPANY)
    before = Organisation.objects.count()

    form = ComposerForm(
        {
            "body": "Arvamus saadeti.",
            "close_matter": "on",
            "disposition": Disposition.RESPONSE_COMPLETE,
            "final_sent_on": "12.08.2026",
            "final_recipient_names": ["Näidiskogu"],
            "work_victory": "EI",
        },
        {"final_file": _pdf()},
        matter=normal_matter,
    )

    assert not form.is_valid()
    assert "final_recipient_names" in form.errors
    assert Organisation.objects.count() == before


def test_a_failed_closure_leaves_no_organisations_behind(signed_in, normal_matter):
    """New recipients are persisted by the save, never by somebody typing (§7E)."""
    before = Organisation.objects.count()

    response = _close_with_opinion(
        signed_in, normal_matter, final_recipient_names=SEVEN_PARTIES, final_sent_on=""
    )

    assert response.status_code == 400
    assert Organisation.objects.count() == before


# ---------------------------------------------------------------------------
# §10 – §13, §23 — Töövõit, and where the commencement date goes
# ---------------------------------------------------------------------------


def test_closing_requires_an_explicit_answer_about_the_work_victory(normal_matter):
    """No silent default. A Matter closed without anybody answering would count
    as "no win", which is a claim the person never made (§10)."""
    form = ComposerForm(
        {"body": "Menetlus lõppes.", "close_matter": "on", "disposition": Disposition.COMPLETED},
        matter=normal_matter,
    )

    assert not form.is_valid()
    assert "work_victory" in form.errors


def test_toovoit_ei_records_no_victory_and_no_commencement(signed_in, normal_matter):
    response = _post(
        signed_in,
        normal_matter,
        body="<p>Eelnõu langes ära.</p>",
        close_matter="on",
        disposition=Disposition.INITIATIVE_WITHDRAWN,
        work_victory="EI",
    )

    assert response.status_code == 200
    normal_matter.refresh_from_db()
    assert not normal_matter.is_open
    # The absence of a victory record is the answer. No negative row (§11).
    assert not MatterWorkVictory.objects.filter(matter=normal_matter).exists()
    assert not MatterEffectiveDate.objects.filter(matter=normal_matter).exists()


def test_toovoit_jah_records_the_victory_and_the_commencement(signed_in, normal_matter):
    response = _post(
        signed_in,
        normal_matter,
        body="<p>Piirmäär tõsteti 2000 euroni.</p>",
        close_matter="on",
        disposition=Disposition.COMPLETED,
        work_victory="JAH",
        victory_effective_on="01.01.2027",
    )
    assert response.status_code == 200, response.content.decode()[:3000]

    victory = MatterWorkVictory.objects.get(matter=normal_matter)
    # The wording is the one narrative this save carried (§12).
    assert victory.title == "Piirmäär tõsteti 2000 euroni."
    assert victory.detail == ""
    # The existing governance, unchanged: the manual door records a decision
    # somebody has already made, and `may_review_work_victory` — the authority
    # to rule on *somebody else's* candidate — is not touched by this form.
    assert victory.status == WorkVictoryStatus.CONFIRMED
    assert victory.confirmed_by is not None
    assert victory.confirmed_at is not None

    # §13: the commencement is a MatterEffectiveDate…
    effective = MatterEffectiveDate.objects.get(matter=normal_matter)
    assert effective.kind == EffectiveDateKind.KNOWN_DATE
    assert effective.date_precision == DatePrecision.EXACT
    assert effective.date_value == date(2027, 1, 1)
    assert effective.period_end == date(2027, 1, 1)
    assert effective.status == FactStatus.ACTIVE
    # …and the victory's reporting period was not borrowed to hold it.
    assert victory.period_date is None
    assert victory.period_end is None


def test_toovoit_jah_without_a_commencement_date_is_refused(normal_matter):
    form = ComposerForm(
        {
            "body": "Piirmäär tõsteti.",
            "close_matter": "on",
            "disposition": Disposition.COMPLETED,
            "work_victory": "JAH",
        },
        matter=normal_matter,
    )

    assert not form.is_valid()
    assert "victory_effective_on" in form.errors


def test_toovoit_jah_without_a_body_is_refused_on_the_body(normal_matter):
    """Rather than inventing a description for a record the department reports
    on. The error belongs where the missing narrative is (§12)."""
    form = ComposerForm(
        {
            "close_matter": "on",
            "disposition": Disposition.COMPLETED,
            "work_victory": "JAH",
            "victory_effective_on": "01.01.2027",
        },
        matter=normal_matter,
    )

    assert not form.is_valid()
    assert "body" in form.errors
    assert not MatterWorkVictory.objects.filter(matter=normal_matter).exists()


def test_an_equivalent_commencement_date_is_not_duplicated(signed_in, normal_matter, specialist):
    """The department may already hold the fact, entered when the act was
    published. Closing the file does not make it true a second time (§13)."""
    existing = add_effective_date(
        matter=normal_matter,
        actor=specialist,
        kind=EffectiveDateKind.KNOWN_DATE,
        date_value=date(2027, 1, 1),
        period_end=date(2027, 1, 1),
        date_precision=DatePrecision.EXACT,
    )

    _post(
        signed_in,
        normal_matter,
        body="<p>Piirmäär tõsteti.</p>",
        close_matter="on",
        disposition=Disposition.COMPLETED,
        work_victory="JAH",
        victory_effective_on="01.01.2027",
    )

    assert MatterEffectiveDate.objects.filter(matter=normal_matter).count() == 1
    assert MatterEffectiveDate.objects.get(matter=normal_matter).pk == existing.pk


# ---------------------------------------------------------------------------
# §14, §24 — one save, one transaction
# ---------------------------------------------------------------------------


def test_a_closure_that_fails_late_commits_nothing(
    signed_in, normal_matter, organisation, monkeypatch
):
    """Everything the closure writes is inside one transaction, so a refusal
    anywhere leaves the Matter exactly as it was — including the institutions
    that were about to be created for it."""
    import app.matters.services as services

    def explode(**_kwargs):
        raise DomainError("Katse: hilisem samm ebaõnnestus.")

    monkeypatch.setattr(services, "close_matter", explode)
    before = Organisation.objects.count()

    response = _close_with_opinion(
        signed_in,
        normal_matter,
        final_recipients=[str(organisation.pk)],
        final_recipient_names=SEVEN_PARTIES,
        work_victory="JAH",
        victory_effective_on="01.01.2027",
    )

    assert response.status_code == 400
    normal_matter.refresh_from_db()
    assert normal_matter.is_open
    assert not Entry.objects.filter(matter=normal_matter).exists()
    assert not Document.objects.filter(matter=normal_matter).exists()
    assert not Submission.objects.filter(matter=normal_matter).exists()
    assert not MatterWorkVictory.objects.filter(matter=normal_matter).exists()
    assert not MatterEffectiveDate.objects.filter(matter=normal_matter).exists()
    assert Organisation.objects.count() == before


def test_one_save_writes_everything_the_closure_carried(signed_in, normal_matter, organisation):
    """The property the composer exists for: a closure is not four POSTs."""
    response = _close_with_opinion(
        signed_in,
        normal_matter,
        body="<p>Arvamus esitati ja piirmäär tõusis.</p>",
        final_recipients=[str(organisation.pk)],
        final_recipient_names=SEVEN_PARTIES,
        work_victory="JAH",
        victory_effective_on="01.01.2027",
    )
    assert response.status_code == 200, response.content.decode()[:3000]

    normal_matter.refresh_from_db()
    assert not normal_matter.is_open
    assert Entry.objects.filter(matter=normal_matter).count() == 1
    assert Document.objects.filter(matter=normal_matter).count() == 1
    submission = Submission.objects.get(matter=normal_matter)
    assert SubmissionRecipient.objects.filter(submission=submission).count() == 8
    assert MatterWorkVictory.objects.filter(matter=normal_matter).count() == 1
    assert MatterEffectiveDate.objects.filter(matter=normal_matter).count() == 1


# ---------------------------------------------------------------------------
# §18 — authorization is unchanged
# ---------------------------------------------------------------------------


def test_a_reader_cannot_close_upload_or_create_recipients(client, normal_matter):
    """One gate, before anything is parsed. A crafted POST from somebody who
    may not write business content adds no document, no recipient, no victory
    and no effective date."""
    reader = factories.ReaderFactory()
    client.force_login(reader)
    before = Organisation.objects.count()

    response = _close_with_opinion(
        client,
        normal_matter,
        final_recipient_names=SEVEN_PARTIES,
        work_victory="JAH",
        victory_effective_on="01.01.2027",
    )

    assert response.status_code == 404
    normal_matter.refresh_from_db()
    assert normal_matter.is_open
    assert not Document.objects.filter(matter=normal_matter).exists()
    assert not Submission.objects.filter(matter=normal_matter).exists()
    assert not MatterWorkVictory.objects.filter(matter=normal_matter).exists()
    assert not MatterEffectiveDate.objects.filter(matter=normal_matter).exists()
    assert Organisation.objects.count() == before


def test_a_closure_on_an_invisible_matter_is_a_404(client, restricted_matter):
    """The same disclosure `get_visible_matter` exists to avoid: a crafted POST
    must not confirm that a restricted file with that id exists.

    An ADMINISTRATOR rather than a lawyer, because the department's
    confidentiality boundary is the application: both lawyer roles read
    RESTRICTED content by design, and technical administration is the role that
    genuinely cannot (`ROLES_WITH_RESTRICTED_ACCESS`, docs/adr/0042)."""
    outsider = factories.AdministratorFactory()
    client.force_login(outsider)

    response = _close_with_opinion(client, restricted_matter, final_recipient_names=["Näidissaaja"])

    assert response.status_code == 404
    restricted_matter.refresh_from_db()
    assert restricted_matter.is_open
    assert not Document.objects.filter(matter=restricted_matter).exists()


def test_final_evidence_is_never_broader_than_its_matter(signed_in, restricted_matter):
    """DATA-001/DATA-002: the file is created on this Matter and inherits it, so
    the upload cannot widen what the closure's own evidence discloses."""
    from app.core.enums import Visibility

    response = _close_with_opinion(
        signed_in, restricted_matter, final_recipient_names=["Näidissaaja"]
    )
    assert response.status_code == 200, response.content.decode()[:3000]

    document = Document.objects.get(matter=restricted_matter)
    submission = Submission.objects.get(matter=restricted_matter)
    assert document.effective_visibility == Visibility.RESTRICTED
    assert submission.effective_visibility == Visibility.RESTRICTED


# ---------------------------------------------------------------------------
# service contract — the composer is not the only caller
# ---------------------------------------------------------------------------


def test_the_service_resolves_recipients_inside_the_closure(normal_matter, specialist):
    result = compose_update(
        matter=normal_matter,
        author=specialist,
        body="<p>Arvamus saadeti.</p>",
        closure={
            "disposition": Disposition.RESPONSE_COMPLETE,
            "reason": "Arvamus saadeti.",
            "final_opinion": {
                "upload": _pdf(),
                "recipients": [],
                "recipient_names": SEVEN_PARTIES,
                "sent_at": timezone.now() - timedelta(days=1),
            },
        },
    )

    assert result.submission is not None
    assert result.closed
    assert SubmissionRecipient.objects.filter(submission=result.submission).count() == 7
