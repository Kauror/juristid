"""Older opinion states the interface can no longer create, written server-side.

Since the Dokumendid `Arvamused` block was retired (docs/adr/0061, amendment of
2026-09-27) nothing in the browser starts a draft `Submission` or files a new
upload as `Arvamus`. Both states still exist wherever they were left before,
and `Lõpetamata arvamused` is what keeps them finishable — so the browser tests
that prove the finishing steps need the state first, and the only honest way to
get it is the way it was always made: through the same services, in the
server's own process.

A helper module rather than a test file on purpose: browser sharding is a pure
function of the collected `test_*.py` set, and a new one would move unrelated
files between shards (`ci_sharding.py`).

The scripts are literals and every value reaches them through the environment,
so `subprocess.run` is given no constructed arguments — the shape
`e2e/test_review_waiting_step.py` and `e2e/test_website_overview.py` use.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

#: A DRAFT on the Matter named by `E2E_LEGACY_MATTER`, started by the persona
#: `E2E_LEGACY_ACTOR`, exactly as `+ Uus arvamus` used to start one: a title,
#: the default kind, and — when `E2E_LEGACY_ADDRESSEE` / `E2E_LEGACY_COPIED`
#: name an organisation — its `Adressaadid` and `Teadmiseks`. With
#: `E2E_LEGACY_FILE` set, the draft's exact final evidence is attached as well,
#: as `Lisa fail` did.
_DRAFT_SCRIPT = (
    "import os;"
    "from django.contrib.auth import get_user_model;"
    "from app.matters.models import Matter;"
    "from app.organisations.models import Organisation;"
    "from app.submissions.services import attach_final_evidence, create_submission;"
    "m = Matter.objects.get(pk=os.environ['E2E_LEGACY_MATTER']);"
    "u = get_user_model().objects.get(upn=os.environ['E2E_LEGACY_ACTOR']);"
    "a = os.environ.get('E2E_LEGACY_ADDRESSEE', '');"
    "c = os.environ.get('E2E_LEGACY_COPIED', '');"
    "s = create_submission(matter=m, title=os.environ['E2E_LEGACY_TITLE'], actor=u,"
    " recipients=[Organisation.objects.get(name=a)] if a else None,"
    " for_information=[Organisation.objects.get(name=c)] if c else None);"
    "f = os.environ.get('E2E_LEGACY_FILE', '');"
    "f and attach_final_evidence(submission=s, content=b'%PDF-1.4 ' + f.encode(),"
    " original_filename=f, mime_type='application/pdf', actor=u);"
    "print(s.pk)"
)

#: A file on the Matter named by `E2E_LEGACY_MATTER` under the `Arvamus` role
#: and bound to no Submission — what `Lae dokument` used to write when
#: somebody chose `Arvamus`, and what only `Registreeri saatmine` finishes.
_STRANDED_UPLOAD_SCRIPT = (
    "import os;"
    "from django.contrib.auth import get_user_model;"
    "from app.documents.enums import DocumentRole;"
    "from app.documents.services import add_evidence_version, create_document;"
    "from app.matters.models import Matter;"
    "m = Matter.objects.get(pk=os.environ['E2E_LEGACY_MATTER']);"
    "u = get_user_model().objects.get(upn=os.environ['E2E_LEGACY_ACTOR']);"
    "f = os.environ['E2E_LEGACY_FILE'];"
    "d = create_document(matter=m, title=f, role=DocumentRole.KODA_SUBMISSION_FINAL,"
    " created_by=u);"
    "add_evidence_version(document=d, content=b'%PDF-1.4 ' + f.encode(),"
    " original_filename=f, mime_type='application/pdf', uploaded_by=u);"
    "print(d.pk)"
)


def matter_id_of(url: str) -> str:
    """The Matter's id from a `/teemad/<id>/…` address."""
    parts = [part for part in url.split("/") if part]
    return parts[parts.index("teemad") + 1]


def _in_the_server(script: str, environment: dict[str, str]) -> str:
    """Run ``script`` against the server's own database and return its last line.

    `DJANGO_SETTINGS_MODULE` is forced for the reason `run_worker` gives: pytest
    sets `config.test_settings` for itself and a child would inherit it.
    """
    result = subprocess.run(  # noqa: S603
        [sys.executable, "manage.py", "shell", "-c", script],
        cwd=REPOSITORY_ROOT,
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings", **environment},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    report = "\n".join(["stdout:", result.stdout, "stderr:", result.stderr])
    assert result.returncode == 0, report
    # The last line is the script's own: `shell` may first announce the objects
    # it imported automatically on stdout.
    lines = result.stdout.strip().splitlines()
    return lines[-1].strip() if lines else ""


def leave_a_draft(
    matter_url: str,
    *,
    title: str,
    actor_upn: str,
    filename: str = "",
    addressee: str = "",
    copied: str = "",
) -> str:
    """A draft opinion as `+ Uus arvamus` left one, optionally with its file.

    ``addressee`` and ``copied`` are organisation names, exactly as the
    catalogue holds them.
    """
    return _in_the_server(
        _DRAFT_SCRIPT,
        {
            "E2E_LEGACY_MATTER": matter_id_of(matter_url),
            "E2E_LEGACY_ACTOR": actor_upn,
            "E2E_LEGACY_TITLE": title,
            "E2E_LEGACY_FILE": filename,
            "E2E_LEGACY_ADDRESSEE": addressee,
            "E2E_LEGACY_COPIED": copied,
        },
    )


#: The catalogue id of the organisation named `E2E_LEGACY_ORGANISATION`.
_ORGANISATION_SCRIPT = (
    "import os;"
    "from app.organisations.models import Organisation;"
    "print(Organisation.objects.get(name=os.environ['E2E_LEGACY_ORGANISATION']).pk)"
)


def organisation_id(name: str) -> str:
    """An organisation's id, for a test that asks the register for it by address."""
    return _in_the_server(_ORGANISATION_SCRIPT, {"E2E_LEGACY_ORGANISATION": name})


def strand_an_opinion_upload(matter_url: str, *, filename: str, actor_upn: str) -> str:
    """An `Arvamus` upload no send accounts for, as `Lae dokument` left one."""
    return _in_the_server(
        _STRANDED_UPLOAD_SCRIPT,
        {
            "E2E_LEGACY_MATTER": matter_id_of(matter_url),
            "E2E_LEGACY_ACTOR": actor_upn,
            "E2E_LEGACY_FILE": filename,
        },
    )
