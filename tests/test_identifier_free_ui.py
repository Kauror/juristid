"""The ordinary UI names a topic by its title, never by `2026_10`.

Human QA read three screens and found the same thing on all of them. A crumb
reading *Teemad / 2026_10*. An intervention row reading *sammuta · 202 P
VAIKUST*. An activity line reading *Ireen lisas sissekande · 2026_303*. In each
one the system had answered "which record is this" where a reader had asked
"which subject is this", and the answer took the space the subject needed.

Three separate things are asserted here and they are not the same claim:

**The reference is gone from the reading surfaces.** Not hidden — absent from
the markup, so it is absent from the accessibility tree and from a tooltip as
well as from the screen.

**The reference is still there.** It is a real column, it still allocates, it
still identifies the record to every other system, and `Muuda teemat` still
states it under `Muutumatu`. Nothing was migrated, nulled or regenerated. A
change that removed the *data* would be a different and much worse change than
the one this round asked for.

**Nothing else moved.** The intervention list holds the same Matters in the same
order — the silence age still decides `sort_on`, it simply stopped being
printed — and the feed still shows exactly the Matters the reader may open.
"""

from __future__ import annotations

import datetime
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.documents.services import add_evidence_version
from app.matters import overview as ov
from app.matters.models import Matter
from app.matters.services import assign_matter, change_stage, create_matter
from app.submissions.enums import SubmissionStatus
from tests import factories

pytestmark = pytest.mark.django_db

#: Distinctive on purpose. A real reference like `2026_1` collides with dates,
#: counts and half the fixtures in this suite, and an absence assertion against
#: a string the page could legitimately print for another reason proves nothing.
REFERENCE = "2099_987"
YEAR, NUMBER = 2099, 987

TITLE = "Ebaausate kaubandustavade toiduainete tarneahelas avalik konsultatsioon"

OVERVIEW = "matters:overview"


def _body(response) -> str:
    return response.content.decode()


def _get(client, name: str, query: str = "", **kwargs) -> str:
    return _body(client.get(reverse(name, kwargs=kwargs) + query))


@pytest.fixture
def marked_matter(specialist):
    """One open Matter carrying the distinctive reference and title."""
    return factories.MatterFactory(
        owner=specialist,
        title=TITLE,
        reference_year=YEAR,
        reference_number=NUMBER,
    )


def _sent_submission(matter, *, title: str, sent, sent_by=None):
    """A canonical sent Submission, with the evidence the database requires."""
    document = factories.DocumentFactory(matter=matter)
    version = add_evidence_version(
        document=document,
        content=f"%PDF-1.4\n{title}".encode(),
        original_filename="naidis.pdf",
        mime_type="application/pdf",
    )
    return factories.SubmissionFactory(
        matter=matter,
        title=title,
        status=SubmissionStatus.SENT,
        sent_at=datetime.datetime.combine(sent, datetime.time(9, 0), tzinfo=datetime.UTC),
        sent_by=sent_by,
        final_version=version,
    )


# ---------------------------------------------------------------------------
# §30 — the reference is absent from the ordinary reading surfaces
# ---------------------------------------------------------------------------


def test_the_matter_page_names_the_topic_and_not_the_record(signed_in, marked_matter):
    """Title in the <h1> and in the tab title; the reference in neither."""
    body = _get(signed_in, "matters:matter_detail", pk=marked_matter.pk)

    assert TITLE in body
    assert REFERENCE not in body


def test_the_breadcrumb_stops_at_teemad(signed_in, marked_matter):
    """*Teemad / 2026_10* became *Teemad*.

    Not *Teemad / <title>*: the title is the <h1> a few lines below the crumb,
    and a trail that repeats the page's own name twice within twenty pixels is
    noise wearing the costume of navigation (human QA §8).
    """
    body = _get(signed_in, "matters:matter_detail", pk=marked_matter.pk)

    crumbs = body.split('class="matterhead__crumbs"', 1)[1].split("</div>", 1)[0]
    assert ">Teemad<" in crumbs
    assert REFERENCE not in crumbs
    # The old no-reference branch went with it. An archive row used to read
    # *Teemad / viiteta*, which announced a missing value nobody wanted.
    assert "viiteta" not in crumbs


def test_the_register_lists_topics_by_title(signed_in, marked_matter):
    body = _get(signed_in, "matters:matter_list")

    assert TITLE in body
    assert REFERENCE not in body


