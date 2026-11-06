"""The production image is the scanned, runtime-checked image, by id (G-02).

`release-image.yml` builds the image afresh, and the Dockerfile's
`apt-get upgrade` takes whatever Debian has published by then, so the image a
release archives is not the `juristid:ci` that CI scanned. Before this, the
release job checked only `GIT_SHA`, the platform and an import: an image with a
fixable HIGH that CI's build never had could reach production with every check
green. Owner's rule: verify the exact image digest that reaches production.

Three halves, none needing Docker or a network:

* the workflow, read as YAML: the release job runs CI's own runtime check and
  CI's own Trivy gate on the image it archives, before it saves anything, and
  writes into the manifest the image id each gate saw;
* `scripts/ci/release_image_record.py`, run: a scan report about another image,
  a fixable HIGH/CRITICAL, or an archive holding another image is refused;
* `scripts/deploy/juristid-verify-release-artifact.sh`, run: the receiving
  side refuses an artifact whose manifest does not name one scanned image, and
  a loaded image that is not it.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
RELEASE = WORKFLOWS / "release-image.yml"
CI = WORKFLOWS / "ci.yml"
RECORD = ROOT / "scripts" / "ci" / "release_image_record.py"
VERIFY = ROOT / "scripts" / "deploy" / "juristid-verify-release-artifact.sh"
DOCKERFILE = ROOT / "Dockerfile"

#: As in tests/test_deployment_scripts.py: present on the runner and in Git for
#: Windows, so a missing one is a broken environment, not a reason to skip.
BASH = shutil.which("bash") or "bash"

RELEASE_IMAGE = '"juristid-main-web:$SHORT"'
CI_IMAGE = "juristid:ci"
RUNTIME_STEP = "The image is what its Dockerfile says it is"
GATE_STEP = "The image has no fixable HIGH or CRITICAL vulnerability"


# -- the workflow ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def release() -> list[dict[str, Any]]:
    return list(yaml.safe_load(RELEASE.read_text(encoding="utf-8"))["jobs"]["build"]["steps"])


@pytest.fixture(scope="module")
def container() -> list[dict[str, Any]]:
    return list(yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]["container"]["steps"])


def _joined(script: str) -> list[str]:
    """The script's commands, one per line, with `\\` continuations joined."""
    return [
        re.sub(r"\s+", " ", line).strip()
        for line in script.replace("\\\n", " ").splitlines()
        if line.strip()
    ]


def _index(steps: list[dict[str, Any]], predicate) -> int:
    found = [index for index, step in enumerate(steps) if predicate(step)]
    assert len(found) == 1, f"expected exactly one such step, found {len(found)}"
    return found[0]


def _runs(needle: str):
    return lambda step: needle in str(step.get("run", ""))


def _named(name: str):
    return lambda step: step.get("name") == name


def _trivy_lines(steps: list[dict[str, Any]], image: str) -> dict[str, str]:
    """The full-report and gate invocations, with the image name made neutral."""
    lines = {}
    for step in steps:
        for line in _joined(str(step.get("run", ""))):
            if not line.startswith("trivy image "):
                continue
            key = "gate" if "--exit-code 1" in line else "report"
            assert key not in lines, f"two Trivy {key} invocations"
            lines[key] = line.replace(image, "<image>")
    return lines


def _runtime_line(steps: list[dict[str, Any]], image: str) -> str:
    found = [
        line.replace(image, "<image>")
        for step in steps
        for line in _joined(str(step.get("run", "")))
        if "assert_image_runtime.py" in line and line.startswith("docker run")
    ]
    assert len(found) == 1, found
    return found[0]


def test_the_release_runs_cis_runtime_check_on_its_own_image(release, container) -> None:
    assert _runtime_line(release, RELEASE_IMAGE) == _runtime_line(container, CI_IMAGE)
    step = release[_index(release, _named(RUNTIME_STEP))]
    ci_step = container[_index(container, _named(RUNTIME_STEP))]
    assert step["env"] == ci_step["env"]
    assert 'echo "RUNTIME_CHECKED_IMAGE_ID=$checked" >> "$GITHUB_ENV"' in step["run"]


