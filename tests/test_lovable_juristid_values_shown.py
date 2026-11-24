"""HR-10 PR 2: Juristid's own value (B) reads as words wherever it is shown.

The engine records B in its own shape: the owner as a user id, the stage as
its vocabulary key, the opinion outcome as a record. Haldus «Vaata erinevusi»
(rows and the resolve page), the Haldus Lovable panel and MCP
``get_lovable_sync_matter_changes`` showed that shape — a UUID, a key such as
``OPINION_DRAFTING``, and «—» for a real outcome. They now show the person's
name, the stage's label and the outcome's words. Rendering only: the stored
decisions keep the raw values, and a missing or unknown value is shown as
before (D13, the missing owner's wording, is the owner's decision).

Synthetic data only: one real COMPARE night of the fake Lovable transport
(`tests.test_lovable_sync`), whose stored decisions are then given the
engine's other B shapes.
"""

from __future__ import annotations

import uuid

import pytest

from app.legacy_import import lovable_observability as obs
from app.legacy_import import lovable_sync, sync_admin
from app.legacy_import.lovable_display import JuristidNames, juristid_names, juristid_text
from app.legacy_import.lovable_sync_models import LovableSyncDecision, SyncMode, SyncOutcome
from app.mcp_server import readers
from app.workflow.models import StageVocabulary
from tests.test_lovable_pilot import pilot_on, world  # noqa: F401
from tests.test_lovable_sync import _run, cohort, haldus  # noqa: F401

REFERENCE = "2026_501"
OUTCOME = {"outcome": "SENT", "date": "2026-01-05", "addressee": "Riigikogu"}


@pytest.fixture
def night(cohort, settings):  # noqa: F811
    """A COMPARE night in which Lovable moved 2026_501's stage (one pending
    stage), plus the engine's owner and opinion-outcome B shapes on pending
    decisions of the same Teema."""
    # The MCP answer links back to the app.
    settings.JURISTID_PUBLIC_ORIGIN = "https://juristid.example.invalid"
    lovable_sync.set_mode(SyncMode.COMPARE, reason="t")
    cohort["source"].edit(REFERENCE, hetkeseis="Valitsuses")
    run = _run(cohort)
    stage = LovableSyncDecision.objects.get(run=run, state__reference=REFERENCE, field="stage")
    jaan = cohort["world"]["jaan"]

    def pending(field_name: str, current, source="x"):
        LovableSyncDecision.objects.filter(
            run=run, state__reference=REFERENCE, field=field_name
        ).delete()
        LovableSyncDecision.objects.create(
            run=run,
            state=stage.state,
            field=field_name,
            outcome=SyncOutcome.REVIEW_REQUIRED,
            reason=lovable_sync.HELD,
            baseline=None,
            current=current,
            source=source,
        )

    pending("owner", str(jaan.pk), source="Jaan")
    pending("sent", OUTCOME, source="2026-01-05")
    pending("sent_date", OUTCOME, source="2026-01-05")
    pending("addressee", OUTCOME, source="Riigikogu")
    return {**cohort, "run": run, "stage": stage, "jaan": jaan}


def _stage_key(current) -> str:
    """B of a stage decision: a key, or under source authority (docs/adr/0213
    §10) the lifecycle record that carries it."""
    return current["stage"] if isinstance(current, dict) else current


def _stage_label(current) -> str:
    return StageVocabulary.objects.get(key=_stage_key(current)).label_et


def _rows(reader) -> dict[str, sync_admin.DifferenceRow]:
    rows, _hidden, _held = sync_admin.engine_rows(reader)
    return {r.field: r for r in rows if r.reference == REFERENCE}


# ---------------------------------------------------------------------------
# Haldus «Vaata erinevusi» and its resolve page
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_vaata_erinevusi_shows_the_owner_by_name_not_by_id(night):
    row = _rows(night["world"]["mari"])["owner"]
    assert row.juristid == "Jaan Näidis"
    assert str(night["jaan"].pk) not in row.juristid


@pytest.mark.django_db
def test_vaata_erinevusi_shows_the_stage_label_not_its_key(night):
    current = night["stage"].current
    key = _stage_key(current)
    assert key and isinstance(key, str)
    row = _rows(night["world"]["mari"])["stage"]
    # Was «True · parliament»: the lifecycle record's raw values.
    assert row.juristid == _stage_label(current)
    assert key not in row.juristid and "True" not in row.juristid


@pytest.mark.django_db
def test_vaata_erinevusi_shows_the_outcome_in_words(night):
    rows = _rows(night["world"]["mari"])
    for name in ("sent", "sent_date", "addressee"):
        # The page's own day format is kept (D4 is the owner's decision).
        assert rows[name].juristid == "Arvamus saadetud · 5.1.2026 · Riigikogu", name


@pytest.mark.django_db
def test_the_resolve_page_shows_the_same_words(night):
    reader = night["world"]["mari"]
    assert sync_admin.difference_for(reader, REFERENCE, "owner").juristid == "Jaan Näidis"
    stage = sync_admin.difference_for(reader, REFERENCE, "stage")
    assert stage.juristid == _stage_label(night["stage"].current)


