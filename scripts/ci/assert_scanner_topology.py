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

    for name in ("web", "extractor"):
        service = services.get(name)
        if service is None:
            found.append(f"{path}: no {name} service to check")
            continue
        environment = service.get("environment") or {}
        if environment.get("MALWARE_SCANNER_BACKEND") != "clamav":
            found.append(f"{path}: {name} is not configured to use the scanner")
        if environment.get("MALWARE_SCANNER_HOST") != "clamav":
            found.append(f"{path}: {name} does not point at the scanner service")

    return found


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
