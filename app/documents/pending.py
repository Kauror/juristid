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

Objects that no save ever claimed are swept by age: on the next hold, and by
the `intake-sweeper` service on every turn (`sweep_held`, docs/adr/0177).
Unlike the evidence store, where being unreferenced might mean a transaction is
still in flight and deciding otherwise needs an operator, an object here that is
older than the grace period is a form somebody walked away from and the only
correct thing to do with it is delete it.

**Bounded before a byte is written** (csf_3942255b466a4e213b958b16, docs/adr/0177).
This store shares its volume with `Uus teema` staging, and a refused post from a
fresh session holds up to `MAX_HELD_FILES` files each time, so the per-session
cap bounds nothing. `hold` therefore takes the same advisory lock staging takes
and refuses (holding nothing, with a sentence) when the held bytes would pass
``PENDING_UPLOAD_MAX_HELD_BYTES`` or the volume would fall below
``INTAKE_STAGING_MIN_FREE_BYTES``.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.db import connection, transaction
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

#: The advisory lock every writer to the pending-upload volume takes before it
#: checks a quota: held uploads here, `Uus teema` staging in
#: `app.matters.intake_staging`. One key, in the two-integer form
#: `app.search.indexing` (24601) and its neighbours use, so a hold and a staged
#: upload cannot both pass the free-space check (docs/adr/0177).
STORAGE_QUOTA_LOCK = (24604, 1)

#: What the person is told when a refused form's files cannot be kept.
HOLD_REFUSED = (
    "Valitud faile ei saanud vormi juurde hoida, sest failide ajutine hoidla on "
    "täis. Vali failid salvestamisel uuesti."
)

#: Most held objects one sweep deletes.
SWEEP_LIMIT = 1000


def lock_storage_quota() -> None:
    """Take `STORAGE_QUOTA_LOCK` until the surrounding transaction ends."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", list(STORAGE_QUOTA_LOCK))


def free_bytes(storage: Any) -> int | None:
    """Free space under a filesystem store, or ``None`` for any other backend."""
    location = getattr(storage, "location", None)
    if not location:
        return None
    path = Path(location)
    # The root is created on first write; until then ask the volume it will be on.
    while not path.exists() and path.parent != path:
        path = path.parent
    return int(shutil.disk_usage(path).free)


def _held_objects(storage: Any) -> list[os.DirEntry[str]]:
    """The regular files at the root of the store: held uploads, nothing else.

    `Uus teema` staging is one directory down (`intake/`) and is not listed; a
    symbolic link is not a held upload and is neither counted nor followed.
    """
    location = getattr(storage, "location", None)
    if not location or not os.path.isdir(location):
        return []
    with os.scandir(location) as listing:
        return [entry for entry in listing if entry.is_file(follow_symlinks=False)]


def held_bytes(storage: Any = None) -> int:
    """Bytes held for refused forms right now, read from the store itself."""
    total = 0
    for entry in _held_objects(storage or pending_storage()):
        try:
            total += entry.stat(follow_symlinks=False).st_size
        except FileNotFoundError:
            continue
    return total


def _hold_refusal(storage: Any, incoming: int) -> str:
    held = held_bytes(storage)
    if held + incoming > settings.PENDING_UPLOAD_MAX_HELD_BYTES:
        reason = f"held bytes {held} + {incoming}"
    else:
        free = free_bytes(storage)
        if free is None or free - incoming >= settings.INTAKE_STAGING_MIN_FREE_BYTES:
            return ""
        reason = f"free bytes {free} - {incoming}"
    logger.warning("pending upload hold refused at the deployment limit: %s", reason)
    return HOLD_REFUSED


def human_size(size: int) -> str:
    """A file size the way the browser's own preview writes it.

    Deliberately the same rounding and the same Estonian decimal comma as
    `uploadSize` in ``static/js/ux.js``. Three kinds of row can sit in the same
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


