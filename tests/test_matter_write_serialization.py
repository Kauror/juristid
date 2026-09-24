"""Matter edits serialise on the Matter before they touch the search index.

Three findings, one mechanism (ENG-027, ENG-028, ENG-029):

* **ENG-027.** `close_matter` locked the Matter and `add_evidence_version`
  locked the Document at plain `FOR UPDATE`, then reached the search rebuild
  gate through their `post_save` refresh. A running `rebuild_all` holds that
  gate exclusively and needs `FOR KEY SHARE` on the same row at COMMIT: a
  deadlock, which PostgreSQL resolved by killing one side.
* **ENG-029.** The set editors refreshed the search projection from inside
  `.set()`, before the Matter row was saved or locked, so two overlapping saves
  of one Teema took the projection row and the Matter row in opposite orders —
  a deadlock — or both deleted the same projection and one re-insert hit the
  unique index.
* **ENG-028.** The inline Valdkonnad, Saatja and Lühikokkuvõte editors, and the
  three structured-fact editors, replaced a whole value with what a possibly
  stale page showed, so a second tab silently reverted a colleague's save.

Every race here is forced, never hoped for: a writer is held at a precise point
until `pg_stat_activity` shows the other backend blocked on a lock (or finished,
when the fix means it never blocks), exactly as `tests/test_evidence_concurrency`
does. Nothing is asserted from elapsed time.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest
from django.db import connection, transaction
from django.test import Client
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.documents.services import add_evidence_version
from app.intelligence import services as facts
from app.intelligence.models import MatterEffectiveDate, MatterImportantDate, MatterWorkVictory
from app.matters.models import Matter
from app.matters.services import (
    MATTER_FIELD_CONFLICT,
    close_matter,
    matter_field_revision,
    set_brief_summary,
    set_policy_areas,
)
from app.search import indexing
from app.search import signals as search_signals
from app.search.indexing import indexable_matters, rebuild_all, refresh_matters
from app.search.management.commands.check_search_integrity import build_report
from app.search.models import INDEX_VERSION, SearchDocument, SearchSourceKind
from app.workflow.enums import Disposition
from tests import factories
from tests import synthetic_corpus as corpus
from tests.test_evidence_concurrency import TIMEOUT, Runner

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


# ---------------------------------------------------------------------------
# Forcing the interleaving
# ---------------------------------------------------------------------------


def _a_backend_is_waiting_on_a_lock() -> bool:
    """From the calling connection, whether any *other* backend is lock-blocked.

    `pg_stat_clear_snapshot()` first: inside a transaction PostgreSQL answers
    `pg_stat_activity` from a snapshot taken on first access, so a writer
    polling from within its own transaction would otherwise never see the
    other backend start waiting.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_stat_clear_snapshot()")
        cursor.execute(
            """
            SELECT count(*) FROM pg_stat_activity
             WHERE datname = current_database()
               AND wait_event_type = 'Lock'
               AND pid <> pg_backend_pid()
            """
        )
        return cursor.fetchone()[0] > 0


def _hold_until_the_other_waits_or_finishes(finished: threading.Event) -> None:
    deadline = time.monotonic() + TIMEOUT
    while not finished.is_set() and time.monotonic() < deadline:
        if _a_backend_is_waiting_on_a_lock():
            return
        # A polling interval, not a synchronisation primitive: the loop exits on
        # observed lock state or on the other side having finished.
        time.sleep(0.02)


