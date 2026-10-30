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
Matters), the count stays as it is. The wording itself is the owner's, shortened
on 2026-10-28 and again on 2026-10-30 to one sentence a figure.

Since 2026-10-30 each text is one short sentence that says what is counted. The
finer rules (which dates are deadlines, what is left out) live in the selectors
and their tests, not in the tooltip. Minu asjad's «tegevust sel nädalal» carries
no explanation at all: the owner removed it.
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


# ---------------------------------------------------------------------------
# Minu asjad (app/matters/my_work.py, `build_my_work`)
#
# Neutral about *whose* desk it is: the same strip is a colleague's desk when a
# head opens it, so the texts never say «sina».
# ---------------------------------------------------------------------------

#: `open_matters`: every open file of this person, whatever its stage.
IN_PROGRESS_HELP = FigureHelp(
    slug="menetluses",
    title="Menetluses olev teema",
    paragraphs=(
        "Menetluses olev teema on teema, millega tuleb veel tegeleda – näiteks "
        "koostada arvamus või hoida pärast arvamuse saatmist teemal silma peal.",
    ),
)

#: The *Üle tähtaja* band's total: rows, so one Matter can count more than once.
MY_OVERDUE_HELP = FigureHelp(
    slug="minu-ule-tahtaja",
    title="Üle tähtaja",
    paragraphs=("Loeb tähtaegu, mis on möödas – ühel teemal võib neid olla mitu.",),
)

#: `quiet_matters` → `matters_without_action(owner=subject)`: the register's
#: `?tegevus=puudub` over this person's open FULL Matters.
MY_NO_ACTION_HELP = FigureHelp(
    slug="minu-jargmise-tegevuseta",
    title="Järgmise tegevuseta",
    paragraphs=(
        "Menetluses olevad teemad, millel ei ole avatud järgmist sammu ega eesolevat "
        "olulist tähtaega.",
    ),
)

#: `OpinionWork.due_this_week`: `drafting_matters` owned by this person whose
#: `Arvamuse tähtaeg` falls Monday to Sunday of the current week, both inclusive.
MY_OPINIONS_DUE_HELP = FigureHelp(
    slug="minu-arvamus-tahtajaga",
    title="Arvamuse tähtaeg sel nädalal",
    paragraphs=(
        "Loeb teemasid, mille arvamus on veel koostamisel ja mille arvamuse tähtaeg on "
        "käesoleval nädalal.",
    ),
)

#: `OpinionWork.in_preparation`: `drafting_matters` owned by this person —
#: `opinion_state_q` (docs/adr/0149 §2), counted as Matters.
MY_OPINIONS_DRAFTING_HELP = FigureHelp(
    slug="minu-arvamus-koostamisel",
    title="Arvamus koostamisel",
    paragraphs=("Menetluses olevad teemad, mille arvamus on koostamisel.",),
)


# ---------------------------------------------------------------------------
# Osakond (app/matters/department_dashboard.py, `seis_figures`)
#
# Every figure but «saadetud» runs the register's filters over
# `Matter.objects.visible_to(reader)` and open FULL Matters — every open file
# the reader may see, not one department's.
# ---------------------------------------------------------------------------

#: `?too=hilinenud` — `overdue_items`, reduced to Matters.
DEPT_OVERDUE_HELP = FigureHelp(
    slug="osakond-ule-tahtaja",
    title="Üle tähtaja",
    paragraphs=("Üle tähtaja olevad teemad.",),
)

#: `?too=tahtaeg-nadalal` — `real_deadlines` in `deadline_window`, which is
#: today to Sunday, reduced to Matters.
DEPT_WEEK_HELP = FigureHelp(
    slug="osakond-tahtaeg-sel-nadalal",
    title="Tähtajaga teemad sel nädalal",
    paragraphs=("Menetluses olevad teemad, millel on tähtaeg tänasest kuni pühapäevani.",),
)

#: `?vastutaja=puudub` over open FULL Matters.
DEPT_UNASSIGNED_HELP = FigureHelp(
    slug="osakond-vastutajata",
    title="Vastutajata",
    paragraphs=("Loeb menetluses olevaid teemasid, millel ei ole vastutajat.",),
)


def dept_unreviewed_help(window_days: int) -> FigureHelp:
    """`_unreviewed_params`: arrived in the window, no owner, no next action."""
    return FigureHelp(
        slug="osakond-labi-vaatamata",
        title="Uus, läbi vaatamata",
        paragraphs=(
            "Loeb menetluses olevaid teemasid, mille saabumise kuupäev on viimase "
            f"{window_days} päeva sees (kuni tänaseni) ja millel ei ole veel vastutajat "
            "ega järgmist sammu.",
        ),
    )


#: `?tegevus=puudub` over open FULL Matters, whoever owns them.
DEPT_NO_ACTION_HELP = FigureHelp(
    slug="osakond-jargmise-tegevuseta",
    title="Järgmise tegevuseta",
    paragraphs=(
        "Loeb menetluses olevaid teemasid, millel ei ole avatud järgmist sammu ega "
        "eesolevat olulist tähtaega.",
    ),
)

#: `sent_this_week`: SENT Submissions, Monday to today on the local calendar,
#: without `NOT_AN_OPINION_KINDS` (docs/adr/0149 §1, docs/adr/0174).
DEPT_SENT_HELP = FigureHelp(
    slug="osakond-arvamus-saadetud",
    title="Arvamused saadetud sel nädalal",
    paragraphs=("Loeb saadetud arvamusi.",),
)

#: `drafting_matters` — `?arvamus=koostamisel` over open FULL Matters.
DEPT_DRAFTING_HELP = FigureHelp(
    slug="osakond-arvamus-koostamisel",
    title="Arvamus koostamisel",
    paragraphs=("Menetluses olevad teemad, mille arvamus on koostamisel.",),
)
