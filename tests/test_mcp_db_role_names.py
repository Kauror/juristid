"""The MCP role's name is a plain identifier and nothing more (security review NEW-08).

`re.match` with `$` also accepts a name followed by a newline. That was not
injectable — the name is double-quoted and `"` is not in the class — but a
name the statements are built from should be exactly the characters it is
allowed to have. No database: these are pure functions.
"""

from __future__ import annotations

import pytest

from app.mcp_server import db_role


@pytest.mark.parametrize(
    "name",
    ["juristid_mcp", "_x", "a" * 63, "mcp2"],
    ids=["default", "underscore", "longest", "digit"],
)
def test_plain_names_are_identifiers(name):
    assert db_role.valid_identifier(name)
    assert db_role.quote_identifier(name) == f'"{name}"'


@pytest.mark.parametrize(
    "name",
    [
        "juristid_mcp\n",
        "juristid_mcp\r\n",
        "\njuristid_mcp",
        "juristid mcp",
        "Juristid_mcp",
        "1mcp",
        "a" * 64,
        "",
        'mcp"; DROP ROLE x; --',
    ],
    ids=[
        "trailing-newline",
        "trailing-crlf",
        "leading-newline",
        "space",
        "upper-case",
        "leading-digit",
        "too-long",
        "empty",
        "quote",
    ],
)
def test_anything_else_is_refused(name):
    assert not db_role.valid_identifier(name)
    with pytest.raises(ValueError, match="not a plain PostgreSQL identifier"):
        db_role.quote_identifier(name)
    with pytest.raises(ValueError):
        db_role.role_sql(role=name, owner="juristid", database="juristid")


def test_a_name_that_is_not_text_is_refused():
    assert not db_role.valid_identifier(None)  # type: ignore[arg-type]
