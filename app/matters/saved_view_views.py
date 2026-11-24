"""«Minu vaated»: save, rename and remove a person's own Teemad view (docs/adr/0222).

Three POSTs, each about the signed-in person's own rows and nothing else — the
class C of tests/test_business_write_boundary.py, beside remembered blocks and
the scratchpad: no business content, no business service, no `ChangeEvent`, and
no parameter that names a person. A view id that is not the sender's own is a
404. Each answers with a redirect back into Teemad and a message saying what
happened, so the page works with no script at all.
"""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect, QueryDict
from django.urls import reverse
from django.views.decorators.http import require_POST

from app.core.errors import DomainError
from app.core.request_params import safe_local_path
from app.matters import saved_views

#: The form field carrying the register address being saved, as a query string.
VIEW_FIELD = "vaade"


def _back(request: HttpRequest, posted: Any) -> str:
    """Where the form was sent from (``posted``), if it was Teemad; else Teemad itself."""
    register = reverse("matters:matter_list")
    target = safe_local_path(posted, host=request.get_host())
    if target and target.split("?", 1)[0] == register:
        return target
    return register


@login_required
@require_POST
def save_view(request: HttpRequest) -> HttpResponse:
    """«Salvesta» — keep the current Teemad address under a name, then open it."""
    params = QueryDict(str(request.POST.get(VIEW_FIELD, ""))[: saved_views.MAX_QUERY_LENGTH * 2])
    try:
        view = saved_views.save_view(
            user=request.user, name=request.POST.get("nimi"), params=params
        )
    except DomainError as error:
        messages.error(request, str(error))
        return HttpResponseRedirect(_back(request, request.POST.get("next")))
    messages.success(request, f"Vaade «{view.name}» on salvestatud.")
    return HttpResponseRedirect(f"{reverse('matters:matter_list')}?{view.query}")


def _own(request: HttpRequest, pk: Any) -> Any:
    view = saved_views.own_view(request.user, pk)
    if view is None:
        raise Http404("Vaadet ei leitud.")
    return view


@login_required
@require_POST
def rename_view(request: HttpRequest, pk: Any) -> HttpResponse:
    """«Nimeta ümber» — the sender's own view only."""
    view = _own(request, pk)
    old = view.name
    try:
        saved_views.rename_view(view=view, name=request.POST.get("nimi"))
    except DomainError as error:
        messages.error(request, str(error))
    else:
        if view.name != old:
            messages.success(request, f"Vaade «{old}» on nüüd «{view.name}».")
    return HttpResponseRedirect(_back(request, request.POST.get("next")))


@login_required
@require_POST
def remove_view(request: HttpRequest, pk: Any) -> HttpResponse:
    """«Eemalda» — forget the sender's own view; the register and its data are untouched."""
    view = _own(request, pk)
    name = view.name
    saved_views.remove_view(view=view)
    messages.success(request, f"Vaade «{name}» on eemaldatud.")
    return HttpResponseRedirect(_back(request, request.POST.get("next")))
