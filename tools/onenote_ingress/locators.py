"""What a OneNote link in the register actually says, without guessing.

Lovable's ``onenote_link`` is free text a person pasted. The shapes observed in
the operational register (counts are in the Phase 1 report, never here) are:

* ``…/_layouts/Doc.aspx?sourcedoc={NB}&wd=target(Section.one|SGUID/Title|PGUID/)``
  ``&wdpartid=…&wdsectionfileid=…&end``
  — the web link OneNote's *Copy link to page* produces;
* the same, followed by a space and an ``onenote:https://…/Section.one#Title&section-id={SGUID}&page-id={PGUID}&end``
  client link — both halves name the page and they must agree;
* ``…/_layouts/OneNote.aspx?id=/sites/…/Notebook&wd=target(…)`` — the
  notebook opened in the browser; ``id`` here is a **notebook path**, not a
  page identifier;
* any of those under ``/_vti_history/<n>/`` — a SharePoint version-history
  prefix that does not change which page the target names;
* ``…/_layouts/15/Doc.aspx?…&action=edit&wd=target(…)&wdorigin=…`` with
  lower-case GUIDs;
* plain SharePoint document-library paths, which are not OneNote at all.

The rules this module keeps:

* The raw text is preserved byte for byte; every derived field is beside it.
* A GUID is compared case-insensitively *as a GUID* (RFC 4122 hex), so it is
  canonicalised to upper case for comparison. Nothing else is case-folded:
  section file names, titles and opaque provider IDs keep their case.
* ``sourcedoc`` and ``wdsectionfileid`` identify the notebook and the section
  *file*; they never identify a page. The notebook GUID is the same for every
  page, so mistaking it for a page id maps every topic onto one identity — the
  ADR 0015 failure in a new form.
* Fragments are kept. In the client link the fragment *is* the identity.
* The title inside a target is a hint for a reviewer, never an identity.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from enum import StrEnum

_GUID = r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
_GUID_RE = re.compile(rf"^\{{?({_GUID})\}}?$")

#: ``target(Section.one|SGUID/Title|PGUID/)``, ``target(Group/Section.one|SGUID/)``.
#: The page title may itself contain ``|``, ``/`` and escaped ``\)``, so the
#: GUIDs are anchored from the right and the title is whatever lies between.
_TARGET_PAGE = re.compile(
    rf"^(?P<section>.+?\.one)\|(?P<sguid>{_GUID})/(?P<title>.*)\|(?P<pguid>{_GUID})/?$",
    re.DOTALL,
)
_TARGET_SECTION = re.compile(rf"^(?P<section>.+?\.one)\|(?P<sguid>{_GUID})/?$", re.DOTALL)
_HISTORY = re.compile(r"/_vti_history/(?P<n>\d+)(?=/)")
_CLIENT_SPLIT = re.compile(r"\s+(?=onenote:)", re.IGNORECASE)
_FRAGMENT_PARAM = re.compile(rf"(?:^|&)(?P<key>section-id|page-id)=\{{?(?P<guid>{_GUID})\}}?", re.I)


class LinkFamily(StrEnum):
    EMPTY = "EMPTY"
    WEB_DOC_TARGET = "WEB_DOC_TARGET"  # Doc.aspx ?sourcedoc&wd=target(...)
    WEB_DOC_TARGET_WITH_CLIENT = "WEB_DOC_TARGET_WITH_CLIENT"  # + onenote: client link
    WEB_ONENOTE_ASPX_TARGET = "WEB_ONENOTE_ASPX_TARGET"  # OneNote.aspx ?id=<notebook path>&wd=
    CLIENT_ONLY = "CLIENT_ONLY"  # onenote:https://…#…&page-id={}
    WEB_NOTEBOOK_ONLY = "WEB_NOTEBOOK_ONLY"  # a notebook link with no target
    SHAREPOINT_PATH = "SHAREPOINT_PATH"  # a document-library path, not OneNote
    OTHER_HOST = "OTHER_HOST"
    UNPARSEABLE = "UNPARSEABLE"


class TargetKind(StrEnum):
    NONE = "NONE"
    PAGE = "PAGE"
    SECTION = "SECTION"


def canonical_guid(value: str) -> str:
    """``{abc…}`` / ``ABC…`` / ``%7Babc…%7D`` → ``ABC…``; anything else → ``""``."""
    text = urllib.parse.unquote(value or "").strip()
    match = _GUID_RE.match(text)
    return match.group(1).upper() if match else ""


@dataclass(frozen=True)
class ParsedLocator:
    raw: str
    family: LinkFamily
    host: str = ""
    site_path: str = ""
    history_version: str = ""
    notebook_guid: str = ""  # sourcedoc
    notebook_path: str = ""  # OneNote.aspx ?id= — a notebook path, never a page id
    section_file_guid: str = ""  # wdsectionfileid — the .one file, not the section object
    target_kind: TargetKind = TargetKind.NONE
    target_section_path: str = ""  # "Group/Section.one", decoded, case preserved
    section_guid: str = ""
    page_guid: str = ""
    title_hint: str = ""
    client_section_guid: str = ""
    client_page_guid: str = ""
    client_path: str = ""  # decoded path of the onenote: link, for scope checks
    wd_decode_passes: int = 0
    problems: tuple[str, ...] = field(default_factory=tuple)

    @property
    def page_guids(self) -> frozenset[str]:
        """Every page GUID the locator asserts. More than one is a conflict."""
        return frozenset(g for g in (self.page_guid, self.client_page_guid) if g)


def _site_path(path: str) -> str:
    match = re.match(r"^(/sites/[^/]+)", path)
    return match.group(1) if match else ""


def _parse_target(wd: str) -> tuple[TargetKind, str, str, str, str, int, list[str]]:
    """Decode ``wd`` until it reads ``target(…)``; at most two extra passes."""
    problems: list[str] = []
    value = wd
    passes = 0
    while not value.startswith("target(") and "%28" in value[:12] and passes < 2:
        value = urllib.parse.unquote(value)
        passes += 1
    if passes:
        problems.append("WD_MULTIPLY_ENCODED")
    if not value.startswith("target("):
        return TargetKind.NONE, "", "", "", "", passes, [*problems, "WD_NOT_A_TARGET"]
    body = value[len("target(") :]
    if body.endswith(")"):
        body = body[:-1]
    else:
        problems.append("WD_TARGET_UNTERMINATED")
    page = _TARGET_PAGE.match(body)
    if page:
        title = page.group("title").replace("\\)", ")").replace("\\|", "|")
        return (
            TargetKind.PAGE,
            page.group("section"),
            page.group("sguid").upper(),
            page.group("pguid").upper(),
            title,
            passes,
            problems,
        )
    section = _TARGET_SECTION.match(body)
    if section:
        return (
            TargetKind.SECTION,
            section.group("section"),
            section.group("sguid").upper(),
            "",
            "",
            passes,
            problems,
        )
    return TargetKind.NONE, "", "", "", "", passes, [*problems, "WD_TARGET_UNRECOGNISED"]


def _parse_client(client: str) -> tuple[str, str, str, str, list[str]]:
    """``onenote:https://host/…/Section.one#Title&section-id={}&page-id={}&end``.

    Returns host, decoded path, section GUID, page GUID and problems. A
    parameter given twice with different values is a problem, not a choice.
    """
    problems: list[str] = []
    inner = client[len("onenote:") :]
    parts = urllib.parse.urlsplit(inner)
    seen: dict[str, set[str]] = {}
    for match in _FRAGMENT_PARAM.finditer(parts.fragment):
        seen.setdefault(match.group("key").lower(), set()).add(match.group("guid").upper())
    for key, values in seen.items():
        if len(values) > 1:
            problems.append(f"CLIENT_{key.upper().replace('-', '_')}_REPEATED")
    page_guid = sorted(seen.get("page-id", {""}))[0] if len(seen.get("page-id", ())) == 1 else ""
    section_guid = (
        sorted(seen.get("section-id", {""}))[0] if len(seen.get("section-id", ())) == 1 else ""
    )
    if not seen:
        problems.append("CLIENT_LINK_WITHOUT_IDS")
    path = urllib.parse.unquote(parts.path, errors="replace")
    return (parts.hostname or "").lower(), path, section_guid, page_guid, problems


def parse_locator(
    raw: str | None, *, approved_hosts: frozenset[str] = frozenset()
) -> ParsedLocator:
    """Classify one register link. Never raises; problems are reported, not fixed."""
    text = raw or ""
    stripped = text.strip()
    if not stripped:
        return ParsedLocator(raw=text, family=LinkFamily.EMPTY)

    if stripped.lower().startswith("onenote:"):
        host, cpath, sguid, pguid, client_only_problems = _parse_client(stripped)
        return ParsedLocator(
            raw=text,
            family=LinkFamily.CLIENT_ONLY,
            host=host,
            target_kind=TargetKind.PAGE
            if pguid
            else TargetKind.SECTION
            if sguid
            else TargetKind.NONE,
            client_path=cpath,
            client_section_guid=sguid,
            client_page_guid=pguid,
            problems=tuple(client_only_problems),
        )

    pieces = _CLIENT_SPLIT.split(stripped, maxsplit=1)
    web = pieces[0]
    client = pieces[1] if len(pieces) > 1 else ""
    try:
        parts = urllib.parse.urlsplit(web)
    except ValueError:
        return ParsedLocator(raw=text, family=LinkFamily.UNPARSEABLE, problems=("URL_UNPARSEABLE",))
    host = (parts.hostname or "").lower()
    if parts.scheme.lower() != "https" or not host:
        return ParsedLocator(
            raw=text, family=LinkFamily.UNPARSEABLE, host=host, problems=("NOT_HTTPS",)
        )
    if approved_hosts and host not in approved_hosts:
        return ParsedLocator(raw=text, family=LinkFamily.OTHER_HOST, host=host)

    problems: list[str] = []
    history = _HISTORY.search(parts.path)
    path = _HISTORY.sub("", parts.path) if history else parts.path
    query = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
    lowered = {key.lower(): values for key, values in query.items()}
    page_kind = path.lower().rsplit("/", 1)[-1]

    wd = lowered.get("wd", [""])[0]
    kind, section_path, sguid, pguid, title, passes, wd_problems = (
        _parse_target(wd) if wd else (TargetKind.NONE, "", "", "", "", 0, [])
    )
    problems.extend(wd_problems)

    if page_kind == "doc.aspx":
        family = LinkFamily.WEB_DOC_TARGET_WITH_CLIENT if client else LinkFamily.WEB_DOC_TARGET
        if kind is TargetKind.NONE:
            family = LinkFamily.WEB_NOTEBOOK_ONLY
    elif page_kind == "onenote.aspx":
        family = (
            LinkFamily.WEB_ONENOTE_ASPX_TARGET
            if kind is not TargetKind.NONE
            else LinkFamily.WEB_NOTEBOOK_ONLY
        )
    elif "/_layouts/" in path.lower():
        family = LinkFamily.UNPARSEABLE
        problems.append("UNKNOWN_LAYOUTS_PAGE")
    else:
        family = LinkFamily.SHAREPOINT_PATH

    client_host = cpath = csguid = cpguid = ""
    if client:
        client_host, cpath, csguid, cpguid, client_problems = _parse_client(client)
        problems.extend(client_problems)
        if client_host and client_host != host:
            problems.append("CLIENT_HOST_DIFFERS")
        if csguid and sguid and csguid != sguid:
            problems.append("CLIENT_SECTION_GUID_DIFFERS")
        if cpguid and pguid and cpguid != pguid:
            problems.append("CLIENT_PAGE_GUID_DIFFERS")

    if parts.fragment:
        problems.append("WEB_FRAGMENT_PRESENT")

    return ParsedLocator(
        raw=text,
        family=family,
        host=host,
        site_path=_site_path(path),
        history_version=history.group("n") if history else "",
        notebook_guid=canonical_guid(lowered.get("sourcedoc", [""])[0]),
        notebook_path=lowered.get("id", [""])[0] if page_kind == "onenote.aspx" else "",
        section_file_guid=canonical_guid(lowered.get("wdsectionfileid", [""])[0]),
        target_kind=kind
        if kind is not TargetKind.NONE
        else (TargetKind.PAGE if cpguid else TargetKind.NONE),
        target_section_path=section_path,
        section_guid=sguid,
        page_guid=pguid,
        title_hint=title,
        client_section_guid=csguid,
        client_path=cpath,
        client_page_guid=cpguid,
        wd_decode_passes=passes,
        problems=tuple(problems),
    )
