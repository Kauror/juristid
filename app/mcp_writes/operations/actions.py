"""MCP writes on a Teema's current action (`PRAEGUNE TEGEVUS`).

`set_next_action` writes new work through `set_next_action_for_new_work` (the
native boundary: the step's Vastutaja is the Matter's owner, refused when that
owner is no longer somebody work may be given to) or rewrites the current step
through `workspace.change_current_action` (`Muuda`: named, re-read under the
Matter's lock, restriction and plan step carried). `complete_action` finishes
the current step with exactly what its kind asks for: an ordinary step with
«Mida tegid?» through `workspace.complete_current_action`, an opinion follow-up
check with its typed outcome through `workspace.check_opinion_follow_up`
(docs/adr/0146 §5). There is no generic «done» flag anywhere.
"""

from __future__ import annotations

import html
import uuid
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

#: `NextActionForm.text` and `CompleteCurrentActionForm.next_text`.
STEP_TEXT_MAX = 2000

KINDS = {"do": "DO", "wait": "WAIT", "monitor": "MONITOR"}
KIND_LABELS = {"DO": "Teen", "WAIT": "Ootan", "MONITOR": "Jälgin"}
MEANINGS = {
    "planned_day": "DEADLINE",
    "review_on": "REVIEW_ON",
    "expected_around": "EXPECTED_AROUND",
}
MEANING_LABELS = {
    "DEADLINE": "Plaanis (päev, mil töö tehakse)",
    "REVIEW_ON": "Vaatan üle (ülevaatuse/meeldetuletuse päev)",
    "EXPECTED_AROUND": "Oodatav umbes (teise poole hinnanguline aeg)",
}
OUTCOMES = {
    "response_received": "RESPONSE_RECEIVED",
    "no_response": "NO_RESPONSE",
    "monitoring_ended": "MONITORING_ENDED",
}

NO_CURRENT_ACTION = "The Teema has no current action to change or complete."
CURRENT_ACTION_EXISTS = (
    "The Teema already has a current action. Use mode 'change_current' to rewrite it, or "
    "complete_action to finish it first; a new step never silently replaces another."
)
NOT_THE_CURRENT_ACTION = (
    "That action is not the Teema's current action any more. Read the Teema again."
)


def _current_action(matter: Any) -> Any:
    from app.workflow.services import current_next_action

    return current_next_action(matter)


def _visible_action(user: Any, action: Any) -> bool:
    from app.workflow.models import NextAction

    return NextAction.objects.visible_to(user).filter(pk=action.pk).exists()


def _action_state(action: Any) -> dict[str, Any] | None:
    if action is None:
        return None
    return {
        "id": str(action.pk),
        "kind": action.kind,
        "date_semantics": action.date_semantics,
        "target_date": common.iso(action.target_date),
        "date_precision": action.date_precision,
        "text_digest": common.digest(action.text),
        "status": action.status,
        "follow_up": action.follow_up_id is not None,
        "updated_at": common.iso(action.updated_at),
    }


def _owner_state(matter: Any) -> dict[str, Any]:
    from app.accounts.selectors import is_assignable_business_user

    return {
        "owner_id": str(matter.owner_id) if matter.owner_id else None,
        "owner_assignable": matter.owner_id is None
        or bool(is_assignable_business_user(matter.owner)),
    }


def _refuse_a_departed_owner(matter: Any) -> None:
    """New work goes to the Matter's owner, and only while work may be given to them."""
    from app.workflow.services import responsible_for_new_work

    with common.domain_refusals():
        responsible_for_new_work(matter=matter)


def _action_label(action: Any) -> str:
    when = action.display_date
    kind = KIND_LABELS.get(action.kind, action.kind)
    return f"{kind}: {action.text}" + (f" ({when})" if when else "")


def _resolve_current(user: Any, matter: Any, named: uuid.UUID | None) -> Any:
    current = _current_action(matter)
    if current is None:
        raise OperationRefused("domain_rule", NO_CURRENT_ACTION)
    if not _visible_action(user, current):
        raise OperationRefused("domain_rule", NO_CURRENT_ACTION)
    if named is not None and current.pk != named:
        raise OperationRefused("stale", NOT_THE_CURRENT_ACTION)
    return current


