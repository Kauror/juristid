"""`Menetluse kulg` — the four-state process rail, and the claims it must refuse.

docs/adr/0092 §13–§15. The rail answers *where is this procedure now*, and the
whole design is about what it is not allowed to say while answering:

* a current stage proves the **current** stage and nothing to its left, so an
  archive Matter first filed at `Riigikogus` reads `Teadmata` for the three
  nodes before it and never `Tehtud` (§13, the late-entry rule);
* a node is `Kirjas` because a stage was **explicitly recorded** — never because
  of a title, a filename, an organisation, a `Menetluse link`, a URL host,
  today's date or a node's position in the list;
* `Õigusakt` may choose a coarse *presentation* and may never write
  `Matter.track`; `NATIONAL_TRANSPOSITION` is not inferred from `Seadus` (§12);
* `Muu` is not forced onto a node, and `Rohkem ei tegele` is not a node at all
  (§13, §15);
* and `Jõustunud` says the act came into force, not that the file is closed
  (docs/adr/0032).
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.matters.legal_process import (
    KODA_STOPPED_LABEL,
    STATE_CURRENT,
    STATE_POSSIBLE,
    STATE_RECORDED,
    STATE_UNKNOWN,
    TEMPLATE_DOMESTIC,
    TEMPLATE_EU,
    legal_process_rail,
    recorded_stage_keys,
    template_for,
)
from app.matters.services import change_stage, close_matter
from app.taxonomy.legal_instruments import (
    DOMESTIC_LEGAL_INSTRUMENT_KEYS,
    EU_LEGAL_INSTRUMENT_KEYS,
)
from app.taxonomy.models import LegalInstrumentType
from app.workflow.enums import Disposition, Track
from app.workflow.models import StageVocabulary
from tests import factories

pytestmark = pytest.mark.django_db


def _stage(key: str) -> StageVocabulary:
    """The reviewed row, never a second one invented here."""
    return StageVocabulary.objects.get(key=key)


def _instrument(key: str) -> LegalInstrumentType:
    return LegalInstrumentType.objects.get(key=key)


def _rail(matter, user):
    return legal_process_rail(matter=matter, user=user)


def _states(rail) -> dict[str, str]:
    return {node.key: node.state for node in rail.nodes}


# ---------------------------------------------------------------------------
# Choosing a rail
# ---------------------------------------------------------------------------


def test_the_track_chooses_the_rail_where_its_semantics_safely_can():
    assert template_for(track=Track.DOMESTIC.value, instrument_keys=frozenset()) == (
        TEMPLATE_DOMESTIC
    )
    assert template_for(track=Track.EU_INITIATIVE.value, instrument_keys=frozenset()) == (
        TEMPLATE_EU
    )


@pytest.mark.parametrize(
    "track",
    [
        Track.NATIONAL_TRANSPOSITION.value,
        Track.STRATEGY.value,
        Track.KODA_INITIATIVE.value,
        Track.IMPLEMENTATION.value,
        Track.OTHER.value,
        "",
    ],
)
def test_the_other_tracks_choose_nothing_and_fall_through(track):
    """Five values plus «nobody said», and none of them answers the question.

    `NATIONAL_TRANSPOSITION` is the one worth naming: a `Seadus` transposing a
    directive runs the domestic procedure *and* the file is about a European
    instrument, so the track alone cannot pick a rail. It falls through to the
    instrument grouping, which answers from what the Matter holds.
    """
    assert template_for(track=track, instrument_keys=frozenset()) == ""
    assert template_for(track=track, instrument_keys=frozenset({"seadus"})) == TEMPLATE_DOMESTIC
    assert template_for(track=track, instrument_keys=frozenset({"direktiiv"})) == TEMPLATE_EU


def test_a_mixed_instrument_file_draws_no_rail():
    """`S, M` and `direktiiv ja määrus` are both real register values.

    A Matter carrying one domestic and one European instrument has no reading
    that picks a rail, so it gets neither and `Hetkeseis` goes on answering.
    """
    assert template_for(track="", instrument_keys=frozenset({"seadus", "direktiiv"})) == ""


def test_the_reviewed_groups_are_what_the_projection_reads():
    """The two sets are Package A's, read and never copied."""
    assert "seadus" in DOMESTIC_LEGAL_INSTRUMENT_KEYS
    assert "direktiiv" in EU_LEGAL_INSTRUMENT_KEYS
    assert not DOMESTIC_LEGAL_INSTRUMENT_KEYS & EU_LEGAL_INSTRUMENT_KEYS


