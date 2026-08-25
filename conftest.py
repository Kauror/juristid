"""The safety boundary every pytest invocation in this repository crosses.

There is deliberately nothing else here. ``tests/conftest.py`` holds the
fixtures and ``e2e/conftest.py`` holds the browser ones; this file exists so
that the isolation check runs *before the first test*, for `pytest`, for
`pytest e2e`, and for any invocation whose rootdir turned out to be somewhere
unexpected — a repository-root conftest is collected as an initial conftest for
every argument inside the repository.

Immediate by design. The failure mode this guards against announces itself
otherwise as a file in a production evidence store, discovered a day later by an
integrity scan.
"""

from __future__ import annotations

from config.test_safety import assert_test_settings_are_in_force


def pytest_configure(config: object) -> None:
    # pytest-django has already resolved the settings module and called
    # django.setup() by this point, so this reads what is actually in force
    # rather than what was requested. Nothing has been collected yet, and no
    # fixture has run.
    assert_test_settings_are_in_force()
