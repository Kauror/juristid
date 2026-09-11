"""`Lõpeta teema`, as the approved Teema target asks it — and everything under it.

**The panel asks two questions.** `Kuidas lõppes` and an optional `Lõppsõna`.
The section asked six until this pass: why, the file that went out, when, to
whom, whether it was a win, and when the result commenced.

The four that went are not a simplification of the closure but a correction of
what closing *means*. **Closing a Matter is not a claim that an opinion was
sent.** Koda closes files it never wrote to anybody about; it sends opinions on
files that stay open for another year. Requiring the sent PDF in order to finish
a file made the commonest closure impossible to record honestly, and made the
rarer one look like the only shape a closure has (docs/adr/0074 §10).

**None of the canonical rules moved, and this file still proves every one of
them.** A SENT `Submission` still needs its exact final evidence, still resolves
its recipients through the organisation catalogue, still refuses an ambiguous
name and still inherits its Matter's visibility. A work victory still goes
through the manual door. A commencement is still a `MatterEffectiveDate` and not
a column borrowed from the victory's reporting period. What changed is the
caller: those tests drive `compose_update` directly, because the composer form no
longer carries that half — and `compose_update` is the contract, not the form.

`+ Töövõit` and `+ Jõustumine` are composer panels of their own now, tested in
`tests/test_teema_approved_target.py`. A win no longer requires closing the file
to record it.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

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
from app.matters.forms import CLOSURE_CHOICES, COMPOSER_CLOSURE_CHOICES, ComposerForm
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
        "next_text": "",
        "next_date": "",
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
    # The `Lõpeta see teema` confirmation. Answering the closing section is the
    # request to close; a seventh control gating the six above it is where the
    # pilot lost a whole closure without being told (pilot QA F-02,
    # tests/test_pilot_p1_workflows.py).
    "close_matter",
    "successor",
    "final_version",
    "final_title",
    "final_channel",
    "final_reference",
    "victory_title",
    "victory_detail",
    # Retired by the approved target: closing a file is not a claim that an
    # opinion was sent, and a win is recorded by `+ Töövõit` whether or not the
    # file is closing (docs/adr/0074 §10).
    "final_file",
    "final_sent_on",
    "final_recipients",
    "final_recipient_names",
    "work_victory",
    "victory_effective_on",
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


def test_the_closing_panel_asks_the_two_approved_questions(signed_in, normal_matter):
    body = _composer_html(signed_in, normal_matter)

    # The panel, behind its own chip, and no confirmation box inside it.
    assert 'id="cx-lopeta"' in body
    assert "+ Lõpeta teema" in body
    assert "Lõpeta see teema" not in body

    # `Kuidas lõppes` — three chips over one hidden field, so an unanswered
    # question is representable and a crafted POST still validates against the
    # whole stored vocabulary.
    assert "Kuidas lõppes" in body
    assert 'name="disposition"' in body
    for label in ("Jõustus", "Menetlus lõppes", "Loobuti"):
        assert f">{label}<" in body

    # `Lõppsõna`, optional.
    assert 'name="closing_words"' in body
    assert "Lõppsõna" in body
    assert "valikuline" in body
    assert "Mis sellest teemast lõpuks sai?" in body

    # And the note the target ends the panel with.
    assert "Teema läheb arhiivi. Avatud järgmised sammud tühistatakse." in body


def test_the_three_offered_outcomes_map_onto_stored_dispositions():
    """Three chips over one vocabulary, not a new one. `RESPONSE_COMPLETE`,
    `NO_POSITION_FORMED`, `DUPLICATE` and `OTHER` remain valid stored values with
    no chip — every one of them still reads, filters and reports."""
    offered = dict(COMPOSER_CLOSURE_CHOICES)

    assert offered == {
        Disposition.COMPLETED.value: "Jõustus",
        Disposition.INITIATIVE_WITHDRAWN.value: "Menetlus lõppes",
        Disposition.MONITORING_STOPPED.value: "Loobuti",
    }
    assert Disposition.SUPERSEDED.value not in offered
    # The field itself still accepts the wider set, so a historical value is
    # never refused by the form that happens to be rendering.
    accepted = {value for value, _label in ComposerForm().fields["disposition"].choices}
    assert Disposition.RESPONSE_COMPLETE.value in accepted


def test_no_chip_is_selected_until_somebody_chooses():
    """An unanswered `Kuidas lõppes` has to be representable, or the panel would
    post a closure from every ordinary save the moment it was opened — which is
    the defect the empty option was added to fix (pilot QA F-02)."""
    chips = ComposerForm().closure_chips

    assert len(chips) == 3
    assert not any(chip["selected"] for chip in chips)


def test_a_closure_needs_its_reason(normal_matter):
    form = ComposerForm(
        {"body": "Menetlus lõppes.", "closing_words": "Sai tehtud."}, matter=normal_matter
    )

    assert not form.is_valid()
    assert "disposition" in form.errors


def test_the_final_word_is_the_stored_reason_when_there_is_one(signed_in, normal_matter):
    """`Lõppsõna` is the box for a closure whose entry says something else, or
    nothing at all."""
    response = _post(
        signed_in,
        normal_matter,
        body="<p>Helistasin ministeeriumi.</p>",
        disposition=Disposition.MONITORING_STOPPED,
        closing_words="Koda ei tegele edasi.",
    )

    assert response.status_code == 200, response.content.decode()[:2000]
    normal_matter.refresh_from_db()
    assert not normal_matter.is_open
    assert normal_matter.disposition_reason == "Koda ei tegele edasi."
    # The entry keeps its own wording; the two are different sentences.
    assert "Helistasin" in Entry.objects.get(matter=normal_matter).body


def test_closing_no_longer_asks_for_a_sent_opinion(signed_in, normal_matter):
    """The commonest closure: a file Koda never wrote to anybody about. It used
    to be unrecordable without uploading something (docs/adr/0074 §10)."""
    response = _post(
        signed_in,
        normal_matter,
        body="<p>Eelnõu langes ära.</p>",
        disposition=Disposition.INITIATIVE_WITHDRAWN,
    )

    assert response.status_code == 200, response.content.decode()[:2000]
    normal_matter.refresh_from_db()
    assert not normal_matter.is_open
    assert not Submission.objects.filter(matter=normal_matter).exists()
    assert not Document.objects.filter(matter=normal_matter).exists()
    # And no victory was invented for a file nobody called a win.
    assert not MatterWorkVictory.objects.filter(matter=normal_matter).exists()


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
# The final opinion, the recipients and the work victory — **through the
# service**, which is where they always were.
#
# The composer no longer offers this half. `compose_update` still accepts it,
# the archive importer and the Submission workflow still call the same four
# canonical acts, and every invariant below is unchanged: a SENT Submission
# needs its exact final evidence, recipients resolve through the organisation
# catalogue, an ambiguous name is refused rather than guessed, and the whole
# thing is one transaction (docs/adr/0074 §10).
# ---------------------------------------------------------------------------


def _close_with_opinion(matter, actor, **overrides):
    """One composer save that closes a Matter and records the opinion that went
    out, as the service receives it."""
    final_opinion = {
        "upload": _pdf(),
        "recipients": [],
        "recipient_names": [],
        "sent_at": timezone.make_aware(datetime(2026, 8, 12, 0, 0)),
    }
    final_opinion.update(overrides.pop("final_opinion", {}))
    closure = {
        "disposition": Disposition.RESPONSE_COMPLETE,
        "reason": "Arvamus saadeti välja.",
        "final_opinion": final_opinion,
    }
    closure.update(overrides.pop("closure", {}))
    return compose_update(
        matter=matter,
        author=actor,
        body=overrides.pop("body", "<p>Arvamus saadeti välja.</p>"),
        closure=closure,
        **overrides,
    )


def test_a_direct_upload_becomes_the_canonical_sent_opinion(
    normal_matter, specialist, organisation
):
    _close_with_opinion(normal_matter, specialist, final_opinion={"recipients": [organisation]})

    document = Document.objects.get(matter=normal_matter)
    assert document.role == DocumentRole.KODA_SUBMISSION_FINAL
    version = document.versions.get()

    submission = Submission.objects.get(matter=normal_matter)
    assert submission.status == SubmissionStatus.SENT
    assert submission.final_version == version
    # The day somebody chose, stored as a day.
    assert timezone.localtime(submission.sent_at).date() == date(2026, 8, 12)
    assert submission.sent_at_precision == SentAtPrecision.DATE
    # Titled internally from the Matter; nobody retyped it.
    assert submission.title == normal_matter.title
    # Nothing this flow does not ask for was invented.
    assert submission.channel == ""
    assert submission.reference == ""
    # The evidence is created on this Matter, so it can never be broader than
    # the Matter or the Submission.
    assert document.matter == normal_matter
    assert document.effective_visibility == normal_matter.visibility


def test_the_final_opinion_is_optional(signed_in, normal_matter):
    response = _post(
        signed_in,
        normal_matter,
        body="<p>Koda ei tegele edasi.</p>",
        disposition=Disposition.MONITORING_STOPPED,
    )

    assert response.status_code == 200
    normal_matter.refresh_from_db()
    assert not normal_matter.is_open
    assert not Submission.objects.filter(matter=normal_matter).exists()
    assert not Document.objects.filter(matter=normal_matter).exists()


def test_a_sent_opinion_always_carries_its_exact_final_evidence(
    normal_matter, specialist, organisation
):
    """The invariant the old form's four questions were protecting, stated where
    it is actually enforced.

    `mark_submission_sent` refuses a submission with no final version, so there
    is no path — form, importer or shell — that marks an opinion sent without the
    bytes that went out. The retired «Lae saadetud fail» refusal was a *form*
    rule layered on top of this one; removing the form did not remove this."""
    from app.submissions.services import create_submission, mark_submission_sent

    submission = create_submission(
        matter=normal_matter,
        title=normal_matter.title,
        actor=specialist,
        recipients=[organisation],
    )
    assert submission.final_version is None

    with pytest.raises(DomainError):
        mark_submission_sent(submission=submission, actor=specialist, sent_at=timezone.now())

    submission.refresh_from_db()
    assert submission.status != SubmissionStatus.SENT


# ---------------------------------------------------------------------------
# Several recipients, existing and new
# ---------------------------------------------------------------------------


def test_seven_new_recipients_land_in_one_save(normal_matter, specialist):
    """The case the fixed checkbox list could not record at all."""
    before = Organisation.objects.count()

    result = _close_with_opinion(
        normal_matter, specialist, final_opinion={"recipient_names": SEVEN_PARTIES}
    )

    rows = SubmissionRecipient.objects.filter(submission=result.submission)
    assert rows.count() == 7
    assert {row.role for row in rows} == {RecipientRole.ADDRESSEE}
    assert Organisation.objects.count() == before + 7

    created = Organisation.objects.filter(name__in=SEVEN_PARTIES)
    assert created.count() == 7
    # Named exactly as typed, and classified as nothing the person did not say.
    assert {organisation.organisation_type for organisation in created} == {OrganisationType.OTHER}


def test_an_existing_name_is_reused_rather_than_duplicated(normal_matter, specialist):
    existing = factories.OrganisationFactory(name="Näidisministeerium Kaks")
    before = Organisation.objects.count()

    result = _close_with_opinion(
        normal_matter,
        specialist,
        final_opinion={"recipient_names": ["Näidisministeerium Kaks"]},
    )

    assert Organisation.objects.count() == before
    assert list(result.submission.recipients.all()) == [existing]


def test_an_existing_alias_resolves_to_its_organisation(normal_matter, specialist):
    """An alias match is somebody's recorded decision, not a fuzzy guess."""
    existing = factories.OrganisationFactory(name="Näidisministeerium Kolm")
    OrganisationAlias.objects.create(
        organisation=existing, alias="NMK", alias_type=AliasType.ABBREVIATION
    )
    before = Organisation.objects.count()

    result = _close_with_opinion(
        normal_matter, specialist, final_opinion={"recipient_names": ["NMK"]}
    )

    assert Organisation.objects.count() == before
    assert list(result.submission.recipients.all()) == [existing]


