"""The AI-access runbook names what the last security review left open (NEW-04..07).

A known gap that is written down is a decision; one that is not is an
oversight waiting to be rediscovered. These are prose checks on purpose: each
residual risk the fix-verification review (2026-10-23) accepted must be in
`docs/mcp/OPERATIONS.md` under *Residual risks*, with what holds instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.conf import settings

OPERATIONS = Path(settings.BASE_DIR) / "docs" / "mcp" / "OPERATIONS.md"


def _residual_risks() -> str:
    text = OPERATIONS.read_text(encoding="utf-8")
    start = text.index("## Residual risks")
    end = text.index("\n## ", start + 1)
    return text[start:end]


@pytest.mark.parametrize(
    ("finding", "must_say"),
    [
        ("NEW-02", ("temp_file_limit", "advisory locks", "temporary tables")),
        ("NEW-04", ("GRANT_SUSPENDED", "mcp_access list", "SecurityAuditEvent")),
        ("NEW-05", ("mcp_access unlock", "shared address")),
        ("NEW-06", ("round trips",)),
        ("NEW-07a", ("CF-Connecting-IP", "app/core/client_ip.py", "web:8000")),
        ("NEW-07b", ("LAN",)),
        ("NEW-07c", ("recreates it by name",)),
    ],
    ids=["NEW-02", "NEW-04", "NEW-05", "NEW-06", "NEW-07a", "NEW-07b", "NEW-07c"],
)
def test_each_accepted_residual_risk_is_written_down(finding, must_say):
    section = _residual_risks()
    assert f"({finding}" in section, finding
    for phrase in must_say:
        assert phrase in section, (finding, phrase)


def test_the_health_table_says_where_a_lock_shows():
    text = OPERATIONS.read_text(encoding="utf-8")
    health = text[text.index("## Health") : text.index("## Granting and revoking a person")]
    assert "Is anybody locked or suspended right now?" in health
    assert "PEATATUD" in health
