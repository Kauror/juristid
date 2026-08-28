"""Showing what the register said, without pretending it is structured work.

One rule, stated in one place because three surfaces need it: the register's
``JÄRGMISEKS`` text is displayed when — and only when — the Matter has no
structured ``NextAction``.

The ordering is not a presentation preference. A source instruction is a
sentence somebody typed into a spreadsheet cell in which the same words carry a
deadline, a review reminder and a guess about a ministry's timetable
interchangeably; that ambiguity is exactly why the importer refuses to convert
it (ADR 0011, ADR 0021). A ``NextAction`` is a decision with a kind, a date
semantic and a precision, made by a person here. Where both exist, the
structured one is the operational authority and showing the older Excel wording
beside it would invite somebody to act on whichever they read first.

Nothing in this module creates, infers or dates anything. It returns text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def source_instruction_for(matter: Any) -> str:
    """The register's ``JÄRGMISEKS`` for this Matter, or "".

    Empty whenever there is no derived register state, the snapshot recorded no
    instruction, or the text is blank. The caller shows it only where no
    structured action exists; this says nothing about *whether* to show it,
    only what there is.
    """
    state = getattr(matter, "current_register_state", None)
    if state is None:
        return ""
    return state.next_action_text.strip()


def source_instructions_for(matters: Any) -> dict[Any, str]:
    """The same, for a list surface, in one query rather than one per row.

    Used by the work lists, where reaching through ``matter.current_register_state``
    per row would be a join the page did not ask for — and, at the register's
    scale, one the reviewer of a hundred rows would notice.
    """
    from app.legacy_import.current_state import CurrentRegisterState

    identifiers = [getattr(matter, "pk", matter) for matter in matters]
    if not identifiers:
        return {}
    rows = CurrentRegisterState.objects.filter(matter_id__in=identifiers).values_list(
        "matter_id", "next_action_text"
    )
    return {matter_id: (text or "").strip() for matter_id, text in rows if (text or "").strip()}


def snapshot_label() -> str:
    """Which approved workbook the register text on screen came from.

    Every ``CurrentRegisterState`` row in one deployment is written by a single
    reconciliation against a single reviewed snapshot, so this is one label for
    the whole surface rather than one per Matter — and asking per row would be a
    join on every work list to render a constant.

    It exists because the text those rows carry is a *photograph* of a
    spreadsheet several people are still editing. An instruction reading "uuri
    21.08 ministeeriumilt" is not wrong, it is from the 21st; a lawyer who has
    since moved that date in Excel needs to see which of the two they are
    looking at, and the fix for that is a date on the label, not a quiet import
    of whatever the newest file happens to say.

    Returns "" when nothing is derived yet, or when the digest production holds
    is not one anybody approved — in which case saying nothing is better than
    naming a workbook the reviewed list has never heard of.
    """
    from app.legacy_import.current_state import CurrentRegisterState
    from app.legacy_import.final_cutover import reviewed_snapshot

    digest = (
        CurrentRegisterState.objects.exclude(source_snapshot_sha256="")
        .values_list("source_snapshot_sha256", flat=True)
        .first()
    )
    if not digest:
        return ""
    snapshot = reviewed_snapshot(digest)
    return snapshot.label if snapshot else ""


# ---------------------------------------------------------------------------
# What else the register observed about this Matter
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemberFeedback:
    """The two counts the register recorded, ready to render.

    A value class rather than a template reaching into the model, for the same
    reason everything else on the Matter page is decided in Python: ``None`` and
    ``0`` are different answers here, and Django templates cannot tell them
    apart. ``{{ state.member_feedback_responded|default:"—" }}`` renders a
    measured zero as an em dash, because ``0`` is falsy — which is precisely the
    conflation the whole feature exists to avoid (brief 10, 11).
    """

    #: How many members answered. ``None`` means the register did not record it.
    responded: int | None
    #: How many were asked directly. ``None`` means the same.
    requested: int | None

    @property
    def known(self) -> bool:
        return self.responded is not None or self.requested is not None

    @property
    def responded_label(self) -> str:
        return "teadmata" if self.responded is None else str(self.responded)

    @property
    def requested_label(self) -> str:
        return "teadmata" if self.requested is None else str(self.requested)


@dataclass(frozen=True)
class RegisterFacts:
    """What the latest reviewed snapshot says about one Matter, for display.

    Read once, in the view. Everything here is derived and everything here is
    labelled as the register's observation on screen — never as a Submission, a
    live analytics figure or a canonical relationship.
    """

    feedback: MemberFeedback
    #: ``DATE`` / ``NOT_SENT`` / ``RECORDED_OTHER`` / ``BLANK``.
    opinion_sent_state: str
    opinion_sent_date: object | None
    #: Every organisation ``KELLELE`` names. One canonical addressee is stored;
    #: this is the complete cell, so a three-ministry consultation reads as one.
    addressees: tuple[str, ...]
    has_multiple_addressees: bool
    legal_instrument: str
    #: ``YYYY_N`` when the register says the work continued elsewhere.
    continues_under_reference: str = ""
    #: The Matter that reference names, when this database holds it — so the
    #: reference can be followed instead of read. No canonical relationship is
    #: created: this is a lookup at render time, and a reference naming nothing
    #: stays plain text rather than becoming a broken link (brief 29).
    continues_under_id: object | None = None

    @property
    def opinion_not_sent(self) -> bool:
        return self.opinion_sent_state == "NOT_SENT"

    @property
    def has_anything(self) -> bool:
        return bool(
            self.feedback.known
            or self.opinion_not_sent
            or self.has_multiple_addressees
            or self.continues_under_reference
        )


def register_facts_for(matter: Any) -> RegisterFacts | None:
    """The derived register observations for one Matter, or ``None``.

    At most one extra query, and only for the continuation link — which is
    asked for at most once per page and only when the register actually named a
    successor.
    """
    state = getattr(matter, "current_register_state", None)
    if state is None:
        return None

    successor_id = None
    if state.continues_under_reference:
        from app.matters.models import Matter

        successor_id = (
            Matter.objects.filter(reference_number__isnull=False)
            .filter(reference_number=_reference_number(state.continues_under_reference))
            .values_list("pk", flat=True)
            .first()
        )

    return RegisterFacts(
        feedback=MemberFeedback(
            responded=state.member_feedback_responded,
            requested=state.member_feedback_requested,
        ),
        opinion_sent_state=state.opinion_sent_state,
        opinion_sent_date=state.opinion_sent_date,
        addressees=state.addressees,
        has_multiple_addressees=state.has_multiple_addressees,
        legal_instrument=state.legal_instrument_raw,
        continues_under_reference=state.continues_under_reference,
        continues_under_id=successor_id,
    )


def _reference_number(reference: str) -> int:
    """The numeric half of ``YYYY_N``, or ``-1`` when it is not one.

    ``-1`` rather than ``None`` so the caller's filter matches nothing instead
    of matching every Matter without a number, which is what a ``None`` would
    quietly do.
    """
    _, _, number = (reference or "").partition("_")
    return int(number) if number.isdigit() else -1