def test_the_same_name_typed_twice_is_one_recipient(normal_matter, specialist):
    result = _close_with_opinion(
        normal_matter,
        specialist,
        final_opinion={"recipient_names": ["Näidiserakond Eeta", "  Näidiserakond Eeta  "]},
    )

    assert SubmissionRecipient.objects.filter(submission=result.submission).count() == 1
    assert Organisation.objects.filter(name="Näidiserakond Eeta").count() == 1


def test_a_chosen_organisation_and_the_same_typed_name_are_one_recipient(normal_matter, specialist):
    existing = factories.OrganisationFactory(name="Näidisministeerium Neli")

    result = _close_with_opinion(
        normal_matter,
        specialist,
        final_opinion={
            "recipients": [existing],
            "recipient_names": ["Näidisministeerium Neli"],
        },
    )

    rows = SubmissionRecipient.objects.filter(submission=result.submission)
    assert rows.count() == 1
    assert rows.get().organisation == existing


def test_similar_names_are_never_merged(normal_matter, specialist):
    """`Keskkonnaministeerium` and `Kliimaministeerium` score highly against each
    other and are different institutions. Only exact normalised identity reuses."""
    factories.OrganisationFactory(name="Näidisministeerium Viis")

    result = _close_with_opinion(
        normal_matter,
        specialist,
        final_opinion={"recipient_names": ["Näidisministeerium Viisteist"]},
    )

    assert SubmissionRecipient.objects.filter(submission=result.submission).count() == 1
    assert Organisation.objects.filter(name="Näidisministeerium Viisteist").exists()
    assert Organisation.objects.filter(name="Näidisministeerium Viis").exists()