def test_the_overview_names_topics_by_title(signed_in, marked_matter):
    body = _get(signed_in, OVERVIEW, "?vaade=osakond")

    assert TITLE in body
    assert REFERENCE not in body


def test_my_work_names_topics_by_title(signed_in, marked_matter):
    """The rail's quiet block lists my own Matters with no next step."""
    body = _get(signed_in, "matters:my_work")

    assert TITLE in body
    assert REFERENCE not in body


def test_the_watchlist_names_topics_by_title(signed_in, marked_matter, specialist):
    factories.ImportantDateFactory(matter=marked_matter, created_by=specialist)

    body = _get(signed_in, "intelligence:important_dates")

    assert TITLE in body
    assert REFERENCE not in body


def test_the_opinion_list_names_topics_by_title(signed_in, marked_matter):
    _sent_submission(marked_matter, title="Koja arvamus", sent=timezone.localdate())

    body = _get(signed_in, "submissions:sent")

    assert TITLE in body
    assert REFERENCE not in body


def test_search_results_name_topics_by_title(signed_in, marked_matter):
    """Searching *by* reference is untouched; the result row stops printing it.

    The two are separate questions and only one of them changed. An exact
    reference still resolves straight to the file — `test_search_authorization`
    owns that promise — and the row a text query lands on is named by its
    subject rather than by its filing code.
    """
    from app.search.indexing import indexable_matters, refresh_matters

    refresh_matters(indexable_matters().filter(pk=marked_matter.pk))

    body = _body(signed_in.get(reverse("search:search"), {"q": "kaubandustavade"}))

    assert TITLE in body
    rows = body.split('class="searchresults"', 1)[1]
    assert REFERENCE not in rows


def test_the_statistics_opinion_table_names_topics_by_title(signed_in, marked_matter):
    """The `Viide` column went with the value: an empty column is worse."""
    _sent_submission(marked_matter, title="Koja arvamus", sent=timezone.localdate())

    body = _get(signed_in, "reporting:submissions")

    assert TITLE in body
    assert REFERENCE not in body


# ---------------------------------------------------------------------------
# §31 — the reference is still data, and still reachable technically
# ---------------------------------------------------------------------------


def test_the_reference_is_untouched_in_the_database(marked_matter):
    """The columns, not a rendering of them. Nothing here was migrated."""
    stored = Matter.objects.get(pk=marked_matter.pk)

    assert stored.reference_year == YEAR
    assert stored.reference_number == NUMBER
    assert stored.display_reference == REFERENCE


def test_the_admin_still_identifies_a_matter_by_its_reference(marked_matter):
    """``__str__`` is what the Django admin, the shell and every log line print.

    Deliberately not changed. Technical surfaces exist to answer "which row is
    this", which is the question the reference is the right answer to.
    """
    assert str(marked_matter).startswith(REFERENCE)


def test_the_edit_page_still_states_the_reference_as_provenance(signed_in, marked_matter):
    """`Muutumatu`, beside `Päritolu` — a labelled fact, not an identity.

    The one ordinary page that still prints it, because it is the one page that
    asks the question. Everywhere else the reference was standing in for the
    topic's name (human QA §5, §31).
    """
    body = _get(signed_in, "matters:matter_edit", pk=marked_matter.pk)

    panel = body.split('aria-label="Muutumatud andmed"', 1)[1].split("</section>", 1)[0]
    assert "Viide" in panel
    assert REFERENCE in panel
    # And the page still leads with the topic, not with the record.
    assert TITLE in body


# ---------------------------------------------------------------------------
# §32 — the no-deadline intervention row
# ---------------------------------------------------------------------------


def _no_action_rows(user, today):
    """The `Vajab sekkumist` rows for Matters nobody has given a next step.

    No dated work items are passed in, so the only rows produced are the two
    undated halves — which is exactly the population this section is about.
    """
    return [
        row for row in ov.intervention_rows(user, today, []) if row.reason == ov.REASON_NO_ACTION
    ]


def test_a_matter_without_a_next_step_says_so_in_words(specialist, marked_matter):
    """*sammuta* was this module's vocabulary, not the department's."""
    today = timezone.localdate()

    (row,) = _no_action_rows(specialist, today)

    assert row.matter.pk == marked_matter.pk
    assert row.value == "tähtaeg puudub"
    assert "sammuta" not in row.value


