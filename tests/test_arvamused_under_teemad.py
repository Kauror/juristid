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

**Nothing widened.** ``may_read_archive`` decides the Arhiivikirjad tab in the section
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

#: Which Teema each of those opinions came out of.
#:
#: A sent row is headed by its *Teema*: the arvamus's own title is a near-copy
#: of it, and printing both gave every row two headings saying the same thing.
#: So a test that searches by one and looks for the other is doing exactly what
#: it means to — the search still reads `Submission.title`, and the row names
#: the work that title belongs to.
TEEMA_OF = dict(zip(OPINION_TITLES, MATTER_TITLES, strict=True))


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


def submission_rows_of(section: str) -> list[str]:
    """Each sent row's own markup, one string per row.

    Needed because most of what this file now checks about a row is a *negative*
    — no version number, no byte size, no «Teema» label — and a negative
    asserted over a whole section is answered by whatever else is on the page.
    Scoped to the article, "v1 is not there" means the row.
    """
    parts = section.split('<article class="submission">')[1:]
    return [part[: part.index("</article>")] for part in parts]


def meta_labels_of(row: str) -> list[str]:
    """The row's metadata labels, in the order they are rendered."""
    return [label.strip() for label in re.findall(r"<dt[^>]*>(.*?)</dt>", row, re.S)]


def file_cell_of(row: str) -> str:
    """The row's file cell — the last thing on the meta line."""
    start = row.index('<div class="submission__meta__file">')
    return row[start : row.index("</dl>", start)]


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
    navigation = navigation_of(signed_in.get(reverse("matters:department")))

    assert ">Arvamused<" not in navigation
    assert SENT_URL not in navigation
    assert ARCHIVE_URL not in navigation


def test_the_bar_still_offers_teemad(signed_in) -> None:
    """The destination the workspace moved under has to still be there."""
    navigation = navigation_of(signed_in.get(reverse("matters:department")))

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

    navigation = navigation_of(client.get(reverse("matters:department")))

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
    # The caption under the heading is gone: the tab strip immediately below it
    # names both sources, and a sentence repeating them is a line a reader steps
    # over on every visit (02-EKRAANID §C).
    assert "Saadetud seisukohad ja ajalooline arhiiv" not in section
    assert "Saadetud" in section


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
    """Two arvamused on *one* Teema, so the narrowing is proved on submissions.

    Both rows are headed by the same Teema title, which is the point: with two
    Matters, a search that quietly matched Matters instead of Submissions would
    pass this. The file is what tells the two rows apart, and neither filename
    contains the search term.
    """
    matter = factories.MatterFactory(owner=specialist, title="Ühine teema")
    evidence = capture_evidence(matter, b"%PDF-1.4 synthetic", "esimene.pdf", "application/pdf")
    sent_submission(matter, evidence=evidence, title="Seisukoht pakendiaktsiisist")
    other = capture_evidence(matter, b"%PDF-1.4 teine", "teine.pdf", "application/pdf")
    sent_submission(matter, evidence=other, title="Seisukoht ehitusloast")

    section = opinion_section_of(
        body_of(signed_in.get(TEEMAD_URL, {"arvamus_q": "pakendiaktsiis"}))
    )

    assert len(submission_rows_of(section)) == 1
    assert "esimene.pdf" in section
    assert "teine.pdf" not in section


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
    assert TEEMA_OF["Beetaseisukoht"] in section
    assert TEEMA_OF["Alfaseisukoht"] not in section


