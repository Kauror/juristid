"""Security review 2026-10-29, assessor D — finding D-03 (P3, CONFIRMED).

ADR 0179: every third-party image is pinned by content (`tag@sha256:…`), and
`tests/test_supply_chain_pinning.py` holds every `FROM` line to it. The first
line of the Dockerfile is not a `FROM`:

    # syntax=docker/dockerfile:1

BuildKit resolves that directive to the Docker Hub image `docker/dockerfile:1`
at build time — a moving tag — and runs it as the *frontend that interprets the
Dockerfile*. release-image.yml builds the production image with `docker build`
on a GitHub runner, where BuildKit is the default builder, so every release
build executes whatever that tag currently points at, outside the digest
discipline the rest of the file was given. The Dockerfile uses no feature that
needs the directive (no `RUN --mount`, no heredoc, no `COPY --link`).

Smallest fix: delete the `# syntax=` line, or pin it like everything else:
`# syntax=docker/dockerfile:1.x@sha256:<index digest>` and let
`test_supply_chain_pinning.py` list it. Code change, no owner decision.

This test FAILS on current code.
"""

from __future__ import annotations

import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"

SYNTAX_DIRECTIVE = re.compile(r"^\s*#\s*syntax\s*=\s*(?P<image>\S+)", re.IGNORECASE)
PINNED = re.compile(r"@sha256:[0-9a-f]{64}$")


def test_the_buildkit_frontend_is_pinned_by_digest_or_not_named() -> None:
    directives = [
        match.group("image")
        for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()[:3]
        if (match := SYNTAX_DIRECTIVE.match(line))
    ]
    for image in directives:
        assert PINNED.search(image), (
            f"Dockerfile names the BuildKit frontend {image!r} by a moving tag; every "
            "release build fetches and executes whatever it points at today (ADR 0179)"
        )
