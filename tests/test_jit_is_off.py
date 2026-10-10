"""PostgreSQL JIT is off for this database, and readiness says so (AUD-039, ADR 0153)."""

from __future__ import annotations

import pytest

from app.core import deployment

pytestmark = pytest.mark.django_db


def test_the_database_answers_jit_off():
    """`core/0002` sets it on the test database exactly as on every other."""
    assert deployment.jit_setting() == deployment.EXPECTED_JIT == "off"


def test_readiness_refuses_a_database_with_jit_on(monkeypatch):
    monkeypatch.setattr(deployment, "jit_setting", lambda: "on")

    report = deployment.readiness_report()

    assert any("JIT is 'on'" in problem for problem in report.problems)


def test_readiness_has_no_jit_problem_when_it_is_off():
    report = deployment.readiness_report()

    assert not any("JIT" in problem for problem in report.problems)