def test_choosing_a_template_from_oigusakt_never_writes_a_track(specialist):
    """Scenario C's «no persisted track invented».

    The rail is drawn from `direktiiv` and `Menetlusliik` stays exactly as empty
    as the person left it (docs/adr/0090 §4, docs/adr/0092 §12).
    """
    matter = factories.MatterFactory(owner=specialist, track="")
    matter.legal_instruments.set([_instrument("direktiiv")])
    change_stage(matter=matter, stage=_stage("eu_procedure"), actor=specialist)

    rail = _rail(matter, specialist)
    assert rail is not None
    assert rail.template == TEMPLATE_EU
    matter.refresh_from_db()
    assert matter.track == ""


def test_a_seadus_does_not_become_a_transposition(specialist):
    """`Õigusakt` chooses a presentation and asserts nothing about the track."""
    matter = factories.MatterFactory(owner=specialist, track="")
    matter.legal_instruments.set([_instrument("seadus")])
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)

    assert _rail(matter, specialist).template == TEMPLATE_DOMESTIC
    matter.refresh_from_db()
    assert matter.track != Track.NATIONAL_TRANSPOSITION.value
    assert matter.track == ""


# ---------------------------------------------------------------------------
# The four states
# ---------------------------------------------------------------------------


def test_a_domestic_file_on_a_consultation_round(specialist):
    matter = factories.MatterFactory(owner=specialist, track=Track.DOMESTIC.value)
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)

    rail = _rail(matter, specialist)
    assert rail.template == TEMPLATE_DOMESTIC
    assert _states(rail) == {
        "algus": STATE_UNKNOWN,
        "kooskolastus": STATE_CURRENT,
        "valitsus": STATE_POSSIBLE,
        "riigikogu": STATE_POSSIBLE,
        "joustumine": STATE_POSSIBLE,
    }


def test_an_eu_file_on_the_estonian_position(specialist):
    """Scenario C. The European rail, and no domestic claim anywhere on it."""
    matter = factories.MatterFactory(owner=specialist, track=Track.EU_INITIATIVE.value)
    change_stage(matter=matter, stage=_stage("estonian_eu_position"), actor=specialist)

    rail = _rail(matter, specialist)
    assert rail.template == TEMPLATE_EU
    assert _states(rail) == {
        "algus": STATE_UNKNOWN,
        "eesti-seisukoht": STATE_CURRENT,
        "el-menetlus": STATE_POSSIBLE,
        "vastu-voetud": STATE_POSSIBLE,
        "ulevotmine": STATE_POSSIBLE,
    }
    assert "Riigikogu" not in [node.label for node in rail.nodes]
    assert "Valitsus" not in [node.label for node in rail.nodes]


def test_a_late_entry_shows_unknown_and_never_completed(specialist):
    """Scenario B, and the rule this component exists for.

    A Matter first created when the bill was already in the Riigikogu. The three
    earlier nodes could be in the past and nothing on this file says they
    happened — so they read `Teadmata`, and the word `Tehtud` appears nowhere.
    """
    matter = factories.ArchiveMatterFactory(owner=specialist, track=Track.DOMESTIC.value)
    matter.stage = _stage("parliament")
    matter.save(update_fields=["stage"])

    rail = _rail(matter, specialist)
    assert _states(rail) == {
        "algus": STATE_UNKNOWN,
        "kooskolastus": STATE_UNKNOWN,
        "valitsus": STATE_UNKNOWN,
        "riigikogu": STATE_CURRENT,
        "joustumine": STATE_POSSIBLE,
    }
    labels = [node.state_label for node in rail.nodes]
    assert labels == ["Teadmata", "Teadmata", "Teadmata", "Praegu", "Võimalik"]
    assert "Tehtud" not in labels
    assert "Lõpetatud" not in labels