def _executed_action(action: Any, matter: Any, **extra: Any) -> Executed:
    return Executed(
        record_type="next_action",
        record_id=str(action.pk),
        matter_id=str(matter.pk),
        matter_reference=matter.display_reference,
        url=common.matter_url(matter.pk),
        saved={
            "text": action.text,
            "kind": action.kind,
            "date_semantics": action.date_semantics,
            "target_date": common.iso(action.target_date),
            "date_precision": action.date_precision,
            "status": action.status,
            **extra,
        },
    )


# ---------------------------------------------------------------------------
# set_next_action
# ---------------------------------------------------------------------------


class SetNextActionArguments(common.Arguments):
    matter: str = Field(
        max_length=common.MATTER_ARGUMENT_MAX, description=common.MATTER_ARGUMENT_DESCRIPTION
    )
    mode: Literal["new", "change_current"] = Field(
        description=(
            "'new' sets the Teema's current action when it has none (refused if one exists — "
            "finish it with complete_action first). 'change_current' rewrites the existing "
            "current action (same work, said differently or re-dated); the old version stays "
            "in history."
        )
    )
    current_action_id: str | None = Field(
        default=None,
        max_length=60,
        description=(
            "With 'change_current': optionally the UUID of the action you mean; refused if it is "
            "no longer the current one."
        ),
    )
    text: str = Field(
        min_length=1,
        max_length=STEP_TEXT_MAX,
        description="The step in the lawyer's own words, e.g. «Saata arvamus ministeeriumile».",
    )
    kind: Literal["do", "wait", "monitor"] = Field(
        description=(
            "'do' — Koda does it ('Teen'); 'wait' — Koda waits for somebody else, name them in "
            "waiting_for ('Ootan'); 'monitor' — Koda watches the process ('Jälgin')."
        )
    )
    date: str | None = Field(
        default=None,
        max_length=10,
        description=(
            "Optional date, written at its precision: YYYY-MM-DD (exact), YYYY-MM (month), "
            "YYYY-Qn (quarter), YYYY (year). Omit when no day is known — never guess one."
        ),
    )
    date_precision: Literal["exact", "month", "quarter", "year"] | None = Field(
        default=None, description="Required with date: how precisely the date is known."
    )
    date_meaning: Literal["planned_day", "review_on", "expected_around"] | None = Field(
        default=None,
        description=(
            "What the date means. 'do' steps: 'planned_day' (the day the work gets done; the "
            "only date that can become overdue). 'wait'/'monitor' steps: 'review_on' (a "
            "reminder to look again) or 'expected_around' (when the other side is expected to "
            "act). A WAIT/MONITOR date is NEVER Koda's opinion deadline — use response_deadline "
            "for that."
        ),
    )
    waiting_for: str | None = Field(
        default=None,
        max_length=common.PERSON_NAME_MAX,
        description="Required for 'wait': who Koda is waiting for (e.g. «Rahandusministeerium»).",
    )

    @model_validator(mode="after")
    def _consistent(self) -> SetNextActionArguments:
        if self.date is not None and self.date_precision is None:
            raise ValueError("date_precision is required with date.")
        if self.date is None and self.date_precision is not None:
            raise ValueError("date_precision without date: omit both when no date is known.")
        if self.kind == "do" and self.date_meaning not in (None, "planned_day"):
            raise ValueError("A 'do' step's date means 'planned_day'.")
        if self.kind != "do" and self.date_meaning == "planned_day":
            raise ValueError("A 'wait' or 'monitor' date means 'review_on' or 'expected_around'.")
        if self.kind != "wait" and self.waiting_for:
            raise ValueError("waiting_for belongs to a 'wait' step.")
        if self.mode == "new" and self.current_action_id:
            raise ValueError("current_action_id belongs to mode 'change_current'.")
        return self


