"""Files a refused save must not throw away.

A browser cannot put a file back into a file input. Nothing can: the value of
``<input type="file">`` is settable only to the empty string, for the obvious
reason that a page able to fill one could read any file on the machine. So every
answer that re-renders a form — a missing title, a valdkond ticked with nothing
written beside it, a next action without its date — comes back with the file
area empty, and the person who chose a file, corrected what the page complained
about and pressed the button again filed a Matter with no documents.

That was not a message they could have missed. There was no message. The upload
had reached the server, been read and been validated, and was then dropped on
the floor because the response was a page rather than a redirect.

So the validated bytes are **held** here for the length of the refusal, and the
re-rendered form carries the keys that name them. The next submit resumes them
and they go through ``add_evidence_version`` exactly as they would have the
first time.

Three properties this deliberately has:

* **The session is the authorization boundary.** A key is resumable only by the
  session that held it. Guessing a key gets nothing, because the lookup is a
  dictionary on the requesting session and not a query.
* **This is not the evidence store, and the distinction is the point.** Held
  bytes are a form's unsaved working state: they describe nothing, no row points
  at them, and losing them costs somebody one re-pick rather than a piece of
  evidence. That is why they get a storage class of their own — the same
  reasoning that keeps derivatives out of the evidence store (docs/adr/0014) —
  and why nothing here is backed up.
* **Held is not stored.** Nothing in this module writes a Document, a version or
  a checksum. It holds bytes that have passed ``read_upload`` and hands them
  back unchanged; every evidence rule still runs at save time.

Objects that no save ever claimed are swept on the next hold, by age. There is
no management command and deliberately so: unlike the evidence store, where
being unreferenced might mean a transaction is still in flight and deciding
otherwise needs an operator, an object here that is older than the grace
period is a form somebody walked away from and the only correct thing to do
with it is delete it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.utils import timezone

from app.core.ids import uuid7
from app.documents.uploads import AcceptedUpload

logger = logging.getLogger(__name__)

#: Where the session records what it is holding. One dictionary, key to
#: manifest, so a resume is a lookup on the requesting session rather than a
#: query anybody could aim at somebody else's key.
SESSION_KEY = "pending_uploads"

#: How many files one session may hold at once. A refused form carries what a
#: person picked, and nobody picks more than this by hand — the cap is here so
#: that a client submitting refusals in a loop cannot grow the session record or
#: the holding area without bound.
MAX_HELD_FILES = 25


def human_size(size: int) -> str:
    """A file size the way the browser's own preview writes it.

    Deliberately the same rounding and the same Estonian decimal comma as
    `humanSize` in ``static/js/app.js``. Three kinds of row can sit in the same
    list under the file control — one the browser is showing from the input, one
    a refused save is holding, one staged and being read — and any two of them
    disagreeing about «1.4 MB» and «1,4 MB» would look like two different kinds
    of thing (app/matters/views.py `_intake_context`).
    """
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{round(size / 1024)} KB"
    return f"{size / (1024 * 1024):.1f}".replace(".", ",") + " MB"


@dataclass(frozen=True)
class HeldUpload:
    """One validated file waiting for the save that was refused."""

    key: str
    filename: str
    mime_type: str
    size: int

    @property
    def human_size(self) -> str:
        return human_size(self.size)


def pending_storage() -> Any:
    return storages[settings.PENDING_UPLOAD_STORAGE_ALIAS]


def _manifest(session: Any) -> dict[str, dict[str, Any]]:
    held = session.get(SESSION_KEY)
    return dict(held) if isinstance(held, dict) else {}


def _write(session: Any, manifest: dict[str, dict[str, Any]]) -> None:
    session[SESSION_KEY] = manifest
    session.modified = True


def _expiry() -> Any:
    return timezone.now() - timedelta(hours=settings.PENDING_UPLOAD_GRACE_HOURS)


def hold(session: Any, uploads: list[AcceptedUpload]) -> list[HeldUpload]:
    """Keep these validated files for the next attempt at the same form.

    Called on the refusal path and nowhere else. Returns what is now held, in
    the order it was given, so the caller can render it back.
    """
    manifest = _manifest(session)
    _forget_expired(session, manifest)
    _sweep()

    held: list[HeldUpload] = []
    storage = pending_storage()
    for upload in uploads:
        if len(manifest) >= MAX_HELD_FILES:
            # Silently, and on purpose. The alternative is refusing a save
            # because of a limit on a convenience, which would make the
            # convenience worse than not having it.
            logger.warning("pending upload hold is full; %s not held", upload.filename)
            break
        # The name the backend actually used, not the one it was asked for: a
        # storage that sanitised or de-duplicated the key would otherwise leave
        # the manifest pointing at an object that is not there. A UUIDv7 never
        # collides, so in practice these are the same string — reading the
        # answer back is what makes that a fact rather than an assumption.
        key = storage.save(str(uuid7()), ContentFile(upload.content))
        manifest[key] = {
            "filename": upload.filename,
            "mime_type": upload.mime_type,
            "size": len(upload.content),
            "held_at": timezone.now().isoformat(),
        }
        held.append(
            HeldUpload(
                key=key,
                filename=upload.filename,
                mime_type=upload.mime_type,
                size=len(upload.content),
            )
        )

    _write(session, manifest)
    return held


def describe(session: Any, keys: list[str]) -> list[HeldUpload]:
    """What these keys are holding, for rendering. Reads no bytes."""
    manifest = _manifest(session)
    described: list[HeldUpload] = []
    for key in keys:
        entry = manifest.get(key)
        if entry is None:
            continue
        described.append(
            HeldUpload(
                key=key,
                filename=entry.get("filename", ""),
                mime_type=entry.get("mime_type", ""),
                size=int(entry.get("size") or 0),
            )
        )
    return described


def resume(session: Any, keys: list[str]) -> list[AcceptedUpload]:
    """The files these keys name, ready to be saved.

    A key this session is not holding is skipped rather than refused: a stale
    form, a second tab or a pruned object are all ordinary, and none of them is
    a reason to refuse a save the person can otherwise complete. What cannot
    happen is reading somebody else's key, because the manifest is theirs.

    Nothing is deleted here. A save can still be refused a second time, and the
    files have to survive that too; :func:`release` is what ends the hold.
    """
    manifest = _manifest(session)
    storage = pending_storage()
    resumed: list[AcceptedUpload] = []

    for key in keys:
        entry = manifest.get(key)
        if entry is None:
            continue
        try:
            with storage.open(key, "rb") as handle:
                content = handle.read()
        except (FileNotFoundError, OSError):
            logger.warning("held upload %s is no longer readable", key)
            continue
        resumed.append(
            AcceptedUpload(
                content=content,
                filename=entry.get("filename", ""),
                mime_type=entry.get("mime_type", ""),
            )
        )

    return resumed


def release(session: Any, keys: list[str]) -> None:
    """The hold is over: forget these and delete their bytes.

    Called once the save they were waiting for has succeeded. A deletion that
    fails is logged and not raised — the Matter is already written, and a stray
    object in a store that is pruned by age is not worth failing a save over.
    """
    manifest = _manifest(session)
    storage = pending_storage()
    changed = False

    for key in keys:
        if manifest.pop(key, None) is None:
            continue
        changed = True
        try:
            storage.delete(key)
        except OSError:  # pragma: no cover - defensive
            logger.warning("could not delete held upload %s", key, exc_info=True)

    if changed:
        _write(session, manifest)


def _sweep() -> None:
    """Delete held objects nobody came back for.

    Opportunistic rather than a management command, and that is a deliberate
    choice about what this data is. The evidence store gets
    ``prune_orphaned_evidence`` because being unreferenced there might mean a
    transaction is still in flight, so deciding an object is an orphan needs
    care, a grace period and an operator reading the output. Nothing here
    describes anything: an object older than the grace period is a form somebody
    walked away from, and the only correct thing to do with it is delete it.

    Running it on the hold path means it runs exactly when the feature is used,
    on a directory that is small by construction, and needs nothing scheduled —
    a store that has to be swept by an operator who was never told is a store
    that fills up.

    Every failure here is swallowed. Sweeping is housekeeping, and a save must
    never be refused because a directory could not be listed.
    """
    storage = pending_storage()
    cutoff = _expiry()
    try:
        _directories, names = storage.listdir("")
    except (FileNotFoundError, NotImplementedError, OSError):  # pragma: no cover - defensive
        return

    for name in names:
        try:
            created = storage.get_created_time(name)
        except (FileNotFoundError, NotImplementedError, OSError):  # pragma: no cover - defensive
            continue
        if timezone.is_naive(created):
            created = timezone.make_aware(created)
        if created >= cutoff:
            continue
        try:
            storage.delete(name)
        except OSError:  # pragma: no cover - defensive
            logger.warning("could not sweep held upload %s", name, exc_info=True)


def _forget_expired(session: Any, manifest: dict[str, dict[str, Any]]) -> None:
    """Drop entries older than the grace period from this session's record.

    Housekeeping, not the pruner: this keeps one session's dictionary from
    growing across a long day. The objects themselves are removed by
    ``prune_pending_uploads``, which is the only thing that may decide an object
    on disk is nobody's.
    """
    from django.utils.dateparse import parse_datetime

    cutoff = _expiry()
    stale = []
    for key, entry in manifest.items():
        held_at = parse_datetime(str(entry.get("held_at") or ""))
        if held_at is not None and held_at < cutoff:
            stale.append(key)
    for key in stale:
        manifest.pop(key, None)