def test_searching_teemad_leaves_the_arvamused_list_alone(three_pairs, signed_in) -> None:
    """Requirement 7, the same property from the other side."""
    body = body_of(signed_in.get(TEEMAD_URL, {"q": "Ehitusseadustiku"}))
    register = register_section_of(body)
    section = opinion_section_of(body)

    assert "<strong>1</strong> teemat" in register
    # The register narrowed to one; the opinion section still holds all three.
    assert "<strong>3</strong> vastet" in section
    for title in MATTER_TITLES:
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
    assert TEEMA_OF["Gammaseisukoht"] in section
    assert TEEMA_OF["Alfaseisukoht"] not in section


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

    # `data-register-state` marks them as the register's, which is what lets
    # `static/js/app.js` replace exactly these on an htmx push and leave the
    # opinion box and the tab alone.
    assert '<input type="hidden" name="q" value="Ükskõik" data-register-state>' in section
    assert '<input type="hidden" name="jarjestus" value="title" data-register-state>' in section
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
    assert "Pakendiseaduse muudatused" in fragment
    # Not the whole page, and not the register.
    assert 'id="teemad-tulemused"' not in fragment
    assert '<nav class="topnav"' not in fragment


# ---------------------------------------------------------------------------
# 8-9. Saadetud and Arhiivikirjad, under the authorization that already existed
# ---------------------------------------------------------------------------


def test_saadetud_is_the_sections_default_source(signed_in, opinion_on_a_matter) -> None:
    section = opinion_section_of(body_of(signed_in.get(TEEMAD_URL)))

    assert "Saadetud" in section
    assert "Pakendiseaduse muudatused" in section


def test_the_archive_tab_is_offered_to_a_reader_who_may_read_it(client, administrator) -> None:
    assert may_read_archive(administrator)
    hold(sha="b" * 64, title="Varasem kiri pakendite kohta")
    rebuild_archive_index()
    client.force_login(administrator)

    section = opinion_section_of(body_of(client.get(TEEMAD_URL)))

    assert ">Arhiivikirjad" in section
    assert "arvamus_vaade=arhiiv" in section


def test_the_archive_tab_is_not_offered_to_a_reader_who_may_not(client, reader) -> None:
    """READER is the role outside `ARCHIVE_READERS` since ADR 0042 reached it.

    The two lawyer roles read the corpus now — a specialist who may open every
    RESTRICTED Matter these letters are filed onto was the gap that closed —
    so the refused reader this asserts about has to be one who genuinely is.
    """
    assert not may_read_archive(reader)
    hold(sha="c" * 64, title="Varasem kiri pakendite kohta")
    rebuild_archive_index()
    client.force_login(reader)

    section = opinion_section_of(body_of(client.get(TEEMAD_URL)))

    assert ">Arhiivikirjad" not in section


def test_the_archive_tab_is_offered_to_a_specialist(client, specialist) -> None:
    """The other side of the same strip, so neither can drift alone."""
    assert may_read_archive(specialist)
    hold(sha="9" * 64, title="Varasem kiri pakendite kohta")
    rebuild_archive_index()
    client.force_login(specialist)

    section = opinion_section_of(body_of(client.get(TEEMAD_URL, {"arvamus_vaade": "arhiiv"})))

    assert ">Arhiivikirjad" in section
    assert "Varasem kiri pakendite kohta" in section


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


def test_a_refused_reader_cannot_reach_the_archive_by_asking_for_it(client, reader) -> None:
    """The section's parameter is not a way past ``may_read_archive``.

    Resolved down to Saadetud in Python, before a query is built — so no archive
    row, no archive count and no archive year reaches the page. It resolves
    rather than raising on purpose: a crafted opinion parameter must not take
    the whole register away from somebody who was reading teemad.
    """
    assert not may_read_archive(reader)
    hold(sha="e" * 64, title="Salajane varasem kiri")
    rebuild_archive_index()
    client.force_login(reader)

    response = client.get(TEEMAD_URL, {"arvamus_vaade": "arhiiv"})
    body = body_of(response)
    section = opinion_section_of(body)

    assert response.status_code == 200
    assert "Salajane varasem kiri" not in body
    assert ">Arhiivikirjad" not in section
    # Falls back to the canonical source rather than showing nothing at all.
    assert "ei ole veel ühtegi arvamust välja saadetud" in section


