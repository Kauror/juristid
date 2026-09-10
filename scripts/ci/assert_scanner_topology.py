"""The scanner's deployment shape, asserted on both stacks.

Five properties, and each of them is a way the gate could be present and
useless:

* **no published port** — a scanner reachable from the LAN is a service on a
  real-data host that nothing authenticates;
* **no volumes** — the bytes are streamed to it over clamd's INSTREAM protocol,
  so a container holding a signature database and a parser for every archive
  format ever invented has no view of the evidence tree;
* **a pinned image** — this decides whether member material is opened by a
  parser, and an unrelated `docker pull` on the host must not be able to change
  that;
* **a healthcheck** — a clamd with no signature database answers a socket and
  calls everything clean, which is the worst state this system could be in;
* **both application services actually configured to use it** — a scanner
  running beside an application that never asks it anything is decoration.

And all of it on the rehearsal stack too. The rehearsal running a different
security model from production is what let the original defect live: it
exercised, with REAL_DATA_ALLOWED off, a branch production could not reach
(ADR 0066).
"""

from __future__ import annotations

import re

import yaml

STACKS = (
    "deploy/unraid-main/compose.yml",
    "deploy/unraid-test/compose.yml",
)


def problems_in(path: str) -> list[str]:
    with open(path, encoding="utf-8") as handle:
        compose = yaml.safe_load(handle)

    services = compose.get("services", {})
    scanner = services.get("clamav")
    if scanner is None:
        return [f"{path}: no clamav service — production and the rehearsal must match"]

    found: list[str] = []
    if scanner.get("ports"):
        found.append(f"{path}: clamav publishes {scanner['ports']} — it must have no host port")
    if scanner.get("volumes"):
        found.append(
            f"{path}: clamav mounts {scanner['volumes']} — it is sent bytes, never given a tree"
        )

    image = scanner.get("image", "")
    tag = image.rpartition(":")[2] if ":" in image else ""
    if not tag or tag == "latest":
        found.append(f"{path}: clamav image {image!r} is not pinned to a reviewed version")
    if "healthcheck" not in scanner:
        found.append(
            f"{path}: clamav has no healthcheck, so a scanner that loaded nothing reads as green"
        )

    application = application_services(services)
    if len(application) < 2:
        found.append(
            f"{path}: found {len(application)} service(s) running the application image, "
            "which cannot be right — the derivation below has stopped matching this file"
        )
    for name in sorted(application):
        environment = services[name].get("environment") or {}
        if environment.get("MALWARE_SCANNER_BACKEND") != "clamav":
            found.append(f"{path}: {name} is not configured to use the scanner")
        if environment.get("MALWARE_SCANNER_HOST") != "clamav":
            found.append(f"{path}: {name} does not point at the scanner service")

    return found


def application_services(services: dict) -> set[str]:
    """Every service that boots Django, derived rather than listed.

    This used to read ``("web", "extractor")``, and on 2026-09-09 that cost a
    production deployment its search-freshness worker: `searchindex` runs the
    same image, `juristid.E015` is a check on configuration that *every* Django
    process runs at start-up, and a service without the setting therefore does
    not start at all under real data. Two of the three were configured, this
    guard asked about exactly those two, and the third restarted 419 times
    behind a stack that was otherwise green.

    A hard-coded pair answers "are these two right?" when the question is "is
    any of them wrong?" — so the set is taken from the file. A service running
    `juristid-<stack>-web:<tag>` is a service running the application, and the
    next one somebody adds is in scope the day it is written.

    The rehearsal stack has `REAL_DATA_ALLOWED=0` and so never reaches E015,
    which is precisely why this is checked statically on both stacks rather than
    left to whether a container happens to fall over (ADR 0066).
    """
    return {
        name
        for name, service in services.items()
        if re.match(r"^juristid-[a-z0-9-]+-web:", str(service.get("image", "")))
    }


def main() -> int:
    failures = [problem for path in STACKS for problem in problems_in(path)]
    for line in failures:
        print(f"::error::{line}")
    if failures:
        return 1
    print(f"scanner topology holds on {len(STACKS)} stacks: internal, pinned, healthchecked, used")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
