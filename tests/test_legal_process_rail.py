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
    legal_process_rail,
    recorded_stage_keys,
)
from app.matters.process_phases import (
    PATTERN_DIRECTIVE,
    PATTERN_DOMESTIC,
    PATTERN_EU,
    pattern_for,
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


def _pattern_key(*, track: str, instrument_keys: frozenset[str]) -> str:
    """Which pattern a file is read against, as a key, or ``""`` for none.

    The projection returns the pattern itself now that there are eight of them
    rather than two templates; these tests are about *which* one is chosen, so
    they keep asking in keys.
    """
    pattern = pattern_for(track=track, instrument_keys=instrument_keys)
    return pattern.key if pattern is not None else ""


def _rail(matter, user):
    return legal_process_rail(matter=matter, user=user)


def _states(rail) -> dict[str, str]:
    return {node.key: node.state for node in rail.nodes}


def _stage_labels(rail) -> dict[str, str]:
    """The explicit `Hetkeseis` each node carries.

    Empty on every node but the current one, which is the whole of
    `ProcessNode.stage_label`'s contract.
    """
    return {node.key: node.stage_label for node in rail.nodes}


# ---------------------------------------------------------------------------
# Choosing a rail
# ---------------------------------------------------------------------------


def test_the_track_chooses_the_rail_where_its_semantics_safely_can():
    assert _pattern_key(track=Track.DOMESTIC.value, instrument_keys=frozenset()) == (
        PATTERN_DOMESTIC
    )
    assert _pattern_key(track=Track.EU_INITIATIVE.value, instrument_keys=frozenset()) == (
        PATTERN_EU
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
    assert _pattern_key(track=track, instrument_keys=frozenset()) == ""
    # The *instrument* answers, and since the patterns became instrument-aware it
    # answers at its own grain: `Seadus` is the domestic bill and `Direktiiv` is
    # the directive, not «something domestic» and «something European». What this
    # test protects is that the track contributed nothing to either answer.
    assert _pattern_key(track=track, instrument_keys=frozenset({"seadus"})) == PATTERN_DOMESTIC
    assert _pattern_key(track=track, instrument_keys=frozenset({"direktiiv"})) == (
        PATTERN_DIRECTIVE
    )


def test_a_mixed_instrument_file_draws_no_rail():
    """`S, M` and `direktiiv ja määrus` are both real register values.

    A Matter carrying one domestic and one European instrument has no reading
    that picks a rail, so it gets neither and `Hetkeseis` goes on answering.
    """
    assert _pattern_key(track="", instrument_keys=frozenset({"seadus", "direktiiv"})) == ""


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
    assert rail.pattern.key == PATTERN_DIRECTIVE
    matter.refresh_from_db()
    assert matter.track == ""


def test_a_seadus_does_not_become_a_transposition(specialist):
    """`Õigusakt` chooses a presentation and asserts nothing about the track."""
    matter = factories.MatterFactory(owner=specialist, track="")
    matter.legal_instruments.set([_instrument("seadus")])
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)

    assert _rail(matter, specialist).pattern.key == PATTERN_DOMESTIC
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
    assert rail.pattern.key == PATTERN_DOMESTIC
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
    assert rail.pattern.key == PATTERN_EU
    assert _states(rail) == {
        "eli-konsultatsioon": STATE_UNKNOWN,
        "eesti-seisukoht": STATE_CURRENT,
        "eli-menetlus": STATE_POSSIBLE,
        "joustumine": STATE_POSSIBLE,
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
    matter.stage = _stage("parliament")
    matter.save(update_fields=["stage"])
    # Exactly the row a pre-docs/adr/0092 `change_stage` wrote: the labels and
    # no keys. Written as a new row rather than by rewriting one — the audit
    # table is append-only and the database refuses an UPDATE, which is the
    # property that makes the history worth reading in the first place.
    ChangeEvent.objects.create(
        matter=matter,
        event_type=ChangeEventType.MATTER_STAGE_CHANGED,
        summary="Kooskõlastusringil",
        payload={"from_label": None, "to_label": "Kooskõlastusringil"},
    )

    assert "consultation" in recorded_stage_keys(matter=matter, user=specialist)
    assert _states(_rail(matter, specialist))["kooskolastus"] == STATE_RECORDED


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
        label="Riigikogu menetluskäik",
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
    assert rail.pattern.key == PATTERN_DOMESTIC
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
    """Two sides of one point in the procedure, and one node for both."""
    matter = factories.MatterFactory(owner=specialist, track=Track.DOMESTIC.value)
    change_stage(matter=matter, stage=_stage("awaiting_entry"), actor=specialist)
    assert _states(_rail(matter, specialist))["joustumine"] == STATE_CURRENT

    change_stage(matter=matter, stage=_stage("in_force"), actor=specialist)
    assert _states(_rail(matter, specialist))["joustumine"] == STATE_CURRENT
    # And no sixth node was invented to hold the second of them.
    assert [node.key for node in _rail(matter, specialist).nodes] == [
        "algus",
        "kooskolastus",
        "valitsus",
        "riigikogu",
        "joustumine",
    ]


# ---------------------------------------------------------------------------
# What the current node says the stage actually is
# ---------------------------------------------------------------------------


def test_the_current_node_names_the_stage_the_file_actually_holds(specialist):
    """The node is broad on purpose; the label says which side of it this is.

    `Jõustumine · Praegu` is the same three words for a file waiting for the act
    to come into force and for one already in force, and those are not the same
    answer to «where is this». The explicit `Hetkeseis` rides on the current node
    (docs/adr/0092 §13, amended).
    """
    matter = factories.MatterFactory(owner=specialist, track=Track.DOMESTIC.value)
    change_stage(matter=matter, stage=_stage("awaiting_entry"), actor=specialist)
    waiting = _stage_labels(_rail(matter, specialist))

    change_stage(matter=matter, stage=_stage("in_force"), actor=specialist)
    in_force = _stage_labels(_rail(matter, specialist))

    assert waiting["joustumine"] == "Jõustumise ootel"
    assert in_force["joustumine"] == "Jõustunud"
    assert waiting["joustumine"] != in_force["joustumine"]


def test_the_three_european_end_stages_are_not_rendered_identically(specialist):
    """The strongest case: `Ülevõtmine / jõustumine` holds three stages."""
    matter = factories.MatterFactory(owner=specialist, track=Track.EU_INITIATIVE.value)
    seen = []
    for key, node in (
        ("awaiting_transposition", "ulevotmine"),
        ("awaiting_entry", "joustumine"),
        ("in_force", "joustumine"),
    ):
        change_stage(matter=matter, stage=_stage(key), actor=specialist)
        rail = _rail(matter, specialist)
        assert _states(rail)[node] == STATE_CURRENT
        seen.append(_stage_labels(rail)[node])

    assert seen == ["ELi õiguse ülevõtmise ootel", "Jõustumise ootel", "Jõustunud"]
    assert len(set(seen)) == 3


def test_no_stage_vocabulary_value_is_invented_for_the_rail(specialist):
    """Whatever a node prints is a reviewed row's own `label_et`."""
    reviewed = set(StageVocabulary.objects.values_list("label_et", flat=True))
    matter = factories.MatterFactory(owner=specialist, track=Track.DOMESTIC.value)
    for key in ("consultation", "government", "parliament", "awaiting_entry", "in_force"):
        change_stage(matter=matter, stage=_stage(key), actor=specialist)
        printed = {label for label in _stage_labels(_rail(matter, specialist)).values() if label}
        assert printed <= reviewed


def test_a_node_whose_words_are_the_stages_own_words_does_not_say_them_twice(specialist):
    """`Kooskõlastus · Praegu · Kooskõlastusringil` would state one thing twice.

    The node's own label already answers it, so the stage label is carried only
    where it adds something.
    """
    matter = factories.MatterFactory(owner=specialist, track=Track.DOMESTIC.value)

    # `Valitsuses` the phase and `Valitsuses` the stage are now the same word —
    # the phase vocabulary is the lawyers' own — so nothing is carried at all.
    change_stage(matter=matter, stage=_stage("government"), actor=specialist)
    assert _stage_labels(_rail(matter, specialist))["valitsus"] == ""

    # `Jõustumine` the phase, `Jõustunud` the stage — different words, so it reads.
    change_stage(matter=matter, stage=_stage("in_force"), actor=specialist)
    rail = _rail(matter, specialist)
    assert _stage_labels(rail)["joustumine"] == "Jõustunud"
    # And it rides on the current node and on no other.
    assert [node.key for node in rail.nodes if node.stage_label] == ["joustumine"]


def test_an_unplaceable_stage_puts_no_label_on_any_node(specialist):
    """`Muu` reads beside the rail, and does not attach itself to a node."""
    matter = factories.MatterFactory(owner=specialist, track=Track.DOMESTIC.value)
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    matter.stage = _stage("other")
    matter.save(update_fields=["stage"])

    rail = _rail(matter, specialist)
    assert rail.unplaced_stage == "Muu"
    assert [node.stage_label for node in rail.nodes] == ["", "", "", "", ""]


# ---------------------------------------------------------------------------
# `Kirjas` describes the path to here, not evidence anywhere
# ---------------------------------------------------------------------------


def test_a_stage_recorded_ahead_of_the_current_one_is_not_kirjas(specialist):
    """A correction must not leave the rail asserting two places at once.

    Somebody picked `Jõustunud` by mistake and moved the file back to the
    consultation round. Read literally, the audit history says `joustumine` was
    reached — and the rail then read `Kooskõlastus · Praegu` and
    `Jõustumine · Kirjas` on one line, which tells a reader the act is both in
    force and out for consultation (docs/adr/0092 §13, amended).
    """
    matter = factories.MatterFactory(owner=specialist, track=Track.DOMESTIC.value)
    change_stage(matter=matter, stage=_stage("in_force"), actor=specialist)
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)

    assert _states(_rail(matter, specialist)) == {
        "algus": STATE_UNKNOWN,
        "kooskolastus": STATE_CURRENT,
        "valitsus": STATE_POSSIBLE,
        "riigikogu": STATE_POSSIBLE,
        "joustumine": STATE_POSSIBLE,
    }


def test_the_audit_history_still_holds_the_stage_that_was_corrected(specialist):
    """A projection rule, and not a rewriting of what was recorded."""
    matter = factories.MatterFactory(owner=specialist, track=Track.DOMESTIC.value)
    change_stage(matter=matter, stage=_stage("in_force"), actor=specialist)
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)

    assert "in_force" in recorded_stage_keys(matter=matter, user=specialist)