def test_an_explicitly_recorded_earlier_stage_is_promoted(specialist):
    """The one thing that moves a node out of `Teadmata`."""
    matter = factories.MatterFactory(owner=specialist, track=Track.DOMESTIC.value)
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    change_stage(matter=matter, stage=_stage("government"), actor=specialist)
    change_stage(matter=matter, stage=_stage("parliament"), actor=specialist)

    rail = _rail(matter, specialist)
    assert _states(rail) == {
        "algus": STATE_UNKNOWN,
        "kooskolastus": STATE_RECORDED,
        "valitsus": STATE_RECORDED,
        "riigikogu": STATE_CURRENT,
        "joustumine": STATE_POSSIBLE,
    }


def test_a_recorded_stage_is_read_from_the_history_by_its_stable_key(specialist):
    matter = factories.MatterFactory(owner=specialist, track=Track.DOMESTIC.value)
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    change_stage(matter=matter, stage=_stage("parliament"), actor=specialist)

    assert {"consultation", "parliament"} <= recorded_stage_keys(matter=matter, user=specialist)


def test_a_historical_row_that_carries_only_a_label_still_resolves(specialist):
    """Rows written before the payload carried a key are not lost.

    Nothing is backfilled: the label is resolved through the reviewed
    vocabulary, live, at read time — which includes the version-1.0 wording of
    the three labels the lawyers reworded (app/workflow/reference_stages.py).
    """
    from app.audit.enums import ChangeEventType
    from app.audit.models import ChangeEvent

    matter = factories.MatterFactory(owner=specialist, track=Track.DOMESTIC.value)
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    change_stage(matter=matter, stage=_stage("parliament"), actor=specialist)
    # Exactly what a pre-docs/adr/0092 row holds.
    for event in ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.MATTER_STAGE_CHANGED
    ):
        payload = dict(event.payload)
        payload.pop("to_key", None)
        payload.pop("from_key", None)
        ChangeEvent.objects.filter(pk=event.pk).update(payload=payload)

    assert "consultation" in recorded_stage_keys(matter=matter, user=specialist)


def test_nothing_is_recorded_from_a_title_a_file_or_a_link(specialist, organisation):
    """Words are not evidence. Neither is a `Menetluse link` (docs/adr/0091 §5.6)."""
    from app.matters.services import record_procedural_link

    matter = factories.MatterFactory(
        owner=specialist,
        track=Track.DOMESTIC.value,
        title="Eelnõu jõudis Riigikokku ja Vabariigi Valitsus kiitis heaks",
    )
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    record_procedural_link(
        matter=matter,
        kind="RIIGIKOGU",
        url="https://www.riigikogu.ee/tegevus/eelnoud/eelnou/123",
        title="Riigikogu menetluskäik",
        actor=specialist,
    )

    rail = _rail(matter, specialist)
    assert _states(rail)["riigikogu"] == STATE_POSSIBLE
    assert _states(rail)["valitsus"] == STATE_POSSIBLE


# ---------------------------------------------------------------------------
# What the rail refuses to place
# ---------------------------------------------------------------------------


def test_muu_is_not_forced_onto_a_node(specialist):
    matter = factories.MatterFactory(owner=specialist, track=Track.DOMESTIC.value)
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    change_stage(matter=matter, stage=_stage("other"), actor=specialist)

    rail = _rail(matter, specialist)
    assert STATE_CURRENT not in _states(rail).values()
    assert rail.unplaced_stage == "Muu"
    # And what the file *can* prove is still on the rail.
    assert _states(rail)["kooskolastus"] == STATE_RECORDED