def test_an_ambiguous_name_is_refused_rather_than_guessed(normal_matter, specialist):
    """Two institutions under one spelling is a question for a person. Picking
    one files the letter against a body nobody named; creating a third makes the
    ambiguity permanent."""
    Organisation.objects.create(name="Näidiskogu", organisation_type=OrganisationType.OTHER)
    Organisation.objects.create(name="Näidiskogu", organisation_type=OrganisationType.COMPANY)
    before = Organisation.objects.count()

    with pytest.raises(DomainError):
        _close_with_opinion(
            normal_matter, specialist, final_opinion={"recipient_names": ["Näidiskogu"]}
        )

    assert Organisation.objects.count() == before
    normal_matter.refresh_from_db()
    assert normal_matter.is_open


def test_a_failed_closure_leaves_no_organisations_behind(normal_matter, specialist):
    """New recipients are persisted by the save, never by somebody typing.

    Seven good names and one ambiguous one: the ambiguity refuses the whole save,
    and the seven that would have been created are rolled back with it."""
    Organisation.objects.create(name="Näidiskogu", organisation_type=OrganisationType.OTHER)
    Organisation.objects.create(name="Näidiskogu", organisation_type=OrganisationType.COMPANY)
    before = Organisation.objects.count()

    with pytest.raises(DomainError):
        _close_with_opinion(
            normal_matter,
            specialist,
            final_opinion={"recipient_names": [*SEVEN_PARTIES, "Näidiskogu"]},
        )

    assert Organisation.objects.count() == before
    assert not Organisation.objects.filter(name__in=SEVEN_PARTIES).exists()


