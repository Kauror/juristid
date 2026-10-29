"""Security/resilience review 2026-10-29, WS1 assessor A: the adversarial matrix.

Every cell is a real request through the whole middleware stack under
`AUTH_MODE=shared_gate` (the production mode). The expectations are the
documented boundaries (docs/adr/0004, 0016, 0034, 0145, 0181, 0186). A cell
that fails is a finding; every cell here PASSES on 359e34bf, so this file is
the executed matrix and the "boundaries verified sound" evidence.

Actors
  anon        nobody: no gate, no session
  gate        passed the gate, no persona
  specialist  gate + SPECIALIST persona
  head        gate + DEPARTMENT_HEAD persona
  admin       gate + verified administrator session (full admin, no persona)
  tech        gate + verified technical ADMINISTRATOR (no persona)
  stale_admin an ADMINISTRATOR account force-logged-in behind the gate (a
              persona the rule excludes) — the middleware must drop it
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from django.test import Client
from django.urls import reverse

from app.accounts import disclosures
from app.accounts.enums import Capability
from app.accounts.models import User
from tests import factories
from tests.admin_session import apply_admin_gate, full_admin, pass_gate, technical_admin, verified

GATE = "/konto/varav/"
CHOOSER = "/konto/kasutaja/"


@pytest.fixture(autouse=True)
def gate_settings(settings):
    apply_admin_gate(settings)
    settings.TRUST_CF_CONNECTING_IP = True
    return settings


@pytest.fixture
def world(db):
    """The accounts a crafted request would reach for."""
    specialist = factories.UserFactory()
    head = factories.DepartmentHeadFactory()
    inactive = factories.UserFactory(is_active=False)
    reader = factories.ReaderFactory()
    technical = factories.AdministratorFactory()
    superuser = factories.AdministratorFactory(is_superuser=True)
    admin_user, admin_secret = full_admin()
    tech_user, tech_secret = technical_admin()
    return {
        "specialist": specialist,
        "head": head,
        "inactive": inactive,
        "reader": reader,
        "technical": technical,
        "superuser": superuser,
        "admin": (admin_user, admin_secret),
        "tech": (tech_user, tech_secret),
    }


# -- actors -----------------------------------------------------------------------


def _anon(world: dict) -> Client:
    return Client()


def _gate(world: dict) -> Client:
    return pass_gate(Client())


def _persona(key: str) -> Callable[[dict], Client]:
    def make(world: dict) -> Client:
        client = pass_gate(Client())
        response = client.post(reverse("accounts:act_as"), {"user_id": str(world[key].pk)})
        assert response.status_code == 302 and response["Location"] != CHOOSER, response
        return client

    return make


def _verified(key: str) -> Callable[[dict], Client]:
    def make(world: dict) -> Client:
        user, secret = world[key]
        return verified(Client(), user, secret)

    return make


def _stale_admin(world: dict) -> Client:
    client = Client()
    client.force_login(world["technical"])
    return pass_gate(client)


ACTORS = {
    "anon": _anon,
    "gate": _gate,
    "specialist": _persona("specialist"),
    "head": _persona("head"),
    "admin": _verified("admin"),
    "tech": _verified("tech"),
    "stale_admin": _stale_admin,
}


# -- the routes and what each actor must get ---------------------------------------
#
# Expected values: an int status, or a (status, location-prefix) pair.
# "G" marks the gate redirect, "C" the persona-chooser redirect.

G = (302, GATE)
C = (302, CHOOSER)
HOME = (302, "/", "exact")


def _pk(world: dict, key: str) -> str:
    target = world[key]
    return str(target[0].pk if isinstance(target, tuple) else target.pk)


CELLS: list[tuple[str, str, str | Callable[[dict], str], dict[str, object], dict[str, object]]] = [
    # (method, path-or-builder, data, {actor: expected})
    # -- accounts: the gate and the persona ------------------------------------
    ("GET", "/konto/varav/", {}, {"anon": 200, "gate": HOME, "specialist": HOME, "admin": HOME}),
    ("GET", "/konto/kasutaja/", {}, {"anon": G, "gate": 200, "specialist": 200, "admin": 200}),
    ("GET", "/konto/kasutaja/vaheta/", {}, {"anon": G, "gate": 405, "specialist": 405}),
    (
        "POST",
        "/konto/kasutaja/vaheta/",
        {"user_id": lambda w: _pk(w, "specialist")},
        {
            "anon": G,
            "gate": (302, "/minu-asjad/"),
            "head": (302, "/minu-asjad/"),
            "admin": (302, "/minu-asjad/"),
        },
    ),
    (
        "POST",
        "/konto/kasutaja/vaheta/",
        {"user_id": lambda w: _pk(w, "inactive")},
        {"gate": C, "specialist": C, "admin": C},
    ),
    (
        "POST",
        "/konto/kasutaja/vaheta/",
        {"user_id": lambda w: _pk(w, "reader")},
        {"gate": C, "specialist": C, "admin": C},
    ),
    (
        "POST",
        "/konto/kasutaja/vaheta/",
        {"user_id": lambda w: _pk(w, "technical")},
        {"gate": C, "specialist": C, "admin": C},
    ),
    (
        "POST",
        "/konto/kasutaja/vaheta/",
        {"user_id": lambda w: _pk(w, "superuser")},
        {"gate": C, "specialist": C, "admin": C},
    ),
    (
        "POST",
        "/konto/kasutaja/vaheta/",
        # The full administrator's own account: a SPECIALIST that also holds
        # accounts.manage. It IS a persona candidate (docs/adr/0186: an
        # administrator who does department work holds a department role);
        # being chosen as a persona grants nothing administrative (see the
        # "sound" test at the end of this file).
        {"user_id": lambda w: _pk(w, "admin")},
        {"gate": (302, "/minu-asjad/"), "admin": (302, "/minu-asjad/")},
    ),
    ("POST", "/konto/kasutaja/vaheta/", {"user_id": "not-a-uuid"}, {"gate": C, "admin": C}),
    ("GET", "/konto/valju/", {}, {"anon": G, "gate": 405, "specialist": 405, "admin": 405}),
    ("POST", "/konto/valju/", {}, {"anon": G, "gate": HOME, "specialist": HOME, "admin": HOME}),
    # -- accounts: the dormant personal sign-in is absent under the gate ----------
    ("GET", "/konto/sisene/", {}, {"anon": G, "gate": 404, "specialist": 404, "admin": 404}),
    ("POST", "/konto/sisene/", {"email": "x@koda.ee", "password": "y"}, {"gate": 404}),
    ("GET", "/konto/sisene/kood/", {}, {"gate": 404, "admin": 404}),
    ("GET", "/konto/parool/unustasin/", {}, {"anon": G, "gate": 404, "admin": 404}),
    ("POST", "/konto/parool/unustasin/", {"email": "x@koda.ee"}, {"gate": 404}),
    ("GET", "/konto/parool/muuda/", {}, {"gate": 404, "specialist": 404, "admin": 404}),
    ("GET", "/konto/profiil/", {}, {"gate": 404, "specialist": 404, "admin": 404}),
    ("GET", "/konto/turvalisus/", {}, {"gate": 404, "specialist": 404, "admin": 404}),
    ("GET", "/konto/turvalisus/seadista/", {}, {"gate": 404, "specialist": 404, "admin": 404}),
    ("POST", "/konto/turvalisus/eemalda/", {}, {"gate": 404, "specialist": 404, "admin": 404}),
    ("GET", "/konto/kinnita/", {}, {"gate": 404, "specialist": 404, "admin": 404}),
    ("GET", "/konto/arendus-sisselogimine/", {}, {"anon": G, "gate": 404, "admin": 404}),
    # the link pages exist under the gate only with a usable administrator link
    ("GET", "/konto/aktiveeri/", {}, {"anon": G, "gate": 404, "specialist": 404, "admin": 404}),
    ("GET", "/konto/parool/taasta/", {}, {"anon": G, "gate": 404, "specialist": 404, "admin": 404}),
    ("POST", "/konto/aktiveeri/", {"password": "x", "password_again": "x"}, {"gate": 404}),
    # -- accounts: remembered blocks need a persona --------------------------------
    (
        "POST",
        "/konto/plokid/",
        {"key": lambda w: next(iter(disclosures.DISCLOSURES)), "open": "1"},
        {"anon": G, "gate": C, "specialist": 204, "admin": C},
    ),
    ("GET", "/konto/plokid/", {}, {"gate": C, "specialist": 405}),
    # -- Haldus → Kasutajad: only a verified administrator holding accounts.manage --
    (
        "GET",
        "/haldus/kasutajad/",
        {},
        {
            "anon": G,
            "gate": 404,
            "specialist": 404,
            "head": 404,
            "admin": 200,
            "tech": 200,
            "stale_admin": 404,
        },
    ),
    (
        "GET",
        "/haldus/kasutajad/uus/",
        {},
        {"gate": 404, "head": 404, "admin": 200, "stale_admin": 404},
    ),
    (
        "GET",
        lambda w: f"/haldus/kasutajad/{_pk(w, 'specialist')}/",
        {},
        {"gate": 404, "specialist": 404, "head": 404, "admin": 200},
    ),
    (
        "POST",
        lambda w: f"/haldus/kasutajad/{_pk(w, 'specialist')}/lulita-valja/",
        {"reason": "matrix"},
        {"anon": G, "gate": 404, "specialist": 404, "head": 404, "stale_admin": 404},
    ),
    (
        "POST",
        lambda w: f"/haldus/kasutajad/{_pk(w, 'specialist')}/teine-tegur/",
        {},
        {"gate": 404, "head": 404, "stale_admin": 404},
    ),
    (
        "POST",
        lambda w: f"/haldus/kasutajad/{_pk(w, 'specialist')}/link/",
        {},
        {"gate": 404, "head": 404, "stale_admin": 404},
    ),
    # -- Haldus shell ----------------------------------------------------------------
    (
        "GET",
        "/haldus/",
        {},
        {"anon": G, "gate": 404, "specialist": 200, "head": 200, "admin": 200, "tech": 200},
    ),
    (
        "GET",
        "/haldus/susteem/",
        {},
        {
            "gate": 404,
            "specialist": 404,
            "head": 404,
            "admin": 404,
            "tech": 200,
            "stale_admin": 404,
        },
    ),
    ("GET", "/haldus/lovable/", {}, {"gate": 404, "head": 404, "admin": 404, "tech": 200}),
    ("POST", "/haldus/lovable/kontrolli/", {}, {"anon": G, "gate": 404, "head": 404, "admin": 404}),
    (
        "GET",
        "/haldus/osakond/",
        {},
        {"gate": 404, "specialist": 404, "head": 200, "admin": 404, "tech": 404},
    ),
    # -- the step-up pages: behind the gate, never a sign-in to the application -----
    (
        "GET",
        "/haldus/sisene/",
        {},
        {"anon": G, "gate": 200, "specialist": 200, "admin": (302, "/haldus/")},
    ),
    (
        "GET",
        "/haldus/sisene/kood/",
        {},
        {"anon": G, "gate": (302, "/haldus/sisene/"), "admin": (302, "/haldus/")},
    ),
    ("POST", "/haldus/sisene/kood/", {"code": "000000"}, {"gate": (302, "/haldus/sisene/")}),
    ("GET", "/haldus/sisene/seadista/", {}, {"gate": (302, "/haldus/sisene/")}),
    ("GET", "/haldus/kinnita/", {}, {"gate": (302, "/haldus/sisene/"), "admin": 200}),
    ("GET", "/haldus/lopeta/", {}, {"gate": 405, "admin": 405}),
    ("POST", "/haldus/lopeta/", {}, {"anon": G, "gate": HOME, "admin": HOME}),
    # -- the Django admin has no browser entrance under the gate --------------------
    (
        "GET",
        "/admin/",
        {},
        {
            "anon": G,
            "gate": (302, "/admin/login/"),
            "specialist": (302, "/admin/login/"),
            "admin": (302, "/admin/login/"),
            "tech": (302, "/admin/login/"),
            "stale_admin": (302, "/admin/login/"),
        },
    ),
    (
        "GET",
        "/admin/login/",
        {},
        {"anon": G, "gate": C, "specialist": HOME, "admin": C, "tech": C, "stale_admin": C},
    ),
    (
        "POST",
        "/admin/login/",
        {"username": "x", "password": "y"},
        {"gate": C, "specialist": HOME, "tech": C},
    ),
    (
        "GET",
        "/admin/accounts/user/",
        {},
        {
            "gate": (302, "/admin/login/"),
            "admin": (302, "/admin/login/"),
            "tech": (302, "/admin/login/"),
        },
    ),
    # -- platform exemptions -----------------------------------------------------------
    ("GET", "/healthz", {}, {"anon": 200}),
    ("GET", "/static/does-not-exist.css", {}, {"anon": 404}),
    ("GET", "/healthzz", {}, {"anon": 404}),
]


def _cases():
    for index, (method, path, data, expected) in enumerate(CELLS):
        for actor, want in expected.items():
            label = path if isinstance(path, str) else f"<{index}>"
            yield pytest.param(method, path, data, actor, want, id=f"{actor}:{method}:{label}")


@pytest.mark.django_db
@pytest.mark.parametrize("method,path,data,actor,want", list(_cases()))
def test_matrix(world, method, path, data, actor, want):
    client = ACTORS[actor](world)
    target = path(world) if callable(path) else path
    payload = {k: (v(world) if callable(v) else v) for k, v in data.items()}
    response = client.post(target, payload) if method == "POST" else client.get(target)
    if isinstance(want, tuple):
        status, prefix = want[0], want[1]
        assert response.status_code == status, (actor, method, target, response.status_code)
        location = response["Location"].split("?")[0]
        if len(want) == 3:
            assert location == prefix, (actor, method, target, response["Location"])
        else:
            assert location.startswith(prefix), (actor, method, target, response["Location"])
    else:
        assert response.status_code == want, (actor, method, target, response.status_code)


# -- sound: an excluded persona is dropped, not served --------------------------------


@pytest.mark.django_db
def test_sound_a_staff_persona_planted_in_the_session_is_dropped_at_the_first_request(world):
    client = _stale_admin(world)
    response = client.get("/haldus/")
    assert response.status_code == 404
    assert "_auth_user_id" not in client.session


# -- sound: the verified administrator reads no business content (docs/adr/0181) ------


@pytest.mark.django_db
def test_sound_a_verified_administrator_without_a_persona_reads_no_register(world):
    client = _verified("tech")(world)
    assert client.get("/haldus/kasutajad/").status_code == 200
    page = client.get("/teemad/")
    assert page.status_code == 302 and page["Location"].startswith(CHOOSER)


# -- sound: step-up state is bound to the gate pass, not copyable into another gate session ---


@pytest.mark.django_db
def test_sound_a_verified_session_copied_into_a_fresh_gate_session_administers_nothing(world):
    from app.accounts import admin_session

    source = _verified("admin")(world)
    state = dict(source.session[admin_session.SESSION_STATE])
    other = pass_gate(Client())
    session = other.session
    session[admin_session.SESSION_STATE] = state
    session.save()
    assert other.get("/haldus/kasutajad/").status_code == 404


# -- sound: a persona's capability never substitutes for the step-up ----------------------


@pytest.mark.django_db
def test_sound_a_persona_holding_accounts_manage_still_needs_the_step_up(world):
    from app.accounts import capabilities

    empowered = factories.UserFactory(
        capability_overrides={Capability.MANAGE_ACCOUNTS.value: capabilities.ALLOW}
    )
    client = pass_gate(Client())
    assert (
        client.post(reverse("accounts:act_as"), {"user_id": str(empowered.pk)}).status_code == 302
    )
    assert client.get("/haldus/kasutajad/").status_code == 404
    assert (
        client.post(
            f"/haldus/kasutajad/{world['specialist'].pk}/lulita-valja/", {"reason": "x"}
        ).status_code
        == 404
    )
    assert User.objects.get(pk=world["specialist"].pk).is_active
