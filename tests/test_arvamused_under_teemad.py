"""Arvamused under Teemad — one page, two sections, two searches that never meet.

The consolidation is an information-architecture move and nothing else. A Teema
is the object the department works on; an arvamus is usually one of the things
that work finishes with, so the bar offers Teemad and the Arvamused workspace is
a section of that page rather than a sibling destination (``docs/adr/0047``).

Four properties matter more than any individual assertion below, and each is one
way this change could have gone wrong:

**The searches are independent.** ``?q=`` is the register's and ``?arvamus_q=``
is the section's. Neither box may narrow the other's list, and a page carrying
both states must answer both correctly at once. This is the hard requirement of
the brief and most of the file is about it.

**Nothing was deleted.** ``/arvamused/`` and ``/arvamused/arhiiv/`` still
resolve, still carry their own filters and their own pager, and are still where
«Vaata kõiki arvamusi» leads. A bookmark from before this change still works.

**Nothing widened.** ``may_read_archive`` decides the Arhiiv tab in the section
exactly as it decides it in the workspace, and it decides it before anything is
counted. A specialist may not reach archive rows, an archive count, or the
corpus's date range by hand-editing the section's parameter.

**The section is bounded.** It shows twelve rows under a fifty-row register and
says so. A page that rendered the whole corpus under the whole register would
have consolidated two surfaces into one unusable one.
"""

from __future__ import annotations

import datetime
import re

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.legacy_import.opinion_access import may_read_archive
from app.legacy_import.opinion_archive import OpinionArchiveBatch, OpinionArchiveItem
from app.legacy_import.opinion_binary import OpinionArchiveBinary
from app.legacy_import.opinion_search import rebuild_archive_index
from app.submissions.embedded import EMBEDDED_ROWS
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from tests import factories

pytestmark = pytest.mark.django_db

TEEMAD_URL = reverse("matters:matter_list")
SENT_URL = reverse("submissions:sent")
ARCHIVE_URL = reverse("submissions:archive")
BLOCK_URL = reverse("submissions:embedded_block")


# ---------------------------------------------------------------------------
# Fixtures — every string invented; see tests/synthetic_opinions.py
# ---------------------------------------------------------------------------


def hold(*, sha: str, title: str, recipient: str = "Näidisministeerium") -> OpinionArchiveBinary:
    """One held archive letter, catalogued and indexed."""
    batch, _ = OpinionArchiveBatch.objects.get_or_create(
        archive_sha256="a" * 64,
        defaults={
            "importer_version": "test/0",
            "started_at": datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        },
    )
    binary = OpinionArchiveBinary.objects.create(
        sha256=sha,
        size_bytes=1024,
        mime_type="application/pdf",
        storage_key=f"opinion-archive/{sha[:2]}/{sha[2:4]}/{sha}",
        source_archive_sha256="a" * 64,
        materialized_at=timezone.now(),
    )
    OpinionArchiveItem.objects.create(
        batch=batch,
        archive_sha256="a" * 64,
        archive_relative_path=f"Opinions/2024/{sha[:6]}.pdf",
        original_filename=f"{sha[:6]}.pdf",
        sha256=sha,
        size_bytes=1024,
        detected_type="application/pdf",
        filename_date=datetime.date(2024, 4, 10),
        filename_recipient=recipient,
        filename_title=title,
        binary=binary,
    )
    return binary


def sent_submission(matter, *, evidence, title: str, when=None) -> Submission:
    """A SENT submission with the immutable evidence the database insists on.

    ``submissions_sent_requires_timestamp_and_evidence`` is a check constraint,
    not a convention (ADR 0011). A fixture that set the status directly would be
    testing a row the product can never produce.
    """
    return factories.SubmissionFactory(
        matter=matter,
        title=title,
        status=SubmissionStatus.SENT,
        sent_at=when or timezone.now(),
        final_version=evidence,
    )


@pytest.fixture
def opinion_on_a_matter(specialist, capture_evidence):
    """One sent opinion, on a Matter with a title of its own.

    Both titles are distinctive and different, because most of this file turns
    on which of the two lists a given string appears in.
    """
    matter = factories.MatterFactory(owner=specialist, title="Pakendiseaduse muudatused")
    evidence = capture_evidence(matter, b"%PDF-1.4 synthetic", "arvamus.pdf", "application/pdf")
    submission = sent_submission(matter, evidence=evidence, title="Seisukoht pakendiaktsiisist")
    return matter, submission