def hold(
    session: Any, uploads: list[AcceptedUpload], *, refusals: list[str] | None = None
) -> list[HeldUpload]:
    """Keep these validated files for the next attempt at the same form.

    Called on the refusal path and nowhere else. Returns what is now held, in
    the order it was given, so the caller can render it back.

    All or nothing against the volume's limits: under `STORAGE_QUOTA_LOCK` the
    files this call would hold are checked against
    ``PENDING_UPLOAD_MAX_HELD_BYTES`` and the free-space headroom before any is
    written, and a write that fails part-way removes what this call wrote. A
    refusal holds nothing and appends `HOLD_REFUSED` to ``refusals`` for the
    caller to show; the save was already refused, so this never raises.
    """
    manifest = _manifest(session)
    _forget_expired(session, manifest)
    _sweep()

    room = max(0, MAX_HELD_FILES - len(manifest))
    if len(uploads) > room:
        # Silently, and on purpose. The alternative is refusing a save
        # because of a limit on a convenience, which would make the
        # convenience worse than not having it.
        #
        # Type and size, never the name. A filename is business text — it
        # is often the subject of the letter — and nothing of that kind
        # goes to the container log (ENG-071). The file has no row yet, so
        # there is no identifier to give instead.
        dropped = uploads[room]
        logger.warning(
            "pending upload hold is full (%d held); a %s file of %d bytes was not held",
            len(manifest),
            dropped.mime_type,
            len(dropped.content),
        )
        uploads = uploads[:room]

    storage = pending_storage()
    held: list[HeldUpload] = []
    written: list[str] = []
    if uploads:
        with transaction.atomic():
            lock_storage_quota()
            refusal = _hold_refusal(storage, sum(len(upload.content) for upload in uploads))
            if not refusal:
                try:
                    _hold_each(storage, uploads, manifest, held, written)
                except OSError:
                    logger.warning("holding uploads failed; nothing was held", exc_info=True)
                    for key in written:
                        manifest.pop(key, None)
                        _delete_quietly(storage, key)
                    held = []
                    refusal = HOLD_REFUSED
        if refusal and refusals is not None:
            refusals.append(refusal)

    _write(session, manifest)
    return held


def _delete_quietly(storage: Any, key: str) -> None:
    try:
        if storage.exists(key):
            storage.delete(key)
    except OSError:  # pragma: no cover - best effort
        logger.warning("could not delete held upload %s", key, exc_info=True)


def _hold_each(
    storage: Any,
    uploads: list[AcceptedUpload],
    manifest: dict[str, dict[str, Any]],
    held: list[HeldUpload],
    written: list[str],
) -> None:
    """Write each file; ``written`` names each object before its bytes land."""
    for upload in uploads:
        name = str(uuid7())
        written.append(name)
        # The name the backend actually used, not the one it was asked for: a
        # storage that sanitised or de-duplicated the key would otherwise leave
        # the manifest pointing at an object that is not there. A UUIDv7 never
        # collides, so in practice these are the same string — reading the
        # answer back is what makes that a fact rather than an assumption.
        key = storage.save(name, ContentFile(upload.content))
        if key != name:  # pragma: no cover - a uuid7 name is never taken
            written.append(key)
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
    sweep_held()


def sweep_held(*, limit: int = SWEEP_LIMIT) -> int:
    """Delete held objects nobody came back for. Returns how many went.

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

    Also run by the `intake-sweeper` service on every turn (docs/adr/0177), so
    held bytes expire whether or not anybody holds again, and bounded by
    ``limit`` per run. Only regular files at the root are considered: staging
    under `intake/` is not this function's, and a symbolic link is neither
    followed nor deleted.
    """
    storage = pending_storage()
    cutoff = _expiry()
    names: list[str]
    if getattr(storage, "location", None):
        names = [entry.name for entry in _held_objects(storage)]
    else:  # pragma: no cover - every configured store is a filesystem
        try:
            _directories, names = storage.listdir("")
        except (FileNotFoundError, NotImplementedError, OSError):
            return 0

    removed = 0
    for name in sorted(names):
        if removed >= limit:
            break
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
            continue
        removed += 1
    return removed


def _forget_expired(session: Any, manifest: dict[str, dict[str, Any]]) -> None:
    """Drop entries older than the grace period from this session's record.

    Housekeeping, not the pruner: this keeps one session's dictionary from
    growing across a long day. The objects themselves are removed by
    :func:`_sweep`, by age, on the next hold — there is no separate pruning
    command for held uploads (an earlier version of this sentence named one that
    never existed; ENG-087).
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
