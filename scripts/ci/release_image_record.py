"""Tie the release image's scan, its runtime check and its archive to one image id (G-02).

`release-image.yml` builds the production image afresh — the Dockerfile's
`apt-get upgrade` means its Debian packages are whatever Debian published that
day, not what CI's `juristid:ci` carried — so the release job scans and
runtime-checks the image it is about to archive, by the same rules CI uses.
This script is the part of that which is easier to get right in Python than in
shell, and it fails closed:

``scan``
    Reads Trivy's full JSON report and proves the report is about the image
    being released: Trivy's own ``Metadata.ImageID`` must be the expected id.
    It counts the findings by severity and counts the fixable HIGH and
    CRITICAL ones — the set CI's gate (``--ignore-unfixed --severity
    HIGH,CRITICAL``) fails on — and exits 1 if there is any, so a gate that
    somehow passed is still refused here. With ``--env-file`` it writes the
    summary for the manifest.

``archive``
    Reads the ``docker save`` archive back, without loading it, and proves it
    holds the image that was scanned: the archive's ``manifest.json`` names one
    image config, whose digest is the image id, and whose bytes hash to that
    digest. With ``--env-file`` it writes ``ARCHIVE_IMAGE_ID``.

Run from this workflow's own revision of main (``git show
"$WORKFLOW_SHA:scripts/ci/release_image_record.py"``), like the provenance
gate, so the commit being released does not bring its own judge. Standard
library only: it runs on the runner's ``python3`` with nothing installed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tarfile
from collections import Counter
from pathlib import Path
from typing import Any

IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")
GATED = frozenset({"CRITICAL", "HIGH"})
#: Image configs are a few kilobytes; anything larger in the archive is a layer.
SMALL_MEMBER = 1 << 20


class RecordError(Exception):
    """The evidence does not describe the image it claims to."""


def _image_id(value: str) -> str:
    if not IMAGE_ID.match(value):
        raise RecordError(f"not an image id (sha256:<64 hex>): {value!r}")
    return value


def _write_env(path: str | None, values: dict[str, str]) -> None:
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as env:
        for name, value in values.items():
            if "\n" in value:
                raise RecordError(f"{name} would span lines: {value!r}")
            env.write(f"{name}={value}\n")


# -- the scan ------------------------------------------------------------------------


def scan_summary(report: dict[str, Any], expected_id: str) -> dict[str, str]:
    """The manifest's scan fields, after proving the report is about `expected_id`."""
    scanned = str((report.get("Metadata") or {}).get("ImageID") or "")
    if scanned != expected_id:
        raise RecordError(
            f"the scan report is about image {scanned or '<none>'}, not {expected_id}: "
            "the image scanned is not the image being released"
        )
    severities: Counter[str] = Counter()
    fixable: list[str] = []
    for result in report.get("Results") or []:
        for finding in result.get("Vulnerabilities") or []:
            severity = str(finding.get("Severity") or "UNKNOWN").upper()
            severities[severity if severity in SEVERITIES else "UNKNOWN"] += 1
            # `--ignore-unfixed` keeps exactly the findings Trivy marks fixed.
            if severity in GATED and finding.get("Status") == "fixed":
                fixable.append(
                    f"{finding.get('VulnerabilityID')} {finding.get('PkgName')} "
                    f"{finding.get('InstalledVersion')} -> {finding.get('FixedVersion')}"
                )
    if fixable:
        raise RecordError(
            f"{len(fixable)} fixable HIGH/CRITICAL finding(s) in {expected_id}: "
            + "; ".join(fixable[:10])
        )
    return {
        "SCANNED_IMAGE_ID": expected_id,
        "IMAGE_SCAN_FINDINGS": " ".join(f"{name}={severities[name]}" for name in SEVERITIES),
        "IMAGE_SCAN_FIXABLE_HIGH_CRITICAL": "0",
    }


def scan_database(version: dict[str, Any] | None) -> str:
    """When Trivy's vulnerability database was published, from `trivy version --format json`."""
    database = version.get("VulnerabilityDB") if isinstance(version, dict) else None
    if not isinstance(database, dict):
        return "unknown"
    return str(database.get("UpdatedAt") or "unknown")


# -- the archive ---------------------------------------------------------------------


def archive_image_id(archive: Path) -> str:
    """The id of the one image a `docker save` archive holds, its config bytes checked."""
    manifest: list[dict[str, Any]] | None = None
    small: dict[str, str] = {}
    with tarfile.open(archive, mode="r|gz") as stream:
        for member in stream:
            if not member.isfile():
                continue
            name = member.name.removeprefix("./")
            if name != "manifest.json" and member.size > SMALL_MEMBER:
                continue
            handle = stream.extractfile(member)
            if handle is None:
                continue
            data = handle.read()
            if name == "manifest.json":
                manifest = json.loads(data)
            else:
                small[name] = hashlib.sha256(data).hexdigest()
    if not isinstance(manifest, list) or len(manifest) != 1:
        raise RecordError(f"{archive} does not hold exactly one image: {manifest!r}")
    config = str(manifest[0].get("Config") or "")
    # `blobs/sha256/<hex>` (OCI layout, Docker 25+) or `<hex>.json` (older).
    match = re.fullmatch(r"(?:blobs/sha256/)?([0-9a-f]{64})(?:\.json)?", config)
    if not match:
        raise RecordError(f"{archive}: unrecognised image config entry {config!r}")
    digest = match.group(1)
    if small.get(config) != digest:
        raise RecordError(
            f"{archive}: the image config {config} is missing or does not hash to its name"
        )
    return f"sha256:{digest}"


# -- the command line ----------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    scan = commands.add_parser("scan", help="prove the Trivy report is about this image")
    scan.add_argument("--report", required=True, type=Path)
    scan.add_argument("--image-id", required=True)
    scan.add_argument("--version-report", type=Path)
    scan.add_argument("--env-file")

    archive = commands.add_parser("archive", help="prove the archive holds this image")
    archive.add_argument("--archive", required=True, type=Path)
    archive.add_argument("--image-id", required=True)
    archive.add_argument("--env-file")

    arguments = parser.parse_args(argv)
    try:
        expected = _image_id(arguments.image_id)
        if arguments.command == "scan":
            report = json.loads(arguments.report.read_text(encoding="utf-8"))
            values = scan_summary(report, expected)
            version = None
            if arguments.version_report and arguments.version_report.exists():
                # Metadata only: an unreadable one is recorded as `unknown`.
                try:
                    version = json.loads(arguments.version_report.read_text(encoding="utf-8"))
                except ValueError:
                    version = None
            values["IMAGE_SCAN_DB"] = scan_database(version)
            values["IMAGE_SCAN_REPORT_SHA256"] = hashlib.sha256(
                arguments.report.read_bytes()
            ).hexdigest()
        else:
            found = archive_image_id(arguments.archive)
            if found != expected:
                raise RecordError(
                    f"{arguments.archive} holds image {found}, not {expected}, the image "
                    "that was scanned and runtime-checked"
                )
            values = {"ARCHIVE_IMAGE_ID": found}
    except (RecordError, OSError, ValueError, tarfile.TarError) as error:
        print(f"release image record: {error}", file=sys.stderr)
        return 1
    for name, value in values.items():
        print(f"{name}={value}")
    _write_env(arguments.env_file, values)
    return 0


if __name__ == "__main__":
    sys.exit(main())
