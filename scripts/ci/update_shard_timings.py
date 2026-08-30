"""Regenerate ``ci/shard-timings.json`` from the JUnit reports a CI run produced.

Balance metadata that nobody can refresh is metadata that rots, and a rotted
table shows up as one shard carrying the suite while the others idle. This is
the refresh, and it takes one command:

    gh run download <run-id> --dir /tmp/junit --pattern 'test-report-*'
    uv run python scripts/ci/update_shard_timings.py /tmp/junit --run <run-id>

Every sharded job uploads its report, so the reports of one run together
describe the whole suite — which is the same completeness the shards themselves
have, and the reason this can be regenerated from an ordinary green run rather
than from a special measurement build.

Correctness never depends on the output. A file the table has never heard of is
weighted from its directory's per-test rate, and a file whose entry is stale is
rescaled by its current test count (``ci_sharding.py``). This only decides which
runner picks it up.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
import xml.etree.ElementTree as ElementTree

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "ci" / "shard-timings.json"

#: Only these directories are partitioned, so only these are worth weighing.
SUITE_DIRECTORIES = ("tests", "e2e")


def case_path(case: ElementTree.Element) -> str | None:
    """The repository-relative test file one JUnit ``testcase`` came from.

    pytest writes the module as a dotted ``classname`` — ``e2e.test_ui_shell``,
    or ``tests.test_thing.TestClass`` when the test lives in a class — and does
    not write a ``file`` attribute. Trimming the dotted path from the right
    until it names a file on disk handles both, and returns nothing rather than
    a guess for anything it cannot resolve.
    """
    attribute = case.get("file")
    if attribute:
        candidate = pathlib.PurePosixPath(attribute.replace("\\", "/")).as_posix()
        if (ROOT / candidate).exists():
            return candidate

    parts = (case.get("classname") or "").split(".")
    while parts:
        candidate = "/".join(parts) + ".py"
        if (
            candidate.startswith(tuple(f"{name}/" for name in SUITE_DIRECTORIES))
            and (ROOT / candidate).exists()
        ):
            return candidate
        parts.pop()
    return None


def read_reports(directory: pathlib.Path) -> tuple[dict[str, float], dict[str, int]]:
    """Total seconds and test count per file, across every report found."""
    seconds: dict[str, float] = collections.defaultdict(float)
    counts: dict[str, int] = collections.defaultdict(int)

    reports = sorted(directory.rglob("*.xml"))
    if not reports:
        raise SystemExit(f"no JUnit reports under {directory}")

    for report in reports:
        # The reports are this repository's own CI artifacts, produced by pytest
        # minutes earlier and downloaded by the person running this. Reaching for
        # defusedxml here would be theatre.
        for case in ElementTree.parse(report).getroot().iter("testcase"):  # noqa: S314
            path = case_path(case)
            if path is None:
                continue
            seconds[path] += float(case.get("time", "0") or 0)
            counts[path] += 1

    return dict(seconds), dict(counts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", type=pathlib.Path, help="directory of downloaded JUnit XML")
    parser.add_argument("--run", required=True, help="the CI run id these numbers came from")
    parser.add_argument("--out", type=pathlib.Path, default=OUTPUT)
    arguments = parser.parse_args()

    seconds, counts = read_reports(arguments.reports)

    known = {path for path in seconds if (ROOT / path).exists()}
    vanished = sorted(set(seconds) - known)
    if vanished:
        print(f"dropping {len(vanished)} file(s) that no longer exist, for example {vanished[0]}")

    document = {
        "measured_from": f"https://github.com/Kauror/juristid/actions/runs/{arguments.run}",
        "note": (
            "Balance input for ci_sharding.py, regenerated with "
            "scripts/ci/update_shard_timings.py. Nothing about which tests run "
            "depends on it; see docs/ci-architecture.md."
        ),
        "files": {
            path: {"seconds": round(seconds[path], 3), "tests": counts[path]}
            for path in sorted(known)
        },
    }

    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    total = sum(seconds[path] for path in known)
    print(f"wrote {arguments.out.relative_to(ROOT)}: {len(known)} files, {total:.0f}s measured")
    return 0


if __name__ == "__main__":
    sys.exit(main())
