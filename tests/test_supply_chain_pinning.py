"""Every third-party image and Action is pinned to its content, not to a name.

ADR 0179, Codex finding csf_b46bef0c0dcacd3af8255291. A tag — `postgres:18`,
`cloudflare/cloudflared:2026.8.2`, `actions/checkout@v4` — is a name its
publisher can move. Production pulled whatever `postgres:18` meant on the day,
and every CI run executed whatever `@v4` meant that morning, with the repository
token in reach. So:

* every image this repository runs and did not build is `repo:tag@sha256:<64 hex>`
  — the tag for the reader, the digest for Docker — in every Compose file, every
  CI service or job container, and every Dockerfile `FROM`;
* every Action that is not local (`./…`) is `owner/repo@<40-hex commit>` with a
  `# vX.Y.Z` comment saying which release that commit is;
* one name has one digest everywhere, so CI, the rehearsals and production run
  the same PostgreSQL and the same Action code.

How each digest and commit was checked against its upstream is recorded in
ADR 0179. Pure text: no Docker, no network, no database.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
COMPOSE_FILES = sorted([ROOT / "docker-compose.yml", *(ROOT / "deploy").glob("**/compose*.yml")])
DOCKERFILES = sorted(ROOT.glob("Dockerfile*"))

#: Images this repository builds itself and loads by tag; never pulled.
OWN_IMAGE = re.compile(r"^juristid-[a-z]+-web:")

#: `name:tag@sha256:<64 hex>`.
PINNED_IMAGE = re.compile(r"^[a-z0-9][a-z0-9./_-]*:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$")

#: `owner/repo[/path]@<40 hex>`.
PINNED_ACTION = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}$")

#: The line as written: the commit, then the release it is.
PINNED_ACTION_LINE = re.compile(r"uses:\s*(?P<ref>[^\s#]+)\s+#\s*v\d+\.\d+\.\d+\s*$")


def image_is_pinned(image: str) -> bool:
    return bool(OWN_IMAGE.match(image) or PINNED_IMAGE.match(image))


def action_is_pinned(uses: str) -> bool:
    if uses.startswith("./"):
        return True
    if uses.startswith("docker://"):
        return image_is_pinned(uses.removeprefix("docker://"))
    return bool(PINNED_ACTION.match(uses))


# -- what the files reference -------------------------------------------------


def _compose_images() -> list[tuple[str, str]]:
    found = []
    for path in COMPOSE_FILES:
        compose = yaml.safe_load(path.read_text(encoding="utf-8"))
        for name, service in (compose.get("services") or {}).items():
            if "image" in service:
                found.append((f"{path.relative_to(ROOT)}:{name}", str(service["image"])))
    return found


def _image_ref(node: Any) -> str | None:
    if isinstance(node, str):
        return node
    if isinstance(node, dict) and "image" in node:
        return str(node["image"])
    return None


def _workflow_images() -> list[tuple[str, str]]:
    found = []
    for path in WORKFLOWS:
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_name, job in workflow.get("jobs", {}).items():
            container = _image_ref(job.get("container"))
            if container:
                found.append((f"{path.name}:{job_name}:container", container))
            for service_name, service in (job.get("services") or {}).items():
                image = _image_ref(service)
                if image:
                    found.append((f"{path.name}:{job_name}:{service_name}", image))
    return found


def _dockerfile_images() -> list[tuple[str, str]]:
    found = []
    for path in DOCKERFILES:
        for line in path.read_text(encoding="utf-8").splitlines():
            words = line.split()
            if words and words[0].upper() == "FROM":
                image = next(w for w in words[1:] if not w.startswith("--"))
                found.append((path.name, image))
    return found


def _uses() -> list[tuple[str, str]]:
    found = []
    for path in WORKFLOWS:
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_name, job in workflow.get("jobs", {}).items():
            if "uses" in job:
                found.append((f"{path.name}:{job_name}", str(job["uses"])))
            for step in job.get("steps") or []:
                if "uses" in step:
                    found.append((f"{path.name}:{job_name}", str(step["uses"])))
    return found


ALL_IMAGES = _compose_images() + _workflow_images() + _dockerfile_images()


def test_the_scan_sees_what_it_is_meant_to_see() -> None:
    """A parser that found nothing would pass every test below."""
    images = dict(ALL_IMAGES)
    assert any(ref.startswith("postgres:18") for ref in images.values())
    assert any(ref.startswith("cloudflare/cloudflared:") for ref in images.values())
    assert len(_dockerfile_images()) >= 2
    assert len(_uses()) >= 30
    assert {path.name for path in WORKFLOWS} >= {"ci.yml", "release-image.yml", "main-proof.yml"}


# -- the rules ----------------------------------------------------------------


@pytest.mark.parametrize(("where", "image"), ALL_IMAGES, ids=[w for w, _ in ALL_IMAGES])
def test_every_third_party_image_is_pinned_by_digest(where: str, image: str) -> None:
    assert image_is_pinned(image), (
        f"{where}: {image!r} is not `name:tag@sha256:<digest>`; a tag is a name its "
        "publisher can move (ADR 0179)"
    )


def test_every_external_action_is_pinned_to_a_full_commit() -> None:
    loose = [f"{where}: {uses}" for where, uses in _uses() if not action_is_pinned(uses)]
    assert not loose, "Actions referenced by a movable name:\n" + "\n".join(loose)


def test_every_pinned_action_says_which_release_it_is() -> None:
    """The commit for GitHub, the version for the next reader and for an update."""
    unlabelled = []
    for path in WORKFLOWS:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip().removeprefix("- ")
            if not stripped.startswith("uses:") or "uses: ./" in stripped:
                continue
            if not PINNED_ACTION_LINE.search(stripped):
                unlabelled.append(f"{path.name}:{number}: {stripped}")
    assert not unlabelled, "no `# vX.Y.Z` beside the commit:\n" + "\n".join(unlabelled)


def test_one_image_name_has_one_digest_everywhere() -> None:
    """CI, the rehearsals and production run the same bytes for the same name."""
    digests: dict[str, set[str]] = defaultdict(set)
    for _, image in ALL_IMAGES:
        if "@" in image:
            name, _, digest = image.partition("@")
            digests[name].add(digest)
    split = {name: found for name, found in digests.items() if len(found) > 1}
    assert not split, split


def test_one_action_has_one_commit_everywhere() -> None:
    commits: dict[str, set[str]] = defaultdict(set)
    for _, uses in _uses():
        name, _, ref = uses.partition("@")
        commits[name].add(ref)
    split = {name: found for name, found in commits.items() if len(found) > 1}
    assert not split, split


def test_production_postgres_is_the_digest_production_runs() -> None:
    """The one pin whose value is load-bearing for the next deployment.

    A different digest means a different PostgreSQL build and, at the next `up`,
    a pull and a recreated database. This is the index production was running
    on 2026-10-22 (18.6); moving it is a decision, so it is spelled out here.
    """
    compose = yaml.safe_load((ROOT / "deploy/unraid-main/compose.yml").read_text("utf-8"))
    assert compose["services"]["db"]["image"] == (
        "postgres:18@sha256:06cad38a5d9f5d24b4d83d86def30795d5e4b757fedbf5281172b576dedcd941"
    )
    assert compose["services"]["tunnel"]["image"] == (
        "cloudflare/cloudflared:2026.8.2"
        "@sha256:0aa26e284f05e6c77ae375b8c9c11d9eb6a448fb7bcd8d40f31cb6176189eb38"
    )


# -- the rules refuse what they should --------------------------------------


@pytest.mark.parametrize(
    "image",
    [
        "postgres:18",
        "postgres",
        "cloudflare/cloudflared:latest",
        "cloudflare/cloudflared:2026.8.2",
        "postgres@sha256:" + "0" * 64,  # a digest with no tag for the reader
        "postgres:18@sha256:" + "0" * 63,
        "postgres:18@sha256:" + "G" * 64,
    ],
)
def test_an_image_without_a_tag_and_digest_is_refused(image: str) -> None:
    assert not image_is_pinned(image)


@pytest.mark.parametrize(
    "uses",
    [
        "actions/checkout@v4",
        "actions/checkout@v4.4.0",
        "actions/checkout@main",
        "actions/checkout@11d5960",  # a short SHA is a prefix anybody can collide with
        "docker://alpine:3",
    ],
)
def test_an_action_by_movable_name_is_refused(uses: str) -> None:
    assert not action_is_pinned(uses)


def test_a_local_action_and_a_full_commit_are_accepted() -> None:
    assert action_is_pinned("./.github/actions/setup")
    assert action_is_pinned("actions/checkout@11d5960a326750d5838078e36cf38b85af677262")
    assert action_is_pinned("docker://alpine:3.20@sha256:" + "a" * 64)
