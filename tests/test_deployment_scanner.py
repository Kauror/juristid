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

import copy
import importlib.util
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "deploy" / "unraid-main" / "compose.yml"
REHEARSAL = ROOT / "deploy" / "unraid-test" / "compose.yml"


def _the_ci_guard() -> Any:
    """The CI script itself, loaded from the file CI runs.

    `scripts/` is not a package and is not on the path, and copying the rule
    here would recreate the thing that went wrong: two places that agree until
    one of them is edited. There were briefly two fixes for this defect open at
    once, and the other one did exactly that — a second list of application
    services, in a second file, correct on the day it was written.

    So the derivation and the settings it requires are imported rather than
    restated, and the fast suite and the container job cannot come to disagree
    about what «an application service, configured» means.
    """
    path = ROOT / "scripts" / "ci" / "assert_scanner_topology.py"
    spec = importlib.util.spec_from_file_location("assert_scanner_topology", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_guard = _the_ci_guard()
application_services = _guard.application_services
scanner_gaps = _guard.scanner_gaps
SCANNER_SETTINGS = _guard.SCANNER_SETTINGS

#: What the derivation is expected to find, per stack, today.
#:
#: Not the input to the rule — the rule reads the file — but a check *on* the
#: rule: a regex that quietly stopped matching `searchindex` would leave the
#: guard green while covering two services out of three, which is the exact
#: failure this whole change exists to end. A fourth worker makes this list
#: fail and somebody read it; it does not make the scanner requirement
#: optional, because that is derived.
TODAYS_APPLICATION_SERVICES = {"web", "extractor", "searchindex"}

#: Images this repository does not build. None of them ever loads Django, so
#: none of them runs `juristid.E015` and none of them needs the setting —
#: and a derivation that swept one of them in would demand configuration of a
#: container that has no idea what to do with it.
FOREIGN_SERVICES = {"db", "clamav", "tunnel"}

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
def test_the_derivation_finds_every_service_that_runs_this_application(
    stacks: dict[str, Any], stack: str
) -> None:
    """The rule reads the file; this reads the rule.

    `application_services` matches on the image, so a service running
    `juristid-<stack>-web:<tag>` is in scope the day somebody writes it. That is
    the property worth having, and it is also the property that can rot
    silently: a pattern that stopped matching `searchindex` would leave every
    guard below green while asking about two services out of three — which is
    the shape of the defect this file exists to prevent, one level up.

    So the derivation's *output* is asserted against what the stacks really
    hold. A fourth worker makes this fail and somebody reads it; it does not
    make the scanner requirement optional, because that stays derived.
    """
    assert application_services(stacks[stack]["services"]) == TODAYS_APPLICATION_SERVICES


@pytest.mark.parametrize("stack", STACKS)
@pytest.mark.parametrize("service", sorted(FOREIGN_SERVICES))
def test_the_derivation_leaves_other_images_alone(
    stacks: dict[str, Any], stack: str, service: str
) -> None:
    """`db`, `clamav` and `tunnel` never load Django, so they never run E015.

    Stated per service rather than as a set difference, because the failure
    would be one of them: a widened pattern that swept in `clamav` would demand
    scanner settings of the scanner, and the message would read like a finding.
    """
    services = stacks[stack]["services"]
    assert service in services, f"{stack}: no {service} service, so this assertion is stale"
    assert service not in application_services(services)


@pytest.mark.parametrize("stack", STACKS)
def test_every_application_service_is_pointed_at_the_scanner(
    stacks: dict[str, Any], stack: str
) -> None:
    """A scanner nothing asks anything is decoration — and a Django process that
    cannot answer where the scanner is does not start at all.

    `extractor` is the process that actually scans. `web` needs the setting
    because `juristid.E015` and `manage.py deployment_readiness` both ask
    whether a scanner is configured. And `searchindex` needs it for the same
    reason as `web` even though it never opens a document: E015 is a check on
    *configuration*, run by every process that boots Django.

    This was parametrised over `["web", "extractor"]` and shipped on 2026-09-09
    with `searchindex` unconfigured, which under real data restarted it behind a
    stack whose other five containers were healthy. A named pair answers "are
    these two right?"; the question is "is any of them wrong?", so the set is
    derived from the file and the next service somebody adds is covered the day
    it is written.

    All three settings, through the same `scanner_gaps` the container job runs.
    """
    assert scanner_gaps(stacks[stack]["services"]) == []


@pytest.mark.parametrize("stack", STACKS)
def test_every_application_service_reads_the_deployment_env_file(
    stacks: dict[str, Any], stack: str
) -> None:
    """Why a missing setting is a crash rather than an omission.

    `REAL_DATA_ALLOWED` arrives from the env file, and every application service
    loads the same one — so on the main stack every one of them reads
    `REAL_DATA_ALLOWED=1`, and every one of them is therefore subject to E015,
    including the two that never open a document. Without this the defect would
    have been a worker carrying a scanner setting it does not use; with it, the
    worker does not start.
    """
    services = stacks[stack]["services"]
    expected = services["web"]["env_file"]
    for name in sorted(application_services(services)):
        assert services[name].get("env_file") == expected, (
            f"{stack}/{name} does not read the same env file as web, so what it is "
            f"subject to is no longer decided by one file"
        )


# --------------------------------------------------------------------------
# The rule itself, against compose files that do not exist. `scanner_gaps`
# returns findings rather than raising for exactly this reason: the arrangement
# it is here to catch cannot be committed to a compose file in order to be
# tested.
# --------------------------------------------------------------------------


def test_the_configuration_that_crash_looped_production_is_caught(
    stacks: dict[str, Any],
) -> None:
    """The 2026-09-09 stack, reconstructed: three settings short on one worker.

    `web` and `extractor` were given the scanner and `searchindex` was not.
    E015 is a check on configuration rather than on behaviour, so the worker
    failed its start-up checks, exited 1, and `restart: unless-stopped` started
    it again — behind five healthy containers, with nothing else on the stack
    reporting that search had stopped converging.

    This is the assertion that would have failed on the pull request that
    shipped it.
    """
    services = copy.deepcopy(stacks["main"]["services"])
    for key in SCANNER_SETTINGS:
        del services["searchindex"]["environment"][key]

    assert scanner_gaps(services) == [f"searchindex: {key}" for key in SCANNER_SETTINGS]


@pytest.mark.parametrize("setting", sorted(SCANNER_SETTINGS))
def test_each_setting_is_required_on_its_own(stacks: dict[str, Any], setting: str) -> None:
    """Including the port.

    Two of the three decide whether E015 passes; the third decides which port a
    process that passed it then talks to. A service given a backend and a host
    and no port is configured against whatever the application's default happens
    to be — true today, and a divergence nobody would see the day it changes.
    """
    services = copy.deepcopy(stacks["main"]["services"])
    del services["searchindex"]["environment"][setting]

    assert scanner_gaps(services) == [f"searchindex: {setting}"]


def test_a_worker_added_tomorrow_is_covered_the_day_it_is_written(
    stacks: dict[str, Any],
) -> None:
    """The whole reason the set is derived rather than listed.

    A fourth service running the application image, written by somebody who has
    never read this file, with everything else about it correct. Nobody has to
    remember to add it anywhere: it runs Django, so it runs E015, so the rule
    already asks about it.
    """
    services = copy.deepcopy(stacks["main"]["services"])
    services["reindexer"] = {
        "image": services["searchindex"]["image"],
        "env_file": services["searchindex"]["env_file"],
        "environment": {"POSTGRES_HOST": "db"},
    }

    assert "reindexer" in application_services(services)
    assert scanner_gaps(services) == [f"reindexer: {key}" for key in SCANNER_SETTINGS]


def test_a_service_on_a_foreign_image_is_asked_for_nothing(stacks: dict[str, Any]) -> None:
    """The other half of the derivation, which is as capable of going wrong.

    A sidecar on a public image has no Django in it, never runs E015, and would
    not know what to do with a scanner host. A rule that demanded settings of it
    would be noise, and noise is how a guard stops being read.
    """
    services = copy.deepcopy(stacks["main"]["services"])
    services["backup-sidecar"] = {"image": "postgres:18", "environment": {}}

    assert "backup-sidecar" not in application_services(services)
    assert scanner_gaps(services) == []


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
