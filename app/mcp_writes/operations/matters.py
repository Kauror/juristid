"""MCP writes on the Matter itself: create, retitle/summarise, (re)assign.

Each operation calls the service the web door calls — `file_new_matter` (the
one creation sequence `Uus teema` and `Saabunud` share), `set_matter_title` /
`set_brief_summary`, `assign_matter` — and applies the rules that door's form
and view apply before it: who may be named Vastutaja (`assignable_business_users`,
`may_assign_work` for anybody but oneself), what a title is, and that nothing
the person did not state is filled in.
"""

from __future__ import annotations

import uuid
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field, model_validator

from app.mcp_writes.operations import common
from app.mcp_writes.operations.base import (
    Change,
    Executed,
    OperationRefused,
    Prepared,
    Preview,
    Target,
)

#: `set_matter_title` caps a title here; refusing beyond it is more honest than cutting.
TITLE_MAX = 1000

ASSIGN_REFUSED = (
    "Your Juristid account may not give a Teema a Vastutaja or hand it to somebody else "
    "(capability «Töö määramine»)."
)


def _may_assign(user: Any) -> bool:
    from app.core.authorization import may_assign_work

    return bool(may_assign_work(user))


def _is_assignable(person: Any) -> bool:
    from app.accounts.selectors import is_assignable_business_user

    return bool(is_assignable_business_user(person))


def _user_by_id(user_id: Any) -> Any:
    from app.accounts.models import User

    if user_id is None:
        return None
    return User.objects.filter(pk=user_id).first()


def _same_title_open_matters(user: Any, title: str) -> list[str]:
    """References of open Matters the user may see with this exact title (a warning only)."""
    from app.matters.models import Matter

    wanted = " ".join(title.split()).casefold()
    rows = (
        Matter.objects.visible_to(user)
        .filter(is_open=True, title__iexact=title.strip())
        .order_by("-created_at")[:5]
    )
    return [
        matter.display_reference or str(matter.pk)
        for matter in rows
        if " ".join(matter.title.split()).casefold() == wanted
    ]


# ---------------------------------------------------------------------------
# create_matter
# ---------------------------------------------------------------------------


class CreateMatterArguments(common.Arguments):
    title: str = Field(
        min_length=1,
        max_length=TITLE_MAX,
        description=(
            "The new Teema's title, as the department would name it (e.g. the bill's or "
            "initiative's name). Required."
        ),
    )
    brief_summary: str | None = Field(
        default=None,
        max_length=common.MAX_TEXT,
        description=(
            "Optional «Lühikokkuvõte»: 2–3 plain-language sentences on what the Matter "
            "means for businesses. Leave out if the user did not give one."
        ),
    )
    responsible_person: str | None = Field(
        default=None,
        max_length=common.PERSON_NAME_MAX,
        description=(
            "Optional Vastutaja, by the colleague's display name (e.g. «Mari Maasikas»). "
            "Omit to make yourself the Vastutaja, as Juristid's «Uus teema» does. Naming "
            "somebody else needs the right to assign work. Never an e-mail address."
        ),
    )