def _race_against_a_rebuild(monkeypatch: Any, write: Callable[[], Any]) -> tuple[Any, Any]:
    """Run ``write`` holding its row lock while a full rebuild runs.

    The writer is held at the moment it asks for the rebuild gate — after its
    row lock, before its refresh — until the rebuild is either blocked on that
    row (the bug) or has committed (the fix). Only then does the writer ask for
    the gate. Under the bug that closes a cycle and PostgreSQL kills one side.
    """
    real_gate = indexing._hold_off_a_rebuild
    writer: dict[str, int] = {}
    holding = threading.Event()
    rebuilt = threading.Event()

    def gate() -> None:
        if threading.get_ident() == writer.get("ident") and not holding.is_set():
            holding.set()
            _hold_until_the_other_waits_or_finishes(rebuilt)
        real_gate()

    monkeypatch.setattr(indexing, "_hold_off_a_rebuild", gate)

    def do_write() -> None:
        writer["ident"] = threading.get_ident()
        with transaction.atomic():
            write()

    def do_rebuild() -> None:
        assert holding.wait(TIMEOUT)
        try:
            rebuild_all()
        finally:
            rebuilt.set()

    writing = Runner(do_write).start()
    rebuilding = Runner(do_rebuild).start()
    return writing.join(), rebuilding.join()


def _race_two_writes(
    monkeypatch: Any, first: Callable[[], Any], second: Callable[[], Any]
) -> tuple[Any, Any]:
    """``first`` holds just after its search refresh until ``second`` waits or ends.

    That is the widest point of the ENG-029 window: under the bug ``first`` has
    already rewritten the projection row and not yet saved the Matter.
    """
    real_refresh = search_signals.refresh_matters
    who: dict[str, int] = {}
    refreshed = threading.Event()
    second_done = threading.Event()

    def refresh(queryset: Any) -> int:
        written = real_refresh(queryset)
        if threading.get_ident() == who.get("first") and not refreshed.is_set():
            refreshed.set()
            _hold_until_the_other_waits_or_finishes(second_done)
        return written

    monkeypatch.setattr(search_signals, "refresh_matters", refresh)

    def run_first() -> Any:
        who["first"] = threading.get_ident()
        return first()

    def run_second() -> Any:
        assert refreshed.wait(TIMEOUT)
        try:
            return second()
        finally:
            second_done.set()

    results: dict[str, Any] = {}

    def keep(tag: str, target: Callable[[], Any]) -> Callable[[], None]:
        def run() -> None:
            results[tag] = target()

        return run

    one = Runner(keep("first", run_first)).start()
    two = Runner(keep("second", run_second)).start()
    errors = (one.join(), two.join())
    assert errors == (None, None), f"a request raised instead of answering: {errors!r}"
    return results["first"], results["second"]


# ---------------------------------------------------------------------------
# What "the index is still right" means
# ---------------------------------------------------------------------------


def _matter_rows(matter: Matter) -> list[tuple[str, str, str, str]]:
    return list(
        SearchDocument.objects.filter(
            source_kind=SearchSourceKind.MATTER, source_object_id=matter.pk
        ).values_list("title", "identifiers", "alias_text", "body_text")
    )


def assert_the_projection_is_current(matter: Matter) -> None:
    """Exactly one row for the Matter, and it says what a fresh projection says."""
    stored = _matter_rows(matter)
    assert len(stored) == 1, f"expected one MATTER row, found {len(stored)}"
    with transaction.atomic():
        refresh_matters(indexable_matters().filter(pk=matter.pk))
    assert _matter_rows(matter) == stored, "the stored projection was stale"


def assert_the_index_is_sound() -> None:
    report = build_report(sample=50)
    assert report.ok, [f"{finding.label}: {finding.detail}" for finding in report.findings]
    assert set(SearchDocument.objects.values_list("index_version", flat=True)) <= {INDEX_VERSION}


# ---------------------------------------------------------------------------
# ENG-027 — closure and a new evidence version against a running rebuild
# ---------------------------------------------------------------------------