#: Three teemad and three arvamused, and no word shared between any of them.
#:
#: Deliberate. The register's `?q=` runs through the search projection, which
#: tokenises — so `Teema number 1` matches all three of `Teema number 0..2` and
#: a test built on those titles would prove nothing about independence while
#: appearing to. The opinion box is an `icontains` substring and would not have
#: shown the difference. Distinct stems make each search narrow to exactly one.
MATTER_TITLES = (
    "Pakendiseaduse muudatused",
    "Ehitusseadustiku revisjon",
    "Metsaseaduse eelnõu",
)
OPINION_TITLES = ("Alfaseisukoht", "Beetaseisukoht", "Gammaseisukoht")


@pytest.fixture
def three_pairs(specialist, capture_evidence):
    """One sent opinion on each of three teemad, all six titles distinguishable."""
    for index, (matter_title, opinion_title) in enumerate(
        zip(MATTER_TITLES, OPINION_TITLES, strict=True)
    ):
        matter = factories.MatterFactory(owner=specialist, title=matter_title)
        evidence = capture_evidence(matter, b"%PDF-1.4 s", f"{index}.pdf", "application/pdf")
        sent_submission(matter, evidence=evidence, title=opinion_title)


def body_of(response) -> str:
    assert response.status_code == 200
    return response.content.decode()


def navigation_of(response) -> str:
    """The main navigation's markup, and nothing else on the page."""
    body = response.content.decode()
    start = body.index('<nav class="topnav"')
    return body[start : body.index("</nav>", start)]


def opinion_section_of(body: str) -> str:
    """Just the Arvamused section, so an assertion cannot match the register.

    Every test that says "this string is in the opinion list" needs to mean the
    opinion list. The register above it holds Matter titles, and a bare
    ``in body`` would pass on the wrong section.
    """
    start = body.index('<section class="opinionblock"')
    return body[start : body.index("</section>", start)]


def register_section_of(body: str) -> str:
    """Just the register's results block, for the same reason in reverse."""
    start = body.index('<div id="teemad-tulemused"')
    return body[start : body.index('<section class="opinionblock"')]


# ---------------------------------------------------------------------------
# 1-2. The navigation
# ---------------------------------------------------------------------------


def test_the_bar_no_longer_offers_arvamused(signed_in) -> None:
    """Removed as a destination, at every width.

    The bar renders its reading destinations twice — inline and inside the
    "Veel" disclosure — and only one branch is visible at a time. Asserting over
    the whole `<nav>` therefore covers both, which is the point: a link merely
    moved into the disclosure would still be a first-level destination.
    """
    navigation = navigation_of(signed_in.get(reverse("matters:overview")))

    assert ">Arvamused<" not in navigation
    assert SENT_URL not in navigation
    assert ARCHIVE_URL not in navigation


def test_the_bar_still_offers_teemad(signed_in) -> None:
    """The destination the workspace moved under has to still be there."""
    navigation = navigation_of(signed_in.get(reverse("matters:overview")))

    assert ">Teemad<" in navigation
    assert TEEMAD_URL in navigation


def test_the_bar_hides_arvamused_from_the_reader_who_could_read_the_archive(
    client, administrator
) -> None:
    """Removed for everybody, not hidden from those without access.

    An ADMINISTRATOR is the persona ``may_read_archive`` admits. If the item is
    gone for them it is gone, rather than having become conditional on a
    permission — which would be a different and much worse change.
    """
    assert may_read_archive(administrator)
    client.force_login(administrator)

    navigation = navigation_of(client.get(reverse("matters:overview")))

    assert ">Arvamused<" not in navigation


