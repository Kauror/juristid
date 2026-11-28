"""A verification query must name a table that exists (r98).

The post-swap check of an r96 kit counted rows in `notifications_notification`, a table the
schema does not have (the notification tables are `notifications_deadlinenotification`,
`notifications_commencementreminder`, …). The kit generator is maintained outside this
repository; what lives here is the rule it must follow, as a helper any kit test can call,
and a scan of every SQL statement the repository's own scripts and runbooks carry.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.apps import apps
from django.db import connection

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[1]
#: `FROM x`, `JOIN x`, `INTO x`, `UPDATE x`, `TABLE x` — x a Django table (app label prefix).
STATEMENT = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+(?:public\.)?\"?([a-z][a-z0-9_]*)\"?", re.I
)


def unknown_tables(text: str) -> set[str]:
    """Tables ``text`` names after SQL keywords that carry an app label but do not exist."""
    labels = tuple(f"{config.label}_" for config in apps.get_app_configs())
    real = set(connection.introspection.table_names())
    named = {m.group(1).lower() for m in STATEMENT.finditer(text)}
    return {name for name in named if name.startswith(labels) and name not in real}


def test_the_helper_catches_the_table_the_r96_kit_named():
    bad = "SELECT count(*) FROM notifications_notification WHERE read_at IS NULL"
    assert unknown_tables(bad) == {"notifications_notification"}


def test_the_real_notification_tables_are_accepted():
    good = (
        "SELECT count(*) FROM notifications_deadlinenotification;"
        " select 1 from public.notifications_commencementreminder"
    )
    assert unknown_tables(good) == set()


def test_words_that_are_not_tables_are_not_flagged():
    prose = "UPDATE the release notes; FROM the host; JOIN the call; INTO a lock file"
    assert unknown_tables(prose) == set()


def test_every_table_a_repository_script_or_runbook_queries_exists():
    problems: dict[str, set[str]] = {}
    for folder in ("scripts", "tools", "deploy"):
        for path in sorted((ROOT / folder).rglob("*")):
            if path.suffix not in {".sh", ".py", ".sql", ".md"} or not path.is_file():
                continue
            found = unknown_tables(path.read_text(encoding="utf-8", errors="replace"))
            if found:
                problems[str(path.relative_to(ROOT))] = found
    assert problems == {}