def test_closing_a_matter_does_not_deadlock_against_a_rebuild(specialist, monkeypatch):
    matter = factories.MatterFactory(owner=specialist)

    writer_error, rebuild_error = _race_against_a_rebuild(
        monkeypatch,
        lambda: close_matter(
            matter=Matter.objects.get(pk=matter.pk),
            disposition=Disposition.COMPLETED,
            actor=specialist,
        ),
    )

    assert writer_error is None, f"the closure lost: {writer_error!r}"
    assert rebuild_error is None, f"the rebuild lost: {rebuild_error!r}"
    stored = Matter.objects.get(pk=matter.pk)
    assert not stored.is_open
    assert (
        ChangeEvent.objects.filter(matter=matter, event_type=ChangeEventType.MATTER_CLOSED).count()
        == 1
    )
    assert_the_projection_is_current(stored)
    assert_the_index_is_sound()


def test_a_new_version_of_an_extracted_document_does_not_deadlock_against_a_rebuild(
    specialist, monkeypatch, capture_evidence, extract
):
    """Only an ACTIVE derivative makes the upload's refresh reach the gate."""
    matter = factories.MatterFactory(owner=specialist)
    first = capture_evidence(
        matter, corpus.text_pdf(["Esimene versioon"]), "otsus.pdf", "application/pdf"
    )
    extract(first)
    document = first.document

    writer_error, rebuild_error = _race_against_a_rebuild(
        monkeypatch,
        lambda: add_evidence_version(
            document=document,
            content=corpus.text_pdf(["Teine versioon"]),
            original_filename="otsus-2.pdf",
            mime_type="application/pdf",
            uploaded_by=specialist,
        ),
    )

    assert writer_error is None, f"the upload lost: {writer_error!r}"
    assert rebuild_error is None, f"the rebuild lost: {rebuild_error!r}"
    document.refresh_from_db()
    assert document.versions.count() == 2
    assert document.current_version.version_number == 2
    assert_the_index_is_sound()


def test_two_closures_still_take_turns(specialist):
    """The weaker lock still excludes: one closure, one refusal, one event."""
    matter = factories.MatterFactory(owner=specialist)
    start = threading.Barrier(2, timeout=TIMEOUT)
    outcomes: dict[str, BaseException | None] = {}

    def close(tag: str) -> Callable[[], None]:
        def run() -> None:
            start.wait()
            try:
                with transaction.atomic():
                    close_matter(
                        matter=Matter.objects.get(pk=matter.pk),
                        disposition=Disposition.COMPLETED,
                        actor=specialist,
                    )
                outcomes[tag] = None
            except BaseException as error:  # recorded, not swallowed
                outcomes[tag] = error

        return run

    first = Runner(close("A")).start()
    second = Runner(close("B")).start()
    assert first.join() is None and second.join() is None

    refused = [error for error in outcomes.values() if error is not None]
    assert len(refused) == 1 and isinstance(refused[0], DomainError)
    assert (
        ChangeEvent.objects.filter(matter=matter, event_type=ChangeEventType.MATTER_CLOSED).count()
        == 1
    )


def test_two_new_versions_still_take_turns(specialist, capture_evidence):
    matter = factories.MatterFactory(owner=specialist)
    document = capture_evidence(
        matter, corpus.text_pdf(["Alus"]), "a.pdf", "application/pdf"
    ).document
    start = threading.Barrier(2, timeout=TIMEOUT)

    def upload(name: str) -> Callable[[], None]:
        def run() -> None:
            start.wait()
            add_evidence_version(
                document=document,
                content=corpus.text_pdf([name]),
                original_filename=f"{name}.pdf",
                mime_type="application/pdf",
            )

        return run

    first = Runner(upload("b")).start()
    second = Runner(upload("c")).start()
    assert first.join() is None and second.join() is None
    assert sorted(document.versions.values_list("version_number", flat=True)) == [1, 2, 3]


# ---------------------------------------------------------------------------
# ENG-029 — two saves of one Teema, through the real endpoints
# ---------------------------------------------------------------------------


def _client(user: Any) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _post(user: Any, url: str, data: dict[str, Any]) -> int:
    return _client(user).post(url, data, HTTP_HX_REQUEST="true").status_code


