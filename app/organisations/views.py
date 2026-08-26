"""Inline institution creation.

One endpoint, reused by every field that names a counterparty: the Matter's
sender and addressee, the intake form's sender, and a Submission's recipients,
information copies and joint submitters. They are different *relationships* to
the same entity, so they share one Organisation table and one create path —
separate incoming and outgoing tables would make "did we ever write to the body
that wrote to us" unanswerable.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from app.core.decorators import business_write_required
from app.core.errors import DomainError
from app.organisations.forms import OrganisationQuickCreateForm
from app.organisations.models import Organisation
from app.organisations.services import get_or_create_organisation


def _options_context(target: str, selected: Organisation | None, **extra: Any) -> dict[str, Any]:
    return {
        "target": target,
        "organisations": Organisation.objects.order_by("name"),
        "selected": selected,
        "form": OrganisationQuickCreateForm(),
        **extra,
    }


@login_required
@business_write_required
@require_http_methods(["GET", "POST"])
def quick_create(request: HttpRequest) -> HttpResponse:
    """Create an institution without leaving the form that needed it.

    Returns the refreshed picker with the new row already selected, so the user
    carries on rather than losing what they had typed.
    """
    target = request.GET.get("target") or request.POST.get("target") or "organisation"
    field_label = request.GET.get("label") or request.POST.get("label") or "Organisatsioon"
    multiple = (request.GET.get("multiple") or request.POST.get("multiple") or "") == "1"

    if request.method == "GET":
        # `cancel` hands the picker straight back, so backing out of the
        # create panel restores the field rather than leaving the user stuck
        # inside a form they no longer want.
        if request.GET.get("cancel"):
            return render(
                request,
                "organisations/partials/picker.html",
                _options_context(target, None, label=field_label, multiple=multiple),
            )
        return render(
            request,
            "organisations/partials/quick_create_form.html",
            {
                "form": OrganisationQuickCreateForm(),
                "target": target,
                "label": field_label,
                "multiple": multiple,
            },
        )

    form = OrganisationQuickCreateForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "organisations/partials/quick_create_form.html",
            {"form": form, "target": target, "label": field_label, "multiple": multiple},
            status=400,
        )

    try:
        result = get_or_create_organisation(
            name=form.cleaned_data["name"],
            organisation_type=form.cleaned_data["organisation_type"],
            registry_code=form.cleaned_data["registry_code"],
        )
    except DomainError as error:
        form.add_error("name", str(error))
        return render(
            request,
            "organisations/partials/quick_create_form.html",
            {"form": form, "target": target, "label": field_label, "multiple": multiple},
            status=400,
        )

    return render(
        request,
        "organisations/partials/picker.html",
        _options_context(
            target,
            result.organisation,
            label=field_label,
            multiple=multiple,
            # Say so when an existing row was reused. Silently selecting a
            # different institution than the one somebody thought they typed is
            # how a Matter ends up filed against the wrong ministry.
            reused=not result.created,
        ),
    )
