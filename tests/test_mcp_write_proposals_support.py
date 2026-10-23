"""Shared set-up for the MCP write tests (tests/test_mcp_write_*.py, docs/adr/0189).

Not a test module of its own (it holds no tests); named inside the write tests'
own file pattern because that is the pattern those tests own.

* `FakeRename` — a stand-in `Operation` registered into `REGISTRY` with
  ``monkeypatch``: it renames a Matter through the canonical
  `set_matter_title`, so a commit writes a real business row and a real
  `ChangeEvent`, and it can be told to refuse or to fail half-way.
* Stand-ins for what `app.mcp_access` adds for writes (`writer_for`,
  `may_write`, `confirm_write_code`) until that branch lands, installed with
  ``monkeypatch.setattr(..., raising=False)`` so the real ones win as soon as
  they exist only where a test does not install these.
* `urlpatterns` — the project's routes plus the approval page, used as
  ``ROOT_URLCONF`` only while `config/urls.py` does not yet include it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from django.conf import settings as django_settings
from django.urls import NoReverseMatch, include, path, reverse
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field

from app.core.authorization import may_write_business_content
from app.matters.locks import lock_matter_for_write
from app.matters.models import Matter
from app.matters.services import set_matter_title
from app.mcp_access import credentials, verification
from app.mcp_access import services as access_services
from app.mcp_access.models import GrantStatus, McpAccessGrant, McpToken, TokenKind
from app.mcp_writes import operations
from app.mcp_writes.operations.base import (
    Change,
    Executed,
    OperationRefused,
    Prepared,
    Preview,
    Target,
)
from config.urls import urlpatterns as project_urlpatterns
from tests import mcp_access_support as access_support

GATEWAY_KEY = "k" * 40
GOOD_CODE = "123456"
READ_WRITE = "juristid.read juristid.write"

urlpatterns = [
    *project_urlpatterns,
    path(
        "mcp-ligipaas/muudatus/",
        include(("app.mcp_writes.urls", "mcp_writes"), namespace="mcp_writes"),
    ),
]


def use_approval_routes(settings: Any) -> None:
    """Route the approval page while `config/urls.py` does not include it yet."""
    try:
        reverse("mcp_writes:approve", kwargs={"proposal_id": uuid.uuid4()})
    except NoReverseMatch:
        settings.ROOT_URLCONF = __name__


def enable(settings: Any) -> Any:
    access_support.enable(settings)
    settings.MCP_WRITES_ENABLED = True
    settings.MCP_WRITE_GATEWAY_KEY = GATEWAY_KEY
    use_approval_routes(settings)
    return settings


# -- the fake operation ----------------------------------------------------------


class RenameArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matter: str = Field(max_length=64, description="The Matter's id.")
    title: str = Field(min_length=1, max_length=200, description="The new title.")


class FakeRename:
    """Rename a Matter, through `set_matter_title`, with knobs for the failure cases."""

    name = "test_rename_matter"
    label = "Muuda teema pealkirja (test)"
    arguments = RenameArguments
    description = "Test operation."

    def __init__(self) -> None:
        self.executions = 0
        #: Raise this after the business row was written (an interrupted commit).
        self.fail_after_write: Exception | None = None
        #: Raise `OperationRefused(domain_rule)` after the business row was written.
        self.refuse_after_write = False

    def prepare(self, *, user: Any, arguments: Any) -> Prepared:
        matter = Matter.objects.filter(pk=_uuid(arguments.matter)).first()
        if matter is None:
            raise OperationRefused("not_found", "No such Matter is available to you.")
        if arguments.title.strip() == "PUUDU":
            raise OperationRefused("missing_information", "Say what the new title is.")
        return Prepared(
            canonical={"matter_id": str(matter.pk), "title": arguments.title.strip()},
            state={"title": matter.title},
            preview=Preview(
                summary=f"Muuda teema {matter.display_reference} pealkirja",
                target=Target(
                    matter_id=str(matter.pk),
                    reference=matter.display_reference,
                    title=matter.title,
                    url=reverse("matters:matter_detail", kwargs={"pk": matter.pk}),
                ),
                changes=[Change("title", "Pealkiri", matter.title, arguments.title.strip())],
                consequences=["Uus pealkiri on kohe kõigile nähtav."],
                unchanged=["Teemat ei suleta"],
                warnings=[],
            ),
            target_matter_id=matter.pk,
            target_record_type="matter",
            target_record_id=str(matter.pk),
        )

    def lock(self, *, canonical: dict[str, Any]) -> None:
        lock_matter_for_write(canonical["matter_id"])

    def state(self, *, user: Any, canonical: dict[str, Any]) -> dict[str, Any]:
        matter = Matter.objects.filter(pk=canonical["matter_id"]).first()
        if matter is None:
            raise OperationRefused("stale", "The Matter is gone.")
        return {"title": matter.title}

    def execute(self, *, user: Any, canonical: dict[str, Any], once_token: uuid.UUID) -> Executed:
        self.executions += 1
        matter = Matter.objects.get(pk=canonical["matter_id"])
        set_matter_title(matter=matter, value=canonical["title"], actor=user)
        if self.fail_after_write is not None:
            raise self.fail_after_write
        if self.refuse_after_write:
            raise OperationRefused("domain_rule", "The Matter is closed.")
        return Executed(
            record_type="matter",
            record_id=str(matter.pk),
            matter_id=str(matter.pk),
            matter_reference=matter.display_reference,
            url=reverse("matters:matter_detail", kwargs={"pk": matter.pk}),
            saved={"title": matter.title},
        )


def _uuid(raw: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def register(monkeypatch: Any) -> FakeRename:
    op = FakeRename()
    monkeypatch.setitem(operations.REGISTRY, op.name, op)
    return op


# -- stand-ins for the write side of app.mcp_access ------------------------------


@dataclass
class AccessStandIns:
    """What `writer_for` / `may_write` / `confirm_write_code` answer in these tests."""

    write_revoked: set[Any] = field(default_factory=set)
    code_calls: list[dict[str, Any]] = field(default_factory=list)
    wrong_codes: int = 0

    def may_write(self, user: Any, grant: Any) -> bool:
        return (
            access_services.may_connect(user, grant)
            and may_write_business_content(user)
            and bool(django_settings.MCP_WRITES_ENABLED)
            and grant.pk not in self.write_revoked
        )

    def writer_for(self, access: Any) -> Any:
        user = verification.reader_for(access)
        if user is None or django_settings.MCP_WRITE_SCOPE not in access.scopes:
            return None
        grant = McpAccessGrant.objects.filter(pk=access.grant_id).first()
        return user if self.may_write(user, grant) else None

    def confirm_write_code(
        self, *, user: Any, grant_id: Any, code: str, ip_address: Any, throttle: Any
    ) -> access_services.AuthorizationOutcome:
        self.code_calls.append(
            {"user": user.pk, "grant": grant_id, "throttle": [item.key for item in throttle]}
        )
        grant = McpAccessGrant.objects.filter(pk=grant_id).first()
        if grant is None or not self.may_write(user, grant):
            return access_services.AuthorizationOutcome(status="no_grant")
        if code != GOOD_CODE:
            self.wrong_codes += 1
            return access_services.AuthorizationOutcome(status="bad_code")
        return access_services.AuthorizationOutcome(status="ok")


def install_access(monkeypatch: Any) -> AccessStandIns:
    stand_ins = AccessStandIns()
    monkeypatch.setattr(verification, "writer_for", stand_ins.writer_for, raising=False)
    monkeypatch.setattr(access_services, "may_write", stand_ins.may_write, raising=False)
    monkeypatch.setattr(
        access_services, "confirm_write_code", stand_ins.confirm_write_code, raising=False
    )
    return stand_ins


# -- accounts, grants, tokens ----------------------------------------------------


def active_grant(user: Any) -> McpAccessGrant:
    return McpAccessGrant.objects.create(
        user=user, status=GrantStatus.ACTIVE, activated_at=timezone.now(), created_via="test"
    )


def token_for(
    user: Any,
    grant: McpAccessGrant,
    *,
    scopes: str = READ_WRITE,
    client_id: str = access_support.PUBLIC_CLIENT,
    family_id: uuid.UUID | None = None,
) -> str:
    """An ACCESS token row for ``user``, as the token endpoint would have issued it."""
    raw = credentials.new_opaque()
    now = timezone.now()
    McpToken.objects.create(
        kind=TokenKind.ACCESS,
        digest=credentials.digest(raw),
        family_id=family_id or uuid.uuid4(),
        grant=grant,
        user=user,
        client_id=client_id,
        scopes=scopes,
        resource=access_support.RESOURCE,
        user_epoch=user.security_epoch,
        issued_at=now,
        expires_at=now + timedelta(hours=1),
        family_started_at=now,
    )
    return raw


@dataclass(frozen=True)
class Access:
    """A `VerifiedAccess` with the ``family_id`` agent A adds, for calling services directly."""

    token_id: uuid.UUID
    user_id: uuid.UUID
    grant_id: uuid.UUID
    client_id: str
    scopes: frozenset[str]
    resource: str
    expires_at: Any
    family_id: uuid.UUID | None


def verified(raw_token: str) -> Access:
    """The token verified for real, with its family, as `verify_access_token` will answer."""
    answer = verification.verify_access_token(raw_token)
    assert answer is not None
    token = McpToken.objects.get(pk=answer.token_id)
    return Access(
        token_id=answer.token_id,
        user_id=answer.user_id,
        grant_id=answer.grant_id,
        client_id=answer.client_id,
        scopes=answer.scopes,
        resource=answer.resource,
        expires_at=answer.expires_at,
        family_id=token.family_id,
    )


def writer(user: Any) -> tuple[McpAccessGrant, str, Access]:
    """An ACTIVE grant, a read+write token and its verified access for ``user``."""
    grant = active_grant(user)
    raw = token_for(user, grant)
    return grant, raw, verified(raw)