def _field_url(matter: Matter, field: str) -> str:
    return reverse("matters:update_field", kwargs={"pk": matter.pk, "field": field})


def _summary_url(matter: Matter) -> str:
    return reverse("matters:update_summary", kwargs={"pk": matter.pk})


def test_valdkonnad_against_valdkonnad_one_saves_and_the_stale_one_is_told(
    specialist, other_specialist, monkeypatch
):
    """Both forms were rendered from the same set; the second to reach the lock
    is stale and gets 409 rather than an IntegrityError or a silent revert."""
    matter = factories.MatterFactory(owner=specialist)
    first_area = factories.PolicyAreaFactory(name_et="Energeetika")
    second_area = factories.PolicyAreaFactory(name_et="Transport")
    token = matter_field_revision(matter, "policy_areas")

    first, second = _race_two_writes(
        monkeypatch,
        lambda: _post(
            specialist,
            _field_url(matter, "policy_areas"),
            {"policy_areas": [first_area.pk], "revision": token},
        ),
        lambda: _post(
            other_specialist,
            _field_url(matter, "policy_areas"),
            {"policy_areas": [second_area.pk], "revision": token},
        ),
    )

    assert sorted([first, second]) == [200, 409]
    stored = Matter.objects.get(pk=matter.pk)
    assert list(stored.policy_areas.values_list("name_et", flat=True)) == ["Energeetika"]
    assert (
        ChangeEvent.objects.filter(
            matter=matter, event_type=ChangeEventType.MATTER_POLICY_AREAS_CHANGED
        ).count()
        == 1
    )
    assert_the_projection_is_current(stored)
    assert_the_index_is_sound()


def test_valdkonnad_against_the_summary_both_save_in_turn(
    specialist, other_specialist, monkeypatch
):
    """Different fields, both fresh: serialised, not refused, and neither lost.
    This pair deadlocked before (ENG-029)."""
    matter = factories.MatterFactory(owner=specialist, brief_summary="Algne")
    area = factories.PolicyAreaFactory(name_et="Keskkond")

    first, second = _race_two_writes(
        monkeypatch,
        lambda: _post(
            specialist,
            _field_url(matter, "policy_areas"),
            {
                "policy_areas": [area.pk],
                "revision": matter_field_revision(matter, "policy_areas"),
            },
        ),
        lambda: _post(
            other_specialist,
            _summary_url(matter),
            {
                "brief_summary": "Uus kokkuvõte",
                "revision": matter_field_revision(matter, "brief_summary"),
            },
        ),
    )

    assert (first, second) == (200, 200)
    stored = Matter.objects.get(pk=matter.pk)
    assert list(stored.policy_areas.values_list("name_et", flat=True)) == ["Keskkond"]
    assert stored.brief_summary == "Uus kokkuvõte"
    assert_the_projection_is_current(stored)
    assert_the_index_is_sound()


def test_saatja_against_another_field_of_the_same_matter(specialist, other_specialist, monkeypatch):
    matter = factories.MatterFactory(owner=specialist)
    sender = factories.OrganisationFactory(name="Rahandusministeerium")

    first, second = _race_two_writes(
        monkeypatch,
        lambda: _post(
            specialist,
            _field_url(matter, "source_organisations"),
            {
                "source_organisations": [sender.pk],
                "revision": matter_field_revision(matter, "source_organisations"),
            },
        ),
        lambda: _post(
            other_specialist,
            _field_url(matter, "response_deadline"),
            {"response_deadline": "1.12.2026"},
        ),
    )

    assert (first, second) == (200, 200)
    stored = Matter.objects.get(pk=matter.pk)
    assert list(stored.source_organisations.values_list("name", flat=True)) == [
        "Rahandusministeerium"
    ]
    assert stored.response_deadline is not None
    assert_the_projection_is_current(stored)
    assert_the_index_is_sound()


