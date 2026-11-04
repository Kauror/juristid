"""The browser and visual jobs' servers do not keep database connections.

Both jobs serve the application with `runserver`, one thread per request. With
`CONN_MAX_AGE` above zero, every finished request thread leaves its connection
open until garbage collection; on 2026-11-04 the six servers of one browser
runner exhausted PostgreSQL's 100 connections, the sign-in page became an
error page and every later test timed out (#592, run 41773261922). This pins
the setting that removes that failure mode without touching any test.
"""

from __future__ import annotations

import pathlib

import yaml
from django.conf import settings

CI = pathlib.Path(settings.BASE_DIR) / ".github" / "workflows" / "ci.yml"


def test_runserver_jobs_do_not_persist_database_connections() -> None:
    jobs = yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]
    for name in ("browser", "visual"):
        assert jobs[name]["env"].get("DJANGO_DB_CONN_MAX_AGE") == "0", name


def test_the_setting_is_read_from_that_variable() -> None:
    source = (pathlib.Path(settings.BASE_DIR) / "config" / "settings.py").read_text(
        encoding="utf-8"
    )
    assert 'env_int("DJANGO_DB_CONN_MAX_AGE"' in source
