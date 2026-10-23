"""MCP writes on a Teema's dates: the opinion deadline and an important date.

`response_deadline` says which of two different facts the user means and calls
the one service that records it (app/matters/response_deadlines.py):

* ``new_request`` — Koda is asked for an opinion again: a new request with its
  own request time (`request_response_deadline`), or — when a request is still
  current — a new request that replaces it, ending the old one with the outcome
  the user states (`change_response_deadline(change=REPLACED)`). Never silently:
  without that outcome the change is refused as missing information.
* ``move`` — the same request, a new date (`change_response_deadline(change=MOVED)`);
  the old date stays in the history as moved.

There is no ``correct`` mode: the service has no way to rewrite a recorded
deadline as if it never was — a wrong date is moved (history kept) or ended in
Juristid's header. `add_important_date` is `intelligence.services.add_important_date`
with its own precision validation and its `kind`, so an estimated commencement is
stored as an estimate and never as a commencement.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any, ClassVar, Literal

from django.utils import timezone
from pydantic import BaseModel, Field, model_validator

from app.mcp_writes.operations import common
from app.mcp_writes.operations.base import (
    Change,
    Executed,
    OperationRefused,
    Prepared,
    Preview,
)

PREVIOUS_OUTCOMES = {
    "answered": "ANSWERED",
    "not_answering": "NOT_ANSWERING",
    "superseded": "SUPERSEDED",
}
PREVIOUS_OUTCOME_LABELS = {
    "ANSWERED": "vastatud",
    "NOT_ANSWERING": "otsustati mitte vastata",
    "SUPERSEDED": "jääb vastamata (uus küsimine asendab)",
}


def _day(value: Any) -> str:
    return common.at_precision(value, "EXACT")


def _deadline_state(user: Any, matter: Any) -> dict[str, Any]:
    from app.matters.models import MatterResponseDeadline
    from app.matters.response_deadlines import deadline_revision
    from app.matters.work_items import response_obligation_of

    last = (
        MatterResponseDeadline.objects.filter(matter_id=matter.pk)
        .order_by("-ended_at", "-created_at", "pk")
        .values_list("pk", flat=True)
        .first()
    )
    return {
        **common.matter_core_state(user, matter),
        "track": matter.track,
        "response_deadline": common.iso(matter.response_deadline),
        "response_requested_at": common.iso(matter.response_requested_at),
        "deadline_revision": deadline_revision(matter),
        "last_history_row": str(last) if last else None,
        # An old-style deadline discharged between prepare and commit (a sent opinion,
        # the register's VÄLJA) changes what a new request or a move means.
        "outstanding": bool(
            matter.response_deadline is not None
            and response_obligation_of(matter, user).is_outstanding
        ),
    }


# ---------------------------------------------------------------------------
# response_deadline
# ---------------------------------------------------------------------------


class ResponseDeadlineArguments(common.Arguments):
    matter: str = Field(
        max_length=common.MATTER_ARGUMENT_MAX, description=common.MATTER_ARGUMENT_DESCRIPTION
    )
    mode: Literal["new_request", "move"] = Field(
        description=(
            "'new_request': Koda has been asked (again) for an opinion — a new request or "
            "consultation round with this deadline; an opinion sent earlier does not answer it. "
            "'move': the SAME request's deadline changed (extended or brought forward); the old "
            "date stays in history as moved."
        )
    )
    deadline: str = Field(
        max_length=10, description="The opinion deadline (Arvamuse tähtaeg), YYYY-MM-DD."
    )
    current_request_outcome: Literal["answered", "not_answering", "superseded"] | None = Field(
        default=None,
        description=(
            "Only for 'new_request' while an earlier request is still current, and then "
            "required: what happened to that earlier request — 'answered', 'not_answering' "
            "(decided not to answer) or 'superseded' (left unanswered, replaced by this one)."
        ),
    )
    note: str | None = Field(
        default=None,
        max_length=common.MAX_TEXT,
        description=(
            "With current_request_outcome 'answered' and required then: how and where the "
            "earlier request was answered. With 'move': optional reason the date moved."
        ),
    )

    @model_validator(mode="after")
    def _consistent(self) -> ResponseDeadlineArguments:
        if self.mode == "move" and self.current_request_outcome is not None:
            raise ValueError(
                "'move' keeps the same request; current_request_outcome is for 'new_request'."
            )
        return self


class ResponseDeadline:
    name: ClassVar[str] = "response_deadline"
    label: ClassVar[str] = "Arvamuse tähtaeg"
    arguments: ClassVar[type[BaseModel]] = ResponseDeadlineArguments
    description: ClassVar[str] = (
        "Record Koda's opinion deadline (Arvamuse tähtaeg) on a Teema: either a NEW request for an "
        "opinion ('new_request') or the SAME request's deadline moved ('move'). Ask the user which "
        "it is when unclear. Every earlier deadline stays in history; nothing is discharged or "
        "opened silently. It does NOT mark an opinion sent, does not clear or end a deadline, does "
        "not set a next action and is not a WAIT review date."
    )

    def prepare(self, *, user: Any, arguments: Any) -> Prepared:
        from app.matters import date_order
        from app.matters.enums import ResponseDeadlineOutcome
        from app.matters.initiative import INITIATIVE_HAS_NO_NEW_REQUEST, is_koda_initiative
        from app.matters.response_deadlines import (
            ACTIVE_DEADLINE_EXISTS,
            ANSWER_NEEDS_A_BASIS,
            LEGACY_DISCHARGE_NOTE,
            NO_CURRENT_DEADLINE,
            deadline_revision,
        )
        from app.matters.work_items import response_obligation_of

        args = common.coerce(ResponseDeadlineArguments, arguments)
        common.require_writer(user)
        matter = common.resolve_matter(user, args.matter)
        common.require_writable_matter(matter)
        deadline = common.parse_day(args.deadline, "deadline")
        current = matter.response_deadline
        note = (args.note or "").strip()
        canonical: dict[str, Any] = {
            "matter_id": str(matter.pk),
            "mode": args.mode,
            "deadline": deadline.isoformat(),
            "expected_revision": deadline_revision(matter),
        }
        consequences: list[str] = []
        unchanged = [
            "Koja arvamust ei märgita saadetuks.",
            "Järgmine tegevus ja hetkeseis ei muutu.",
            "Teemat ei suleta.",
            "Varasemad tähtajad jäävad ajalukku.",
        ]

        if args.mode == "new_request":
            if is_koda_initiative(matter):
                raise OperationRefused("domain_rule", INITIATIVE_HAS_NO_NEW_REQUEST)
            if current is None:
                if args.current_request_outcome is not None:
                    raise OperationRefused(
                        "invalid_argument",
                        "The Teema has no current opinion deadline; "
                        "current_request_outcome does not apply.",
                    )
                canonical["service"] = "request"
                consequences.append(
                    "Avatakse uus arvamuse küsimine; enne seda saadetud arvamus seda ei vasta."
                )
            else:
                legacy_settled = (
                    matter.response_requested_at is None
                    and not response_obligation_of(matter, user).is_outstanding
                )
                if legacy_settled:
                    if args.current_request_outcome is not None:
                        raise OperationRefused(
                            "invalid_argument",
                            "The earlier deadline already reads as answered under the older "
                            "rules; current_request_outcome does not apply.",
                        )
                    canonical["service"] = "request"
                    consequences.append(
                        f"Varasem tähtaeg {_day(current)} kantakse ajalukku vastatuna märkusega "
                        f"«{LEGACY_DISCHARGE_NOTE}» — see oli juba varasema arvestuse "
                        "järgi lõpetatud."
                    )
                    consequences.append(
                        "Avatakse uus arvamuse küsimine; enne seda saadetud arvamus seda ei vasta."
                    )
                else:
                    if args.current_request_outcome is None:
                        raise OperationRefused(
                            "missing_information",
                            ACTIVE_DEADLINE_EXISTS.format(date=_day(current))
                            + " Say what happened to that request (current_request_outcome: "
                            "answered + note, not_answering, or superseded), or use mode 'move' "
                            "if it is the same request with a new date.",
                        )
                    outcome = PREVIOUS_OUTCOMES[args.current_request_outcome]
                    if outcome == "ANSWERED" and not note:
                        raise OperationRefused("missing_information", ANSWER_NEEDS_A_BASIS)
                    if deadline == current:
                        raise OperationRefused(
                            "domain_rule",
                            "The new request's deadline equals the current one; Juristid cannot "
                            "record that as a new request. Check the date.",
                        )
                    canonical.update(
                        {"service": "replace", "previous_outcome": outcome, "previous_note": note}
                    )
                    consequences.append(
                        f"Praegune küsimine (tähtaeg {_day(current)}) lõpetatakse ajaloos: "
                        f"{PREVIOUS_OUTCOME_LABELS[outcome]}."
                    )
                    consequences.append(
                        "Avatakse uus arvamuse küsimine; enne seda saadetud arvamus seda ei vasta."
                    )
        else:
            if current is None:
                raise OperationRefused("domain_rule", NO_CURRENT_DEADLINE)
            if deadline == current:
                raise OperationRefused(
                    "invalid_argument", "That is already the deadline; nothing would change."
                )
            canonical.update({"service": "move", "previous_note": note})
            direction = "lükatakse edasi" if deadline > current else "tuuakse ettepoole"
            consequences.append(
                f"Sama küsimise tähtaeg {direction}: {_day(current)} → {_day(deadline)}. Endine "
                f"tähtaeg jääb ajalukku märkega «{ResponseDeadlineOutcome.MOVED.label}»."
            )
            unchanged.append("Uut arvamuse küsimist ei avata.")

        warnings = []
        if (
            args.mode == "move"
            and matter.response_requested_at is None
            and not response_obligation_of(matter, user).is_outstanding
        ):
            warnings.append(
                "Praegune tähtaeg on vanema arvestuse järgi juba vastatud (saadetud arvamus või "
                "registri VÄLJA). Kui Kojalt küsiti arvamust uuesti, on see uus küsimine "
                "(mode 'new_request'), mitte tähtaja muutmine."
            )
        if date_order.deadline_precedes_received(received=matter.received_date, deadline=deadline):
            warnings.append(date_order.DEADLINE_BEFORE_RECEIVED)
        if deadline < timezone.localdate():
            warnings.append("Tähtaeg on minevikus.")
        changes = [
            Change(
                "response_deadline",
                "Arvamuse tähtaeg",
                _day(current) if current is not None else None,
                _day(deadline),
            ),
            Change(
                "meaning",
                "Tähendus",
                None,
                "Uus arvamuse küsimine"
                if args.mode == "new_request"
                else "Sama küsimise tähtaeg muutus",
            ),
        ]
        if note:
            changes.append(Change("note", "Märkus", None, note))
        preview = Preview(
            summary=(
                f"Uus arvamuse küsimine teemal {matter.display_reference}, tähtaeg {_day(deadline)}"
                if args.mode == "new_request"
                else f"Muuda teema {matter.display_reference} arvamuse tähtaeg: {_day(deadline)}"
            ),
            target=common.matter_target(matter),
            changes=changes,
            consequences=consequences,
            unchanged=unchanged,
            warnings=warnings,
        )
        return Prepared(
            canonical=canonical,
            state=self.state(user=user, canonical=canonical),
            preview=preview,
            target_matter_id=matter.pk,
        )

    def lock(self, *, canonical: dict[str, Any]) -> None:
        common.lock_matter(canonical["matter_id"])

    def state(self, *, user: Any, canonical: dict[str, Any]) -> dict[str, Any]:
        return _deadline_state(user, common.state_matter(canonical["matter_id"]))

    def execute(self, *, user: Any, canonical: dict[str, Any], once_token: uuid.UUID) -> Executed:
        from app.matters.enums import ResponseDeadlineChange
        from app.matters.locks import lock_open_matter_for_business_write
        from app.matters.response_deadlines import (
            change_response_deadline,
            request_response_deadline,
        )

        common.require_writer(user)
        common.visible_matter_by_id(user, canonical["matter_id"])
        deadline = date.fromisoformat(canonical["deadline"])
        revision = canonical["expected_revision"]
        with common.domain_refusals():
            matter = lock_open_matter_for_business_write(canonical["matter_id"])
            common.require_writable_matter(matter)
            if canonical["service"] == "request":
                request_response_deadline(
                    matter=matter, deadline=deadline, actor=user, expected_revision=revision
                )
            elif canonical["service"] == "replace":
                change_response_deadline(
                    matter=matter,
                    deadline=deadline,
                    actor=user,
                    change=ResponseDeadlineChange.REPLACED,
                    previous_outcome=canonical["previous_outcome"],
                    previous_note=canonical.get("previous_note") or "",
                    expected_revision=revision,
                )
            else:
                change_response_deadline(
                    matter=matter,
                    deadline=deadline,
                    actor=user,
                    change=ResponseDeadlineChange.MOVED,
                    previous_note=canonical.get("previous_note") or "",
                    expected_revision=revision,
                )
        matter.refresh_from_db()
        return Executed(
            record_type="matter",
            record_id=str(matter.pk),
            matter_id=str(matter.pk),
            matter_reference=matter.display_reference,
            url=common.matter_url(matter.pk),
            saved={
                "response_deadline": common.iso(matter.response_deadline),
                "requested_at": common.iso(matter.response_requested_at),
                "mode": canonical["mode"],
            },
        )


# ---------------------------------------------------------------------------
# add_important_date
# ---------------------------------------------------------------------------

IMPORTANT_DATE_KINDS = {
    "other": "OTHER",
    "transposition_deadline": "TRANSPOSITION_DEADLINE",
    "planned_commencement": "PLANNED_COMMENCEMENT",
    "estimated_commencement": "ESTIMATED_COMMENCEMENT",
}

IMPORTANT_DATE_TITLE_MAX = 2000


class AddImportantDateArguments(common.Arguments):
    matter: str = Field(
        max_length=common.MATTER_ARGUMENT_MAX, description=common.MATTER_ARGUMENT_DESCRIPTION
    )
    title: str = Field(
        min_length=1,
        max_length=IMPORTANT_DATE_TITLE_MAX,
        description="What is expected, e.g. «eelnõu kooskõlastusringi lõpp».",
    )
    date: str = Field(
        max_length=10,
        description=(
            "When, written at its precision: YYYY-MM-DD (exact), YYYY-MM (month), YYYY-Qn "
            "(quarter) or YYYY (year). A vague date stays vague — never pick a day."
        ),
    )
    date_precision: Literal["exact", "month", "quarter", "year"] = Field(
        description="How precisely the date is known."
    )
    kind: Literal[
        "other", "transposition_deadline", "planned_commencement", "estimated_commencement"
    ] = Field(
        default="other",
        description=(
            "'other' (default, any milestone); 'transposition_deadline' (an EU directive's "
            "transposition deadline); 'planned_commencement' (a commencement a DRAFT proposes); "
            "'estimated_commencement' (somebody's estimate of a commencement). Neither "
            "commencement kind records that an act commences — that is a separate record."
        ),
    )
    note: str | None = Field(
        default=None, max_length=common.MAX_TEXT, description="Optional note (Märkus)."
    )


class AddImportantDate:
    name: ClassVar[str] = "add_important_date"
    label: ClassVar[str] = "Oluline tähtaeg"
    arguments: ClassVar[type[BaseModel]] = AddImportantDateArguments
    description: ClassVar[str] = (
        "Add an «Oluline tähtaeg» (important date) to a Teema: an expected milestone the "
        "department watches, at the precision it is known (day, month, quarter, year). A planned "
        "or estimated commencement stays planned/estimated. It does NOT create a next action, an "
        "opinion deadline, a confirmed commencement («Jõustumine») or any notification."
    )

    def prepare(self, *, user: Any, arguments: Any) -> Prepared:
        from app.intelligence.enums import ImportantDateKind, is_informational_milestone

        args = common.coerce(AddImportantDateArguments, arguments)
        common.require_writer(user)
        matter = common.resolve_matter(user, args.matter)
        common.require_writable_matter(matter)
        title = args.title.strip()
        if not title:
            raise OperationRefused("missing_information", "Describe the important date (title).")
        start, end, precision = common.parse_period(args.date, args.date_precision, "date")
        kind = IMPORTANT_DATE_KINDS[args.kind]
        note = (args.note or "").strip()
        canonical = {
            "matter_id": str(matter.pk),
            "title": title,
            "date_value": start.isoformat(),
            "period_end": end.isoformat(),
            "date_precision": precision,
            "kind": kind,
            "note": note,
        }
        changes = [
            Change("title", "Mis on oodata", None, title),
            Change("date", "Millal", None, common.at_precision(start, precision)),
            Change("kind", "Liik", None, str(ImportantDateKind(kind).label)),
        ]
        if note:
            changes.append(Change("note", "Märkus", None, note))
        consequences = []
        if precision != "EXACT":
            consequences.append(
                f"Kuupäev salvestatakse perioodina ({common.at_precision(start, precision)}), "
                "mitte päevana."
            )
        if kind == "ESTIMATED_COMMENCEMENT":
            consequences.append(
                "Salvestatakse allika hinnanguna jõustumise kohta — mitte kinnitatud jõustumisena."
            )
        if kind == "PLANNED_COMMENCEMENT":
            consequences.append(
                "Salvestatakse eelnõus kavandatud jõustumisena — mitte kinnitatud jõustumisena."
            )
        if is_informational_milestone(kind):
            consequences.append(
                "See on teave: see ei ole kunagi järgmine tegevus ega muutu hilinenuks."
            )
        else:
            # docs/adr/0120 §2: a milestone of these kinds may stand in for a missing
            # `Järgmiseks` and reads as dated work.
            consequences.append(
                "Kui teemal ei ole praegust tegevust, võib see tähtaeg (kui see on lähim "
                "eelseisev) olla teema järgmine samm teemal, «Minu asjad» lehel ja registris."
            )
        if kind == "TRANSPOSITION_DEADLINE":
            consequences.append(
                "ELi õiguse ülevõtmise tähtajana näidatakse seda «Menetluse kulg» real "
                "ülevõtmise etapi juures."
            )
        preview = Preview(
            summary=f"Lisa teemale {matter.display_reference} oluline tähtaeg",
            target=common.matter_target(matter),
            changes=changes,
            consequences=consequences,
            unchanged=[
                "Järgmist tegevust ei looda.",
                "Arvamuse tähtaeg ei muutu.",
                "Jõustumise kirjet («Jõustumine») ei looda; jõustumise meeldetuletust ei teki.",
                "Teavitust ei saadeta.",
                "Teemat ei suleta.",
            ],
        )
        return Prepared(
            canonical=canonical,
            state=self.state(user=user, canonical=canonical),
            preview=preview,
            target_matter_id=matter.pk,
        )

    def lock(self, *, canonical: dict[str, Any]) -> None:
        common.lock_matter(canonical["matter_id"])

    def state(self, *, user: Any, canonical: dict[str, Any]) -> dict[str, Any]:
        return common.matter_core_state(user, common.state_matter(canonical["matter_id"]))

    def execute(self, *, user: Any, canonical: dict[str, Any], once_token: uuid.UUID) -> Executed:
        from app.intelligence.services import add_important_date
        from app.matters.locks import lock_open_matter_for_business_write

        common.require_writer(user)
        common.visible_matter_by_id(user, canonical["matter_id"])
        with common.domain_refusals():
            matter = lock_open_matter_for_business_write(canonical["matter_id"])
            common.require_writable_matter(matter)
            record = add_important_date(
                matter=matter,
                actor=user,
                title=canonical["title"],
                date_value=date.fromisoformat(canonical["date_value"]),
                period_end=date.fromisoformat(canonical["period_end"]),
                date_precision=canonical["date_precision"],
                kind=canonical["kind"],
                note=canonical.get("note") or "",
            )
        return Executed(
            record_type="important_date",
            record_id=str(record.pk),
            matter_id=str(matter.pk),
            matter_reference=matter.display_reference,
            url=common.matter_url(matter.pk),
            saved={
                "title": record.title,
                "date_value": common.iso(record.date_value),
                "period_end": common.iso(record.period_end),
                "date_precision": record.date_precision,
                "kind": record.kind,
            },
        )


RESPONSE_DEADLINE = ResponseDeadline()
ADD_IMPORTANT_DATE = AddImportantDate()