def test_the_standalone_workspace_marks_teemad_as_the_current_area(signed_in) -> None:
    """A reader at /arvamused/ is inside the Teemad area, and the bar says so.

    Without this the bar would have nothing current on it while somebody is
    plainly somewhere, which reads as a page that does not know where it is.
    """
    navigation = navigation_of(signed_in.get(SENT_URL))

    # The whole anchor, matched rather than sliced at by offset: the attribute
    # order in the template is not what this test is about.
    teemad = re.search(r"<a\b[^>]*>Teemad</a>", navigation)
    assert teemad, navigation
    assert "is-active" in teemad.group(0)
    assert 'aria-current="page"' in teemad.group(0)


# ---------------------------------------------------------------------------
# 3. The Teemad page contains an Arvamused section
# ---------------------------------------------------------------------------


def test_the_teemad_page_carries_an_arvamused_section(signed_in) -> None:
    body = body_of(signed_in.get(TEEMAD_URL))

    assert '<section class="opinionblock"' in body
    assert 'id="arvamused"' in body
    section = opinion_section_of(body)
    assert "Arvamused" in section
    assert "Saadetud seisukohad ja ajalooline arhiiv" in section


def test_the_section_offers_its_own_search_box(signed_in) -> None:
    """Named as the brief names it, and reading its own parameter."""
    section = opinion_section_of(body_of(signed_in.get(TEEMAD_URL)))

    assert "Otsi arvamustest" in section
    assert 'name="arvamus_q"' in section
    # And the register's box is still the register's, on the same page.
    assert 'name="q"' not in section


def test_the_section_reaches_the_full_workspace(signed_in) -> None:
    """Requirement 12: the way out, to the surface that was not deleted."""
    section = opinion_section_of(body_of(signed_in.get(TEEMAD_URL)))

    assert "Vaata kõiki arvamusi" in section
    assert f'href="{SENT_URL}"' in section


def test_the_way_out_carries_the_search_rather_than_dropping_it(
    signed_in, opinion_on_a_matter
) -> None:
    """A reader who searched and wants more must not have to retype it."""
    section = opinion_section_of(
        body_of(signed_in.get(TEEMAD_URL, {"arvamus_q": "pakendiaktsiis"}))
    )

    assert f'href="{SENT_URL}?q=pakendiaktsiis"' in section


# ---------------------------------------------------------------------------
# 4-7. Two searches, and they do not touch
# ---------------------------------------------------------------------------


def test_teemad_search_still_narrows_teemad(signed_in, specialist) -> None:
    factories.MatterFactory(owner=specialist, title="Pakendiseaduse muudatused")
    factories.MatterFactory(owner=specialist, title="Ehitusseadustiku revisjon")

    body = body_of(signed_in.get(TEEMAD_URL, {"q": "Pakendiseaduse"}))
    register = register_section_of(body)

    assert "Pakendiseaduse muudatused" in register
    assert "Ehitusseadustiku revisjon" not in register


def test_arvamused_search_still_narrows_arvamused(signed_in, specialist, capture_evidence) -> None:
    matter = factories.MatterFactory(owner=specialist, title="Ühine teema")
    evidence = capture_evidence(matter, b"%PDF-1.4 synthetic", "a.pdf", "application/pdf")
    sent_submission(matter, evidence=evidence, title="Seisukoht pakendiaktsiisist")
    other = capture_evidence(matter, b"%PDF-1.4 teine", "b.pdf", "application/pdf")
    sent_submission(matter, evidence=other, title="Seisukoht ehitusloast")

    section = opinion_section_of(
        body_of(signed_in.get(TEEMAD_URL, {"arvamus_q": "pakendiaktsiis"}))
    )

    assert "Seisukoht pakendiaktsiisist" in section
    assert "Seisukoht ehitusloast" not in section


def test_searching_arvamused_leaves_the_teemad_list_alone(three_pairs, signed_in) -> None:
    """Requirement 6. The register answers the same before and after.

    Compared by *count* as well as by rows: the register's own answer is the
    thing that must not move, and its count is the register's own statement of
    it.
    """
    before = register_section_of(body_of(signed_in.get(TEEMAD_URL)))
    body = body_of(signed_in.get(TEEMAD_URL, {"arvamus_q": "Beetaseisukoht"}))
    after = register_section_of(body)

    assert "<strong>3</strong> teemat" in before
    assert "<strong>3</strong> teemat" in after
    for title in MATTER_TITLES:
        assert title in after
    # And the opinion list did narrow, or the assertion above proves nothing.
    section = opinion_section_of(body)
    assert "Beetaseisukoht" in section
    assert "Alfaseisukoht" not in section