def test_the_row_no_longer_prints_how_long_it_has_been_quiet(specialist, marked_matter):
    """202 or 182, the thing to do about it is the same (human QA §11)."""
    today = timezone.localdate()

    (row,) = _no_action_rows(specialist, today)

    assert row.meaning == ""


def test_the_rendered_row_shows_only_the_missing_deadline(signed_in, marked_matter):
    """Read off the page, because an empty dataclass field can still render."""
    body = _get(signed_in, OVERVIEW, "?vaade=osakond&sekkumine=sammuta")

    assert "tähtaeg puudub" in body
    assert "vaikust" not in body.lower()
    assert "VIIMANE TEGEVUS TEADMATA" not in body
    # The row's second line kept the fact that is worth reading and lost the one
    # that was not (human QA §13).
    assert "järgmine samm määramata" in body
    assert REFERENCE not in body


def test_the_silence_age_still_decides_where_the_row_sits(specialist):
    """Ranking is unchanged. Only the label was ever the complaint.

    Two Matters with no next step, one quiet since last year and one since this
    morning. The older one sorts first — which is a property of `sort_on`, and
    `sort_on` is still the activity date the row stopped printing (human QA §12).
    """
    today = timezone.localdate()
    old = factories.MatterFactory(owner=specialist, title="Vana vaikiv teema")
    recent = factories.MatterFactory(owner=specialist, title="Värske vaikiv teema")
    factories.EntryFactory(
        matter=old, author=specialist, occurred_at=timezone.now() - timedelta(days=200)
    )
    factories.EntryFactory(
        matter=recent, author=specialist, occurred_at=timezone.now() - timedelta(days=2)
    )

    rows = _no_action_rows(specialist, today)

    ordered = [row.matter.pk for row in rows]
    assert ordered.index(old.pk) < ordered.index(recent.pk)
    assert rows[ordered.index(old.pk)].sort_on < rows[ordered.index(recent.pk)].sort_on


def test_the_matter_is_still_in_the_intervention_population(specialist, marked_matter):
    """Membership, not wording. The list holds what it held before."""
    today = timezone.localdate()

    rows = ov.intervention_rows(specialist, today, [])

    assert marked_matter.pk in {row.matter.pk for row in rows}


# ---------------------------------------------------------------------------
# §33 — Viimane tegevus became Viimased muudatused
# ---------------------------------------------------------------------------


def test_the_section_is_called_viimased_muudatused(signed_in, marked_matter):
    body = _get(signed_in, OVERVIEW, "?vaade=osakond")

    assert 'aria-label="Viimased muudatused"' in body
    assert "Viimane tegevus" not in body


def test_the_register_column_keeps_its_own_name(signed_in, marked_matter):
    """A different fact, deliberately not renamed.

    The register's *Viimane tegevus* column answers "when did work last happen
    on this file". It is a date, derived in `app.matters.activity`, and it is
    not a list of changes — calling it *Viimased muudatused* would promise a
    feed and deliver a timestamp.
    """
    body = _get(signed_in, "matters:matter_list")

    assert "Viimane tegevus" in body


# ---------------------------------------------------------------------------
# §34, §35 — what the feed says, and where its link goes
# ---------------------------------------------------------------------------


def _feed(user, kind: str = ov.FEED_ALL):
    return ov.activity_feed(user, timezone.localdate(), kind)


def test_a_created_matter_reads_as_a_sentence_about_a_colleague(specialist):
    matter = create_matter(title=TITLE, actor=specialist, owner=specialist)

    (item,) = [row for row in _feed(specialist) if row.matter.pk == matter.pk]

    assert item.actor_name == specialist.display_name
    assert item.verb == "avas teema"
    assert item.matter_title == TITLE


def test_an_entry_reads_as_a_sentence_about_a_colleague(specialist, marked_matter):
    factories.EntryFactory(matter=marked_matter, author=specialist)

    (item,) = _feed(specialist, ov.FEED_ENTRIES)

    assert item.verb == "lisas sissekande"
    assert item.matter_title == TITLE


def test_a_stage_change_reads_as_a_sentence_about_a_colleague(specialist, marked_matter):
    change_stage(matter=marked_matter, stage=factories.StageFactory(), actor=specialist)

    verbs = [row.verb for row in _feed(specialist, ov.FEED_STATUS)]

    assert "muutis hetkeseisu" in verbs


