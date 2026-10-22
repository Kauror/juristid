"""The bytes of an accepted upload, kept where they already are.

An accepted file used to become a ``bytes`` object the moment it was validated
(`read_upload` called ``uploaded_file.read()``), and every door then held the
whole batch in the web worker's memory until the last file was written: twenty
files of a hundred megabytes were two gigabytes in one gunicorn worker before
any aggregate limit was even asked (csf_35546c06657fae7b9935969d,
docs/adr/0182).

A body is the opposite: a size and a way to read the bytes again, in bounded
chunks, from wherever they already are —

* :class:`UploadedFileBody`, the file Django's upload handlers wrote while the
  request arrived (a temporary file on disk, or a small in-memory one inside
  ``FILE_UPLOAD_MAX_MEMORY_SIZE``), bounded per request by
  `app.documents.upload_limits`;
* :class:`StoredBody`, a file already on the pending-upload volume (a held
  upload resumed, a staged file promoted);
* :class:`BytesBody`, bytes a caller already has (an importer, a test).

:func:`store_body` is the one way a body reaches a store: streamed in chunks,
hashed while it streams, counted, and removed again if the write did not
complete or did not write the bytes the body claimed. The digest it returns
describes the bytes that were written, not bytes read in an earlier pass.

Nothing here validates anything. Validation is `app.documents.uploads`'s, and a
body is only ever made from a file that passed it.
"""

from __future__ import annotations

import hashlib
import io
import logging
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import IO, Any

from django.core.files.base import File

from app.core.errors import DomainError

logger = logging.getLogger(__name__)

#: How much of a body is in memory at once while it is hashed or copied.
CHUNK_SIZE = 256 * 1024

#: Refused when a body did not read back as the bytes it claimed to be.
BODY_CHANGED = "Faili sisu muutus salvestamise ajal. Vali fail uuesti."


class BodyChanged(DomainError):
    """The bytes read from a body are not the ones it described."""


class UploadBody:
    """A size and the bytes behind it, readable again in bounded chunks."""

    size: int = 0

    def open(self) -> AbstractContextManager[IO[bytes]]:  # pragma: no cover - overridden
        raise NotImplementedError

    def chunks(self) -> Iterator[bytes]:
        with self.open() as handle:
            while True:
                chunk = handle.read(CHUNK_SIZE)
                if not chunk:
                    return
                yield chunk

    def head(self, length: int) -> bytes:
        """The first ``length`` bytes (fewer if the body is shorter)."""
        with self.open() as handle:
            return handle.read(length)

    def sha256(self) -> str:
        """The body's SHA-256, computed by streaming it once and then remembered."""
        cached = getattr(self, "_sha256", None)
        if cached is None:
            digest = hashlib.sha256()
            for chunk in self.chunks():
                digest.update(chunk)
            cached = digest.hexdigest()
            self._sha256 = cached
        return cached

    def read_all(self) -> bytes:
        """Every byte, in memory. For callers that genuinely need ``bytes``.

        Not on any upload door: a door streams (:func:`store_body`). Kept for the
        compatibility property `AcceptedUpload.content` and for small,
        already-bounded uses.
        """
        return b"".join(self.chunks())


class BytesBody(UploadBody):
    """Bytes the caller already holds."""

    def __init__(self, content: bytes) -> None:
        self._content = bytes(content)
        self.size = len(self._content)

    @contextmanager
    def open(self) -> Iterator[IO[bytes]]:
        yield io.BytesIO(self._content)

    def read_all(self) -> bytes:
        return self._content


class UploadedFileBody(UploadBody):
    """A file Django's upload handlers already wrote while the request arrived.

    Read in place and never moved: the temporary file is Django's, and Django
    deletes it when the request ends (`HttpRequest.close`).
    """

    def __init__(self, uploaded_file: Any) -> None:
        self._file = uploaded_file
        self.size = int(getattr(uploaded_file, "size", 0) or 0)

    @contextmanager
    def open(self) -> Iterator[IO[bytes]]:
        handle = self._file
        handle.seek(0)
        try:
            yield handle
        finally:
            # Left open on purpose: the request owns the file and closes it.
            handle.seek(0)


class StoredBody(UploadBody):
    """A file already in a storage backend: a held upload, a staged file."""

    def __init__(self, storage: Any, key: str, size: int, *, label: str = "") -> None:
        self._storage = storage
        self.key = key
        self.size = int(size)
        self.label = label

    @contextmanager
    def open(self) -> Iterator[IO[bytes]]:
        try:
            handle = self._storage.open(self.key, "rb")
        except OSError as error:
            # A held or staged object swept or lost between the moment it was
            # described and the moment it is written: a sentence on the form,
            # never a 500 (the same words staging promotion always used).
            named = f"Faili „{self.label}”" if self.label else "Faili"
            raise DomainError(f"{named} ei õnnestunud lugeda. Vali see uuesti.") from error
        with handle:
            yield handle


class _DigestingFile(File):
    """What a storage backend is handed: the body's chunks, hashed as they pass.

    Deliberately without ``temporary_file_path``: `FileSystemStorage` would
    otherwise *move* Django's temporary file into place, and the request may
    still need it (a held copy and a saved copy of the same file).
    """

    def __init__(self, body: UploadBody, name: str) -> None:
        super().__init__(None, name)
        self._body = body
        self.size = body.size
        self.digest = hashlib.sha256()
        self.consumed = 0

    def chunks(self, chunk_size: int | None = None) -> Iterator[bytes]:
        for chunk in self._body.chunks():
            self.digest.update(chunk)
            self.consumed += len(chunk)
            yield chunk

    def multiple_chunks(self, chunk_size: int | None = None) -> bool:
        return True

    def open(self, *args: Any, **kwargs: Any) -> _DigestingFile:
        return self

    def close(self) -> None:
        return None

    def read(self, *args: Any, **kwargs: Any) -> bytes:  # pragma: no cover - guards a backend
        raise io.UnsupportedOperation("a body is written through chunks()")


def store_body(storage: Any, name: str, body: UploadBody) -> tuple[str, str]:
    """Write ``body`` to ``storage`` under ``name``; return ``(key, sha256)``.

    Streamed in :data:`CHUNK_SIZE` pieces and hashed on the way through, so the
    digest is of exactly the bytes the backend was given. A write that fails
    part-way, or that did not write ``body.size`` bytes, leaves nothing behind:
    the object (partial or whole) is deleted before the error propagates.
    """
    digesting = _DigestingFile(body, name)
    key = name
    try:
        key = storage.save(name, digesting)
        if digesting.consumed != body.size:
            raise BodyChanged(BODY_CHANGED)
    except BaseException:
        _delete_quietly(storage, key)
        if key != name:  # pragma: no cover - a fresh name is never taken
            _delete_quietly(storage, name)
        raise
    return key, digesting.digest.hexdigest()


def _delete_quietly(storage: Any, key: str) -> None:
    try:
        if storage.exists(key):
            storage.delete(key)
    except Exception:  # pragma: no cover - best effort; the sweeps find leftovers
        logger.warning("could not remove a partially written object %s", key, exc_info=True)