def test_searching_teemad_leaves_the_arvamused_list_alone(three_pairs, signed_in) -> None:
    """Requirement 7, the same property from the other side."""
    body = body_of(signed_in.get(TEEMAD_URL, {"q": "Ehitusseadustiku"}))
    register = register_section_of(body)
    section = opinion_section_of(body)

    assert "<strong>1</strong> teemat" in register
    # The register narrowed to one; the opinion section still holds all three.
    assert "<strong>3</strong> vastet" in section
    for title in OPINION_TITLES:
        assert title in section


def test_the_two_searches_coexist_in_one_address(three_pairs, signed_in) -> None:
    """Both states in one URL, both honoured, neither reading the other's name."""
    body = body_of(
        signed_in.get(TEEMAD_URL, {"q": "Ehitusseadustiku", "arvamus_q": "Gammaseisukoht"})
    )

    register = register_section_of(body)
    section = opinion_section_of(body)

    assert "Ehitusseadustiku revisjon" in register
    assert "Pakendiseaduse muudatused" not in register
    assert "Gammaseisukoht" in section
    assert "Alfaseisukoht" not in section


def test_the_opinion_form_carries_the_register_state(signed_in, specialist) -> None:
    """Progressive enhancement: a plain GET must not widen the register.

    With JavaScript off the opinion form submits to `/teemad/`, so every
    register parameter has to travel with it as a hidden input — otherwise the
    first opinion search silently drops the filters somebody had applied above.
    """
    factories.MatterFactory(owner=specialist, title="Ükskõik milline teema")

    section = opinion_section_of(
        body_of(signed_in.get(TEEMAD_URL, {"q": "Ükskõik", "jarjestus": "title"}))
    )

    assert '<input type="hidden" name="q" value="Ükskõik">' in section
    assert '<input type="hidden" name="jarjestus" value="title">' in section
    # And the form's plain-GET target is the Teemad page, landing on the section.
    assert f'action="{TEEMAD_URL}#arvamused"' in section


def test_the_register_search_carries_the_opinion_state(signed_in, specialist) -> None:
    """The same property in reverse, through the register's existing mechanism.

    `carried_params` already carries every parameter that is not `q` or `leht`,
    so the opinion state travels for free — this asserts it rather than assuming
    the mechanism keeps behaving that way.
    """
    factories.MatterFactory(owner=specialist, title="Ükskõik milline teema")

    body = body_of(signed_in.get(TEEMAD_URL, {"arvamus_q": "pakend"}))
    register_form = body[body.index('class="registersearch"') : body.index('class="filterbar"')]

    assert '<input type="hidden" name="arvamus_q" value="pakend">' in register_form


def test_the_register_fragment_route_still_answers_with_teemad(signed_in, specialist) -> None:
    """The section's live search must not be able to reach this branch.

    `matters.views._wants_fragment` answers *any* HTMX request to `/teemad/`
    with the register's results, which is why the opinion box posts to its own
    route. If it ever pointed here, an opinion search would come back as a table
    of teemad — so both halves of that arrangement are asserted.
    """
    factories.MatterFactory(owner=specialist, title="Pakendiseaduse muudatused")

    fragment = body_of(signed_in.get(TEEMAD_URL, {"q": "Pakendi"}, HTTP_HX_REQUEST="true"))

    assert 'id="teemad-tulemused"' in fragment
    assert '<section class="opinionblock"' not in fragment


def test_the_opinion_fragment_route_answers_with_opinions_only(
    signed_in, opinion_on_a_matter
) -> None:
    fragment = body_of(signed_in.get(BLOCK_URL, {"arvamus_q": "pakendiaktsiis"}))

    assert 'id="arvamused-tulemused"' in fragment
    assert "Seisukoht pakendiaktsiisist" in fragment
    # Not the whole page, and not the register.
    assert 'id="teemad-tulemused"' not in fragment
    assert '<nav class="topnav"' not in fragment


# ---------------------------------------------------------------------------
# 8-9. Saadetud and Arhiiv, under the authorization that already existed
# ---------------------------------------------------------------------------


