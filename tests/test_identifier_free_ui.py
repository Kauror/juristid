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

from app.audit.enums import ChangeEventType
from app.core.enums import Visibility
from app.documents.services import add_evidence_version
from app.intelligence.services import add_important_date
from app.matters import overview as ov
from app.matters.enums import EngagementKind
from app.matters.models import Matter
from app.matters.services import (
    add_engagement,
    assign_matter,
    change_stage,
    create_matter,
    set_matter_title,
)
from app.submissions.enums import SubmissionStatus
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

#: Distinctive on purpose. A real reference like `2026_1` collides with dates,
#: counts and half the fixtures in this suite, and an absence assertion against
#: a string the page could legitimately print for another reason proves nothing.
REFERENCE = "2099_987"
YEAR, NUMBER = 2099, 987

TITLE = "Ebaausate kaubandustavade toiduainete tarneahelas avalik konsultatsioon"

OVERVIEW = "matters:department"

#: The ordering control itself. Anchored on the `<select>` rather than on
#: `name="jarjestus"`, because the search form above it renders a *hidden* input
#: of the same name whenever a sort is active — split on the name and the
#: assertion reads a different control's options.
SORT_SELECT = '<select class="field__input" name="jarjestus">'


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
    """Title in the <h1> and in the tab title; the reference in neither.

    The v2 design put the reference back in one place on this page — the
    «Teema andmed» facts rail, under the label `Teemaviide`, beside the other
    things somebody looks up (02-EKRAANID §C). The rule this file is about is
    unchanged and is what the assertions below check: the *topic* is named by
    its title, in the heading, the crumb and the tab title, and the reference
    identifies rather than names.
    """
    body = _get(signed_in, "matters:matter_detail", pk=marked_matter.pk)

    assert TITLE in body
    heading = body.split('matterhead__title">', 1)[1].split("</h1>", 1)[0]
    assert TITLE in heading
    assert REFERENCE not in heading
    assert f"<title>{REFERENCE}" not in body
    # Once, in the rail, under a label.
    assert body.count(REFERENCE) == 1
    assert f'class="railcard__ref">{REFERENCE}</span>' in body


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
#
# "Technically" means the database, `__str__`, exact search, the export and the
# import tooling. It no longer means any page of the ordinary application: the
# edit page's provenance panel was the last one, and review took it out.
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


def test_the_edit_page_states_provenance_without_the_reference(signed_in, marked_matter):
    """`Muutumatu` keeps what a colleague can act on, and drops the filing code.

    This panel was the last ordinary surface printing the reference, kept on the
    argument that a labelled fact is not an identity. Review rejected the
    distinction: `Muuda teemat` is the ordinary application — not admin, not
    import tooling, not a diagnostic view — and the rule is about who is looking
    (review of PR #72, §2).

    What stays is provenance that answers a question somebody actually has about
    the record: what kind of record it is, and which source row it came from.
    """
    body = _get(signed_in, "matters:matter_edit", pk=marked_matter.pk)

    # The panel is a compact `Muutumatu` strip since the v2 rebuild, not a card
    # (02-EKRAANID §C). What it holds did not change.
    panel = body.split('class="uxfixed"', 1)[1].split("</div>", 1)[0]
    assert "Päritolu" in panel
    assert "Viide" not in panel
    assert REFERENCE not in panel
    # The page as a whole, not only the strip: nowhere on it.
    assert REFERENCE not in body
    assert TITLE in body


def test_the_csv_export_still_carries_the_reference(signed_in, specialist):
    """A CSV is reconciled against other systems, and that is what cites it.

    The strongest of the persistence proofs, because it goes through a real
    response: the value is not merely in a column, it still reaches the file the
    department hands to somebody else.

    Its own Matter, with a reporting year, because the export is scoped to the
    reporting period and `marked_matter` deliberately has none.
    """
    exported = factories.MatterFactory(
        owner=specialist,
        title="Ekspordi kaudu kontrollitav teema",
        reference_year=2098,
        reference_number=765,
        reporting_year=2026,
    )

    response = signed_in.get(reverse("reporting:export", kwargs={"slug": "teemad"}))
    body = b"".join(response.streaming_content).decode("utf-8-sig")

    assert exported.display_reference == "2098_765"
    assert "2098_765" in body
    assert exported.title in body


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


