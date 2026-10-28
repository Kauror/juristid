"""What each Seis figure counts, in words — the `i` beside Minu asjad's and Osakond's figures.

One module for every explanation either strip carries, so the sentences that
describe a count sit together and can be read against the selectors they
describe (components/seis.html, `help`; UX package 2026-10-28, requirement 6).

**The words follow the code, never the other way round.** Each text below was
written against the selector its figure calls — `work_items.real_deadlines`,
`work_items.band_of`, `dashboard.drafting_matters`,
`department_dashboard.sent_this_week` and the register parameters beside them —
and a definition is never changed to make a sentence true. Where a figure counts
something a reader might not expect (Osakond's strip counts every open Matter the
reader may see, not one department's; Minu asjad's week figure counts rows, not
Matters), the sentence says so rather than the count moving.

Each text names, as far as it matters for that figure: what is counted (Matters,
or individual deadlines and actions, or sent letters), which dates (the current
week runs Monday to Sunday on the Europe/Tallinn calendar, `start_of_iso_week`
and `end_of_iso_week` over the local `today`), and what is left out.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FigureHelp:
    """The explanation one figure carries beside its link (components/seis.html).

    A title and plain paragraphs, nothing else: it is read on hover, on focus
    and on a tap, and by a screen reader as the description of its button.
    ``slug`` makes the element id, so two explained figures on one page could
    never share one.
    """

    slug: str
    title: str
    paragraphs: tuple[str, ...]


def counted(value: int, one: str, many: str) -> str:
    """The caption that reads after ``value``: «1 teema …», «2 teemat …»."""
    return one if value == 1 else many


#: What «koostamisel» means, for both strips' drafting figure: the three halves
#: of `register_filters.opinion_state_q` — the native opinion step while an
#: `Arvamuse tähtaeg` is owed, a DRAFT Submission, or a register row with a blank
#: VÄLJA that no SENT Submission has overtaken.
DRAFTING_MEANING = (
    "Koostamisel tähendab, et teemal on avatud samm «Koostan arvamuse» (või "
    "tööplaani arvamuse samm) ja arvamuse tähtaeg on veel vastamata; või arvamuse "
    "mustand; või vana registri kirje ilma saatmise kuupäevata, mida ükski hiljem "
    "saadetud arvamus ei ole asendanud. Tähtaja kuupäevast sõltumata."
)


# ---------------------------------------------------------------------------
# Minu asjad (app/matters/my_work.py, `build_my_work`)
#
# Neutral about *whose* desk it is: the same strip is a colleague's desk when a
# head opens it, so the texts say «see inimene», never «sina».
# ---------------------------------------------------------------------------

#: «Menetluses olev teema» — the product owner's explanation (review of
#: 2026-10-12), adapted from the earlier interface. Two words differ from what
#: was supplied, both to match what the figure actually counts: the stage list
#: names «Kooskõlastusringil» by its own label, and it ends with the Matters
#: whose `Hetkeseis` is not chosen yet, because `open_matters` counts every open
#: file whatever its stage — a `Määramata` one included. The figure itself is
#: unchanged (docs/adr/0149 amendment of 2026-10-12).
IN_PROGRESS_HELP = FigureHelp(
    slug="menetluses",
    title="Menetluses olev teema",
    paragraphs=(
        "Menetluses olev teema on teema, millega tuleb veel tegeleda – näiteks "
        "koostada arvamus või hoida pärast arvamuse saatmist teemal silma peal.",
        "Menetluses on teemad, mille hetkeseis on idee, kooskõlastusringil, "
        "valitsuses, Riigikogus, jõustumise ootel, Eesti seisukoht koostamisel, "
        "ELi menetluses, ELi õiguse ülevõtmise ootel või muu, ning teemad, mille "
        "hetkeseis on veel määramata.",
        "Kui teemaga ei ole vaja enam tegeleda, näiteks teema on jõustunud või "
        "rohkem ei tegele, kuulub see lõpetatud teemade hulka.",
    ),
)

#: The *Üle tähtaja* band's total: rows, each `is_overdue` — a DO deadline whose
#: whole period has passed, a non-informational `Oluline tähtaeg`, an outstanding
#: `Arvamuse tähtaeg` and a dated feedback wait. Never a WAIT or MONITOR date.
MY_OVERDUE_HELP = FigureHelp(
    slug="minu-ule-tahtaja",
    title="Üle tähtaja",
    paragraphs=(
        "Loeb üksikuid tähtaegu, mitte teemasid: ühel teemal võib neid olla mitu. "
        "Arv on sama, mis allpool jaotises «Üle tähtaja».",
        "Arvesse lähevad möödunud ja täitmata tähtajad: järgmise sammu plaanitud "
        "kuupäev («Plaanis»), kui samm on selle inimese teha, ning oluline tähtaeg, "
        "arvamuse tähtaeg ja kaasamise tagasiside tähtaeg teemadel, mille vastutaja "
        "ta on. Kuu või kvartali täpsusega kuupäev on üle alles siis, kui kogu "
        "periood on möödas.",
        "Möödunud ülevaatamise («Vaatan üle») või oodatav («Oodatav») kuupäev ei ole "
        "tähtaeg: see on jaotises «Sel nädalal».",
    ),
)

#: The *Sel nädalal* band's total (`band_of`): rows whose day falls from today to
#: Sunday — any kind of dated work — plus every past row that is not overdue
#: (a WAIT or MONITOR date come round, a vague DO expectation whose period has
#: ended). A period that has not ended is never this week's (docs/adr/0121 §7).
MY_WEEK_HELP = FigureHelp(
    slug="minu-sel-nadalal",
    title="Tegevused sel nädalal",
    paragraphs=(
        "Loeb ridu allpool jaotises «Sel nädalal», mitte teemasid: ühel teemal võib "
        "olla mitu tegevust või tähtaega.",
        "Arvesse lähevad järgmised sammud («Plaanis», «Vaatan üle», «Oodatav») ning "
        "olulised tähtajad, arvamuse tähtajad ja kaasamise tagasiside tähtajad, mille "
        "päev on tänasest kuni pühapäevani (käesolev nädal esmaspäevast pühapäevani, "
        "Eesti aja järgi). Kuu või kvartali täpsusega kuupäev ei kuulu siia enne, "
        "kui kogu periood on möödas.",
        "Lisaks on siin möödunud kuupäevad, mis ei ole tähtajad – näiteks "
        "ülevaatamise («Vaatan üle») või oodatav («Oodatav») kuupäev, mis on kätte "
        "jõudnud. Möödunud tähtajad on eraldi arvus «üle tähtaja».",
    ),
)

#: `quiet_matters` → `matters_without_action(owner=subject)`: the register's
#: `?tegevus=puudub` over this person's open FULL Matters.
MY_NO_ACTION_HELP = FigureHelp(
    slug="minu-jargmise-tegevuseta",
    title="Järgmise tegevuseta",
    paragraphs=(
        "Loeb teemasid: menetluses olevad teemad, mille vastutaja on see inimene ja "
        "millel ei ole avatud järgmist sammu ega eesolevat olulist tähtaega.",
        "Sellist teemat ei too ükski kuupäev töölauale, seepärast on see eraldi välja toodud.",
    ),
)

#: `OpinionWork.due_this_week`: `drafting_matters` owned by this person whose
#: `Arvamuse tähtaeg` falls Monday to Sunday of the current week, both inclusive.
MY_OPINIONS_DUE_HELP = FigureHelp(
    slug="minu-arvamus-tahtajaga",
    title="Arvamuse tähtaeg sel nädalal",
    paragraphs=(
        "Loeb teemasid, mille vastutaja on see inimene, mille arvamus on veel "
        "koostamisel ja mille arvamuse tähtaeg on käesoleval nädalal (esmaspäevast "
        "pühapäevani, Eesti aja järgi).",
        "Ka selle nädala juba möödunud tähtaeg on siin; varasemate nädalate "
        "tähtajad ei ole. Saadetud arvamusi see arv ei loe: loetakse ainult teemasid, "
        "mille arvamus on veel koostamisel.",
    ),
)

#: `OpinionWork.in_preparation`: `drafting_matters` owned by this person —
#: `opinion_state_q` (docs/adr/0149 §2), counted as Matters.
MY_OPINIONS_DRAFTING_HELP = FigureHelp(
    slug="minu-arvamus-koostamisel",
    title="Arvamus koostamisel",
    paragraphs=(
        "Loeb teemasid, mitte dokumente: menetluses olevad teemad, mille vastutaja on "
        "see inimene ja mille arvamus on koostamisel. Kaks mustandit ühel teemal on "
        "üks teema.",
        DRAFTING_MEANING,
    ),
)


# ---------------------------------------------------------------------------
# Osakond (app/matters/department_dashboard.py, `seis_figures`)
#
# Every figure but «saadetud» runs the register's filters over
# `Matter.objects.visible_to(reader)` and open FULL Matters — every open file
# the reader may see, not one department's. The texts say so.
# ---------------------------------------------------------------------------

#: `?too=hilinenud` — `overdue_items`, reduced to Matters.
DEPT_OVERDUE_HELP = FigureHelp(
    slug="osakond-ule-tahtaja",
    title="Üle tähtaja",
    paragraphs=(
        "Loeb teemasid, mitte tähtaegu: teema on siin üks kord, olgu tal üks või mitu "
        "möödunud tähtaega.",
        "Arvesse lähevad menetluses olevad teemad (kõik, mida sul on õigus näha), "
        "millel on möödunud ja täitmata järgmise sammu plaanitud kuupäev («Plaanis»), "
        "oluline tähtaeg, arvamuse tähtaeg või kaasamise tagasiside tähtaeg. Kuu või "
        "kvartali täpsusega kuupäev on üle alles siis, kui kogu periood on möödas.",
        "Möödunud ülevaatamise («Vaatan üle») või oodatav («Oodatav») kuupäev ei ole "
        "tähtaeg ega too teemat siia.",
    ),
)

#: `?too=tahtaeg-nadalal` — `real_deadlines` in `deadline_window`, which is
#: today to Sunday, reduced to Matters. A feedback wait is not a real deadline.
DEPT_WEEK_HELP = FigureHelp(
    slug="osakond-tahtaeg-sel-nadalal",
    title="Tähtajaga teemad sel nädalal",
    paragraphs=(
        "Loeb teemasid, mitte tähtaegu: menetluses olevad teemad (kõik, mida sul on "
        "õigus näha), millel on tähtaeg tänasest kuni pühapäevani (Eesti aja järgi). "
        "Mitme tähtajaga teema on siin üks kord.",
        "Tähtajaks loetakse järgmise sammu plaanitud kuupäeva («Plaanis»), olulist "
        "tähtaega ja arvamuse tähtaega, päeva täpsusega. Ülevaatamise («Vaatan üle») "
        "ja oodatav («Oodatav») kuupäev ning kaasamise tagasiside tähtaeg siia ei kuulu.",
        "Selle nädala juba möödunud tähtaeg on arvus «üle tähtaja».",
    ),
)

#: `?vastutaja=puudub` over open FULL Matters.
DEPT_UNASSIGNED_HELP = FigureHelp(
    slug="osakond-vastutajata",
    title="Vastutajata",
    paragraphs=(
        "Loeb menetluses olevaid teemasid (kõik, mida sul on õigus näha), millel ei "
        "ole vastutajat.",
    ),
)


def dept_unreviewed_help(window_days: int) -> FigureHelp:
    """`_unreviewed_params`: arrived in the window, no owner, no next action."""
    return FigureHelp(
        slug="osakond-labi-vaatamata",
        title="Uus, läbi vaatamata",
        paragraphs=(
            "Loeb menetluses olevaid teemasid (kõik, mida sul on õigus näha), mille "
            f"saabumise kuupäev on viimase {window_days} päeva sees (kuni tänaseni) ja "
            "millel ei ole veel vastutajat ega järgmist sammu (ka eesolevat olulist "
            "tähtaega).",
            "Eraldi «läbi vaadatud» märget teemal ei ole: arv tugineb neile kolmele tunnusele.",
        ),
    )


#: `?tegevus=puudub` over open FULL Matters, whoever owns them.
DEPT_NO_ACTION_HELP = FigureHelp(
    slug="osakond-jargmise-tegevuseta",
    title="Järgmise tegevuseta",
    paragraphs=(
        "Loeb menetluses olevaid teemasid (kõik, mida sul on õigus näha), millel ei "
        "ole avatud järgmist sammu ega eesolevat olulist tähtaega, olgu vastutaja "
        "määratud või mitte.",
    ),
)

#: `sent_this_week`: SENT Submissions, Monday to today on the local calendar,
#: without `NOT_AN_OPINION_KINDS` (docs/adr/0149 §1, docs/adr/0174).
DEPT_SENT_HELP = FigureHelp(
    slug="osakond-arvamus-saadetud",
    title="Arvamused saadetud sel nädalal",
    paragraphs=(
        "Loeb saadetud arvamusi, mitte teemasid: kui ühel teemal saadeti kaks "
        "arvamust, on neid siin kaks.",
        "Arvesse lähevad arvamused (kõik, mida sul on õigus näha), mille saatmise "
        "kuupäev on käesoleva nädala esmaspäevast tänaseni (Eesti aja järgi). "
        "Koostamisel ja tagasi võetud arvamused ei ole siin, samuti mitte vastus "
        "«märkusi ja ettepanekuid ei ole».",
        "Arvul ei ole nimekirja linki, sest ükski nimekiri ei näita täpselt sama nädalat.",
    ),
)

#: `drafting_matters` — `?arvamus=koostamisel` over open FULL Matters.
DEPT_DRAFTING_HELP = FigureHelp(
    slug="osakond-arvamus-koostamisel",
    title="Arvamus koostamisel",
    paragraphs=(
        "Loeb teemasid, mitte dokumente: menetluses olevad teemad (kõik, mida sul on "
        "õigus näha), mille arvamus on koostamisel. Kaks mustandit ühel teemal on "
        "üks teema.",
        DRAFTING_MEANING,
    ),
)