class SetNextAction:
    name: ClassVar[str] = "set_next_action"
    label: ClassVar[str] = "Järgmine tegevus"
    arguments: ClassVar[type[BaseModel]] = SetNextActionArguments
    description: ClassVar[str] = (
        "Set the Teema's current action (one DO/WAIT/MONITOR step with optional date) when it "
        "has none, or rewrite the existing current action. Choose 'wait' only when Koda waits "
        "for a named party; a WAIT/MONITOR date is a review or expected date, NOT an opinion "
        "deadline (that is response_deadline). Dates keep their precision; no day is invented. "
        "It does NOT complete the current action (use complete_action), change the opinion "
        "deadline, move the stage, plan a later step, or touch an opinion follow-up check."
    )

    def _semantics(self, args: SetNextActionArguments) -> str:
        if args.kind == "do":
            return MEANINGS["planned_day"]
        if args.date_meaning is None:
            if args.date is not None:
                raise OperationRefused(
                    "missing_information",
                    "Say what the date of a 'wait' or 'monitor' step means: 'review_on' or "
                    "'expected_around'.",
                )
            # Nothing is dated, so nothing is said about a date (docs/adr/0106).
            return MEANINGS["review_on"]
        return MEANINGS[args.date_meaning]

    def _text(self, args: SetNextActionArguments) -> str:
        text = " ".join(args.text.split())
        if args.kind == "wait":
            party = " ".join((args.waiting_for or "").split())
            if not party:
                raise OperationRefused(
                    "missing_information", "Say who Koda is waiting for (waiting_for)."
                )
            if party.casefold() not in text.casefold():
                text = f"{text} (ootame: {party})"
        if len(text) > STEP_TEXT_MAX:
            raise OperationRefused(
                "invalid_argument", f"The step is longer than {STEP_TEXT_MAX} characters."
            )
        return text

    def prepare(self, *, user: Any, arguments: Any) -> Prepared:
        args = common.coerce(SetNextActionArguments, arguments)
        common.require_writer(user)
        matter = common.resolve_matter(user, args.matter)
        common.require_writable_matter(matter)
        _refuse_a_departed_owner(matter)
        named = common.uuid_or_refuse(args.current_action_id, "current_action_id")
        text = self._text(args)
        kind = KINDS[args.kind]
        semantics = self._semantics(args)
        start = None
        precision = "EXACT"
        if args.date is not None:
            start, _end, precision = common.parse_period(
                args.date, args.date_precision or "", "date"
            )

        current = _current_action(matter)
        if args.mode == "new":
            if current is not None:
                raise OperationRefused("domain_rule", CURRENT_ACTION_EXISTS)
        else:
            current = _resolve_current(user, matter, named)
            if current.follow_up_id is not None:
                from app.workflow.follow_ups import FOLLOW_UP_HAS_ITS_OWN_FORM

                raise OperationRefused("domain_rule", FOLLOW_UP_HAS_ITS_OWN_FORM)

        canonical = {
            "matter_id": str(matter.pk),
            "mode": args.mode,
            "action_id": str(current.pk) if current is not None else None,
            "text": text,
            "kind": kind,
            "date_semantics": semantics,
            "target_date": common.iso(start),
            "date_precision": precision,
        }
        when = common.at_precision(start, precision) or "kuupäev määramata"
        changes = [
            Change(
                "text",
                "Järgmiseks",
                current.text if current is not None else None,
                text,
            ),
            Change(
                "kind",
                "Liik",
                KIND_LABELS.get(current.kind) if current is not None else None,
                KIND_LABELS[kind],
            ),
            Change(
                "date",
                "Kuupäev",
                (current.display_date or "kuupäev määramata") if current is not None else None,
                when,
            ),
        ]
        if start is not None:
            changes.append(
                Change(
                    "date_semantics",
                    "Kuupäeva tähendus",
                    MEANING_LABELS.get(current.date_semantics) if current is not None else None,
                    MEANING_LABELS[semantics],
                )
            )
        consequences = []
        if args.mode == "new":
            consequences.append("Sellest saab teema praegune tegevus.")
        else:
            consequences.append(
                "Praegune tegevus asendatakse uuega; vana jääb ajalukku («Asendatud»)."
            )
            if current is not None and current.plan_step_id is not None:
                consequences.append("Seos tööplaani sammuga säilib.")
        if kind == "DO":
            consequences.append(
                "Kuupäev on päev, mil töö tehakse; kui see möödub, on tegevus üle aja."
                if start is not None
                else "Kuupäeva ei ole — tegevus ei ole kunagi «üle aja», kuni päev lisatakse."
            )
        else:
            consequences.append(
                "Ootamise/jälgimise kuupäev on ülevaatuse päev, mitte tähtaeg ega arvamuse "
                "tähtaeg; selle möödumine ei tee tegevust hilinenuks."
            )
        if matter.response_deadline is not None:
            consequences.append(
                f"Arvamuse tähtaeg {common.at_precision(matter.response_deadline, 'EXACT')} "
                "jääb kehtima ja tööloendisse eraldi."
            )
        consequences.append(
            f"Vastutaja: teema vastutaja {matter.owner.display_name}."
            if matter.owner is not None
            else "Teemal ei ole vastutajat, seega ka tegevusel mitte."
        )
        preview = Preview(
            summary=(
                f"Määra teema {matter.display_reference} praegune tegevus"
                if args.mode == "new"
                else f"Muuda teema {matter.display_reference} praegust tegevust"
            ),
            target=common.matter_target(
                matter,
                record_type="next_action" if current is not None else "",
                record_id=str(current.pk) if current is not None else "",
                record_label=_action_label(current) if current is not None else "",
            ),
            changes=changes,
            consequences=consequences,
            unchanged=[
                "Praegust tegevust ei märgita tehtuks.",
                "Arvamuse tähtaeg ei muutu.",
                "Hetkeseis ei muutu.",
                "Planeeritud tegevused jäävad alles.",
                "Teemat ei suleta.",
            ],
        )
        return Prepared(
            canonical=canonical,
            state=self.state(user=user, canonical=canonical),
            preview=preview,
            target_matter_id=matter.pk,
            target_record_type="next_action" if current is not None else "",
            target_record_id=str(current.pk) if current is not None else "",
        )

    def lock(self, *, canonical: dict[str, Any]) -> None:
        common.lock_matter(canonical["matter_id"])

    def state(self, *, user: Any, canonical: dict[str, Any]) -> dict[str, Any]:
        matter = common.state_matter(canonical["matter_id"])
        return {
            **common.matter_core_state(user, matter),
            **_owner_state(matter),
            "current_action": _action_state(_current_action(matter)),
        }

    def execute(self, *, user: Any, canonical: dict[str, Any], once_token: uuid.UUID) -> Executed:
        from app.matters.locks import lock_open_matter_for_business_write
        from app.matters.workspace import change_current_action
        from app.workflow.services import set_next_action_for_new_work

        common.require_writer(user)
        common.visible_matter_by_id(user, canonical["matter_id"])
        step = {
            "text": canonical["text"],
            "kind": canonical["kind"],
            "date_semantics": canonical["date_semantics"],
            "target_date": common.from_iso(canonical.get("target_date")),
            "date_precision": canonical["date_precision"],
        }
        with common.domain_refusals():
            matter = lock_open_matter_for_business_write(canonical["matter_id"])
            common.require_writable_matter(matter)
            current = _current_action(matter)
            if canonical["mode"] == "new":
                if current is not None:
                    raise OperationRefused("stale", CURRENT_ACTION_EXISTS)
                action = set_next_action_for_new_work(matter=matter, actor=user, **step)
            else:
                if current is None or str(current.pk) != canonical["action_id"]:
                    raise OperationRefused("stale", NOT_THE_CURRENT_ACTION)
                action = change_current_action(
                    matter=matter, actor=user, action_id=current.pk, **step
                )
        return _executed_action(action, matter)


