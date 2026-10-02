"""Which `Hetkeseis` ends a Matter, and which ones a lawyer is likely to move to next.

Keys only, and no database: every answer here is a function of stage keys and
`Õigusakt` keys, so it can be read by a form, a service and a test alike and
cannot ask a different question of the data than the caller already did
(docs/adr/0131).

Two things live here and they are different in kind.

**What ends a Matter is a rule.** «Jõustunud» and «Rohkem ei tegele» are the
two ordinary `Hetkeseis` values that close the file, each with the
`Disposition` the closure records (:data:`TERMINAL_DISPOSITIONS`).
«Jõustumise ootel» does not: an act waiting to come into force is still a file
somebody may write about.

**Which stage comes next is only an order.** `+ Märge → Uus hetkeseis` offers
the stages a lawyer is likely to pick first and the rest after them; nothing is
refused, hidden or disabled by it and the server still accepts every stage. Real
procedures move backwards, sideways and across from Brussels to Toompea, so the
list is short where the file's own history says where it is going and permissive
where it does not. No score, no model, no warning (docs/adr/0131 §7).

**Not on `Uus teema` and not on `Muuda teemat`.** `Uus teema` keeps docs/adr/0130's
soft guidance over every stage, and `Muuda teemat` is a correction surface that
offers the whole vocabulary; neither is a «next step» question.

**Never `Matter.track`.** The flow below is read from the stages the Matter has
actually held and, failing that, from the reviewed domestic/European `Õigusakt`
grouping. It is not written anywhere and it is not `Menetlusliik`
(docs/adr/0090 §4).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from app.taxonomy.legal_instruments import DOMESTIC_LEGAL_INSTRUMENT_KEYS, EU_LEGAL_INSTRUMENT_KEYS
from app.workflow.enums import Disposition

IN_FORCE = "in_force"
MONITORING_STOPPED = "monitoring_stopped"
AWAITING_TRANSPOSITION = "awaiting_transposition"

#: The `Hetkeseis` values that end a Matter, and the closure each one records.
#:
#: Mapped onto the existing closure vocabulary rather than beside it: the
#: closure is still `close_matter`, still a `Disposition`, still the
#: `MATTER_CLOSED` event every report reads (docs/adr/0131 §10).
TERMINAL_DISPOSITIONS: dict[str, str] = {
    IN_FORCE: Disposition.COMPLETED.value,
    MONITORING_STOPPED: Disposition.MONITORING_STOPPED.value,
}

TERMINAL_STAGE_KEYS: frozenset[str] = frozenset(TERMINAL_DISPOSITIONS)

FLOW_DOMESTIC = "domestic"
FLOW_EU = "eu"

#: Stages that place a file in one procedure or the other on their own.
DOMESTIC_STAGE_KEYS: tuple[str, ...] = ("idea", "consultation", "government", "parliament")
EU_STAGE_KEYS: tuple[str, ...] = ("estonian_eu_position", "eu_procedure", AWAITING_TRANSPOSITION)

#: Each procedure's ordinary road, first stage first. `awaiting_entry` belongs
#: to both — an Estonian law and an EU act both wait to come into force.
FLOW_CHAINS: dict[str, tuple[str, ...]] = {
    FLOW_DOMESTIC: (*DOMESTIC_STAGE_KEYS, "awaiting_entry"),
    FLOW_EU: ("estonian_eu_position", "eu_procedure", "awaiting_entry", AWAITING_TRANSPOSITION),
}

#: Where a directive goes once Estonia has to transpose it: back to the start of
#: the domestic road, inside the same Matter (docs/adr/0131 §8).
EU_TO_DOMESTIC_BRIDGE: tuple[str, ...] = ("idea", "consultation")

#: What every procedure can end in or step aside to, after its forward road.
COMMON_TAIL: tuple[str, ...] = (IN_FORCE, "other", MONITORING_STOPPED)


def is_terminal(stage_key: str | None) -> bool:
    """Whether choosing this stage ends the Matter."""
    return bool(stage_key) and stage_key in TERMINAL_STAGE_KEYS


def stage_flow(stage_key: str | None) -> str | None:
    """The procedure a stage places a file in by itself, or ``None`` for a common one."""
    if stage_key in DOMESTIC_STAGE_KEYS:
        return FLOW_DOMESTIC
    if stage_key in EU_STAGE_KEYS:
        return FLOW_EU
    return None


def flow_context(
    *,
    current_key: str | None,
    history_keys: Sequence[str],
    instrument_keys: Iterable[str],
) -> str | None:
    """Which procedure this file is moving through now, or ``None`` when nothing says.

    In order, stopping at the first answer:

    1. the current stage, when it belongs to one procedure;
    2. the most recent earlier period whose stage did — so a file at
       «Jõustumise ootel» after «Riigikogus» is still domestic, and one that
       crossed from «ELi õiguse ülevõtmise ootel» to «Idee» is domestic from
       then on;
    3. the ticked `Õigusakt`, when every one of them is domestic or every one
       European;
    4. otherwise ``None``, and the caller stays permissive.

    ``history_keys`` is newest first and may include the current stage.
    """
    for key in (current_key, *history_keys):
        flow = stage_flow(key)
        if flow is not None:
            return flow
    instruments = {key for key in instrument_keys if key}
    if instruments and instruments <= DOMESTIC_LEGAL_INSTRUMENT_KEYS:
        return FLOW_DOMESTIC
    if instruments and instruments <= EU_LEGAL_INSTRUMENT_KEYS:
        return FLOW_EU
    return None


def _ordered_unique(keys: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for key in keys:
        if key and key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered


def next_stage_keys(
    *,
    current_key: str | None,
    history_keys: Sequence[str],
    instrument_keys: Iterable[str],
    available_keys: Sequence[str],
) -> list[str]:
    """The stages `Uus hetkeseis` offers, most likely first (docs/adr/0131 §7).

    ``available_keys`` is the active vocabulary in its reviewed order; nothing
    outside it is ever offered, and nothing inside it the file has held before
    is ever left out.

    Within a known procedure the order is: the road ahead of the current stage;
    then «Jõustunud», «Muu» and «Rohkem ei tegele»; then the road behind. From
    «ELi õiguse ülevõtmise ootel» the road ahead begins with «Idee» and
    «Kooskõlastusringil» — the domestic half of the same Matter. Last come any
    stages this Matter has held before that the procedure would not offer, so a
    genuine correction back across the bridge is never impossible.

    Without a known procedure every stage is offered, in the vocabulary's order.
    The current stage is never offered as a new one, and `Määramata` — no
    stage — is not a period and is never in the list.
    """
    flow = flow_context(
        current_key=current_key, history_keys=history_keys, instrument_keys=instrument_keys
    )
    if flow is None:
        ordered = list(available_keys)
    else:
        chain = FLOW_CHAINS[flow]
        if current_key in chain:
            at = chain.index(current_key)
            forward: tuple[str, ...] = chain[at + 1 :]
            backward: tuple[str, ...] = chain[:at]
        else:
            forward, backward = chain, ()
        if current_key == AWAITING_TRANSPOSITION:
            forward = (*EU_TO_DOMESTIC_BRIDGE, *forward)
        ordered = [*forward, *COMMON_TAIL, *backward, *history_keys]
    available = set(available_keys)
    return [key for key in _ordered_unique(ordered) if key in available and key != current_key]


def reopening_stage_keys(
    *,
    current_key: str | None,
    history_keys: Sequence[str],
    instrument_keys: Iterable[str],
    available_keys: Sequence[str],
) -> list[str]:
    """The stages a closed Matter may be reopened into (docs/adr/0131 §12).

    Every stage of the file's procedure from its beginning, then «Muu», then any
    other stage the file has held — and never one that ends a Matter: reopening
    into «Jõustunud» would close it again in the same press. A reopening always
    names a real stage, so `Määramata` is not offered either.
    """
    flow = flow_context(
        current_key=current_key, history_keys=history_keys, instrument_keys=instrument_keys
    )
    if flow is None:
        ordered = list(available_keys)
    else:
        chain = FLOW_CHAINS[flow]
        bridge = EU_TO_DOMESTIC_BRIDGE if flow == FLOW_EU else ()
        ordered = [*chain, *bridge, "other", *history_keys]
    available = set(available_keys)
    return [
        key
        for key in _ordered_unique(ordered)
        if key in available and key not in TERMINAL_STAGE_KEYS
    ]
