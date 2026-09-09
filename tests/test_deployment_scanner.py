"""The scanner's deployment shape, on both stacks.

`scripts/ci/assert_scanner_topology.py` makes the same assertions inside the
container job, where they run beside a real ClamAV. These run in the fast suite,
because a Compose file that does not deploy the scanner correctly should fail in
seconds rather than fifteen minutes into a build — and because a reviewer
reading `tests/` should be able to find out what the deployment promises without
reading a workflow.

Neither of them replaces the other half of the proof, which is that the scanner
*detects*: a clamd with no signature database satisfies every assertion in this
file and calls every document clean. That is
`manage.py check_malware_scanner --eicar`, run against the real container in CI
(ADR 0066).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "deploy" / "unraid-main" / "compose.yml"
REHEARSAL = ROOT / "deploy" / "unraid-test" / "compose.yml"

#: Both of them, always. The rehearsal exists to meet a deployment defect before
#: production does, and it can only do that where the two agree — and *this* is
#: the boundary that hid the original defect. The rehearsal runs
#: REAL_DATA_ALLOWED=0, where PENDING is already extractable, so it exercised
#: assisted intake successfully against a code path production could not reach.
STACKS = ("main", "rehearsal")


@pytest.fixture(scope="module")
def stacks() -> dict[str, dict[str, Any]]:
    return {
        "main": yaml.safe_load(MAIN.read_text(encoding="utf-8")),
        "rehearsal": yaml.safe_load(REHEARSAL.read_text(encoding="utf-8")),
    }


@pytest.mark.parametrize("stack", STACKS)
def test_both_stacks_run_a_scanner(stacks: dict[str, Any], stack: str) -> None:
    assert "clamav" in stacks[stack]["services"], (
        f"{stack} has no scanner: with real data nothing could ever become CLEAN, "
        "and with synthetic data it would be rehearsing a different security model"
    )


@pytest.mark.parametrize("stack", STACKS)
def test_the_scanner_publishes_no_host_port(stacks: dict[str, Any], stack: str) -> None:
    """A scanner on the LAN is an unauthenticated service on a real-data host."""
    scanner = stacks[stack]["services"]["clamav"]
    assert not scanner.get("ports"), f"{stack}: clamav publishes {scanner.get('ports')}"
    assert scanner.get("network_mode") != "host", f"{stack}: clamav is on the host network"


@pytest.mark.parametrize("stack", STACKS)
def test_the_scanner_is_given_no_files_at_all(stacks: dict[str, Any], stack: str) -> None:
    """It is *sent* bytes; it is never handed a tree.

    The application streams each file over clamd's INSTREAM protocol, so the
    container holding a signature database and a parser for every archive format
    anybody has ever invented has no view of the evidence store, the derivative
    store or the staging volume. A mount here would be a much larger blast
    radius bought for nothing (ADR 0066).
    """
    scanner = stacks[stack]["services"]["clamav"]
    assert not scanner.get("volumes"), f"{stack}: clamav mounts {scanner.get('volumes')}"


@pytest.mark.parametrize("stack", STACKS)
def test_the_scanner_image_is_pinned(stacks: dict[str, Any], stack: str) -> None:
    """This decides whether member material is opened by a parser.

    `latest` would mean an unrelated `docker pull` on the host could change that
    without anybody deciding to — the same argument the tunnel image carries.
    """
    image = stacks[stack]["services"]["clamav"].get("image", "")
    tag = image.rpartition(":")[2] if ":" in image else ""
    assert tag and tag != "latest", f"{stack}: clamav image {image!r} is not pinned"


@pytest.mark.parametrize("stack", STACKS)
def test_the_scanner_is_healthchecked(stacks: dict[str, Any], stack: str) -> None:
    """Because the dangerous failure is silent.

    A clamd that is running with no signature database answers a socket
    perfectly and calls everything clean. Every other assertion in this file
    passes in that state, and the column would say CLEAN about files nothing had
    examined — so the probe has to ask whether it is *loaded*, which is what
    `clamdcheck.sh` does.
    """
    scanner = stacks[stack]["services"]["clamav"]
    assert "healthcheck" in scanner, f"{stack}: clamav has no healthcheck"
    probe = " ".join(scanner["healthcheck"].get("test") or [])
    assert "clamdcheck" in probe, f"{stack}: the probe is {probe!r}, which does not ask clamd"


@pytest.mark.parametrize("stack", STACKS)
@pytest.mark.parametrize("service", ["web", "extractor"])
def test_both_application_services_are_pointed_at_the_scanner(
    stacks: dict[str, Any], stack: str, service: str
) -> None:
    """A scanner nothing asks anything is decoration.

    `extractor` is the process that actually scans. `web` needs the setting too,
    because `juristid.E015` and `manage.py deployment_readiness` both ask whether
    a scanner is configured, and a web container that answered "no" would refuse
    to start with real data.
    """
    environment = stacks[stack]["services"][service].get("environment") or {}
    assert environment.get("MALWARE_SCANNER_BACKEND") == "clamav", (
        f"{stack}/{service} does not use the scanner"
    )
    assert environment.get("MALWARE_SCANNER_HOST") == "clamav", (
        f"{stack}/{service} does not point at the scanner service"
    )


@pytest.mark.parametrize("stack", STACKS)
def test_the_scanner_shares_the_stack_network_and_no_other(
    stacks: dict[str, Any], stack: str
) -> None:
    scanner = stacks[stack]["services"]["clamav"]
    assert scanner.get("networks") == ["internal"], (
        f"{stack}: clamav is on {scanner.get('networks')} rather than the stack's own network"
    )


def test_the_extractor_does_not_wait_for_the_signature_database(
    stacks: dict[str, Any],
) -> None:
    """Started, not healthy — and the distinction is deliberate.

    Loading the signature database takes minutes. Holding the worker out of the
    queue for that would delay every deployment's extraction backlog for no
    gain: a scanner that is not answering yet simply leaves rows PENDING, which
    is exactly what it does when one is down, and the next turn of the loop picks
    them up.
    """
    for stack in STACKS:
        depends = stacks[stack]["services"]["extractor"].get("depends_on") or {}
        assert "clamav" in depends, f"{stack}: the extractor does not depend on the scanner"
        condition = depends["clamav"]
        condition = condition.get("condition") if isinstance(condition, dict) else condition
        assert condition == "service_started", (
            f"{stack}: the extractor waits for {condition!r}, which delays every "
            "deployment by the signature-database load"
        )
