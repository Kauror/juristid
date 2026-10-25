"""Haldus → Kasutajad: the account administration pages.

**One gate in front of all of them:** a *verified administrator* holding
`accounts.manage` (`admin_session.may_administer_accounts`). Under the shared
gate that is the administrator session — the person's own e-mail, personal
password and TOTP, bound to that person and never to the chosen persona
(docs/adr/0186); under `local_password` it is the personal sign-in with a
proved second factor, as before (docs/adr/0145 §12). Every other request is a
404 — the same answer whether the reader is anonymous, a shared-gate persona of
any role, or a colleague without the capability — so the pages do not confirm
that they exist (docs/adr/0037 on why 404).

Every change is a POST that additionally needs a **recent** proof of identity,
and every change is made by one function in `app.accounts.administration`,
handed a `VerifiedActor` — which the services require under the shared gate and
re-check under their lock. A view that forgot its gate would still be refused
below it.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django import forms
from django.contrib import messages
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.http.response import HttpResponseBase
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from app.accounts import admin_session, administration, capabilities, mail, mfa
from app.accounts.admin_session import VerifiedActor
from app.accounts.admin_session_views import reauthenticate_url
from app.accounts.enums import AccountStatus, Capability, ProvisioningState, UserRole
from app.accounts.forms import (
    AccountForm,
    ChangeEmailForm,
    CreateAccountForm,
    DeactivateForm,
    field_name,
)
from app.accounts.models import User
from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from app.core.authorization import account_capabilities, capability_overrides, has_capability
from app.core.client_ip import client_ip

#: The answer every refused administration request gets.
NOT_HERE = "Lehte ei leitud."


def account_admin_required[R: HttpResponseBase](view: Callable[..., R]) -> Callable[..., R]:
    @functools.wraps(view)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> R:
        if not admin_session.may_administer_accounts(request):
            raise Http404(NOT_HERE)
        # Administration is the activity that keeps the session alive.
        admin_session.note_activity(request)
        return view(request, *args, **kwargs)

    return wrapper


#: The change a stale proof stopped, kept for the page the person comes back to.
#: Never a secret: only the named fields of the page's own form, none of which
#: is a password or a code.
SESSION_UNSAVED = "account_admin:unsaved"
#: What that page then says, so nobody believes the change was made: the proof
#: gates the change, it does not make it (docs/adr/0186 §1, «Fresh proof»).
UNSAVED_NOTICE = "Muudatust ei salvestatud. Kinnitasid oma isiku — tee muudatus uuesti."
#: How long the kept change waits for the person to come back.
UNSAVED_SECONDS = 900
_UNSAVED_FIELD_LENGTH = 1000


def ask_for_fresh_proof(
    request: HttpRequest, back: str, *, message: str, keep: Iterable[str] = ()
) -> HttpResponseRedirect:
    """Send the administrator to prove themselves again; the change is **not** made.

    Nothing of the request is replayed afterwards — a privileged POST is never
    resubmitted on the strength of a proof. What is kept, one-shot, for the
    page ``back`` and this verified principal only, is that a change was
    stopped and the non-secret values typed into it, so that page can say the
    change was not saved and offer the values again.
    """
    principal = admin_session.verified_administrator(request)
    if principal is not None:
        request.session[SESSION_UNSAVED] = {
            "path": back,
            "principal": str(principal.pk),
            "at": timezone.now().isoformat(),
            "fields": {
                name: str(request.POST[name])[:_UNSAVED_FIELD_LENGTH]
                for name in keep
                if name in request.POST
            },
        }
    messages.info(request, message)
    return HttpResponseRedirect(reauthenticate_url(back))


def take_unsaved_change(request: HttpRequest) -> dict[str, str] | None:
    """The change a stale proof stopped on this page, once — and the notice that says so.

    Only for the same page and the same verified principal, only once the
    proof is fresh again (so the notice's «Kinnitasid oma isiku» is true), and
    only for a while. Answers the kept fields, or None.
    """
    kept = request.session.get(SESSION_UNSAVED)
    if not isinstance(kept, dict) or kept.get("path") != request.path:
        return None
    if admin_session.needs_fresh_proof(request):
        return None
    request.session.pop(SESSION_UNSAVED, None)
    principal = admin_session.verified_administrator(request)
    at = parse_datetime(str(kept.get("at") or ""))
    if (
        principal is None
        or kept.get("principal") != str(principal.pk)
        or at is None
        or timezone.now() - at > timedelta(seconds=UNSAVED_SECONDS)
    ):
        return None
    messages.warning(request, UNSAVED_NOTICE)
    fields = kept.get("fields")
    return {str(k): str(v) for k, v in fields.items()} if isinstance(fields, dict) else {}


def fresh_proof_required[R: HttpResponseBase](
    *, keep: Iterable[str] = ()
) -> Callable[[Callable[..., R]], Callable[..., R | HttpResponseBase]]:
    """Every administrative change asks for a proof no older than the re-auth window."""

    def decorator(view: Callable[..., R]) -> Callable[..., R | HttpResponseBase]:
        @functools.wraps(view)
        def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> R | HttpResponseBase:
            if admin_session.needs_fresh_proof(request):
                pk = kwargs.get("pk")
                back = (
                    reverse("account_admin:detail", kwargs={"pk": pk})
                    if pk
                    else reverse("account_admin:list")
                )
                return ask_for_fresh_proof(
                    request, back, message="Kinnita enne muutmist uuesti oma isik.", keep=keep
                )
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def _refused(request: HttpRequest, error: administration.AdministrationRefused) -> None:
    if isinstance(error, administration.PrivilegeRefused):
        # The verified administrator, never the persona: under the shared gate
        # `request.user` is whoever was picked from the list.
        administration.record_refusal(
            actor=admin_session.verified_administrator(request),
            error=error,
            ip_address=client_ip(request),
        )
    messages.error(request, str(error))


def _actor(request: HttpRequest) -> VerifiedActor:
    """The verified administrator this request acts as — never `request.user`."""
    actor = admin_session.administrator_actor(request)
    if actor is None:
        raise Http404(NOT_HERE)
    return actor


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
            **_shell(request),
        },
    )


def _shell(request: HttpRequest) -> dict[str, Any]:
    """The Haldus shell's sub-navigation, for the pages that sit inside it."""
    from app.haldus.sections import visible_sections

    return {"haldus_sections": visible_sections(request), "haldus_active": "kasutajad"}


# -- creating ---------------------------------------------------------------------------------


@never_cache
@account_admin_required
@require_http_methods(["GET", "POST"])
def user_create(request: HttpRequest) -> HttpResponse | HttpResponseBase:
    actor = _actor(request)
    may_delegate = has_capability(actor.user, Capability.DELEGATE_ADMINISTRATION)
    if request.method == "POST":
        if admin_session.needs_fresh_proof(request):
            return ask_for_fresh_proof(
                request,
                reverse("account_admin:create"),
                message="Kinnita enne konto loomist uuesti oma isik.",
                keep=CreateAccountForm.base_fields,
            )
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
    form = CreateAccountForm(include_technical=may_delegate)
    _offer_again(form, take_unsaved_change(request))
    return render(request, "accounts/admin/create.html", {"form": form, "nav_active": "haldus"})


def _offer_again(form: Any, kept: dict[str, str] | None) -> None:
    """Show the values of a change a stale proof stopped, in the controls the reader may use.

    Only as the form's initial values — nothing is submitted — and never in a
    disabled control, which shows what the account holds now. A choice that is
    not on offer is left as it was.
    """
    if kept is None:
        return
    for name, field in form.fields.items():
        if field.disabled:
            continue
        if isinstance(field, forms.BooleanField):
            form.initial[name] = name in kept
        elif name in kept and (
            not isinstance(field, forms.ChoiceField) or field.valid_value(kept[name])
        ):
            form.initial[name] = kept[name]


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


def _authority(actor: VerifiedActor, person: User) -> Authority:
    may_delegate = has_capability(actor.user, Capability.DELEGATE_ADMINISTRATION)
    is_self = actor.pk == person.pk
    # The technical ADMINISTRATOR counts as an administrator here, as it does in
    # `administration._touches_administration` (security review 2026-10-25).
    touches_administration = person.role == UserRole.ADMINISTRATOR or bool(
        account_capabilities(person) & set(capabilities.ADMINISTRATIVE_CAPABILITIES)
    )
    return Authority(
        may_delegate=may_delegate,
        is_self=is_self,
        may_change_power=not is_self and (may_delegate or not touches_administration),
    )


def _account_form(actor: VerifiedActor, person: User, data: Any = None) -> AccountForm:
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
        kept = take_unsaved_change(request)
        form = _account_form(_actor(request), person)
        _offer_again(form, kept if kept is not None and "display_name" in kept else None)
        context = _context(request, person, form)
        context["deactivation_reason"] = (kept or {}).get("reason", "")
        return render(request, "accounts/admin/detail.html", context)

    if admin_session.needs_fresh_proof(request):
        return ask_for_fresh_proof(
            request,
            reverse("account_admin:detail", kwargs={"pk": person.pk}),
            message="Kinnita enne muutmist uuesti oma isik.",
            keep=["display_name", "role", *map(field_name, capabilities.ALL_CAPABILITIES)],
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
    perform: Callable[[VerifiedActor, User, HttpRequest], str],
    *,
    keep: Iterable[str] = (),
) -> Callable[..., HttpResponseBase]:
    """A POST-only change on one account, refused or confirmed with a message."""

    @never_cache
    @account_admin_required
    @require_http_methods(["POST"])
    @fresh_proof_required(keep=keep)
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


def _send_link(actor: VerifiedActor, person: User, request: HttpRequest) -> str:
    outcome = administration.send_setup_link(actor=actor, user=person)
    if outcome.delivery.delivered:
        return "Link saadeti konto aadressile."
    message = outcome.delivery.message_et
    if admin_session.is_available() and admin_session.may_receive_administrator_link(person):
        # Behind the shared gate account e-mail stays off (juristid.E036): the
        # decision is recorded, and the operator hands the link over in person
        # (docs/adr/0186).
        message += (
            " Otsus on salvestatud. Halduri lingi väljastab operaator serveris käsuga "
            "issue_administrator_link ja annab selle inimesele üle."
        )
    raise administration.AdministrationRefused(message)


def _cancel_invitation(actor: VerifiedActor, person: User, request: HttpRequest) -> str:
    administration.cancel_invitation(actor=actor, user=person)
    return "Kutse on tühistatud. Konto ootab uuesti kinnitust."


def _deactivate(actor: VerifiedActor, person: User, request: HttpRequest) -> str:
    form = DeactivateForm(request.POST)
    reason = form.cleaned_data["reason"] if form.is_valid() else ""
    administration.deactivate_account(actor=actor, user=person, reason=reason)
    return "Konto on välja lülitatud. Selle seansid lõpetati."


def _reactivate(actor: VerifiedActor, person: User, request: HttpRequest) -> str:
    administration.reactivate_account(actor=actor, user=person)
    return "Konto on uuesti sisse lülitatud."


def _reset_second_factor(actor: VerifiedActor, person: User, request: HttpRequest) -> str:
    administration.reset_second_factor(actor=actor, user=person)
    return "Teine tegur on eemaldatud. Inimene seadistab uue järgmisel sisselogimisel."


send_link = _account_action(_send_link)
cancel_invitation = _account_action(_cancel_invitation)
deactivate = _account_action(_deactivate, keep=DeactivateForm.base_fields)
reactivate = _account_action(_reactivate)
reset_second_factor = _account_action(_reset_second_factor)


@never_cache
@account_admin_required
@require_http_methods(["GET", "POST"])
def change_email(request: HttpRequest, pk: Any) -> HttpResponse | HttpResponseBase:
    """Correct the sign-in address. Delegating administrators only."""
    person = get_object_or_404(User, pk=pk)
    actor = _actor(request)
    if not has_capability(actor.user, Capability.DELEGATE_ADMINISTRATION):
        raise Http404(NOT_HERE)
    if request.method == "GET":
        form = ChangeEmailForm()
        _offer_again(form, take_unsaved_change(request))
        return render(
            request,
            "accounts/admin/change_email.html",
            {"person": person, "form": form, "nav_active": "haldus"},
        )
    if admin_session.needs_fresh_proof(request):
        return ask_for_fresh_proof(
            request,
            reverse("account_admin:change_email", kwargs={"pk": person.pk}),
            message="Kinnita enne muutmist uuesti oma isik.",
            keep=ChangeEmailForm.base_fields,
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