def test_the_release_runs_cis_trivy_gate_on_its_own_image(release, container) -> None:
    """Same scanner digest, same report, same gate: severity, unfixed rule, exit code."""
    assert _trivy_lines(release, RELEASE_IMAGE) == _trivy_lines(container, CI_IMAGE)
    scan = release[_index(release, _named(GATE_STEP))]
    ci_scan = container[_index(container, _named(GATE_STEP))]
    assert scan["env"]["TRIVY_IMAGE"] == ci_scan["env"]["TRIVY_IMAGE"]
    assert re.fullmatch(
        r"aquasec/trivy:\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", scan["env"]["TRIVY_IMAGE"]
    )
    gate = _trivy_lines(release, RELEASE_IMAGE)["gate"]
    assert "--ignore-unfixed" in gate and "--severity HIGH,CRITICAL" in gate


def test_neither_workflow_has_an_ignore_file_the_other_lacks() -> None:
    for path in (RELEASE, CI):
        text = path.read_text(encoding="utf-8")
        assert "--ignorefile" not in text and ".trivyignore" not in text, path.name
    assert not (ROOT / ".trivyignore").exists()


def test_the_gates_run_after_the_build_and_before_anything_is_saved(release) -> None:
    build = _index(release, _named("Build"))
    runtime = _index(release, _named(RUNTIME_STEP))
    gate = _index(release, _named(GATE_STEP))
    save = _index(release, _named("Save and digest"))
    upload = _index(release, _named("Upload"))
    assert build < runtime < gate < save < upload


def test_a_failed_gate_stops_the_job(release) -> None:
    """No `continue-on-error`, and nothing that saves or uploads the image runs regardless."""
    for step in release:
        assert not step.get("continue-on-error"), step.get("name")
    for name in ("The image is what its Dockerfile says it is", "Save and digest", "Upload"):
        step = release[_index(release, _named(name))]
        assert "if" not in step, f"{name!r} must run only when every step before it passed"
    gate = release[_index(release, _named(GATE_STEP))]
    assert "if" not in gate
    for name in ("The image is what its Dockerfile says it is", "Save and digest"):
        assert "set -euo pipefail" in release[_index(release, _named(name))]["run"], name
    assert "set -euo pipefail" in gate["run"]


def test_the_scan_is_proved_to_be_about_the_released_image(release) -> None:
    step = release[_index(release, _named(GATE_STEP))]
    script = step["run"]
    assert 'git show "$WORKFLOW_SHA:scripts/ci/release_image_record.py"' in script
    assert step["env"]["WORKFLOW_SHA"] == "${{ github.sha }}"
    record = [line for line in _joined(script) if 'release_image_record.py" scan' in line]
    assert len(record) == 1
    assert '--image-id "$scanned"' in record[0]
    assert "--report image-scan/trivy-image.json" in record[0]
    assert '--env-file "$GITHUB_ENV"' in record[0]
    # The id is taken before the scan and asked again after it.
    assert (
        script.count("""docker image inspect "juristid-main-web:$SHORT" --format '{{.Id}}'""") == 2
    )


def test_the_archive_is_tied_to_the_checked_image(release) -> None:
    script = release[_index(release, _named("Save and digest"))]["run"]
    assert 'test "$image_id" = "$RUNTIME_CHECKED_IMAGE_ID"' in script
    assert 'test "$image_id" = "$SCANNED_IMAGE_ID"' in script
    assert 'release_image_record.py" archive --archive "$archive" --image-id "$image_id"' in script
    assert 'test "$archive_image_id" = "$image_id"' in script
    # The comparisons come before the archive is written.
    assert script.index("$SCANNED_IMAGE_ID") < script.index("docker save")


def _manifest_keys(release) -> set[str]:
    script = release[_index(release, _named("Save and digest"))]["run"]
    return set(re.findall(r'echo "([a-z_0-9]+):', script))


def test_the_manifest_names_what_was_checked(release) -> None:
    keys = _manifest_keys(release)
    for key in (
        "image_id",
        "scanned_image_id",
        "runtime_checked_image_id",
        "archive_image_id",
        "runtime_check",
        "image_scan",
        "image_scanner",
        "image_scan_db",
        "image_scan_findings",
        "image_scan_fixable_high_critical",
        "image_scan_report_sha256",
        "archive_sha256",
        "os_packages_sha256",
    ):
        assert key in keys, f"the release manifest has no {key}"