def test_the_fragment_route_refuses_the_archive_the_same_way(client, reader) -> None:
    """The block answers a box on somebody else's page, and holds the same line."""
    hold(sha="f" * 64, title="Salajane varasem kiri")
    rebuild_archive_index()
    client.force_login(reader)

    fragment = body_of(client.get(BLOCK_URL, {"arvamus_vaade": "arhiiv"}))

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


def test_a_refused_reader_is_told_nothing_about_the_corpus_size(client, reader) -> None:
    """A count is an access decision too.

    Three held letters, and a refused reader's page must not print three
    anywhere in the section — not as a tab count, not as a total.
    """
    for index, letter in enumerate("abc"):
        hold(sha=letter * 64, title=f"Varasem kiri {index}")
    rebuild_archive_index()
    client.force_login(reader)

    section = opinion_section_of(body_of(client.get(TEEMAD_URL)))

    assert "Varasem kiri" not in section
    assert ">Arhiivikirjad" not in section
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


def test_the_standalone_archive_still_refuses_a_reader_who_may_not(client, reader) -> None:
    """The route's own refusal is unchanged; only the link to it left the bar."""
    assert not may_read_archive(reader)
    client.force_login(reader)

    assert client.get(ARCHIVE_URL).status_code == 403


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

    # Counted by row rather than by title: these are all on one Teema and
    # therefore all headed by the same words, which makes the article the only
    # honest unit here.
    assert len(submission_rows_of(section)) == EMBEDDED_ROWS
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
    """The whole reason the section sits here: no second search to get back.

    One destination per row, and it is the Teema. The row used to carry two —
    its own heading to the Matter's Arvamused page, and a «TEEMA …» line to the
    Matter — which is one link too many on a line whose job is to say what the
    letter was about. The heading is the Teema now and keeps the Teema's
    address; the reader reaches the arvamus surface from there.
    """
    matter, _submission = opinion_on_a_matter

    section = opinion_section_of(body_of(signed_in.get(TEEMAD_URL)))

    detail = reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    # The whole attribute value, because `matter_detail` is `/teemad/<pk>/` and
    # `matter_position` is `/teemad/<pk>/seisukoht/` — the first is a prefix of
    # the second, and a bare `in` would be answered by either.
    assert f'href="{detail}"' in section
    assert "Pakendiseaduse muudatused" in section
    assert reverse("matters:matter_position", kwargs={"pk": matter.pk}) not in section


# ---------------------------------------------------------------------------
# What a sent row says: the Teema, when, to whom, and the file
# ---------------------------------------------------------------------------
#
# The row printed its own title as a heading and the Teema underneath it as a
# labelled field. Both are sentences about the same piece of work — an arvamus
# is usually named after the teema it came out of — so every row asked the
# reader to read two headings to learn one thing, and the tail of the line
# carried «v1 · 224,8 kB», which is two facts about a file nobody was choosing
# between. The row is the Teema, when it went, who it went to, and the file.
#
# Every test below uses `opinion_on_a_matter`, whose two titles are deliberately
# different: that is what makes "the Matter title is rendered" a claim about
# which field the template reads rather than a coincidence.


def one_row(client, params=None) -> str:
    """The section's single sent row, or a failure that says how many there were."""
    rows = submission_rows_of(opinion_section_of(body_of(client.get(TEEMAD_URL, params or {}))))
    assert len(rows) == 1, f"expected one sent row, got {len(rows)}"
    return rows[0]


def test_the_row_is_headed_by_its_teema(signed_in, opinion_on_a_matter) -> None:
    """Requirement: the Teema is the row's identity, so it is the headline."""
    matter, submission = opinion_on_a_matter
    assert submission.title != matter.title, "the fixture must be able to tell the two apart"

    heading = re.search(r'<h3 class="submission__title">(.*?)</h3>', one_row(signed_in), re.S)

    assert heading, "the row has no headline"
    assert matter.title in heading.group(1)


