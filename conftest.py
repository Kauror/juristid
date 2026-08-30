"""The safety boundary every pytest invocation in this repository crosses,
and the shard selection every CI runner makes.

``tests/conftest.py`` holds the fixtures and ``e2e/conftest.py`` holds the
browser ones; this file exists so that the isolation check runs *before the
first test*, for `pytest`, for `pytest e2e`, and for any invocation whose
rootdir turned out to be somewhere unexpected — a repository-root conftest is
collected as an initial conftest for every argument inside the repository.

Immediate by design. The failure mode this guards against announces itself
otherwise as a file in a production evidence store, discovered a day later by an
integrity scan.

The sharding options live here for the same reason: they have to apply to
`pytest` and to `pytest e2e` alike, and this is the one file both invocations
load. The partitioning itself is in ``ci_sharding.py``, where it can be reasoned
about — and proved complete — without starting pytest at all.
"""

from __future__ import annotations

import pytest

import ci_sharding
from config.test_safety import assert_test_settings_are_in_force


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("sharding", "run one deterministic slice of the suite")
    group.addoption(
        "--shard-count",
        type=int,
        default=1,
        help="how many CI runners this suite is being split across (1 disables sharding)",
    )
    group.addoption(
        "--shard-index",
        type=int,
        default=1,
        help="which 1-based shard this runner is",
    )


def pytest_configure(config: object) -> None:
    # pytest-django has already resolved the settings module and called
    # django.setup() by this point, so this reads what is actually in force
    # rather than what was requested. Nothing has been collected yet, and no
    # fixture has run.
    assert_test_settings_are_in_force()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Keep only the files belonging to this runner's shard.

    Deselection rather than a narrower collection, so the run reports how many
    tests it did not take and `-ra` shows it: a shard that silently collected
    less than it should have would look identical to a fast one.

    Whole files, and the partition is computed from the files pytest just
    collected — not from a list in the workflow. A test file added by any other
    pull request is therefore picked up by whichever shard the partition lands
    it in, with nothing to remember (docs/ci-architecture.md).
    """
    shard_count = config.getoption("--shard-count")
    shard_index = config.getoption("--shard-index")

    if shard_count == 1 and shard_index == 1:
        return
    if shard_count < 1:
        raise pytest.UsageError(f"--shard-count must be at least 1, got {shard_count}")
    if not 1 <= shard_index <= shard_count:
        raise pytest.UsageError(
            f"--shard-index {shard_index} does not exist in a partition of {shard_count}"
        )

    root = config.rootpath
    # How many tests each file actually holds, right now. This is what keeps a
    # file somebody added this morning from being weighed as an average one: the
    # count is live, and only the seconds-per-test rate comes from metadata.
    counts: dict[str, int] = {}
    for item in items:
        if item.path is not None:
            relative = item.path.relative_to(root).as_posix()
            counts[relative] = counts.get(relative, 0) + 1
    files = sorted(counts)
    mine = ci_sharding.shard_for(files, shard_count, shard_index, test_counts=counts)

    # An empty shard is a configuration error, not a fast run. It means the
    # matrix asks for more runners than the suite has files, and the honest
    # outcome is a red job rather than a green one that proved nothing.
    if not mine and files:
        raise pytest.UsageError(
            f"shard {shard_index} of {shard_count} selected none of "
            f"the {len(files)} collected files"
        )

    kept: list[pytest.Item] = []
    dropped: list[pytest.Item] = []
    for item in items:
        target = (
            kept
            if item.path is not None and item.path.relative_to(root).as_posix() in mine
            else dropped
        )
        target.append(item)

    if dropped:
        config.hook.pytest_deselected(items=dropped)
    items[:] = kept