# ---------------------------------------------------------------------------
# complete_action
# ---------------------------------------------------------------------------


class CompleteActionArguments(common.Arguments):
    matter: str = Field(
        max_length=common.MATTER_ARGUMENT_MAX, description=common.MATTER_ARGUMENT_DESCRIPTION
    )
    action_id: str | None = Field(
        default=None,
        max_length=60,
        description=(
            "Optionally the UUID of the current action you mean; refused if it is no longer "
            "current. Without it the Teema's current action is completed — the approval page "
            "names it."
        ),
    )
    what_was_done: str | None = Field(
        default=None,
        max_length=common.MAX_TEXT,
        description=(
            "«Mida tegid?»: what was actually done, in plain text. Required for an ordinary "
            "action. For an opinion follow-up check it is the explanation: what was answered, "
            "or (required with 'monitoring_ended') why monitoring ends."
        ),
    )
    follow_up_outcome: Literal["response_received", "no_response", "monitoring_ended"] | None = (
        Field(
            default=None,
            description=(
                "Only when the current action is an opinion follow-up check («Kontrolli, kas "
                "adressaat on Koja arvamusele vastanud»), and then required: 'response_received', "
                "'no_response' (give next_check_on) or 'monitoring_ended' (give the reason in "
                "what_was_done)."
            ),
        )
    )
    next_check_on: str | None = Field(
        default=None,
        max_length=10,
        description="With 'no_response': the day of the next check, YYYY-MM-DD, not in the past.",
    )
    next_step_text: str | None = Field(
        default=None,
        max_length=STEP_TEXT_MAX,
        description=(
            "Ordinary actions only: an optional next step in the user's own words, which becomes "
            "the new current action. Omit if the user named none — nothing is chosen for them."
        ),
    )
    next_step_on: str | None = Field(
        default=None,
        max_length=10,
        description="Optional exact day (YYYY-MM-DD) for next_step_text.",
    )

    @model_validator(mode="after")
    def _consistent(self) -> CompleteActionArguments:
        if self.next_step_on is not None and not self.next_step_text:
            raise ValueError("next_step_on without next_step_text: a day is not a step.")
        if self.next_check_on is not None and self.follow_up_outcome != "no_response":
            raise ValueError("next_check_on belongs to follow_up_outcome 'no_response'.")
        if self.follow_up_outcome is not None and self.next_step_text:
            raise ValueError("A follow-up check takes no next_step_text.")
        return self