def test_the_department_page_carries_no_second_change_feed(signed_in, marked_matter):
    """*Viimased muudatused* is not a section any more (docs/adr/0049 §7).

    Its question — what has this department finished — is *Tehtud*'s, read from
    the canonical records rather than from the audit stream. The feed's own read
    model is still asserted below, in full: what it says about a Matter is a
    rule worth keeping whether or not a page is currently printing it (DS-25).
    """
    body = _get(signed_in, OVERVIEW, "?vaade=osakond")

    assert 'aria-label="Viimased muudatused"' not in body
    assert 'aria-label="Vajab sekkumist"' in body


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

    assert item.url == detail
    assert item.matter_title == TITLE
    assert REFERENCE not in item.matter_title


# ---------------------------------------------------------------------------
# Review §6-§11 - the feed covers the work, not just the status field
# ---------------------------------------------------------------------------


def _verbs(user, kind: str = ov.FEED_ALL) -> list[str]:
    return [row.verb for row in _feed(user, kind)]


def test_a_rename_reads_as_a_sentence_about_a_colleague(specialist, marked_matter):
    """The one field edit that earns a line.

    Not because renaming matters more than moving a deadline, but because it is
    the change most likely to make a colleague think they are looking at a
    different file - and this row is the only place that says who did it.
    """
    set_matter_title(matter=marked_matter, value="Uus pealkiri vanale teemale", actor=specialist)

    rows = [r for r in _feed(specialist, ov.FEED_STATUS) if r.verb == "muutis teema pealkirja"]

    (item,) = rows
    assert item.actor_name == specialist.display_name
    # The *current* title, not the one the event recorded. The row points at a
    # file somebody is about to open, so it is named the way it will be.
    assert item.matter_title == "Uus pealkiri vanale teemale"


def test_setting_the_next_step_reads_as_a_sentence_about_a_colleague(specialist, marked_matter):
    set_next_action(
        matter=marked_matter,
        text="Koosta arvamus",
        target_date=timezone.localdate() + timedelta(days=7),
        actor=specialist,
    )

    rows = [
        r
        for r in _feed(specialist, ov.FEED_STATUS)
        if "jargmise tegevuse" in r.verb.replace("ä", "a")
    ]

    (item,) = rows
    assert item.verb == "määras järgmise tegevuse"
    assert item.matter_title == TITLE
    assert item.url == reverse("matters:matter_detail", kwargs={"pk": marked_matter.pk})


def test_an_important_date_reads_as_a_sentence_about_a_colleague(specialist, marked_matter):
    add_important_date(
        matter=marked_matter,
        title="Kooskolastusringi lopp",
        date_value=timezone.localdate(),
        period_end=timezone.localdate(),
        actor=specialist,
    )

    rows = [r for r in _feed(specialist, ov.FEED_STATUS) if "tahtaja" in r.verb.replace("ä", "a")]

    (item,) = rows
    assert item.verb == "lisas olulise tähtaja"
    assert item.matter_title == TITLE
    assert item.url == reverse("matters:matter_detail", kwargs={"pk": marked_matter.pk})


def test_an_engagement_reads_as_a_sentence_about_a_colleague(specialist, marked_matter):
    add_engagement(
        matter=marked_matter,
        kind=EngagementKind.WEB_CALL,
        title="Liikmete kaasamiskutse",
        occurred_on=timezone.localdate(),
        actor=specialist,
    )

    rows = [r for r in _feed(specialist, ov.FEED_STATUS) if "kaasamis" in r.verb]

    (item,) = rows
    assert item.verb == "lisas kaasamise"
    assert item.matter_title == TITLE
    assert item.url == reverse("matters:matter_detail", kwargs={"pk": marked_matter.pk})


