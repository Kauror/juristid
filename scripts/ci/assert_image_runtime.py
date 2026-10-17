"""Ask the built image, from inside it, for what its Dockerfile promises.

Run by the container smoke job in CI, against the image it has just built:

    docker run --rm -e DJANGO_SECRET_KEY=... -e DJANGO_ALLOWED_HOSTS=localhost \\
      --entrypoint python juristid:ci scripts/ci/assert_image_runtime.py

`tests/test_deployment_image.py` reads the Dockerfile and `.dockerignore` as
text, which is fast and runs anywhere, and proves what the files *say*. This
proves what the build *made* (JSEC-007, JOPS-006, JSEC-004):

* the base is Debian 13;
* it runs as uid 10001, and that user owns the four storage directories and
  nothing else — the code under `/app` and the venv are root's and it cannot
  write to them;
* neither the system interpreter nor the venv carries pip, setuptools or wheel;
* the build context left out the test suites, the prose and every environment
  file, and kept the two `docs/` trees the application reads while it runs;
* OCR works on this base: the engine is there with Estonian and English, and the
  application's own image parser reads text off a scanned page with them. A
  missing language pack fails here twice — in `check_ocr_runtime --required`,
  which asks Tesseract for its installed languages, and in the recognition,
  where Tesseract refuses to load a language it does not have.

Every check prints one line, and the script exits non-zero if any failed, so a
single run reports all of them rather than the first.
"""

from __future__ import annotations

import io
import os
import pwd
import shutil
import subprocess
import sys
from pathlib import Path

APP = Path("/app")
VENV = Path("/opt/venv")
SYSTEM_PYTHON = "/usr/local/bin/python3"
APPLICATION_UID = 10001

#: The only directories under /app the application user may write.
WRITABLE = ("evidence", "derivatives", "legacy-source", "pending-uploads")

#: Left out by `.dockerignore`.
ABSENT = ("tests", "e2e", "docs/adr", "docs/master-specification.md")

#: Kept, because the running application or the image's own tooling reads them.
PRESENT = (
    "manage.py",
    "GIT_SHA",
    "BUILD_STAMP",
    "scripts/start-dev.sh",
    "docs/release-notes/uuendused.toml",
    "docs/data-contracts/excel-era-2026.toml",
)

INSTALLERS = ("pip", "setuptools", "wheel")

#: The word the scanned-page fixture carries; the same one the extraction tests
#: use (tests/synthetic_corpus.py), which are not in the image.
SCANNED_WORD = "MAARUS"

failures: list[str] = []


def check(ok: bool, message: str) -> None:
    print(("ok    " if ok else "FAIL  ") + message, flush=True)
    if not ok:
        failures.append(message)


def can_create_in(directory: Path) -> bool:
    probe = directory / f".image-probe-{os.getpid()}"
    try:
        with probe.open("wb"):
            pass
    except OSError:
        return False
    probe.unlink()
    return True


def can_open_for_writing(path: Path) -> bool:
    # Append mode, so a success changes nothing in the file.
    try:
        with path.open("ab"):
            pass
    except OSError:
        return False
    return True


def the_operating_system() -> None:
    fields = dict(
        line.split("=", 1)
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    codename = fields.get("VERSION_CODENAME", "").strip('"')
    print(f"      {fields.get('PRETTY_NAME', '?').strip(chr(34))}")
    check(codename == "trixie", f"the base is Debian 13 (trixie), not {codename!r}")


def the_user() -> None:
    uid = os.getuid()
    name = pwd.getpwuid(uid).pw_name
    check(
        uid == APPLICATION_UID and name == "juristid", f"runs as juristid/10001, not {name}/{uid}"
    )


def the_code_is_read_only() -> None:
    for directory in (
        APP,
        APP / "app",
        APP / "config",
        APP / "templates",
        APP / "static",
        APP / "staticfiles",
        APP / "scripts",
        VENV,
        VENV / "bin",
    ):
        owner = directory.stat().st_uid
        check(owner == 0, f"{directory} is owned by root (uid {owner})")
        check(not can_create_in(directory), f"{directory} refuses a new file")
    for path in (
        APP / "manage.py",
        APP / "config" / "settings.py",
        APP / "GIT_SHA",
        APP / "BUILD_STAMP",
        VENV / "bin" / "gunicorn",
        VENV / "pyvenv.cfg",
    ):
        check(not can_open_for_writing(path), f"{path} cannot be opened for writing")

    writable = {APP / name for name in WRITABLE}
    handed_over: list[str] = []
    world_writable: list[str] = []
    for root in (APP, VENV):
        for current, directories, files in os.walk(root):
            here = Path(current)
            directories[:] = [name for name in directories if here / name not in writable]
            for name in [*directories, *files]:
                entry = here / name
                status = entry.lstat()
                if status.st_uid == APPLICATION_UID:
                    handed_over.append(str(entry))
                if status.st_mode & 0o002 and not entry.is_symlink():
                    world_writable.append(str(entry))
    check(
        not handed_over,
        f"nothing outside the storage directories belongs to uid 10001 {handed_over[:5]}",
    )
    check(
        not world_writable,
        f"nothing under /app or /opt/venv is world-writable {world_writable[:5]}",
    )


def the_storage_directories_are_writable() -> None:
    for name in WRITABLE:
        directory = APP / name
        exists = directory.is_dir()
        check(exists, f"{directory} exists")
        if not exists:
            continue
        check(directory.stat().st_uid == APPLICATION_UID, f"{directory} belongs to uid 10001")
        check(can_create_in(directory), f"{directory} accepts a new file")


def there_is_no_installer() -> None:
    probe = (
        "import importlib.util, json; "
        f"print(json.dumps([m for m in {INSTALLERS!r} if importlib.util.find_spec(m)]))"
    )
    for interpreter in (SYSTEM_PYTHON, sys.executable):
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [interpreter, "-c", probe], capture_output=True, text=True, check=False
        )
        found = result.stdout.strip() or result.stderr.strip()
        check(
            result.returncode == 0 and found == "[]",
            f"{interpreter} has none of pip/setuptools/wheel (found {found})",
        )
    for command in ("pip", "pip3", "wheel"):
        check(shutil.which(command) is None, f"no `{command}` on PATH")