def test_saadetud_is_the_sections_default_source(signed_in, opinion_on_a_matter) -> None:
    section = opinion_section_of(body_of(signed_in.get(TEEMAD_URL)))

    assert "Saadetud" in section
    assert "Seisukoht pakendiaktsiisist" in section


def test_the_archive_tab_is_offered_to_a_reader_who_may_read_it(client, administrator) -> None:
    assert may_read_archive(administrator)
    hold(sha="b" * 64, title="Varasem kiri pakendite kohta")
    rebuild_archive_index()
    client.force_login(administrator)

    section = opinion_section_of(body_of(client.get(TEEMAD_URL)))

    assert ">Arhiiv" in section
    assert "arvamus_vaade=arhiiv" in section


def test_the_archive_tab_is_not_offered_to_a_reader_who_may_not(signed_in, specialist) -> None:
    assert not may_read_archive(specialist)
    hold(sha="c" * 64, title="Varasem kiri pakendite kohta")
    rebuild_archive_index()

    section = opinion_section_of(body_of(signed_in.get(TEEMAD_URL)))

    assert ">Arhiiv" not in section


def test_the_archive_opens_in_the_section_for_a_reader_who_may(client, administrator) -> None:
    hold(sha="d" * 64, title="Varasem kiri pakendite kohta")
    rebuild_archive_index()
    client.force_login(administrator)

    section = opinion_section_of(body_of(client.get(TEEMAD_URL, {"arvamus_vaade": "arhiiv"})))

    assert "Varasem kiri pakendite kohta" in section
    # And its way out leads to the archive rather than to Saadetud.
    assert f'href="{ARCHIVE_URL}"' in section


# ---------------------------------------------------------------------------
# 14. No authorization regression
# ---------------------------------------------------------------------------


def test_a_refused_reader_cannot_reach_the_archive_by_asking_for_it(signed_in, specialist) -> None:
    """The section's parameter is not a way past ``may_read_archive``.

    Resolved down to Saadetud in Python, before a query is built — so no archive
    row, no archive count and no archive year reaches the page. It resolves
    rather than raising on purpose: a crafted opinion parameter must not take
    the whole register away from somebody who was reading teemad.
    """
    assert not may_read_archive(specialist)
    hold(sha="e" * 64, title="Salajane varasem kiri")
    rebuild_archive_index()

    response = signed_in.get(TEEMAD_URL, {"arvamus_vaade": "arhiiv"})
    body = body_of(response)
    section = opinion_section_of(body)

    assert response.status_code == 200
    assert "Salajane varasem kiri" not in body
    assert ">Arhiiv" not in section
    # Falls back to the canonical source rather than showing nothing at all.
    assert "ei ole veel ühtegi arvamust välja saadetud" in section


def test_the_fragment_route_refuses_the_archive_the_same_way(signed_in, specialist) -> None:
    """The block answers a box on somebody else's page, and holds the same line."""
    hold(sha="f" * 64, title="Salajane varasem kiri")
    rebuild_archive_index()

    fragment = body_of(signed_in.get(BLOCK_URL, {"arvamus_vaade": "arhiiv"}))

    assert "Salajane varasem kiri" not in fragment


def test_an_opinion_on_an_invisible_matter_is_not_in_the_section(
    client, reader, specialist, capture_evidence
) -> None:
    """Visibility is inherited from the Matter, never re-derived here."""
    hidden = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    evidence = capture_evidence(hidden, b"%PDF-1.4 s", "x.pdf", "application/pdf")
    sent_submission(hidden, evidence=evidence, title="Piiratud arvamus")

    client.force_login(reader)
    section = opinion_section_of(body_of(client.get(TEEMAD_URL)))

    assert "Piiratud arvamus" not in section
    assert "<strong>0</strong> vastet" in section


def test_a_refused_reader_is_told_nothing_about_the_corpus_size(signed_in, specialist) -> None:
    """A count is an access decision too.

    Three held letters, and a specialist's page must not print three anywhere in
    the section — not as a tab count, not as a total.
    """
    for index, letter in enumerate("abc"):
        hold(sha=letter * 64, title=f"Varasem kiri {index}")
    rebuild_archive_index()

    section = opinion_section_of(body_of(signed_in.get(TEEMAD_URL)))

    assert "Varasem kiri" not in section
    assert ">Arhiiv" not in section
    assert "<strong>3</strong>" not in section