def test_the_headline_keeps_the_teemas_own_destination(signed_in, opinion_on_a_matter) -> None:
    """The label moved into the heading; the link did not move with the heading.

    `matter_detail` is where the «TEEMA …» field went, and it is where the
    heading goes now. Asserted inside the `<h3>` rather than over the row, so a
    link that survived somewhere else on the line could not answer this — and as
    the whole `href`, because `/teemad/<pk>/` is a prefix of the arvamus surface
    the old heading pointed at and a bare `in` would pass on either.
    """
    matter, _submission = opinion_on_a_matter

    heading = re.search(r'<h3 class="submission__title">(.*?)</h3>', one_row(signed_in), re.S)

    assert heading
    detail = reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    assert f'href="{detail}"' in heading.group(1)


def test_the_submissions_own_title_is_not_printed_in_the_row(
    signed_in, opinion_on_a_matter
) -> None:
    """The heading was replaced, not duplicated.

    The title is still stored, still searched and still shown on the Matter's
    own Arvamused page — this is about the register's list, where it was the
    second of two headings.
    """
    _matter, submission = opinion_on_a_matter

    assert submission.title not in one_row(signed_in)


def test_the_row_carries_exactly_three_facts(signed_in, opinion_on_a_matter) -> None:
    """Saadetud, Adressaat, and the file. No «Teema» label, and no fourth.

    Asserted as the whole ordered list rather than as three separate `in`
    checks: what this change removed is a *field*, and only the complete list
    can say that nothing was left behind or quietly added back.
    """
    assert meta_labels_of(one_row(signed_in)) == ["Saadetud", "Adressaat", "Fail"]


def test_the_row_still_says_when_it_was_sent(signed_in, opinion_on_a_matter) -> None:
    _matter, submission = opinion_on_a_matter
    sent = timezone.localtime(submission.sent_at)

    row = one_row(signed_in)

    assert "<dt>Saadetud</dt>" in row
    assert f"{sent.day}.{sent.month}.{sent.year}" in row


def test_a_reconstructed_date_still_refuses_to_invent_a_time(
    signed_in, specialist, capture_evidence
) -> None:
    """The precision logic is untouched by the row losing a field.

    A register-sourced arvamus carries a date and no time. The anchor in the
    column is an implementation detail, and printing it as "00:00" would tell a
    lawyer the letter went out at midnight (Stage-2H brief 20).
    """
    from app.submissions.enums import SentAtPrecision

    matter = factories.MatterFactory(owner=specialist, title="Ajalooline teema")
    evidence = capture_evidence(matter, b"%PDF-1.4 s", "vana.pdf", "application/pdf")
    submission = sent_submission(
        matter,
        evidence=evidence,
        title="Ajalooline arvamus",
        when=datetime.datetime(2026, 8, 29, tzinfo=datetime.UTC),
    )
    Submission.objects.filter(pk=submission.pk).update(sent_at_precision=SentAtPrecision.DATE)

    row = one_row(signed_in)

    assert "29.8.2026" in row
    assert "00:00" not in row


def test_the_row_still_says_who_it_went_to(signed_in, specialist, capture_evidence) -> None:
    """Adressaat keeps its label and its list semantics."""
    from app.submissions.enums import RecipientRole

    matter = factories.MatterFactory(owner=specialist, title="Adressaadiga teema")
    evidence = capture_evidence(matter, b"%PDF-1.4 s", "kiri.pdf", "application/pdf")
    submission = sent_submission(matter, evidence=evidence, title="Arvamus adressaadiga")
    for name in ("Näidisministeerium", "Teine näidisamet"):
        submission.recipient_rows.create(
            organisation=factories.OrganisationFactory(name=name),
            role=RecipientRole.ADDRESSEE,
        )

    row = one_row(signed_in)

    assert "<dt>Adressaat</dt>" in row
    assert "Näidisministeerium, Teine näidisamet" in row


