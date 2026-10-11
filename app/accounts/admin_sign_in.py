"""The Django admin's sign-in page, which checks no password outside development.

`admin.site.urls` mounts Django's own `LoginView` at `/admin/login/`: a form
that hands an address and a password to `ModelBackend` and signs the session in
as whoever they belong to. In every mode in which the application has a sign-in
of its own, that form is a second door beside it (AUD-051 AU-2):

* **no throttle and no second factor.** Under `local_password` every `is_staff`
  account must prove a second factor (`local_auth.second_factor_mandatory`,
  docs/adr/0145 §8); this page would sign one in on a password alone, and
  answer "wrong password" or "signed in" to anybody already past the door — a
  password oracle for every technical account;
* **past the shared gate**, the same page is how a staff account was signed in
  without being a persona, which the persona rule exists to rule out
  (docs/adr/0034).

So outside `AUTH_MODE=none` this view stands where Django's did and decides only
where to send the visitor. It never reads the submitted credentials, never calls
`authenticate` and never touches the session, whatever the method:

* a signed-in, active staff account is already the admin's user — on to the
  admin (or the safe `next` it asked for);
* somebody signed in who is not staff gets the product, not a form inviting
  them to try another account's password;
* nobody signed in is sent to the mode's own sign-in, `settings.LOGIN_URL`,
  with the admin as where to come back to. Under `local_password` that is
  `/konto/sisene/`, which throttles, audits and asks a staff account for its
  second factor; under the shared gate it is the persona list, which offers no
  staff account — so the Django admin has no browser entrance there at all.

Under `AUTH_MODE=none` — a developer laptop and CI — Django's page answers as it
always did: there is no other sign-in for a technical account there, and that
mode holds no real data (docs/adr/0004).
"""

from __future__ import annotations

from django.conf import settings
from django.contrib import admin
from django.contrib.auth.views import redirect_to_login
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.views.decorators.cache import never_cache

from app.accounts.enums import AuthMode
from app.accounts.shared_gate import current_mode
from app.core.request_params import safe_local_path


@never_cache
def admin_sign_in(request: HttpRequest) -> HttpResponse:
    """`/admin/login/`: Django's page under `none`, a redirect in every other mode."""
    if current_mode() == AuthMode.NONE:
        return admin.site.login(request)

    target = safe_local_path(
        request.GET.get("next"), host=request.get_host(), require_https=request.is_secure()
    ) or reverse("admin:index")
    if admin.site.has_permission(request):
        return HttpResponseRedirect(target)
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return HttpResponseRedirect(reverse("core:home"))
    return redirect_to_login(target, settings.LOGIN_URL)
