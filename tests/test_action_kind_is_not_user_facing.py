"""TEEN / OOTAN / JÄLGIN is not a concept this product asks anybody to hold.

ADR 0052 stopped the Teema composer asking a lawyer to classify their own work.
This module is the other half of that decision (ADR 0054): the classification is
not *displayed* either — not on Minu asjad, not on a colleague's work page, not
in the register table, not in a search result, and not as a published statistic.

Two things this suite is careful about, because both are ways the same fix goes
wrong:

* **The domain is untouched.** `ActionKind.DO`, `WAIT` and `MONITOR` still exist,
  are still stored, still decide what can go overdue and what is merely ripe for
  a look, and still carry the provenance of an imported register instruction. A
  test that proved the labels were gone by proving the kinds were gone would have
  proved the wrong thing, so every rendering assertion below is paired with one
  that reads the record back out of the database.
* **Date meaning is not classification.** TÄHTAEG, VAATAN ÜLE, OODATAV AEG and
  OLULINE TÄHTAEG say what the *date* is, which is the one thing a bare `27.08`
  cannot. Those stay, and the tests assert they stay.
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from app.matters import selectors
from app.matters import work_items as wi
from app.matters.my_work import build_my_work
from app.matters.services import create_matter
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics
from app.workflow.models import NextAction
from app.workflow.services import set_next_action

pytestmark = pytest.mark.django_db

#: The three words as the chip printed them. The stored labels are `Teen`,
#: `Ootan` and `Jälgin`; both spellings are checked wherever a page is read.
RETIRED = ("TEEN", "OOTAN", "JÄLGIN")

#: A lawyer may well write "ootan ministeeriumi vastust", and that sentence must
#: survive — it is the person's own words, not a category the system applied. So
#: these fixtures avoid the verbs, and one test below asserts a step that does
#: use them is printed verbatim.
DO_TEXT = "Koosta ja saada koja arvamus"
WAIT_TEXT = "vaata üle septembris, kui varem infot ei tule"
MONITOR_TEXT = "vaata rakendusaktide koostamine üle"


@pytest.fixture
def today():
    return timezone.localdate()


@pytest.fixture
def three_steps(specialist, today):
    """One of each kind, each on a date that keeps it in a predictable band.

    «Sel nädalal» is the *calendar* week, cut by the calendar and ending on
    Sunday (`end_of_iso_week`, ADR 0046). So «two days from today» is inside it
    from Monday to Friday and outside it at the weekend, and this fixture's
    review steps silently moved into `Järgmised 30 päeva` on a Saturday — a
    failure with no bug behind it, on a test whose subject is which band a
    *kind* lands in rather than which day CI runs.

    Clamped to the end of the week instead. The date still means what the test
    says it means — a review later this week — on every day of the week.
    """
    later_this_week = min(today + timedelta(days=2), wi.end_of_iso_week(today))
    steps = {}
    for kind, semantics, text, target in (
        (ActionKind.DO, DateSemantics.DEADLINE, DO_TEXT, today - timedelta(days=3)),
        (ActionKind.WAIT, DateSemantics.REVIEW_ON, WAIT_TEXT, later_this_week),
        (ActionKind.MONITOR, DateSemantics.REVIEW_ON, MONITOR_TEXT, later_this_week),
    ):
        matter = create_matter(
            title=f"Teema {kind.value}",
            owner=specialist,
            reference_year=2026,
        )
        steps[kind.value] = set_next_action(
            matter=matter,
            text=text,
            kind=kind,
            date_semantics=semantics,
            target_date=target,
            date_precision=DatePrecision.EXACT,
            actor=specialist,
        )
    return steps


def body_of(response) -> str:
    assert response.status_code == 200, response.status_code
    return response.content.decode()


def items_by_text(user, today) -> dict:
    return {
        item.text: item for band in build_my_work(user, today=today).bands for item in band.items
    }


# ---------------------------------------------------------------------------
# 1-3. Each row renders its sentence and not its kind
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "text", "retired"),
    (
        (ActionKind.DO, DO_TEXT, "TEEN"),
        (ActionKind.WAIT, WAIT_TEXT, "OOTAN"),
        (ActionKind.MONITOR, MONITOR_TEXT, "JÄLGIN"),
    ),
)
def test_a_work_row_renders_its_sentence_and_not_its_kind(
    signed_in, three_steps, kind, text, retired
):
    body = body_of(signed_in.get(reverse("matters:my_work")))

    assert text in body, "the step's own sentence is the row"
    assert retired not in body, retired
    assert three_steps[kind.value].get_kind_display() not in body


def test_minu_asjad_renders_no_mode_chip_at_all(signed_in, three_steps):
    """Not "no label" — no element. A chip with an empty string is still a chip."""
    body = body_of(signed_in.get(reverse("matters:my_work")))

    assert 'class="mode' not in body
    assert "modechip" not in body


def test_an_undated_step_is_a_sentence_too(signed_in, specialist):
    """The Kuupäevata rail carried the same chip and lost it with the timeline."""
    matter = create_matter(title="Kuupäevata teema", owner=specialist, reference_year=2026)
    set_next_action(
        matter=matter,
        text="selgita välja, kes eelnõu eest vastutab",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=None,
        actor=specialist,
    )

    body = body_of(signed_in.get(reverse("matters:my_work")))

    assert "selgita välja, kes eelnõu eest vastutab" in body
    assert 'class="mode' not in body
    for retired in RETIRED:
        assert retired not in body, retired


def test_a_lawyers_own_words_survive_even_when_they_are_the_retired_verbs(
    signed_in, specialist, today
):
    """«ootan 2. lugemist» is a sentence somebody wrote, not a classification.

    The distinction the whole change rests on: the product stopped *applying* a
    category, and never edits what a person typed.
    """
    matter = create_matter(title="Lugemist ootav eelnõu", owner=specialist, reference_year=2026)
    set_next_action(
        matter=matter,
        text="vaata üle septembris, ootan 2. lugemist",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=today + timedelta(days=1),
        date_precision=DatePrecision.EXACT,
        actor=specialist,
    )

    body = body_of(signed_in.get(reverse("matters:my_work")))

    assert "vaata üle septembris, ootan 2. lugemist" in body
    assert 'class="mode' not in body


# ---------------------------------------------------------------------------
# 4. The date still says what it means
# ---------------------------------------------------------------------------


def test_the_date_cell_still_says_what_the_date_means(signed_in, three_steps):
    """TÄHTAEG and VAATAN ÜLE are about the date, and they stay (ADR 0054)."""
    body = body_of(signed_in.get(reverse("matters:my_work")))

    assert wi.MEANING_DEADLINE in body
    assert wi.MEANING_REVIEW in body


def test_the_register_row_still_qualifies_its_date(signed_in, three_steps):
    body = body_of(signed_in.get(reverse("matters:matter_list")))

    assert "Tähtaeg" in body
    assert "Vaatan üle" in body
    for retired in RETIRED:
        assert retired not in body, retired


def test_a_search_result_names_the_step_without_classifying_it(signed_in, three_steps):
    """The «vaste» line read `· jälgin: vaata rakendusaktide…`.

    It reads `· järgmiseks:` now — the product's own name for the field, which is
    the heading on the Teema row this text comes from, not a new synonym for the
    retired category (ADR 0054).
    """
    from app.search.indexing import rebuild_all

    rebuild_all()
    body = body_of(signed_in.get(reverse("search:search"), {"q": "Teema"}))

    assert "järgmiseks:" in body
    for retired in ("jälgin:", "ootan:", "teen:", *RETIRED):
        assert retired not in body, retired


# ---------------------------------------------------------------------------
# 5-6. Nothing moved, and nothing changed meaning
# ---------------------------------------------------------------------------


def test_all_three_steps_are_still_in_the_bands_they_were_in(specialist, three_steps, today):
    bands = {
        band.key: [item.text for item in band.items]
        for band in build_my_work(specialist, today=today).bands
    }

    assert DO_TEXT in bands[wi.BAND_OVERDUE]
    assert WAIT_TEXT in bands[wi.BAND_WEEK]
    assert MONITOR_TEXT in bands[wi.BAND_WEEK]


def test_overdue_and_review_ripe_are_unchanged(specialist, three_steps, today):
    """Only DO + DEADLINE is late; a passed review date is ripe, never late."""
    items = items_by_text(specialist, today)

    assert items[DO_TEXT].is_overdue is True
    assert items[DO_TEXT].is_review_ripe is False
    for text in (WAIT_TEXT, MONITOR_TEXT):
        assert items[text].is_overdue is False, text

    action = three_steps[ActionKind.WAIT.value]
    action.target_date = today - timedelta(days=4)
    action.save(update_fields=["target_date"])

    ripe = items_by_text(specialist, today)[WAIT_TEXT]
    assert ripe.is_review_ripe is True
    assert ripe.is_overdue is False


def test_the_stored_kinds_are_exactly_what_they_were(three_steps):
    """The point of the whole change: presentation only.

    Read back off the database rather than off the objects the fixture holds, so
    a migration or a save-time rewrite would fail this.
    """
    stored = {name: NextAction.objects.get(pk=action.pk) for name, action in three_steps.items()}

    assert stored["DO"].kind == ActionKind.DO
    assert stored["WAIT"].kind == ActionKind.WAIT
    assert stored["MONITOR"].kind == ActionKind.MONITOR

    # The labels still exist on the enum, because the enum is not what changed.
    assert dict(ActionKind.choices) == {"DO": "Teen", "WAIT": "Ootan", "MONITOR": "Jälgin"}


def test_the_intervention_row_states_the_dates_meaning_not_the_steps_kind(
    department_head, specialist, three_steps, today
):
    """«Vajab sekkumist» said `OOTAN AL 27.08`. It says `VAATAN ÜLE 27.08`.

    Same row, same population, same ordering — the cell names what the date is,
    which is what a head reading a list of trouble actually needs (ADR 0054).
    """
    from app.matters.overview import intervention_rows

    action = three_steps[ActionKind.WAIT.value]
    action.target_date = today - timedelta(days=4)
    action.save(update_fields=["target_date"])

    item = items_by_text(specialist, today)[WAIT_TEXT]
    assert item.is_review_ripe is True

    rows = intervention_rows(department_head, today, [item])

    assert len(rows) == 1
    assert rows[0].meaning.startswith(wi.MEANING_REVIEW)
    for retired in RETIRED:
        assert retired not in rows[0].meaning, retired


# ---------------------------------------------------------------------------
# 7. A colleague's page is the same page
# ---------------------------------------------------------------------------


def test_a_colleagues_work_page_says_no_more_than_your_own(
    client, department_head, specialist, three_steps
):
    client.force_login(department_head)
    body = body_of(client.get(reverse("matters:person_work", kwargs={"pk": specialist.pk})))

    assert DO_TEXT in body
    assert 'class="mode' not in body
    for retired in RETIRED:
        assert retired not in body, retired


# ---------------------------------------------------------------------------
# 8. No form offers the classification back
# ---------------------------------------------------------------------------


def test_no_user_facing_form_offers_a_kind_selector(signed_in, normal_matter):
    """Neither the composer, nor Uus teema, nor the register's own filter.

    The register filter is the one that was still offering it: `?tegevus=` had a
    value per stored kind. What is left are the two conditions a reader can act
    on, and neither of them names a category (ADR 0054).
    """
    from app.matters.views import NEXT_ACTION_LABELS

    assert set(selectors.NEXT_ACTION_FILTERS) == {
        selectors.MISSING,
        "hilinenud",
        selectors.REVIEW_DUE,
    }
    for label in NEXT_ACTION_LABELS.values():
        assert not any(word in label.upper() for word in RETIRED), label

    for url in (
        reverse("matters:matter_list"),
        reverse("matters:matter_create"),
        reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}),
    ):
        body = body_of(signed_in.get(url))
        for retired in RETIRED:
            assert retired not in body, f"{url}: {retired}"


def test_the_review_filter_still_finds_both_kinds(signed_in, three_steps, today):
    """One filter over both review kinds — the union of the two it replaced."""
    for name in ("WAIT", "MONITOR"):
        action = three_steps[name]
        action.target_date = today - timedelta(days=1)
        action.save(update_fields=["target_date"])

    response = signed_in.get(
        reverse("matters:matter_list"),
        {"olek": "avatud", "tegevus": selectors.REVIEW_DUE},
    )

    assert response.status_code == 200
    assert response.context["total"] == 2


# ---------------------------------------------------------------------------
# The contract that stops it coming back
# ---------------------------------------------------------------------------

#: Only rendered output counts. A `{% comment %}` explaining why the chip is not
#: there is exactly the note a future reader needs, and stripping it out of the
#: templates would be the opposite of the point.
_DJANGO_COMMENT = re.compile(r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}", re.S)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)


def rendered_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return _HTML_COMMENT.sub(" ", _DJANGO_COMMENT.sub(" ", text))


def test_no_active_template_can_render_the_classification():
    """A grep, deliberately narrow.

    Scoped to `templates/` — the active user-facing surface — and to what is
    actually *rendered*. It says nothing about migrations, ADRs, the enum, the
    register parser or the domain tests, all of which are entitled to the words
    and several of which would be wrong without them.
    """
    template_dir = Path(settings.BASE_DIR) / "templates"
    offenders = []
    for template in sorted(template_dir.rglob("*.html")):
        relative = str(template.relative_to(template_dir)).replace("\\", "/")
        text = rendered_text(template)
        for word in RETIRED:
            if word in text:
                offenders.append(f"{relative}: {word}")
        # `get_kind_display` is deliberately *not* here: `Submission`, `Entry`
        # and the intelligence records each have a `kind` of their own, and
        # those are real distinctions a reader does need. What may not come
        # back is the `NextAction` chip and the vocabulary it carried.
        for name in ("action_kind_label", "action_kind|", "mode--", "modechip"):
            if name in text:
                offenders.append(f"{relative}: {name}")
    assert offenders == [], offenders


def test_the_stylesheet_no_longer_defines_the_chip():
    """A dead rule is how a removed component quietly comes back."""
    css = (Path(settings.BASE_DIR) / "static" / "css" / "app.css").read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", " ", css, flags=re.S)
    for selector in (".mode--do", ".mode--wait", ".mode--monitor", ".modechip", ".modeselect"):
        assert selector not in css, selector
