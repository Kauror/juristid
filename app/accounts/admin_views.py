"""Haldus → Kasutajad: the account administration pages.

**One gate in front of all of them:** `local_auth.may_administer_accounts`. It
is false in every mode but `local_password`, false for any session not signed
in there, false without a proved second factor, and false without
`accounts.manage`. A refused request is a 404 — the same answer whether the
reader is anonymous, a shared-gate persona of any role, or a colleague without
the capability — so the pages do not confirm that they exist (docs/adr/0145
§12, docs/adr/0037 on why 404).

Every change is a POST that additionally needs a **recent** proof of identity,
and every change is made by one function in `app.accounts.administration`,
which checks the actor's own authority again. A view that forgot its gate
would still be refused below it.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.contrib import messages
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.http.response import HttpResponseBase
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from app.accounts import administration, capabilities, local_auth, mail, mfa
from app.accounts.enums import AccountStatus, Capability, ProvisioningState
from app.accounts.forms import (
    AccountForm,
    ChangeEmailForm,
    CreateAccountForm,
    DeactivateForm,
    field_name,
)
from app.accounts.local_views import _reauthenticate_then
from app.accounts.models import User
from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from app.core.authorization import account_capabilities, capability_overrides, has_capability

#: The answer every refused administration request gets.
NOT_HERE = "Lehte ei leitud."


def account_admin_required[R: HttpResponseBase](view: Callable[..., R]) -> Callable[..., R]:
    @functools.wraps(view)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> R:
        if not local_auth.may_administer_accounts(request):
            raise Http404(NOT_HERE)
        return view(request, *args, **kwargs)

    return wrapper


def fresh_proof_required[R: HttpResponseBase](
    view: Callable[..., R],
) -> Callable[..., R | HttpResponseBase]:
    """Every administrative change asks for a proof no older than the re-auth window."""

    @functools.wraps(view)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> R | HttpResponseBase:
        if local_auth.needs_reauthentication(request):
            pk = kwargs.get("pk")
            back = (
                reverse("account_admin:detail", kwargs={"pk": pk})
                if pk
                else reverse("account_admin:list")
            )
            messages.info(request, "Kinnita enne muutmist uuesti oma isik.")
            return HttpResponseRedirect(_reauthenticate_then(back))
        return view(request, *args, **kwargs)

    return wrapper


def _refused(request: HttpRequest, error: administration.AdministrationRefused) -> None:
    if isinstance(error, administration.PrivilegeRefused):
        administration.record_refusal(
            actor=request.user, error=error, ip_address=request.META.get("REMOTE_ADDR")
        )
    messages.error(request, str(error))


def _actor(request: HttpRequest) -> User:
    return local_auth.person(request)


# -- the list -------------------------------------------------------------------------------


STATUS_FILTERS = {
    "": None,
    "ootel": AccountStatus.PENDING,
    "kutsutud": AccountStatus.INVITED,
    "aktiivne": AccountStatus.ACTIVE,
    "valjas": AccountStatus.DISABLED,
}


def _status_q(status: str | None) -> Q:
    if status == AccountStatus.PENDING:
        return Q(provisioning_state=ProvisioningState.PENDING)
    if status == AccountStatus.INVITED:
        return Q(provisioning_state=ProvisioningState.INVITED)
    if status == AccountStatus.ACTIVE:
        return Q(provisioning_state=ProvisioningState.ACTIVATED, is_active=True)
    if status == AccountStatus.DISABLED:
        return Q(provisioning_state=ProvisioningState.ACTIVATED, is_active=False)
    return Q()


@never_cache
@account_admin_required
@require_http_methods(["GET"])
def user_list(request: HttpRequest) -> HttpResponse:
    query = (request.GET.get("q") or "").strip()[:200]
    status_key = request.GET.get("seis", "")
    status = STATUS_FILTERS.get(status_key)
    people = User.objects.all().order_by("display_name", "upn")
    if query:
        people = people.filter(
            Q(display_name__icontains=query) | Q(upn__icontains=query) | Q(email__icontains=query)
        )
    people = people.filter(_status_q(status))
    rows = [
        {
            "person": person,
            "administrative": [
                capabilities.TEXT[capability].label
                for capability in capabilities.ADMINISTRATIVE_CAPABILITIES
                if capability in account_capabilities(person)
            ],
        }
        for person in people[:500]
    ]
    return render(
        request,
        "accounts/admin/list.html",
        {
            "rows": rows,
            "query": query,
            "status_key": status_key if status_key in STATUS_FILTERS else "",
            "statuses": [
                ("", "Kõik"),
                ("aktiivne", AccountStatus.ACTIVE.label),
                ("ootel", AccountStatus.PENDING.label),
                ("kutsutud", AccountStatus.INVITED.label),
                ("valjas", AccountStatus.DISABLED.label),
            ],
            "nav_active": "haldus",
        },
    )


# -- creating ---------------------------------------------------------------------------------


@never_cache
@account_admin_required
@require_http_methods(["GET", "POST"])
def user_create(request: HttpRequest) -> HttpResponse | HttpResponseBase:
    actor = _actor(request)
    may_delegate = has_capability(actor, Capability.DELEGATE_ADMINISTRATION)
    if request.method == "POST":
        if local_auth.needs_reauthentication(request):
            messages.info(request, "Kinnita enne konto loomist uuesti oma isik.")
            return HttpResponseRedirect(_reauthenticate_then(reverse("account_admin:create")))
        form = CreateAccountForm(request.POST, include_technical=may_delegate)
        if form.is_valid():
            try:
                user = administration.create_account(
                    actor=actor,
                    email=form.cleaned_data["email"],
                    display_name=form.cleaned_data["display_name"],
                    role=form.cleaned_data["role"],
                    external_reason=form.cleaned_data["external_reason"],
                )
            except administration.AdministrationRefused as error:
                if isinstance(error, administration.PrivilegeRefused):
                    administration.record_refusal(actor=actor, error=error)
                form.add_error(None, str(error))
            else:
                messages.success(
                    request,
                    "Konto on loodud. See ei ole veel kasutatav: kinnita ja saada kutse.",
                )
                return redirect("account_admin:detail", pk=user.pk)
        return render(
            request,
            "accounts/admin/create.html",
            {"form": form, "nav_active": "haldus"},
            status=400,
        )
    return render(
        request,
        "accounts/admin/create.html",
        {"form": CreateAccountForm(include_technical=may_delegate), "nav_active": "haldus"},
    )


# -- one account ------------------------------------------------------------------------------


#: The audit rows the account page's history shows — what administrators and
#: the account's owner did to the account, not every page they opened.
HISTORY_EVENTS = (
    SecurityEventType.ACCOUNT_CREATED,
    SecurityEventType.ACCOUNT_UPDATED,
    SecurityEventType.ACCOUNT_INVITED,
    SecurityEventType.ACCOUNT_INVITATION_CANCELLED,
    SecurityEventType.ACCOUNT_ACTIVATED,
    SecurityEventType.ACCOUNT_DEACTIVATED,
    SecurityEventType.ACCOUNT_REACTIVATED,
    SecurityEventType.LOGIN_EMAIL_CHANGED,
    SecurityEventType.EMAIL_EXCEPTION_APPROVED,
    SecurityEventType.ROLE_CHANGED,
    SecurityEventType.CAPABILITIES_CHANGED,
    SecurityEventType.ADMINISTRATOR_BOOTSTRAPPED,
    SecurityEventType.PASSWORD_SET,
    SecurityEventType.PASSWORD_CHANGED,
    SecurityEventType.CREDENTIAL_LINK_ISSUED,
    SecurityEventType.ACCOUNT_EMAIL_DELIVERY,
    SecurityEventType.MFA_ENROLLED,
    SecurityEventType.MFA_REMOVED,
    SecurityEventType.MFA_RECOVERY_CODES_REGENERATED,
    SecurityEventType.ACCESS_REFUSED,
)


def _history(person: User) -> list[dict[str, Any]]:
    events = (
        SecurityAuditEvent.objects.filter(subject_id=person.pk, event_type__in=HISTORY_EVENTS)
        .select_related("actor")
        .order_by("-occurred_at", "-created_at")[:60]
    )
    return [
        {
            "event": event,
            "label": SecurityEventType(event.event_type).label,
            "summary": _summary(event),
        }
        for event in events
    ]


def _labels(names: list[str] | None) -> str:
    known = [capabilities.TEXT[name].label for name in names or [] if name in capabilities.TEXT]
    return ", ".join(known) or "—"


def _summary(event: SecurityAuditEvent) -> str:
    """One readable line per history row. Never a raw identifier."""
    from app.accounts.enums import UserRole

    detail = event.detail or {}
    kind = event.event_type
    if kind == SecurityEventType.CAPABILITIES_CHANGED:
        before = _labels(detail.get("previous_capabilities"))
        return f"{before} → {_labels(detail.get('capabilities'))}"
    if kind == SecurityEventType.ROLE_CHANGED:
        named = dict(UserRole.choices)
        before = str(detail.get("previous_role") or "")
        after = str(detail.get("role") or "")
        return f"{named.get(before, before) or '—'} → {named.get(after, after)}"
    if kind in (SecurityEventType.LOGIN_EMAIL_CHANGED, SecurityEventType.ACCOUNT_UPDATED):
        return f"{detail.get('from', '')} → {detail.get('to', '')}"
    if kind == SecurityEventType.EMAIL_EXCEPTION_APPROVED:
        return str(detail.get("reason", ""))
    if kind == SecurityEventType.ACCOUNT_EMAIL_DELIVERY:
        return {
            "sent": "saadeti",
            "disabled": "saatmine välja lülitatud — ei saadetud",
            "no_link_base": "ei saadetud — avalik aadress seadistamata",
            "no_recipient": "ei saadetud — aadress puudub",
            "transport_error": "saatmine ebaõnnestus",
        }.get(str(detail.get("outcome", "")), "")
    if kind == SecurityEventType.ACCESS_REFUSED:
        return str(detail.get("refusal", ""))
    if kind == SecurityEventType.ACCOUNT_DEACTIVATED:
        return str(detail.get("reason", ""))
    return ""


@dataclass(frozen=True)
class Authority:
    """What the reader of an account page may change on it — for the page only.

    The services decide again, from the actor's own row, whatever this says.
    This exists so the page does not offer a control that can only be refused.
    """

    may_delegate: bool
    is_self: bool
    #: Role and business permissions. Never one's own, and an account that holds
    #: administrative power is changed only by somebody who may delegate it.
    may_change_power: bool

    @property
    def may_act_on_account(self) -> bool:
        return self.may_change_power


def _authority(actor: User, person: User) -> Authority:
    may_delegate = has_capability(actor, Capability.DELEGATE_ADMINISTRATION)
    is_self = actor.pk == person.pk
    touches_administration = bool(
        account_capabilities(person) & set(capabilities.ADMINISTRATIVE_CAPABILITIES)
    )
    return Authority(
        may_delegate=may_delegate,
        is_self=is_self,
        may_change_power=not is_self and (may_delegate or not touches_administration),
    )


def _account_form(actor: User, person: User, data: Any = None) -> AccountForm:
    """The account form, with every control the reader may not change disabled.

    A disabled field is ignored in the POST and keeps its initial value, which
    is the person's current one — so saving a name never changes a role or a
    permission by omission, and a crafted value for a locked toggle is not even
    read.
    """
    authority = _authority(actor, person)
    initial: dict[str, Any] = {"display_name": person.display_name, "role": person.role}
    # What the account grants while active, so a pending account's page shows
    # the role's defaults it will receive rather than a row of empty boxes.
    held = account_capabilities(person)
    for capability in capabilities.ALL_CAPABILITIES:
        initial[field_name(capability)] = capability in held
    form = AccountForm(
        data,
        initial=initial,
        include_technical=authority.may_delegate,
        current_role=person.role,
    )
    if not authority.may_change_power:
        form.fields["role"].disabled = True
    for capability in capabilities.ALL_CAPABILITIES:
        administrative = capabilities.is_administrative(capability)
        if not authority.may_change_power or (administrative and not authority.may_delegate):
            form.fields[field_name(capability)].disabled = True
    return form


def _context(request: HttpRequest, person: User, form: AccountForm | None = None) -> dict[str, Any]:
    actor = _actor(request)
    authority = _authority(actor, person)
    if form is None:
        form = _account_form(actor, person)
    overrides = capability_overrides(person)
    toggles = [
        {
            "field": form[field_name(capability)],
            "text": capabilities.TEXT[capability],
            "administrative": capabilities.is_administrative(capability),
            "overridden": capability in overrides,
        }
        for capability in capabilities.ALL_CAPABILITIES
    ]
    return {
        "person": person,
        "form": form,
        "business_toggles": [t for t in toggles if not t["administrative"]],
        "administrative_toggles": [t for t in toggles if t["administrative"]],
        "is_self": authority.is_self,
        "may_delegate": authority.may_delegate,
        "may_change_power": authority.may_change_power,
        "may_act_on_account": authority.may_act_on_account,
        "has_second_factor": mfa.has_second_factor(person),
        "recovery_codes_left": mfa.remaining_recovery_codes(person),
        "email_delivery_enabled": mail.delivery_enabled(),
        "history": _history(person),
        "nav_active": "haldus",
    }


@never_cache
@account_admin_required
@require_http_methods(["GET", "POST"])
def user_detail(request: HttpRequest, pk: Any) -> HttpResponse | HttpResponseBase:
    person = get_object_or_404(User, pk=pk)
    if request.method == "GET":
        return render(request, "accounts/admin/detail.html", _context(request, person))

    if local_auth.needs_reauthentication(request):
        messages.info(request, "Kinnita enne muutmist uuesti oma isik.")
        return HttpResponseRedirect(
            _reauthenticate_then(reverse("account_admin:detail", kwargs={"pk": person.pk}))
        )
    actor = _actor(request)
    form = _account_form(actor, person, request.POST)
    if not form.is_valid():
        return render(
            request, "accounts/admin/detail.html", _context(request, person, form), status=400
        )
    try:
        change = administration.update_account(
            actor=actor,
            user=person,
            display_name=form.cleaned_data["display_name"],
            role=form.cleaned_data["role"],
            wanted=form.wanted(),
        )
    except administration.AdministrationRefused as error:
        _refused(request, error)
        person.refresh_from_db()
        return render(
            request, "accounts/admin/detail.html", _context(request, person, form), status=400
        )
    if change.changed:
        messages.success(request, "Salvestatud.")
    else:
        messages.info(request, "Muudatusi ei olnud.")
    return redirect("account_admin:detail", pk=person.pk)


def _account_action(
    perform: Callable[[User, User, HttpRequest], str],
) -> Callable[..., HttpResponseBase]:
    """A POST-only change on one account, refused or confirmed with a message."""

    @never_cache
    @account_admin_required
    @require_http_methods(["POST"])
    @fresh_proof_required
    @functools.wraps(perform)
    def view(request: HttpRequest, pk: Any) -> HttpResponseBase:
        person = get_object_or_404(User, pk=pk)
        try:
            message = perform(_actor(request), person, request)
        except administration.AdministrationRefused as error:
            _refused(request, error)
        else:
            messages.success(request, message)
        return redirect("account_admin:detail", pk=person.pk)

    return view


def _send_link(actor: User, person: User, request: HttpRequest) -> str:
    outcome = administration.send_setup_link(actor=actor, user=person)
    if outcome.delivery.delivered:
        return "Link saadeti konto aadressile."
    raise administration.AdministrationRefused(outcome.delivery.message_et)


def _cancel_invitation(actor: User, person: User, request: HttpRequest) -> str:
    administration.cancel_invitation(actor=actor, user=person)
    return "Kutse on tühistatud. Konto ootab uuesti kinnitust."


def _deactivate(actor: User, person: User, request: HttpRequest) -> str:
    form = DeactivateForm(request.POST)
    reason = form.cleaned_data["reason"] if form.is_valid() else ""
    administration.deactivate_account(actor=actor, user=person, reason=reason)
    return "Konto on välja lülitatud. Selle seansid lõpetati."


def _reactivate(actor: User, person: User, request: HttpRequest) -> str:
    administration.reactivate_account(actor=actor, user=person)
    return "Konto on uuesti sisse lülitatud."


def _reset_second_factor(actor: User, person: User, request: HttpRequest) -> str:
    administration.reset_second_factor(actor=actor, user=person)
    return "Teine tegur on eemaldatud. Inimene seadistab uue järgmisel sisselogimisel."


send_link = _account_action(_send_link)
cancel_invitation = _account_action(_cancel_invitation)
deactivate = _account_action(_deactivate)
reactivate = _account_action(_reactivate)
reset_second_factor = _account_action(_reset_second_factor)


@never_cache
@account_admin_required
@require_http_methods(["GET", "POST"])
def change_email(request: HttpRequest, pk: Any) -> HttpResponse | HttpResponseBase:
    """Correct the sign-in address. Delegating administrators only."""
    person = get_object_or_404(User, pk=pk)
    actor = _actor(request)
    if not has_capability(actor, Capability.DELEGATE_ADMINISTRATION):
        raise Http404(NOT_HERE)
    if request.method == "GET":
        return render(
            request,
            "accounts/admin/change_email.html",
            {"person": person, "form": ChangeEmailForm(), "nav_active": "haldus"},
        )
    if local_auth.needs_reauthentication(request):
        messages.info(request, "Kinnita enne muutmist uuesti oma isik.")
        return HttpResponseRedirect(
            _reauthenticate_then(reverse("account_admin:change_email", kwargs={"pk": person.pk}))
        )
    form = ChangeEmailForm(request.POST)
    if form.is_valid():
        try:
            administration.change_login_email(
                actor=actor,
                user=person,
                email=form.cleaned_data["email"],
                external_reason=form.cleaned_data["external_reason"],
            )
        except administration.AdministrationRefused as error:
            if isinstance(error, administration.PrivilegeRefused):
                administration.record_refusal(actor=actor, error=error)
            form.add_error(None, str(error))
        else:
            messages.success(request, "Sisselogimise aadress on muudetud. Konto seansid lõpetati.")
            return redirect("account_admin:detail", pk=person.pk)
    return render(
        request,
        "accounts/admin/change_email.html",
        {"person": person, "form": form, "nav_active": "haldus"},
        status=400,
    )
