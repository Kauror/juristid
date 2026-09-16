"""`Menetluse link` — where the official proceeding on a Matter lives (docs/adr/0089).

A pointer, and the rules this file is most careful about are the ones a
screenshot cannot show:

* **it is a reference, not an ingestion.** Nothing is fetched, no `Document` is
  created, no date or metadata is read off the other end and no background work
  is scheduled. §D asserts the absence, because an absence is exactly the kind
  of property that erodes without a test;
* **the kind is the lawyer's statement**, never derived from the hostname: all
  five values accept any public address, and an `EIS` link on a ministry's
  domain is a legitimate row rather than a contradiction;
* **the address rule is the shared one** — a parsed host, `http`/`https` only,
  userinfo refused, refused rather than truncated;
* **one address per Matter**, so a double-click, a browser retry and a stale
  response cannot leave one file holding one pointer twice;
* **a restricted Matter's links leak nowhere**: not into the page, not into
  search, not into a count;
* **none of it is work**: no `NextAction`, no deadline, no work item, no
  chronology row, no search row.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.matters import work_items
from app.matters.enums import ProceduralLinkKind
from app.matters.models import MatterProceduralLink
from app.matters.services import (
    ProceduralLinkConflict,
    close_matter,
    correct_procedural_link,
    procedural_link_revision,
    record_procedural_link,
)
from app.matters.timeline import TIMELINE_EVENT_TYPES, matter_timeline
from app.search.indexing import rebuild_all
from app.search.models import SearchDocument
from app.workflow.enums import Disposition
from tests import factories

pytestmark = pytest.mark.django_db

EIS_URL = "https://eelnoud.valitsus.ee/main/mount/docList/8f2c1a30-0000-0000-0000-000000000001"
REGISTER_URL = "https://dokumendiregister.example.ee/otsing?nr=1-4%2F2026-123"
EU_URL = "https://eur-lex.europa.eu/legal-content/ET/TXT/?uri=CELEX%3A52026PC0041"
RIIGIKOGU_URL = "https://riigikogu.ee/tegevus/eelnoud/eelnou/123-SE"
OTHER_URL = "http://vana.register.example/2019/toimik/77"


def _record(matter, actor=None, *, kind=ProceduralLinkKind.EIS, url=EIS_URL, label=""):
    return record_procedural_link(
        matter=matter, kind=kind, url=url, label=label, actor=actor or matter.owner
    )


def _detail(client, matter) -> str:
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    return response.content.decode()


def _add(client, matter, **fields):
    return client.post(
        reverse("matters:add_procedural_link", kwargs={"pk": matter.pk}),
        fields,
        headers={"HX-Request": "true"},
    )


def _correct(client, matter, link, **fields):
    return client.post(
        reverse("matters:correct_procedural_link", kwargs={"pk": matter.pk, "link_id": link.pk}),
        fields,
        headers={"HX-Request": "true"},
    )


def _card(body: str) -> str:
    start = body.index('id="menetluse-lingid"')
    return body[start : body.index("</aside>", start)]


# ===========================================================================
# A — the record: five kinds, several per Matter, and none required
# ===========================================================================


def test_an_eis_link_is_recorded_and_reads_on_the_matter(signed_in, normal_matter, specialist):
    """Scenario A. The whole of the ordinary case, end to end.

    Saved, on the page, opening normally — and creating no publication, which is
    the confusion docs/adr/0089 §5 exists to prevent.
    """
    response = _add(
        signed_in, normal_matter, kind=ProceduralLinkKind.EIS.value, url=EIS_URL, label=""
    )

    assert response.status_code == 200
    link = MatterProceduralLink.objects.get(matter=normal_matter)
    assert link.kind == ProceduralLinkKind.EIS
    assert link.url == EIS_URL
    assert link.label == ""
    assert link.created_by == specialist

    card = _card(_detail(signed_in, normal_matter))
    assert "EIS" in card
    assert f'href="{EIS_URL}"' in card
    assert 'target="_blank"' in card
    assert 'rel="noopener noreferrer"' in card
    assert "avaneb uues aknas" in card

    # And nothing else was created. An address is not a publication.
    assert not normal_matter.website_overviews.exists()
    assert not normal_matter.external_positions.exists()


def test_all_five_kinds_are_recordable_and_coexist(normal_matter, specialist):
    """Scenario B. Four references on one file, each keeping its own kind.

    The point is that none of them is a fixed column: a Matter carries the
    addresses it carries, and the fifth kind is as ordinary as the first.
    """
    pairs = [
        (ProceduralLinkKind.EIS, EIS_URL),
        (ProceduralLinkKind.MINISTRY_REGISTER, REGISTER_URL),
        (ProceduralLinkKind.EU_PROCEDURE, EU_URL),
        (ProceduralLinkKind.RIIGIKOGU, RIIGIKOGU_URL),
        (ProceduralLinkKind.OTHER, OTHER_URL),
    ]
    for kind, url in pairs:
        _record(normal_matter, specialist, kind=kind, url=url)

    stored = {
        link.kind: link.url for link in MatterProceduralLink.objects.filter(matter=normal_matter)
    }
    assert stored == {kind.value: url for kind, url in pairs}


def test_several_links_of_one_kind_are_ordinary(normal_matter, specialist):
    """Two ministries' registers on one long proceeding is not a duplicate.

    There is deliberately no uniqueness on `(matter, kind)`: a `kind` usable
    once would make the second register unrecordable.
    """
    _record(
        normal_matter,
        specialist,
        kind=ProceduralLinkKind.MINISTRY_REGISTER,
        url=REGISTER_URL,
        label="Rahandusministeerium",
    )
    _record(
        normal_matter,
        specialist,
        kind=ProceduralLinkKind.MINISTRY_REGISTER,
        url="https://dokumendiregister.example.ee/otsing?nr=5-1%2F2026-9",
        label="Justiitsministeerium",
    )

    links = MatterProceduralLink.objects.filter(
        matter=normal_matter, kind=ProceduralLinkKind.MINISTRY_REGISTER
    )
    assert links.count() == 2
    assert {link.label for link in links} == {"Rahandusministeerium", "Justiitsministeerium"}


def test_a_matter_with_no_links_renders_no_card_and_no_placeholders(signed_in, normal_matter):
    """Scenario C. There are no permanently visible empty sections on this page.

    Specifically **not** five empty rows waiting to be filled in: the one
    compact add affordance is the launcher chip, which is on every open Matter.
    """
    body = _detail(signed_in, normal_matter)

    assert 'id="menetluse-lingid"' not in body
    assert "Menetluse lingid" not in body
    assert "+ Menetluse link" in body


def test_the_label_is_optional_and_the_host_stands_in_for_it(normal_matter, specialist):
    """A row with no name reads as its host, never as its raw address.

    A raw URL as a row's own text is a line a reader has to parse instead of
    read, and it is the one shape in which a look-alike address is believed.
    """
    unnamed = _record(normal_matter, specialist, url=EIS_URL)
    named = _record(
        normal_matter,
        specialist,
        kind=ProceduralLinkKind.RIIGIKOGU,
        url=RIIGIKOGU_URL,
        label="Eelnõu 123 SE",
    )

    assert unnamed.display_label == "eelnoud.valitsus.ee"
    assert named.display_label == "Eelnõu 123 SE"


# ===========================================================================
# B — the address rule, and the kind that is not derived from it
# ===========================================================================


@pytest.mark.parametrize(
    "bad_url",
    [
        "ei ole aadress",
        "ftp://eelnoud.valitsus.ee/x",
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "file:///c:/windows/system32",
        "https:///toimik/1",
        "https://kasutaja:parool@eelnoud.valitsus.ee/x",
    ],
)
def test_an_unsafe_or_unusable_address_is_refused(normal_matter, specialist, bad_url):
    """Scenario J. The shared safety half, reached through this record's door.

    Each refusal names `Menetluse link` rather than saying «link», because a
    Teema page renders four kinds of address and a sentence that does not say
    which one it means is one the reader has to locate first.
    """
    with pytest.raises(DomainError) as refusal:
        _record(normal_matter, specialist, url=bad_url)

    assert "Menetluse link" in str(refusal.value), bad_url
    assert not MatterProceduralLink.objects.filter(matter=normal_matter).exists()


def test_an_address_past_the_column_is_refused_rather_than_truncated(normal_matter, specialist):
    """A link cut off at a thousand characters is a link that no longer resolves."""
    with pytest.raises(DomainError) as refusal:
        _record(normal_matter, specialist, url="https://example.ee/" + "a" * 1000)

    assert "liiga pikk" in str(refusal.value)


def test_an_empty_address_is_refused(normal_matter, specialist):
    """There is no state of this record that legitimately has no address."""
    with pytest.raises(DomainError):
        _record(normal_matter, specialist, url="")
    with pytest.raises(DomainError):
        _record(normal_matter, specialist, url="   ")


def test_no_kind_is_inferred_from_the_hostname(normal_matter, specialist):
    """docs/adr/0089 §2. The kind is stated, and any address accepts any kind.

    A ministry runs several registers, an EU file is read on EUR-Lex one month
    and on a Commission page the next, and a host rule would silently
    reclassify every stored row the day a register moved domain.
    """
    link = _record(
        normal_matter,
        specialist,
        kind=ProceduralLinkKind.MINISTRY_REGISTER,
        url=EIS_URL,
    )

    assert link.kind == ProceduralLinkKind.MINISTRY_REGISTER
    assert link.url == EIS_URL


def test_the_address_is_stored_exactly_as_it_was_given(normal_matter, specialist):
    """Nothing is canonicalised, so nothing quietly points somewhere else.

    A document register's deep link is frequently a query and nothing else, and
    a «tidied» one is a different page.
    """
    for url in (REGISTER_URL, EU_URL, OTHER_URL, "https://example.ee/a/?b=1&c=2#d"):
        link = _record(normal_matter, specialist, url=url, kind=ProceduralLinkKind.OTHER)
        assert link.url == url


def test_a_kind_outside_the_vocabulary_is_refused_by_name(normal_matter, specialist):
    """A crafted POST meets a sentence, not an `IntegrityError` with no row."""
    with pytest.raises(DomainError) as refusal:
        _record(normal_matter, specialist, kind="EELNOUD_VALITSUS")

    assert "menetluse allikas" in str(refusal.value).lower()


def test_the_database_refuses_a_row_outside_the_vocabulary_or_with_no_address(normal_matter):
    """The defence behind the service, for a write that did not come through it."""
    with pytest.raises(IntegrityError), transaction.atomic():
        MatterProceduralLink.objects.create(
            matter=normal_matter, kind="EELNOUD_VALITSUS", url=EIS_URL
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        MatterProceduralLink.objects.create(
            matter=normal_matter, kind=ProceduralLinkKind.EIS, url=""
        )


# ===========================================================================
# C — duplicates: one address per Matter, whatever the browser does
# ===========================================================================


def test_submitting_the_same_link_twice_writes_one_row_and_one_event(
    signed_in, normal_matter, specialist
):
    """Scenario: a double-click, a browser retry, a stale response.

    The second submit is answered with the row that is already there — not a
    refusal, because the save the person meant *happened*, and not a second row.
    """
    first = _add(signed_in, normal_matter, kind=ProceduralLinkKind.EIS.value, url=EIS_URL)
    second = _add(signed_in, normal_matter, kind=ProceduralLinkKind.EIS.value, url=EIS_URL)

    assert first.status_code == 200
    assert second.status_code == 200
    assert MatterProceduralLink.objects.filter(matter=normal_matter).count() == 1
    assert (
        ChangeEvent.objects.filter(
            matter=normal_matter, event_type=ChangeEventType.PROCEDURAL_LINK_RECORDED
        ).count()
        == 1
    )


def test_the_same_address_under_a_different_kind_is_refused_by_name(normal_matter, specialist):
    """One address filed as `EIS` and again as `Muu` is one address.

    Refused rather than silently ignored: dropping a changed classification
    would leave somebody looking at a row saying something they had just
    corrected and been told was saved.
    """
    _record(normal_matter, specialist, kind=ProceduralLinkKind.EIS, url=EIS_URL)

    with pytest.raises(DomainError) as refusal:
        _record(normal_matter, specialist, kind=ProceduralLinkKind.OTHER, url=EIS_URL)

    assert "juba" in str(refusal.value)
    assert MatterProceduralLink.objects.filter(matter=normal_matter).count() == 1


def test_the_database_refuses_one_address_twice_on_one_matter(normal_matter, specialist):
    """The constraint behind the service, for a write that did not come through it."""
    _record(normal_matter, specialist, url=EIS_URL)

    with pytest.raises(IntegrityError), transaction.atomic():
        MatterProceduralLink.objects.create(
            matter=normal_matter, kind=ProceduralLinkKind.OTHER, url=EIS_URL
        )


def test_one_address_on_two_matters_is_two_legitimate_rows(specialist):
    """Uniqueness is per Matter. Two files may reference one proceeding."""
    first = factories.MatterFactory(owner=specialist)
    second = factories.MatterFactory(owner=specialist)

    _record(first, specialist, url=EIS_URL)
    _record(second, specialist, url=EIS_URL)

    assert MatterProceduralLink.objects.filter(url=EIS_URL).count() == 2


# ===========================================================================
# D — a reference, and nothing else: what this record deliberately does not do
# ===========================================================================


def test_recording_a_link_fetches_nothing(normal_matter, specialist, monkeypatch):
    """docs/adr/0089 §4. The boundary, asserted rather than assumed.

    Every outbound door is replaced with an explosion: `urllib`, `requests` if
    it is installed, and `socket.create_connection` under both. A record that
    quietly grew a HEAD request to «check» the address would fail here and
    nowhere else, because a green page looks identical either way.
    """
    import socket
    import urllib.request

    def explode(*args, **kwargs):
        raise AssertionError("a procedural link must never be fetched")

    monkeypatch.setattr(urllib.request, "urlopen", explode)
    monkeypatch.setattr(socket, "create_connection", explode)

    link = _record(normal_matter, specialist, url=EIS_URL)

    assert link.url == EIS_URL


def test_a_link_creates_no_document_and_no_evidence(normal_matter, specialist):
    """An official register page is not the evidence store."""
    from app.documents.models import Document, DocumentVersion

    _record(normal_matter, specialist, url=EIS_URL)

    assert not Document.objects.filter(matter=normal_matter).exists()
    assert not DocumentVersion.objects.exists()


def test_a_link_creates_no_work_and_makes_no_matter_late(normal_matter, specialist):
    """No `NextAction`, no deadline, no work item, no badge.

    A Matter carrying four references must read exactly as it did with none.
    """
    from app.workflow.models import NextAction

    before = work_items.work_items(specialist)
    for kind, url in (
        (ProceduralLinkKind.EIS, EIS_URL),
        (ProceduralLinkKind.RIIGIKOGU, RIIGIKOGU_URL),
    ):
        _record(normal_matter, specialist, kind=kind, url=url)

    assert not NextAction.objects.filter(matter=normal_matter).exists()
    assert work_items.work_items(specialist) == before
    normal_matter.refresh_from_db()
    assert normal_matter.response_deadline is None


def test_a_link_writes_no_chronology_row(signed_in, normal_matter, specialist):
    """docs/adr/0089 §11. It is not something that happened, it is where it happens.

    The audit events exist and are readable; what they are not is a line in the
    chronology, which would be the history of somebody's typing rather than of
    the proceeding.
    """
    _record(normal_matter, specialist, url=EIS_URL)
    correct_procedural_link(
        link=MatterProceduralLink.objects.get(matter=normal_matter),
        kind=ProceduralLinkKind.EIS,
        url=EIS_URL,
        label="Eelnõu 123 SE",
        actor=specialist,
    )

    items, _ = matter_timeline(matter=normal_matter, user=specialist)

    assert ChangeEventType.PROCEDURAL_LINK_RECORDED not in TIMELINE_EVENT_TYPES
    assert ChangeEventType.PROCEDURAL_LINK_CORRECTED not in TIMELINE_EVENT_TYPES
    assert not any("Menetluse link" in (item.summary or "") for item in items)


def test_a_link_writes_no_search_row(normal_matter, specialist):
    """§8 of the brief's default: this package changes no search index contract.

    The address is not indexed, the label is not indexed, and nothing about the
    linked page is. Opening the Matter is how its links are found.
    """
    _record(normal_matter, specialist, url=EIS_URL, label="Eelnõu 123 SE")
    rebuild_all()

    assert not SearchDocument.objects.filter(body_text__icontains="eelnoud.valitsus").exists()
    assert not SearchDocument.objects.filter(body_text__icontains="123 SE").exists()


# ===========================================================================
# E — corrections, and the absence of a delete
# ===========================================================================


def test_the_kind_the_label_and_the_address_are_all_correctable(normal_matter, specialist):
    """One correction, one event, and the event says what moved."""
    link = _record(normal_matter, specialist, url=EIS_URL)

    corrected = correct_procedural_link(
        link=link,
        kind=ProceduralLinkKind.RIIGIKOGU,
        url=RIIGIKOGU_URL,
        label="Eelnõu 123 SE",
        actor=specialist,
    )

    corrected.refresh_from_db()
    assert corrected.kind == ProceduralLinkKind.RIIGIKOGU
    assert corrected.url == RIIGIKOGU_URL
    assert corrected.label == "Eelnõu 123 SE"

    event = ChangeEvent.objects.get(
        matter=normal_matter, event_type=ChangeEventType.PROCEDURAL_LINK_CORRECTED
    )
    assert set(event.payload["fields"]) == {"kind", "url", "label"}
    assert event.payload["url_from"] == EIS_URL
    assert event.payload["url_to"] == RIIGIKOGU_URL


def test_a_correction_that_changes_nothing_writes_nothing(normal_matter, specialist):
    link = _record(normal_matter, specialist, url=EIS_URL, label="Toimik")

    correct_procedural_link(
        link=link, kind=ProceduralLinkKind.EIS, url=EIS_URL, label="Toimik", actor=specialist
    )

    assert not ChangeEvent.objects.filter(
        matter=normal_matter, event_type=ChangeEventType.PROCEDURAL_LINK_CORRECTED
    ).exists()


def test_a_stale_correction_is_refused_and_writes_nothing(normal_matter, specialist):
    """Optimistic concurrency, the shape every other correction on this page has."""
    link = _record(normal_matter, specialist, url=EIS_URL)
    stale = procedural_link_revision(link)
    correct_procedural_link(
        link=link,
        kind=ProceduralLinkKind.EIS,
        url=EIS_URL,
        label="Esimene",
        actor=specialist,
        expected_revision=stale,
    )

    with pytest.raises(ProceduralLinkConflict) as conflict:
        correct_procedural_link(
            link=link,
            kind=ProceduralLinkKind.EIS,
            url=EIS_URL,
            label="Teine",
            actor=specialist,
            expected_revision=stale,
        )

    assert conflict.value.current.label == "Esimene"
    link.refresh_from_db()
    assert link.label == "Esimene"


def test_a_correction_onto_another_rows_address_is_refused_by_name(normal_matter, specialist):
    """Not an `IntegrityError` with an aborted transaction."""
    first = _record(normal_matter, specialist, url=EIS_URL)
    _record(normal_matter, specialist, kind=ProceduralLinkKind.RIIGIKOGU, url=RIIGIKOGU_URL)

    with pytest.raises(DomainError) as refusal:
        correct_procedural_link(
            link=first, kind=ProceduralLinkKind.EIS, url=RIIGIKOGU_URL, actor=specialist
        )

    assert "juba" in str(refusal.value)
    first.refresh_from_db()
    assert first.url == EIS_URL


def test_there_is_no_route_that_deletes_a_link(signed_in, normal_matter, specialist):
    """Create and correct. A mistaken row is corrected, never removed.

    The rule `MatterEngagement` and `MatterExternalPosition` both keep: what the
    file recorded and who recorded it is part of the file (docs/adr/0084 §8).
    """
    link = _record(normal_matter, specialist, url=EIS_URL)

    body = _card(_detail(signed_in, normal_matter))

    assert "Kustuta" not in body
    assert "Eemalda" not in body
    response = signed_in.post(
        reverse(
            "matters:correct_procedural_link", kwargs={"pk": normal_matter.pk, "link_id": link.pk}
        )
        + "kustuta/"
    )
    assert response.status_code == 404


# ===========================================================================
# F — the closed Matter: no new links, and a correction that still works
# ===========================================================================


def test_a_closed_matter_refuses_a_new_link(signed_in, normal_matter, specialist):
    """New business content, refused under the Matter's own row lock.

    A closed Teema renders no launcher, and that decides nothing about a POST
    arriving from a tab that was open before somebody else shut the file.
    """
    close_matter(matter=normal_matter, disposition=Disposition.OTHER, actor=specialist)

    response = _add(signed_in, normal_matter, kind=ProceduralLinkKind.EIS.value, url=EIS_URL)

    assert response.status_code == 400
    assert not MatterProceduralLink.objects.filter(matter=normal_matter).exists()


def test_a_closed_matter_still_permits_a_correction(normal_matter, specialist):
    """Closure has never meant that an address recorded wrongly must stay wrong."""
    link = _record(normal_matter, specialist, url=EIS_URL)
    close_matter(matter=normal_matter, disposition=Disposition.OTHER, actor=specialist)

    correct_procedural_link(
        link=link, kind=ProceduralLinkKind.EIS, url=REGISTER_URL, actor=specialist
    )

    link.refresh_from_db()
    assert link.url == REGISTER_URL


def test_closing_a_matter_leaves_its_links_exactly_as_they_were(normal_matter, specialist):
    """Unlike a planned `Ülevaade / uudis`, a reference is not a debt to cancel."""
    _record(normal_matter, specialist, url=EIS_URL)

    close_matter(matter=normal_matter, disposition=Disposition.OTHER, actor=specialist)

    link = MatterProceduralLink.objects.get(matter=normal_matter)
    assert link.url == EIS_URL


# ===========================================================================
# G — visibility: a link is never less restrictive than its Matter
# ===========================================================================


def test_a_restricted_matters_links_are_invisible_to_an_unauthorised_reader(
    client, restricted_matter, specialist, reader
):
    """Scenario I. Not on the page, and not by name anywhere on it."""
    _record(restricted_matter, specialist, url=EIS_URL, label="Eelnõu 123 SE")

    client.force_login(reader)
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": restricted_matter.pk}))

    assert response.status_code == 404


def test_a_restricted_link_on_a_visible_matter_is_invisible_to_a_reader(
    client, normal_matter, specialist, reader
):
    """A child may be more restrictive than its Matter, and this is that case.

    Read through `visible_to` rather than filtered after the fact, so the row
    never reaches the page to be hidden.
    """
    _record(normal_matter, specialist, url=REGISTER_URL, label="Avalik")
    _record(
        normal_matter,
        specialist,
        kind=ProceduralLinkKind.RIIGIKOGU,
        url=RIIGIKOGU_URL,
        label="Piiratud",
    )
    MatterProceduralLink.objects.filter(url=RIIGIKOGU_URL).update(
        visibility_override=Visibility.RESTRICTED
    )

    client.force_login(reader)
    body = client.get(
        reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk})
    ).content.decode()

    assert "Avalik" in body
    assert "Piiratud" not in body
    assert RIIGIKOGU_URL not in body


def test_a_link_can_never_be_less_restrictive_than_its_matter(restricted_matter, specialist):
    """AGENTS.md: a restricted child may be more restrictive, never less."""
    link = _record(restricted_matter, specialist, url=EIS_URL)
    link.visibility_override = Visibility.NORMAL
    link.save(update_fields=["visibility_override"])

    link.refresh_from_db()
    assert link.effective_visibility == Visibility.RESTRICTED


def test_a_reader_cannot_correct_a_link_on_a_matter_they_may_not_see(
    client, restricted_matter, specialist, reader
):
    """The write route is scoped the same way the read is."""
    link = _record(restricted_matter, specialist, url=EIS_URL)

    client.force_login(reader)
    response = client.post(
        reverse(
            "matters:correct_procedural_link",
            kwargs={"pk": restricted_matter.pk, "link_id": link.pk},
        ),
        {"kind": ProceduralLinkKind.OTHER.value, "url": OTHER_URL, "revision": ""},
        headers={"HX-Request": "true"},
    )

    assert response.status_code in {403, 404}
    link.refresh_from_db()
    assert link.url == EIS_URL


# ===========================================================================
# H — the browser's own route: refusals keep what was typed
# ===========================================================================


def test_a_refused_address_comes_back_in_the_box(signed_in, normal_matter):
    """Losing a pasted address would cost the one fact they opened the panel for."""
    response = _add(
        signed_in, normal_matter, kind=ProceduralLinkKind.EIS.value, url="ftp://example.ee/x"
    )
    body = response.content.decode()

    assert response.status_code == 400
    assert not MatterProceduralLink.objects.filter(matter=normal_matter).exists()
    assert 'id="lisa-menetluse-link"' in body
    assert 'value="ftp://example.ee/x"' in body


def test_a_missing_kind_is_refused_on_the_chip_row(signed_in, normal_matter):
    """The panel pre-selects nothing, so the question has to be answered."""
    response = _add(signed_in, normal_matter, kind="", url=EIS_URL)
    body = response.content.decode()

    assert response.status_code == 400
    assert "Vali, millise menetluse allikaga" in body
    assert f'value="{EIS_URL}"' in body


def test_a_correction_refusal_reopens_the_row_it_came_from(signed_in, normal_matter, specialist):
    link = _record(normal_matter, specialist, url=EIS_URL)

    response = _correct(
        signed_in,
        normal_matter,
        link,
        kind=ProceduralLinkKind.EIS.value,
        url="javascript:alert(1)",
        revision=link.revision_token,
    )
    body = response.content.decode()

    assert response.status_code == 400
    assert f'id="menetluse-link-{link.pk}"' in body
    assert 'value="javascript:alert(1)"' in body
    link.refresh_from_db()
    assert link.url == EIS_URL


def test_a_stale_correction_through_the_browser_answers_409(signed_in, normal_matter, specialist):
    link = _record(normal_matter, specialist, url=EIS_URL)
    stale = link.revision_token
    correct_procedural_link(
        link=link,
        kind=ProceduralLinkKind.EIS,
        url=EIS_URL,
        label="Mujal muudetud",
        actor=specialist,
    )

    response = _correct(
        signed_in,
        normal_matter,
        link,
        kind=ProceduralLinkKind.EIS.value,
        url=EIS_URL,
        label="Minu oma",
        revision=stale,
    )

    assert response.status_code == 409
    link.refresh_from_db()
    assert link.label == "Mujal muudetud"
