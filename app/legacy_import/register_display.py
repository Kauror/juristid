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