def test_every_field_the_receiving_side_requires_is_written(release) -> None:
    required = set(re.findall(r'"\$\(required ([a-z_0-9]+)\)"', VERIFY.read_text(encoding="utf-8")))
    assert {"scanned_image_id", "runtime_check", "os_packages_sha256"} <= required
    missing = required - _manifest_keys(release)
    assert not missing, f"the verifier requires fields the workflow never writes: {missing}"


def test_the_full_scan_report_is_kept_three_days_only(release) -> None:
    step = release[_index(release, _named("Upload the image scan"))]
    assert step["with"]["path"] == "image-scan/"
    assert int(step["with"]["retention-days"]) <= 3
    assert "!cancelled()" in step["if"]


def test_the_dockerfile_no_longer_claims_the_release_image_is_the_scanned_build() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "built and archived once" not in text
    assert "release-image.yml" in text
    assert "scanned_image_id" in text


# -- scripts/ci/release_image_record.py ------------------------------------------------


def _record_module():
    spec = importlib.util.spec_from_file_location("release_image_record", RECORD)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


record = _record_module()

CONFIG = json.dumps({"architecture": "amd64", "os": "linux", "config": {}}).encode()
IMAGE = "sha256:" + hashlib.sha256(CONFIG).hexdigest()
OTHER = "sha256:" + "0" * 64


def _finding(severity: str, status: str, name: str = "CVE-2026-0001") -> dict[str, str]:
    return {
        "VulnerabilityID": name,
        "PkgName": "libexample",
        "InstalledVersion": "1.0",
        "FixedVersion": "1.1" if status == "fixed" else "",
        "Severity": severity,
        "Status": status,
    }


def _report(image_id: str, *findings: dict[str, str]) -> dict[str, Any]:
    return {
        "Metadata": {"ImageID": image_id},
        "Results": [{"Target": "debian", "Vulnerabilities": list(findings)}, {"Target": "venv"}],
    }


def test_a_report_with_only_unfixed_high_findings_passes_and_is_counted(tmp_path) -> None:
    path = tmp_path / "trivy.json"
    path.write_text(
        json.dumps(
            _report(
                IMAGE,
                _finding("HIGH", "affected"),
                _finding("CRITICAL", "will_not_fix"),
                _finding("MEDIUM", "fixed"),
                _finding("LOW", "fixed"),
            )
        ),
        encoding="utf-8",
    )
    version = tmp_path / "version.json"
    version.write_text(json.dumps({"VulnerabilityDB": {"UpdatedAt": "2026-11-06T00:00:00Z"}}))
    env = tmp_path / "env"
    assert (
        record.main(
            [
                "scan",
                "--report",
                str(path),
                "--image-id",
                IMAGE,
                "--version-report",
                str(version),
                "--env-file",
                str(env),
            ]
        )
        == 0
    )
    written = dict(line.split("=", 1) for line in env.read_text().splitlines())
    assert written["SCANNED_IMAGE_ID"] == IMAGE
    assert written["IMAGE_SCAN_FINDINGS"] == "CRITICAL=1 HIGH=1 MEDIUM=1 LOW=1 UNKNOWN=0"
    assert written["IMAGE_SCAN_FIXABLE_HIGH_CRITICAL"] == "0"
    assert written["IMAGE_SCAN_DB"] == "2026-11-06T00:00:00Z"
    assert written["IMAGE_SCAN_REPORT_SHA256"] == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("content", ["", "[]", '{"VulnerabilityDB": null}'])
def test_an_unreadable_database_version_is_recorded_as_unknown(tmp_path, content) -> None:
    """Metadata, not a gate: the scan's verdict does not depend on it."""
    path = tmp_path / "trivy.json"
    path.write_text(json.dumps(_report(IMAGE)))
    version = tmp_path / "version.json"
    version.write_text(content)
    env = tmp_path / "env"
    arguments = ["--version-report", str(version), "--env-file", str(env)]
    assert record.main(["scan", "--report", str(path), "--image-id", IMAGE, *arguments]) == 0
    assert "IMAGE_SCAN_DB=unknown" in env.read_text()