def test_an_assignment_reads_as_a_sentence_about_a_colleague(
    specialist, other_specialist, marked_matter
):
    assign_matter(matter=marked_matter, owner=other_specialist, actor=specialist)

    verbs = [row.verb for row in _feed(specialist, ov.FEED_STATUS)]

    assert "määras vastutaja" in verbs


def test_a_sent_opinion_reads_as_a_sentence_about_a_colleague(specialist, marked_matter):
    _sent_submission(
        marked_matter, title="Koja arvamus", sent=timezone.localdate(), sent_by=specialist
    )

    (item,) = _feed(specialist, ov.FEED_SUBMISSIONS)

    assert item.verb == "esitas arvamuse"
    assert item.matter_title == TITLE


def test_no_row_ever_carries_a_raw_event_name(specialist, marked_matter):
    """The feed is professional chronology, not the audit table rendered."""
    create_matter(title="Teine teema", actor=specialist, owner=specialist)
    change_stage(matter=marked_matter, stage=factories.StageFactory(), actor=specialist)
    factories.EntryFactory(matter=marked_matter, author=specialist)

    rows = _feed(specialist)

    assert rows
    for item in rows:
        assert "_" not in item.verb
        assert item.verb == item.verb.lower()
        # The fallback exists for an event type added to `_STATUS_EVENTS`
        # without a word beside it. Reaching it means the map fell behind.
        assert item.verb != ov._EVENT_VERB_FALLBACK


def test_the_feed_row_links_to_the_topic_it_names(signed_in, specialist, marked_matter):
    factories.EntryFactory(matter=marked_matter, author=specialist)
    detail = reverse("matters:matter_detail", kwargs={"pk": marked_matter.pk})

    (item,) = _feed(specialist, ov.FEED_ENTRIES)
    body = _get(signed_in, OVERVIEW, "?vaade=osakond&voog=sissekanded")

    assert item.url == detail
    feed = body.split('aria-label="Viimased muudatused"', 1)[1]
    assert f'href="{detail}"' in feed
    assert TITLE in feed
    assert REFERENCE not in feed


# ---------------------------------------------------------------------------
# §36 — a title says more than a reference did, so the boundary matters
# ---------------------------------------------------------------------------


def test_a_restricted_matter_a_reader_cannot_open_is_not_in_their_feed(
    other_specialist, specialist
):
    """No row, so no title, no reference and no description of the activity.

    The rule the change relies on: `activity_feed` builds items *from* the
    reader's authorized population, so a Matter they may not open produces
    nothing to render. It never resolves a title afterwards by primary key,
    which is the shape that would have turned a safer identifier into a leak
    (human QA §21).
    """
    hidden = factories.MatterFactory(
        owner=specialist,
        title="Salajane ettevalmistus konkurentsiameti menetluseks",
        visibility=Visibility.RESTRICTED,
    )
    factories.EntryFactory(matter=hidden, author=specialist, body="<p>Piiratud märkus.</p>")

    rows = _feed(other_specialist)

    assert all(row.matter is None or row.matter.pk != hidden.pk for row in rows)
    assert all(hidden.title not in row.matter_title for row in rows)


def test_the_rendered_feed_never_mentions_a_matter_the_reader_cannot_open(
    client, other_specialist, specialist
):
    hidden = factories.MatterFactory(
        owner=specialist,
        title="Salajane ettevalmistus konkurentsiameti menetluseks",
        reference_year=YEAR,
        reference_number=NUMBER,
        visibility=Visibility.RESTRICTED,
    )
    factories.EntryFactory(matter=hidden, author=specialist)
    client.force_login(other_specialist)

    body = _get(client, OVERVIEW, "?vaade=osakond")

    assert hidden.title not in body
    assert REFERENCE not in body
    assert str(hidden.pk) not in body


def test_a_participant_sees_the_restricted_topic_by_name(signed_in, specialist):
    """The other half of the same rule, and the reason it is safe to say it.

    A reader who may open the file can read its title on the file itself. A feed
    that showed them a padlock instead of the name they already have access to
    would be protecting nothing and costing them the row.
    """
    mine = factories.MatterFactory(
        owner=specialist,
        title="Piiratud teema minu enda portfellis",
        visibility=Visibility.RESTRICTED,
    )
    factories.EntryFactory(matter=mine, author=specialist)

    body = _get(signed_in, OVERVIEW, "?vaade=osakond")

    feed = body.split('aria-label="Viimased muudatused"', 1)[1]
    assert mine.title in feed
    assert "Piiratud teema" in feed
