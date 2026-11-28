"""How document processing is doing, from the rows and marks that already exist (r98, ADR 0239).

Counts and ages only — never a file name, a path or a document's words. Mutually exclusive
states are kept apart and never summed into one «problems» figure:

* waiting (PENDING), being processed (PROCESSING; a claim older than the stale window is shown
  as interrupted, not as running), read (DONE), failed (FAILED, split by the recorded reason),
  not processed on purpose (NOT_APPLICABLE; a signed ASiC-E container is *intentional*, not a
  failure), and read once while a Teema was created (INTAKE_READ, no persistent extraction).

What the schema does not record is said, not guessed: there is no per-file processing time
(so no duration is shown), and a failure's *category* is read from the note the isolation layer
wrote («kestis üle», «mälu», «ootamatult»), not from a stored code. The reader's mark says its
loop is turning; the parser services' reachability is checked from this process only when it can
reach their sockets, and says «ei kontrollitud» otherwise. Nothing is reprocessed.
"""

from __future__ import annotations

import datetime as dt
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings
from django.db.models import Count, Min
from django.utils import timezone

from app.documents.enums import ExtractionState
from app.documents.extraction import heartbeat
from app.documents.models import DocumentVersion
from app.documents.uploads import SIGNED_CONTAINER_MIME_TYPE

#: The reasons the isolation layer writes beside a FAILED file, by a fragment of the sentence.
FAILURE_KINDS: tuple[tuple[str, str], ...] = (
    ("kestis üle", "timeout"),
    ("protsessoriaega", "cpu_limit"),
    ("mälu", "memory_limit"),
    ("ajutine fail ületas", "file_limit"),
    ("tulemus ületas", "output_limit"),
    ("ootamatult", "crashed"),
    ("vastus oli vigane", "protocol"),
)
KIND_LABELS = {
    "timeout": "Ajapiir",
    "cpu_limit": "Protsessoriaja piir",
    "memory_limit": "Mälupiir",
    "file_limit": "Ajutise faili piir",
    "output_limit": "Tulemuse mahupiir",
    "crashed": "Lugeja lõpetas ootamatult",
    "protocol": "Vigane vastus",
    "other": "Muu põhjus",
}


def failure_kind(note: str) -> str:
    for fragment, kind in FAILURE_KINDS:
        if fragment in note:
            return kind
    return "other"


@dataclass
class ServiceMark:
    label: str
    state: str  # "ok" | "stale" | "unknown" | "not_service"
    line: str


@dataclass
class Health:
    counts: dict[str, int]
    failures: dict[str, int]
    interrupted: int
    oldest_waiting_age: dt.timedelta | None
    intentionally_unprocessed: int
    unsupported_other: int
    marks: list[ServiceMark] = field(default_factory=list)
    limits: list[str] = field(default_factory=list)


def _parser_socket(name: str) -> ServiceMark:
    configured = str(getattr(settings, name, "") or "")
    label = "Parser" if name == "DOCUMENT_PARSER_SOCKET" else name
    if not configured:
        return ServiceMark(label, "unknown", "Selles protsessis seadistamata; ei kontrollitud")
    if sys.platform == "win32":  # the parser sockets exist on the Linux host only
        return ServiceMark(label, "unknown", "Ei kontrollitud (pole Linuxi hostis)")
    path = Path(configured)
    if not path.exists():
        return ServiceMark(
            label, "unknown", "Pesa pole sellest protsessist nähtav; ei kontrollitud"
        )
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(1.0)
    try:
        probe.connect(configured)
    except OSError:
        return ServiceMark(label, "stale", "Pesa ei vasta")
    finally:
        probe.close()
    return ServiceMark(label, "ok", "Pesa vastab")


def collect(now: dt.datetime | None = None) -> Health:
    now = now or timezone.now()
    states = {
        row["extraction_state"]: row["n"]
        for row in DocumentVersion.objects.values("extraction_state").annotate(n=Count("pk"))
    }
    counts = {state.value: states.get(state.value, 0) for state in ExtractionState}
    stale_after = dt.timedelta(minutes=int(getattr(settings, "EXTRACTION_STALE_CLAIM_MINUTES", 30)))
    processing = DocumentVersion.objects.filter(extraction_state=ExtractionState.PROCESSING)
    interrupted = processing.filter(extraction_claimed_at__lt=now - stale_after).count()
    failures: dict[str, int] = {}
    for note in DocumentVersion.objects.filter(extraction_state=ExtractionState.FAILED).values_list(
        "extraction_note", flat=True
    ):
        kind = failure_kind(note or "")
        failures[kind] = failures.get(kind, 0) + 1
    oldest = DocumentVersion.objects.filter(extraction_state=ExtractionState.PENDING).aggregate(
        first=Min("created_at")
    )["first"]
    not_applicable = DocumentVersion.objects.filter(extraction_state=ExtractionState.NOT_APPLICABLE)
    signed = not_applicable.filter(mime_type=SIGNED_CONTAINER_MIME_TYPE).count()
    reader = heartbeat.INTAKE_READER
    age = reader.age_seconds()
    if age is None:
        reader_mark = ServiceMark(
            "Teema loomise lugeja", "unknown", "Märget pole (pole selles konteineris)"
        )
    elif reader.is_alive():
        reader_mark = ServiceMark(
            "Teema loomise lugeja", "ok", f"Tsükkel käib (märge {int(age)} s tagasi)"
        )
    else:
        reader_mark = ServiceMark("Teema loomise lugeja", "stale", f"Märge on {int(age)} s vana")
    return Health(
        counts=counts,
        failures=failures,
        interrupted=interrupted,
        oldest_waiting_age=(now - oldest) if oldest else None,
        intentionally_unprocessed=signed,
        unsupported_other=not_applicable.count() - signed,
        marks=[reader_mark, _parser_socket("DOCUMENT_PARSER_SOCKET")],
        limits=[
            "Töötlemise kestust ei salvestata, seega kestuse arvu ei näidata "
            "(ADR 0230: skaneeritud faili 240 s piir ootab tegelikku mõõtmist).",
            "Rikke põhjus loetakse isolatsioonikihi märkusest, mitte salvestatud koodist.",
            "Ei käivita ega kordatöötle ühtegi dokumenti.",
        ],
    )