class CompleteAction:
    name: ClassVar[str] = "complete_action"
    label: ClassVar[str] = "Praegune tegevus tehtud"
    arguments: ClassVar[type[BaseModel]] = CompleteActionArguments
    description: ClassVar[str] = (
        "Finish the Teema's current action with what its kind requires: an ordinary action with "
        "«what was done» (written to the chronology), optionally naming the next step; an opinion "
        "follow-up check with its outcome (response received / no response yet + next check day / "
        "monitoring ended + reason). There is no bare «done». It does NOT move the stage, close "
        "the Matter, change the opinion deadline, finish a consultation round's feedback wait, or "
        "complete a planned (not current) action."
    )

    def prepare(self, *, user: Any, arguments: Any) -> Prepared:
        from app.matters.selectors import planned_actions_of

        args = common.coerce(CompleteActionArguments, arguments)
        common.require_writer(user)
        matter = common.resolve_matter(user, args.matter)
        common.require_writable_matter(matter)
        named = common.uuid_or_refuse(args.action_id, "action_id")
        current = _resolve_current(user, matter, named)
        said = (args.what_was_done or "").strip()
        canonical: dict[str, Any] = {"matter_id": str(matter.pk), "action_id": str(current.pk)}
        changes = [Change("status", "Olek", "Kehtiv", "Tehtud")]
        consequences: list[str] = []
        planned = planned_actions_of(matter, user)

        if current.follow_up_id is not None:
            if args.follow_up_outcome is None:
                raise OperationRefused(
                    "missing_information",
                    "This is an opinion follow-up check: say what it found — "
                    "'response_received', 'no_response' (with next_check_on) or "
                    "'monitoring_ended' (with the reason in what_was_done).",
                )
            outcome = OUTCOMES[args.follow_up_outcome]
            next_check = None
            if outcome == "NO_RESPONSE":
                if args.next_check_on is None:
                    raise OperationRefused(
                        "missing_information", "Give next_check_on, the day of the next check."
                    )
                next_check = common.parse_day(args.next_check_on, "next_check_on")
                if next_check < timezone.localdate():
                    from app.workflow.follow_ups import FOLLOW_UP_DATE_IN_THE_PAST

                    raise OperationRefused("domain_rule", FOLLOW_UP_DATE_IN_THE_PAST)
            if outcome == "MONITORING_ENDED" and not said:
                raise OperationRefused(
                    "missing_information",
                    "Write why monitoring of the opinion ends (what_was_done).",
                )
            canonical.update(
                {
                    "kind": "follow_up",
                    "outcome": outcome,
                    "body": html.escape(said),
                    "next_check_on": common.iso(next_check),
                }
            )
            from app.workflow.enums import FollowUpOutcome

            changes.append(
                Change("outcome", "Mis selgus", None, str(FollowUpOutcome(outcome).label))
            )
            if said:
                changes.append(Change("explanation", "Selgitus", None, said))
            if next_check is not None:
                changes.append(
                    Change(
                        "next_check_on",
                        "Järgmine kontroll",
                        None,
                        common.at_precision(next_check, "EXACT"),
                    )
                )
                consequences.append(
                    "Järgmine järelkontroll planeeritakse valitud päevale "
                    "(sama arvamus, sama tekst)."
                )
            else:
                consequences.append("Arvamuse jälgimine lõpeb; uut kontrolli ei planeerita.")
            if planned:
                consequences.append(
                    f"Praeguseks tegevuseks saab varaseim planeeritud tegevus: «{planned[0].text}»."
                )
        else:
            if args.follow_up_outcome is not None:
                raise OperationRefused(
                    "invalid_argument",
                    "The current action is not an opinion follow-up check; give what_was_done "
                    "instead of follow_up_outcome.",
                )
            if not said:
                raise OperationRefused(
                    "missing_information",
                    "Write what was done about the current action (what_was_done). Juristid never "
                    "records a step as done without it.",
                )
            body = common.text_to_entry_html(said)
            next_text = " ".join((args.next_step_text or "").split())
            next_on = (
                common.parse_day(args.next_step_on, "next_step_on") if args.next_step_on else None
            )
            if next_text:
                _refuse_a_departed_owner(matter)
            canonical.update(
                {
                    "kind": "ordinary",
                    "body": body,
                    "next_text": next_text,
                    "next_date": common.iso(next_on),
                }
            )
            changes.append(Change("what_was_done", "Mida tegid?", None, said))
            consequences.append("Kirjeldus lisatakse teema käiku selle tegevuse tulemusena.")
            if next_text:
                changes.append(
                    Change(
                        "next_step",
                        "Järgmine tegevus",
                        None,
                        next_text
                        + (f" ({common.at_precision(next_on, 'EXACT')})" if next_on else ""),
                    )
                )
                consequences.append("Järgmisest tegevusest saab teema praegune tegevus (Teen).")
            elif planned:
                consequences.append(
                    f"Praeguseks tegevuseks saab varaseim planeeritud tegevus: «{planned[0].text}»."
                )
            else:
                consequences.append("Teemal ei ole pärast seda praegust tegevust.")
            if current.plan_step_id is not None:
                consequences.append("Tööplaani vastav samm märgitakse tehtuks.")

        preview = Preview(
            summary=f"Märgi teema {matter.display_reference} praegune tegevus tehtuks",
            target=common.matter_target(
                matter,
                record_type="next_action",
                record_id=str(current.pk),
                record_label=_action_label(current),
            ),
            changes=changes,
            consequences=consequences,
            unchanged=[
                "Hetkeseis ei muutu.",
                "Arvamuse tähtaeg ei muutu.",
                "Teemat ei suleta.",
            ],
        )
        return Prepared(
            canonical=canonical,
            state=self.state(user=user, canonical=canonical),
            preview=preview,
            target_matter_id=matter.pk,
            target_record_type="next_action",
            target_record_id=str(current.pk),
        )

    def lock(self, *, canonical: dict[str, Any]) -> None:
        common.lock_matter(canonical["matter_id"])

    def state(self, *, user: Any, canonical: dict[str, Any]) -> dict[str, Any]:
        from app.workflow.enums import ActionStatus
        from app.workflow.models import NextAction

        matter = common.state_matter(canonical["matter_id"])
        first_planned = (
            NextAction.objects.filter(matter=matter, status=ActionStatus.PLANNED)
            .order_by("target_date", "created_at", "pk")
            .values_list("pk", flat=True)
            .first()
        )
        return {
            **common.matter_core_state(user, matter),
            **_owner_state(matter),
            "current_action": _action_state(_current_action(matter)),
            "first_planned_action": str(first_planned) if first_planned else None,
        }

    def execute(self, *, user: Any, canonical: dict[str, Any], once_token: uuid.UUID) -> Executed:
        from app.matters.locks import lock_open_matter_for_business_write
        from app.matters.workspace import check_opinion_follow_up, complete_current_action
        from app.workflow.models import NextAction

        common.require_writer(user)
        common.visible_matter_by_id(user, canonical["matter_id"])
        with common.domain_refusals():
            matter = lock_open_matter_for_business_write(canonical["matter_id"])
            common.require_writable_matter(matter)
            current = _current_action(matter)
            if current is None or str(current.pk) != canonical["action_id"]:
                raise OperationRefused("stale", NOT_THE_CURRENT_ACTION)
            if canonical["kind"] == "follow_up":
                result = check_opinion_follow_up(
                    matter=matter,
                    author=user,
                    action_id=current.pk,
                    outcome=canonical["outcome"],
                    body=canonical.get("body") or "",
                    next_check_on=common.from_iso(canonical.get("next_check_on")),
                )
            else:
                result = complete_current_action(
                    matter=matter,
                    author=user,
                    action_id=current.pk,
                    body=canonical["body"],
                    next_text=canonical.get("next_text") or "",
                    next_date=common.from_iso(canonical.get("next_date")),
                )
        completed = NextAction.objects.get(pk=current.pk)
        return _executed_action(
            completed,
            matter,
            entry_id=str(result.entry.pk) if result.entry is not None else None,
            next_action_id=str(result.action.pk) if result.action is not None else None,
        )


SET_NEXT_ACTION = SetNextAction()
COMPLETE_ACTION = CompleteAction()
