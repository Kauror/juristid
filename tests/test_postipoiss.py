"""Postipoiss letters and their Teemad (docs/adr/0216).

The package loads and decides nothing; only the calibrated tier applies without
a person; applying files the letter through the archive's evidence path and
dates the opinion with the letter's own date; seven days is one act, both here
and in Statistika; lawyers decide the rest in Haldus → Ajaloolised kirjad.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.errors import DomainError
from app.legacy_import import postipoiss
from app.legacy_import.archive_views import _served_filename
from app.legacy_import.opinion_binary import OpinionArchiveBinary, OpinionArchiveMatterLink
from app.legacy_import.postipoiss_models import (
    PairingBasis,
    PairingState,
    PostipoissLetter,
    PostipoissPairing,
)
from app.matters.enums import MatterDataClass
from app.reporting.opinions import SAME_ACT_DAYS, opinion_facts
from app.submissions.enums import SentAtPrecision, SubmissionStatus
from app.submissions.models import Submission
from tests import factories
from tests.refusals import refused
from tests.test_statistika_dashboard import _imported

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.4\n% Postipoiss test letter\n"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _letter(kirja_id, *, reference="", tier="A", files=(PDF,), letter_date="2014-03-10", year=2014):
    return {
        "kirja_id": kirja_id,
        "year": year,
        "register_row": int(kirja_id[-4:]) if kirja_id[-4:].isdigit() else None,
        "registered_on": letter_date,
        "letter_date": letter_date,
        "title": f"Arvamus {kirja_id}",
        "recipient": "Rahandusministeerium",
        "body": "Eesti Kaubandus-Tööstuskoda esitab arvamuse",
        "files": [
            {"sha256": hashlib.sha256(data).hexdigest(), "filename": f"{kirja_id}-{i}.pdf"}
            for i, data in enumerate(files)
        ],
        "suggestion": {"reference": reference, "tier": tier, "signals": ["date", "recipient"]},
    }


def _package(tmp_path, letters, blobs=(PDF,)):
    root = tmp_path / "pkg"
    (root / "files").mkdir(parents=True)
    for data in blobs:
        (root / "files" / hashlib.sha256(data).hexdigest()).write_bytes(data)
    (root / "manifest.json").write_text(json.dumps({"letters": letters}), encoding="utf-8")
    return postipoiss.read_package(root)


@pytest.fixture
def matter(db):
    return factories.MatterFactory(reference_year=2014, reference_number=85)


# -- loading ---------------------------------------------------------------------------------


def test_load_stores_letters_files_and_proposals_and_decides_nothing(tmp_path, matter):
    package = _package(
        tmp_path,
        [
            _letter("V2014-0072", reference="2014_85"),
            _letter("V2014-0073", reference="2014_999", tier="B"),
            _letter("V2014-0074", tier="F", files=()),
        ],
    )
    report = postipoiss.load(package, write=True)

    assert report.letters_created == 3
    assert report.proposals_resolved == 1 and report.proposals_unresolved == 1
    assert OpinionArchiveBinary.objects.count() == 1  # one byte sequence, held once
    first = PostipoissPairing.objects.get(letter__kirja_id="V2014-0072")
    assert first.suggested_matter == matter and first.state == PairingState.PENDING
    assert (
        PostipoissPairing.objects.get(letter__kirja_id="V2014-0073").suggested_reference
        == "2014_999"
    )
    assert not Submission.objects.exists()

    again = postipoiss.load(package, write=True)
    assert again.letters_created == 0 and again.letters_already == 3


def test_plan_writes_nothing_and_a_different_digest_is_refused(tmp_path, matter):
    package = _package(tmp_path, [_letter("V2014-0072", reference="2014_85")])
    assert postipoiss.load(package, write=False).letters_created == 1
    assert not PostipoissLetter.objects.exists()
    with refused("Pakett ei ole ülevaadatud pakett"):
        postipoiss.read_package(package.root, expected_sha256="0" * 64)


def test_a_file_whose_bytes_do_not_match_its_hash_is_reported_not_stored(tmp_path):
    letter = _letter("V2014-0072")
    letter["files"][0]["sha256"] = hashlib.sha256(b"other").hexdigest()
    report = postipoiss.load(_package(tmp_path, [letter]), write=True)
    assert any("puudub paketist" in p for p in report.problems)
    assert not OpinionArchiveBinary.objects.exists()


# -- the automatic phase ---------------------------------------------------------------------


def test_auto_apply_files_tier_a_as_a_sent_opinion_dated_by_the_letter(tmp_path, matter):
    postipoiss.load(
        _package(
            tmp_path,
            [
                _letter("V2014-0072", reference="2014_85"),
                _letter("V2014-0080", reference="2014_85", tier="B", letter_date="2014-06-01"),
            ],
        ),
        write=True,
    )
    assert postipoiss.auto_apply(write=False).applied == 1
    assert not Submission.objects.exists()  # the dry run rolled back

    report = postipoiss.auto_apply(write=True)

    assert report.applied == 1 and report.submissions_created == 1
    submission = Submission.objects.get()
    assert submission.matter == matter
    assert submission.status == SubmissionStatus.SENT
    assert timezone.localdate(submission.sent_at) == dt.date(2014, 3, 10)
    assert submission.sent_at_precision == SentAtPrecision.DATE
    assert submission.reference == "V2014-0072"
    assert submission.final_version.sha256 == hashlib.sha256(PDF).hexdigest()
    assert OpinionArchiveMatterLink.objects.filter(matter=matter).count() == 1
    applied = PostipoissPairing.objects.get(letter__kirja_id="V2014-0072")
    assert applied.state == PairingState.APPLIED and applied.basis == PairingBasis.AUTOMATIC
    assert (
        PostipoissPairing.objects.get(letter__kirja_id="V2014-0080").state == PairingState.PENDING
    )


def test_a_sent_opinion_within_seven_days_is_the_same_act(tmp_path, matter):
    postipoiss.load(_package(tmp_path, [_letter("V2014-0072", reference="2014_85")]), write=True)
    postipoiss.auto_apply(write=True)
    other = b"%PDF-1.4\n% a second letter\n"
    postipoiss.load(
        _package(
            tmp_path / "second",
            [_letter("V2014-0075", reference="2014_85", files=(other,), letter_date="2014-03-14")],
            blobs=(other,),
        ),
        write=True,
    )

    report = postipoiss.auto_apply(write=True)

    assert report.joined_existing == 1 and report.submissions_created == 0
    assert Submission.objects.count() == 1
    assert PostipoissPairing.objects.get(letter__kirja_id="V2014-0075").joined_existing


def test_a_letter_without_a_file_is_never_applied_automatically(tmp_path, matter):
    postipoiss.load(
        _package(tmp_path, [_letter("V2014-0072", reference="2014_85", files=())]), write=True
    )
    report = postipoiss.auto_apply(write=True)
    assert report.applied == 0 and report.skipped
    assert not Submission.objects.exists()


def test_a_test_teema_is_refused(tmp_path):
    factories.MatterFactory(
        reference_year=2014, reference_number=85, data_class=MatterDataClass.TEST
    )
    postipoiss.load(_package(tmp_path, [_letter("V2014-0072", reference="2014_85")]), write=True)
    report = postipoiss.auto_apply(write=True)
    assert report.applied == 0
    assert not Submission.objects.exists()


# -- a lawyer's decisions --------------------------------------------------------------------


def _loaded(tmp_path, **kw):
    postipoiss.load(_package(tmp_path, [_letter("V2014-0072", **kw)]), write=True)
    return PostipoissPairing.objects.get()


def test_the_page_lists_waiting_letters_for_a_lawyer(client, tmp_path, matter, specialist):
    _loaded(tmp_path, reference="2014_85", tier="C")
    client.force_login(specialist)
    page = client.get(reverse("haldus:letters"))
    assert page.status_code == 200
    assert "V2014-0072" in page.content.decode()


def test_a_lawyer_accepts_the_proposal(client, tmp_path, matter, specialist):
    pairing = _loaded(tmp_path, reference="2014_85", tier="C")
    client.force_login(specialist)
    client.post(reverse("haldus:letters_decide", kwargs={"pk": pairing.pk}), {"otsus": "kinnita"})
    pairing.refresh_from_db()
    assert pairing.state == PairingState.APPLIED and pairing.basis == PairingBasis.ACCEPTED
    assert pairing.decided_by == specialist
    assert Submission.objects.filter(matter=matter).count() == 1


def test_a_lawyer_chooses_another_teema_by_its_reference(client, tmp_path, matter, specialist):
    other = factories.MatterFactory(reference_year=2014, reference_number=90)
    pairing = _loaded(tmp_path, reference="2014_85", tier="E")
    client.force_login(specialist)
    client.post(
        reverse("haldus:letters_decide", kwargs={"pk": pairing.pk}),
        {"otsus": "vali", "viide": "2014_90"},
    )
    pairing.refresh_from_db()
    assert pairing.matter == other and pairing.basis == PairingBasis.CHOSEN


def test_background_and_taking_it_back(client, tmp_path, specialist):
    pairing = _loaded(tmp_path, tier="F")
    client.force_login(specialist)
    url = reverse("haldus:letters_decide", kwargs={"pk": pairing.pk})
    client.post(url, {"otsus": "taust", "markus": "pole teemat"})
    pairing.refresh_from_db()
    assert pairing.state == PairingState.BACKGROUND and pairing.note == "pole teemat"
    client.post(url, {"otsus": "taasta"})
    pairing.refresh_from_db()
    assert pairing.state == PairingState.PENDING


def test_a_reader_cannot_decide(client, tmp_path, matter, reader):
    pairing = _loaded(tmp_path, reference="2014_85", tier="C")
    client.force_login(reader)
    response = client.post(
        reverse("haldus:letters_decide", kwargs={"pk": pairing.pk}), {"otsus": "kinnita"}
    )
    assert response.status_code == 404
    pairing.refresh_from_db()
    assert pairing.state == PairingState.PENDING


def test_an_applied_letter_is_not_applied_twice(tmp_path, matter, specialist):
    pairing = _loaded(tmp_path, reference="2014_85", tier="C")
    postipoiss.accept(pairing, actor=specialist)
    with refused("Sellel kirjal on juba otsus"):
        postipoiss.accept(pairing, actor=specialist)


def test_an_unknown_reference_is_refused(tmp_path, specialist):
    pairing = _loaded(tmp_path, tier="F")
    with pytest.raises(DomainError):
        postipoiss.choose(pairing, actor=specialist, reference="2099_1")


# -- Statistika and the archive --------------------------------------------------------------


def test_statistika_counts_a_letter_and_the_register_within_seven_days_once(
    tmp_path, matter, specialist
):
    pairing = _loaded(tmp_path, reference="2014_85", tier="C")
    postipoiss.accept(pairing, actor=specialist)
    _imported(matter, "SENT", dt.date(2014, 3, 14), request_deadline=dt.date(2014, 3, 12))

    facts = [f for f in opinion_facts(specialist) if f.matter.matter_id == matter.pk]

    assert len(facts) == 1
    assert facts[0].sent_on == dt.date(2014, 3, 10)
    assert facts[0].deadline == dt.date(2014, 3, 12)  # the register's deadline is kept


def test_statistika_keeps_two_opinions_further_apart(tmp_path, matter, specialist):
    pairing = _loaded(tmp_path, reference="2014_85", tier="C")
    postipoiss.accept(pairing, actor=specialist)
    _imported(matter, "SENT", dt.date(2014, 3, 10) + dt.timedelta(days=SAME_ACT_DAYS + 3))
    facts = [f for f in opinion_facts(specialist) if f.matter.matter_id == matter.pk]
    assert len(facts) == 2


def test_the_two_seven_day_windows_are_the_same():
    assert postipoiss.SAME_ACT_DAYS == SAME_ACT_DAYS


def test_a_word_letter_is_served_under_its_own_suffix():
    binary = OpinionArchiveBinary(sha256="a" * 64, mime_type=DOCX_MIME)
    assert _served_filename(binary).endswith(".docx")
    assert _served_filename(
        OpinionArchiveBinary(sha256="b" * 64, mime_type="application/pdf")
    ).endswith(".pdf")