def test_a_recorded_stage_before_the_current_one_is_still_kirjas(specialist):
    """The ordinary case is untouched: evidence on the way here still reads."""
    matter = factories.MatterFactory(owner=specialist, track=Track.DOMESTIC.value)
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    change_stage(matter=matter, stage=_stage("parliament"), actor=specialist)

    assert _states(_rail(matter, specialist))["kooskolastus"] == STATE_RECORDED


def test_the_late_entry_rule_is_unchanged_by_the_recorded_rule(specialist):
    """A file with no recorded history still reads `Teadmata` to its left."""
    matter = factories.ArchiveMatterFactory(owner=specialist, track=Track.DOMESTIC.value)
    matter.stage = _stage("parliament")
    matter.save(update_fields=["stage"])

    assert _states(_rail(matter, specialist)) == {
        "algus": STATE_UNKNOWN,
        "kooskolastus": STATE_UNKNOWN,
        "valitsus": STATE_UNKNOWN,
        "riigikogu": STATE_CURRENT,
        "joustumine": STATE_POSSIBLE,
    }


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


def test_the_current_nodes_stage_label_reaches_the_page(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist, track=Track.DOMESTIC.value)
    change_stage(matter=matter, stage=_stage("awaiting_entry"), actor=specialist)

    url = reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    waiting = signed_in.get(url).content.decode()
    assert "Jõustumise ootel" in waiting[waiting.index('class="lprail"') :][:2000]

    change_stage(matter=matter, stage=_stage("in_force"), actor=specialist)
    in_force = signed_in.get(url).content.decode()
    rail = in_force[in_force.index('class="lprail"') :][:2000]
    assert "Jõustunud" in rail
    assert "Jõustumise ootel" not in rail