class CreateMatter:
    name: ClassVar[str] = "create_matter"
    label: ClassVar[str] = "Uus teema"
    arguments: ClassVar[type[BaseModel]] = CreateMatterArguments
    description: ClassVar[str] = (
        "Create ONE new Teema (Matter) with a title, optionally a brief summary and a "
        "Vastutaja. Use only when the user explicitly asks to open a new Matter that does "
        "not exist yet — search first. It does NOT set an opinion deadline, a Hetkeseis "
        "(stage), a received date, senders, a next action or any outcome, and attaches no "
        "files; those are added on the Teema afterwards. The standard work plan "
        "(suggestions, not tasks) is added as Juristid's «Uus teema» adds it."
    )

    def _owner(self, user: Any, args: CreateMatterArguments) -> Any:
        if args.responsible_person:
            person = common.find_assignable_person(args.responsible_person)
            if person.pk != user.pk and not _may_assign(user):
                raise OperationRefused("forbidden", ASSIGN_REFUSED)
            return person
        # The person filing is the Vastutaja unless they chose somebody, when they
        # are somebody work may be given to (`creator_if_nameable`, docs/adr/0166 §3).
        return user if _is_assignable(user) else None

    def prepare(self, *, user: Any, arguments: Any) -> Prepared:
        args = common.coerce(CreateMatterArguments, arguments)
        common.require_writer(user)
        title = " ".join(args.title.split())
        if not title:
            raise OperationRefused("missing_information", "The new Teema needs a title.")
        owner = self._owner(user, args)
        summary = (args.brief_summary or "").strip()
        canonical = {
            "title": title,
            "brief_summary": summary,
            "owner_id": str(owner.pk) if owner is not None else None,
        }
        changes = [Change("title", "Pealkiri", None, title)]
        if summary:
            changes.append(Change("brief_summary", "Lühikokkuvõte", None, summary))
        changes.append(Change("owner", "Vastutaja", None, common.person_label(owner)))
        consequences = [
            "Teemale antakse järgmine vaba viide (aasta_number).",
            "Teemale lisatakse tavapärane tööplaan — soovitused, mitte ülesanded.",
        ]
        if owner is not None and owner.pk != user.pk:
            consequences.append(
                f"{owner.display_name} näeb teemat oma «Minu asjad» lehel uue asjana."
            )
        if owner is None:
            consequences.append("Teemal ei ole vastutajat.")
        warnings = []
        duplicates = _same_title_open_matters(user, title)
        if duplicates:
            warnings.append(
                "Sama pealkirjaga avatud teema on juba olemas: "
                + ", ".join(duplicates)
                + ". Kontrolli, et see ei ole sama teema teist korda."
            )
        preview = Preview(
            summary=f"Loo uus teema «{title}»",
            target=Target(matter_id=None, reference="", title=title, url=""),
            changes=changes,
            consequences=consequences,
            unchanged=[
                "Arvamuse tähtaega ei määrata.",
                "Hetkeseisu ei määrata.",
                "Saabumise kuupäeva, saatjat ega õigusakti ei määrata.",
                "Järgmist tegevust ei looda.",
                "Menetluse tulemust ei märgita.",
                "Faile ei lisata.",
                "Teema on tavanähtavusega, mitte piiratud.",
            ],
            warnings=warnings,
        )
        return Prepared(
            canonical=canonical, state=self.state(user=user, canonical=canonical), preview=preview
        )

    def lock(self, *, canonical: dict[str, Any]) -> None:
        # A new Matter has no row to lock; `file_new_matter` takes the reference
        # sequence's own lock when it allocates the number.
        return None

    def state(self, *, user: Any, canonical: dict[str, Any]) -> dict[str, Any]:
        owner = _user_by_id(canonical.get("owner_id"))
        return {
            "may_write": bool(common.may_write_business_content(user)),
            "may_assign_work": _may_assign(user),
            "owner_id": canonical.get("owner_id"),
            "owner_assignable": owner is not None and _is_assignable(owner),
        }

    def execute(self, *, user: Any, canonical: dict[str, Any], once_token: uuid.UUID) -> Executed:
        from app.core.enums import Visibility
        from app.matters.enums import MatterDataClass
        from app.matters.intake import file_new_matter

        common.require_writer(user)
        owner = _user_by_id(canonical.get("owner_id"))
        if canonical.get("owner_id") is not None:
            if owner is None or not _is_assignable(owner):
                raise OperationRefused(
                    "domain_rule", "The chosen Vastutaja is no longer an active colleague."
                )
            if owner.pk != user.pk and not _may_assign(user):
                raise OperationRefused("forbidden", ASSIGN_REFUSED)
        with common.domain_refusals():
            matter = file_new_matter(
                title=canonical["title"],
                actor=user,
                owner=owner,
                brief_summary=canonical.get("brief_summary") or "",
                visibility=Visibility.NORMAL,
                data_class=MatterDataClass.REAL,
                seed_plan=True,
            )
        return Executed(
            record_type="matter",
            record_id=str(matter.pk),
            matter_id=str(matter.pk),
            matter_reference=matter.display_reference,
            url=common.matter_url(matter.pk),
            saved={
                "reference": matter.display_reference,
                "title": matter.title,
                "brief_summary": matter.brief_summary,
                "owner": common.person_label(matter.owner),
            },
        )


# ---------------------------------------------------------------------------
# update_matter
# ---------------------------------------------------------------------------


class UpdateMatterArguments(common.Arguments):
    matter: str = Field(
        max_length=common.MATTER_ARGUMENT_MAX, description=common.MATTER_ARGUMENT_DESCRIPTION
    )
    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=TITLE_MAX,
        description="The new title. Omit to keep the current one.",
    )
    brief_summary: str | None = Field(
        default=None,
        min_length=1,
        max_length=common.MAX_TEXT,
        description=(
            "The new «Lühikokkuvõte» (replaces the whole current summary). Omit to keep it; "
            "use clear_brief_summary to remove it."
        ),
    )
    clear_brief_summary: bool = Field(
        default=False,
        description="True removes the current «Lühikokkuvõte». Not together with brief_summary.",
    )

    @model_validator(mode="after")
    def _one_meaning(self) -> UpdateMatterArguments:
        if self.brief_summary is not None and self.clear_brief_summary:
            raise ValueError("Give brief_summary or clear_brief_summary, not both.")
        if self.title is None and self.brief_summary is None and not self.clear_brief_summary:
            raise ValueError("Nothing to change: give title, brief_summary or clear_brief_summary.")
        return self


