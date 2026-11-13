"""Lovable's PostgREST as the synchronisation reads it, in process (QA-02, audit §D.2).

`FakeLovable` moved here from `tests/test_lovable_sync.py`, which re-exports it
so the suites that import it from there keep working unchanged. It serves the
allowlisted tables above the transport (`lovable_source.Transport.select`); the
HTTP layer — paging, ``Content-Range``, the server's row cap — is the PostgREST
fake's (`tests/lovable_postgrest_fake.py`, QA-01), not this one's.

`ContractLovable` adds what the contract suite needs and nothing more: the
write-layer rules of Lovable's own `save_topic` / `delete_topic` /
`restore_topic` (Lovable `drizzle/migrations/0015_canonical_topic_write_layer.sql`
at `vocabulary.LOVABLE_COMMIT`) so a scenario cannot stage a row Lovable itself
could never hold, and a record of every call the synchronisation makes, so a
test can assert that the source was only ever read (LC-R1).

Every row is invented; nothing here is data.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from tests.lovable_contract import vocabulary


class FakeLovable:
    """Lovable's PostgREST as the sync reads it: the allowlisted tables only."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.commencements: list[dict[str, Any]] = []
        #: `topic_stage_link` rows: ``{"prev_topic_id", "next_topic_id"}``.
        self.stage_links: list[dict[str, str]] = []
        self.fail: Exception | None = None
        self.on_select: Any = None

    def put(self, topic_id: str, row: dict[str, Any], *, version: int) -> None:
        self.rows[topic_id] = {**row, "id": topic_id, "version": version}

    def edit(self, reference: str, **values: Any) -> None:
        for row in self.rows.values():
            if row["nr"] == reference:
                row.update(values)
                row["version"] += 1
                return
        raise KeyError(reference)

    def select(
        self, table: str, columns: Any, *, filters: dict[str, str], order: str
    ) -> list[dict[str, Any]]:
        if self.fail is not None:
            raise self.fail
        if self.on_select is not None:
            self.on_select(table)
        wanted = None
        for value in filters.values():
            wanted = set(value[4:-1].split(","))
        if table == "topic":
            rows = [r for r in self.rows.values() if wanted is None or r["id"] in wanted]
            return [{c: r.get(c) for c in columns} for r in sorted(rows, key=lambda r: r["id"])]
        if table == "topic_commencement":
            return [dict(r) for r in self.commencements if r["topic_id"] in (wanted or set())]
        if table == "topic_stage_link" and filters:
            column = next(iter(filters))
            return [dict(r) for r in self.stage_links if r.get(column) in (wanted or set())]
        return []


class LovableRefused(Exception):
    """What Lovable's write layer raises: the scenario asked for a row it cannot hold."""


#: Columns `topic_guard` makes immutable (Lovable `0015_canonical_topic_write_layer.sql` §3).
IMMUTABLE = ("id", "nr", "aasta")


class ContractLovable(FakeLovable):
    """`FakeLovable` that keeps Lovable's own write rules and records every call.

    Scenario code changes the source only through `edit`, `set_commencements`,
    `link`, `soft_delete` and `restore`, the way a lawyer's save in Lovable
    would; the synchronisation reaches it only through `select`."""

    def __init__(self) -> None:
        super().__init__()
        #: ``(method, table)`` for every call the synchronisation made.
        self.calls: list[tuple[str, str]] = []
        #: Soft-deleted topics: Lovable keeps them, its read view hides them.
        self.deleted: dict[str, dict[str, Any]] = {}

    # -- the synchronisation's side ------------------------------------------------

    def select(
        self, table: str, columns: Any, *, filters: dict[str, str], order: str
    ) -> list[dict[str, Any]]:
        self.calls.append(("GET", table))
        return super().select(table, columns, filters=filters, order=order)

    def __getattr__(self, name: str) -> Any:
        # Anything but `select` asked of the transport is a write path or an
        # unknown one: recorded, then refused (LC-R1).
        if name.startswith("__"):
            raise AttributeError(name)
        self.__dict__.setdefault("calls", []).append((name.upper(), "?"))
        raise AttributeError(f"Lovable is read-only for the synchronisation: {name}")

    @property
    def writes(self) -> list[tuple[str, str]]:
        return [call for call in self.calls if call[0] != "GET"]

    # -- the lawyer's side, as Lovable's write layer allows it ---------------------

    def _row(self, reference: str) -> dict[str, Any]:
        for row in self.rows.values():
            if row["nr"] == reference:
                return row
        raise KeyError(reference)

    def edit(self, reference: str, **values: Any) -> None:
        for column in IMMUTABLE:
            if column in values:
                raise LovableRefused(f"{column} is immutable in Lovable (topic_guard)")
        # `hetkeseis` and `oigusakt` are not whitelisted by `save_topic` (LV-07),
        # only by the form, so a scenario may write a value outside the
        # vocabulary: that is what LC-U1 exercises.
        super().edit(reference, **values)

    def touch(self, reference: str) -> None:
        """A save that changes only a child table still bumps the version (Lovable 0015_…sql §6)."""
        super().edit(reference)

    def set_commencements(self, reference: str, items: list[dict[str, Any]]) -> None:
        """Replace the topic's whole commencement list, as `save_topic` does."""
        row = self._row(reference)
        for item in items:
            if item["liik"] not in vocabulary.C_LIIGID:
                raise LovableRefused("VIGA: Tundmatu jõustumise liik.")
            if item["liik"] in vocabulary.C_LIIGID_WITH_DATE and not item.get("kuupaev"):
                raise LovableRefused("VIGA: Jõustumise kuupäev puudub.")
        self.commencements = [c for c in self.commencements if c["topic_id"] != row["id"]]
        for position, item in enumerate(items):
            self.commencements.append(
                {
                    "id": f"{row['id'][-12:]}-c{position}",
                    "topic_id": row["id"],
                    "position": position,
                    "liik": item["liik"],
                    "kuupaev": item.get("kuupaev")
                    if item["liik"] in vocabulary.C_LIIGID_WITH_DATE
                    else None,
                    "tapsustus": (item.get("tapsustus") or "").strip() or None,
                }
            )
        self.touch(reference)

    def link(self, prev_reference: str, next_reference: str) -> None:
        """A `topic_stage_link`: ``prev`` continues as ``next``."""
        prev, nxt = self._row(prev_reference), self._row(next_reference)
        self.stage_links.append({"prev_topic_id": prev["id"], "next_topic_id": nxt["id"]})
        self.touch(prev_reference)

    def soft_delete(self, reference: str) -> None:
        """`delete_topic`: ``deleted_at`` set, the version bumped, the row hidden
        from the reader's view. Never a physical delete."""
        row = self._row(reference)
        row["version"] += 1
        row["deleted_at"] = dt.datetime(2026, 11, 11, 12, 0, tzinfo=dt.UTC).isoformat()
        self.deleted[row["id"]] = self.rows.pop(row["id"])

    def restore(self, reference: str) -> None:
        """`restore_topic`: the same id comes back, one version higher."""
        for topic_id, row in list(self.deleted.items()):
            if row["nr"] == reference:
                row["version"] += 1
                row.pop("deleted_at", None)
                self.rows[topic_id] = self.deleted.pop(topic_id)
                return
        raise KeyError(reference)
