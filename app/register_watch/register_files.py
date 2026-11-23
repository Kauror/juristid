"""The register's own file, brought onto the Teema with «Lisa märkena» (owner, 2026-11-22).

A finding used to reach the Teema as a `Menetluse areng` that said where the
document was and linked to it; the document itself stayed in the register, so
a lawyer opened the register, downloaded it and attached it by hand. Now the
press fetches the document's public files through the register watch's one
door (`sources.http`: fixed hosts, no redirects, a timeout, a size cap) and
hands them to `+ Märge`'s use case as its uploads, so they go through the same
checks, evidence store and malware scan as a file a person attached.

Where each register keeps its files (read 2026-11-22):

* **HTM and Kliima (PDR)** — one file per document:
  ``/api/v1/documents/<id>.json`` names it (``file``), ``/api/v1/documents/<id>/file``
  serves it.
* **The ministries on adr.rik.ee** — the document page links each file as
  ``/<code>/fail/<id>/<name>``.
* **The Riigikogu** — ``/api/documents/<uuid>`` lists a text's or an opinion's
  files, each with its ``accessRestrictionType``; only ``PUBLIC`` ones are taken,
  from ``/api/files/<uuid>/download``. A sitting (``event:``) and the bill itself
  (``draft:``) have no file of their own.

**Nothing here can stop the note.** A register that does not answer, a file of
a kind the evidence store does not take (a Riigikogu opinion is often `.html`),
or one too large, is left out and named; the note is written with its link as
before, and the person is told which file did not come.

**The press has a time budget (HR-04).** Everything it fetches — the listing and
every file — shares one deadline, `FETCH_BUDGET_SECONDS` from the press, well
inside the web worker's 120 s (`sources.http` says what the deadline bounds:
everything but the DNS lookup). A file not brought by then is left out like any other, and its
own address is written into the note (`Fetched.missed_links`), so it can still be
opened from the Teema. Nothing is fetched while a row is locked: the fetch runs
before `watch.add_to_teema` opens its transaction.
"""

from __future__ import annotations

import html
import os
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from django.core.files.uploadedfile import SimpleUploadedFile

from app.documents.uploads import EXTENSION_MIME_TYPES
from app.register_watch.models import WatchSource
from app.register_watch.sources import adr, pdr, riigikogu
from app.register_watch.sources.http import FetchError, get_bytes, get_json, get_text

#: More than this many files on one register document are left to the link.
MAX_FILES = 5
#: Seconds one «Lisa märkena» may spend on the register, listing and files together
#: (HR-04): far inside gunicorn's ``--timeout 120`` and the proxy's own limit.
FETCH_BUDGET_SECONDS = 40.0

_PDR_HOSTS = {WatchSource.HTM_REGISTER: pdr.HTM.host, WatchSource.KLIM_REGISTER: pdr.KLIM.host}


@dataclass(frozen=True)
class RemoteFile:
    host: str
    path: str
    name: str


@dataclass
class Fetched:
    """What the press brought: the files, and the names of those it could not."""

    uploads: list[Any] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)
    #: ``[{name, url}]`` of the missed files whose address is known, for the note.
    missed_links: list[dict[str, str]] = field(default_factory=list)
    #: ``[{name, url}]`` of the files in ``uploads``, in order: their links if the
    #: save has to go on without them.
    upload_links: list[dict[str, str]] = field(default_factory=list)
    #: The register could not be read at all (the files are unknown).
    unreachable: bool = False

    def miss(self, remote: RemoteFile) -> None:
        self.missed.append(remote.name)
        self.missed_links.append({"name": remote.name, "url": url_of(remote)})

    def give_up_uploads(self) -> None:
        """Move every fetched file to the missed ones: the save goes on without them."""
        self.missed.extend(one["name"] for one in self.upload_links)
        self.missed_links.extend(self.upload_links)
        self.uploads, self.upload_links = [], []


def accepted(name: str) -> bool:
    """Whether the evidence store takes a file of this name (`documents.uploads`)."""
    return os.path.splitext(name)[1].lower() in EXTENSION_MIME_TYPES


def pdr_files(host: str, key: str, data: Any) -> list[RemoteFile]:
    name = str(data.get("file") or "").strip() if isinstance(data, dict) else ""
    if not name:
        return []
    return [RemoteFile(host=host, path=f"/api/v1/documents/{key}/file", name=name)]