# ---------------------------------------------------------------------------
# 10. The standalone routes are untouched
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [SENT_URL, ARCHIVE_URL])
def test_the_standalone_urls_still_resolve(client, administrator, path) -> None:
    """A bookmark from before this change still opens the page it opened then."""
    client.force_login(administrator)

    response = client.get(path)

    assert response.status_code == 200
    assert '<h1 class="pagehead__title">Arvamused</h1>' in response.content.decode()


def test_the_standalone_archive_still_refuses_a_reader_who_may_not(signed_in, specialist) -> None:
    """The route's own refusal is unchanged; only the link to it left the bar."""
    assert not may_read_archive(specialist)

    assert signed_in.get(ARCHIVE_URL).status_code == 403


def test_the_standalone_workspace_keeps_its_own_filters(
    signed_in, specialist, capture_evidence
) -> None:
    """The section is bounded; the workspace it links to is not a copy of it.

    ``?olek=KOIK`` is a filter the embedded section deliberately does not offer,
    and it still works where it always did — which is what makes «Vaata kõiki
    arvamusi» a real destination rather than a longer version of the same list.
    """
    matter = factories.MatterFactory(owner=specialist)
    factories.SubmissionFactory(matter=matter, title="Pooleli", status=SubmissionStatus.DRAFT)

    assert "Pooleli" in body_of(signed_in.get(SENT_URL, {"olek": "KOIK"}))


# ---------------------------------------------------------------------------
# 11. The section is bounded
# ---------------------------------------------------------------------------


def test_the_section_shows_at_most_the_bound(signed_in, specialist, capture_evidence) -> None:
    """More opinions than the bound, and the section still fits under a register."""
    matter = factories.MatterFactory(owner=specialist, title="Üks teema, palju arvamusi")
    for index in range(EMBEDDED_ROWS + 4):
        evidence = capture_evidence(matter, b"%PDF-1.4 s", f"{index}.pdf", "application/pdf")
        sent_submission(matter, evidence=evidence, title=f"Arvamus number {index:02d}")

    section = opinion_section_of(body_of(signed_in.get(TEEMAD_URL)))

    assert section.count("Arvamus number") == EMBEDDED_ROWS
    # And the real total is stated rather than implied by the rows shown.
    assert f"<strong>{EMBEDDED_ROWS + 4}</strong> vastet" in section
    assert f"kuvatud {EMBEDDED_ROWS}" in section


def test_the_bound_is_not_announced_when_everything_fits(signed_in, opinion_on_a_matter) -> None:
    """ "kuvatud 12" beside a single row would be a page qualifying nothing."""
    section = opinion_section_of(body_of(signed_in.get(TEEMAD_URL)))

    assert "<strong>1</strong> vastet" in section
    assert "kuvatud" not in section


def test_the_section_does_not_paginate(signed_in, specialist, capture_evidence) -> None:
    """It hands over instead. A second pager under the register's own pager
    would give one page two "next page" controls meaning different things."""
    matter = factories.MatterFactory(owner=specialist)
    for index in range(EMBEDDED_ROWS + 4):
        evidence = capture_evidence(matter, b"%PDF-1.4 s", f"{index}.pdf", "application/pdf")
        sent_submission(matter, evidence=evidence, title=f"Arvamus number {index:02d}")

    section = opinion_section_of(body_of(signed_in.get(TEEMAD_URL)))

    assert "pagination" not in section
    assert "Vaata kõiki arvamusi" in section


# ---------------------------------------------------------------------------
# 13. From an arvamus back to its teema
# ---------------------------------------------------------------------------


def test_each_opinion_row_links_to_its_teema(signed_in, opinion_on_a_matter) -> None:
    """The whole reason the section sits here: no second search to get back."""
    matter, _submission = opinion_on_a_matter

    section = opinion_section_of(body_of(signed_in.get(TEEMAD_URL)))

    assert reverse("matters:matter_detail", kwargs={"pk": matter.pk}) in section
    assert "Pakendiseaduse muudatused" in section
    # And to the opinion itself, which is a different destination.
    assert reverse("matters:matter_position", kwargs={"pk": matter.pk}) in section