@pytest.mark.parametrize("severity", ["HIGH", "CRITICAL"])
def test_a_fixable_high_or_critical_is_refused_even_after_the_gate(
    tmp_path, capsys, severity
) -> None:
    path = tmp_path / "trivy.json"
    path.write_text(json.dumps(_report(IMAGE, _finding(severity, "fixed", "CVE-2026-109518"))))
    env = tmp_path / "env"
    assert (
        record.main(["scan", "--report", str(path), "--image-id", IMAGE, "--env-file", str(env)])
        == 1
    )
    assert "CVE-2026-109518" in capsys.readouterr().err
    assert not env.exists(), "a refused scan wrote a summary for the manifest"


@pytest.mark.parametrize(
    "metadata", [{"ImageID": OTHER}, {}, None], ids=["another image", "no image id", "no metadata"]
)
def test_a_report_about_another_image_is_refused(tmp_path, capsys, metadata) -> None:
    report = _report(IMAGE)
    report["Metadata"] = metadata
    path = tmp_path / "trivy.json"
    path.write_text(json.dumps(report))
    assert record.main(["scan", "--report", str(path), "--image-id", IMAGE]) == 1
    assert "not the image being released" in capsys.readouterr().err


def test_an_id_that_is_not_an_image_id_is_refused(tmp_path, capsys) -> None:
    path = tmp_path / "trivy.json"
    path.write_text(json.dumps(_report("juristid-main-web:abc")))
    assert record.main(["scan", "--report", str(path), "--image-id", "juristid-main-web:abc"]) == 1
    assert "not an image id" in capsys.readouterr().err


