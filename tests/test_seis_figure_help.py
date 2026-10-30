"""Minu asjad's and Osakond's Seis figures: clear captions, and an `i` on every one.

UX package of 2026-10-28, requirement 6. Captions and explanations only — no
population, count or link moved — so the tests hold three things:

* **the words** — every figure on both strips carries a `FigureHelp`
  (app/matters/seis_help.py) except Minu asjad's «tegevust sel nädalal», whose
  explanation the owner removed on 2026-10-30; each explanation is one short
  sentence; the captions say what they count, and one reads in the nominative
  singular («1 tegevus», «1 tähtajaga teema», «1 arvamus …»);
* **the meaning the words claim** — a figure the text calls a count of Matters
  counts each Matter once however many deadlines it holds, and the one the text
  calls a count of rows counts each row; Osakond's week runs from today to
  Sunday; *due*, *being written* and *sent* are three populations;
* **the control** — the `i` is a button *beside* the figure's link, never inside
  it, on Osakond as on Minu asjad, and the strips of Saabunud and Statistika grow
  none (tests/test_feedback_and_my_work_ux_oct12.py).

Count-to-list parity is held where it always was: tests/test_dashboard_kpi_links.py,
tests/test_my_work_opinion_kpis.py, tests/test_department_page.py. Every title
here is invented.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

import pytest
from django.urls import reverse
from django.utils import timezone

from app.matters import department_dashboard as dd
from app.matters import seis_help
from app.matters import work_items as wi
from app.matters.my_work import build_my_work
from app.matters.services import create_matter
from app.submissions.services import create_submission
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics
from app.workflow.services import set_next_action

pytestmark = pytest.mark.django_db

#: Wednesday 7 October 2026: the week is Monday 5 to Sunday 11.
WEDNESDAY = dt.date(2026, 10, 7)
MONDAY = dt.date(2026, 10, 5)
THURSDAY = dt.date(2026, 10, 8)
FRIDAY = dt.date(2026, 10, 9)
NEXT_MONDAY = dt.date(2026, 10, 12)


@pytest.fixture
def frozen(monkeypatch):
    """Hold the application's today on WEDNESDAY, for the rendered pages."""
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: WEDNESDAY)
    return WEDNESDAY


def stepped(
    owner: Any,
    title: str,
    on: dt.date,
    *,
    kind: str = ActionKind.DO,
    semantics: str = DateSemantics.DEADLINE,
    precision: str = DatePrecision.EXACT,
) -> Any:
    matter = create_matter(title=title, owner=owner, reference_year=2026, actor=owner)
    set_next_action(
        matter=matter,
        text=f"Samm: {title}",
        kind=kind,
        date_semantics=semantics,
        target_date=on,
        date_precision=precision,
        responsible=owner,
        actor=owner,
    )
    return matter


def drafted(owner: Any, title: str, *, deadline: dt.date | None) -> Any:
    matter = create_matter(
        title=title, owner=owner, actor=owner, reference_year=2026, response_deadline=deadline
    )
    create_submission(matter=matter, title="Arvamuse mustand", actor=owner)
    return matter


def by_key(figures: list[Any]) -> dict[str, Any]:
    return {figure.key: figure for figure in figures}


# ---------------------------------------------------------------------------
# Every figure is explained, and the explanation says what is counted
# ---------------------------------------------------------------------------


def test_every_minu_asjad_figure_carries_its_explanation(specialist) -> None:
    work = build_my_work(specialist, today=WEDNESDAY)
    helps = {figure.key: figure.help for figure in work.seis}

    assert helps == {
        "open": seis_help.IN_PROGRESS_HELP,
        "overdue": seis_help.MY_OVERDUE_HELP,
        # The owner removed this one on 2026-10-30.
        "week": None,
        "no_action": seis_help.MY_NO_ACTION_HELP,
        "opinions_week": seis_help.MY_OPINIONS_DUE_HELP,
        "opinions_drafting": seis_help.MY_OPINIONS_DRAFTING_HELP,
    }


