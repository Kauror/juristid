"""MCP writes that add a dated record to a Teema's shared chronology, or a link.

`add_matter_note` is `+ Lisa · Muu` (`workspace.add_matter_other`),
`record_meeting` is `Kohtumine / arutelu` (`workspace.add_matter_meeting`) and
`add_procedural_link` is `+ Menetluse link`
(`workspace.add_matter_procedural_link`). The same rules as those forms: the
day is stated and is not after today (Tallinn), a link is stored exactly as
given and never fetched, nothing opens or finishes a step, nothing closes the
Matter. The two dated doors are save-once (docs/adr/0167): the proposal's id is
their token.
"""

from __future__ import annotations

import uuid
from typing import Any, ClassVar, Literal

from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone
from pydantic import BaseModel, Field

from app.mcp_writes.operations import common
from app.mcp_writes.operations.base import (
    Change,
    Executed,
    OperationRefused,
    Prepared,
    Preview,
)

#: `ENTRY_URL_MAX_LENGTH`; a longer address is refused by the service anyway.
ENTRY_URL_MAX = 1000


def _entry_state(user: Any, canonical: dict[str, Any]) -> dict[str, Any]:
    matter = common.state_matter(canonical["matter_id"])
    return common.matter_core_state(user, matter)


def _occurred_on(value: str, name: str, ahead_refusal: str) -> Any:
    day = common.parse_day(value, name)
    if day > timezone.localdate():
        raise OperationRefused("domain_rule", ahead_refusal)
    return day


def _entry_executed(result: Any, matter: Any) -> Executed:
    from app.mcp_server import links

    entry = result.entry
    try:
        url = links.entry_url(matter.pk, entry.pk)
    except ImproperlyConfigured:
        url = common.matter_url(matter.pk)
    return Executed(
        record_type="entry",
        record_id=str(entry.pk),
        matter_id=str(matter.pk),
        matter_reference=matter.display_reference,
        url=url,
        saved={
            "kind": entry.kind,
            "occurred_on": common.iso(timezone.localtime(entry.occurred_at).date()),
            "url": entry.url or None,
        },
    )


# ---------------------------------------------------------------------------
# add_matter_note
# ---------------------------------------------------------------------------


class AddMatterNoteArguments(common.Arguments):
    matter: str = Field(
        max_length=common.MATTER_ARGUMENT_MAX, description=common.MATTER_ARGUMENT_DESCRIPTION
    )
    text: str = Field(
        min_length=1,
        max_length=common.MAX_TEXT,
        description=(
            "What happened or what material this is, and why it matters to the Teema, in plain "
            "text (blank lines become paragraphs). Shared with every colleague on the Teema."
        ),
    )
    occurred_on: str = Field(
        max_length=10,
        description=(
            "The day it happened or the material is from, ISO YYYY-MM-DD. Required; not after "
            "today (Europe/Tallinn). Ask the user if they did not say."
        ),
    )
    source_url: str | None = Field(
        default=None,
        max_length=ENTRY_URL_MAX,
        description=(
            "Optional http(s) address of the source. Stored as a link; never opened or fetched."
        ),
    )