def test_the_set_services_serialise_for_every_caller(specialist, monkeypatch):
    """The Matter-first lock is in the services, not only behind the views, so
    `Muuda teemat` and any other caller get it too (ENG-029)."""
    matter = factories.MatterFactory(owner=specialist)
    area = factories.PolicyAreaFactory(name_et="Põllumajandus")

    def areas() -> None:
        with transaction.atomic():
            set_policy_areas(
                matter=Matter.objects.get(pk=matter.pk), policy_areas=[area], actor=specialist
            )

    def summary() -> None:
        with transaction.atomic():
            set_brief_summary(
                matter=Matter.objects.get(pk=matter.pk), value="Teenus", actor=specialist
            )

    _race_two_writes(monkeypatch, areas, summary)
    stored = Matter.objects.get(pk=matter.pk)
    assert stored.brief_summary == "Teenus"
    assert list(stored.policy_areas.all()) == [area]
    assert_the_projection_is_current(stored)


# ---------------------------------------------------------------------------
# ENG-028 — two tabs, through the rendered page
# ---------------------------------------------------------------------------


def _revision_for(page: str, action: str) -> str:
    """The `revision` a form posting to ``action`` was rendered with."""
    start = page.index(f'hx-post="{action}"')
    match = re.search(r'name="revision" value="([^"]*)"', page[start:])
    assert match, f"no revision rendered for {action}"
    return match.group(1)


def _open(client: Client, matter: Matter) -> str:
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    return response.content.decode()


@pytest.fixture
def two_tabs(specialist, other_specialist):
    return _client(specialist), _client(other_specialist)


def test_a_stale_valdkonnad_does_not_revert_a_colleagues_save(two_tabs, specialist):
    matter = factories.MatterFactory(owner=specialist)
    base = factories.PolicyAreaFactory(name_et="Maksud")
    added_by_b = factories.PolicyAreaFactory(name_et="Tööturg")
    added_by_a = factories.PolicyAreaFactory(name_et="Digiriik")
    matter.policy_areas.set([base])
    tab_a, tab_b = two_tabs
    url = _field_url(matter, "policy_areas")
    token_a = _revision_for(_open(tab_a, matter), url)
    token_b = _revision_for(_open(tab_b, matter), url)

    saved = tab_b.post(url, {"policy_areas": [base.pk, added_by_b.pk], "revision": token_b})
    stale = tab_a.post(url, {"policy_areas": [base.pk, added_by_a.pk], "revision": token_a})

    assert saved.status_code == 200
    assert stale.status_code == 409
    assert MATTER_FIELD_CONFLICT in stale.content.decode()
    names = set(Matter.objects.get(pk=matter.pk).policy_areas.values_list("name_et", flat=True))
    assert names == {"Maksud", "Tööturg"}

    # The refusal re-renders the header with the stored set and a fresh token,
    # so a second press — a decision, having seen it — saves.
    again = tab_a.post(
        url,
        {
            "policy_areas": [base.pk, added_by_b.pk, added_by_a.pk],
            "revision": _revision_for(stale.content.decode(), url),
        },
    )
    assert again.status_code == 200
    names = set(Matter.objects.get(pk=matter.pk).policy_areas.values_list("name_et", flat=True))
    assert names == {"Maksud", "Tööturg", "Digiriik"}


def test_a_stale_saatja_does_not_revert_a_colleagues_save(two_tabs, specialist):
    matter = factories.MatterFactory(owner=specialist)
    by_b = factories.OrganisationFactory(name="Justiitsministeerium")
    by_a = factories.OrganisationFactory(name="Riigikantselei")
    tab_a, tab_b = two_tabs
    url = _field_url(matter, "source_organisations")
    token_a = _revision_for(_open(tab_a, matter), url)
    token_b = _revision_for(_open(tab_b, matter), url)

    assert (
        tab_b.post(url, {"source_organisations": [by_b.pk], "revision": token_b}).status_code == 200
    )
    stale = tab_a.post(url, {"source_organisations": [by_a.pk], "revision": token_a})

    assert stale.status_code == 409
    assert MATTER_FIELD_CONFLICT in stale.content.decode()
    names = list(
        Matter.objects.get(pk=matter.pk).source_organisations.values_list("name", flat=True)
    )
    assert names == ["Justiitsministeerium"]