def test_the_file_is_named_and_downloadable(signed_in, opinion_on_a_matter) -> None:
    """The original filename, through the authorization-checked route."""
    _matter, submission = opinion_on_a_matter

    cell = file_cell_of(one_row(signed_in))

    assert submission.final_version.original_filename == "arvamus.pdf"
    assert "arvamus.pdf" in cell
    assert reverse("documents:download", kwargs={"pk": submission.final_version.pk}) in cell


def test_the_file_carries_no_version_and_no_size(signed_in, opinion_on_a_matter) -> None:
    """«v1 · 224,8 kB» is gone, and the cell is the link and nothing else.

    Matched as the whole rendered `<dd>` rather than as `"v1" not in html`: the
    page around it is full of ones and of «kB»-shaped strings, and a global
    substring would either pass by accident or fail by accident. The one
    assertion that cannot do either is "this cell is exactly an anchor".
    """
    from django.template.defaultfilters import filesizeformat

    _matter, submission = opinion_on_a_matter
    version = submission.final_version

    cell = file_cell_of(one_row(signed_in))
    rendered = re.search(r"<dd>(.*?)</dd>", cell, re.S)

    assert rendered
    assert re.fullmatch(r'<a href="[^"]+">arvamus\.pdf</a>', rendered.group(1).strip()), (
        rendered.group(1)
    )
    assert f"v{version.version_number}" not in cell
    assert filesizeformat(version.size_bytes) not in cell


def test_an_unreadable_file_is_still_not_named(client, reader, specialist, capture_evidence):
    """AUTH-003 §21, unchanged: naming the file is itself a disclosure.

    A Submission a reader may see can point at a Document restricted below it,
    because a Document carries its own override. The row appears — this is not a
    test about hiding the arvamus — and says «Fail puudub» where the filename
    would be.
    """
    matter = factories.MatterFactory(owner=specialist, title="Nähtav teema")
    evidence = capture_evidence(
        matter,
        b"%PDF-1.4 s",
        "Salajane_arvamus.pdf",
        "application/pdf",
        visibility_override=Visibility.RESTRICTED,
    )
    sent_submission(matter, evidence=evidence, title="Arvamus salajase failiga")
    client.force_login(reader)

    row = one_row(client)

    assert "Nähtav teema" in row
    assert "Salajane_arvamus.pdf" not in row
    assert "Fail puudub" in row
    assert reverse("documents:download", kwargs={"pk": evidence.pk}) not in row