# ---------------------------------------------------------------------------
# Töövõit, and where the commencement date goes
# ---------------------------------------------------------------------------


def test_an_ordinary_closure_records_no_victory_and_no_commencement(signed_in, normal_matter):
    response = _post(
        signed_in,
        normal_matter,
        body="<p>Eelnõu langes ära.</p>",
        disposition=Disposition.INITIATIVE_WITHDRAWN,
    )

    assert response.status_code == 200
    normal_matter.refresh_from_db()
    assert not normal_matter.is_open
    # The absence of a victory record is the answer. No negative row.
    assert not MatterWorkVictory.objects.filter(matter=normal_matter).exists()
    assert not MatterEffectiveDate.objects.filter(matter=normal_matter).exists()


def test_a_closure_may_still_carry_a_victory_and_a_commencement(normal_matter, specialist):
    """The service contract is unchanged, and the archive importer and any later
    closing operation still rely on it."""
    result = compose_update(
        matter=normal_matter,
        author=specialist,
        body="<p>Piirmäär tõsteti 2000 euroni.</p>",
        closure={
            "disposition": Disposition.COMPLETED,
            "reason": "Piirmäär tõsteti 2000 euroni.",
            "work_victory": {"title": "Piirmäär tõsteti 2000 euroni.", "detail": ""},
            "effective_date": {
                "date_value": date(2027, 1, 1),
                "period_end": date(2027, 1, 1),
            },
        },
    )
    assert result.closed

    victory = MatterWorkVictory.objects.get(matter=normal_matter)
    assert victory.title == "Piirmäär tõsteti 2000 euroni."
    assert victory.detail == ""
    # The existing governance, unchanged: the manual door records a decision
    # somebody has already made, and `may_review_work_victory` — the authority
    # to rule on *somebody else's* candidate — is not touched by it.
    assert victory.status == WorkVictoryStatus.CONFIRMED
    assert victory.confirmed_by is not None
    assert victory.confirmed_at is not None

    # The commencement is a MatterEffectiveDate…
    effective = MatterEffectiveDate.objects.get(matter=normal_matter)
    assert effective.kind == EffectiveDateKind.KNOWN_DATE
    assert effective.date_precision == DatePrecision.EXACT
    assert effective.date_value == date(2027, 1, 1)
    assert effective.period_end == date(2027, 1, 1)
    assert effective.status == FactStatus.ACTIVE
    # …and the victory's reporting period was not borrowed to hold it.
    assert victory.period_date is None
    assert victory.period_end is None