def test_every_row_carries_an_actor_a_verb_a_title_and_a_url(signed_in, specialist, marked_matter):
    """The shape of the whole section, over one of every kind of change.

    Asserted together rather than once per event, because the failure this
    guards against is a family reaching the population without reaching the
    vocabulary - which produces a row complete in every respect except the one
    that matters.
    """
    change_stage(matter=marked_matter, stage=factories.StageFactory(), actor=specialist)
    set_next_action(
        matter=marked_matter,
        text="Koosta arvamus",
        target_date=timezone.localdate() + timedelta(days=7),
        actor=specialist,
    )
    add_important_date(
        matter=marked_matter,
        title="Kooskolastusringi lopp",
        date_value=timezone.localdate(),
        period_end=timezone.localdate(),
        actor=specialist,
    )
    add_engagement(
        matter=marked_matter,
        kind=EngagementKind.WEB_CALL,
        title="Liikmete kaasamiskutse",
        occurred_on=timezone.localdate(),
        actor=specialist,
    )
    factories.EntryFactory(matter=marked_matter, author=specialist)
    detail = reverse("matters:matter_detail", kwargs={"pk": marked_matter.pk})

    rows = _feed(specialist)
    body = _get(signed_in, OVERVIEW, "?vaade=osakond")

    assert len(rows) >= 5
    for item in rows:
        assert item.actor_name
        assert item.verb and item.verb != ov._EVENT_VERB_FALLBACK
        assert "_" not in item.verb
        assert item.matter_title == TITLE
        assert item.url == detail
    assert REFERENCE not in body


def test_import_and_cutover_events_never_reach_the_feed(specialist, marked_matter):
    """The line between a curated feed and the audit table rendered.

    These are real, stored, correct `ChangeEvent` rows about a visible Matter.
    They describe what a pipeline did to a record, not what a colleague did to
    the work, and a section mixing the two gets skimmed instead of read
    (review §10).
    """
    from app.audit.services import record_change_event

    noisy = (
        ChangeEventType.IMPORT_APPLIED,
        ChangeEventType.MATTER_SOURCE_FIELDS_REFRESHED,
        ChangeEventType.MATTER_HISTORICAL_CUTOVER_CLOSED,
        ChangeEventType.MATTER_REGISTER_CUTOVER_RETIRED,
        ChangeEventType.MATTER_REGISTER_CUTOVER_ACTIVATED,
        # Promotion is cutover machinery too: its only callers are the register
        # importers, never a person in the application.
        ChangeEventType.MATTER_PROMOTED,
        # Field-level corrections. Each is a legitimate audit row and none is
        # something a colleague would call a change to the department's work.
        ChangeEventType.MATTER_DATE_CHANGED,
        ChangeEventType.MATTER_VISIBILITY_CHANGED,
        ChangeEventType.MATTER_DATA_CLASS_CHANGED,
        ChangeEventType.TAG_ASSIGNED,
    )
    for event_type in noisy:
        record_change_event(event_type=event_type, matter=marked_matter, actor=specialist)

    assert _feed(specialist) == []


def test_the_filter_is_named_for_what_it_now_holds(specialist, marked_matter):
    """`Staatuse muutused` stopped being true the moment the bucket widened.

    Asserted on the vocabulary rather than on a rendered page: the feed has no
    surface since the department pages merged, and the word would otherwise
    stop being checked at all (DS-25).
    """
    add_engagement(
        matter=marked_matter,
        kind=EngagementKind.WEB_CALL,
        title="Liikmete kaasamiskutse",
        occurred_on=timezone.localdate(),
        actor=specialist,
    )

    labels = dict(ov.FEED_FILTERS)

    assert labels[ov.FEED_STATUS] == "Teema muudatused"
    assert "Staatuse muutused" not in labels.values()


