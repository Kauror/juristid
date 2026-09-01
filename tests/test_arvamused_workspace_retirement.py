"""`Koja seisukoht` leaves the Arvamused surface, and nothing else does.

The facts rail lost its position block in the first half of this round
(tests/test_teema_rail.py). `templates/matters/matter_position.html` was the
last user-facing surface for the concept: a panel reading `position_summary`
above a disclosure that wrote it and its `Põhjendus`. It is gone, and the page
is the Submission workflow it was always mostly made of.

Two things this file is careful to separate, because they are easy to conflate
and only one of them happened:

**The surface is retired. The data is not.** `position_summary` and
`rationale_summary` still hold everything the register cutover and the opinion
archive put in them, and `app/search/indexing.py` still reads both. A Matter
carrying a position renders a page that never mentions it and stores exactly
the same two strings afterwards.

**`matters:update_position` still routes.** Nothing links to it, which is the
point; it stays inside the business-write boundary rather than being deleted,
because dropping the only writer for live indexed columns is a data decision
and this was a UI one. `tests/test_business_write_boundary.py` keeps firing
every forbidden actor at it, and the test at the bottom of this file is what
would notice a link to it reappearing.
"""

from __future__ import annotations

import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from app.legacy_import.opinion_archive import OpinionArchiveBatch, OpinionArchiveItem
from app.legacy_import.opinion_binary import OpinionArchiveBinary
from app.legacy_import.opinion_enums import ArchiveLinkBasis
from app.legacy_import.opinion_links import link_matter
from app.legacy_import.opinion_search import rebuild_archive_index
from app.matters.models import Matter
from app.matters.services import set_position
from app.submissions.services import (
    attach_final_evidence,
    create_submission,
    mark_submission_sent,
)
from tests import factories

pytestmark = pytest.mark.django_db


#: Every string the retired panel put on the page. None of them may render from
#: this surface again — and they are asserted as literals rather than by class,
#: because a reader recognises the wording, not the markup.
RETIRED_COPY = (
    "Koja seisukoht",
    "Koja seisukohta ei ole veel sõnastatud",
    "Sõnasta Koja seisukoht",
    "Muuda seisukohta",
    "Salvesta seisukoht",
    "Põhjendus",
    "positionpanel",
    "id_position_summary",
    "id_rationale_summary",
)