def test_a_stale_summary_does_not_revert_a_colleagues_save_and_keeps_the_draft(
    two_tabs, specialist
):
    matter = factories.MatterFactory(owner=specialist, brief_summary="Algne kokkuvõte")
    tab_a, tab_b = two_tabs
    url = _summary_url(matter)
    token_a = _revision_for(_open(tab_a, matter), url)
    token_b = _revision_for(_open(tab_b, matter), url)

    assert (
        tab_b.post(url, {"brief_summary": "Kolleegi kokkuvõte", "revision": token_b}).status_code
        == 200
    )
    stale = tab_a.post(url, {"brief_summary": "Minu mustand", "revision": token_a})

    assert stale.status_code == 409
    body = stale.content.decode()
    assert MATTER_FIELD_CONFLICT in body
    assert "Kolleegi kokkuvõte" in body  # what is stored, above the box
    assert "Minu mustand" in body  # what they typed, still in it
    assert Matter.objects.get(pk=matter.pk).brief_summary == "Kolleegi kokkuvõte"


def test_saving_one_field_does_not_make_another_field_stale_in_the_same_tab(two_tabs, specialist):
    """Why the token is the field's own: the header and the rail re-render
    separately, so a whole-Matter token would refuse a person in their own tab."""
    matter = factories.MatterFactory(owner=specialist)
    area = factories.PolicyAreaFactory(name_et="Kultuur")
    sender = factories.OrganisationFactory(name="Kultuuriministeerium")
    tab, _ = two_tabs
    page = _open(tab, matter)
    senders_url = _field_url(matter, "source_organisations")
    areas_url = _field_url(matter, "policy_areas")
    summary_url = _summary_url(matter)

    assert (
        tab.post(
            senders_url,
            {"source_organisations": [sender.pk], "revision": _revision_for(page, senders_url)},
        ).status_code
        == 200
    )
    assert (
        tab.post(
            areas_url, {"policy_areas": [area.pk], "revision": _revision_for(page, areas_url)}
        ).status_code
        == 200
    )
    assert (
        tab.post(
            summary_url,
            {"brief_summary": "Kokkuvõte", "revision": _revision_for(page, summary_url)},
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    ("field", "data"),
    [
        ("policy_areas", {"policy_areas": []}),
        ("source_organisations", {"source_organisations": []}),
    ],
)
def test_a_whole_value_save_without_a_token_is_refused(two_tabs, specialist, field, data):
    matter = factories.MatterFactory(owner=specialist)
    tab, _ = two_tabs
    assert tab.post(_field_url(matter, field), data).status_code == 409


def test_a_summary_save_without_a_token_is_refused(two_tabs, specialist):
    matter = factories.MatterFactory(owner=specialist, brief_summary="Jääb")
    tab, _ = two_tabs
    assert tab.post(_summary_url(matter), {"brief_summary": "Uus"}).status_code == 409
    assert Matter.objects.get(pk=matter.pk).brief_summary == "Jääb"


# ---------------------------------------------------------------------------
# ENG-028 — the structured-fact editors, still reachable by URL
# ---------------------------------------------------------------------------


def _fact_form(client: Client, url: str) -> str:
    response = client.get(url)
    assert response.status_code == 200
    match = re.search(r'name="revision" value="([^"]*)"', response.content.decode())
    assert match, "the edit form rendered no revision"
    return match.group(1)


def test_a_stale_important_date_edit_is_refused(two_tabs, specialist):
    import datetime as dt

    matter = factories.MatterFactory(owner=specialist)
    record = facts.add_important_date(
        matter=matter,
        title="Kooskõlastusring",
        date_value=dt.date(2026, 10, 1),
        period_end=dt.date(2026, 10, 1),
        note="Algne märkus",
        actor=specialist,
    )
    tab_a, tab_b = two_tabs
    url = reverse(
        "intelligence:edit_important_date", kwargs={"matter_id": matter.pk, "pk": record.pk}
    )
    token_a, token_b = _fact_form(tab_a, url), _fact_form(tab_b, url)
    data = {"title": "Kooskõlastusring", "precision": "EXACT", "exact_date": "1.10.2026"}

    assert (
        tab_b.post(url, {**data, "note": "Kolleegi märkus", "revision": token_b}).status_code == 302
    )
    stale = tab_a.post(url, {**data, "note": "Algne märkus", "revision": token_a})

    assert stale.status_code == 409
    assert facts.FACT_EDIT_CONFLICT in stale.content.decode()
    assert MatterImportantDate.objects.get(pk=record.pk).note == "Kolleegi märkus"


def test_a_stale_effective_date_edit_is_refused(two_tabs, specialist):
    import datetime as dt

    matter = factories.MatterFactory(owner=specialist)
    record = facts.add_effective_date(
        matter=matter,
        date_value=dt.date(2027, 1, 1),
        period_end=dt.date(2027, 1, 1),
        description="Seadus jõustub",
        actor=specialist,
    )
    tab_a, tab_b = two_tabs
    url = reverse(
        "intelligence:edit_effective_date", kwargs={"matter_id": matter.pk, "pk": record.pk}
    )
    token_a, token_b = _fact_form(tab_a, url), _fact_form(tab_b, url)
    data = {"kind": record.kind, "precision": "EXACT", "exact_date": "1.1.2027"}

    assert (
        tab_b.post(
            url, {**data, "description": "Kolleegi kirjeldus", "revision": token_b}
        ).status_code
        == 302
    )
    stale = tab_a.post(url, {**data, "description": "Seadus jõustub", "revision": token_a})

    assert stale.status_code == 409
    assert MatterEffectiveDate.objects.get(pk=record.pk).description == "Kolleegi kirjeldus"


def test_a_stale_work_victory_edit_is_refused(two_tabs, specialist):
    matter = factories.MatterFactory(owner=specialist)
    record = facts.add_confirmed_work_victory(
        matter=matter, title="Maksumuudatus jäi ära", actor=specialist
    )
    tab_a, tab_b = two_tabs
    url = reverse(
        "intelligence:edit_work_victory", kwargs={"matter_id": matter.pk, "pk": record.pk}
    )
    token_a, token_b = _fact_form(tab_a, url), _fact_form(tab_b, url)
    data = {"precision": "YEAR", "year": "2026"}

    assert (
        tab_b.post(url, {**data, "title": "Kolleegi sõnastus", "revision": token_b}).status_code
        == 302
    )
    stale = tab_a.post(url, {**data, "title": "Maksumuudatus jäi ära", "revision": token_a})

    assert stale.status_code == 409
    assert MatterWorkVictory.objects.get(pk=record.pk).title == "Kolleegi sõnastus"


def test_a_fact_service_caller_without_a_form_keeps_the_old_contract(specialist):
    """``expected_revision=None`` is a caller that rendered nothing."""
    import datetime as dt

    matter = factories.MatterFactory(owner=specialist)
    record = facts.add_important_date(
        matter=matter,
        title="Algne",
        date_value=dt.date(2026, 10, 1),
        period_end=dt.date(2026, 10, 1),
        actor=specialist,
    )
    updated = facts.update_important_date(
        record=record,
        title="Muudetud",
        date_value=dt.date(2026, 10, 2),
        period_end=dt.date(2026, 10, 2),
        date_precision="EXACT",
        actor=specialist,
    )
    assert updated.title == "Muudetud"