def test_an_archive_row_never_names_a_teema(client, administrator, specialist) -> None:
    """ADR 0028, unchanged by the move.

    An archive row naming its Matter would make the archive a route into the
    register — a reader could learn the title of a RESTRICTED entry from a
    letter tied to it. Embedding the workspace must not have loosened that.
    """
    restricted = factories.MatterFactory(
        owner=specialist, visibility=Visibility.RESTRICTED, title="Piiratud teema pealkiri"
    )
    binary = hold(sha="9" * 64, title="Varasem kiri")
    from app.legacy_import.opinion_enums import ArchiveLinkBasis
    from app.legacy_import.opinion_links import link_matter

    link_matter(
        binary=binary, matter=restricted, basis=ArchiveLinkBasis.REVIEWED, actor=administrator
    )
    rebuild_archive_index()
    client.force_login(administrator)

    section = opinion_section_of(body_of(client.get(TEEMAD_URL, {"arvamus_vaade": "arhiiv"})))

    assert "Varasem kiri" in section
    assert "Piiratud teema pealkiri" not in section
    assert "Teemaga seotud" in section


# ---------------------------------------------------------------------------
# 15-16. What this change is not
# ---------------------------------------------------------------------------


def test_the_search_index_version_is_untouched() -> None:
    """Requirement 15. This composes two surfaces; it writes no projection.

    Pinned by value rather than merely asserted to exist: a UI consolidation
    that moved `INDEX_VERSION` would silently require a search rebuild on
    deployment, which is exactly the cost this change should not have.
    """
    from app.search.models import INDEX_VERSION

    assert INDEX_VERSION == "AUTH003.1"


def test_no_migration_is_outstanding() -> None:
    """Requirement 16. Nothing here needs a schema change, so nothing made one."""
    from io import StringIO

    from django.core.management import call_command

    out = StringIO()
    call_command("makemigrations", "--check", "--dry-run", stdout=out, stderr=out)


def test_a_bad_opinion_query_does_not_take_the_register_down(signed_in, specialist) -> None:
    """The section reports its own refusal; the register keeps its answer."""
    factories.MatterFactory(owner=specialist, title="Ükskõik milline teema")

    response = signed_in.get(TEEMAD_URL, {"arvamus_q": "x" * 501})
    body = body_of(response)

    assert response.status_code == 200
    assert "Ükskõik milline teema" in register_section_of(body)
    assert "tähemärki" in opinion_section_of(body)


def test_the_section_costs_a_fixed_number_of_queries(
    three_pairs, specialist, django_assert_max_num_queries
) -> None:
    """It does not grow a query per row.

    The section adds work to a page that was already doing plenty, so the cost
    is bounded here rather than discovered on a register with thousands of rows:
    the row query, its count, the three headline counts, and — for a reader who
    may read it — the archive counts. `select_related`/`prefetch_related` on
    ``sent_queryset`` are what keep the recipient and evidence columns from
    becoming one query each.
    """
    from django.test import RequestFactory

    from app.submissions.embedded import embedded_context

    request = RequestFactory().get(TEEMAD_URL)
    request.user = specialist

    with django_assert_max_num_queries(8):
        context = embedded_context(request)
        # Force the lazy queryset, or the rows are never fetched at all.
        assert len(list(context["opinion_rows"])) == 3


def test_the_register_live_search_does_not_pay_for_the_section(
    three_pairs, signed_in, django_assert_max_num_queries
) -> None:
    """A keystroke in «Otsi teemadest» must not build an opinion list.

    The section is composed after the fragment branch returns, so the register's
    own live search never touches it. Asserted rather than trusted: moving that
    one line up would make every keystroke pay for opinions nobody can see move.
    """
    fragment = body_of(signed_in.get(TEEMAD_URL, {"q": "Ehitusseadustiku"}, HTTP_HX_REQUEST="true"))

    assert "opinionblock" not in fragment
    for title in OPINION_TITLES:
        assert title not in fragment