def adr_files(code: str, page: str) -> list[RemoteFile]:
    link = re.compile(rf'href="(/{re.escape(code)}/fail/\d+/[^"]+)"')
    found: list[RemoteFile] = []
    for path in link.findall(page):
        path = html.unescape(path)
        name = urllib.parse.unquote(path.rsplit("/", 1)[-1]).strip()
        if name and all(one.path != path for one in found):
            found.append(RemoteFile(host=adr.HOST, path=path, name=name))
    return found


def riigikogu_files(data: Any) -> list[RemoteFile]:
    files = data.get("files") if isinstance(data, dict) else None
    found: list[RemoteFile] = []
    for one in files or []:
        key = one.get("uuid")
        name = str(one.get("fileName") or "").strip()
        if key and name and one.get("accessRestrictionType") == "PUBLIC":
            found.append(
                RemoteFile(host=riigikogu.HOST, path=f"/api/files/{key}/download", name=name)
            )
    return found


def locate(source: str, external_id: str, *, deadline: float | None = None) -> list[RemoteFile]:
    """The files the register holds for one finding; raises `FetchError` if unread."""
    kind, _, key = external_id.partition(":")
    if kind == "doc" and source in _PDR_HOSTS:
        host = _PDR_HOSTS[WatchSource(source)]
        data = get_json(host, f"/api/v1/documents/{key}.json", deadline=deadline)
        return pdr_files(host, key, data)
    if kind == "adr":
        code, _, number = key.partition(":")
        if not code.isalnum() or not number.isdigit():
            return []
        page = get_text(adr.HOST, f"/{code}/dokument/{number}", deadline=deadline)
        return adr_files(code, page)
    if kind in ("text", "opinion") and source == WatchSource.RIIGIKOGU:
        if not re.fullmatch(r"[0-9a-f-]{36}", key):
            return []
        data = get_json(riigikogu.HOST, f"/api/documents/{key}", deadline=deadline)
        return riigikogu_files(data)
    return []


def fetch(source: str, external_id: str, *, budget: float = FETCH_BUDGET_SECONDS) -> Fetched:
    """Download what `locate` finds, as uploads `+ Märge` takes; never raises.

    The listing and every file share one deadline ``budget`` seconds away; once
    it has passed, the files still to come are not asked for and are missed
    with their links, like a file the register would not serve.
    """
    deadline = time.monotonic() + budget
    result = Fetched()
    try:
        files = locate(source, external_id, deadline=deadline)
    except FetchError:
        result.unreachable = True
        return result
    for remote in files[:MAX_FILES]:
        if not accepted(remote.name) or time.monotonic() >= deadline:
            result.miss(remote)
            continue
        try:
            content = get_bytes(remote.host, remote.path, deadline=deadline)
        except FetchError:
            result.miss(remote)
            continue
        result.uploads.append(SimpleUploadedFile(remote.name, content))
        result.upload_links.append({"name": remote.name, "url": url_of(remote)})
    for remote in files[MAX_FILES:]:
        result.miss(remote)
    return result


#: How many findings one pass looks up; the rest wait for the next night.
REMEMBER_LIMIT = 300


def url_of(remote: RemoteFile) -> str:
    return f"https://{remote.host}{remote.path}"


def remember(limit: int = REMEMBER_LIMIT) -> int:
    """Store each waiting finding's file links for its «Fail» links (owner, 2026-11-23).

    The row's title opens the register's own page, and a file is not always
    there to be found; this records where `locate` finds the document's files
    so the row can link each one directly. Only findings never looked up
    (``files`` is ``None``) and still waiting; a register that does not answer
    leaves the finding for the next pass. Never raises. Returns how many
    findings were stored.
    """
    from app.register_watch.models import FindingStatus, WatchFinding

    stored = 0
    waiting = (
        WatchFinding.objects.filter(status=FindingStatus.PENDING, files__isnull=True)
        .select_related("target")
        .order_by("-occurred_on", "pk")[:limit]
    )
    for finding in waiting:
        try:
            found = locate(finding.target.source, finding.external_id)
        except FetchError:
            continue  # unread tonight; looked up again on the next pass
        finding.files = [{"name": one.name, "url": url_of(one)} for one in found[:MAX_FILES]]
        finding.save(update_fields=["files", "updated_at"])
        stored += 1
    return stored