def test_a_european_stage_on_a_domestic_rail_reads_beside_it(specialist):
    """The rail does not pretend a stage belongs to a node it has none for."""
    matter = factories.MatterFactory(owner=specialist, track=Track.DOMESTIC.value)
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    change_stage(matter=matter, stage=_stage("eu_procedure"), actor=specialist)

    rail = _rail(matter, specialist)
    assert rail.template == TEMPLATE_DOMESTIC
    assert STATE_CURRENT not in _states(rail).values()
    assert rail.unplaced_stage == "ELi menetluses"


def test_a_matter_with_no_track_no_instrument_and_no_stage_draws_nothing(specialist):
    matter = factories.MatterFactory(owner=specialist, track="")
    assert _rail(matter, specialist) is None


def test_a_rail_with_nothing_to_place_is_not_drawn(specialist):
    """A chosen template plus no stage and no history says nothing at all."""
    matter = factories.MatterFactory(owner=specialist, track=Track.DOMESTIC.value)
    assert matter.stage is None
    assert _rail(matter, specialist) is None


# ---------------------------------------------------------------------------
# Disposition is not a process stage
# ---------------------------------------------------------------------------


def test_koda_stopping_is_not_a_node_and_does_not_move_the_rail(specialist):
    """Scenario G. `Rohkem ei tegele` while the bill is still in the Riigikogu."""
    matter = factories.MatterFactory(owner=specialist, track=Track.DOMESTIC.value)
    change_stage(matter=matter, stage=_stage("parliament"), actor=specialist)
    close_matter(
        matter=matter,
        disposition=Disposition.MONITORING_STOPPED,
        reason="Koda ei tegele edasi.",
        actor=specialist,
    )

    rail = _rail(matter, specialist)
    assert _states(rail)["riigikogu"] == STATE_CURRENT
    assert _states(rail)["joustumine"] == STATE_POSSIBLE
    assert rail.koda_stopped is True
    assert KODA_STOPPED_LABEL == "Koda ei tegele edasi"
    # No node named after the disposition, and no terminal legal node invented.
    assert "Koda" not in " ".join(node.label for node in rail.nodes)


def test_joustunud_does_not_close_the_matter(specialist):
    """ADR 0032's separation, read from the rail's side."""
    matter = factories.MatterFactory(owner=specialist, track=Track.DOMESTIC.value)
    change_stage(matter=matter, stage=_stage("in_force"), actor=specialist)

    matter.refresh_from_db()
    assert matter.is_open is True
    assert matter.disposition == ""
    rail = _rail(matter, specialist)
    assert _states(rail)["joustumine"] == STATE_CURRENT
    assert rail.koda_stopped is False


def test_awaiting_entry_and_in_force_share_the_final_node(specialist):
    """Two sides of one point in the procedure, told apart by the state."""
    matter = factories.MatterFactory(owner=specialist, track=Track.DOMESTIC.value)
    change_stage(matter=matter, stage=_stage("awaiting_entry"), actor=specialist)
    assert _states(_rail(matter, specialist))["joustumine"] == STATE_CURRENT

    change_stage(matter=matter, stage=_stage("in_force"), actor=specialist)
    assert _states(_rail(matter, specialist))["joustumine"] == STATE_CURRENT


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


def test_the_rail_renders_on_the_matter_page_with_its_states_in_words(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist, track=Track.DOMESTIC.value)
    change_stage(matter=matter, stage=_stage("parliament"), actor=specialist)

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()
    rail = body[body.index('class="lprail"') : body.index('id="ajalugu-loend"')]

    assert "Menetluse kulg" in rail
    assert "Riigisisene menetlus" in rail
    # Every state is text on the page, not only a class.
    assert "Teadmata" in rail
    assert "Praegu" in rail
    assert "Võimalik" in rail
    assert 'aria-current="step"' in rail
    # And the history section is still below it, saying what actually happened.
    assert "Teema käik" in body


def test_the_rail_is_absent_rather_than_empty(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist, track="")
    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()

    assert "lprail" not in body
    assert "Menetluse kulg" not in body