def test_the_old_query_value_still_selects_that_bucket(signed_in, specialist, marked_matter):
    """The label moved; the URL did not.

    `?voog=staatus` is in bookmarks and in links people have pasted to each
    other. Renaming the value to match the label would have broken those to fix
    a word (review §12).
    """
    add_engagement(
        matter=marked_matter,
        kind=EngagementKind.WEB_CALL,
        title="Liikmete kaasamiskutse",
        occurred_on=timezone.localdate(),
        actor=specialist,
    )
    factories.EntryFactory(matter=marked_matter, author=specialist)

    assert ov.FEED_STATUS == "staatus"
    assert _verbs(specialist, ov.FEED_STATUS) == ["lisas kaasamise"]
    # The Sissekanded bucket is untouched by the widening.
    assert _verbs(specialist, ov.FEED_ENTRIES) == ["lisas sissekande"]


# ---------------------------------------------------------------------------
# §36 — a title says more than a reference did, so the boundary matters
# ---------------------------------------------------------------------------


def test_a_restricted_matter_a_reader_cannot_open_is_not_in_their_feed(reader, specialist):
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

    rows = _feed(reader)

    assert all(row.matter is None or row.matter.pk != hidden.pk for row in rows)
    assert all(hidden.title not in row.matter_title for row in rows)


def test_the_rendered_feed_never_mentions_a_matter_the_reader_cannot_open(
    client, reader, specialist
):
    hidden = factories.MatterFactory(
        owner=specialist,
        title="Salajane ettevalmistus konkurentsiameti menetluseks",
        reference_year=YEAR,
        reference_number=NUMBER,
        visibility=Visibility.RESTRICTED,
    )
    factories.EntryFactory(matter=hidden, author=specialist)
    client.force_login(reader)

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

    titles = {item.matter_title for item in _feed(specialist)}

    assert mine.title in titles


def test_a_restricted_matters_engagement_is_not_in_a_strangers_feed(reader, specialist):
    """A newly-carried event family, checked at the same boundary as the rest.

    `Kaasamine` reached the feed in this round. It is a child record, so the
    question it has to answer is not only "may this reader open the Matter" but
    "may this reader know this record exists" - and here the answer to both is
    no (review §16).
    """
    hidden = factories.MatterFactory(
        owner=specialist,
        title="Salajane ettevalmistus konkurentsiameti menetluseks",
        visibility=Visibility.RESTRICTED,
    )
    add_engagement(
        matter=hidden,
        kind=EngagementKind.WEB_CALL,
        title="Konfidentsiaalne liikmete kusitlus",
        occurred_on=timezone.localdate(),
        actor=specialist,
    )

    rows = _feed(reader)

    assert rows == []


def test_a_restricted_matters_important_date_is_not_in_a_strangers_feed(client, reader, specialist):
    hidden = factories.MatterFactory(
        owner=specialist,
        title="Salajane ettevalmistus konkurentsiameti menetluseks",
        reference_year=YEAR,
        reference_number=NUMBER,
        visibility=Visibility.RESTRICTED,
    )
    add_important_date(
        matter=hidden,
        title="Konfidentsiaalne kooskolastusringi lopp",
        date_value=timezone.localdate(),
        period_end=timezone.localdate(),
        actor=specialist,
    )
    client.force_login(reader)

    body = _get(client, OVERVIEW, "?vaade=osakond")

    assert hidden.title not in body
    assert "Konfidentsiaalne" not in body
    assert REFERENCE not in body
    assert str(hidden.pk) not in body
    assert "lisas olulise tähtaja" not in body


def test_a_restricted_child_on_an_ordinary_matter_is_not_announced(client, reader, specialist):
    """The case that makes the child scoping load-bearing rather than decorative.

    The Matter is NORMAL and every colleague can open it. One deadline on it is
    restricted by its own `visibility_override`, which is exactly what that
    column is for: a milestone somebody may record without the whole department
    being told it is being watched.

    Filtering the feed on `matter__in=visible` alone would put "Sandra lisas
    olulise tähtaja - <topic>" in front of a reader who is not allowed to know
    that deadline exists. Selecting each family through its own `visible_to`
    queryset is what stops it, and this is the test that would catch the
    filtering being loosened back (review §13).
    """
    open_matter = factories.MatterFactory(
        owner=specialist,
        title="Tavaline teema, mille uks tahtaeg on piiratud",
        visibility=Visibility.NORMAL,
    )
    record = add_important_date(
        matter=open_matter,
        title="Piiratud tahtaeg",
        date_value=timezone.localdate(),
        period_end=timezone.localdate(),
        actor=specialist,
    )
    record.visibility_override = Visibility.RESTRICTED
    record.save(update_fields=["visibility_override"])
    client.force_login(reader)

    # The reader may open the Matter, and the register still lists it.
    assert open_matter.title in _get(client, "matters:matter_list")

    # The feed says nothing about the deadline.
    assert _feed(reader) == []