def test_an_equivalent_commencement_date_is_not_duplicated(normal_matter, specialist):
    """The department may already hold the fact, entered when the act was
    published. Closing the file does not make it true a second time."""
    existing = add_effective_date(
        matter=normal_matter,
        actor=specialist,
        kind=EffectiveDateKind.KNOWN_DATE,
        date_value=date(2027, 1, 1),
        period_end=date(2027, 1, 1),
        date_precision=DatePrecision.EXACT,
    )

    compose_update(
        matter=normal_matter,
        author=specialist,
        body="<p>Piirmäär tõsteti.</p>",
        closure={
            "disposition": Disposition.COMPLETED,
            "reason": "Piirmäär tõsteti.",
            "work_victory": {"title": "Piirmäär tõsteti.", "detail": ""},
            "effective_date": {
                "date_value": date(2027, 1, 1),
                "period_end": date(2027, 1, 1),
            },
        },
    )

    assert MatterEffectiveDate.objects.filter(matter=normal_matter).count() == 1
    assert MatterEffectiveDate.objects.get(matter=normal_matter).pk == existing.pk


# ---------------------------------------------------------------------------
# One save, one transaction
# ---------------------------------------------------------------------------


def test_a_closure_that_fails_late_commits_nothing(
    normal_matter, specialist, organisation, monkeypatch
):
    """Everything the closure writes is inside one transaction, so a refusal
    anywhere leaves the Matter exactly as it was — including the institutions
    that were about to be created for it."""
    import app.matters.services as services

    def explode(**_kwargs):
        raise DomainError("Katse: hilisem samm ebaõnnestus.")

    monkeypatch.setattr(services, "close_matter", explode)
    before = Organisation.objects.count()

    with pytest.raises(DomainError):
        _close_with_opinion(
            normal_matter,
            specialist,
            final_opinion={
                "recipients": [organisation],
                "recipient_names": SEVEN_PARTIES,
            },
            closure={
                "disposition": Disposition.COMPLETED,
                "reason": "Arvamus saadeti.",
                "work_victory": {"title": "Piirmäär tõsteti.", "detail": ""},
                "effective_date": {
                    "date_value": date(2027, 1, 1),
                    "period_end": date(2027, 1, 1),
                },
            },
        )

    normal_matter.refresh_from_db()
    assert normal_matter.is_open
    assert not Entry.objects.filter(matter=normal_matter).exists()
    assert not Document.objects.filter(matter=normal_matter).exists()
    assert not Submission.objects.filter(matter=normal_matter).exists()
    assert not MatterWorkVictory.objects.filter(matter=normal_matter).exists()
    assert not MatterEffectiveDate.objects.filter(matter=normal_matter).exists()
    assert Organisation.objects.count() == before


