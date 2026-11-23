"""The refusal guard sits outside the method restriction, on every view (HR-16).

`app/core/decorators.py` (`business_write_required`) fixes the composition:
``@login_required`` outermost, then the refusal guard, then
``@require_http_methods``. Placed the other way round, a non-writer who tries the
wrong verb is answered 405 by the method check before the guard ever runs, and a
405 confirms the endpoint exists to exactly the caller who should learn nothing.

Nothing tested the order, so the inverted form was copied into new code (QA26-DUP-14
grew from five sites to nine). The first test walks every module under ``app/`` with
``ast`` and names each ``file:function`` that breaks it; the rest show the answer a
reader and a writer actually get from a few of the routes that were fixed.
"""

from __future__ import annotations

import ast
import pathlib
import uuid

import pytest
from django.urls import reverse

from tests import factories

APP_ROOT = pathlib.Path(__file__).resolve().parent.parent / "app"

# Decorators that answer a disallowed verb with 405.
METHOD_RESTRICTIONS = frozenset(
    {"require_http_methods", "require_GET", "require_POST", "require_safe"}
)

# Guards whose refusal (a 404) must be the answer whatever verb is tried.
REFUSAL_GUARDS = frozenset(
    {"business_write_required", "account_admin_required", "reference_data_required"}
)


def _decorator_name(node: ast.expr) -> str:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Attribute):
        return target.attr
    if isinstance(target, ast.Name):
        return target.id
    return ""


def order_violations(root: pathlib.Path = APP_ROOT) -> list[str]:
    """Every ``file:function`` whose decorators put a method check or the guard in
    the wrong place. A decorator listed earlier is applied outermost."""
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            names = [_decorator_name(d) for d in node.decorator_list]
            where = f"{path.relative_to(root.parent).as_posix()}:{node.lineno}:{node.name}"
            for index, name in enumerate(names):
                if name not in REFUSAL_GUARDS:
                    continue
                outer = names[:index]
                inverted = sorted(METHOD_RESTRICTIONS.intersection(outer))
                if inverted:
                    found.append(f"{where} — {', '.join(inverted)} above {name}")
                if name == "business_write_required" and "login_required" in names[index + 1 :]:
                    found.append(f"{where} — login_required below {name}")
    return found


def test_no_view_lets_the_method_check_answer_before_the_refusal_guard():
    violations = order_violations()
    assert not violations, (
        "Put the refusal guard above (outside) the method restriction, and "
        "@login_required above both — see business_write_required in "
        "app/core/decorators.py:\n  " + "\n  ".join(violations)
    )


def test_the_scan_recognises_an_inverted_view(tmp_path):
    """The walker itself: an inverted view is named, a correct one is not."""
    package = tmp_path / "app"
    package.mkdir()
    (package / "views.py").write_text(
        "@login_required\n"
        "@require_http_methods(['POST'])\n"
        "@business_write_required\n"
        "def inverted(request): ...\n"
        "\n"
        "@login_required\n"
        "@decorators.business_write_required\n"
        "@require_POST\n"
        "def correct(request): ...\n",
        encoding="utf-8",
    )

    assert order_violations(package) == [
        "app/views.py:4:inverted — require_http_methods above business_write_required"
    ]


# ---------------------------------------------------------------------------
# What the fixed routes answer
# ---------------------------------------------------------------------------

FIXED_ROUTES = [
    ("register_watch:decide", {"pk": uuid.uuid4()}),
    ("register_watch:dismiss_all", {"pk": uuid.uuid4()}),
    ("register_watch:phrase", {"pk": uuid.uuid4()}),
    ("register_watch:candidate_decide", {"pk": uuid.uuid4()}),
    ("register_watch:agenda_decide", {"pk": uuid.uuid4()}),
    ("register_watch:candidate_audience", {}),
    ("haldus:letters_decide", {"pk": uuid.uuid4()}),
    ("reporting:classification_decide", {"pk": uuid.uuid4()}),
    ("reporting:classification_accept_page", {}),
]


@pytest.mark.django_db
@pytest.mark.parametrize(("name", "kwargs"), FIXED_ROUTES)
@pytest.mark.parametrize("verb", ["get", "put"])
def test_a_reader_gets_the_refusal_whatever_verb(client, name, kwargs, verb):
    client.force_login(factories.ReaderFactory())
    assert getattr(client, verb)(reverse(name, kwargs=kwargs)).status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize(("name", "kwargs"), FIXED_ROUTES)
@pytest.mark.parametrize("verb", ["get", "put"])
def test_a_writer_still_gets_405_for_a_wrong_verb(client, name, kwargs, verb):
    client.force_login(factories.UserFactory())
    assert getattr(client, verb)(reverse(name, kwargs=kwargs)).status_code == 405


@pytest.mark.django_db
def test_a_writer_still_posts(client):
    """The right verb from a writer reaches the view as before."""
    client.force_login(factories.UserFactory())
    response = client.post(reverse("reporting:classification_accept_page"), {})
    assert response.status_code == 302


@pytest.mark.django_db
def test_a_reader_posting_is_refused_as_before(client):
    client.force_login(factories.ReaderFactory())
    response = client.post(reverse("reporting:classification_accept_page"), {})
    assert response.status_code == 404
