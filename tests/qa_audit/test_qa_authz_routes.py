"""QA authz (2026-10-14): every routed URL, fired by the personas who must learn nothing.

Walks Django's own resolver (no hand-kept list) and requests every route with
GET and POST as:

* an anonymous visitor in ``AUTH_MODE=none`` (development/CI mode);
* a visitor who has not passed the shared gate;
* a visitor past the shared gate with no persona (`DepartmentViewer`);
* a session in shared-gate mode trying the dormant ``local_password`` and
  account-administration routes (ADR 0145: 404 in every other mode).

Path parameters are filled with a real, NORMAL Matter (and its children) so a
refusal is a refusal, not a 404 for a missing row. Until docs/adr/0181 the gate
reader *did* reach the `@gate_required` pages; one internal read policy retired
them, and that visitor now reaches no business page at all.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from django.test import Client
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.utils import timezone

from app.accounts.enums import AuthMode
from app.core.enums import Visibility
from app.submissions.enums import SubmissionStatus
from tests import factories
from tests.gate import PASSWORD, apply_shared_gate

pytestmark = pytest.mark.django_db

WORKLOG = Path(r"C:\CC\_worklogs\independent-qa-2026-10-14\agents\authz")

#: Answer with no identity in every mode, by design.
PUBLIC = {"core:healthz", "core:favicon"}

#: The routes a shared-gate reader with no persona may read. Empty since
#: docs/adr/0181: the eighteen `@gate_required` pages (Osakond, Statistika,
#: Arvamused, the document routes) need a persona like every other.
GATE_READABLE: set[str] = set()

#: Routes that exist only for ``AUTH_MODE=local_password`` (ADR 0145).
LOCAL_PASSWORD_ONLY_PREFIXES = (
    "accounts:sign_in",
    "accounts:activate",
    "accounts:forgot_password",
    "accounts:reset_password",
    "accounts:change_password",
    "accounts:profile",
    "accounts:security",
    "accounts:reauthenticate",
    "account_admin:",
)


def _routes():
    def walk(resolver, prefix="", namespace=""):
        for pattern in resolver.url_patterns:
            if isinstance(pattern, URLResolver):
                ns = namespace + (f"{pattern.namespace}:" if pattern.namespace else "")
                yield from walk(pattern, prefix + str(pattern.pattern), ns)
            elif isinstance(pattern, URLPattern) and pattern.name:
                yield namespace + pattern.name, pattern

    return [
        (name, pattern) for name, pattern in walk(get_resolver()) if not name.startswith("admin:")
    ]


@pytest.fixture
def ids(db, capture_evidence):
    owner = factories.UserFactory()
    matter = factories.MatterFactory(owner=owner, visibility=Visibility.NORMAL)
    version = capture_evidence(matter, b"%PDF-1.4 avalik", "avalik.pdf", "application/pdf")
    submission = factories.SubmissionFactory(
        matter=matter, status=SubmissionStatus.SENT, sent_at=timezone.now(), final_version=version
    )
    return {
        "pk": matter.pk,
        "matter_id": matter.pk,
        "_version": version.pk,
        "_document": version.document_id,
        "_submission": submission.pk,
        "_owner": owner.pk,
    }


def _url(name: str, pattern: URLPattern, ids) -> str:
    kwargs = {}
    for key, converter in pattern.pattern.converters.items():
        type_name = type(converter).__name__
        if type_name == "UUIDConverter":
            if name.startswith("documents:") and key == "pk":
                kwargs[key] = (
                    ids["_document"]
                    if name
                    in {
                        "documents:document_detail",
                        "documents:add_version",
                        "documents:change_role",
                        "documents:rename",
                        "documents:remove",
                    }
                    else ids["_version"]
                )
            elif name.startswith("submissions:") and key == "pk":
                kwargs[key] = ids["_submission"]
            elif name in {"matters:person_work", "account_admin:detail"} or name.startswith(
                "account_admin:"
            ):
                kwargs[key] = ids["_owner"]
            elif key in ids:
                kwargs[key] = ids[key]
            else:
                kwargs[key] = uuid.uuid4()
        elif type_name == "SlugConverter":
            kwargs[key] = "teemad"
        else:
            kwargs[key] = "x"
    return reverse(name, kwargs=kwargs)


#: Routes that end or change the session; requested separately, never mid-sweep.
SESSION_ENDING = {"accounts:sign_out", "accounts:act_as", "accounts:dev_login"}


def _table(client: Client, ids, only=None) -> dict[str, tuple[str, int, int, str]]:
    rows = {}
    for name, pattern in _routes():
        if name in SESSION_ENDING or (only is not None and not name.startswith(only)):
            continue
        url = _url(name, pattern, ids)
        get = client.get(url)
        post = client.post(url, {})
        rows[name] = (url, get.status_code, post.status_code, get.headers.get("Location", ""))
    return rows


def _dump(label: str, rows) -> None:
    """Evidence for the QA worklog, only on the machine that has one (never in CI)."""
    if not WORKLOG.is_dir():
        return
    with (WORKLOG / f"routes-{label}.tsv").open("w", encoding="utf-8") as handle:
        for name, (url, get, post, location) in sorted(rows.items()):
            handle.write(f"{name}\t{url}\t{get}\t{post}\t{location}\n")


def test_anonymous_in_mode_none_reaches_nothing(client, settings, ids):
    settings.AUTH_MODE = AuthMode.NONE
    rows = _table(client, ids)
    _dump("anonymous-mode-none", rows)
    leaks = {
        name: row
        for name, row in rows.items()
        if name not in PUBLIC and (row[1] == 200 or row[2] in {200, 201, 204})
    }
    # Pages that answer 200 to anybody by design in mode none are listed here
    # only once they are proven content-free; anything else is a leak.
    allowed_without_identity = {
        "core:home",  # a static «sign in» doorway, no data (core/home.html)
        "core:design_tokens",
        "core:release_notes",
        "core:keyboard_shortcuts",  # the shortcut reference, no data
        "accounts:dev_login",
        "accounts:choose_persona",
        "accounts:shared_gate",
    }
    unexplained = {k: v for k, v in leaks.items() if k not in allowed_without_identity}
    assert unexplained == {}


def test_before_the_gate_everything_is_the_gate(client, settings, ids):
    apply_shared_gate(settings)
    rows = _table(client, ids)
    _dump("shared-gate-not-passed", rows)
    gate = reverse("accounts:shared_gate")
    wrong = {
        name: row
        for name, row in rows.items()
        if name not in PUBLIC
        and name != "accounts:shared_gate"
        and not (row[1] == 302 and row[3].startswith(gate))
    }
    assert wrong == {}


def test_department_viewer_reads_no_business_page_and_writes_nothing(client, settings, ids):
    apply_shared_gate(settings)
    assert client.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302
    rows = _table(client, ids)
    _dump("department-viewer", rows)

    read = {name for name, row in rows.items() if row[1] == 200}
    wrote = {name: row for name, row in rows.items() if row[2] in {200, 201, 204}}
    # The pages `@gate_required` opened to them before docs/adr/0181 now send
    # them to choose a persona.
    chooser = reverse("accounts:choose_persona")
    for name in ("matters:department", "reporting:overview", "documents:download"):
        assert rows[name][1] == 302 and rows[name][3].startswith(chooser), rows[name]
    unexpected_reads = (
        read
        - GATE_READABLE
        - PUBLIC
        - {
            # Content-free pages: the persona chooser, the gate, the release notes,
            # the keyboard-shortcut reference.
            "accounts:choose_persona",
            "accounts:shared_gate",
            "core:release_notes",
            "core:keyboard_shortcuts",
            "core:design_tokens",
            # The administrator sign-in (docs/adr/0186): an address and a
            # password field, before any persona — the step-up is not one.
            "haldus:sign_in",
        }
    )
    assert unexpected_reads == set()
    # A POST that answers 200 is either a refusal re-rendered or a write; the
    # persona chooser and the gate's own form are the only forms they may post.
    assert (
        set(wrote)
        # The administrator sign-in re-renders its one refusal; it writes nothing.
        - {
            "accounts:shared_gate",
            "accounts:choose_persona",
            "core:release_notes",
            "haldus:sign_in",
        }
        - PUBLIC
        - (GATE_READABLE)
        == set()
    ), wrote


def test_local_password_and_account_admin_are_dormant_behind_the_gate(
    client, settings, ids, specialist
):
    apply_shared_gate(settings)
    assert client.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302
    client.post(reverse("accounts:act_as"), {"user_id": str(specialist.pk)})
    rows = _table(client, ids, only=LOCAL_PASSWORD_ONLY_PREFIXES)
    assert len(rows) >= 15
    _dump("shared-gate-specialist", rows)
    alive = {
        name: row
        for name, row in rows.items()
        if name.startswith(LOCAL_PASSWORD_ONLY_PREFIXES) and (row[1] != 404 or row[2] != 404)
    }
    assert alive == {}
