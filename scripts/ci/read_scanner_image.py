"""Print the scanner image a Compose file pins.

CI runs the scanner container it is asserting about, and the tag has to come
from the deployment's own file rather than from a second copy in the workflow —
or the day somebody upgrades ClamAV, CI goes on proving the old image works.
"""

from __future__ import annotations

import sys

import yaml


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: read_scanner_image.py <compose file>", file=sys.stderr)
        return 2

    with open(argv[1], encoding="utf-8") as handle:
        compose = yaml.safe_load(handle)

    service = compose.get("services", {}).get("clamav")
    if service is None:
        print(f"{argv[1]} has no clamav service", file=sys.stderr)
        return 1

    image = service.get("image", "")
    if not image:
        print(f"{argv[1]}: the clamav service pins no image", file=sys.stderr)
        return 1

    print(image)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
