"""The browser's own limits on an HTML page (ENG-124).

Defence in depth: no working XSS was found, and nothing here claims to fix one.
What is tested is that the limits are *there* on every page, strict where they
must be — ``script-src`` with no ``'unsafe-inline'`` and no ``'unsafe-eval'`` —
absent where they would mean nothing, and never laid over the stricter policy a
served document already carries. And that the code the pages ship keeps inside
them: no inline handler or script in a template, no evaluated string in a
first-party script, htmx configured never to evaluate.

Whether a real browser actually runs every workflow under the policy is the
browser suite's question: `e2e/conftest.py` fails any browser test whose page
logged a Content-Security-Policy violation.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import reverse

from app.core.browser_policy import (
    CONTENT_SECURITY_POLICY,
    PERMISSIONS_POLICY,
    BrowserPolicyMiddleware,
)
from app.documents.enums import DocumentRole
from app.documents.services import add_evidence_version, create_document
from tests import factories

ROOT = Path(settings.BASE_DIR)

#: The policy, spelled out here rather than imported, so that a change to the
#: middleware's constant is a change this file has to agree to.
EXPECTED_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self'; font-src 'self'; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
EXPECTED_PERMISSIONS_POLICY = (
    "accelerometer=(), camera=(), display-capture=(), geolocation=(), gyroscope=(), "
    "hid=(), magnetometer=(), microphone=(), midi=(), payment=(), screen-wake-lock=(), "
    "serial=(), usb=(), xr-spatial-tracking=()"
)


def directives(policy: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for part in policy.split(";"):
        name, _, sources = part.strip().partition(" ")
        parsed[name] = sources
    return parsed


# -- the policy itself ----------------------------------------------------------


def test_the_policy_is_the_one_decided():
    assert CONTENT_SECURITY_POLICY == EXPECTED_CSP
    assert PERMISSIONS_POLICY == EXPECTED_PERMISSIONS_POLICY


def test_script_execution_is_strict():
    policy = directives(CONTENT_SECURITY_POLICY)
    assert policy["script-src"] == "'self'"
    assert "'unsafe-inline'" not in policy["script-src"]
    assert "'unsafe-eval'" not in policy["script-src"]
    assert "'unsafe-eval'" not in CONTENT_SECURITY_POLICY
    assert policy["object-src"] == "'none'"
    assert policy["base-uri"] == "'none'"
    assert policy["frame-ancestors"] == "'none'"
    assert policy["form-action"] == "'self'"
    assert policy["default-src"] == "'self'"
    # Inline *styles* are allowed for now, and only styles: seven `style=`
    # attributes carry a width from data. Recorded so the day they go, this
    # line is the one that changes.
    assert policy["style-src"] == "'self' 'unsafe-inline'"


def test_nothing_the_application_uses_is_switched_off():
    """The copy buttons write to the clipboard; nothing else here asks for more."""
    assert "clipboard" not in PERMISSIONS_POLICY
    for feature in ("camera", "microphone", "geolocation", "payment", "usb"):
        assert f"{feature}=()" in PERMISSIONS_POLICY


# -- on the wire ----------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/osakond/", "/minu-asjad/", "/teemad/", "/otsing/?q=eelnou"])
def test_a_page_carries_both_policies(signed_in, path):
    response = signed_in.get(path)
    assert response.status_code == 200, path
    assert response["Content-Type"].startswith("text/html")
    assert response["Content-Security-Policy"] == EXPECTED_CSP
    assert response["Permissions-Policy"] == EXPECTED_PERMISSIONS_POLICY


@pytest.mark.django_db
def test_the_matter_page_and_its_fragments_carry_it(signed_in, normal_matter):
    page = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}))
    assert page.status_code == 200
    assert page["Content-Security-Policy"] == EXPECTED_CSP

    # An htmx swap is HTML too. The browser enforces the policy of the page it
    # lands in, so this changes nothing there — but a fragment opened on its
    # own, by address, is then a page like any other.
    fragment = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}), HTTP_HX_REQUEST="true"
    )
    assert fragment["Content-Type"].startswith("text/html")
    assert fragment["Content-Security-Policy"] == EXPECTED_CSP


@pytest.mark.django_db
def test_the_sign_in_and_error_pages_carry_it(client, settings):
    settings.DEV_LOGIN_ENABLED = True
    sign_in = client.get(reverse("accounts:dev_login"))
    assert sign_in["Content-Security-Policy"] == EXPECTED_CSP

    missing = client.get("/ei-ole-sellist-lehte/")
    assert missing.status_code in {302, 404}
    if missing["Content-Type"].startswith("text/html"):
        assert missing["Content-Security-Policy"] == EXPECTED_CSP


@pytest.mark.django_db
def test_the_legacy_queues_carry_it(client):
    client.force_login(factories.AdministratorFactory())
    for route in ("legacy_import:opinion_queue", "legacy_import:review_queue"):
        response = client.get(reverse(route))
        assert response.status_code == 200, route
        assert response["Content-Security-Policy"] == EXPECTED_CSP, route
        # And the handlers that the policy would have refused are gone.
        assert "onchange=" not in response.content.decode()


@pytest.mark.django_db
def test_the_existing_protections_are_all_still_there(signed_in):
    response = signed_in.get("/minu-asjad/")
    assert response["X-Frame-Options"] == "DENY"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Referrer-Policy"] == "same-origin"
    assert response["Cross-Origin-Opener-Policy"] == "same-origin"
    assert response["Cache-Control"].startswith("no-store")


# -- where it does not belong ---------------------------------------------------


@pytest.mark.django_db
def test_a_csv_export_is_not_a_page(signed_in):
    response = signed_in.get(reverse("reporting:export", kwargs={"slug": "teemad"}))
    assert response.status_code == 200
    assert not response["Content-Type"].startswith("text/html")
    assert "Content-Security-Policy" not in response
    assert "Permissions-Policy" not in response


@pytest.mark.django_db
def test_the_health_check_is_not_a_page(client):
    response = client.get("/healthz")
    assert not response["Content-Type"].startswith("text/html")
    assert "Content-Security-Policy" not in response


@pytest.mark.django_db
def test_a_served_document_keeps_its_own_stricter_policy(signed_in, evidence_root, normal_matter):
    document = create_document(
        matter=normal_matter, title="kaaskiri.pdf", role=DocumentRole.INCOMING_AUTHORITY
    )
    version = add_evidence_version(
        document=document,
        content=b"%PDF-1.4\n%%EOF\n",
        original_filename="kaaskiri.pdf",
        mime_type="application/pdf",
    )

    response = signed_in.get(reverse("documents:open", kwargs={"pk": version.pk}))

    assert response.status_code == 200
    policy = directives(response["Content-Security-Policy"])
    assert policy["script-src"] == "'none'"
    assert policy["default-src"] == "'none'"
    assert response["Content-Security-Policy"] != EXPECTED_CSP


def test_a_view_that_chose_its_own_policy_keeps_it():
    """setdefault: an HTML response that already has a policy is left alone."""
    own = "default-src 'none'; sandbox"

    def view(request):
        response = HttpResponse("<p>oma</p>")
        response["Content-Security-Policy"] = own
        response["Permissions-Policy"] = "camera=(self)"
        return response

    response = BrowserPolicyMiddleware(view)(RequestFactory().get("/"))
    assert response["Content-Security-Policy"] == own
    assert response["Permissions-Policy"] == "camera=(self)"


def test_only_html_gets_it():
    def view(request):
        return HttpResponse(b"{}", content_type="application/json")

    response = BrowserPolicyMiddleware(view)(RequestFactory().get("/"))
    assert "Content-Security-Policy" not in response
    assert "Permissions-Policy" not in response


# -- what the pages ship keeps inside the policy ---------------------------------

TEMPLATES = sorted((ROOT / "templates").rglob("*.html"))

#: A template that genuinely needs one of these is listed here, with the
#: reason, and nowhere else. There are none.
DOCUMENTED_EXCEPTIONS: dict[str, str] = {}

#: `{# … #}` and `{% comment %}…{% endcomment %}` are prose, not markup: several
#: explain in words why a handler is *not* used.
_TEMPLATE_COMMENTS = re.compile(r"{#.*?#}|{%\s*comment\s*%}.*?{%\s*endcomment\s*%}", re.DOTALL)
_TAGS = re.compile(r"<[a-zA-Z][^>]*>", re.DOTALL)
_HANDLER = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)
_INLINE_SCRIPT = re.compile(r"<script(?![^>]*\ssrc=)[^>]*>", re.IGNORECASE)


def _markup(path: Path) -> str:
    return _TEMPLATE_COMMENTS.sub("", path.read_text(encoding="utf-8"))


def test_the_templates_were_found():
    assert len(TEMPLATES) > 100


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda path: path.relative_to(ROOT).as_posix())
def test_no_template_carries_code_the_policy_would_refuse(path):
    """No `on*=` handler, no inline `<script>`, no `javascript:` URL, no `hx-on`.

    Each of these is refused by `script-src 'self'` — silently, in the
    browser, where the feature it carried simply stops working. So each is
    refused here first, where it fails a build instead of a person's click.
    """
    name = path.relative_to(ROOT).as_posix()
    if name in DOCUMENTED_EXCEPTIONS:
        pytest.skip(DOCUMENTED_EXCEPTIONS[name])
    markup = _markup(path)
    for tag in _TAGS.findall(markup):
        assert not _HANDLER.search(tag), f"{name}: inline event handler in {tag[:120]!r}"
        assert "hx-on" not in tag, f"{name}: hx-on is evaluated JavaScript: {tag[:120]!r}"
        assert "javascript:" not in tag.lower(), f"{name}: javascript: URL in {tag[:120]!r}"
    assert not _INLINE_SCRIPT.search(markup), f"{name}: an inline <script>"


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda path: path.relative_to(ROOT).as_posix())
def test_no_htmx_attribute_asks_for_evaluation(path):
    """`hx-trigger` filters and `js:` values are compiled strings too."""
    markup = _markup(path)
    for trigger in re.findall(r'hx-trigger="([^"]*)"', markup, re.DOTALL):
        assert "[" not in trigger, f"{path.name}: a trigger filter is evaluated: {trigger!r}"
    for attribute in re.findall(
        r"hx-(?:vals|vars|headers)='([^']*)'|hx-(?:vals|vars|headers)=\"([^\"]*)\"", markup
    ):
        value = "".join(attribute).strip()
        assert not value.startswith(("js:", "javascript:")), f"{path.name}: {value!r}"
    assert "hx-vars" not in markup, f"{path.name}: hx-vars is always evaluated"


FIRST_PARTY_SCRIPTS = sorted((ROOT / "static" / "js").glob("*.js"))


@pytest.mark.parametrize("path", FIRST_PARTY_SCRIPTS, ids=lambda path: path.name)
def test_no_first_party_script_evaluates_a_string(path):
    source = path.read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.DOTALL)
    assert not re.search(r"\beval\s*\(", code), path.name
    assert not re.search(r"\bnew\s+Function\s*\(|[^.\w]Function\s*\(", code), path.name
    assert not re.search(r"\bset(?:Timeout|Interval)\s*\(\s*['\"`]", code), path.name
    assert "document.write" not in code, path.name


def test_htmx_is_configured_never_to_evaluate():
    """The configuration, read from the script every page loads."""
    source = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    for line in (
        "window.htmx.config.allowEval = false;",
        "window.htmx.config.allowScriptTags = false;",
        "window.htmx.config.includeIndicatorStyles = false;",
    ):
        assert line in source, line