def _page(client, matter) -> str:
    response = client.get(reverse("matters:matter_position", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    return response.content.decode()


def _archive_letter(*, sha: str = "b" * 64, title: str = "Varasem kiri") -> OpinionArchiveBinary:
    """One held letter. Every string invented; mirrors tests/test_opinions_workspace.py."""
    batch = OpinionArchiveBatch.objects.create(
        archive_sha256="a" * 64,
        importer_version="test/0",
        started_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
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
        archive_relative_path="Opinions/2024/naidis.pdf",
        original_filename="naidis.pdf",
        sha256=sha,
        size_bytes=1024,
        detected_type="application/pdf",
        filename_date=datetime.date(2024, 4, 10),
        filename_recipient="Näidisministeerium",
        filename_title=title,
        binary=binary,
    )
    return binary


# ---------------------------------------------------------------------------
# 1. The panel is gone
# ---------------------------------------------------------------------------


def test_the_arvamused_page_says_nothing_about_a_seisukoht(signed_in, specialist):
    """Removed, not renamed, and not replaced by another prose field."""
    matter = factories.MatterFactory(owner=specialist)

    body = _page(signed_in, matter)

    for phrase in RETIRED_COPY:
        assert phrase not in body, f"{phrase!r} still renders from the Arvamused surface"


def test_the_page_does_not_post_to_update_position(signed_in, specialist):
    """The endpoint survives; the native link to it does not."""
    matter = factories.MatterFactory(owner=specialist)

    body = _page(signed_in, matter)

    assert reverse("matters:update_position", kwargs={"pk": matter.pk}) not in body


def test_a_matter_that_has_a_position_still_renders_none_of_it(signed_in, specialist):
    """The strongest form of the assertion: the data exists and is not shown.

    An empty Matter would pass the test above whether the panel were removed or
    merely rendering its empty state.
    """
    matter = factories.MatterFactory(owner=specialist)
    set_position(
        matter=matter,
        position_summary="Koda ei toeta pakendiaktsiisi kavandatud tõusu.",
        rationale_summary="Liikmete hinnangul kasvab halduskoormus.",
        actor=specialist,
    )

    body = _page(signed_in, matter)

    assert "Koda ei toeta pakendiaktsiisi" not in body
    assert "Liikmete hinnangul kasvab halduskoormus" not in body
    for phrase in RETIRED_COPY:
        assert phrase not in body, phrase


def test_the_view_no_longer_carries_a_position_form(signed_in, specialist):
    """A bound form nothing renders is two model reads for absent markup."""
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.get(reverse("matters:matter_position", kwargs={"pk": matter.pk}))

    assert "position_form" not in response.context


# ---------------------------------------------------------------------------
# 2. The data is untouched
# ---------------------------------------------------------------------------


def test_reading_the_page_leaves_the_stored_position_exactly_as_it_was(signed_in, specialist):
    """UI retirement, not a data change. No migration, no backfill, no clear."""
    matter = factories.MatterFactory(owner=specialist)
    set_position(
        matter=matter,
        position_summary="Koda toetab eelnõu põhimõtet.",
        rationale_summary="Halduskoormus ei kasva.",
        actor=specialist,
    )

    before = Matter.objects.values("position_summary", "rationale_summary").get(pk=matter.pk)
    _page(signed_in, matter)
    after = Matter.objects.values("position_summary", "rationale_summary").get(pk=matter.pk)

    assert before == after
    assert after["position_summary"] == "Koda toetab eelnõu põhimõtet."
    assert after["rationale_summary"] == "Halduskoormus ei kasva."


def test_both_columns_still_reach_the_search_index(specialist):
    """Retiring an editor does not retire what the corpus is searched on.

    `indexed_text_for` is the one owner of what text represents a Matter, so
    this asks it directly rather than through a rebuild: the question is
    whether the two columns are still part of the composition.
    """
    from app.search.indexing import indexed_text_for, refresh_matter

    matter = factories.MatterFactory(owner=specialist, title="Pakendiseaduse eelnõu")
    set_position(
        matter=matter,
        position_summary="Ainulaadnesõna seisukohas.",
        rationale_summary="Teineainulaadnesõna põhjenduses.",
        actor=specialist,
    )
    matter.refresh_from_db()

    body_text = indexed_text_for(matter)["body_text"]
    assert "Ainulaadnesõna" in body_text
    assert "Teineainulaadnesõna" in body_text

    # And the projection actually carries it.
    refresh_matter(matter)
    row = matter.search_documents.get(source_kind="MATTER")  # type: ignore[attr-defined]
    assert "Ainulaadnesõna" in row.body_text
    assert "Teineainulaadnesõna" in row.body_text


# ---------------------------------------------------------------------------
# 3. Everything the page is actually for
# ---------------------------------------------------------------------------


def test_the_submission_workflow_is_untouched(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    create_submission(matter=matter, title="Koostamisel arvamus", actor=specialist)

    body = _page(signed_in, matter)

    assert "Koja arvamused" in body
    assert "Koostamisel arvamus" in body
    assert "+ Uus arvamus" in body
    assert reverse("submissions:create", kwargs={"matter_id": matter.pk}) in body


def test_a_sent_opinion_still_lists_with_its_evidence(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    sent = create_submission(matter=matter, title="Saadetud arvamus", actor=specialist)
    attach_final_evidence(
        submission=sent,
        content=b"%PDF-1.4 arvamus",
        original_filename="arvamus.pdf",
        mime_type="application/pdf",
        actor=specialist,
    )
    mark_submission_sent(submission=sent, actor=specialist)

    body = _page(signed_in, matter)

    assert "Saadetud arvamus" in body
    assert "arvamus.pdf" in body


def test_the_empty_state_is_the_submission_one(signed_in, specialist):
    """The page with nothing on it says so once, about opinions."""
    matter = factories.MatterFactory(owner=specialist)

    body = _page(signed_in, matter)

    assert "Ühtegi arvamust ei ole veel loodud." in body


def test_archive_letters_still_render_where_they_are_allowed(client, specialist):
    """Signed in as an ADMINISTRATOR, because `may_read_archive` is a question
    about the corpus and outside the shared gate only that role passes it
    (app/legacy_import/opinion_access.py, docs/adr/0028)."""
    matter = factories.MatterFactory(owner=specialist)
    head = factories.DepartmentHeadFactory()
    link_matter(
        binary=_archive_letter(),
        matter=matter,
        basis=ArchiveLinkBasis.REVIEWED,
        actor=head,
    )
    # The row renders its title from the archive's own projection, so the
    # projection has to exist. Synthetic rows in a test database; this is the
    # same call the sibling suite makes (tests/test_opinions_workspace.py).
    rebuild_archive_index()
    client.force_login(factories.AdministratorFactory())

    body = _page(client, matter)

    assert "Seotud arhiivikirjad" in body
    assert "Varasem kiri" in body


def test_a_reader_who_may_not_open_the_archive_sees_no_letters(client, specialist):
    """The other half of the same rule.

    The refused reader is a READER since ADR 0055 — the two lawyer roles read
    the corpus now, so a specialist is the wrong person to ask this about.
    """
    from app.accounts.enums import UserRole

    reader = factories.UserFactory(role=UserRole.READER)
    matter = factories.MatterFactory(owner=specialist)
    head = factories.DepartmentHeadFactory()
    link_matter(
        binary=_archive_letter(sha="c" * 64),
        matter=matter,
        basis=ArchiveLinkBasis.REVIEWED,
        actor=head,
    )

    client.force_login(reader)
    assert "Seotud arhiivikirjad" not in _page(client, matter)


def test_the_rail_still_travels_with_the_page(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    body = _page(signed_in, matter)

    assert 'id="teema-andmed"' in body
    assert 'id="koja-arvamus"' in body


# ---------------------------------------------------------------------------
# 4. Authorization is exactly what it was
# ---------------------------------------------------------------------------


def test_a_reader_reads_the_page_and_is_offered_no_writes(client, reader, specialist):
    matter = factories.MatterFactory(owner=specialist)
    create_submission(matter=matter, title="Koostamisel arvamus", actor=specialist)
    client.force_login(reader)

    body = _page(client, matter)

    assert "Koja arvamused" in body
    assert "Koostamisel arvamus" in body
    assert "+ Uus arvamus" not in body
    assert reverse("submissions:create", kwargs={"matter_id": matter.pk}) not in body


def test_the_endpoint_still_refuses_a_reader(client, reader, specialist):
    """Unreachable is not the same as unguarded.

    The route has no native caller any more, so this is the assertion that it
    is still a door with a lock rather than one nobody happens to be walking
    towards. The full matrix is in tests/test_business_write_boundary.py.
    """
    matter = factories.MatterFactory(owner=specialist)
    client.force_login(reader)

    response = client.post(
        reverse("matters:update_position", kwargs={"pk": matter.pk}),
        {"position_summary": "Loata seisukoht", "rationale_summary": ""},
    )

    assert response.status_code == 404
    matter.refresh_from_db()
    assert matter.position_summary == ""


def test_the_endpoint_still_works_for_an_authorized_actor(signed_in, specialist):
    """Kept deliberately, so its removal is a decision somebody takes on
    purpose rather than something that rots into a 404."""
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        reverse("matters:update_position", kwargs={"pk": matter.pk}),
        {"position_summary": "Koda toetab.", "rationale_summary": ""},
    )

    assert response.status_code == 302
    matter.refresh_from_db()
    assert matter.position_summary == "Koda toetab."


# ---------------------------------------------------------------------------
# 5. The retirement cannot be undone by accident
# ---------------------------------------------------------------------------


def test_no_template_links_to_update_position() -> None:
    """The guard against a native editor quietly coming back.

    A whole-tree check rather than one page's body, because the next place
    somebody would add one is not necessarily this file.
    """
    import re
    from pathlib import Path

    from django.conf import settings

    offenders = [
        str(template.relative_to(settings.BASE_DIR))
        for template in Path(settings.BASE_DIR).joinpath("templates").rglob("*.html")
        # The `{% url %}` tag, not the bare name: this file's own header
        # comment names the route in prose, and so may a future one.
        if re.search(
            r"""\{%\s*url\s+['"]matters:update_position['"]""",
            template.read_text(encoding="utf-8"),
        )
    ]

    assert not offenders, (
        "`Koja seisukoht` has no user-facing surface; these templates link to "
        f"its endpoint: {offenders}"
    )
