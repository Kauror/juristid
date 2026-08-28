"""Placing a mailing and a public consultation against the right Matter.

Two halves, tested apart because they are separated on purpose. The **matcher**
proposes and may never write; the **reviewed mapping** writes and never guesses.
Every assertion below is about one of those two sentences.

The campaigns here are invented. The real export holds member mailing data and
is read from wherever the operator keeps it — never copied into this repository,
and never into a fixture (brief 1).
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.legacy_import.current_state import CurrentRegisterState
from app.legacy_import.models import OutreachChannel, RegisterEngagementImport
from app.legacy_import.register_outreach import (
    CAMPAIGN_COLUMNS,
    REFUSED_CAMPAIGN_COLUMNS,
    Confidence,
    MappingError,
    apply_mapping,
    build_outreach_plan,
    campaign_note,
    candidate_rows,
    content_words,
    mapping_digest,
    named_owner,
    read_campaigns,
    read_mapping,
    summary,
)
from app.legacy_import.register_refresh import build_refresh_plan, outreach_targets
from app.legacy_import.resolution import KnownPeople, MappingTables
from app.matters.enums import EngagementKind, RecordMode
from app.matters.models import Matter, MatterEngagement
from app.matters.services import create_imported_matter
from app.search.models import SearchDocument
from app.workflow.models import NextAction
from tests.synthetic_cutover import SNAPSHOT_DATE, approve_snapshot
from tests.synthetic_portfolio import (
    CURRENT_YEAR,
    add_source_reference,
    build_people,
    build_register,
    snapshot_for,
)

pytestmark = pytest.mark.django_db

SNAPSHOT = snapshot_for("outreach-28-08")
WINDOW = (dt.date(2026, 1, 1), dt.date(2026, 8, 28))

TEMPLATE_URL = "https://example.invalid/templates/aaaa-1111/html/"
OTHER_URL = "https://example.invalid/templates/bbbb-2222/html/"
PUBLIC_URL = "https://example.invalid/et/synteetiline-konsultatsioon/"

#: The invented consultation both channels are about.
MATTER_TITLE = "Sünteetilise pakendiseaduse muutmise seaduse eelnõu"

#: VASTUTAJA holds a given name and the campaign template names the same one.
#: That is the shape the matcher's owner filter is written for; a full display
#: name in the register cell is not something this source produces.
GIVEN_NAME = "Sandra"
OTHER_GIVEN_NAME = "Martin"


def export_row(
    *,
    section: str,
    template: str,
    url: str = TEMPLATE_URL,
    due: str = "2026-03-05 10:00:00",
    enqueues: str = "789",
) -> dict[str, str]:
    """One export row, with every column the real file carries.

    The refused analytics columns are present *and populated*, because a test
    that omitted them could not prove they are not imported.
    """
    row = {
        "Section name": section,
        "Template name": template,
        "Template preview": url,
        "Due at": due,
        "Enqueues": enqueues,
    }
    for column in REFUSED_CAMPAIGN_COLUMNS:
        row[column] = "4242"
    return row


@pytest.fixture
def world(monkeypatch):
    approve_snapshot(monkeypatch, sha256=SNAPSHOT)
    people = build_people()
    register = build_register(people)
    register.snapshot = SNAPSHOT
    return register


def add_matter(
    register,
    *,
    title: str = MATTER_TITLE,
    reference: int = 1,
    owner_cell: str = "",
    received: str = "16.02.2026",
    deadline: str = "10.03.2026",
    visibility: str = Visibility.NORMAL,
):
    def as_date(cell: str):
        if not cell:
            return None
        day, month, year = cell.split(".")
        return dt.date(int(year), int(month), int(day))

    # The canonical record genuinely holds the dates its first snapshot gave
    # it. Without this the Matter has no window at all, and a test claiming to
    # prove "the newer workbook's deadline wins" would only be proving "a
    # deadline appears where there was none" — a weaker statement that a
    # matcher reading stored state would also satisfy.
    matter = create_imported_matter(
        title=title,
        reference_year=CURRENT_YEAR,
        reference_number=reference,
        record_mode=RecordMode.FULL,
        visibility=visibility,
        received_date=as_date(received),
        response_deadline=as_date(deadline),
    )
    add_source_reference(
        register,
        matter,
        snapshot=SNAPSHOT,
        status_cell="kooskõlastusringil",
        owner_cell=owner_cell,
        received_cell=received,
        deadline_cell=deadline,
    )
    return matter


def plan_with(world, rows):
    """The outreach half of a dry-run, with **nothing applied first**.

    This used to apply the refresh and then plan the outreach over what it left,
    and every assertion below passed because of it — the matcher was reading a
    database the apply had already brought up to date. That is not the operation
    being tested: an operator approves campaign candidates *before* anything is
    written, and against a database that may never have seen this snapshot.

    Planning without applying is what the defect looked like from the outside,
    and it is what these tests now do.
    """
    campaigns, _ = read_campaigns(rows, since=WINDOW[0], until=WINDOW[1])
    refresh = build_refresh_plan(
        snapshot_sha256=SNAPSHOT,
        today=SNAPSHOT_DATE,
        campaigns=campaigns,
        campaign_window=WINDOW,
    )
    assert refresh.outreach is not None
    return refresh.outreach


# ---------------------------------------------------------------------------
# Reading the export
# ---------------------------------------------------------------------------


def test_only_the_five_recorded_columns_are_read():
    """An allowlist, so a future export column cannot arrive importable."""
    campaigns, _ = read_campaigns(
        [export_row(section="Sünteetiline küsimus", template="synth 05.03.26 Sandra")],
        since=WINDOW[0],
        until=WINDOW[1],
    )
    campaign = campaigns[0]

    for value in vars(campaign).values():
        assert str(value) != "4242"
    assert set(CAMPAIGN_COLUMNS).isdisjoint(REFUSED_CAMPAIGN_COLUMNS)


def test_a_campaign_outside_the_window_is_not_read():
    campaigns, tally = read_campaigns(
        [
            export_row(
                section="Sünteetiline vana küsimus",
                template="synth 05.03.25 Sandra",
                due="2025-03-05 10:00:00",
            )
        ],
        since=WINDOW[0],
        until=WINDOW[1],
    )
    assert campaigns == []
    assert tally["outside_window"] == 1


def test_a_campaign_without_a_template_url_is_not_read():
    """No stable identity means no idempotency, so it is not a candidate.

    A record that could be written twice is worse than one that is not written.
    """
    campaigns, tally = read_campaigns(
        [export_row(section="Sünteetiline", template="synth 05.03.26 Sandra", url="")],
        since=WINDOW[0],
        until=WINDOW[1],
    )
    assert campaigns == []
    assert tally["no_template_url"] == 1


def test_the_due_date_becomes_the_neutral_campaign_date():
    campaigns, _ = read_campaigns(
        [
            export_row(
                section="Sünteetiline",
                template="synth 05.03.26 Sandra",
                due="2026-03-05 23:45:00",
            )
        ],
        since=WINDOW[0],
        until=WINDOW[1],
    )
    assert campaigns[0].sent_on == dt.date(2026, 3, 5)


# ---------------------------------------------------------------------------
# The signals
# ---------------------------------------------------------------------------


def test_boilerplate_words_do_not_count_as_agreement():
    """Almost every register title is a *… muutmise seaduse eelnõu*.

    Counting that as subject overlap would make every campaign a
    high-confidence match for every Matter in its window.
    """
    assert content_words("Pakendiseaduse muutmise seaduse eelnõu") == frozenset({"pakendi"})
    assert content_words("Seaduse muutmise seaduse eelnõu") == frozenset()


def test_two_named_owners_name_nobody():
    """Which lawyer ran it is legible to a reader and not to this function."""
    known = frozenset({"Sandra", "Ireen"})
    assert named_owner("synth 05.03.26 Sandra", known) == "Sandra"
    assert named_owner("synth 05.03.26 Sandra ja Ireen", known) == ""
    assert named_owner("synth 05.03.26", known) == ""


def test_a_strong_candidate_is_identified(world):
    matter = add_matter(world, owner_cell=GIVEN_NAME)
    rows = [
        export_row(
            section="Mida arvad sünteetilise pakendiseaduse muudatustest?",
            template=f"pakendid 05.03.26 {GIVEN_NAME}",
        )
    ]

    plan = plan_with(world, rows)

    assert len(plan.high_confidence) == 1
    candidate = plan.high_confidence[0]
    assert candidate.matter_id == matter.pk
    assert "pakendi" in candidate.shared_terms


def test_wording_alone_never_reaches_high_confidence(world):
    """Subject overlap raises a candidate; it cannot create one.

    Without the owner and the window the campaign is not a candidate at all,
    however well the words agree.
    """
    add_matter(world, owner_cell=GIVEN_NAME)
    rows = [
        export_row(
            section="Mida arvad sünteetilise pakendiseaduse muudatustest?",
            template=f"pakendid 05.03.26 {OTHER_GIVEN_NAME}",
        )
    ]

    plan = plan_with(world, rows)

    assert plan.candidates == []
    assert len(plan.unmatched_campaigns) == 1


def test_a_wrong_owner_rejects_the_match(world):
    add_matter(world, owner_cell=GIVEN_NAME)
    rows = [
        export_row(
            section="Mida arvad sünteetilise pakendiseaduse muudatustest?",
            template=f"pakendid 05.03.26 {OTHER_GIVEN_NAME}",
        )
    ]

    assert plan_with(world, rows).candidates == []


def test_a_date_incompatible_campaign_rejects(world):
    """Outside the consultation window is outside the candidate set.

    Asking members after the deadline is not how the work runs, and asking
    before the file arrived is impossible.
    """
    add_matter(world, owner_cell=GIVEN_NAME, received="01.06.2026", deadline="30.06.2026")
    rows = [
        export_row(
            section="Mida arvad sünteetilise pakendiseaduse muudatustest?",
            template=f"pakendid 05.03.26 {GIVEN_NAME}",
        )
    ]

    assert plan_with(world, rows).candidates == []


def test_a_matter_with_no_window_is_never_a_candidate(world):
    """No recorded consultation period, no placement.

    Owner and wording alone would be exactly the fuzzy match this refuses.
    """
    add_matter(world, owner_cell=GIVEN_NAME, received="", deadline="")
    rows = [
        export_row(
            section="Mida arvad sünteetilise pakendiseaduse muudatustest?",
            template=f"pakendid 05.03.26 {GIVEN_NAME}",
        )
    ]

    assert plan_with(world, rows).candidates == []


def test_a_real_consultation_may_only_be_a_candidate(world):
    """The honest case, and the reason the reviewed file exists.

    The register titles the file by its instrument and the campaign titles it by
    its subject. They share no content word and they are the same consultation.
    A matcher confident enough to link them would link things that are not.
    """
    add_matter(
        world,
        title="Sünteetilise võlaõigusseaduse muutmise seaduse eelnõu (direktiiv 2024/825)",
        owner_cell=GIVEN_NAME,
    )
    rows = [
        export_row(
            section="Mida arvad toodete vastupidavusest ja parandatavusest?",
            template=f"jaekauplejad 05.03.26 {GIVEN_NAME}",
        )
    ]

    plan = plan_with(world, rows)

    assert len(plan.candidates) == 1
    assert plan.candidates[0].confidence == Confidence.CANDIDATE
    assert plan.high_confidence == []


def test_the_plan_writes_nothing_at_all(world):
    add_matter(world, owner_cell=GIVEN_NAME)
    rows = [
        export_row(
            section="Mida arvad sünteetilise pakendiseaduse muudatustest?",
            template=f"pakendid 05.03.26 {GIVEN_NAME}",
        )
    ]

    plan = plan_with(world, rows)
    candidate_rows(plan)

    assert MatterEngagement.objects.count() == 0
    assert RegisterEngagementImport.objects.count() == 0
    assert summary(plan)["writes_without_reviewed_mapping"] == 0


# ---------------------------------------------------------------------------
# The reviewed mapping
# ---------------------------------------------------------------------------


def approved(matter, **overrides):
    entry = {
        "reference": matter.display_reference,
        "channel": OutreachChannel.EMAIL_CAMPAIGN,
        "source_key": TEMPLATE_URL,
        "title": "Mida arvad sünteetilise pakendiseaduse muudatustest?",
        "url": TEMPLATE_URL,
        "occurred_on": "2026-03-05",
        "note": campaign_note(789),
    }
    entry.update(overrides)
    return read_mapping([entry])


def test_a_reviewed_mapping_creates_the_engagement(world):
    matter = add_matter(world)
    links = approved(matter)

    result = apply_mapping(links=links, expect_mapping_sha256=mapping_digest(links))

    assert result.created == 1
    engagement = MatterEngagement.objects.get(matter=matter)
    assert engagement.kind == EngagementKind.EMAIL_CAMPAIGN
    assert engagement.occurred_on == dt.date(2026, 3, 5)
    assert engagement.note == "Sendsmaily adressaate: 789"


def test_re_applying_the_same_mapping_creates_no_duplicate(world):
    matter = add_matter(world)
    links = approved(matter)
    digest = mapping_digest(links)

    apply_mapping(links=links, expect_mapping_sha256=digest)
    second = apply_mapping(links=links, expect_mapping_sha256=digest)

    assert second.created == 0
    assert second.unchanged == 1
    assert MatterEngagement.objects.filter(matter=matter).count() == 1
    assert RegisterEngagementImport.objects.filter(matter=matter).count() == 1


def test_a_corrected_title_does_not_duplicate_the_record(world):
    """The case title matching cannot survive.

    Correcting a row is the only editing ``Kaasamine`` supports, and identity
    that moved when somebody fixed a typo would add a second copy of the thing
    they were tidying.
    """
    matter = add_matter(world)
    links = approved(matter)
    apply_mapping(links=links, expect_mapping_sha256=mapping_digest(links))

    engagement = MatterEngagement.objects.get(matter=matter)
    engagement.title = "Inimese parandatud pealkiri"
    engagement.save(update_fields=["title"])

    apply_mapping(links=links, expect_mapping_sha256=mapping_digest(links))

    assert MatterEngagement.objects.filter(matter=matter).count() == 1


def test_a_web_call_and_a_campaign_are_two_records_not_a_duplicate(world):
    """Two outreach channels, both true, on one Matter.

    Members were e-mailed *and* anybody could respond through the public page.
    Deduplicating one against the other would delete a fact.
    """
    matter = add_matter(world)
    links = read_mapping(
        [
            {
                "reference": matter.display_reference,
                "channel": OutreachChannel.EMAIL_CAMPAIGN,
                "source_key": TEMPLATE_URL,
                "title": "Sünteetiline kiri liikmetele",
                "url": TEMPLATE_URL,
                "occurred_on": "2026-03-05",
            },
            {
                "reference": matter.display_reference,
                "channel": OutreachChannel.PUBLIC_PAGE,
                "source_key": PUBLIC_URL,
                "title": "Sünteetiline avalik konsultatsioon",
                "url": PUBLIC_URL,
                "occurred_on": "2026-03-02",
            },
        ]
    )

    apply_mapping(links=links, expect_mapping_sha256=mapping_digest(links))

    kinds = set(MatterEngagement.objects.filter(matter=matter).values_list("kind", flat=True))
    assert kinds == {EngagementKind.EMAIL_CAMPAIGN, EngagementKind.WEB_CALL}
    assert RegisterEngagementImport.objects.filter(matter=matter).count() == 2


def test_the_wrong_mapping_digest_writes_nothing(world):
    matter = add_matter(world)
    links = approved(matter)

    with pytest.raises(MappingError):
        apply_mapping(links=links, expect_mapping_sha256="0" * 64)

    assert MatterEngagement.objects.count() == 0


def test_a_mapping_naming_an_unknown_matter_writes_nothing(world):
    add_matter(world)
    links = read_mapping(
        [
            {
                "reference": "2026_9999",
                "channel": OutreachChannel.EMAIL_CAMPAIGN,
                "source_key": TEMPLATE_URL,
                "title": "Sünteetiline",
            }
        ]
    )

    with pytest.raises(MappingError):
        apply_mapping(links=links, expect_mapping_sha256=mapping_digest(links))
    assert MatterEngagement.objects.count() == 0


def test_a_mapping_without_a_source_key_is_refused():
    """No identity, no idempotency: it is refused at read time, not at write."""
    with pytest.raises(MappingError):
        read_mapping([{"reference": "2026_1", "channel": "EMAIL_CAMPAIGN", "title": "x"}])


def test_the_same_link_approved_twice_is_refused():
    with pytest.raises(MappingError):
        read_mapping(
            [
                {
                    "reference": "2026_1",
                    "channel": OutreachChannel.EMAIL_CAMPAIGN,
                    "source_key": TEMPLATE_URL,
                    "title": "x",
                },
                {
                    "reference": "2026_1",
                    "channel": OutreachChannel.EMAIL_CAMPAIGN,
                    "source_key": TEMPLATE_URL,
                    "title": "y",
                },
            ]
        )


def test_no_analytics_reach_the_engagement(world):
    """Opens, clicks, bounces and the rest never leave the export.

    They are engagement analytics about identifiable members, they answer no
    question the file asks, and only the recipient count is recorded.
    """
    matter = add_matter(world)
    links = approved(matter)
    apply_mapping(links=links, expect_mapping_sha256=mapping_digest(links))

    engagement = MatterEngagement.objects.get(matter=matter)
    stored = f"{engagement.title} {engagement.note} {engagement.url}"
    assert "4242" not in stored
    for column in REFUSED_CAMPAIGN_COLUMNS:
        assert column.lower() not in stored.lower()


def test_the_recipient_count_is_not_the_registers_feedback_count(world):
    """Two populations, both true, and neither replaces the other.

    The mailing went to 789 addresses; the register records how many members
    were asked directly. They are different questions about different sets and
    they live on different records.
    """
    matter = add_matter(world)
    links = approved(matter)
    apply_mapping(links=links, expect_mapping_sha256=mapping_digest(links))

    engagement = MatterEngagement.objects.get(matter=matter)
    assert "Sendsmaily" in engagement.note

    state = matter.current_register_state if hasattr(matter, "current_register_state") else None
    if state is not None:
        assert state.member_feedback_requested != 789 or state.member_feedback_requested is None


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------


def test_an_imported_engagement_obeys_matter_visibility(world):
    """Inherited through the Matter, exactly like a hand-made one.

    An importer that produced a child record visible more widely than its parent
    would be AUTH-003 with a different spelling — so the Matter here is
    restricted, which is the only state where the two answers differ.
    """
    people = world.people
    matter = add_matter(world, visibility=Visibility.RESTRICTED)
    links = approved(matter)
    apply_mapping(links=links, expect_mapping_sha256=mapping_digest(links))

    engagement = MatterEngagement.objects.get(matter=matter)
    # Derived from the parent rather than stored, which is what makes it
    # impossible for an importer to leave a stale, less-restrictive copy.
    assert engagement.visibility_override == ""
    assert engagement.effective_visibility == matter.visibility == Visibility.RESTRICTED
    assert not MatterEngagement.objects.visible_to(people.reader).filter(pk=engagement.pk).exists()
    assert not MatterEngagement.objects.visible_to(None).filter(pk=engagement.pk).exists()


def test_a_web_call_obeys_matter_visibility(world):
    people = world.people
    matter = add_matter(world, visibility=Visibility.RESTRICTED)
    links = read_mapping(
        [
            {
                "reference": matter.display_reference,
                "channel": OutreachChannel.PUBLIC_PAGE,
                "source_key": PUBLIC_URL,
                "title": "Sünteetiline avalik konsultatsioon",
                "url": PUBLIC_URL,
            }
        ]
    )
    apply_mapping(links=links, expect_mapping_sha256=mapping_digest(links))

    engagement = MatterEngagement.objects.get(matter=matter)
    assert engagement.kind == EngagementKind.WEB_CALL
    assert engagement.effective_visibility == Visibility.RESTRICTED
    # A public page is public; the *pointer* on a restricted file is not, and
    # the record inherits the file rather than the page.
    assert not MatterEngagement.objects.visible_to(people.reader).filter(pk=engagement.pk).exists()


def test_the_search_projection_is_refreshed_for_touched_matters(world):
    """Search is derived and stays behind unless something refreshes it.

    Nothing here changes ``INDEX_VERSION``, the ranking or the tokenisation —
    the existing mechanism is used, once, after the writes.
    """
    matter = add_matter(world)
    links = approved(matter)
    apply_mapping(links=links, expect_mapping_sha256=mapping_digest(links))

    document = SearchDocument.objects.filter(matter=matter).first()
    assert document is not None


# ---------------------------------------------------------------------------
# The plan matches against the portfolio the refresh would produce
# ---------------------------------------------------------------------------
#
# The matcher used to read `CurrentRegisterState` and `Matter` from the
# database. Before an apply those describe the *previous* workbook, so a
# dry-run's campaign candidates were computed from facts the refresh was about
# to overwrite — and on a database that had never seen the snapshot, from
# nothing at all. The tests below are the correction, and each one fails against
# a matcher that reads stored state.


def restate(register, matter, **cells):
    """What a newer workbook says about a Matter that already has a row.

    It adds a row; it does not remove the old one. Source references are
    immutable evidence, and the reconciliation resolves a Matter to the last row
    the snapshot names it on.
    """
    return add_source_reference(
        register,
        matter,
        snapshot=SNAPSHOT,
        status_cell=cells.pop("status_cell", "kooskõlastusringil"),
        **cells,
    )


def test_the_plan_matches_before_anything_has_been_written(world):
    """The defect, stated as the property it broke.

    Nothing has been applied and the derived state table is empty, which is
    exactly the state an operator reads a plan in. The campaign still has to
    find its Matter.
    """
    matter = add_matter(world, owner_cell=GIVEN_NAME)
    rows = [
        export_row(
            section="Mida arvad sünteetilise pakendiseaduse muudatustest?",
            template=f"pakendid 05.03.26 {GIVEN_NAME}",
        )
    ]

    assert CurrentRegisterState.objects.count() == 0
    plan = plan_with(world, rows)

    assert [candidate.matter_id for candidate in plan.high_confidence] == [matter.pk]
    assert CurrentRegisterState.objects.count() == 0


def test_a_deadline_the_newer_workbook_moves_decides_eligibility_in_plan_mode(world):
    """Window A excludes the campaign; window B includes it.

    The database holds A and the newer snapshot says B, and the plan must
    answer B — before any write. A matcher reading the stored deadline sees A
    and reports no candidate, which is the shape of the defect.
    """
    matter = add_matter(world, owner_cell=GIVEN_NAME, received="16.02.2026", deadline="20.02.2026")
    rows = [
        export_row(
            section="Mida arvad sünteetilise pakendiseaduse muudatustest?",
            template=f"pakendid 05.03.26 {GIVEN_NAME}",
        )
    ]

    # Window A: the campaign falls after the consultation closed.
    assert plan_with(world, rows).candidates == []

    # The newer workbook extends the deadline past the campaign.
    restate(
        world,
        matter,
        owner_cell=GIVEN_NAME,
        received_cell="16.02.2026",
        deadline_cell="10.03.2026",
    )

    plan = plan_with(world, rows)
    assert [candidate.matter_id for candidate in plan.candidates] == [matter.pk]
    # Still nothing written: the eligibility came from the projection, and the
    # canonical record still holds the old deadline.
    assert CurrentRegisterState.objects.count() == 0
    matter.refresh_from_db()
    assert matter.response_deadline == dt.date(2026, 2, 20)


def test_an_owner_the_newer_workbook_moves_decides_eligibility_in_plan_mode(world):
    """The campaign names one lawyer; the register hands the file to another.

    The owner filter is hard, so the same campaign matches under one snapshot
    and not the other — and the plan must use the newer ``VASTUTAJA``.
    """
    matter = add_matter(world, owner_cell=OTHER_GIVEN_NAME)
    rows = [
        export_row(
            section="Mida arvad sünteetilise pakendiseaduse muudatustest?",
            template=f"pakendid 05.03.26 {GIVEN_NAME}",
        )
    ]

    assert plan_with(world, rows).candidates == []

    restate(
        world,
        matter,
        owner_cell=GIVEN_NAME,
        received_cell="16.02.2026",
        deadline_cell="10.03.2026",
    )

    plan = plan_with(world, rows)
    assert [candidate.matter_id for candidate in plan.candidates] == [matter.pk]
    assert plan.candidates[0].owner_raw == GIVEN_NAME


def test_a_matter_the_newer_workbook_retires_is_not_a_campaign_target(world):
    """The portfolio the refresh *produces*, not the one it starts from.

    A terminal ``HETKESEIS`` takes the Matter out of current work, so its old
    dates no longer place anything however well they fit.
    """
    matter = add_matter(world, owner_cell=GIVEN_NAME)
    rows = [
        export_row(
            section="Mida arvad sünteetilise pakendiseaduse muudatustest?",
            template=f"pakendid 05.03.26 {GIVEN_NAME}",
        )
    ]
    assert plan_with(world, rows).candidates

    restate(
        world,
        matter,
        status_cell="rohkem pole tegevusi plaanis",
        owner_cell=GIVEN_NAME,
        received_cell="16.02.2026",
        deadline_cell="10.03.2026",
    )

    assert plan_with(world, rows).candidates == []


def test_the_matcher_asks_the_database_nothing(world, django_assert_num_queries):
    """A pure computation over the projection and the campaign list.

    Not a performance nicety: a query here is a fact read from the wrong
    snapshot, which is the whole defect. Zero is the only number that cannot
    regress back into it.
    """
    add_matter(world, owner_cell=GIVEN_NAME)
    rows = [
        export_row(
            section="Mida arvad sünteetilise pakendiseaduse muudatustest?",
            template=f"pakendid 05.03.26 {GIVEN_NAME}",
        )
    ]
    campaigns, _ = read_campaigns(rows, since=WINDOW[0], until=WINDOW[1])
    refresh = build_refresh_plan(snapshot_sha256=SNAPSHOT, today=SNAPSHOT_DATE)
    targets = outreach_targets(
        refresh.cutover, people=KnownPeople.load(), mappings=MappingTables.empty()
    )

    with django_assert_num_queries(0):
        build_outreach_plan(
            snapshot_sha256=SNAPSHOT,
            campaigns=campaigns,
            targets=targets,
            since=WINDOW[0],
            until=WINDOW[1],
        )


def test_planning_with_campaigns_writes_nothing_anywhere(world):
    """Every table the refresh can touch, counted before and after.

    The plan may allocate ordinary objects; it may not leave one behind, and it
    may not bump a Matter's ``updated_at`` either.
    """
    add_matter(world, owner_cell=GIVEN_NAME)
    rows = [
        export_row(
            section="Mida arvad sünteetilise pakendiseaduse muudatustest?",
            template=f"pakendid 05.03.26 {GIVEN_NAME}",
        )
    ]

    def census():
        return {
            "matters": Matter.objects.count(),
            "state": CurrentRegisterState.objects.count(),
            "actions": NextAction.objects.count(),
            "engagements": MatterEngagement.objects.count(),
            "imports": RegisterEngagementImport.objects.count(),
            "events": ChangeEvent.objects.count(),
            "matter_rows": sorted(
                (str(pk), stamp) for pk, stamp in Matter.objects.values_list("pk", "updated_at")
            ),
        }

    before = census()
    plan = plan_with(world, rows)
    candidate_rows(plan)
    summary(plan)

    assert census() == before
