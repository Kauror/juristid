"""Role questions go through `acting_role`; each route is registered once.

ENG-128: three sites compared the raw `User.role` — the historical
reconciliation queue's gate, the quality page's link to that queue, and who may
grant break-glass. They agreed with `acting_role` for every person who can reach
them today, and would not have followed it the day it learns about another kind
of non-person. They now ask `may_review_historical_matches` and
`is_department_head`.

ENG-134: `/teemad/<pk>/kustuta/` was registered twice (a merge artefact) and
`/osakond/` carried a second name, `matters:overview`, for a caller that no
longer existed.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import URLPattern, URLResolver, get_resolver, resolve, reverse

from app.accounts.services import grant_break_glass
from app.core.authorization import DepartmentViewer, may_review_historical_matches
from app.core.errors import DomainError
from tests import factories

pytestmark = pytest.mark.django_db


# --- ENG-128 ---------------------------------------------------------------


def test_only_an_acting_administrator_may_review_historical_matches(
    administrator, specialist, department_head
):
    assert may_review_historical_matches(administrator)
    assert not may_review_historical_matches(specialist)
    assert not may_review_historical_matches(department_head)
    assert not may_review_historical_matches(None)
    assert not may_review_historical_matches(DepartmentViewer())

    administrator.is_active = False
    assert not may_review_historical_matches(administrator)


def test_the_queue_and_its_link_answer_the_same_question(client, administrator, specialist):
    from django.utils import timezone

    from app.reporting.context import ReportingContext, parse_period
    from app.reporting.selectors.quality import can_open_review_queue

    url = reverse("legacy_import:review_queue")
    client.force_login(specialist)
    assert client.get(url).status_code == 404
    client.force_login(administrator)
    assert client.get(url).status_code == 200

    for user, expected in ((administrator, True), (specialist, False)):
        context = ReportingContext(
            viewer=user,
            period=parse_period("koik", timezone.localdate()),
            today=timezone.localdate(),
            now=timezone.now(),
        )
        assert can_open_review_queue(context) is expected


def test_break_glass_is_granted_by_an_acting_department_head_only(department_head, specialist):
    granted = grant_break_glass(
        user=specialist,
        granted_by=department_head,
        reason="Sünteetiline põhjus.",
        duration=dt.timedelta(hours=1),
    )
    assert granted.pk

    with pytest.raises(DomainError):
        grant_break_glass(
            user=department_head,
            granted_by=specialist,
            reason="Sünteetiline põhjus.",
            duration=dt.timedelta(hours=1),
        )

    department_head.is_active = False
    with pytest.raises(DomainError):
        grant_break_glass(
            user=specialist,
            granted_by=department_head,
            reason="Sünteetiline põhjus.",
            duration=dt.timedelta(hours=1),
        )


def test_no_raw_user_role_comparison_is_left_in_the_application():
    """The census the finding was measured with, kept as a test."""
    raw = re.compile(
        r"getattr\([\w.]+,\s*\"role\",\s*None\)\s*(==|!=)|\.role\s*(==|!=)\s*UserRole\."
    )
    offenders = [
        f"{path.relative_to(settings.BASE_DIR)}:{number}"
        for path in (Path(settings.BASE_DIR) / "app").rglob("*.py")
        if "migrations" not in path.parts
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if raw.search(line)
    ]
    assert offenders == []


# --- ENG-134 ---------------------------------------------------------------


def _routes() -> list[tuple[str, str]]:
    def walk(patterns, prefix="", namespace=""):
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                inner = pattern.namespace or ""
                joined = f"{namespace}:{inner}" if namespace and inner else inner or namespace
                yield from walk(pattern.url_patterns, prefix + str(pattern.pattern), joined)
            elif isinstance(pattern, URLPattern):
                name = f"{namespace}:{pattern.name}" if namespace else pattern.name or ""
                yield prefix + str(pattern.pattern), name

    return list(walk(get_resolver().url_patterns))


def test_every_path_is_registered_once():
    counts = Counter(path for path, _ in _routes())
    assert [path for path, count in counts.items() if count > 1] == []


def test_the_delete_route_and_the_department_page_still_resolve():
    matter = factories.MatterFactory()
    assert resolve(reverse("matters:matter_delete", kwargs={"pk": matter.pk})).url_name == (
        "matter_delete"
    )
    assert resolve("/osakond/").view_name == "matters:department"