class UpdateMatter:
    name: ClassVar[str] = "update_matter"
    label: ClassVar[str] = "Teema pealkiri või lühikokkuvõte"
    arguments: ClassVar[type[BaseModel]] = UpdateMatterArguments
    description: ClassVar[str] = (
        "Change an existing Teema's title and/or its «Lühikokkuvõte» (brief summary). The "
        "new text replaces the old one; the old title stays in the history. It does NOT "
        "change the reference, the Vastutaja, the stage, any date or deadline, and does not "
        "touch a closed Matter."
    )

    def _current(self, matter: Any) -> dict[str, Any]:
        return {"title": matter.title, "brief_summary": matter.brief_summary or ""}

    def prepare(self, *, user: Any, arguments: Any) -> Prepared:
        args = common.coerce(UpdateMatterArguments, arguments)
        common.require_writer(user)
        matter = common.resolve_matter(user, args.matter)
        common.require_writable_matter(matter)
        current = self._current(matter)
        changes: list[Change] = []
        canonical: dict[str, Any] = {"matter_id": str(matter.pk)}
        if args.title is not None:
            title = " ".join(args.title.split())
            if not title:
                raise OperationRefused("invalid_argument", "A Teema's title cannot be empty.")
            if title != current["title"]:
                canonical["title"] = title
                changes.append(Change("title", "Pealkiri", current["title"], title))
        if args.clear_brief_summary:
            if current["brief_summary"]:
                canonical["brief_summary"] = ""
                changes.append(
                    Change("brief_summary", "Lühikokkuvõte", current["brief_summary"], "")
                )
        elif args.brief_summary is not None:
            summary = args.brief_summary.strip()
            if summary != current["brief_summary"]:
                canonical["brief_summary"] = summary
                changes.append(
                    Change("brief_summary", "Lühikokkuvõte", current["brief_summary"], summary)
                )
        if not changes:
            raise OperationRefused(
                "invalid_argument",
                "The proposed values equal the current ones; nothing would change.",
            )
        consequences = []
        if "title" in canonical:
            consequences.append("Endine pealkiri jääb teema ajalukku.")
        preview = Preview(
            summary=f"Muuda teema {matter.display_reference or matter.title} "
            + " ja ".join(change.label.lower() for change in changes),
            target=common.matter_target(matter),
            changes=changes,
            consequences=consequences,
            unchanged=[
                "Viide, vastutaja, hetkeseis, tähtajad ja tegevused ei muutu.",
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
        matter = common.state_matter(canonical["matter_id"])
        return {
            **common.matter_core_state(user, matter),
            "title": matter.title,
            "brief_summary_digest": common.digest(matter.brief_summary),
        }

    def execute(self, *, user: Any, canonical: dict[str, Any], once_token: uuid.UUID) -> Executed:
        from app.matters.locks import lock_open_matter_for_business_write
        from app.matters.services import set_brief_summary, set_matter_title

        common.require_writer(user)
        common.visible_matter_by_id(user, canonical["matter_id"])
        with common.domain_refusals():
            matter = lock_open_matter_for_business_write(canonical["matter_id"])
            common.require_writable_matter(matter)
            if "title" in canonical:
                set_matter_title(matter=matter, value=canonical["title"], actor=user)
            if "brief_summary" in canonical:
                set_brief_summary(matter=matter, value=canonical["brief_summary"], actor=user)
        return Executed(
            record_type="matter",
            record_id=str(matter.pk),
            matter_id=str(matter.pk),
            matter_reference=matter.display_reference,
            url=common.matter_url(matter.pk),
            saved={"title": matter.title, "brief_summary": matter.brief_summary},
        )


# ---------------------------------------------------------------------------
# assign_matter
# ---------------------------------------------------------------------------


class AssignMatterArguments(common.Arguments):
    matter: str = Field(
        max_length=common.MATTER_ARGUMENT_MAX, description=common.MATTER_ARGUMENT_DESCRIPTION
    )
    action: Literal["assign", "clear"] = Field(
        description=(
            "'assign' makes responsible_person the Vastutaja; 'clear' leaves the Teema with "
            "no Vastutaja (as Juristid's header allows)."
        )
    )
    responsible_person: str | None = Field(
        default=None,
        max_length=common.PERSON_NAME_MAX,
        description=(
            "With action='assign': the colleague's display name (e.g. «Mari Maasikas»). "
            "Never an e-mail address."
        ),
    )

    @model_validator(mode="after")
    def _person_with_assign(self) -> AssignMatterArguments:
        if self.action == "assign" and not self.responsible_person:
            raise ValueError("action='assign' needs responsible_person.")
        if self.action == "clear" and self.responsible_person:
            raise ValueError("action='clear' takes no responsible_person.")
        return self


class AssignMatter:
    name: ClassVar[str] = "assign_matter"
    label: ClassVar[str] = "Teema vastutaja"
    arguments: ClassVar[type[BaseModel]] = AssignMatterArguments
    description: ClassVar[str] = (
        "Give a Teema a Vastutaja (responsible lawyer), hand it to another colleague, or "
        "leave it with nobody. Needs the right to assign work. The current step and planned "
        "steps that were following the previous Vastutaja move with the file, exactly as in "
        "Juristid. It does NOT change any step's text or date, the stage or a deadline."
    )

    def prepare(self, *, user: Any, arguments: Any) -> Prepared:
        args = common.coerce(AssignMatterArguments, arguments)
        common.require_writer(user)
        matter = common.resolve_matter(user, args.matter)
        common.require_writable_matter(matter)
        if not _may_assign(user):
            raise OperationRefused("forbidden", ASSIGN_REFUSED)
        person = (
            common.find_assignable_person(args.responsible_person or "")
            if args.action == "assign"
            else None
        )
        if getattr(person, "pk", None) == matter.owner_id:
            raise OperationRefused(
                "invalid_argument", "That is already the Vastutaja; nothing would change."
            )
        canonical = {
            "matter_id": str(matter.pk),
            "owner_id": str(person.pk) if person is not None else None,
        }
        consequences = []
        if person is not None and person.pk != user.pk:
            consequences.append(
                f"{person.display_name} näeb teemat oma «Minu asjad» lehel uue asjana."
            )
        consequences.append(
            "Praegune ja planeeritud tegevused, mis järgisid eelmist vastutajat, liiguvad "
            + ("uuele vastutajale." if person is not None else "vastutajata teemaga kaasa.")
        )
        preview = Preview(
            summary=(
                f"Määra teema {matter.display_reference} vastutajaks {person.display_name}"
                if person is not None
                else f"Jäta teema {matter.display_reference} vastutajata"
            ),
            target=common.matter_target(matter),
            changes=[
                Change(
                    "owner",
                    "Vastutaja",
                    common.person_label(matter.owner),
                    common.person_label(person),
                )
            ],
            consequences=consequences,
            unchanged=[
                "Tegevuste tekst ja kuupäevad, hetkeseis ja tähtajad ei muutu.",
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
        matter = common.state_matter(canonical["matter_id"])
        person = _user_by_id(canonical.get("owner_id"))
        return {
            **common.matter_core_state(user, matter),
            "may_assign_work": _may_assign(user),
            "owner_id": str(matter.owner_id) if matter.owner_id else None,
            "proposed_owner_assignable": person is None or _is_assignable(person),
        }

    def execute(self, *, user: Any, canonical: dict[str, Any], once_token: uuid.UUID) -> Executed:
        from app.matters.locks import lock_open_matter_for_business_write
        from app.matters.services import assign_matter

        common.require_writer(user)
        if not _may_assign(user):
            raise OperationRefused("forbidden", ASSIGN_REFUSED)
        common.visible_matter_by_id(user, canonical["matter_id"])
        person = _user_by_id(canonical.get("owner_id"))
        if canonical.get("owner_id") is not None and (person is None or not _is_assignable(person)):
            raise OperationRefused(
                "domain_rule", "The chosen Vastutaja is no longer an active colleague."
            )
        with common.domain_refusals():
            matter = lock_open_matter_for_business_write(canonical["matter_id"])
            common.require_writable_matter(matter)
            assign_matter(matter=matter, owner=person, actor=user)
        return Executed(
            record_type="matter",
            record_id=str(matter.pk),
            matter_id=str(matter.pk),
            matter_reference=matter.display_reference,
            url=common.matter_url(matter.pk),
            saved={"owner": common.person_label(matter.owner)},
        )


CREATE_MATTER = CreateMatter()
UPDATE_MATTER = UpdateMatter()
ASSIGN_MATTER = AssignMatter()