def test_the_owner_of_a_restricted_child_still_sees_the_humanised_row(specialist):
    """The other half of the same rule, on the same data.

    A reader who may see the record gets the ordinary row - actor, verb, topic
    title, link. The scoping narrows what is offered; it does not degrade what
    is shown.
    """
    open_matter = factories.MatterFactory(
        owner=specialist,
        title="Tavaline teema, mille uks tahtaeg on piiratud",
        visibility=Visibility.NORMAL,
    )
    record = add_important_date(
        matter=open_matter,
        title="Piiratud tahtaeg",
        date_value=timezone.localdate(),
        period_end=timezone.localdate(),
        actor=specialist,
    )
    record.visibility_override = Visibility.RESTRICTED
    record.save(update_fields=["visibility_override"])

    (item,) = _feed(specialist)

    assert item.verb == "lisas olulise tähtaja"
    assert item.matter_title == open_matter.title
    assert item.url == reverse("matters:matter_detail", kwargs={"pk": open_matter.pk})


# ---------------------------------------------------------------------------
# Review §17-§19 - the register no longer offers to sort by an invisible value
# ---------------------------------------------------------------------------


def test_the_default_sort_is_offered_by_what_it_does(signed_in, marked_matter):
    """`Viide` named a column this page stopped having.

    The option asked somebody to order the register by a value that is nowhere
    on it - the reference column went in an earlier cleanup, and the last places
    printing the reference went in this round. `Vaikimisi` names what the option
    is instead: the list you get when you have not chosen anything
    (review §18).
    """
    body = _get(signed_in, "matters:matter_list")

    panel = body.split(SORT_SELECT, 1)[1].split("</select>", 1)[0]

    assert ">Vaikimisi<" in panel
    assert ">Viide<" not in panel
    # The value is untouched. It is in bookmarks, in the form this page posts,
    # and in `register_filters`; only the word a human reads changed.
    assert 'value="reference"' in panel


def test_the_reference_ordering_is_unchanged(signed_in, specialist):
    """Same query value, same rows, same order.

    Read off the register itself rather than re-deriving the ordering, so this
    is a statement about the page a lawyer opens and not about a selector this
    test happened to call the same way the view does.
    """
    for number, title in ((11, "Kolmas teema"), (12, "Teine teema"), (13, "Esimene teema")):
        factories.MatterFactory(
            owner=specialist,
            title=title,
            reference_year=2097,
            reference_number=number,
        )

    explicit = signed_in.get(reverse("matters:matter_list"), {"jarjestus": "reference"})
    default = signed_in.get(reverse("matters:matter_list"))

    ordered = [m.title for m in explicit.context["page"].object_list]
    assert explicit.status_code == 200
    assert ordered == [m.title for m in default.context["page"].object_list]
    # Newest reference first: 2097_13 before 2097_12 before 2097_11.
    assert ordered.index("Esimene teema") < ordered.index("Teine teema")
    assert ordered.index("Teine teema") < ordered.index("Kolmas teema")


def test_the_ordering_option_still_round_trips_as_the_selected_one(signed_in, marked_matter):
    """A bookmarked `?jarjestus=reference` comes back selected, under its new name."""
    body = _body(signed_in.get(reverse("matters:matter_list"), {"jarjestus": "reference"}))

    panel = body.split(SORT_SELECT, 1)[1].split("</select>", 1)[0]
    selected = [line for line in panel.splitlines() if "selected" in line]

    assert len(selected) == 1
    assert 'value="reference"' in selected[0]
    assert "Vaikimisi" in selected[0]