@pytest.mark.django_db
def test_stored_decisions_keep_their_raw_values(night):
    """Rendering only: nothing the engine stored or compares is rewritten."""
    sync_admin.engine_rows(night["world"]["mari"])
    obs.matter_changes(night["world"]["mari"], REFERENCE)
    stored = {
        d.field: d.current
        for d in LovableSyncDecision.objects.filter(run=night["run"], state__reference=REFERENCE)
    }
    assert stored["owner"] == str(night["jaan"].pk)
    assert stored["stage"] == night["stage"].current
    assert stored["sent"] == OUTCOME
    item = next(
        p
        for p in lovable_sync.pending_differences(visible_to=None)
        if p.reference == REFERENCE and p.field == "owner"
    )
    assert item.current == str(night["jaan"].pk)


@pytest.mark.django_db
def test_the_haldus_lovable_panel_shows_names_labels_and_the_outcome(client, haldus, night):  # noqa: F811
    from django.urls import reverse

    from tests.admin_session import choose

    haldus["verified"](client, haldus["admin"], haldus["secret"])
    choose(client, night["world"]["mari"])
    response = client.get(reverse("haldus:lovable"))
    assert response.status_code == 200
    pending = {
        row["field"]: row["current"]
        for row in response.context["pending"]
        if row["matter"].display_reference == REFERENCE
    }
    assert pending["Vastutaja"] == "Jaan Näidis"
    assert pending["Hetkeseis"] == _stage_label(night["stage"].current)
    # The panel's own ISO day is kept (D4 is the owner's decision).
    assert pending["Välja"] == "Arvamus saadetud · 2026-01-05 · Riigikogu"
    # (The page body names Jaan's id elsewhere: the persona picker's form.)


# ---------------------------------------------------------------------------
# MCP get_lovable_sync_matter_changes (and the read service behind it)
# ---------------------------------------------------------------------------


def _decisions(answer: dict) -> dict[str, dict]:
    return {d["field"]: d for d in answer["decisions"]}


@pytest.mark.django_db
def test_mcp_matter_changes_shows_names_labels_and_the_outcome(night):
    reader = night["world"]["mari"]
    answer = readers.lovable_sync_matter_changes(reader, REFERENCE)
    decisions = _decisions(answer)
    assert decisions["owner"]["current"] == "Jaan Näidis"
    assert decisions["stage"]["current"] == _stage_label(night["stage"].current)
    for name in ("sent", "sent_date", "addressee"):
        # Was «—»: the outcome record was read as a step with no day or text.
        assert decisions[name]["current"] == "Arvamus saadetud · 05.01.2026 · Riigikogu", name
    # Lovable's own values (C) and the baseline (A) are rendered as before.
    assert decisions["owner"]["source"] == "Jaan"
    assert decisions["sent"]["source"] == "05.01.2026"


# ---------------------------------------------------------------------------
# Missing and unknown stay apart, and stay as they were
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_missing_or_unknown_value_is_shown_as_before(night):
    run = night["run"]
    gone = str(uuid.uuid4())
    decisions = LovableSyncDecision.objects.filter(run=run, state__reference=REFERENCE)
    decisions.filter(field="owner").update(current=gone)
    decisions.filter(field="stage").update(current="NO_SUCH_STAGE")
    decisions.filter(field="sent").update(current=None)
    reader = night["world"]["mari"]
    rows = _rows(reader)
    # Unknown: shown as recorded, never guessed or blanked.
    assert rows["owner"].juristid == gone
    assert rows["stage"].juristid == "NO_SUCH_STAGE"
    # Missing: «—», as before (the missing owner's words are decision D13).
    assert rows["sent"].juristid == "—"
    mcp = _decisions(readers.lovable_sync_matter_changes(reader, REFERENCE))
    assert mcp["owner"]["current"] == gone
    assert mcp["stage"]["current"] == "NO_SUCH_STAGE"
    assert mcp["sent"]["current"] == "—"


def test_the_renderer_leaves_every_other_value_to_the_caller():
    names = JuristidNames(owners={}, stages={})
    day = str
    assert juristid_text("owner", None, names, day=day) is None
    assert juristid_text("owner", "", names, day=day) is None
    assert juristid_text("owner", "not-a-uuid", names, day=day) is None
    assert juristid_text("title", "Pealkiri", names, day=day) is None
    assert juristid_text("next_action_text", {"text": "x"}, names, day=day) is None
    assert juristid_text("sent", "2026-01-05", names, day=day) is None
    assert (
        juristid_text("sent", {"outcome": "NOT_SENT", "date": None}, names, day=day)
        == "Arvamust ei saadetud"
    )
    assert juristid_text("sent", {"outcome": "ODD"}, names, day=day) == "ODD"
    labelled = JuristidNames(stages={"parliament": "Riigikogus"})
    closed = {"open": False, "stage": "parliament", "disposition": "SUPERSEDED", "successor": "s"}
    assert (
        juristid_text("stage", closed, labelled, day=day)
        == "Riigikogus · suletud: Asendatud järgnenud teemaga · jätkub: s"
    )
    assert juristid_text("stage", {"open": True, "stage": "gone"}, labelled, day=day) == "gone"
    assert juristid_text("stage", {"open": True, "stage": None}, labelled, day=day) is None


@pytest.mark.django_db
def test_names_cost_nothing_when_no_row_needs_them(django_assert_num_queries):
    with django_assert_num_queries(0):
        juristid_names([("title", "x"), ("owner", None), ("owner", "not-a-uuid"), ("sent", {})])