class AddMatterNote:
    name: ClassVar[str] = "add_matter_note"
    label: ClassVar[str] = "Märge teema käiku (Muu)"
    arguments: ClassVar[type[BaseModel]] = AddMatterNoteArguments
    description: ClassVar[str] = (
        "Add a dated entry to the Teema's shared chronology («Teema käik», kind «Muu»): "
        "something that happened or material that arrived, with an optional source link. "
        "Use for facts already happened. It is NOT a private personal note, NOT a next "
        "action, NOT a meeting (use record_meeting), NOT a sent opinion or a received "
        "position; it completes no step, opens no wait and never closes the Matter. The link "
        "is never fetched."
    )

    def prepare(self, *, user: Any, arguments: Any) -> Prepared:
        from app.matters.services import normalize_entry_url
        from app.matters.workspace import OTHER_ENTRY_DAY_AHEAD

        args = common.coerce(AddMatterNoteArguments, arguments)
        common.require_writer(user)
        matter = common.resolve_matter(user, args.matter)
        common.require_writable_matter(matter)
        day = _occurred_on(args.occurred_on, "occurred_on", OTHER_ENTRY_DAY_AHEAD)
        body = common.text_to_entry_html(args.text)
        if not body:
            raise OperationRefused("missing_information", "Write what is being added to the Teema.")
        try:
            url = normalize_entry_url(args.source_url or "")
        except common.DomainError as error:
            raise OperationRefused("invalid_argument", str(error)) from None
        canonical = {
            "matter_id": str(matter.pk),
            "body": body,
            "occurred_on": day.isoformat(),
            "url": url,
        }
        changes = [
            Change("text", "Kirjeldus", None, args.text.strip()),
            Change("occurred_on", "Millal", None, common.at_precision(day, "EXACT")),
        ]
        if url:
            changes.append(Change("url", "Link", None, url))
        preview = Preview(
            summary=f"Lisa teema {matter.display_reference} käiku sissekanne (Muu)",
            target=common.matter_target(matter),
            changes=changes,
            consequences=["Sissekanne on teema ühises käigus nähtav kõigile kolleegidele."],
            unchanged=[
                "See ei ole isiklik märge.",
                "Järgmist tegevust ei looda ega lõpetata.",
                "Hetkeseis ja tähtajad ei muutu.",
                "Teemat ei suleta.",
                "Linki ei avata ega laadita alla — see salvestatakse viitena.",
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
        return _entry_state(user, canonical)

    def execute(self, *, user: Any, canonical: dict[str, Any], once_token: uuid.UUID) -> Executed:
        from app.matters.workspace import add_matter_other

        common.require_writer(user)
        matter = common.visible_matter_by_id(user, canonical["matter_id"])
        common.require_writable_matter(matter)
        with common.domain_refusals():
            result = add_matter_other(
                matter=matter,
                author=user,
                description=canonical["body"],
                occurred_on=common.from_iso(canonical["occurred_on"]),
                url=canonical.get("url") or "",
                once_token=once_token,
            )
        return _entry_executed(result, matter)


# ---------------------------------------------------------------------------
# record_meeting
# ---------------------------------------------------------------------------


class RecordMeetingArguments(common.Arguments):
    matter: str = Field(
        max_length=common.MATTER_ARGUMENT_MAX, description=common.MATTER_ARGUMENT_DESCRIPTION
    )
    summary: str = Field(
        min_length=1,
        max_length=common.MAX_TEXT,
        description=(
            "Short summary in plain text: who took part, what was discussed and agreed. "
            "Participants are named here (there is no separate participants field)."
        ),
    )
    held_on: str = Field(
        max_length=10,
        description="The day the meeting was held, ISO YYYY-MM-DD. Required; not after today.",
    )


class RecordMeeting:
    name: ClassVar[str] = "record_meeting"
    label: ClassVar[str] = "Kohtumine / arutelu"
    arguments: ClassVar[type[BaseModel]] = RecordMeetingArguments
    description: ClassVar[str] = (
        "Record a meeting or discussion that has already taken place on a Teema, with its "
        "summary and the day it was held. It does NOT create a Koja arvamus (formal opinion), "
        "a submission, a consultation round (kaasamine) or a feedback wait, does not plan a "
        "future meeting (use set_next_action), and completes or opens no step."
    )

    def prepare(self, *, user: Any, arguments: Any) -> Prepared:
        from app.matters.workspace import MEETING_ENTRY_DAY_AHEAD

        args = common.coerce(RecordMeetingArguments, arguments)
        common.require_writer(user)
        matter = common.resolve_matter(user, args.matter)
        common.require_writable_matter(matter)
        day = _occurred_on(args.held_on, "held_on", MEETING_ENTRY_DAY_AHEAD)
        body = common.text_to_entry_html(args.summary)
        if not body:
            raise OperationRefused("missing_information", "Write the meeting's summary.")
        canonical = {"matter_id": str(matter.pk), "body": body, "held_on": day.isoformat()}
        preview = Preview(
            summary=f"Lisa teema {matter.display_reference} käiku kohtumine",
            target=common.matter_target(matter),
            changes=[
                Change("summary", "Lühikokkuvõte", None, args.summary.strip()),
                Change("held_on", "Kuupäev", None, common.at_precision(day, "EXACT")),
            ],
            consequences=["Kohtumine on teema ühises käigus nähtav kõigile kolleegidele."],
            unchanged=[
                "Koja arvamust, pöördumist ega kaasamist ei looda.",
                "Tagasiside ootust ei avata.",
                "Järgmist tegevust ei looda ega lõpetata.",
                "Hetkeseis ja arvamuse tähtaeg ei muutu.",
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
        return _entry_state(user, canonical)

    def execute(self, *, user: Any, canonical: dict[str, Any], once_token: uuid.UUID) -> Executed:
        from app.matters.workspace import add_matter_meeting

        common.require_writer(user)
        matter = common.visible_matter_by_id(user, canonical["matter_id"])
        common.require_writable_matter(matter)
        with common.domain_refusals():
            result = add_matter_meeting(
                matter=matter,
                author=user,
                summary=canonical["body"],
                occurred_on=common.from_iso(canonical["held_on"]),
                once_token=once_token,
            )
        return _entry_executed(result, matter)


# ---------------------------------------------------------------------------
# add_procedural_link
# ---------------------------------------------------------------------------

LINK_KINDS: dict[str, str] = {
    "eis": "EIS",
    "ministry_register": "MINISTRY_REGISTER",
    "eu_procedure": "EU_PROCEDURE",
    "riigikogu": "RIIGIKOGU",
    "other": "OTHER",
}


class AddProceduralLinkArguments(common.Arguments):
    matter: str = Field(
        max_length=common.MATTER_ARGUMENT_MAX, description=common.MATTER_ARGUMENT_DESCRIPTION
    )
    kind: Literal["eis", "ministry_register", "eu_procedure", "riigikogu", "other"] = Field(
        description=(
            "Which official source the address points at: 'eis' (the government's "
            "draft-legislation system), 'ministry_register' (a ministry's document register), "
            "'eu_procedure' (EUR-Lex, a Commission or Parliament procedure page), 'riigikogu' "
            "(a Riigikogu proceeding) or 'other'. The user's statement, not inferred from the host."
        )
    )
    url: str = Field(
        min_length=1,
        max_length=1000,
        description="The http(s) address, exactly as the user gave it. Stored, never fetched.",
    )
    label: str | None = Field(
        default=None,
        max_length=120,
        description="Optional few words naming which proceeding (e.g. «1. lugemine»).",
    )


class AddProceduralLink:
    name: ClassVar[str] = "add_procedural_link"
    label: ClassVar[str] = "Menetluse link"
    arguments: ClassVar[type[BaseModel]] = AddProceduralLinkArguments
    description: ClassVar[str] = (
        "Record where the official proceeding of a Teema lives (EIS, a ministry register, an "
        "EU procedure, Riigikogu or other) as a link. Nothing is fetched, downloaded or "
        "scheduled; no document is created. An address the Teema already holds is refused."
    )

    def prepare(self, *, user: Any, arguments: Any) -> Prepared:
        from app.matters.enums import ProceduralLinkKind
        from app.matters.models import MatterProceduralLink
        from app.matters.services import (
            PROCEDURAL_LINK_DUPLICATE,
            normalize_procedural_link_kind,
            normalize_procedural_link_label,
            normalize_procedural_link_url,
        )

        args = common.coerce(AddProceduralLinkArguments, arguments)
        common.require_writer(user)
        matter = common.resolve_matter(user, args.matter)
        common.require_writable_matter(matter)
        try:
            url = normalize_procedural_link_url(args.url)
            kind = normalize_procedural_link_kind(LINK_KINDS[args.kind])
            label = normalize_procedural_link_label(args.label or "")
        except common.DomainError as error:
            raise OperationRefused("invalid_argument", str(error)) from None
        existing = MatterProceduralLink.objects.filter(matter=matter, url=url).first()
        if existing is not None:
            if existing.kind == kind and existing.label == label:
                raise OperationRefused(
                    "domain_rule", "This Teema already records that address; nothing would change."
                )
            raise OperationRefused("domain_rule", PROCEDURAL_LINK_DUPLICATE)
        canonical = {"matter_id": str(matter.pk), "kind": kind, "url": url, "label": label}
        changes = [
            Change("kind", "Allikas", None, str(ProceduralLinkKind(kind).label)),
            Change("url", "Aadress", None, url),
        ]
        if label:
            changes.append(Change("label", "Nimetus", None, label))
        preview = Preview(
            summary=f"Lisa teemale {matter.display_reference} menetluse link",
            target=common.matter_target(matter),
            changes=changes,
            unchanged=[
                "Linki ei avata ega laadita alla; dokumenti ei looda.",
                "Hetkeseis, tähtajad ja tegevused ei muutu.",
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
        from app.matters.models import MatterProceduralLink

        matter = common.state_matter(canonical["matter_id"])
        return {
            **common.matter_core_state(user, matter),
            "address_recorded": MatterProceduralLink.objects.filter(
                matter_id=matter.pk, url=canonical["url"]
            ).exists(),
        }

    def execute(self, *, user: Any, canonical: dict[str, Any], once_token: uuid.UUID) -> Executed:
        from app.matters.workspace import add_matter_procedural_link

        common.require_writer(user)
        matter = common.visible_matter_by_id(user, canonical["matter_id"])
        common.require_writable_matter(matter)
        with common.domain_refusals():
            result = add_matter_procedural_link(
                matter=matter,
                author=user,
                kind=canonical["kind"],
                url=canonical["url"],
                label=canonical.get("label") or "",
            )
        link = result.record
        return Executed(
            record_type="procedural_link",
            record_id=str(link.pk),
            matter_id=str(matter.pk),
            matter_reference=matter.display_reference,
            url=common.matter_url(matter.pk),
            saved={"kind": link.kind, "url": link.url, "label": link.label},
        )


ADD_MATTER_NOTE = AddMatterNote()
RECORD_MEETING = RecordMeeting()
ADD_PROCEDURAL_LINK = AddProceduralLink()