def test_the_archive_tab_keeps_its_own_row_shape(client, administrator) -> None:
    """Out of scope and proved so.

    The archive is a table of four identical columns and is not the `submission`
    component. A change to the sent row must not have reached it.
    """
    hold(sha="7" * 64, title="Varasem kiri pakenditest", recipient="Näidisministeerium")
    rebuild_archive_index()
    client.force_login(administrator)

    section = opinion_section_of(body_of(client.get(TEEMAD_URL, {"arvamus_vaade": "arhiiv"})))

    assert '<table class="table">' in section
    assert submission_rows_of(section) == []
    for column in ("Kuupäev", "Kiri", "Saaja", "Seisund"):
        assert f'<th scope="col">{column}</th>' in section
    assert "Varasem kiri pakenditest" in section


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

    Nine rather than eight since a specialist may read the archive: ADR 0047
    already wrote down that "a sixth appears for a reader who may read it", and
    a specialist is now one. The arithmetic moved by exactly that one query —
    `visible_archive(viewer).count()` — and not by anything that scales with
    rows, which is the property this test exists to hold.
    """
    from django.test import RequestFactory

    from app.submissions.embedded import embedded_context

    request = RequestFactory().get(TEEMAD_URL)
    request.user = specialist

    with django_assert_max_num_queries(9):
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
    # By the component rather than by title: a sent row is headed by its Teema
    # now, and `Ehitusseadustiku revisjon` is legitimately in this fragment as a
    # *register* row. The article is the thing that must not be built.
    assert '<article class="submission">' not in fragment


# ---------------------------------------------------------------------------
# The address the live search leaves behind
# ---------------------------------------------------------------------------


def test_the_fragment_pushes_the_teemad_address_never_its_own(signed_in, three_pairs) -> None:
    """``HX-Push-Url`` names the page, not the piece of it that answered.

    The fragment lives at `/arvamused/plokk/`. That address in the bar would be
    a table with no page around it, and a colleague sent the link would get
    exactly that — the trap `matters.views._wants_fragment` documents for the
    register's own fragment. So the header carries the Teemad URL and the path
    is built server-side rather than taken from the request.
    """
    response = signed_in.get(BLOCK_URL, {"arvamus_q": "Beetaseisukoht"})

    assert response.status_code == 200
    pushed = response.headers["HX-Push-Url"]
    assert pushed.startswith(TEEMAD_URL)
    assert BLOCK_URL not in pushed
    assert "arvamus_q=Beetaseisukoht" in pushed


def test_the_pushed_address_keeps_every_register_parameter(signed_in) -> None:
    """The register's half of the address survives an opinion search.

    Everything the opinion form carries as a hidden input has to come back out
    in the pushed URL, or a live opinion search would quietly widen the teemad
    list the moment somebody reloaded.
    """
    response = signed_in.get(
        BLOCK_URL,
        {
            "q": "Ehitusseadustiku",
            "aasta": "2026",
            "vastutaja": "keegi",
            "olek": "koik",
            "leht": "2",
            "arvamus_q": "Beetaseisukoht",
        },
    )

    pushed = response.headers["HX-Push-Url"]
    for expected in ("q=Ehitusseadustiku", "aasta=2026", "vastutaja=keegi", "olek=koik", "leht=2"):
        assert expected in pushed, pushed
    assert "arvamus_q=Beetaseisukoht" in pushed


def test_clearing_the_opinion_box_takes_the_parameter_out(signed_in) -> None:
    """An empty box removes `arvamus_q` rather than leaving `arvamus_q=`.

    A parameter with no value reads as a filter that is still applied, and the
    reader would have cleared the box and still be looking at an address that
    says they had not.
    """
    pushed = signed_in.get(BLOCK_URL, {"q": "Ehitusseadustiku", "arvamus_q": ""}).headers[
        "HX-Push-Url"
    ]

    assert "arvamus_q" not in pushed
    # ...and the register's search is untouched by the clearing.
    assert "q=Ehitusseadustiku" in pushed


def test_the_default_source_is_not_written_into_the_address(signed_in) -> None:
    """A bare address already means Saadetud.

    `arvamus_vaade=saadetud` on every link would be a redundant parameter a
    reader has to learn to ignore, and it round-trips identically without one.
    """
    assert "arvamus_vaade" not in signed_in.get(BLOCK_URL, {}).headers["HX-Push-Url"]


def test_the_chosen_source_is_written_into_the_address(client, administrator) -> None:
    """Arhiivikirjad is a choice, so it survives a reload and a pasted link."""
    client.force_login(administrator)

    pushed = client.get(BLOCK_URL, {"arvamus_vaade": "arhiiv"}).headers["HX-Push-Url"]

    assert "arvamus_vaade=arhiiv" in pushed


def test_the_pushed_address_reproduces_both_states_when_followed(signed_in, three_pairs) -> None:
    """The invariant, checked end to end on the server.

    What the fragment answered with, and what the pushed address renders when
    somebody actually opens it, have to be the same two lists. A browser test
    proves the address bar really holds this; this proves the address is worth
    holding.
    """
    fragment = signed_in.get(BLOCK_URL, {"q": "Ehitusseadustiku", "arvamus_q": "Beetaseisukoht"})
    pushed = fragment.headers["HX-Push-Url"]

    body = body_of(signed_in.get(pushed))

    register = register_section_of(body)
    section = opinion_section_of(body)
    assert "Ehitusseadustiku revisjon" in register
    assert "Pakendiseaduse muudatused" not in register
    assert TEEMA_OF["Beetaseisukoht"] in section
    assert TEEMA_OF["Alfaseisukoht"] not in section
