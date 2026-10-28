"""What the footer and Haldus → Rakendus claim about the build that is serving the page.

The stamp exists so that "which build is on the server" is answerable by
looking, rather than by reading a deployment log or trusting that whoever
deployed last remembered to update an environment variable.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from django.test import Client

from app.core.context_processors import parse_built_at


def test_a_utc_stamp_from_the_image_parses_to_that_instant() -> None:
    """The Dockerfile writes `date -u +%Y-%m-%dT%H:%M:%SZ`."""
    parsed = parse_built_at("2026-08-19T11:52:22Z")
    assert parsed == datetime(2026, 8, 19, 11, 52, 22, tzinfo=UTC)


def test_a_stamp_without_a_zone_is_read_as_utc() -> None:
    """Not a guess: the image writes UTC, so a bare time is UTC.

    Reading it as local time instead would shift a Tallinn footer by three
    hours in summer — wrong in the direction that looks plausible, which is
    the direction nobody checks.
    """
    parsed = parse_built_at("2026-08-19T11:52:22")
    assert parsed == datetime(2026, 8, 19, 11, 52, 22, tzinfo=UTC)


@pytest.mark.parametrize("raw", ["", "   ", "unknown", "eile", "2026-13-45T99:99:99Z"])
def test_an_unusable_stamp_becomes_nothing(raw: str) -> None:
    """Better a footer with no build time than one showing `eile`."""
    assert parse_built_at(raw) is None


def test_whitespace_around_a_stamp_does_not_defeat_it() -> None:
    """`date > file` leaves a trailing newline; `read_text` keeps it."""
    assert parse_built_at("  2026-08-19T11:52:22Z\n  ") == datetime(
        2026, 8, 19, 11, 52, 22, tzinfo=UTC
    )


def _footer(body: str) -> str:
    start = body.index('<footer class="app__footer">')
    return body[start : body.index("</footer>", start)]


def _rakendus(body: str) -> str:
    start = body.index('aria-labelledby="rakendus"')
    return body[start : body.index("</section>", start)]


@pytest.mark.django_db
def test_the_footer_shows_the_build_time_in_local_time(client: Client, settings) -> None:
    """11:52 UTC is 14:52 in Tallinn, and Tallinn is where this is read.

    Since 2026-10-28 the time is the footer's only build fact, and it is the
    link to Uuendused: what changed in the build it dates.
    """
    settings.APPLICATION_BUILT_AT = "2026-08-19T11:52:22Z"
    settings.APPLICATION_REVISION = "ceb324c"

    footer = _footer(client.get("/").content.decode())

    assert "Eesti Kaubandus-Tööstuskoda" in footer
    assert re.search(r'<a href="/uuendused/"[^>]*>ehitatud 19\.8\.2026 14:52</a>', footer)
    assert "ceb324c" not in footer and "versioon" not in footer


@pytest.mark.django_db
def test_the_footer_omits_the_build_time_when_there_is_none(client: Client, settings) -> None:
    """Outside a container there is no stamp, and the footer must not invent one.

    The link to Uuendused stays, named for where it goes.
    """
    settings.APPLICATION_BUILT_AT = ""

    footer = _footer(client.get("/").content.decode())

    assert "ehitatud" not in footer
    assert re.search(r'<a href="/uuendused/">Uuendused</a>', footer)


@pytest.mark.django_db
def test_haldus_rakendus_says_which_build_is_running(client: Client, settings) -> None:
    """The stage, environment, revision and build time, read where the footer read them."""
    from tests import factories

    settings.APPLICATION_BUILT_AT = "2026-08-19T11:52:22Z"
    settings.APPLICATION_REVISION = "ceb324c"
    settings.APPLICATION_STAGE = "Stage 4"
    settings.APPLICATION_ENVIRONMENT = "production"
    client.force_login(factories.ReaderFactory())

    section = _rakendus(client.get("/haldus/").content.decode())

    for value in ("Stage 4", "production", "ceb324c", "19.8.2026 14:52"):
        assert value in section, value


@pytest.mark.django_db
def test_haldus_rakendus_invents_no_build_time(client: Client, settings) -> None:
    from tests import factories

    settings.APPLICATION_BUILT_AT = "eile"
    client.force_login(factories.ReaderFactory())

    section = _rakendus(client.get("/haldus/").content.decode())

    assert "eile" not in section
    assert re.search(r"<dt>Ehitatud</dt>\s*<dd>—</dd>", section)