def _archive(path: Path, members: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return path


def _oci_archive(path: Path, config: bytes = CONFIG, *, name: str | None = None) -> Path:
    digest = hashlib.sha256(CONFIG).hexdigest()
    entry = name or f"blobs/sha256/{digest}"
    manifest = [{"Config": entry, "RepoTags": ["juristid-main-web:d4accd2253f2"], "Layers": []}]
    return _archive(
        path,
        {
            "blobs/sha256/" + "a" * 64: b"layer bytes",
            entry: config,
            "index.json": b"{}",
            "manifest.json": json.dumps(manifest).encode(),
        },
    )


def test_the_archive_holds_the_checked_image(tmp_path) -> None:
    archive = _oci_archive(tmp_path / "image.tar.gz")
    assert record.archive_image_id(archive) == IMAGE
    assert record.main(["archive", "--archive", str(archive), "--image-id", IMAGE]) == 0


def test_the_older_docker_save_layout_is_read_too(tmp_path) -> None:
    digest = hashlib.sha256(CONFIG).hexdigest()
    archive = _oci_archive(tmp_path / "image.tar.gz", name=f"{digest}.json")
    assert record.archive_image_id(archive) == IMAGE


def test_an_archive_of_another_image_is_refused(tmp_path, capsys) -> None:
    archive = _oci_archive(tmp_path / "image.tar.gz")
    assert record.main(["archive", "--archive", str(archive), "--image-id", OTHER]) == 1
    assert "not " + OTHER in capsys.readouterr().err


def test_an_archive_whose_config_is_not_what_its_name_says_is_refused(tmp_path, capsys) -> None:
    archive = _oci_archive(tmp_path / "image.tar.gz", config=CONFIG + b" ")
    assert record.main(["archive", "--archive", str(archive), "--image-id", IMAGE]) == 1
    assert "does not hash to its name" in capsys.readouterr().err


def test_an_archive_of_two_images_is_refused(tmp_path, capsys) -> None:
    digest = hashlib.sha256(CONFIG).hexdigest()
    entry = f"blobs/sha256/{digest}"
    manifest = [{"Config": entry}, {"Config": entry}]
    archive = _archive(
        tmp_path / "image.tar.gz", {entry: CONFIG, "manifest.json": json.dumps(manifest).encode()}
    )
    assert record.main(["archive", "--archive", str(archive), "--image-id", IMAGE]) == 1
    assert "exactly one image" in capsys.readouterr().err


# -- scripts/deploy/juristid-verify-release-artifact.sh -------------------------------

TAG = "d4accd2253f2"
REVISION = "d4accd2253f2c2e5ef4ffb5bfaf9b961740513a6"


def _artifact(directory: Path, **overrides: str | None) -> dict[str, str]:
    """A release artifact as the workflow writes it, with any field replaced or removed."""
    archive = directory / f"juristid-main-web-{TAG}.tar.gz"
    archive.write_bytes(b"synthetic image archive")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    # Unix line endings, as the runner writes them: `sha256sum -c` reads a CR
    # as part of the file name.
    (directory / f"{archive.name}.sha256").write_bytes(f"{digest}  {archive.name}\n".encode())
    packages = directory / f"os-packages-{TAG}.txt"
    packages.write_bytes(b"apt=3.0.3\nlibc6=2.41-12\n")
    fields: dict[str, str | None] = {
        "revision": REVISION,
        "image_tag": f"juristid-main-web:{TAG}",
        "image_id": IMAGE,
        "scanned_image_id": IMAGE,
        "runtime_checked_image_id": IMAGE,
        "archive_image_id": IMAGE,
        "runtime_check": "PASS scripts/ci/assert_image_runtime.py, from inside the image",
        "image_scan": "PASS no fixable HIGH or CRITICAL",
        "image_scan_fixable_high_critical": "0",
        "archive_sha256": digest,
        "os_packages_sha256": hashlib.sha256(packages.read_bytes()).hexdigest(),
    }
    fields.update(overrides)
    (directory / f"release-manifest-{TAG}.txt").write_text(
        "".join(f"{key}:   {value}\n" for key, value in fields.items() if value is not None),
        newline="\n",
    )
    return {"digest": digest}


def verify(directory: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - a fixed interpreter and a repository path
        [BASH, str(VERIFY), "--dir", directory.as_posix(), "--tag", TAG, *extra],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_a_scanned_checked_artifact_is_accepted(tmp_path) -> None:
    made = _artifact(tmp_path)
    result = verify(
        tmp_path,
        "--revision",
        REVISION,
        "--archive-sha256",
        made["digest"],
        "--loaded-image-id",
        IMAGE,
    )
    assert result.returncode == 0, result.stderr
    assert f"holds {IMAGE}, scanned and runtime-checked, and is the image loaded" in result.stdout


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"scanned_image_id": None}, "the manifest has no scanned_image_id"),
        ({"runtime_checked_image_id": None}, "the manifest has no runtime_checked_image_id"),
        ({"os_packages_sha256": None}, "the manifest has no os_packages_sha256"),
        ({"scanned_image_id": OTHER}, f"scanned_image_id {OTHER} is not image_id"),
        ({"runtime_checked_image_id": OTHER}, "runtime_checked_image_id"),
        ({"archive_image_id": OTHER}, f"archive_image_id {OTHER} is not image_id"),
        ({"image_id": "juristid-main-web:latest"}, "is not an image id"),
        ({"runtime_check": "FAIL 3 image check(s) failed"}, "runtime_check is 'FAIL"),
        ({"image_scan": "FAIL"}, "image_scan is 'FAIL', not PASS"),
        ({"image_scan_fixable_high_critical": "1"}, "image_scan_fixable_high_critical is 1"),
        ({"archive_sha256": "f" * 64}, "is not the .sha256 digest"),
        ({"os_packages_sha256": "e" * 64}, "is not the list the manifest hashed"),
        ({"revision": "e" * 40}, "is not tag"),
    ],
    ids=lambda value: value if isinstance(value, str) else "-".join(value),
)
def test_an_artifact_that_does_not_name_one_checked_image_is_refused(
    tmp_path, overrides, reason
) -> None:
    _artifact(tmp_path, **overrides)
    result = verify(tmp_path)
    assert result.returncode == 1, result.stdout
    assert reason in result.stderr, result.stderr


def test_a_loaded_image_that_is_not_the_scanned_one_is_refused(tmp_path) -> None:
    _artifact(tmp_path)
    result = verify(tmp_path, "--loaded-image-id", OTHER)
    assert result.returncode == 1
    assert f"the loaded image is {OTHER}, not the scanned {IMAGE}" in result.stderr


def test_an_archive_changed_after_it_was_digested_is_refused(tmp_path) -> None:
    _artifact(tmp_path)
    (tmp_path / f"juristid-main-web-{TAG}.tar.gz").write_bytes(b"different bytes")
    result = verify(tmp_path)
    assert result.returncode == 1
    assert "does not match" in result.stderr


def test_an_archive_other_than_the_one_the_kit_recorded_is_refused(tmp_path) -> None:
    _artifact(tmp_path)
    result = verify(tmp_path, "--archive-sha256", "a" * 64)
    assert result.returncode == 1
    assert "is not the expected" in result.stderr