def test_every_osakond_figure_carries_its_explanation(department_head) -> None:
    figures = dd.seis_figures(department_head, WEDNESDAY)

    assert all(figure.help is not None for figure in figures)
    assert by_key(figures)["week"].help is seis_help.DEPT_WEEK_HELP
    assert by_key(figures)["sent"].help is seis_help.DEPT_SENT_HELP
    assert str(dd.UNREVIEWED_WINDOW_DAYS) in " ".join(by_key(figures)["unreviewed"].help.paragraphs)
    slugs = [figure.help.slug for figure in figures]
    assert len(set(slugs)) == len(slugs)


def test_the_slugs_never_collide_between_the_two_strips(specialist, department_head) -> None:
    mine = {f.help.slug for f in build_my_work(specialist, today=WEDNESDAY).seis if f.help}
    theirs = {f.help.slug for f in dd.seis_figures(department_head, WEDNESDAY)}
    assert not mine & theirs


#: Every explanation on either strip. The unreviewed one is built with the
#: window the page uses.
ALL_HELPS = [
    seis_help.IN_PROGRESS_HELP,
    seis_help.MY_OVERDUE_HELP,
    seis_help.MY_NO_ACTION_HELP,
    seis_help.MY_OPINIONS_DUE_HELP,
    seis_help.MY_OPINIONS_DRAFTING_HELP,
    seis_help.DEPT_OVERDUE_HELP,
    seis_help.DEPT_WEEK_HELP,
    seis_help.DEPT_UNASSIGNED_HELP,
    seis_help.dept_unreviewed_help(dd.UNREVIEWED_WINDOW_DAYS),
    seis_help.DEPT_NO_ACTION_HELP,
    seis_help.DEPT_SENT_HELP,
    seis_help.DEPT_DRAFTING_HELP,
]


@pytest.mark.parametrize("help_text", ALL_HELPS, ids=lambda h: h.slug)
def test_each_explanation_is_one_short_sentence(help_text) -> None:
    """The owner's review of 2026-10-30: the long explanations were not read."""
    assert len(help_text.paragraphs) == 1
    text = help_text.paragraphs[0]
    assert text.endswith(".")
    assert len(text.split()) <= 30, text


@pytest.mark.parametrize(
    ("help_text", "unit"),
    [
        # Matters. The wording is the owner's (2026-10-30); what is checked is
        # that the sentence still names the unit counted.
        (seis_help.DEPT_OVERDUE_HELP, "teemad"),
        (seis_help.DEPT_WEEK_HELP, "teemad"),
        (seis_help.DEPT_DRAFTING_HELP, "teemad"),
        (seis_help.MY_OPINIONS_DRAFTING_HELP, "teemad"),
        (seis_help.MY_OPINIONS_DUE_HELP, "Loeb teemasid"),
        # Rows and letters.
        (seis_help.MY_OVERDUE_HELP, "Loeb tähtaegu"),
        (seis_help.DEPT_SENT_HELP, "Loeb saadetud arvamusi"),
    ],
)
def test_each_explanation_names_what_it_counts(help_text, unit) -> None:
    assert unit in help_text.paragraphs[0]


def test_the_week_texts_name_the_week() -> None:
    assert "tänasest kuni pühapäevani" in seis_help.DEPT_WEEK_HELP.paragraphs[0]
    assert "käesoleval nädalal" in seis_help.MY_OPINIONS_DUE_HELP.paragraphs[0]


def test_sent_due_and_drafting_texts_keep_their_populations_apart() -> None:
    sent = " ".join(seis_help.DEPT_SENT_HELP.paragraphs)
    due = " ".join(seis_help.MY_OPINIONS_DUE_HELP.paragraphs)
    drafting = " ".join(seis_help.DEPT_DRAFTING_HELP.paragraphs)
    assert "saadetud arvamusi" in sent
    assert "veel koostamisel" in due
    assert "on koostamisel" in drafting


