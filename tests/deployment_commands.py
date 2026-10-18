"""How every deployed web process starts, shared by the deployment tests.

The image's CMD and both Unraid stacks start gunicorn through
`scripts/serve-after-checks.sh`, which runs `manage.py check --deploy
--fail-level ERROR` and execs the rest of the command only if it passes
(JOPS-004). The tests that read "what serves requests" off a command strip that
prefix with `served()` — which also fails if the prefix is missing, so none of
them can pass over a command that skipped the checks.
"""

from __future__ import annotations

#: The script, run by `sh` so that its file mode does not matter.
SERVE_AFTER_CHECKS = ["sh", "/app/scripts/serve-after-checks.sh"]


def served(command: list[str]) -> list[str]:
    """The command the checks hand over to, with the check prefix asserted."""
    words = [str(word) for word in command]
    assert words[: len(SERVE_AFTER_CHECKS)] == SERVE_AFTER_CHECKS, (
        f"{words[:4]}… does not start behind scripts/serve-after-checks.sh, so a "
        "configuration the system checks refuse would serve anyway (JOPS-004)"
    )
    return words[len(SERVE_AFTER_CHECKS) :]
