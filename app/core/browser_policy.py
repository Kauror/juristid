"""The browser's own limits on what an HTML page from this application may do.

Defence in depth, and only that (ENG-124). No working XSS was found here: entry
bodies are sanitised by an allow-list before they are rendered, templates
escape everything else, and the audit's probes came back escaped. What these
headers buy is a smaller blast radius for the next mistake — a ``|safe`` in the
wrong place, a DOM sink in a script — which would otherwise run with a lawyer's
session in a single-origin application where every page can read every other.

**Content-Security-Policy.** Derived from what the pages actually load, not
from a template:

* scripts come from this origin and nowhere else — three ``<script src>`` in
  ``templates/base.html``, no inline script, no inline event handler (a static
  scan in ``tests/test_browser_policy.py`` keeps it that way), and htmx
  configured never to evaluate (``static/js/app.js``). No ``'unsafe-inline'``
  and no ``'unsafe-eval'`` in ``script-src``;
* **inline styles are still allowed, deliberately and for now.** Seven
  ``style=`` attributes set a bar width or a timeline reach from data, and
  Playwright's visual masks inject styles as well; moving them to classes is a
  CSS task of its own. Script execution is hardened; style is not yet;
* fonts, images, and every ``fetch``/htmx/``sendBeacon`` request are same-origin;
* no plugins, no ``<base>``, forms submit only here, and no page may be framed
  (``X-Frame-Options: DENY`` stays as well — the two agree, and the older header
  still covers an older browser).

**Permissions-Policy.** Powerful browser features the application never uses,
switched off for its pages and anything they could ever embed. Only feature
names Chromium recognises: an unknown one is a console error on every page.
The clipboard is *not* here — the copy buttons use it.

**HTML only, and never over a policy a view chose.** A served document —
an inline PDF, a thumbnail — carries its own, stricter CSP
(``app/documents/inline.py``), and a CSV or a health check is not a page. So
this sets its two headers only on ``text/html`` responses, htmx fragments
included, and only where the view has not already set one.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest, HttpResponse

#: Directive by directive, so a change reads as the one line it is.
CONTENT_SECURITY_POLICY_DIRECTIVES: tuple[tuple[str, str], ...] = (
    ("default-src", "'self'"),
    ("script-src", "'self'"),
    ("style-src", "'self' 'unsafe-inline'"),
    ("img-src", "'self'"),
    ("font-src", "'self'"),
    ("connect-src", "'self'"),
    ("object-src", "'none'"),
    ("base-uri", "'none'"),
    ("form-action", "'self'"),
    ("frame-ancestors", "'none'"),
)

CONTENT_SECURITY_POLICY = "; ".join(
    f"{directive} {sources}" for directive, sources in CONTENT_SECURITY_POLICY_DIRECTIVES
)

#: Features this application never asks the browser for.
DISABLED_FEATURES: tuple[str, ...] = (
    "accelerometer",
    "camera",
    "display-capture",
    "geolocation",
    "gyroscope",
    "hid",
    "magnetometer",
    "microphone",
    "midi",
    "payment",
    "screen-wake-lock",
    "serial",
    "usb",
    "xr-spatial-tracking",
)

PERMISSIONS_POLICY = ", ".join(f"{feature}=()" for feature in DISABLED_FEATURES)


def is_html(response: HttpResponse) -> bool:
    media_type = response.get("Content-Type", "").split(";", 1)[0].strip().lower()
    return media_type == "text/html"


class BrowserPolicyMiddleware:
    """Content-Security-Policy and Permissions-Policy on every HTML page."""

    def __init__(self, get_response: Any) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        if is_html(response):
            # setdefault, as `PrivateResponseMiddleware` does: a view that has
            # decided its own policy keeps it.
            response.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
            response.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
        return response