# ---------------------------------------------------------------------------
# Singular and plural
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("count", "caption"), [(1, "tegevus sel nädalal"), (2, "tegevust sel nädalal")]
)
def test_minu_asjad_week_caption_agrees_with_its_number(specialist, count, caption) -> None:
    for index in range(count):
        stepped(specialist, f"Nädala töö {index}", THURSDAY)

    week = by_key(build_my_work(specialist, today=WEDNESDAY).seis)["week"]

    assert (week.value, week.caption) == (count, caption)


@pytest.mark.parametrize(
    ("count", "week", "drafting"),
    [
        (1, "tähtajaga teema sel nädalal", "arvamus koostamisel"),
        (2, "tähtajaga teemat sel nädalal", "arvamust koostamisel"),
    ],
)
def test_osakond_captions_agree_with_their_number(
    department_head, specialist, count, week, drafting
) -> None:
    for index in range(count):
        drafted(specialist, f"Arvamus {index}", deadline=FRIDAY)

    figures = by_key(dd.seis_figures(department_head, WEDNESDAY))

    assert (figures["week"].value, figures["week"].caption) == (count, week)
    assert (figures["drafting"].value, figures["drafting"].caption) == (count, drafting)


def test_osakond_zero_reads_in_the_partitive(department_head) -> None:
    figures = by_key(dd.seis_figures(department_head, WEDNESDAY))

    assert figures["week"].caption == "tähtajaga teemat sel nädalal"
    assert figures["sent"].caption == "arvamust saadetud sel nädalal"
    assert figures["unreviewed"].caption == "uut läbi vaatamata"


# ---------------------------------------------------------------------------
# What the words claim is what the selectors do
# ---------------------------------------------------------------------------


def test_osakond_week_counts_matters_from_today_and_minu_asjad_counts_rows(
    department_head, specialist
) -> None:
    """One desk, read by both strips.

    * a DO due Thursday — a deadline this week on both;
    * a WAIT expected Friday — a row of Minu asjad's *Sel nädalal*, never an
      Osakond deadline (`real_deadlines`);
    * a DO due Monday, missed — overdue on both, in neither week figure;
    * a DO due next Monday — in neither.
    """
    thursday = stepped(specialist, "Neljapäevane tähtaeg", THURSDAY)
    stepped(
        specialist,
        "Ootame reedeks",
        FRIDAY,
        kind=ActionKind.WAIT,
        semantics=DateSemantics.EXPECTED_AROUND,
    )
    stepped(specialist, "Esmaspäevane tähtaeg", MONDAY)
    stepped(specialist, "Järgmise nädala tähtaeg", NEXT_MONDAY)

    department = by_key(dd.seis_figures(department_head, WEDNESDAY))
    ids = wi.work_population_ids(department_head, wi.WORK_DEADLINE_THIS_WEEK, today=WEDNESDAY)
    assert ids == {thursday.pk}
    assert department["week"].value == 1
    assert department["overdue"].value == 1

    mine = by_key(build_my_work(specialist, today=WEDNESDAY).seis)
    assert (mine["week"].value, mine["week"].caption) == (2, "tegevust sel nädalal")
    assert mine["overdue"].value == 1


def test_sent_due_and_drafting_are_three_populations(
    department_head, specialist, send_opinion
) -> None:
    """A draft due this week, a draft with no deadline, an opinion sent today."""
    drafted(specialist, "Selle nädala arvamus", deadline=FRIDAY)
    drafted(specialist, "Tähtajata arvamus", deadline=None)
    send_opinion(create_matter(title="Saadetud", owner=specialist, actor=specialist))

    today = timezone.localdate()
    department = by_key(dd.seis_figures(department_head, today))
    assert department["sent"].value == 1
    assert department["drafting"].value == 2

    mine = by_key(build_my_work(specialist, today=WEDNESDAY).seis)
    assert (mine["opinions_week"].value, mine["opinions_drafting"].value) == (1, 2)
    assert mine["opinions_week"].caption == "arvamus tähtajaga sel nädalal"


