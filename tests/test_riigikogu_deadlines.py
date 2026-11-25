"""The invitation deadline reader and koda's name in the Riigikogu's data (ADR 0225 §1, §5).

No database. The evaluation set is `tests/fixtures/riigikogu/deadline_cases.json`
(see `app.riigikogu.evaluation`): 205 real letters' date sentences, sanitised and
reviewed by hand, and 25 synthetic cases for what the register does not contain.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.riigikogu import deadlines, evaluation, organisation
from app.riigikogu.enums import DeadlineKind, DocumentKind, ExtractionStatus, KodaRole

CASES = evaluation.load()


def _one(text: str, letter: dt.date | None = dt.date(2026, 11, 10)) -> deadlines.Extraction:
    return deadlines.extract(text, letter_date=letter)


# -- the measured bar -----------------------------------------------------------------


def test_the_evaluation_set_is_large_enough_and_says_where_each_case_came_from():
    real = [c for c in CASES if c["origin"] == "riigikogu-kiri"]
    assert len(real) >= 50
    assert {c["split"] for c in real} == {"dev", "heldout"}
    assert len([c for c in CASES if c["origin"] == "synteetiline"]) >= 20


def test_the_public_fixture_carries_no_contact_details():
    import re

    for case in CASES:
        assert "@" not in case["text"], case["id"]
        assert not re.search(r"\b6\d{2}\s?\d{4}\b", case["text"]), case["id"]


@pytest.mark.parametrize("kind", [DeadlineKind.OPINION_REQUEST, DeadlineKind.ATTENDANCE])
def test_no_wrong_date_is_ever_accepted(kind):
    score = evaluation.measure(CASES, kind=kind)
    assert score.wrong_accepted == 0, score.failures
    assert score.doubt_lost == 0, score.failures


def test_opinion_dates_are_read_on_at_least_95_percent_of_unambiguous_letters():
    score = evaluation.measure(CASES, kind=DeadlineKind.OPINION_REQUEST)
    assert score.extractable >= 150
    assert score.coverage >= 0.95, score.failures
    held_out = evaluation.measure(CASES, kind=DeadlineKind.OPINION_REQUEST, split="heldout")
    assert held_out.coverage >= 0.95, held_out.failures


def test_the_amendment_deadline_mentioned_in_a_letter_is_never_taken_for_kodas():
    score = evaluation.measure(CASES, kind=DeadlineKind.AMENDMENTS)
    assert score.wrong_accepted == 0, score.failures


# -- the rules, one by one ------------------------------------------------------------


def test_a_letter_sentence_with_the_initiation_date_yields_only_the_deadline():
    found = _one(
        "Riigikogu menetluses on Vabariigi Valitsuse poolt 09.11.26 algatatud eelnõu (1082 SE). "
        "Teil on võimalus avaldada eelnõu kohta arvamust ja esitada ettepanekuid õiguskomisjonile "
        "hiljemalt 24.11.26 e-posti aadressil."
    )
    opinion = found.date_of(DeadlineKind.OPINION_REQUEST)
    assert opinion is not None
    assert (opinion.day, opinion.status) == (dt.date(2026, 11, 24), ExtractionStatus.FOUND)
    assert "hiljemalt 24.11.26" in opinion.evidence


def test_different_actions_keep_different_dates():
    found = _one("Palume teatada osalemisest 20.11 ja saata arvamus 24.11.")
    assert found.date_of(DeadlineKind.ATTENDANCE).day == dt.date(2026, 11, 20)
    assert found.date_of(DeadlineKind.OPINION_REQUEST).day == dt.date(2026, 11, 24)


def test_a_sitting_is_a_hearing_not_a_deadline():
    found = _one("Arvamust ootame 24.11, istung toimub 26.11.")
    assert found.hearing_on == dt.date(2026, 11, 26)
    assert [d.kind for d in found.dates] == [DeadlineKind.OPINION_REQUEST]


def test_a_time_is_kept_only_when_written_and_a_date_only_stays_date_only():
    assert _one("Palume ettepanekud hiljemalt 24.11 kell 12.00.").dates[0].time == dt.time(12, 0)
    assert _one("Palume ettepanekud hiljemalt 24.11 kella 17.15-ks.").dates[0].time == dt.time(
        17, 15
    )
    assert _one("Palume arvamust hiljemalt 24.11.2026.").dates[0].time is None


def test_a_weekday_is_never_turned_into_a_date():
    found = _one("Palume vastata reedeks.")
    opinion = found.date_of(DeadlineKind.OPINION_REQUEST)
    assert opinion.day is None
    assert opinion.status == ExtractionStatus.CHECK
    assert "nädalapäev" in opinion.doubt


def test_a_year_is_taken_from_the_letter_only_when_that_is_plain():
    across = deadlines.extract(
        "Palume arvamust hiljemalt 15. jaanuariks.", letter_date=dt.date(2026, 12, 18)
    )
    assert across.dates[0].day == dt.date(2027, 1, 15)
    unknown = deadlines.extract("Palume arvamust hiljemalt 15. jaanuariks.", letter_date=None)
    assert unknown.dates[0].status == ExtractionStatus.CHECK
    assert unknown.dates[0].day is None


def test_a_deadline_before_the_letter_needs_checking():
    found = _one("Palume oma arvamus esitada hiljemalt 05.11.2026.")
    assert found.status == ExtractionStatus.CHECK
    assert "varasem" in found.dates[0].doubt


def test_two_different_opinion_dates_without_an_extension_need_checking():
    found = _one("Palume arvamust hiljemalt 24.11.2026. Palume arvamust hiljemalt 30.11.2026.")
    assert found.status == ExtractionStatus.CHECK
    assert "mitu kuupäeva" in found.dates[0].doubt


def test_an_extension_is_recognised_as_one():
    found = _one("Pikendame varasemat tähtaega 28. novembrini.")
    opinion = found.date_of(DeadlineKind.OPINION_REQUEST)
    assert opinion.day == dt.date(2026, 11, 28)
    assert opinion.extension


def test_a_cancellation_is_recognised():
    assert _one("Teatame, et 26.11 kavandatud arutelu jääb ära ja kutse tühistatakse.").cancelled


def test_an_empty_text_is_unreadable_not_dateless():
    assert _one("").status == ExtractionStatus.UNREADABLE


# -- koda's name ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "parties",
    [
        "Finantsinspektsioon, Eesti Kaubandus-Tööstuskoda, Eesti Advokatuur",
        "Andmekaitse Inspektsioon, Eesti Kaubandus- ja Tööstuskoda, Häirekeskus",
        "Eesti Kaubandus- Tööstuskoda - Mait Palts",
        "Eesti Kaubandus–Tööstuskoda",
    ],
)
def test_kodas_name_variants_are_recognised(parties):
    assert organisation.koda_parties(parties)


def test_the_agricultural_chamber_is_not_koda():
    parties = "Eesti Põllumajandus-Kaubanduskoda, Eesti Linnade ja Valdade Liit"
    assert organisation.koda_parties(parties) == []
    assert (
        organisation.koda_role(direction="VALJA", parties=parties, title="Kaasamiskiri")
        == KodaRole.NONE
    )


def test_an_addressee_is_not_a_mention_and_a_mention_is_not_an_invitation():
    addressed = organisation.koda_role(
        direction="VALJA",
        parties="Eesti Kaubandus-Tööstuskoda, Eesti Pank",
        title="Kaasamiskiri 1082 SE",
    )
    assert addressed == KodaRole.ADDRESSEE
    mentioned = organisation.koda_role(
        direction="VALJA",
        parties="Eesti Pank",
        title="Vastus Eesti Kaubandus-Tööstuskoja pöördumisele",
    )
    assert mentioned == KodaRole.MENTIONED
    assert (
        organisation.classify(role=mentioned, title="…", document_type="letterDocument")
        != DocumentKind.INVITATION
    )


def test_kodas_own_letter_is_its_opinion_not_an_invitation():
    role = organisation.koda_role(
        direction="SISSE", parties="Eesti Kaubandus-Tööstuskoda - Mait Palts", title="x"
    )
    assert role == KodaRole.AUTHOR
    assert (
        organisation.classify(role=role, title="x", document_type="letterDocument")
        == DocumentKind.KODA_OPINION
    )


def test_a_committee_notice_without_a_request_is_a_notification():
    kind = organisation.classify(
        role=KodaRole.ADDRESSEE,
        title="Ehitusseadustiku muutmise seaduse eelnõu (655 SE) menetlemine Riigikogus",
        document_type="letterDocument",
        text="Eelnõu võeti menetlusse. Teavitatud huvirühmad ja organisatsioonid: …",
    )
    assert kind == DocumentKind.NOTIFICATION
    asked = organisation.classify(
        role=KodaRole.ADDRESSEE,
        title="Ehitusseadustiku muutmise seaduse eelnõu (655 SE) menetlemine Riigikogus",
        document_type="letterDocument",
        text="Kui Teil on ettepanekuid, palun esitage need hiljemalt 11. novembriks.",
    )
    assert asked == DocumentKind.INVITATION
