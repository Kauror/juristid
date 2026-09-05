"""The deterministic text signals behind «Seotud materjalid», with no database.

These are the rules a lawyer reads on the page as «Sama õigusakt: pakendiseadus»
or «Pealkirjas kordub: pakendijäätmed». They have to be right in Python, on any
machine, before PostgreSQL is asked anything — which is also why this file needs
no database: every function under test is a pure function of its strings.
"""

from __future__ import annotations

import pytest

from app.related_materials import text

# ---------------------------------------------------------------------------
# Named acts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Jäätmeseaduse muutmise seaduse eelnõu", ["jäätmeseadus"]),
        (
            "Jäätmeseaduse ja pakendiseaduse muutmise seadus",
            ["jäätmeseadus", "pakendiseadus"],
        ),
        ("Pakendiseaduse muutmise seaduse eelnõu", ["pakendiseadus"]),
        ("Äriseadustiku muutmise seaduse eelnõu", ["äriseadustik"]),
        (
            "Karistusseadustiku ja väärteomenetluse seadustiku muutmine",
            ["karistusseadustik", "väärteomenetluse seadustik"],
        ),
        ("Töölepingu seaduse muutmise seaduse eelnõu", ["töölepingu seadus"]),
        (
            "Riigihangete seaduse ja audiitortegevuse seaduse muutmise seadus",
            ["riigihangete seadus", "audiitortegevuse seadus"],
        ),
        ("Koja arvamus töölepingu seaduse eelnõule", ["töölepingu seadus"]),
        ("Kohaliku omavalitsuse korralduse seaduse eelnõu", ["omavalitsuse korralduse seadus"]),
        ("Elektrituruseaduse muutmine", ["elektrituruseadus"]),
        ("Vabariigi Valitsuse seaduse muutmine", ["valitsuse seadus"]),
    ],
)
def test_named_acts_are_recognised_in_both_shapes(title, expected):
    assert [act.display for act in text.legal_instruments(title)] == expected


@pytest.mark.parametrize(
    "title",
    [
        "Seaduse eelnõu",
        "Uue seaduse eelnõu",
        "Arvamus seaduse kohta",
        "Ettepanekud seaduse muutmiseks",
        "Eelnõu ja seletuskiri",
        "Metsanduse arengukava 2030",
        "Seadusandluse ülevaade",
        "Seaduseelnõu kooskõlastamine",
        "Vaidlustatud seaduse rakendamine",
    ],
)
def test_boilerplate_is_never_an_act(title):
    """`seadus` on its own, or behind a procedural word, names nothing."""
    assert text.legal_instruments(title) == []


def test_the_same_act_in_two_cases_is_one_act():
    ours = text.legal_instruments("Pakendiseaduse muutmine")
    theirs = text.legal_instruments("Pakendiseadus ja pakendijäätmed")
    assert [act.display for act in text.shared_instruments(ours, theirs)] == ["pakendiseadus"]


def test_a_shared_act_is_reported_in_our_spelling():
    ours = text.legal_instruments("Töölepingu seadus")
    theirs = text.legal_instruments("Koja arvamus töölepingu seaduse eelnõule")
    shared = text.shared_instruments(ours, theirs)
    assert [act.display for act in shared] == ["töölepingu seadus"]


def test_different_acts_share_nothing():
    ours = text.legal_instruments("Jäätmeseaduse muutmine")
    theirs = text.legal_instruments("Käibemaksuseaduse muutmine")
    assert text.shared_instruments(ours, theirs) == []


def test_an_act_is_reported_once_however_often_the_title_repeats_it():
    found = text.legal_instruments("Pakendiseadus, pakendiseaduse ja pakendiseadust")
    assert [act.display for act in found] == ["pakendiseadus"]


# ---------------------------------------------------------------------------
# Subject words
# ---------------------------------------------------------------------------


def test_subject_terms_drop_the_scaffolding_of_a_legal_title():
    terms = text.subject_terms("Jäätmeseaduse muutmise seaduse eelnõu seletuskiri")
    assert [term.display for term in terms] == ["jäätmeseaduse"]


def test_subject_terms_keep_the_words_that_say_what_it_is_about():
    terms = text.subject_terms(
        "Pakendijäätmete ringmajanduse eelnõu kooskõlastamine ministeeriumile"
    )
    assert [term.display for term in terms] == ["pakendijäätmete", "ringmajanduse"]


def test_subject_terms_merge_inflections_of_one_word():
    terms = text.subject_terms("Pakendijäätmed", "pakendijäätmete käitlus")
    displays = [term.display for term in terms]
    assert displays.count("pakendijäätmed") + displays.count("pakendijäätmete") == 1


def test_subject_terms_are_bounded():
    words = " ".join(f"pikksõna{letter * 2}" for letter in "abcdefghijklmnopqrst")
    assert len(text.subject_terms(words, limit=8)) == 8


def test_a_subject_term_carries_a_prefix_the_simple_vector_can_be_asked_for():
    (term,) = text.subject_terms("Pakendijäätmete")
    assert term.prefix == "pakendijäätme"
    assert term.key == "pakendijaatme"


def test_short_and_generic_words_are_not_subject_terms():
    assert text.subject_terms("ELi ja Eesti Vabariigi Valitsuse otsus") == []


# ---------------------------------------------------------------------------
# Words matching across inflections
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("pakendijäätmed", "pakendijäätmete"),
        ("ringmajandus", "ringmajanduse"),
        ("elektrituru", "elektriturul"),
        ("jäätmeseadus", "jäätmeseaduse"),
    ],
)
def test_inflections_of_one_word_are_the_same_word(first, second):
    assert text.same_word(text.stem(first), text.stem(second))


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("ettevõtja", "ettevõtlus"),
        ("pakend", "pakendijäätmed"),
        ("metsandus", "metsaseadus"),
        ("maksu", "maksuvaba"),
    ],
)
def test_different_words_are_not_the_same_word(first, second):
    assert not text.same_word(text.stem(first), text.stem(second))


def test_matching_terms_finds_our_words_in_their_text():
    terms = text.subject_terms("Pakendijäätmete ringmajandus")
    found = text.matching_terms(terms, "Arvamus pakendijäätmete käitlemise kohta")
    assert [term.display for term in found] == ["pakendijäätmete"]


def test_matching_terms_finds_nothing_in_unrelated_text():
    terms = text.subject_terms("Pakendijäätmete ringmajandus")
    assert text.matching_terms(terms, "Metsanduse arengukava aastani 2030") == []


def test_generic_words_are_recognised_with_and_without_diacritics():
    assert text.is_generic("eelnõu")
    assert text.is_generic("eelnou")
    assert text.is_generic("Kooskõlastamine")
    assert not text.is_generic("pakendijäätmed")


def test_format_list_keeps_a_reason_line_short():
    assert text.format_list(["a", "b", "c", "d"]) == "a, b, c"
