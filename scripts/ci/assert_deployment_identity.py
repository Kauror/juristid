"""Does the production stack resolve one target image from one identity?

Run against `docker compose config --format json` for
`deploy/unraid-main/compose.yml`, from the compose smoke job in CI. It exists
because the alternative is a unit test that re-implements Compose's
`${VAR:-default}` interpolation and then proves its own implementation right.
Compose does the interpolation here; this only reads the answer.

Two calls, and the second matters as much as the first:

    assert_deployment_identity.py tagged.json <full-40-char-sha>
        every application service resolves `juristid-main-web:<sha12>`, and the
        build receives the full SHA.

    assert_deployment_identity.py untagged.json --fallback
        with the variables unset, every application service falls back to
        `juristid-main-web:local` — together. `local` is the tag a hand-built
        image overwrites, so a `migrate` that resolved it would be a schema
        change made by an unreviewed build. The runbook names that tag when it
        explains why the exports come first, so a silent change to the fallback
        would make the runbook wrong without making anything red.

No Docker and no network of its own: it reads a JSON file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

#: Services that run the application image. The tunnel and the database run
#: pinned upstream images and are deliberately not in this list.
APPLICATION_SERVICES = ("web", "extractor")

IMAGE_PREFIX = "juristid-main-web:"
FALLBACK_TAG = "local"


def fail(message: str) -> None:
    print(f"::error::{message}")
    sys.exit(1)


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        fail("usage: assert_deployment_identity.py CONFIG_JSON (SHA | --fallback)")

    config = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    services = config.get("services", {})

    for name in APPLICATION_SERVICES:
        if name not in services:
            fail(f"the stack has no '{name}' service; this check is looking at the wrong file")

    if argv[2] == "--fallback":
        expected = IMAGE_PREFIX + FALLBACK_TAG
        for name in APPLICATION_SERVICES:
            image = services[name].get("image")
            if image != expected:
                fail(
                    f"with no identity exported, '{name}' resolved '{image}' rather than "
                    f"'{expected}'. The runbook explains the exports by naming that fallback."
                )
        print(f"unset -> {expected} on {', '.join(APPLICATION_SERVICES)}")
        return

    sha = argv[2]
    if len(sha) != 40 or not all(character in "0123456789abcdef" for character in sha):
        fail(f"'{sha}' is not a full 40-character commit id")

    expected = IMAGE_PREFIX + sha[:12]
    for name in APPLICATION_SERVICES:
        image = services[name].get("image")
        if image != expected:
            fail(
                f"'{name}' resolved '{image}' rather than '{expected}'. Build, migration plan, "
                f"migrate and up must all reach the same image."
            )

    argument = services["web"].get("build", {}).get("args", {}).get("GIT_SHA")
    if argument != sha:
        fail(
            f"the build receives GIT_SHA='{argument}' rather than the full '{sha}'. The image "
            "would then be unable to say which commit it is, and deployment_readiness refuses "
            "a build that cannot."
        )

    print(f"{expected} on {', '.join(APPLICATION_SERVICES)}; build GIT_SHA={sha}")


if __name__ == "__main__":
    main(sys.argv)