def the_build_context_was_filtered() -> None:
    for relative in ABSENT:
        check(not (APP / relative).exists(), f"/app/{relative} is not in the image")
    for relative in PRESENT:
        check((APP / relative).exists(), f"/app/{relative} is in the image")
    environment_files: list[str] = []
    for current, _directories, files in os.walk(APP):
        for name in files:
            is_env = name == ".env" or name.startswith(".env.") or name.endswith(".env")
            if is_env and name != ".env.example":
                environment_files.append(str(Path(current) / name))
    check(not environment_files, f"no environment file anywhere under /app {environment_files}")


def the_application_reads_its_documents() -> None:
    from app.core.release_notes import load_release_notes
    from app.legacy_import.contracts import load_contracts

    days = load_release_notes()
    check(bool(days), f"the release notes load ({len(days)} days)")
    contracts = load_contracts()
    check(bool(contracts), f"the era contracts load ({len(contracts)} years)")


def ocr_works_in_estonian_and_english() -> None:
    from django.core.management import call_command
    from django.core.management.base import CommandError

    try:
        call_command("check_ocr_runtime", "--required")
        check(True, "check_ocr_runtime --required")
    except CommandError as error:
        check(False, f"check_ocr_runtime --required: {error}")

    tesseract = shutil.which("tesseract")
    listed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [tesseract or "tesseract", "--list-langs"], capture_output=True, text=True, check=False
    )
    languages = set(listed.stdout.split())
    for language in ("est", "eng"):
        check(language in languages, f"tesseract lists `{language}`")

    from PIL import Image, ImageDraw
    from PIL.Image import Resampling

    from app.documents.extraction.base import SourceFile, registry
    from app.documents.extraction.errors import ExtractionFailed
    from app.documents.extraction.parsers import registry as _loaded  # registers the parsers

    del _loaded
    width, height = 900, 260
    small = Image.new("RGB", (width // 4, height // 4), "white")
    ImageDraw.Draw(small).text((6, height // 12), SCANNED_WORD, fill="black")
    buffer = io.BytesIO()
    small.resize((width, height), Resampling.LANCZOS).save(buffer, format="PNG")

    parser = registry.for_mime_type("image/png")
    if parser is None:
        check(False, "a parser is registered for image/png")
        return
    try:
        result = parser.parse(
            SourceFile(content=buffer.getvalue(), filename="skann.png", mime_type="image/png")
        )
    except ExtractionFailed as error:
        check(False, f"the image parser read the scanned page: {error}")
        return
    text = "\n".join(
        fragment.text for payload in result.derivatives for fragment in payload.fragments
    )
    check(SCANNED_WORD in text.upper(), f"OCR read {SCANNED_WORD!r} off a scanned page: {text!r}")


def main() -> int:
    the_operating_system()
    the_user()
    the_code_is_read_only()
    the_storage_directories_are_writable()
    there_is_no_installer()
    the_build_context_was_filtered()

    sys.path.insert(0, str(APP))
    import django

    django.setup()
    the_application_reads_its_documents()
    ocr_works_in_estonian_and_english()

    if failures:
        print(f"\n{len(failures)} image check(s) failed.", file=sys.stderr)
        return 1
    print("\nThe image is what its Dockerfile says it is.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