def test_one_save_writes_everything_the_closure_carried(normal_matter, specialist, organisation):
    """The property the composer exists for: a closure is not four POSTs."""
    _close_with_opinion(
        normal_matter,
        specialist,
        body="<p>Arvamus esitati ja piirmäär tõusis.</p>",
        final_opinion={
            "recipients": [organisation],
            "recipient_names": SEVEN_PARTIES,
        },
        closure={
            "disposition": Disposition.COMPLETED,
            "reason": "Arvamus esitati ja piirmäär tõusis.",
            "work_victory": {"title": "Piirmäär tõusis.", "detail": ""},
            "effective_date": {
                "date_value": date(2027, 1, 1),
                "period_end": date(2027, 1, 1),
            },
        },
    )

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


def test_a_reader_cannot_close_a_matter(client, normal_matter):
    """One gate, before anything is parsed. A crafted POST from somebody who may
    not write business content closes nothing and writes nothing."""
    reader = factories.ReaderFactory()
    client.force_login(reader)

    response = _post(
        client,
        normal_matter,
        body="<p>Katse.</p>",
        disposition=Disposition.COMPLETED,
    )

    assert response.status_code == 404
    normal_matter.refresh_from_db()
    assert normal_matter.is_open
    assert not Entry.objects.filter(matter=normal_matter).exists()


def test_a_closure_on_an_invisible_matter_is_a_404(client, restricted_matter):
    """The same disclosure `get_visible_matter` exists to avoid: a crafted POST
    must not confirm that a restricted file with that id exists.

    An ADMINISTRATOR rather than a lawyer, because the department's
    confidentiality boundary is the application: both lawyer roles read
    RESTRICTED content by design, and technical administration is the role that
    genuinely cannot (`ROLES_WITH_RESTRICTED_ACCESS`, docs/adr/0042).

    That same reasoning is why the 404 now arrives from the write gate instead:
    an ADMINISTRATOR may not write either, and `@business_write_required` runs
    before `get_visible_matter`. The refusal and the silence are both still
    asserted here; what this route can no longer show is *which* rule produced
    them. See `tests/test_one_write_gate.py`."""
    outsider = factories.AdministratorFactory()
    client.force_login(outsider)

    response = _post(
        client,
        restricted_matter,
        body="<p>Katse.</p>",
        disposition=Disposition.COMPLETED,
    )

    assert response.status_code == 404
    restricted_matter.refresh_from_db()
    assert restricted_matter.is_open
    assert not Entry.objects.filter(matter=restricted_matter).exists()


def test_final_evidence_is_never_broader_than_its_matter(restricted_matter, specialist):
    """DATA-001/DATA-002: the file is created on this Matter and inherits it, so
    the upload cannot widen what the closure's own evidence discloses."""
    from app.core.enums import Visibility

    _close_with_opinion(
        restricted_matter, specialist, final_opinion={"recipient_names": ["Näidissaaja"]}
    )

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