@pytest.fixture
def send_opinion(capture_evidence, specialist):
    """A SENT opinion, dated now, with the evidence the database insists on."""
    from app.submissions.enums import SentAtPrecision, SubmissionStatus
    from tests import factories

    def make(matter):
        version = capture_evidence(matter, b"%PDF-1.4 synthetic", "arvamus.pdf", "application/pdf")
        return factories.SubmissionFactory(
            matter=matter,
            title="Sünteetiline arvamus",
            status=SubmissionStatus.SENT,
            sent_at=timezone.now(),
            sent_at_precision=SentAtPrecision.TIMESTAMP,
            final_version=version,
        )

    return make


# ---------------------------------------------------------------------------
# The control: a button beside each link, never inside it
# ---------------------------------------------------------------------------


def _strip_markup(body: str) -> str:
    start = body.index('<div class="seis')
    return body[start : body.index("</div>", start)]


def _assert_explained_and_never_nested(body: str, unexplained: int = 0) -> None:
    """``unexplained`` is how many drawn figures carry no `i` on purpose."""
    strip = _strip_markup(body)
    figures = strip.count('class="seis__figure"')
    assert figures >= 1
    assert strip.count("data-seishelp") == figures - unexplained
    assert strip.count('class="seis__info"') == figures - unexplained
    # A tooltip is plain text: no link or button inside an explanation…
    for tip in re.findall(r'<span class="seis__tip".*?</span>\s*</span>', strip, re.S):
        assert "<a " not in tip and "<button" not in tip
    # …and no button or link inside a figure's link.
    assert not re.search(r"<a [^>]*>(?:(?!</a>).)*<(?:a|button)\b", strip, re.S)
    # Each button names and describes itself by an element that exists.
    for described in re.findall(r'aria-describedby="([^"]+)"', strip):
        assert f'id="{described}"' in strip
    for label in re.findall(r'class="seis__info"\s+aria-label="([^"]+)"', strip):
        assert label.startswith("Selgitus: ")


def test_osakond_draws_an_explanation_beside_every_figure(client, department_head, frozen):
    stepped(department_head, "Neljapäevane tähtaeg", THURSDAY)
    client.force_login(department_head)

    body = client.get(reverse("matters:department")).content.decode()

    _assert_explained_and_never_nested(body)
    # Osakond shows its zeros, so all seven are there, each with its words.
    assert _strip_markup(body).count('class="seis__info"') == 7
    for text in seis_help.DEPT_WEEK_HELP.paragraphs:
        assert text in body
    assert '<span class="seis__caption">tähtajaga teema sel nädalal</span>' in body
    assert "tähtaeg sel nädalal</span>" not in body
    assert "arvamust välja sel nädalal" not in body


def test_minu_asjad_draws_an_explanation_beside_every_figure(client, specialist, frozen):
    stepped(specialist, "Neljapäevane tähtaeg", THURSDAY)
    drafted(specialist, "Selle nädala arvamus", deadline=FRIDAY)
    client.force_login(specialist)

    body = client.get(reverse("matters:my_work")).content.decode()

    # «tegevust sel nädalal» is drawn and is the one figure without an `i`.
    _assert_explained_and_never_nested(body, unexplained=1)
    assert '<span class="seis__caption">tegevust sel nädalal</span>' in body
    assert "seis-selgitus-minu-sel-nadalal" not in body
    assert '<span class="seis__caption">arvamus tähtajaga sel nädalal</span>' in body
    assert '<span class="seis__caption">arvamus koostamisel</span>' in body
    assert "arvamust kokku" not in body