def test_another_revision_than_the_kit_names_is_refused(tmp_path) -> None:
    _artifact(tmp_path)
    result = verify(tmp_path, "--revision", TAG + "0" * 28)
    assert result.returncode == 1
    assert "is not " + TAG + "0" * 28 in result.stderr


def test_a_manifest_from_before_the_release_scanned_its_image_is_refused(tmp_path) -> None:
    """r49's manifest shape: archive and image id, and no word about a scan."""
    _artifact(
        tmp_path,
        scanned_image_id=None,
        runtime_checked_image_id=None,
        archive_image_id=None,
        runtime_check=None,
        image_scan=None,
        image_scan_fixable_high_critical=None,
        os_packages_sha256=None,
    )
    result = verify(tmp_path)
    assert result.returncode == 1
    assert "built before the release job scanned its image" in result.stderr


def test_a_missing_file_is_refused(tmp_path) -> None:
    _artifact(tmp_path)
    (tmp_path / f"os-packages-{TAG}.txt").unlink()
    result = verify(tmp_path)
    assert result.returncode == 1
    assert "missing" in result.stderr


def _flip_last_byte(archive: Path) -> None:
    data = bytearray(archive.read_bytes())
    data[-1] ^= 0xFF
    archive.write_bytes(bytes(data))


def test_a_sha256_file_naming_another_file_does_not_vouch_for_the_archive(tmp_path) -> None:
    """QA 2026-11 (G-02), «BYPASS?»: the archive tampered, its `.sha256` pointing
    at the package list (whose hash it carries) and the manifest's
    `archive_sha256` set to match. `sha256sum -c` checked the package list and
    the artifact verified; the archive is now hashed by its own name."""
    _artifact(tmp_path)
    archive = tmp_path / f"juristid-main-web-{TAG}.tar.gz"
    _flip_last_byte(archive)
    packages = tmp_path / f"os-packages-{TAG}.txt"
    vouched = hashlib.sha256(packages.read_bytes()).hexdigest()
    (tmp_path / f"{archive.name}.sha256").write_bytes(f"{vouched}  {packages.name}\n".encode())
    manifest = tmp_path / f"release-manifest-{TAG}.txt"
    manifest.write_text(
        re.sub(r"(?m)^archive_sha256:.*$", f"archive_sha256:   {vouched}", manifest.read_text()),
        newline="\n",
    )

    result = verify(tmp_path)

    assert result.returncode == 1, result.stdout
    assert f"names {packages.name}, not {archive.name}" in result.stderr


def test_a_sha256_file_naming_the_archive_with_another_digest_is_refused(tmp_path) -> None:
    """The `.sha256` names the archive but carries the manifest's (forged) digest."""
    _artifact(tmp_path, archive_sha256="c" * 64)
    archive = tmp_path / f"juristid-main-web-{TAG}.tar.gz"
    (tmp_path / f"{archive.name}.sha256").write_bytes(f"{'c' * 64}  {archive.name}\n".encode())
    result = verify(tmp_path)
    assert result.returncode == 1, result.stdout
    assert "does not match" in result.stderr and "it hashes to" in result.stderr


@pytest.mark.parametrize(
    "content",
    [
        "{digest}  {name}\n{digest}  {name}\n",
        "{digest}  ./{name}\n",
        "{digest}  {name}\r\n",
        "{digest}  {name}",
        "{digest}{name}\n",
        "",
    ],
    ids=["two-lines", "path-prefix", "crlf", "no-newline", "no-separator", "empty"],
)
def test_a_sha256_file_that_is_not_one_line_naming_the_archive_is_refused(
    tmp_path, content
) -> None:
    made = _artifact(tmp_path)
    name = f"juristid-main-web-{TAG}.tar.gz"
    (tmp_path / f"{name}.sha256").write_bytes(
        content.format(digest=made["digest"], name=name).encode()
    )
    result = verify(tmp_path)
    assert result.returncode == 1, result.stdout
    assert "release artifact refused" in result.stderr


def test_the_binary_mode_sha256_line_is_accepted(tmp_path) -> None:
    made = _artifact(tmp_path)
    name = f"juristid-main-web-{TAG}.tar.gz"
    (tmp_path / f"{name}.sha256").write_bytes(f"{made['digest']} *{name}\n".encode())
    result = verify(tmp_path, "--archive-sha256", made["digest"])
    assert result.returncode == 0, result.stderr