# ---------------------------------------------------------------------------
# `Menetluse kulg` is a sibling of `Teema käik`, not a block inside it
# ---------------------------------------------------------------------------
#
# Three sections answer three questions: `Menetluse tähtajad` the dated points,
# `Teema käik` what happened, `Menetluse kulg` where the procedure stands.
# Nesting the third inside the second made collapsing the history — the ordinary
# thing to do when it runs to six months — take the answer to the other question
# with it (docs/adr/0092 §2, amended).


def _detail(signed_in, matter) -> str:
    return signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()


def test_the_rail_is_not_inside_the_teema_kaik_disclosure(signed_in, specialist):
    """The structural assertion: `.lprail` is not a descendant of `#ajajoon`.

    `#ajajoon` is a `<details>` and it is the last of the two, so «outside» is
    «before its opening tag» — which is also the reading order the lawyers use:
    where is this, then how did it get there.
    """
    matter = factories.MatterFactory(owner=specialist, track=Track.DOMESTIC.value)
    change_stage(matter=matter, stage=_stage("parliament"), actor=specialist)
    body = _detail(signed_in, matter)

    assert body.index('class="lprail"') < body.index('id="ajajoon"')
    inside = body[body.index('id="ajajoon"') :]
    assert "lprail" not in inside


def test_the_dated_strip_reads_inside_menetluse_kulg(signed_in, specialist):
    """The dated points answer «where is this going», so they moved to the rail.

    They rendered at the head of `Teema käik` until the road ahead arrived, which
    left three diagram-shaped things stacked above the working area. The strip is
    unchanged — same columns, same sources, same states — under
    `Kirjas olevad kuupäevad`, and it is no longer inside the history disclosure
    (§4 of the brief, docs/adr/0074 §12).
    """
    matter = factories.MatterFactory(owner=specialist, track=Track.DOMESTIC.value)
    change_stage(matter=matter, stage=_stage("parliament"), actor=specialist)
    body = _detail(signed_in, matter)

    assert "Kirjas olevad kuupäevad" in body
    assert body.index('aria-label="Menetluse tähtajad"') < body.index('id="ajajoon"')
    inside = body[body.index('id="ajajoon"') :]
    assert 'aria-label="Menetluse tähtajad"' not in inside


def test_the_rail_heading_is_a_sibling_heading_and_not_a_sub_heading(signed_in, specialist):
    """A section of the page carries the page's section level.

    It was an `h3` under `Teema käik`'s `h2`, which told a screen reader that
    «where is the procedure» is part of «what happened». Both are `h2` now, and
    the level below the last `h2` before it is not skipped.
    """
    matter = factories.MatterFactory(owner=specialist, track=Track.DOMESTIC.value)
    change_stage(matter=matter, stage=_stage("parliament"), actor=specialist)
    body = _detail(signed_in, matter)

    head = body[body.index('id="menetluse-kulg-heading"') - 60 :]
    assert head.startswith("<h2") or "<h2" in head[:60]
    assert '<h3 class="lprail__head"' not in body
    assert 'aria-labelledby="menetluse-kulg-heading"' in body


def test_the_anchor_and_the_filter_query_are_unchanged(signed_in, specialist):
    """Links people have already sent still work."""
    matter = factories.MatterFactory(owner=specialist, track=Track.DOMESTIC.value)
    change_stage(matter=matter, stage=_stage("parliament"), actor=specialist)

    body = _detail(signed_in, matter)
    assert 'id="ajajoon"' in body

    filtered = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk}), {"ajajoon": "sissekanded"}
    )
    assert filtered.status_code == 200
    assert 'class="lprail"' in filtered.content.decode()


def test_the_rail_is_absent_rather_than_empty(signed_in, specialist):
    """No pattern, no rail and no road ahead — and the recorded dates still read.

    The three blocks of `Menetluse kulg` are independent. A file with no
    `Õigusakt` and no `Menetlusliik` is read against no procedure, so it draws no
    nodes and is promised no next steps: six «Teadmata» nodes is a heading spent
    announcing that the application knows nothing.

    Its `Alustatud` is not a claim about a procedure, though — it is a date the
    file recorded — and it goes on reading. Withdrawing it because the file is
    unclassified would lose recorded information in what is otherwise a layout
    change.
    """
    matter = factories.MatterFactory(owner=specialist, track="")
    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()

    assert "lprail__node" not in body
    assert "lprail__now" not in body
    assert "Ees võib olla" not in body
    assert "Alustatud" in body
