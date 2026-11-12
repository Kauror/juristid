"""The nightly Lovable → Juristid synchronisation (docs/adr/0192, 0202, 0213).

**Since 11.11.2026 Lovable is authoritative** for the operational cohorts
(docs/adr/0213 §10, `_plan_authoritative`): every source-owned field follows the
latest verified Lovable value through its domain service, also over a local
divergence, and only technical problems hold (`TECHNICAL_HOLDS`). What follows
describes the rules the closed historical register (`lovable-history`) and
tombstones still keep (`_plan_archive`); the machinery — snapshots, A/B/C,
locks, read-back, idempotency, breakers — is shared.

One way, for the Teemad of the approved cohorts and for nothing else: the 100
Teemad the Lovable pilot created (docs/adr/0191) — the built-in cohort — and
every cohort enrolled since (`LovableSyncCohort`, `enroll`), each found only
through its approved provenance. Every run reads a consistent snapshot of those rows
(`app.legacy_import.lovable_source`), compares three values per field and writes
only what is safe:

* **A** — what Lovable said when Juristid last accepted the field
  (`LovableSyncState.applied`, seeded from the pilot's immutable provenance);
* **B** — what Juristid holds now;
* **C** — what Lovable says tonight.

=============================  ===================================================
A = C, B = A                   UNCHANGED
A = C, B ≠ A                   LOCAL_TEST_CHANGE — the lawyer's edit is kept
A ≠ C, B = A, mapping exact    ELIGIBLE → APPLIED through the canonical service
A ≠ C, B = C                   ALREADY_CONVERGED — baseline advances, no write
A ≠ C, B ≠ A, B ≠ C            CONFLICT — nothing is overwritten
no baseline / no mapping       REVIEW_REQUIRED
=============================  ===================================================

**The allowlist** (`AUTO_FIELDS`) is the title, the owner, the day of an
unchanged current step, and the OneNote link (appended as a new source
observation, never by editing the old one). Everything else is held and
reported: new next-step wording (its DO/WAIT/MONITOR reading is a person's call),
`hetkeseis` (no approved stage mapping exists; 0191 keeps it NEVER_AUTO), the
opinion deadline, `valja`/`valja_kuupaev`, the addressee, commencements, the
received date, the act, and lifecycle. A held change stays pending — the
baseline does not move — until the source returns to it, Juristid converges on
it, or an operator resolves it (`resolve_field`).

**Conditional fields and new topics** (docs/adr/0213, `FIELD_POLICY`): a stage
that does not end the work, through the reader's exact `STAGE_MAPPING`; a new
opinion request where none is open and nothing is sent; an added commencement of
an unambiguous kind, never on a past day. Lovable's stage link (the
continuation) is compared and always held. An operational topic with no Teema
is discovered every run, checked (identity — tombstones included —, reference,
reading, plan) and, on an APPLY night only, created once through the machine
reader and the importer's own CREATE writer, then enrolled in the register with
a ``NEW_TOPIC`` membership (`create_new_topic`).

**The limits, and why each stays** (brief 2026-11-09 §6 B6): ``MAX_CHANGED_MATTERS``
(20 Teemad written a night) and ``MAX_NEW_HOLDS`` (25 more held fields than the
previous run; it replaced ``MAX_NEW_CONFLICTS``, LS-07) were sized for daily
work, not a backfill — a backfill goes through `lovable_pilot` packages, never
through a night — and a night over them is a changed source or a broken mapping;
``MAX_MISSING`` (5) separates a deleted topic from a broken read;
``MAX_NEW_TOPICS`` (10 created a night) is new and separate. The field breakers
hold every write of the night (REVIEW_ONLY) and the new-topic breaker every
creation of it; ``MAX_MISSING`` fails the run.

A field the import itself held for review (``HELD_FOR_REVIEW`` in the package's
``field_decisions``) is seeded with ``import_held`` and reported
``REVIEW_REQUIRED`` / ``IMPORT_HELD`` every night until an operator resolves it;
a convergence never clears it (docs/adr/0202 §6).

Writes go through the domain services with ``actor=None`` and a ``LOVABLE_SYNC``
provenance; nothing here sets a model field directly.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import Count, F, Min, Q
from django.utils import timezone

from app.legacy_import.lovable_pilot import (
    HELD_FIELD_COLUMNS,
    HISTORY_COHORT,
    OPERATIONAL_COHORT,
    PILOT,
    REGISTER_COHORT,
    SOURCE_SYSTEM,
    SOURCE_TABLE,
)
from app.legacy_import.lovable_source import (
    SourceError,
    Transport,
    take_snapshot,
    verify_snapshot,
)
from app.legacy_import.lovable_sync_labels import LOVABLE_COHORT_LABELS
from app.legacy_import.lovable_sync_models import (
    LovableSyncBaselineArchive,
    LovableSyncCohort,
    LovableSyncControl,
    LovableSyncDecision,
    LovableSyncMembership,
    LovableSyncNewTopic,
    LovableSyncRetirement,
    LovableSyncRun,
    LovableSyncSnapshot,
    LovableSyncState,
    MembershipBasis,
    NewTopicStatus,
    SyncMode,
    SyncOutcome,
    SyncRunStatus,
    SyncTrigger,
)

logger = logging.getLogger(__name__)

MAPPING_VERSION = "lovable-sync/1"
#: The engine's own version (docs/adr/0213): the conditional fields and the new
#: topics. ``MAPPING_VERSION`` stays: it names the reconciliation's canonical
#: values, which did not change, and stored snapshots and references carry it.
ENGINE_VERSION = "lovable-sync/2"
PROVENANCE_SOURCE = "LOVABLE_SYNC"
TALLINN = ZoneInfo("Europe/Tallinn")
LOCK_NAMESPACE, LOCK_KEY = 24607, 1
#: The pilot's size (docs/adr/0191): the built-in cohort. Every reader of the
#: whole population's size uses `expected_cohorts()` instead.
EXPECTED_COHORT = 100

#: The cohort keys (docs/adr/0202 §1): the `EvidencePilotRecord.pilot` each
#: importer writes. The pilot is built in; any other is synchronised only once
#: enrolled (`enroll`).
PILOT_COHORT = PILOT
#: The historical cohort (brief 2026-11-09 §5 A4): the closed Teemad the backfill
#: creates from Lovable's non-operational topics. Every change to one of them is
#: a person's call (MATTER_CLOSED); a topic gone from the source is never closed
#: or deleted here.
ENROLLABLE_COHORTS = (OPERATIONAL_COHORT, REGISTER_COHORT, HISTORY_COHORT)
#: The cohort a new operational topic joins when an APPLY night creates its
#: Teema (docs/adr/0213): the register, Lovable's operational scope.
NEW_TOPIC_COHORT = REGISTER_COHORT
#: Cohorts a rebuild may supersede (`enroll --supersede`): only the pilot.
SUPERSEDABLE_COHORTS = (PILOT_COHORT,)
#: The provenance kinds that make a Teema a member of an enrolled cohort: a
#: Teema the package created, and a pilot Teema it rebased in place (ADR 0202 §1).
MEMBER_KINDS = ("MATTER", "REBASE")

#: The import's own review hold (docs/adr/0202 §3): a Lovable column the
#: package's `field_decisions` marks thus is seeded ``import_held``.
HELD_FOR_REVIEW = "HELD_FOR_REVIEW"
#: Lovable's column → the synchronised field it feeds (one set with the importer).
IMPORT_HELD_COLUMNS = HELD_FIELD_COLUMNS

#: A topic's source activity (docs/adr/0202 §6), as Lovable's dashboard counts
#: «ongoing». Counts only; nothing is written because of it.
ACTIVITY_ONGOING = "ONGOING"
ACTIVITY_FINISHED = "FINISHED"
ACTIVITY_CONTINUES = "CONTINUES"
ACTIVITY_NOT_OPERATIONAL = "NOT_OPERATIONAL"
ACTIVITY_MISSING = "MISSING"
ACTIVITIES = (
    ACTIVITY_ONGOING,
    ACTIVITY_FINISHED,
    ACTIVITY_CONTINUES,
    ACTIVITY_NOT_OPERATIONAL,
    ACTIVITY_MISSING,
)
#: `hetkeseis` values (as `name_key`) that end a topic's ongoing work.
FINISHED_STAGES = frozenset({"joustunud", "rohkem ei tegele"})

#: Circuit breakers (docs/adr/0192 §7): a night that would touch more Teemad than
#: this, or raise more conflicts, writes nothing and waits for a person.
MAX_CHANGED_MATTERS = 20
#: Teemad one night may *restore* (owner decision 11.11): ordinary lawyer edits
#: Lovable is put back over. Separate from `MAX_CHANGED_MATTERS`, so a busy day
#: of local edits never stops the source's own changes; over it, the restores
#: wait (`RESTORE_BREAKER`) and the source changes are written.
MAX_RESTORED_MATTERS = 100
#: Held fields (REVIEW_REQUIRED or CONFLICT, any reason: a technical hold, an
#: import hold or a code no rule names) stay until a person or the source moves
#: them, so this breaker is on the *rise* since the previous run that recorded
#: its decisions, never on the standing total (audit 2026-11-11 LS-07; it
#: replaces ``MAX_NEW_CONFLICTS``, which source authority had made inert). A
#: night whose planner suddenly holds this many more fields is a broken mapping
#: or a broken reading, not a night's work: it writes nothing (`BREAKER`).
#: Proposed value; production needs the owner's approval.
MAX_NEW_HOLDS = 25
#: The live source population (the index of every live topic) may move this
#: much against the previous baseline reading before an APPLY run refuses to
#: write (`POPULATION_CHANGE`): more than `POPULATION_MAX_DELTA` topics and more
#: than `POPULATION_MAX_SHARE` of the previous count. A changed scope, a row
#: policy hiding rows or a truncated read, never a night's work. A COMPARE run
#: reads and stores the new population as usual, which is how a person accepts
#: a real change. Proposed values; production needs the owner's approval.
POPULATION_MAX_DELTA = 50
POPULATION_MAX_SHARE = 0.05
#: How old a backup set may be when the mutating stage starts (the host script
#: takes and verifies one minutes before; a stale or invented name writes nothing).
BACKUP_MAX_AGE = dt.timedelta(hours=2)
#: The verification a backup set must prove before an APPLY run writes (audit
#: 2026-11-11 DR-05): the host's `juristid-verify-backup.sh --record` writes
#: ``VERIFIED-L<level>`` into the set once level 1 (checksums) and level 2 (the
#: archive reads to the end, the pools hold the set's objects) have passed, and
#: the host script mounts the set read-only into the run's container at the
#: directory `HOST_BACKUP_DIR_ENV` names (`backup_proof_problem`).
BACKUP_PROOF_LEVEL = 2
#: More cohort rows missing from the source than this is a broken read or a
#: broken source, never a night's work: the run stops.
MAX_MISSING = 5
#: New Teemad one night may create (docs/adr/0213): the department opens a few
#: topics a week; more than this in one night is a changed source scope or a
#: broken reading, never a night's work. A separate breaker: the field breakers
#: above are unchanged, and a night over this creates none and writes the rest.
MAX_NEW_TOPICS = 10
SNAPSHOT_RETENTION_DAYS = 30
_BACKUP_SET = re.compile(r"^sets/\d{8}T\d{6}Z$")

AUTO_FIELDS = ("title", "owner", "next_action_date", "onenote_link")
#: Written automatically only under exact preconditions (`FIELD_POLICY`,
#: docs/adr/0213): a stage that does not end the work, a new opinion request
#: where none is open, an added commencement of an unambiguous kind.
CONDITIONAL_FIELDS = ("stage", "opinion_deadline", "commencements")
#: Every field a run may write, in the order it writes them.
WRITABLE_FIELDS = AUTO_FIELDS + CONDITIONAL_FIELDS
HELD_FIELDS = (
    "next_action_text",
    "stage",
    "opinion_deadline",
    "sent",
    "sent_date",
    "addressee",
    "koda_initiative",
    "legal_act",
    "received",
    "commencements",
    "operational_scope",
)
FIELDS = AUTO_FIELDS + HELD_FIELDS
#: The current step's wording and day: one operation (`_step_decisions`).
STEP_FIELDS = ("next_action_text", "next_action_date")
#: Lovable's structured stage link (`topic_stage_link`, this topic as `prev`):
#: compared and reported, never written (no service writes a continuation
#: without closing the predecessor). Not a `SOURCE_FIELDS` field: it is read off
#: the bundle's links, beside the canonical row values.
CONTINUATION = "continuation"
#: A new operational topic, as a decision's field (`LovableSyncDecision.field`).
NEW_TOPIC = "new_topic"
#: What `resolve_field` accepts.
RESOLVABLE_FIELDS = (*FIELDS, CONTINUATION)

#: Why a source change was held. Codes, never business text.
HELD = "HELD_FIELD"
MATTER_CLOSED = "MATTER_CLOSED"
NO_BASELINE = "NO_BASELINE"
UNRESOLVED_OWNER = "UNRESOLVED_OWNER"
NOT_ASSIGNABLE = "OWNER_NOT_ASSIGNABLE"
TITLE_REVIEWED = "TITLE_WAS_REVIEWED"
TITLE_EMPTY = "TITLE_EMPTY"
TEXT_CHANGED = "NEXT_STEP_TEXT_CHANGED"
WITH_TEXT = "DATE_WITH_TEXT_CHANGE"
NO_STEP = "NO_SOURCE_STEP"
NOT_SOURCE_STEP = "STEP_NOT_FROM_SOURCE_WORDING"
DATE_CLEARED = "DATE_CLEARED"
DATE_PAST = "DATE_IN_PAST"
NOT_EXACT = "STEP_DATE_NOT_EXACT"
LINK_NOT_HTTPS = "LINK_NOT_HTTPS"
#: The conditional fields' holds (docs/adr/0213).
STAGE_ENDS_WORK = "STAGE_ENDS_WORK"
REQUEST_OPEN = "REQUEST_ALREADY_OPEN"
REQUEST_CLEARED = "REQUEST_CLEARED"
REQUEST_ANSWERED = "REQUEST_SENT_IN_SOURCE"
REQUEST_INITIATIVE = "REQUEST_ON_INITIATIVE"
COMMENCEMENT_CLASH = "COMMENCEMENT_DAY_CLASH"
CONTINUATION_CHANGED = "CONTINUATION_CHANGED"
#: A new topic's holds (docs/adr/0213 §new topics).
NEW_UUID_TOMBSTONE = "UUID_HELD_BY_TOMBSTONE"
NEW_UUID_LIVE = "UUID_HELD_BY_UNSYNCED_TEEMA"
NEW_REFERENCE_TAKEN = "REFERENCE_TAKEN"
NEW_REFERENCE_DELETED = "REFERENCE_OF_DELETED_TEEMA"
NEW_NOT_READ = "NOT_IN_READING"
NEW_UNREADABLE = "READING_REFUSED"
NEW_INVALID = "READING_INVALID"
NEW_PLAN = "PLAN_PROBLEM"
NEW_NO_COHORT = "NO_TARGET_COHORT"
NEW_FAILED = "CREATE_FAILED"
NEW_BREAKER = "NEW_TOPIC_BREAKER"
#: The import held the field for a person (docs/adr/0202 §6); only `resolve_field` clears it.
IMPORT_HELD = "IMPORT_HELD"
BREAKER = "CIRCUIT_BREAKER"
PARTIAL = "PARTIAL_FAILURE"
NO_BACKUP = "NO_VERIFIED_BACKUP"
COMPARE_ONLY = "COMPARE_ONLY"
#: An applied Teema did not read back as written: the run stops writing, and
#: every eligible change after it is recorded as not executed (ADR 0192 §7).
VERIFY_MISMATCH = "VERIFY_MISMATCH"
VERIFY_STOPPED = "VERIFY_STOPPED"
#: An operator's `resolve_field` decision carries this reason prefix.
RESOLVED_PREFIX = "RESOLVED:"

#: `Kontrolli muudatusi` is a person pressing a button: a second press this soon
#: after the last is refused, and a check that would record exactly what the
#: newest check already recorded records nothing new (audit 2026-10-28 C-06).
CHECK_MIN_INTERVAL = dt.timedelta(seconds=60)


class SyncRefused(Exception):
    """The run did not start, or stopped before writing. ``code`` goes to the ledger."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# The field policy (docs/adr/0213, brief 2026-11-09 §6 B3)
# ---------------------------------------------------------------------------

#: How a reconciled field may change in Juristid.
AUTO = "AUTO"  # written whenever A is reliable, B = A and the service supports it
CONDITIONAL = "CONDITIONAL"  # written only under the exact preconditions stated
REVIEW = "REVIEW"  # never written by the machine: a person decides
NOT_REPRESENTABLE = "NOT_REPRESENTABLE"  # Juristid has no honest place for it
#: Lovable is authoritative (docs/adr/0213 §10, owner brief 2026-11-11): the
#: latest verified source value is written through the field's domain service,
#: also over a local divergence; only a technical problem holds it
#: (`TECHNICAL_HOLDS`). The historical register keeps the earlier classes.
AUTHORITATIVE = "AUTHORITATIVE"
POLICY_CLASSES = (AUTO, CONDITIONAL, REVIEW, NOT_REPRESENTABLE, AUTHORITATIVE)
#: The fields source authority writes.
AUTHORITATIVE_FIELDS = (
    "title",
    "owner",
    "onenote_link",
    "next_action_text",
    "next_action_date",
    "stage",
    "opinion_deadline",
    "sent",
    "sent_date",
    "addressee",
    "koda_initiative",
    "legal_act",
    "received",
    "commencements",
)


@dataclass(frozen=True)
class FieldPolicy:
    """One reconciled field's class, the service that writes it (if any) and why,
    in Estonian for the administration pages."""

    field: str
    policy: str
    explanation: str
    service: str = ""


def _policy(field_name: str, policy: str, explanation: str, service: str = "") -> FieldPolicy:
    return FieldPolicy(field_name, policy, explanation, service)


#: Every reconciled field, the continuation, a new topic and a vanished source
#: (interface 3), as source authority writes them for the operational cohorts
#: (docs/adr/0213 §10). The closed historical register (`lovable-history`) keeps
#: the rules of 0213 §4: every change there is a person's (MATTER_CLOSED).
#: `tests/test_r4_authority.py` holds the table and the engine together.
FIELD_POLICY: dict[str, FieldPolicy] = {
    p.field: p
    for p in (
        _policy(
            "title",
            AUTHORITATIVE,
            "Pealkiri järgib Lovable'it. Muutmata allika korral jääb Juristides tehtud muudatus "
            "alles; allika muutus kirjutatakse üle. Tühja pealkirja ei kirjutata.",
            "set_matter_title(provenance=…)",
        ),
        _policy(
            "owner",
            AUTHORITATIVE,
            "Vastutaja järgib Lovable'it, kui nimi vastab täpselt ühele aktiivsele kasutajale, "
            "kellele saab tööd määrata; avatud tegevus liigub kaasa. Tuvastamata nimi jääb "
            "ootele.",
            "assign_matter(provenance=…)",
        ),
        _policy(
            "next_action_date",
            AUTHORITATIVE,
            "Tegevuse päev on Lovable'i «tegevuse tähtaeg»; see on ühe toiminguna seotud "
            "sõnastusega (vt järgmise tegevuse sõnastus).",
            "set_next_action",
        ),
        _policy(
            "onenote_link",
            AUTHORITATIVE,
            "Uus https-link lisatakse uue allikaviitena; vana jääb alles. Eemaldatud või "
            "mitte-https link jääb ootele: allikaviiteid ei kustutata.",
            "uus MatterSourceReference",
        ),
        _policy(
            "next_action_text",
            AUTHORITATIVE,
            "Praegune tegevus on Lovable'i «järgmiseks» ja «tegevuse tähtaeg». "
            "Liik (tee/oota/jälgi) loetakse lause esimesest tegusõnast "
            "(Koostan/Kirjutan/Esitan/Saadan → teen; Ootan → ootan; "
            "Jälgin/Vaata(n) → jälgin); muu lause jääb sõnasõnaliselt «liik määramata» "
            "tegevuseks. Uus tegevus asendab vana (ajalugu jääb), tehtuks midagi ei märgita.",
            "set_next_action / cancel_next_action",
        ),
        _policy(
            "stage",
            AUTHORITATIVE,
            "Hetkeseis ja elutsükkel järgivad Lovable'it: «jõustunud» sulgeb teema põhjusega "
            "«Jõustus või töö lõppes», «rohkem ei tegele» põhjusega «Koda ei tegele edasi», "
            "menetlusetappide seos järglasega sulgeb teema jätkuna järglase all. Kui Lovable "
            "teema uuesti avab, avatakse see vastava hetkeseisuga. Kaardistamata silt ja "
            "Juristides puuduv järglane jäävad ootele.",
            "close_matter_from_source / change_stage / reopen_matter_into_stage",
        ),
        _policy(
            "opinion_deadline",
            AUTHORITATIVE,
            "Arvamuse tähtaeg järgib Lovable'it: praegune küsimine nihkub uuele päevale (sama "
            "küsimine), puuduva korral luuakse küsimine ainult siis, kui midagi pole saadetud ja "
            "päev pole möödas. Möödunud tähtaeg pole kunagi saatmine ega kohustus.",
            "change_response_deadline",
        ),
        _policy(
            "sent",
            AUTHORITATIVE,
            "«Välja» kuupäev tähendab, et arvamus saadeti sel päeval; «ei saatnud», et ei "
            "saadetud. Saatmine märgitakse teemale ilma dokumendita («Arvamuse dokument pole "
            "veel saadaval»); praegune küsimine lõpeb. Dokumenti, adressaati ega allkirja ei "
            "looda.",
            "LovableOpinionOutcome + resolve_response_deadline",
        ),
        _policy(
            "sent_date",
            AUTHORITATIVE,
            "Saatmise kuupäev käib koos «välja» väljaga; kaks eri kuupäeva jäävad ootele.",
            "LovableOpinionOutcome",
        ),
        _policy(
            "addressee",
            AUTHORITATIVE,
            "Adressaat (kellele) salvestatakse saatmise seisu juurde allika sõnastuses; "
            "asutuse vastet ei arvata. Ilma saatmiseta jääb see ootele.",
            "LovableOpinionOutcome",
        ),
        _policy(
            "koda_initiative",
            AUTHORITATIVE,
            "Koja algatus muudab menetlusliigi Koja algatuseks; algatuse lõppemisel pole teada, "
            "milline liik see on, nii et see jääb ootele.",
            "change_track",
        ),
        _policy(
            "legal_act",
            AUTHORITATIVE,
            "Õigusakt järgib Lovable'it, kui silt vastab täpselt ühele kehtivale liigile; muu "
            "jääb ootele.",
            "set_legal_instruments",
        ),
        _policy(
            "received",
            AUTHORITATIVE,
            "Saabumise kuupäev järgib Lovable'it; tulevane päev jääb ootele.",
            "set_matter_dates",
        ),
        _policy(
            "commencements",
            AUTHORITATIVE,
            "Lisandunud jõustumine lisatakse, kui selle liik on üheselt loetav (üldises korras, "
            "kindel kuupäev, eeldatav kuupäev — eeldatav jääb hinnanguks). Muudetud või "
            "eemaldatud jõustumine ja sama päeva teine kirje jäävad ootele.",
            "add_effective_date / add_important_date",
        ),
        _policy(
            "operational_scope",
            NOT_REPRESENTABLE,
            "Juristides pole operatiivse ulatuse mõistet; ulatusest väljumine ei sulge ega "
            "kustuta teemat ja jääb teatena ootele.",
        ),
        _policy(
            CONTINUATION,
            AUTHORITATIVE,
            "Lovable'i menetlusetappide seos järglasega sulgeb eelkäija jätkuna järglase all "
            "(sama toiming kui hetkeseis). Järglane, keda Juristides veel pole, jääb ootele, kuni "
            "see on olemas.",
            "close_matter_from_source(successor=…)",
        ),
        _policy(
            NEW_TOPIC,
            CONDITIONAL,
            "Uus operatiivne Lovable'i teema luuakse ainult rakendamise ööl, kui selle Lovable'i "
            "tunnust ei hoia ükski teema (ka kustutatud), viide on vaba ja lugemine on ohutu; "
            "samal ööl viiakse selle praegune tegevus ja saatmise seis Lovable'iga kooskõlla. "
            "Sama teema luuakse ainult üks kord.",
            "lovable_reading.read_topic + lovable_pilot CREATE",
        ),
        _policy(
            "source_missing",
            NOT_REPRESENTABLE,
            "Lovable'ist kadunud või kustutatud teemat ei kustutata ega suleta Juristides; see "
            "jääb teatena alles, kuni inimene otsustab.",
        ),
        _policy(
            "identifier_changed",
            NOT_REPRESENTABLE,
            "Muutunud Lovable'i number või tunnus peatab ainult selle teema võrdluse; viidet ei "
            "muudeta automaatselt.",
        ),
    )
}


def policy_of(field_name: str) -> FieldPolicy:
    """A field's policy; an unknown field is REVIEW, never automatic."""
    return FIELD_POLICY.get(field_name) or FieldPolicy(field_name, REVIEW, "Tundmatu väli.")


# ---------------------------------------------------------------------------
# Canonical values
# ---------------------------------------------------------------------------


def text_value(raw: Any) -> str:
    """Whitespace-collapsed, NFC text: two spellings of one sentence are one value."""
    return " ".join(unicodedata.normalize("NFC", str(raw or "")).split())


def name_key(raw: Any) -> str:
    """Lovable's own `raw_norm`: lower-cased, collapsed, diacritics folded."""
    return text_value(raw).lower().translate(str.maketrans("õäöüšž", "oaousz"))


def day_value(raw: Any) -> str | None:
    if not raw:
        return None
    try:
        return dt.date.fromisoformat(str(raw)[:10]).isoformat()
    except ValueError:
        return f"?{text_value(raw)}"


def operative_owner(row: dict[str, Any]) -> str:
    """Lovable's `topic_operative_vastutaja`: the new owner if named, else the owner."""
    new = text_value(row.get("uus_vastutaja"))
    if new:
        return "" if name_key(new) in {"vastutaja puudub", "vastutaja puudub."} else new
    return text_value(row.get("vastutaja"))


def _commencements(bundle: dict[str, Any]) -> list[list[Any]]:
    items = bundle.get("commencements") or []
    ordered = sorted(items, key=lambda r: (r.get("position") or 0, str(r.get("liik") or "")))
    return [
        [text_value(r.get("liik")), day_value(r.get("kuupaev")), text_value(r.get("tapsustus"))]
        for r in ordered
    ]


SOURCE_FIELDS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "title": lambda b: text_value(b["row"].get("teema")),
    "owner": lambda b: operative_owner(b["row"]),
    "next_action_text": lambda b: text_value(b["row"].get("jargmiseks")),
    "next_action_date": lambda b: day_value(b["row"].get("tegevuse_tahtaeg")),
    "onenote_link": lambda b: str(b["row"].get("onenote_link") or "").strip(),
    "stage": lambda b: text_value(b["row"].get("hetkeseis")),
    "opinion_deadline": lambda b: day_value(b["row"].get("arvamuse_tahtaeg")),
    "sent": lambda b: text_value(b["row"].get("valja")),
    "sent_date": lambda b: day_value(b["row"].get("valja_kuupaev")),
    "addressee": lambda b: text_value(b["row"].get("kellele")),
    "koda_initiative": lambda b: bool(b["row"].get("koja_ettepanek")),
    "legal_act": lambda b: text_value(b["row"].get("oigusakt")),
    "received": lambda b: day_value(b["row"].get("sisse")),
    "commencements": _commencements,
    "operational_scope": lambda b: bool(b["row"].get("operatiivne_ulatus")),
}


def bundle_of(snapshot: dict[str, Any], source_id: Any) -> dict[str, Any] | None:
    """A topic's bundle in a reading: the cohort's, or — for a Teema the same
    night created from a new topic (docs/adr/0213) — the new topics' reading."""
    key = str(source_id)
    found = (snapshot.get("cohort") or {}).get(key)
    if found is None:
        found = ((snapshot.get("new_topics") or {}).get("cohort") or {}).get(key)
    return found


def canonical_source(bundle: dict[str, Any]) -> dict[str, Any]:
    return {key: extract(bundle) for key, extract in SOURCE_FIELDS.items()}


def source_activity(bundle: dict[str, Any] | None) -> str:
    """Whether Lovable counts the topic as ongoing work (docs/adr/0202 §6).

    Lovable's own classification: a live topic with `operatiivne_ulatus`, whose
    `hetkeseis` is neither «jõustunud» nor «rohkem ei tegele», and which is not
    the predecessor (`prev`) of a stage link — its work continues elsewhere."""
    if bundle is None:
        return ACTIVITY_MISSING
    row = bundle.get("row") or {}
    if not row.get("operatiivne_ulatus"):
        return ACTIVITY_NOT_OPERATIONAL
    if name_key(row.get("hetkeseis")) in FINISHED_STAGES:
        return ACTIVITY_FINISHED
    me = str(row.get("id"))
    if any(str(link.get("prev")) == me for link in bundle.get("stage_links") or []):
        return ACTIVITY_CONTINUES
    return ACTIVITY_ONGOING


# ---------------------------------------------------------------------------
# The cohorts and their baselines
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Cohort:
    """One synchronised population (docs/adr/0202 §6)."""

    key: str
    #: The `EvidencePilotRecord.pilot` whose MATTER records select it.
    pilot: str
    #: The approved package digest; ``""`` for the built-in pilot (every pilot record).
    package_sha256: str
    #: How many Teemad the enrolment recorded (`LovableSyncCohort.expected`).
    expected: int
    #: Members that left with their Teema's permanent deletion (`LovableSyncRetirement`).
    retired: int = 0
    #: Members that joined after the enrolment: Teemad the synchronisation
    #: created from new Lovable topics (`MembershipBasis.NEW_TOPIC`, docs/adr/0213).
    joined: int = 0

    @property
    def builtin(self) -> bool:
        return self.key == PILOT_COHORT

    @property
    def required(self) -> int:
        """How many Teemad a run must find: the enrolled size plus the members
        that joined as new topics, less the authorised retirements, never below
        zero (audit 2026-11 C-L-04; docs/adr/0213). A state missing without a
        retirement is still one short of this, and a joined member is counted
        only through its append-only membership."""
        return max(0, self.expected + self.joined - self.retired)


def pilot_cohort(retired: int = 0) -> Cohort:
    # Read at call time: tests (and only tests) size the pilot down.
    return Cohort(PILOT_COHORT, PILOT, "", EXPECTED_COHORT, retired)


def _arrivals() -> tuple[Counter[str], Counter[str]]:
    """(joined, moved out) per cohort, in one query when nothing ever moved:
    members a cohort gained as new topics or moved in from the historical
    register when Lovable reopened them (owner decision 11.11), and members a
    cohort lost to such a move (the Teema's earlier membership names it)."""
    joined: Counter[str] = Counter()
    into: dict[Any, str] = {}
    for basis, cohort, matter_id in LovableSyncMembership.objects.filter(
        basis__in=(MembershipBasis.NEW_TOPIC, MembershipBasis.MOVED)
    ).values_list("basis", "cohort", "matter_id"):
        joined[cohort] += 1
        if basis == MembershipBasis.MOVED:
            into[matter_id] = cohort
    out: Counter[str] = Counter()
    if into:
        for matter_id, cohort in LovableSyncMembership.objects.filter(
            matter_id__in=list(into)
        ).values_list("matter_id", "cohort"):
            if cohort != into[matter_id]:
                out[cohort] += 1
    return joined, out


def joined_members() -> Counter[str]:
    """How many members each cohort gained (`_arrivals`). Reads only."""
    return _arrivals()[0]


def moved_out_members() -> Counter[str]:
    """How many members each cohort lost to a move (`_arrivals`). Reads only."""
    return _arrivals()[1]


def retired_members() -> Counter[str]:
    """How many members each cohort lost to a permanent deletion. Reads only."""
    return Counter(
        dict(
            LovableSyncRetirement.objects.values("cohort")
            .annotate(n=Count("id"))
            .values_list("cohort", "n")
        )
    )


def cohorts() -> list[Cohort]:
    """The pilot (until a rebuild retires it), then every enrolled cohort that is
    not retired, each with its members retired by a permanent deletion. Reads
    only: no control row, no state."""
    rows = list(LovableSyncCohort.objects.order_by("enrolled_at", "key"))
    retired = {row.key for row in rows if row.retired_at is not None}
    gone = retired_members()
    joined, left = _arrivals()
    enrolled = [
        Cohort(
            row.key,
            row.pilot,
            row.package_sha256,
            row.expected,
            gone[row.key] + left[row.key],
            joined[row.key],
        )
        for row in rows
        if row.retired_at is None and row.key != PILOT_COHORT
    ]
    pilot = [] if PILOT_COHORT in retired else [pilot_cohort(gone[PILOT_COHORT])]
    return pilot + enrolled


def expected_cohorts() -> dict[str, int]:
    """Each synchronised cohort's required size, by key: enrolled less retired
    (`Cohort.required`). Read-only (docs/adr/0201: observability never creates
    the control row; this touches no control row)."""
    return {cohort.key: cohort.required for cohort in cohorts()}


def cohort_ledger() -> list[dict[str, Any]]:
    """Per synchronised cohort: enrolled, joined, retired and required, numbers only."""
    return [
        {
            "key": cohort.key,
            "enrolled": cohort.expected,
            **({"joined": cohort.joined} if cohort.joined else {}),
            "retired": cohort.retired,
            "expected": cohort.required,
        }
        for cohort in cohorts()
    ]


def retirement_report() -> dict[str, Any]:
    """What `lovable_sync report` prints about retirements: each cohort's
    numbers, and each retirement as its cohort, kind, moment and whether a
    person is named on it — never the Teema, its reference or a reason."""
    active = {row["key"]: row for row in cohort_ledger()}
    return {
        "cohorts": {
            key: {k: v for k, v in row.items() if k != "key"} for key, row in active.items()
        },
        "retired": [
            {
                "cohort": cohort,
                "kind": kind,
                "retired_at": moment,
                "attributed": actor is not None,
                "active": cohort in active,
            }
            for cohort, kind, moment, actor in LovableSyncRetirement.objects.order_by(
                "retired_at", "id"
            ).values_list("cohort", "kind", "retired_at", "actor_id")
        ],
    }


def expected_total() -> int:
    """How many Teemad the synchronisation must hold across every cohort."""
    return sum(expected_cohorts().values())


def _canonical_references(matter_ids: Any) -> Any:
    """The canonical Lovable source references of these Teemad: an import's own
    (never one the synchronisation appended for a new OneNote link)."""
    from app.legacy_import.models import MatterSourceReference

    return (
        MatterSourceReference.objects.filter(
            matter_id__in=matter_ids,
            source_system=SOURCE_SYSTEM,
            source_file_name=SOURCE_TABLE,
            import_batch__isnull=False,
        )
        .exclude(source_contract_version__startswith=MAPPING_VERSION.split("/")[0])
        .select_related("matter")
        .order_by("created_at")
    )


def cohort_references(cohort: Cohort | None = None) -> list[Any]:
    """A cohort's canonical Lovable source references — one per Teema its
    approved provenance names, never one of the 13 that were there before (they
    have no record) and never a Teema found by its reference number.

    For an enrolled cohort the members are the package's MATTER (created) and
    REBASE (a pilot Teema brought to the snapshot in place) records, and each
    member's reference is the one the package wrote — its import batch — not the
    pilot's older one (docs/adr/0202 §6)."""
    from app.legacy_import.models import EvidencePilotRecord, ImportBatch

    cohort = cohort or pilot_cohort()
    if cohort.builtin:
        records = EvidencePilotRecord.objects.filter(pilot=PILOT, record_kind="MATTER")
        return list(_canonical_references(records.values("matter_id")))
    members = list(
        EvidencePilotRecord.objects.filter(
            pilot=cohort.pilot,
            package_sha256=cohort.package_sha256,
            record_kind__in=MEMBER_KINDS,
        ).values_list("matter_id", "record_kind", "target_model", "target_id")
    )
    created = {matter for matter, kind, *_ in members if kind == "MATTER"}
    references = list(_canonical_references({matter for matter, *_ in members}))
    # The package's import batch: the one its created Teemad's references are
    # in, and the batch the importer named after the package.
    batches = {r.import_batch_id for r in references if r.matter_id in created}
    batches |= set(
        ImportBatch.objects.filter(
            importer_version__startswith=f"{cohort.pilot}/",
            notes__contains=cohort.package_sha256,
        ).values_list("pk", flat=True)
    )
    # A REBASE record that names its new reference decides for its Teema; the
    # batch is the fallback for one that does not.
    named = {
        matter: str(target)
        for matter, kind, model, target in members
        if kind == "REBASE" and model == "legacy_import.MatterSourceReference" and target
    }
    return [
        r
        for r in references
        if (
            str(r.pk) == named[r.matter_id]
            if r.matter_id in named
            else r.import_batch_id in batches
        )
    ]


def _action_dict(action: Any) -> dict[str, Any] | None:
    if action is None:
        return None
    return {
        "id": str(action.pk),
        "text": action.text,
        "kind": action.kind,
        "date_semantics": action.date_semantics,
        "target_date": action.target_date.isoformat() if action.target_date else None,
        "date_precision": action.date_precision,
    }


def _imported_action(
    matter: Any, pilot: str = PILOT, package_sha256: str = ""
) -> tuple[dict[str, Any] | None, str]:
    """The step the import wrote and the treatment its package stated. ``pilot``
    is the cohort's provenance key: the step is looked up under it, never under
    another importer's.

    Read off the pilot's own ``NEXT_ACTION_SET`` event, not off the row as it is
    now: «Vaatasin üle» and a rescheduled check change a step's day **in place**,
    keeping its id, and a baseline taken from the edited row would make the
    lawyer's day look like the pilot's and let a source change replace it. A row
    edited in place since gets an unknowable precision, so it reads as a local
    change, never as the baseline."""
    from app.audit.models import ChangeEvent
    from app.legacy_import.models import EvidencePilotRecord
    from app.workflow.models import NextAction

    records = EvidencePilotRecord.objects.filter(
        pilot=pilot, matter=matter, record_kind="NEXT_ACTION"
    )
    if package_sha256:
        records = records.filter(package_sha256=package_sha256)
    record = records.order_by("-created_at").first()
    if record is None or record.target_id is None:
        return None, ""
    action = NextAction.objects.filter(pk=record.target_id).first()
    event = (
        ChangeEvent.objects.filter(
            matter=matter, object_id=record.target_id, event_type="NEXT_ACTION_SET"
        )
        .order_by("occurred_at")
        .first()
    )
    if action is None or event is None:
        return None, ""
    payload = event.payload or {}
    treatment = str((payload.get("provenance") or {}).get("treatment") or "")
    written = _action_dict(action) or {}
    written.update(
        kind=payload.get("kind", written["kind"]),
        date_semantics=payload.get("date_semantics", written["date_semantics"]),
        target_date=payload.get("target_date"),
    )
    edited = ChangeEvent.objects.filter(
        matter=matter,
        object_id=record.target_id,
        event_type__in=("NEXT_ACTION_REVIEWED", "NEXT_ACTION_RESCHEDULED"),
    ).exists()
    if edited:
        written["date_precision"] = "?"
    return written, treatment


def _imported_owner_id(matter: Any) -> str | None:
    """Who the pilot made the owner: the first hand-over's ``from``, or the owner
    now if nobody has handed it over since."""
    from app.accounts.models import User
    from app.audit.models import ChangeEvent

    first = (
        ChangeEvent.objects.filter(matter=matter, event_type="MATTER_ASSIGNED")
        .order_by("occurred_at")
        .first()
    )
    if first is None:
        return str(matter.owner_id) if matter.owner_id else None
    name = (first.payload or {}).get("from_name")
    if not name:
        return None
    users = list(User.objects.filter(display_name=name).values_list("pk", flat=True)[:2])
    return str(users[0]) if len(users) == 1 else "?"


def seed_values(
    reference: Any,
    cohort: Cohort | None = None,
    previous: dict[str, Any] | None = None,
    resolver: OwnerResolver | None = None,
) -> dict[str, Any]:
    """A's first value, from what the import wrote — never from the current row,
    which may already hold a lawyer's edit.

    For an enrolled cohort, every field the package held for review
    (`HELD_FOR_REVIEW`) is marked ``import_held`` (docs/adr/0202 §6). The pilot's
    baselines are seeded exactly as 0192 seeds them.

    ``previous`` is a rebased Teema's pilot baseline. The title's and the
    owner's target is what the rebuild wrote, by its own record (``TITLE``: the
    new reference's `source_title`; ``OWNER``: the user it assigned), and
    otherwise what the pilot import gave Juristid — never Juristid's value read
    now, so a lawyer's edit before or after the rebuild stays a local value,
    never a silent convergence — unless Juristid already says exactly what the
    snapshot says, which is a convergence the nightly run would record anyway
    (``ALREADY_CONVERGED``), so the target is that value. The OneNote link's
    target is the pilot's when the source did not move, else the new
    reference's. A rebased Teema whose step the rebuild did not write (no
    ``NEXT_ACTION`` record: a lawyer's step it kept) keeps the pilot's own step
    as the baseline, so the lawyer's step still reads as a local change."""
    cohort = cohort or pilot_cohort()
    matter = reference.matter
    raw = reference.source_row_raw or {}
    bundle = {"row": raw.get("row") or {}, "commencements": raw.get("commencements") or []}
    source = canonical_source(bundle)
    action, treatment = _imported_action(matter, cohort.pilot, cohort.package_sha256)
    applied: dict[str, Any] = {key: {"source": value} for key, value in source.items()}
    applied["title"]["target"] = text_value(reference.source_title)
    applied["owner"]["target"] = _imported_owner_id(matter)
    applied["onenote_link"]["target"] = reference.onenote_url
    applied["next_action"] = {"target": action, "treatment": treatment}
    if previous is not None:
        written = _rebuild_writes(matter, cohort, reference)
        now = {
            "title": text_value(matter.title),
            "owner": str(matter.owner_id) if matter.owner_id else None,
        }
        for name in ("title", "owner"):
            before = previous.get(name) or {}
            if name in written:
                applied[name]["target"] = written[name]
            elif now[name] is not None and now[name] == _source_target(
                name, source[name], resolver
            ):
                applied[name]["target"] = now[name]
            elif "target" in before:
                applied[name]["target"] = before["target"]
        if not _has_step_record(matter, cohort):
            action, treatment = _imported_action(matter, PILOT)
            applied["next_action"] = {"target": action, "treatment": treatment}
        link = previous.get("onenote_link") or {}
        if "target" in link and link.get("source") == source["onenote_link"]:
            applied["onenote_link"]["target"] = link["target"]
    if not cohort.builtin:
        for name in import_held_fields(raw):
            applied[name]["import_held"] = True
    lovable = raw.get("lovable") or {}
    return {
        "source_id": lovable["id"],
        "reference": f"{matter.reference_year}_{matter.reference_number}",
        "cohort": cohort.key,
        "applied": applied,
        "applied_version": lovable.get("version"),
        "applied_snapshot_sha256": reference.source_snapshot_sha256,
    }


def _source_target(name: str, value: Any, resolver: OwnerResolver | None) -> Any:
    """What Juristid would hold for the source's value: the title itself, or the
    one user the owner's name resolves to (`OwnerResolver`), else ``None``."""
    if name == "title":
        return value or None
    target, _reason = (resolver or OwnerResolver([])).resolve(value)
    return target


def _has_step_record(matter: Any, cohort: Cohort) -> bool:
    from app.legacy_import.models import EvidencePilotRecord

    return EvidencePilotRecord.objects.filter(
        pilot=cohort.pilot,
        package_sha256=cohort.package_sha256,
        matter=matter,
        record_kind="NEXT_ACTION",
    ).exists()


def _rebuild_writes(matter: Any, cohort: Cohort, reference: Any) -> dict[str, Any]:
    """What a rebuild package wrote to a rebased Teema's title and owner, by its
    own records: ``TITLE`` (the written title is the new reference's
    `source_title`) and ``OWNER`` (the user it assigned)."""
    from app.legacy_import.models import EvidencePilotRecord

    out: dict[str, Any] = {}
    for kind, target in EvidencePilotRecord.objects.filter(
        pilot=cohort.pilot,
        package_sha256=cohort.package_sha256,
        matter=matter,
        record_kind__in=("TITLE", "OWNER"),
    ).values_list("record_kind", "target_id"):
        if kind == "TITLE":
            out["title"] = text_value(reference.source_title)
        elif target is not None:
            out["owner"] = str(target)
    return out


def seed_state(reference: Any, cohort: Cohort | None = None) -> LovableSyncState:
    """A new state for a Teema, seeded by `seed_values`, and the membership that
    outlives it (REL-01)."""
    state = LovableSyncState.objects.create(
        matter=reference.matter, **seed_values(reference, cohort)
    )
    remember_membership(cohort=state.cohort, matter_id=state.matter_id, source_id=state.source_id)
    return state


def remember_membership(
    *, cohort: str, matter_id: Any, source_id: Any, basis: str = MembershipBasis.ENROLMENT
) -> LovableSyncMembership:
    """Record that this Teema is a member of ``cohort`` under ``source_id``
    (owner brief 2026-11-06 §17, REL-01), once. Called only where a state of
    exactly that cohort and Lovable id has just been written from the cohort's
    approved provenance (`seed_state`, `reseed_state`), or by the deletion after
    checking that provenance (`lovable_sync_retirement.members_of`); migration
    0024's trigger refuses one without such a state on a live Teema. An earlier
    membership of the same cohort is kept as it is, even under another id: a
    retirement must then match that one, so a changed identity fails closed."""
    existing = LovableSyncMembership.objects.filter(cohort=cohort, matter_id=matter_id).first()
    if existing is not None:
        return existing
    return LovableSyncMembership.objects.create(
        cohort=cohort, matter_id=matter_id, source_id=source_id, basis=basis
    )


def reseed_state(
    state: LovableSyncState,
    reference: Any,
    cohort: Cohort,
    *,
    reason: str,
    resolver: OwnerResolver | None = None,
) -> LovableSyncBaselineArchive:
    """A rebased Teema's state, brought to the rebuild's snapshot in place
    (docs/adr/0202 §6): its baseline and last observation are archived exactly as
    they were, then the same row takes the rebuild's baseline, with nothing
    observed yet. Its runs and decisions keep pointing at it."""
    archive = LovableSyncBaselineArchive.objects.create(
        state=state,
        matter_id=state.matter_id,
        cohort_before=state.cohort,
        cohort_after=cohort.key,
        applied=state.applied,
        applied_version=state.applied_version,
        applied_snapshot_sha256=state.applied_snapshot_sha256,
        observed=state.observed,
        observed_version=state.observed_version,
        observed_run_id=state.observed_run_id,
        archived_at=timezone.now(),
        reason=reason[:300],
        package_sha256=cohort.package_sha256,
    )
    values = seed_values(reference, cohort, previous=state.applied, resolver=resolver)
    if str(values["source_id"]) != str(state.source_id):
        raise SyncRefused(
            "IDENTITY_MISMATCH", "A rebased Teema's new reference names another Lovable topic."
        )
    LovableSyncState.objects.filter(pk=state.pk).update(
        cohort=values["cohort"],
        reference=values["reference"],
        applied=values["applied"],
        applied_version=values["applied_version"],
        applied_snapshot_sha256=values["applied_snapshot_sha256"],
        observed={},
        observed_version=None,
        observed_run=None,
        updated_at=timezone.now(),
    )
    remember_membership(
        cohort=values["cohort"], matter_id=state.matter_id, source_id=state.source_id
    )
    return archive


def import_held_fields(raw: dict[str, Any]) -> list[str]:
    """The synchronised fields a package's `field_decisions` held for review.
    The step's wording and its day are one operation: held together."""
    decisions = raw.get("field_decisions") or {}
    held = {
        IMPORT_HELD_COLUMNS[column]
        for column, decision in decisions.items()
        if decision == HELD_FOR_REVIEW and column in IMPORT_HELD_COLUMNS
    }
    if held & set(STEP_FIELDS):
        held |= set(STEP_FIELDS)
    return sorted(held)


def ensure_states() -> list[LovableSyncState]:
    """Seed any cohort Teema that has no state yet; the population afterwards.

    An existing state — the pilot's 100 above all — is never re-seeded or changed.
    A permanently deleted Teema is never seeded (REL-01): its references may still
    be listed if the deletion committed after they were read, and a state on a
    tombstone is not a member (migration 0024 refuses its membership). Each Teema
    is taken under the same row lock a deletion takes, so one that is being
    deleted is seen as deleted once that deletion commits."""
    from app.matters.models import Matter

    existing = set(LovableSyncState.objects.values_list("matter_id", flat=True))
    with transaction.atomic():
        for cohort in cohorts():
            for reference in cohort_references(cohort):
                if reference.matter_id in existing:
                    continue
                live = list(
                    Matter.all_objects.select_for_update(no_key=True)
                    .filter(pk=reference.matter_id, deleted_at__isnull=True)
                    .values_list("pk", flat=True)
                )
                if not live:
                    continue
                seed_state(reference, cohort)
                existing.add(reference.matter_id)
    return population()


def population() -> list[LovableSyncState]:
    """Every state of the pilot and the enrolled cohorts, by reference."""
    return list(
        LovableSyncState.objects.filter(cohort__in=list(expected_cohorts()))
        .select_related("matter")
        .order_by("reference")
    )


def not_yet_read(states: Iterable[LovableSyncState], snapshot: LovableSyncSnapshot) -> set[Any]:
    """The states whose cohort was enrolled after a stored reading was taken. A
    created Teema was never in that reading; a rebased one was, but against the
    pilot's baseline, which the rebuild has replaced. A check (`Kontrolli
    muudatusi`) skips both — no decision, not missing, no spurious change —
    until the next run reads them against their new baseline."""
    late = set(
        LovableSyncCohort.objects.filter(
            enrolled_at__gt=snapshot.taken_at, retired_at__isnull=True
        ).values_list("key", flat=True)
    )
    return {s.pk for s in states if s.cohort in late}


def cohort_size_problem(states: Iterable[LovableSyncState]) -> tuple[str, int, int] | None:
    """The first cohort that does not hold exactly its required number of Teemad —
    the enrolled size less the members retired with an authorised permanent
    deletion (`Cohort.required`): ``(key, held, expected)``, or ``None`` when
    every cohort is whole. A state gone without a retirement is drift and stops
    the run (audit 2026-11 C-L-04)."""
    held = Counter(state.cohort for state in states)
    for key, expected in expected_cohorts().items():
        if held.get(key, 0) != expected:
            return key, held.get(key, 0), expected
    return None


# ---------------------------------------------------------------------------
# B — what Juristid holds now
# ---------------------------------------------------------------------------


def current_values(matter: Any) -> dict[str, Any]:
    from app.legacy_import.models import MatterSourceReference
    from app.workflow.enums import ActionStatus
    from app.workflow.models import NextAction

    link = (
        MatterSourceReference.objects.filter(matter=matter, source_system=SOURCE_SYSTEM)
        .order_by("-created_at")
        .values_list("onenote_url", flat=True)
        .first()
    )
    return {
        "title": text_value(matter.title),
        "owner": str(matter.owner_id) if matter.owner_id else None,
        "onenote_link": link or "",
        "next_action": _action_dict(
            NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).first()
        ),
        # A tombstoned Teema is treated as a closed one: nothing is written to it.
        "is_open": matter.is_open and getattr(matter, "deleted_at", None) is None,
    }


def full_values(matter: Any) -> dict[str, Any]:
    """B for every field the engine compares: `current_values` (the shape the
    read service's bulk reader mirrors) and the conditional fields' values."""
    return {**current_values(matter), **conditional_values(matter)}


#: The important-date kinds that are commencements (`lovable_pilot.IMPORTANT_KINDS`);
#: any other important date is a person's own and never Lovable's.
COMMENCEMENT_IMPORTANT_KINDS = ("PLANNED_COMMENCEMENT", "ESTIMATED_COMMENCEMENT")


def conditional_values(matter: Any) -> dict[str, Any]:
    """B for the conditional fields and the continuation (docs/adr/0213): the
    stage key, the current opinion request's day, whether the Teema is a Chamber
    initiative, its active commencements as ``[target, kind, day]`` (the shape
    `lovable_pilot.derived_values` gives), and the Lovable ids its continuation
    names (through the successor's own synchronisation state, never a title)."""
    from app.intelligence.enums import FactStatus
    from app.intelligence.models import MatterEffectiveDate, MatterImportantDate
    from app.workflow.enums import Track

    dates = [
        ["MatterEffectiveDate", str(kind or ""), day.isoformat() if day else None]
        for kind, day in MatterEffectiveDate.objects.filter(
            matter_id=matter.pk, status=FactStatus.ACTIVE
        ).values_list("kind", "date_value")
    ] + [
        ["MatterImportantDate", str(kind or ""), day.isoformat() if day else None]
        for kind, day in MatterImportantDate.objects.filter(
            matter_id=matter.pk,
            status=FactStatus.ACTIVE,
            kind__in=COMMENCEMENT_IMPORTANT_KINDS,
        ).values_list("kind", "date_value")
    ]
    successor = getattr(matter, "superseded_by_id", None)
    continuation = (
        sorted(
            str(source)
            for source in LovableSyncState.objects.filter(matter_id=successor).values_list(
                "source_id", flat=True
            )
        )
        if successor
        else []
    )
    stage_key = None
    if getattr(matter, "stage_id", None):
        from app.workflow.models import StageVocabulary

        stage_key = (
            StageVocabulary.objects.filter(pk=matter.stage_id).values_list("key", flat=True).first()
        )
    deadline = getattr(matter, "response_deadline", None)
    from app.matters.models import MatterResponseDeadline

    received = getattr(matter, "received_date", None)
    successor_id = getattr(matter, "superseded_by_id", None)
    return {
        # The lifecycle as source authority compares it (docs/adr/0213 §10).
        "lifecycle": {
            "open": bool(getattr(matter, "is_open", False)),
            "stage": stage_key,
            "disposition": str(getattr(matter, "disposition", "") or ""),
            "successor": str(successor_id) if successor_id else None,
        },
        "received": received.isoformat() if received else None,
        "legal_act": sorted(
            str(key) for key in matter.legal_instruments.values_list("key", flat=True)
        )
        if getattr(matter, "pk", None)
        else [],
        "opinion_outcome": _outcome_view(matter.pk),
        "lovable_commencements": lovable_commencements(matter.pk) if matter.pk else [],
        # Requests that have ended (answered, withdrawn, replaced): a lawyer's
        # decisions the request rule must never undo.
        "request_history": MatterResponseDeadline.objects.filter(matter_id=matter.pk).count(),
        # The latest ended request: how it ended, its day and whether a person
        # ended it (the sync ends requests with no actor), or ``None``.
        "request_last_ended": _last_ended_request(matter.pk) if matter.pk else None,
        "stage": stage_key,
        "opinion_deadline": deadline.isoformat() if deadline else None,
        "koda_initiative": getattr(matter, "track", "") == Track.KODA_INITIATIVE,
        "commencements": sorted(dates, key=lambda d: (d[0], d[1], d[2] or "")),
        CONTINUATION: continuation,
    }


def _last_ended_request(matter_id: Any) -> dict[str, Any] | None:
    from app.matters.models import MatterResponseDeadline

    rows = list(
        MatterResponseDeadline.objects.filter(matter_id=matter_id)
        .order_by("-ended_at", "-created_at")
        .values(
            "outcome",
            "deadline",
            "next_deadline",
            "ended_by_id",
            "note",
            "submission_id",
            "requested_at",
        )
    )
    if not rows:
        return None
    row = rows[0]
    # The request's chain: every day it had. A MOVED request keeps its
    # `requested_at`; a legacy request with none is walked back through its
    # MOVED rows (`next_deadline` → the day it was moved from).
    days = {row["deadline"]}
    if row["requested_at"] is not None:
        days |= {r["deadline"] for r in rows if r["requested_at"] == row["requested_at"]}
    else:
        moved = [r for r in rows if r["outcome"] == "MOVED" and r["requested_at"] is None]
        grew = True
        while grew:
            grew = False
            for r in moved:
                if r["next_deadline"] in days and r["deadline"] not in days:
                    days.add(r["deadline"])
                    grew = True
    # Every request Juristid itself answered or declined, by identity.
    answered = sorted(
        {
            r["requested_at"].isoformat()
            for r in rows
            if r["requested_at"] is not None
            and r["outcome"] in ("ANSWERED", "NOT_ANSWERING")
            and not is_sync_ending(
                ended_by_id=r["ended_by_id"], note=r["note"], submission_id=r["submission_id"]
            )
        }
    )
    return {
        "outcome": row["outcome"],
        "deadline": row["deadline"].isoformat() if row["deadline"] else None,
        "days": sorted(day.isoformat() for day in days if day),
        "by_sync": is_sync_ending(
            ended_by_id=row["ended_by_id"],
            note=row["note"],
            submission_id=row["submission_id"],
        ),
        "juristid_answered": answered,
    }


#: How every request ending the synchronisation writes begins (`_apply_request`,
#: `_apply_opinion_outcome`); the sync writes no actor and no submission.
SYNC_ENDING_NOTE = "Lovable'i järgi"


def is_sync_ending(*, ended_by_id: Any, note: str, submission_id: Any) -> bool:
    """Whether an ended request was ended by the synchronisation itself: no
    actor, no submission, and the sync's own note. Anything else — a person, a
    pilot's ending with a submission, an ending whose person was since deleted
    (`ended_by` is SET_NULL, the note is theirs) — is Juristid's."""
    return (
        ended_by_id is None and submission_id is None and (note or "").startswith(SYNC_ENDING_NOTE)
    )


def _outcome_view(matter_id: Any) -> dict[str, Any] | None:
    """B for Lovable's opinion outcome: the Teema's current `LovableOpinionOutcome`
    as ``{"outcome", "date", "addressee"}``, or ``None``."""
    from app.legacy_import.lovable_sync_models import current_opinion_outcomes

    row = current_opinion_outcomes([matter_id]).get(matter_id)
    if row is None:
        return None
    return {
        "outcome": row.outcome,
        "date": row.sent_on.isoformat() if row.sent_on else None,
        "addressee": row.addressee_raw,
    }


def source_continuation(bundle: dict[str, Any]) -> list[str]:
    """The Lovable ids this topic continues as (`topic_stage_link`, this topic as
    ``prev``), sorted: the source's side of the continuation."""
    me = str((bundle.get("row") or {}).get("id"))
    return sorted(
        {
            str(link.get("next"))
            for link in bundle.get("stage_links") or []
            if str(link.get("prev")) == me
        }
    )


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


@dataclass
class FieldDecision:
    field: str
    outcome: str
    reason: str = ""
    baseline: Any = None
    current: Any = None
    source: Any = None
    #: What Juristid should hold for an ELIGIBLE or converged field.
    target: Any = None
    #: Unchanged in the source and already what Juristid holds, but never
    #: mirrored before: the baseline is settled (`_mirror`) without a decision
    #: row, so a later local edit reads as local (docs/adr/0213 §10).
    settle: bool = False


@dataclass
class MatterPlan:
    state: LovableSyncState
    status: str = ""  # SOURCE_MISSING / IDENTIFIER_CHANGED, or "" when compared
    source: dict[str, Any] = field(default_factory=dict)
    version: int | None = None
    decisions: list[FieldDecision] = field(default_factory=list)
    #: Written, but did not read back as written (`verify_matter`).
    verify_failed: bool = False
    #: Eligible, but not written: an earlier Teema's read-back failed this run.
    not_executed: bool = False
    #: The source's activity tonight (`source_activity`) and whether the Teema is
    #: open in Juristid: counted, never acted on (docs/adr/0202 §6).
    activity: str = ""
    open: bool = False

    def outcomes(self) -> set[str]:
        return {d.outcome for d in self.decisions} | ({self.status} if self.status else set())

    @property
    def eligible(self) -> list[FieldDecision]:
        return [d for d in self.decisions if d.outcome == SyncOutcome.ELIGIBLE]


def _three_way(
    name: str, a_src: Any, c_src: Any, a_tgt: Any, b: Any, c_tgt: Any, held: str = ""
) -> FieldDecision:
    """The table at the top of this module, for one field."""
    common = {"baseline": a_src, "current": b, "source": c_src}
    if c_src == a_src:
        outcome = SyncOutcome.UNCHANGED if b == a_tgt else SyncOutcome.LOCAL_TEST_CHANGE
        return FieldDecision(name, outcome, **common)
    if c_tgt is not None and b == c_tgt:
        # Juristid already says what the source now says — even for a field a
        # machine may not write: the baseline catches up and nothing is written.
        return FieldDecision(name, SyncOutcome.ALREADY_CONVERGED, target=c_tgt, **common)
    if held:
        return FieldDecision(name, SyncOutcome.REVIEW_REQUIRED, held, **common)
    if b == c_tgt:
        return FieldDecision(name, SyncOutcome.ALREADY_CONVERGED, target=c_tgt, **common)
    if b == a_tgt:
        return FieldDecision(name, SyncOutcome.ELIGIBLE, target=c_tgt, **common)
    return FieldDecision(name, SyncOutcome.CONFLICT, **common)


class OwnerResolver:
    """A Lovable owner name → exactly one active, assignable Juristid user.

    First the pairs the pilot's reviewed people map produced (A's source name and
    A's owner, across the cohort), then an exact display name. Two candidates, or
    one who may not hold current work, is no answer."""

    def __init__(self, states: Iterable[LovableSyncState]) -> None:
        pairs: dict[str, set[str]] = {}
        for state in states:
            owner = state.applied.get("owner") or {}
            if owner.get("resolved"):
                continue  # an operator's resolution says who kept the file, not who a name is
            if owner.get("import_held"):
                continue  # the import's reviewer did not vouch for this pairing (ADR 0202 §3)
            if owner.get("source") and owner.get("target") not in (None, "?"):
                pairs.setdefault(name_key(owner["source"]), set()).add(owner["target"])
        self.pairs = pairs

    def resolve(self, name: str) -> tuple[str | None, str]:
        from app.accounts.models import User
        from app.accounts.selectors import is_assignable_business_user

        if not name:
            return None, UNRESOLVED_OWNER
        known = self.pairs.get(name_key(name), set())
        candidates: list[Any]
        if len(known) == 1:
            candidates = list(User.objects.filter(pk__in=known, is_active=True))
        elif known:
            return None, UNRESOLVED_OWNER
        else:
            candidates = [
                user
                for user in User.objects.filter(is_active=True)
                if name_key(user.display_name) == name_key(name)
            ]
        if len(candidates) != 1:
            return None, UNRESOLVED_OWNER
        if not is_assignable_business_user(candidates[0]):
            return None, NOT_ASSIGNABLE
        return str(candidates[0].pk), ""


def _step_decisions(
    applied: dict[str, Any], src: dict[str, Any], b_step: dict[str, Any] | None, *, today: dt.date
) -> list[FieldDecision]:
    """`jargmiseks` and `tegevuse_tahtaeg` are one operation: a new day for an
    unchanged sentence may move, a new sentence never carries the old reading."""
    a_text = (applied.get("next_action_text") or {}).get("source")
    a_date = (applied.get("next_action_date") or {}).get("source")
    a_step = (applied.get("next_action") or {}).get("target")
    treatment = (applied.get("next_action") or {}).get("treatment") or ""
    c_text, c_date = src["next_action_text"], src["next_action_date"]
    b_view = {k: v for k, v in (b_step or {}).items() if k != "id"}

    out: list[FieldDecision] = []
    text_changed = c_text != a_text
    if text_changed:
        converged = (
            b_step is not None
            and text_value(b_step["text"]) == c_text
            and b_step["target_date"] == c_date
        )
        out.append(
            FieldDecision(
                "next_action_text",
                SyncOutcome.ALREADY_CONVERGED if converged else SyncOutcome.REVIEW_REQUIRED,
                "" if converged else TEXT_CHANGED,
                a_text,
                b_view or None,
                c_text,
                # The lawyer wrote the source's sentence: that step is now the
                # baseline, and its wording is the source's own.
                target=b_step if converged else None,
            )
        )
    else:
        local = (b_step or None) != a_step
        out.append(
            FieldDecision(
                "next_action_text",
                SyncOutcome.LOCAL_TEST_CHANGE if local else SyncOutcome.UNCHANGED,
                baseline=a_text,
                current=b_view or None,
                source=c_text,
            )
        )
    if c_date == a_date:
        local = (b_step or None) != a_step
        out.append(
            FieldDecision(
                "next_action_date",
                SyncOutcome.LOCAL_TEST_CHANGE if local else SyncOutcome.UNCHANGED,
                baseline=a_date,
                current=(b_step or {}).get("target_date"),
                source=c_date,
            )
        )
        return out
    common = {"baseline": a_date, "current": (b_step or {}).get("target_date"), "source": c_date}
    if text_changed:
        held = out[0].outcome == SyncOutcome.REVIEW_REQUIRED
        out.append(
            FieldDecision(
                "next_action_date",
                SyncOutcome.REVIEW_REQUIRED if held else SyncOutcome.ALREADY_CONVERGED,
                WITH_TEXT if held else "",
                **common,
            )
        )
        return out
    reason = ""
    if not a_step:
        reason = NO_STEP
    elif treatment != "SOURCE_NEXT_STEP":
        reason = NOT_SOURCE_STEP
    elif c_date is None:
        reason = DATE_CLEARED
    elif str(c_date).startswith("?"):
        reason = NO_BASELINE
    elif a_step.get("date_precision") != "EXACT":
        reason = NOT_EXACT
    elif dt.date.fromisoformat(c_date) < today:
        reason = DATE_PAST
    if (
        b_step
        and b_step.get("target_date") == c_date
        and {k: v for k, v in b_step.items() if k not in ("id", "target_date")}
        == {k: v for k, v in (a_step or {}).items() if k not in ("id", "target_date")}
    ):
        out.append(
            FieldDecision(
                "next_action_date", SyncOutcome.ALREADY_CONVERGED, target=b_step, **common
            )
        )
        return out
    if a_step and (b_step or None) != a_step:
        # The lawyer's step differs from what was applied: that is a conflict to
        # show, whatever else would also have held the change.
        out.append(FieldDecision("next_action_date", SyncOutcome.CONFLICT, **common))
        return out
    if reason or a_step is None:
        out.append(
            FieldDecision(
                "next_action_date", SyncOutcome.REVIEW_REQUIRED, reason or NO_STEP, **common
            )
        )
        return out
    target = {**a_step, "target_date": c_date}
    out.append(FieldDecision("next_action_date", SyncOutcome.ELIGIBLE, target=target, **common))
    return out


def plan_matter(
    state: LovableSyncState,
    bundle: dict[str, Any] | None,
    *,
    resolver: OwnerResolver,
    today: dt.date,
    current: dict[str, Any] | None = None,
) -> MatterPlan:
    plan = MatterPlan(state=state, activity=source_activity(bundle))
    if bundle is None:
        plan.status = SyncOutcome.SOURCE_MISSING
        plan.open = _is_open(state.matter)
        return plan
    row = bundle["row"]
    if f"{row.get('nr')}" != state.reference or str(row.get("id")) != str(state.source_id):
        plan.status = SyncOutcome.IDENTIFIER_CHANGED
        plan.open = _is_open(state.matter)
        return plan
    src = canonical_source(bundle)
    plan.source, plan.version = (
        {**src, CONTINUATION: source_continuation(bundle)},
        row.get("version"),
    )
    b = current if current is not None else current_values(state.matter)
    if "stage" not in b or "lifecycle" not in b:
        b = {**b, **conditional_values(state.matter)}
    closed = not b["is_open"]
    plan.open = not closed
    if getattr(state.matter, "deleted_at", None) is None:
        # Lovable is authoritative for the operational register (docs/adr/0213
        # §10, owner brief 2026-11-11): every source-owned field follows the
        # source through its domain service; only technical problems hold.
        _plan_authoritative(plan, state, plan.source, b, resolver=resolver, today=today)
        return plan
    # The closed historical register (`lovable-history`) and tombstones keep the
    # earlier rules exactly: a change there is a person's call (MATTER_CLOSED,
    # IMPORT_HELD; docs/adr/0213 §9.9).
    _plan_archive(plan, state, src, b, resolver=resolver, today=today)
    return plan


def _plan_archive(
    plan: MatterPlan,
    state: LovableSyncState,
    src: dict[str, Any],
    b: dict[str, Any],
    *,
    resolver: OwnerResolver,
    today: dt.date,
) -> None:
    """The rules before source authority (docs/adr/0192, 0213 §4), for the
    historical cohort and for tombstones only."""
    applied = state.applied
    closed = not b["is_open"]

    def a(name: str, part: str = "source") -> Any:
        return (applied.get(name) or {}).get(part)

    # The title: the source's sentence is the Teema's only when the review kept it.
    title_held = MATTER_CLOSED if closed else ""
    if not title_held and a("title", "target") != a("title"):
        title_held = TITLE_REVIEWED
    if not title_held and not src["title"]:
        title_held = TITLE_EMPTY
    plan.decisions.append(
        _three_way(
            "title",
            a("title"),
            src["title"],
            a("title", "target"),
            b["title"],
            src["title"],
            title_held,
        )
    )

    # The owner, only to a person who resolves exactly.
    owner_target, owner_held = (None, MATTER_CLOSED) if closed else (None, "")
    owner_baseline = a("owner", "target")
    if owner_baseline == "?":
        # The pilot's owner could not be named again from the audit: unchanged
        # stays unchanged, and any source change is a person's call.
        owner_baseline, owner_held = b["owner"], owner_held or NO_BASELINE
    if not owner_held and src["owner"] != a("owner"):
        owner_target, owner_held = resolver.resolve(src["owner"])
    plan.decisions.append(
        _three_way(
            "owner", a("owner"), src["owner"], owner_baseline, b["owner"], owner_target, owner_held
        )
    )

    # The current step.
    if closed:
        for name in ("next_action_text", "next_action_date"):
            plan.decisions.append(
                _three_way(name, a(name), src[name], None, None, None, MATTER_CLOSED)
            )
    else:
        plan.decisions.extend(_step_decisions(applied, src, b["next_action"], today=today))

    # The OneNote link: a new observation beside the old, https only.
    link_held = (
        ""
        if src["onenote_link"].startswith("https://") or not src["onenote_link"]
        else LINK_NOT_HTTPS
    )
    if not src["onenote_link"]:
        link_held = HELD  # a removed link is a person's call, never an erasure
    if closed:
        link_held = MATTER_CLOSED
    plan.decisions.append(
        _three_way(
            "onenote_link",
            a("onenote_link"),
            src["onenote_link"],
            a("onenote_link", "target"),
            b["onenote_link"],
            src["onenote_link"],
            link_held,
        )
    )

    for name in HELD_FIELDS:
        if name == "next_action_text":
            continue
        if name not in applied:
            continue  # started below, on first observation
        if name == "stage":
            plan.decisions.append(_stage_decision(applied, src["stage"], b, closed=closed))
        elif name == "opinion_deadline":
            plan.decisions.append(_request_decision(applied, src, b, closed=closed, today=today))
        elif name == "commencements":
            plan.decisions.append(
                _commencement_decision(applied, src, b, closed=closed, today=today)
            )
        else:
            plan.decisions.append(_three_way(name, a(name), src[name], None, None, None, HELD))
    # The continuation's baseline is the last accepted link, or — until one is
    # held — the last observed one: an unchanged link never touches `applied`.
    entry = applied.get(CONTINUATION)
    a_link = entry.get("source") if entry else (state.observed or {}).get(CONTINUATION)
    if entry or CONTINUATION in (state.observed or {}):
        link = _continuation_decision(a_link, plan.source[CONTINUATION], b)
        # Not one of `FIELDS`: an unchanged link adds no decision at all, so the
        # readers that tally decisions per field see only a moved one.
        if link.outcome != SyncOutcome.UNCHANGED:
            plan.decisions.append(link)
    _hold_imported(plan, applied)


def _stage_decision(
    applied: dict[str, Any], c_src: Any, b: dict[str, Any], *, closed: bool
) -> FieldDecision:
    """`hetkeseis` (CONDITIONAL, docs/adr/0213): only through the reader's exact
    `STAGE_MAPPING`, on an open Teema still at the stage last accepted, and never
    to a stage that ends the work — that would close the Teema (0131 §10), which
    is a person's decision (0202 §3)."""
    from app.legacy_import.lovable_reading import TERMINAL_STAGES, stage_for

    entry = applied.get("stage") or {}
    a_src = entry.get("source")
    a_tgt = entry["target"] if "target" in entry else (stage_for(a_src) or None)
    c_tgt = stage_for(c_src) or None
    b_stage = b.get("stage")
    common = {"baseline": a_src, "current": b_stage, "source": c_src}
    if c_src == a_src:
        # Unchanged in the source: whatever Juristid holds stays, and is not
        # reported (as for every held field). A Teema whose stage differs from
        # the mapped label is caught when the source does move: CONFLICT.
        return FieldDecision("stage", SyncOutcome.UNCHANGED, **common)
    if c_tgt is not None and b_stage == c_tgt:
        return FieldDecision("stage", SyncOutcome.ALREADY_CONVERGED, target=c_tgt, **common)
    reason = ""
    if closed:
        reason = MATTER_CLOSED
    elif c_tgt is None:
        # No mapping: no rule writes it (the field's own hold, as before).
        reason = HELD
    elif c_tgt in TERMINAL_STAGES:
        reason = STAGE_ENDS_WORK
    elif a_tgt is None:
        reason = NO_BASELINE
    if reason:
        return FieldDecision("stage", SyncOutcome.REVIEW_REQUIRED, reason, **common)
    if b_stage == a_tgt:
        return FieldDecision("stage", SyncOutcome.ELIGIBLE, target=c_tgt, **common)
    return FieldDecision("stage", SyncOutcome.CONFLICT, **common)


def _request_decision(
    applied: dict[str, Any],
    src: dict[str, Any],
    b: dict[str, Any],
    *,
    closed: bool,
    today: dt.date,
) -> FieldDecision:
    """`arvamuse_tahtaeg` (CONDITIONAL, docs/adr/0213): a new day becomes a **new**
    opinion request only where no request is open, the source records nothing
    sent (blank `valja` and `valja_kuupaev`; «ei saatnud» is not blank), it is
    not a Chamber initiative and the day is not before today. A current request
    is never moved or replaced here; earlier requests and their history are
    never touched. A past deadline never means sent."""
    entry = applied.get("opinion_deadline") or {}
    a_src = entry.get("source")
    # What Juristid was given for A: the request a run wrote or converged on, or
    # — at first sight — A's own day (an open request the import wrote) or none.
    a_tgt = entry["target"] if "target" in entry else a_src
    c_src = src["opinion_deadline"]
    b_day = b.get("opinion_deadline")
    common = {"baseline": a_src, "current": b_day, "source": c_src}
    if c_src == a_src:
        return FieldDecision("opinion_deadline", SyncOutcome.UNCHANGED, **common)
    if c_src is not None and b_day == c_src:
        return FieldDecision(
            "opinion_deadline", SyncOutcome.ALREADY_CONVERGED, target=c_src, **common
        )
    reason = ""
    if closed:
        reason = MATTER_CLOSED
    elif c_src is None:
        reason = REQUEST_CLEARED
    elif str(c_src).startswith("?"):
        reason = NO_BASELINE
    elif dt.date.fromisoformat(c_src) < today:
        reason = DATE_PAST
    elif src.get("sent") or src.get("sent_date"):
        reason = REQUEST_ANSWERED
    elif src.get("koda_initiative") or b.get("koda_initiative"):
        reason = REQUEST_INITIATIVE
    elif b_day != a_tgt and not (
        a_tgt is not None and b_day is None and not b.get("request_history") and _past(a_tgt, today)
    ):
        # Juristid no longer holds what it was given for A — a lawyer ended,
        # moved or added a request: a three-way conflict, never overwritten.
        return FieldDecision("opinion_deadline", SyncOutcome.CONFLICT, **common)
    elif b_day is not None:
        reason = REQUEST_OPEN
    if reason:
        return FieldDecision("opinion_deadline", SyncOutcome.REVIEW_REQUIRED, reason, **common)
    return FieldDecision("opinion_deadline", SyncOutcome.ELIGIBLE, target=c_src, **common)


def _past(day: Any, today: dt.date) -> bool:
    """A's day is history and Juristid has no ended request: the import wrote
    none for it (SOURCE_ONLY), so B empty is exactly what Juristid was given."""
    try:
        return dt.date.fromisoformat(str(day)) < today
    except ValueError:
        return False


def _commencement_decision(
    applied: dict[str, Any],
    src: dict[str, Any],
    b: dict[str, Any],
    *,
    closed: bool,
    today: dt.date,
) -> FieldDecision:
    """`joustumised` (CONDITIONAL, docs/adr/0213): only **added** commencements of
    a kind the reader reads exactly (`lovable_reading.commencement_reading`; an
    estimate stays an estimate, a known day only on an adopted act), never on a
    day Juristid already holds another commencement for. A changed or removed
    commencement is a person's call; nothing is edited or cancelled. An added
    day before today is history, never a fresh fact: held."""
    from app.legacy_import.lovable_reading import commencement_reading

    entry = applied.get("commencements") or {}
    a_src = entry.get("source") or []
    c_src = src["commencements"]
    have = b.get("commencements") or []
    common = {"baseline": a_src, "current": have, "source": c_src}
    if c_src == a_src:
        return FieldDecision("commencements", SyncOutcome.UNCHANGED, **common)
    before = Counter(tuple(item) for item in a_src)
    after = Counter(tuple(item) for item in c_src)
    removed, added = before - after, after - before
    readings = [
        {
            "liik": liik,
            "note": note,
            **commencement_reading({"liik": liik, "kuupaev": day}, status=src["stage"]),
        }
        for liik, day, note in added.elements()
    ]
    want = [[r["target"], r["kind"], r["date"]] for r in readings if r["target"]]
    if not removed and len(want) == len(readings) and all(w in have for w in want):
        return FieldDecision("commencements", SyncOutcome.ALREADY_CONVERGED, target=have, **common)
    reason = ""
    if closed:
        reason = MATTER_CLOSED
    elif removed:
        # Changed or removed: no rule writes it (the field's own hold, as
        # before this engine).
        reason = HELD
    elif len(want) != len(readings):
        # A kind or day the reader cannot read: no rule writes it.
        reason = HELD
    elif any(w[2] and dt.date.fromisoformat(w[2]) < today for w in want if w not in have):
        reason = DATE_PAST
    elif _commencements_moved(entry, a_src, have):
        # Juristid no longer holds what it was given for A (a lawyer cancelled
        # or changed one): adding beside it is a three-way conflict.
        return FieldDecision("commencements", SyncOutcome.CONFLICT, **common)
    else:
        missing = [w for w in want if w not in have]
        days = {item[2] for item in have if item[2]}
        general = any(item[1] == "GENERAL_ORDER" for item in have)
        if any((w[2] in days) if w[2] else general for w in missing):
            reason = COMMENCEMENT_CLASH
    if reason:
        return FieldDecision("commencements", SyncOutcome.REVIEW_REQUIRED, reason, **common)
    target = [
        {"target": r["target"], "kind": r["kind"], "date": r["date"], "note": r["note"]}
        for r in readings
        if [r["target"], r["kind"], r["date"]] not in have
    ]
    return FieldDecision("commencements", SyncOutcome.ELIGIBLE, target=target, **common)


def _commencements_moved(entry: dict[str, Any], a_src: list[Any], have: list[Any]) -> bool:
    """Whether B no longer holds what Juristid was given for A: the target a run
    stored, or else every readable commencement of A."""
    from app.legacy_import.lovable_reading import commencement_reading

    if "target" in entry:
        given = [list(item) for item in entry["target"] or []]
    else:
        given = []
        for liik, day, _note in a_src:
            reading = commencement_reading({"liik": liik, "kuupaev": day}, status="")
            if reading["target"]:
                given.append([reading["target"], reading["kind"], reading["date"]])
        # A fixed day reads as known once adopted: either kind is what was given.
        have = [
            ["MatterImportantDate", "PLANNED_COMMENCEMENT", item[2]]
            if item[1] == "KNOWN_DATE"
            else item
            for item in have
        ]
    return any(item not in have for item in given)


def _continuation_decision(a_src: Any, c_src: list[str], b: dict[str, Any]) -> FieldDecision:
    """Lovable's stage link (REVIEW, docs/adr/0213): the only service that writes
    a continuation closes the predecessor, which is a lifecycle decision, so a
    new, changed or removed link is reported every night and never written."""
    b_now = b.get(CONTINUATION) or []
    common: dict[str, Any] = {"baseline": a_src, "current": b_now, "source": c_src}
    if c_src == a_src:
        return FieldDecision(CONTINUATION, SyncOutcome.UNCHANGED, **common)
    if c_src and b_now == c_src:
        return FieldDecision(CONTINUATION, SyncOutcome.ALREADY_CONVERGED, target=c_src, **common)
    return FieldDecision(CONTINUATION, SyncOutcome.REVIEW_REQUIRED, CONTINUATION_CHANGED, **common)


def held_by_import(applied: dict[str, Any]) -> set[str]:
    """The fields still under the import's review hold. The step's wording and
    its day are one operation: if either is held, both are."""
    held = {name for name in FIELDS if (applied.get(name) or {}).get("import_held")}
    if held & set(STEP_FIELDS):
        held |= set(STEP_FIELDS)
    return held


def _hold_imported(plan: MatterPlan, applied: dict[str, Any]) -> None:
    """An import-held field is a person's call every night, whatever A, B and C
    say — never eligible, never applied, and never cleared by a convergence."""
    held = held_by_import(applied)
    if not held:
        return
    for decision in plan.decisions:
        if decision.field in held:
            decision.outcome = SyncOutcome.REVIEW_REQUIRED
            decision.reason = IMPORT_HELD
            decision.target = None


def _is_open(matter: Any) -> bool:
    return bool(matter.is_open) and getattr(matter, "deleted_at", None) is None


# ---------------------------------------------------------------------------
# Source authority (docs/adr/0213 §10, owner brief 2026-11-11)
# ---------------------------------------------------------------------------
#
# Lovable is the authoritative source for everything that originates in it.
# For the operational cohorts (the pilot, the operational rebuild and the
# register) every source-owned field follows the **latest verified** Lovable
# value through its canonical domain service, also where Juristid diverged
# locally (B ≠ A): a source change wins. What stays the same:
#
# * a source value that did not change modifies nothing (A = C → UNCHANGED, or
#   LOCAL_TEST_CHANGE when Juristid differs: the local value is kept until the
#   source next changes);
# * a field the engine has never mirrored (the import held it, or it was only
#   ever observed) converges once: Juristid is brought to what the source says
#   now, then it is settled (``mirrored``);
# * a value that cannot be represented without inventing something, an
#   identity that does not resolve, a contradictory reading or an unsafe
#   operation is held with its own code (`TECHNICAL_HOLDS`) — never a business
#   divergence.

#: Marks a baseline entry the source-authoritative engine wrote or converged.
MIRRORED = "mirrored"
#: The step treatment a mirrored step carries in the baseline.
MIRROR_TREATMENT = "LOVABLE_MIRROR"
#: Step treatments whose step already reads the source's sentence (a reviewed
#: package's reading, an operator's resolution, the mirror itself): settled.
SETTLED_STEP_TREATMENTS = frozenset({"SOURCE_NEXT_STEP", "RESOLVED_LOCAL", MIRROR_TREATMENT})

#: The technical holds (codes; the pages' words are `LOVABLE_REASONS`).
SUCCESSOR_MISSING = "SUCCESSOR_NOT_IN_JURISTID"
SUCCESSOR_AMBIGUOUS = "SUCCESSOR_AMBIGUOUS"
#: The opinion outcome is held (contradictory or impossible): the request waits too.
OUTCOME_HELD = "OUTCOME_HELD"
#: A write that brings Lovable's unchanged value back over a local edit
#: (owner decision 11.11: Lovable is the source of truth at all times).
RESTORED = "RESTORED_SOURCE_VALUE"
#: A historical Teema Lovable reopened outside its operational scope: no
#: register membership to move it to, nothing changed.
HISTORY_REOPENED_OUTSIDE_SCOPE = "HISTORY_REOPENED_OUTSIDE_SCOPE"
#: Lovable calls an open Teema finished in words that state no reason
#: («lõpetatud»): closing it would invent one.
CLOSURE_REASON_UNKNOWN = "CLOSURE_REASON_UNKNOWN"
#: A request a person ended in Juristid (answered or declined) while Lovable's
#: `valja` is still blank: never a second consultation; Lovable has not recorded
#: the sending yet.
ANSWERED_IN_JURISTID = "ANSWERED_IN_JURISTID_NOT_IN_LOVABLE"
#: More Teemad to restore tonight than `MAX_RESTORED_MATTERS`: the restores wait,
#: the source changes are written (run REVIEW_ONLY with this code).
RESTORE_BREAKER = "RESTORE_BREAKER"
#: A historical Teema's owner name resolves to no active Juristid user
#: (former staff): shown in the historical register's own group.
HISTORY_OWNER_UNRESOLVED = "HISTORY_OWNER_UNRESOLVED"
#: «lõpetatud» — finished, no reason stated (docs/adr/0213 §9.1).
FINISHED_NO_REASON = "lõpetatud"
STAGE_UNMAPPED = "STAGE_UNMAPPED"
CLOSED_OTHERWISE = "CLOSED_WITH_OTHER_REASON"
LIFECYCLE_HELD = "LIFECYCLE_HELD"
STEP_DATE_NO_TEXT = "STEP_DATE_WITHOUT_TEXT"
DATE_UNREADABLE = "DATE_UNREADABLE"
SENT_CONTRADICTORY = "SENT_CONTRADICTORY"
SENT_UNREADABLE = "SENT_VALUE_UNREADABLE"
SENT_FUTURE = "SENT_DATE_IN_FUTURE"
ADDRESSEE_NO_SEND = "ADDRESSEE_WITHOUT_SEND"
TRACK_NOT_STATED = "TRACK_NOT_STATED"
INSTRUMENT_UNMAPPED = "INSTRUMENT_UNMAPPED"
INSTRUMENT_SEVERAL = "INSTRUMENT_SEVERAL"
INSTRUMENT_CLEARED = "INSTRUMENT_CLEARED"
RECEIVED_IN_FUTURE = "RECEIVED_IN_FUTURE"
RECEIVED_ON_INITIATIVE = "RECEIVED_ON_INITIATIVE"
SCOPE_NOT_REPRESENTABLE = "SCOPE_NOT_REPRESENTABLE"
COMMENCEMENT_REMOVED = "COMMENCEMENT_CHANGED_OR_REMOVED"
COMMENCEMENT_UNREADABLE = "COMMENCEMENT_UNREADABLE"
LINK_REMOVED = "LINK_REMOVED_KEPT"
#: Every code the source-authoritative engine holds with, by the brief's kinds
#: of genuine technical problem.
TECHNICAL_HOLDS: dict[str, str] = {
    UNRESOLVED_OWNER: "unresolvable identity",
    NOT_ASSIGNABLE: "unresolvable identity",
    SUCCESSOR_MISSING: "missing successor identity",
    SUCCESSOR_AMBIGUOUS: "duplicate reference",
    OUTCOME_HELD: "contradictory snapshot",
    HISTORY_REOPENED_OUTSIDE_SCOPE: "unrepresentable value",
    CLOSURE_REASON_UNKNOWN: "unrepresentable value",
    HISTORY_OWNER_UNRESOLVED: "unresolvable identity",
    ANSWERED_IN_JURISTID: "unsafe operation",
    TITLE_EMPTY: "unrepresentable value",
    STAGE_UNMAPPED: "unrepresentable value",
    STEP_DATE_NO_TEXT: "unrepresentable value",
    DATE_UNREADABLE: "invalid read",
    SENT_CONTRADICTORY: "contradictory snapshot",
    SENT_UNREADABLE: "unrepresentable value",
    SENT_FUTURE: "invalid read",
    ADDRESSEE_NO_SEND: "unrepresentable value",
    TRACK_NOT_STATED: "unrepresentable value",
    INSTRUMENT_UNMAPPED: "unrepresentable value",
    INSTRUMENT_SEVERAL: "unrepresentable value",
    INSTRUMENT_CLEARED: "unrepresentable value",
    RECEIVED_IN_FUTURE: "invalid read",
    RECEIVED_ON_INITIATIVE: "unsafe operation",
    SCOPE_NOT_REPRESENTABLE: "unrepresentable value",
    COMMENCEMENT_REMOVED: "unsafe operation",
    COMMENCEMENT_UNREADABLE: "unrepresentable value",
    COMMENCEMENT_CLASH: "duplicate reference",
    LINK_NOT_HTTPS: "unsafe operation",
    LINK_REMOVED: "unsafe operation",
    CLOSED_OTHERWISE: "unsafe operation",
    LIFECYCLE_HELD: "unsafe operation",
    MATTER_CLOSED: "unsafe operation",
}


def _entry(applied: dict[str, Any], name: str) -> dict[str, Any]:
    return applied.get(name) or {}


def _restore_reason(unchanged: bool, entry: dict[str, Any], matches: Callable[[Any], bool]) -> str:
    """`RESTORED` only when the decision's whole input is unchanged: Lovable's
    value is (``unchanged``) *and* what the rule derives from it tonight is what
    it derived last time (``matches(entry["target"])``). A target that moved
    because another Lovable field changed (the sent status, the stage) or
    because time passed (a drafting step's day) is a source or derived change,
    counted as one, never a restore. A baseline that holds no target yet keeps
    the plain rule."""
    if not unchanged:
        return ""
    if "target" not in entry:
        return RESTORED
    return RESTORED if matches(entry["target"]) else ""


def _settled(applied: dict[str, Any], name: str) -> bool:
    """Whether the field's baseline is something Juristid was given: not under
    the import's hold. (`_step_settled` and the opinion outcome ask more.)"""
    return not _entry(applied, name).get("import_held")


def _decision(name: str, outcome: str, reason: str = "", **values: Any) -> FieldDecision:
    return FieldDecision(name, outcome, reason, **values)


def _scalar(
    name: str,
    *,
    applied: dict[str, Any],
    c_src: Any,
    b: Any,
    c_tgt: Any,
    hold: str = "",
    blank_converges: bool = False,
) -> FieldDecision:
    """One source-authoritative scalar field. Juristid must hold C's target after
    the night (owner decision 11.11: Lovable is the source of truth at all
    times):

    * B already is it → nothing to write (unchanged, or converged);
    * the source changed → written (ELIGIBLE);
    * the source did not change but Juristid was edited → written back
      (ELIGIBLE, reason `RESTORED`);
    * C's target cannot be determined (a technical hold) → held, and a local
      value is never «restored over» a hold."""
    entry = _entry(applied, name)
    a_src, a_tgt = entry.get("source"), entry.get("target")
    unchanged = c_src == a_src and _settled(applied, name)
    common = {"baseline": a_src, "current": b, "source": c_src}
    if not hold and (c_tgt is not None or blank_converges) and b == c_tgt:
        if unchanged:
            decision = _decision(name, SyncOutcome.UNCHANGED, target=c_tgt, **common)
            decision.settle = a_tgt != c_tgt
            return decision
        return _decision(name, SyncOutcome.ALREADY_CONVERGED, target=c_tgt, **common)
    if hold:
        known_differs = c_tgt is not None and b != c_tgt
        if unchanged and (b == a_tgt or ("target" not in entry and not known_differs)):
            return _decision(name, SyncOutcome.UNCHANGED, **common)
        if c_tgt is not None and b == c_tgt:
            return _decision(name, SyncOutcome.ALREADY_CONVERGED, target=c_tgt, **common)
        return _decision(name, SyncOutcome.REVIEW_REQUIRED, hold, **common)
    return _decision(
        name, SyncOutcome.ELIGIBLE, RESTORED if unchanged else "", target=c_tgt, **common
    )


def _successor_matter(source_id: str) -> str | None:
    """The live Teema that follows a Lovable id (its synchronisation state), or ``None``."""
    found = (
        LovableSyncState.objects.filter(source_id=source_id, matter__deleted_at__isnull=True)
        .values_list("matter_id", flat=True)
        .first()
    )
    return str(found) if found else None


def desired_lifecycle(
    stage_label: Any, continuation: list[str], *, matter_id: Any
) -> tuple[dict[str, Any] | None, str]:
    """What Juristid's lifecycle must be for Lovable's `hetkeseis` and stage link,
    or ``(None, hold)``. The rule, in order, and nothing beyond it:

    1. a structured stage link naming exactly one successor (this topic as
       ``prev``) → closed, SUPERSEDED, continuing under that successor's Teema,
       at the mapped stage if any. A successor not (yet) in Juristid holds
       (`SUCCESSOR_MISSING`) until it is; two successors hold
       (`SUCCESSOR_AMBIGUOUS`).
    2. a stage that ends the work («jõustunud», «rohkem ei tegele») → closed at
       that stage with its own reason (`TERMINAL_DISPOSITIONS`: COMPLETED «Jõustus
       või töö lõppes», MONITORING_STOPPED «Koda ei tegele edasi»).
    3. Lovable's own disposition-only words (`CLOSURE_BY_STATUS`, «rohkem pole
       tegevusi plaanis») → closed MONITORING_STOPPED, stage kept.
    4. «lõpetatud» → closed, no reason stated: any closure is it (disposition
       ``None``); an open Teema cannot be closed without inventing a reason.
    5. any other mapped stage → open at it.
    6. anything else (an unmapped label, a blank one) → `STAGE_UNMAPPED`.

    No more specific reason than the source states is ever recorded."""
    from app.legacy_import.lovable_reading import CLOSURE_BY_STATUS, TERMINAL_STAGES, stage_for
    from app.workflow.stage_flow import TERMINAL_DISPOSITIONS

    label = text_value(stage_label)
    mapped = stage_for(label) or None
    if continuation:
        if len(continuation) != 1:
            return None, SUCCESSOR_AMBIGUOUS
        successor = _successor_matter(continuation[0])
        if successor is None:
            return None, SUCCESSOR_MISSING
        if successor == str(matter_id):
            return None, SUCCESSOR_AMBIGUOUS
        return {
            "open": False,
            "stage": mapped,
            "disposition": "SUPERSEDED",
            "successor": successor,
        }, ""
    if mapped in TERMINAL_STAGES:
        return {
            "open": False,
            "stage": mapped,
            "disposition": TERMINAL_DISPOSITIONS[mapped],
            "successor": None,
        }, ""
    stated = CLOSURE_BY_STATUS.get(label.casefold())
    if stated is not None:
        return {"open": False, "stage": None, "disposition": stated[1], "successor": None}, ""
    if label.casefold() == FINISHED_NO_REASON:
        return {"open": False, "stage": None, "disposition": None, "successor": None}, ""
    if mapped:
        return {"open": True, "stage": mapped, "disposition": "", "successor": None}, ""
    return None, STAGE_UNMAPPED


def _lifecycle_matches(desired: dict[str, Any], b_life: dict[str, Any]) -> bool:
    """Whether Juristid's lifecycle already is the desired one. A closed Teema's
    stage is not compared: it moves only with a reopening, and a closure the
    source does not tell apart («lõpetatud», disposition ``None``) is any."""
    if desired["open"]:
        return bool(b_life["open"]) and b_life["stage"] == desired["stage"]
    if b_life["open"]:
        return False
    if desired["disposition"] is None:
        return True
    return (
        b_life["disposition"] == desired["disposition"]
        and b_life["successor"] == desired["successor"]
    )


def _lifecycle_decisions(
    state: LovableSyncState, src: dict[str, Any], b: dict[str, Any]
) -> tuple[list[FieldDecision], dict[str, Any] | None, str]:
    """The stage and the continuation as one lifecycle operation. Returns the
    decisions (``stage``, and ``continuation`` when the link moved), the desired
    lifecycle (``None`` when held) and the hold code.

    Lovable is the source of truth at all times (owner decision 11.11): a
    lifecycle a person changed locally is put back — a Teema closed while
    Lovable has it open is reopened, one reopened while Lovable has it closed
    is closed again (reason `RESTORED`). A Teema closed for another reason than
    Lovable states is never reopened and closed again: changed in Lovable it is
    held (`CLOSED_OTHERWISE`), unchanged it stays as closed. A historical Teema
    Lovable reopens moves to the register (`move_to`) only inside Lovable's
    operational scope; outside it, it is held."""
    applied = state.applied
    entry = _entry(applied, "stage")
    a_stage = entry.get("source")
    c_stage = src["stage"]
    link = applied.get(CONTINUATION)
    c_link = src[CONTINUATION]
    b_life = b["lifecycle"]
    # Never observed: on an open Teema no link was known, so a link seen now is a
    # change (Lovable says the work goes on elsewhere); a closed Teema's link
    # is taken as it stands — its closure was the import's reading of it.
    unknown = [] if b_life["open"] else c_link
    a_link = link.get("source") if link else (state.observed or {}).get(CONTINUATION, unknown)
    desired, hold = desired_lifecycle(c_stage, c_link, matter_id=state.matter_id)
    changed = c_stage != a_stage or (a_link or []) != c_link
    unchanged = not changed and _settled(applied, "stage")
    common = {"baseline": a_stage, "current": b_life, "source": c_stage}
    link_common = {"baseline": a_link, "current": b.get(CONTINUATION) or [], "source": c_link}
    link_moved = (a_link or []) != c_link

    def decide(outcome: str, reason: str = "", target: Any = None) -> list[FieldDecision]:
        found = [_decision("stage", outcome, reason, target=target, **common)]
        if link_moved:
            found.append(_decision(CONTINUATION, outcome, reason, target=target, **link_common))
        return found

    if hold or desired is None:
        if unchanged:
            # A technical hold is never «restored over».
            return [_decision("stage", SyncOutcome.UNCHANGED, **common)], None, ""
        hold = hold or STAGE_UNMAPPED
        return decide(SyncOutcome.REVIEW_REQUIRED, hold), None, hold
    if _lifecycle_matches(desired, b_life):
        if unchanged:
            quiet = [_decision("stage", SyncOutcome.UNCHANGED, target=dict(b_life), **common)]
            quiet[0].settle = MIRRORED not in entry
            return quiet, desired, ""
        return decide(SyncOutcome.ALREADY_CONVERGED, target=dict(b_life)), desired, ""
    restored = _restore_reason(
        unchanged,
        entry,
        lambda before: isinstance(before, dict) and _lifecycle_matches(desired, before),
    )
    target = dict(desired)
    if not desired["open"] and not b_life["open"]:
        # Closed in both, for another reason or under another successor:
        # Lovable's reason wins, written as a closure-reason change (never a
        # reopening and a new closure; the closing moment stays).
        target["reclose"] = True
        return decide(SyncOutcome.ELIGIBLE, restored, target), desired, ""
    if not desired["open"] and desired["disposition"] is None:
        # «lõpetatud» on an open Teema: closing it would invent a reason.
        return decide(SyncOutcome.REVIEW_REQUIRED, CLOSURE_REASON_UNKNOWN), None, ""
    if desired["open"] and not b_life["open"] and state.cohort == HISTORY_COHORT:
        # The backfill closed every non-operational topic as ARCHIVE whatever its
        # label said: only a change in Lovable reopens one — its label moving
        # from a closing one to an open one, or its scope becoming operational.
        a_desired, _a_hold = desired_lifecycle(a_stage, a_link or [], matter_id=state.matter_id)
        a_scope = _entry(applied, "operational_scope").get("source")
        relabelled = c_stage != a_stage and not (a_desired is not None and a_desired["open"])
        into_scope = bool(src.get("operational_scope")) and not a_scope
        if not (relabelled or into_scope):
            quiet = [_decision("stage", SyncOutcome.UNCHANGED, target=dict(b_life), **common)]
            quiet[0].settle = True
            return quiet, None, ""
        if not src.get("operational_scope"):
            hold = HISTORY_REOPENED_OUTSIDE_SCOPE
            return decide(SyncOutcome.REVIEW_REQUIRED, hold), None, ""
        target["move_to"] = NEW_TOPIC_COHORT
    return decide(SyncOutcome.ELIGIBLE, restored, target), desired, ""


def _step_view(step: dict[str, Any] | None) -> dict[str, Any] | None:
    """A step as compared with Lovable's: its exact sentence (whitespace
    collapsed), kind, date meaning, day and precision — never its id."""
    if not step:
        return None
    return {
        "text": text_value(step.get("text")),
        "kind": step.get("kind"),
        "date_semantics": step.get("date_semantics"),
        "target_date": step.get("target_date"),
        "date_precision": step.get("date_precision"),
    }


#: The beginning every drafting sentence shares (`OPINION_PREPARATION_TEXT`).
DRAFTING_WORDS = "koostan arvamus"


def _step_decisions_authoritative(
    applied: dict[str, Any],
    src: dict[str, Any],
    b_step: dict[str, Any] | None,
    *,
    closed_after: bool,
    lifecycle_hold: str,
    request_day: str | None = None,
    request_before: str | None = None,
    outcome: dict[str, Any] | None = None,
    outcome_hold: str = "",
    today: dt.date | None = None,
) -> list[FieldDecision]:
    """`jargmiseks` + `tegevuse_tahtaeg` → the current step, one operation, a pure
    function of Lovable's current values (owner decision 11.11), re-derived on
    every run:

    * a closed Teema (or one closing tonight) holds no current step: the
      closure ends it (cancelled, never completed), and Lovable's sentence
      settles as «no step»;
    * a sentence → `lovable_reading.step_reading`: the exact sentence, the kind
      `lovable-step/1` reads from it (or `ActionKind.SOURCE`), Lovable's day; a
      DO with no day of its own takes the request's day after tonight;
    * no sentence → the structured drafting step of an open request with
      nothing sent (DO «Koostan arvamuse» on the request's day, the reader's
      own rule, docs/adr/0148 §10), else no step;
    * a day with no sentence cannot be a step: held.

    Whatever Juristid holds that differs — a new sentence, a day, a kind an
    earlier import gave, a local edit — is replaced through `set_next_action`
    (the old step superseded and kept, never completed); unchanged in Lovable it
    is a restore (reason `RESTORED`)."""
    from app.legacy_import.lovable_reading import step_reading
    from app.workflow.services import OPINION_PREPARATION_TEXT

    text_entry = _entry(applied, "next_action_text")
    date_entry = _entry(applied, "next_action_date")
    step_entry = _entry(applied, "next_action")
    a_text, a_date = text_entry.get("source"), date_entry.get("source")
    a_step = step_entry.get("target")
    held = text_entry.get("import_held") or date_entry.get("import_held")
    settled = not held and (step_entry.get("treatment") or "") in SETTLED_STEP_TREATMENTS
    c_text, c_date = src["next_action_text"], src["next_action_date"]
    b_view = {k: v for k, v in (b_step or {}).items() if k != "id"} or None
    unchanged = (c_text, c_date) == (a_text, a_date) and settled

    def both(outcome: str, reason: str = "", target: Any = None) -> list[FieldDecision]:
        return [
            _decision(
                "next_action_text",
                outcome,
                reason,
                baseline=a_text,
                current=b_view,
                source=c_text,
                target=target,
            ),
            _decision(
                "next_action_date",
                outcome,
                reason,
                baseline=a_date,
                current=(b_step or {}).get("target_date"),
                source=c_date,
                target=target,
            ),
        ]

    def same(target: Any) -> list[FieldDecision]:
        if unchanged:
            quiet = both(SyncOutcome.UNCHANGED, target=target)
            for decision in quiet:
                decision.settle = _step_view(a_step) != _step_view(target) or (
                    (a_step or {}).get("id") != (target or {}).get("id")
                )
            return quiet
        return both(SyncOutcome.ALREADY_CONVERGED, target=target)

    if lifecycle_hold:
        return same(b_step) if unchanged else both(SyncOutcome.REVIEW_REQUIRED, LIFECYCLE_HELD)
    desired: dict[str, Any] | None
    if closed_after:
        desired = None
    elif not c_text:
        if c_date:
            if unchanged:
                return same(b_step)
            return both(SyncOutcome.REVIEW_REQUIRED, STEP_DATE_NO_TEXT)
        if outcome_hold and request_day is not None:
            # Whether anything was sent is held: no drafting step is invented
            # and none is ended on that word either.
            if unchanged:
                return same(b_step)
            return both(SyncOutcome.REVIEW_REQUIRED, OUTCOME_HELD)
        drafting = (
            request_day is not None
            and not src.get("sent")
            and not src.get("sent_date")
            and not src.get("koda_initiative")
            and (today is None or dt.date.fromisoformat(request_day) >= today)
        )
        if drafting:
            desired = {
                "text": OPINION_PREPARATION_TEXT,
                "kind": "DO",
                "date_semantics": "DEADLINE",
                "target_date": request_day,
                "date_precision": "EXACT",
            }
        else:
            desired = None
    else:
        if c_date is not None and str(c_date).startswith("?"):
            if unchanged:
                return same(b_step)
            return both(SyncOutcome.REVIEW_REQUIRED, DATE_UNREADABLE)
        desired = step_reading(c_text, c_date) or {}
        if desired.get("kind") == "DO" and desired.get("target_date") is None and request_day:
            # A drafting sentence with no action day is due on the request's
            # day after tonight (docs/adr/0148 §10, `lovable_reading.read_topic`).
            desired["target_date"] = request_day
    if desired is None:
        if b_step is None:
            return same(None)
        if closed_after:
            # The closure ends it: never a restore of its own.
            return both(SyncOutcome.ELIGIBLE, target={"end": True})
    elif _step_view(b_step) == _step_view(desired):
        return same(b_step)
    # A restore only when the step the rule derives tonight is the one it
    # derived last time: a drafting step ending because its day passed, or a
    # kind re-derived, is a derived change.
    restored = _restore_reason(
        unchanged, step_entry, lambda before: _step_view(before) == _step_view(desired)
    )
    if desired is None:
        return both(SyncOutcome.ELIGIBLE, restored, target={"end": True})
    return both(SyncOutcome.ELIGIBLE, restored, target=desired)


def desired_outcome(src: dict[str, Any], *, today: dt.date) -> tuple[dict[str, Any] | None, str]:
    """`valja` / `valja_kuupaev` → Lovable's opinion outcome, or a hold.

    * «ei saatnud» → NOT_SENT; with a sent day beside it → contradictory.
    * a day in `valja_kuupaev`, or `valja` itself a day → SENT on it; two
      different days → contradictory; a day after today → an invalid read.
    * any other wording in `valja` → unrepresentable (no day, no «ei saatnud»).
    * both blank → nothing sent (``None``): blank is never sent, and a passed
      deadline is never sent."""
    from app.legacy_import.lovable_reading import NOT_SENT, _wording

    valja, day = src["sent"], src["sent_date"]
    words = _wording(valja)
    valja_day = (
        day_value(valja) if valja and re.fullmatch(r"\d{4}-\d{2}-\d{2}", valja[:10]) else None
    )
    if not valja_day and valja:
        found = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})\.?", valja)
        if found:
            try:
                valja_day = dt.date(
                    int(found.group(3)), int(found.group(2)), int(found.group(1))
                ).isoformat()
            except ValueError:
                valja_day = None
    if words == NOT_SENT:
        if day:
            return None, SENT_CONTRADICTORY
        return {"outcome": "NOT_SENT", "date": None, "addressee": src["addressee"]}, ""
    if day or valja_day:
        if day and str(day).startswith("?"):
            return None, DATE_UNREADABLE
        if day and valja_day and day != valja_day:
            return None, SENT_CONTRADICTORY
        when = day or valja_day
        if dt.date.fromisoformat(str(when)) > today:
            return None, SENT_FUTURE
        return {"outcome": "SENT", "date": when, "addressee": src["addressee"]}, ""
    if valja:
        return None, SENT_UNREADABLE
    return None, ""


def _outcome_decisions(
    applied: dict[str, Any], src: dict[str, Any], b: dict[str, Any], *, today: dt.date
) -> tuple[list[FieldDecision], dict[str, Any] | None]:
    """`valja`, `valja_kuupaev` and `kellele` as one operation: the Teema's
    current `LovableOpinionOutcome`. Never mirrored before → converges once."""
    names = ("sent", "sent_date", "addressee")
    a = [_entry(applied, n).get("source") for n in names]
    c = [src[n] for n in names]
    b_out = b.get("opinion_outcome")
    settled = bool(_entry(applied, "sent").get(MIRRORED)) and all(
        _settled(applied, n) for n in names
    )
    desired, hold = desired_outcome(src, today=today)

    def each(outcome: str, reason: str = "", target: Any = None) -> list[FieldDecision]:
        return [
            _decision(n, outcome, reason, baseline=a[i], current=b_out, source=c[i], target=target)
            for i, n in enumerate(names)
        ]

    if a == c and (settled or (desired is None and not hold and b_out is None)):
        # Unchanged; or never mirrored but nothing to say (nothing sent, nothing
        # recorded): no decision, and nothing to converge — only settled.
        quiet = each(SyncOutcome.UNCHANGED)
        for decision in quiet:
            # Only the import's hold is lifted; nothing else needs a baseline here
            # (no person writes a `LovableOpinionOutcome`).
            decision.settle = bool(_entry(applied, decision.field).get("import_held"))
        return quiet, desired
    if hold:
        return each(SyncOutcome.REVIEW_REQUIRED, hold), None
    if desired is None:
        if b_out is None:
            out = each(SyncOutcome.ALREADY_CONVERGED, target=None)
        else:
            out = each(SyncOutcome.ELIGIBLE, target={"outcome": "CLEARED"})
        if c[2] != a[2] and c[2]:
            # An addressee with nothing sent has no record to live on.
            out[2] = _decision(
                "addressee",
                SyncOutcome.REVIEW_REQUIRED,
                ADDRESSEE_NO_SEND,
                baseline=a[2],
                current=b_out,
                source=c[2],
            )
        return out, None
    if b_out == desired:
        return each(SyncOutcome.ALREADY_CONVERGED, target=desired), desired
    return each(SyncOutcome.ELIGIBLE, target=desired), desired


def _request_decision_authoritative(
    applied: dict[str, Any],
    src: dict[str, Any],
    b: dict[str, Any],
    *,
    closed_after: bool,
    lifecycle_hold: str,
    outcome: dict[str, Any] | None,
    today: dt.date,
    outcome_hold: str = "",
) -> FieldDecision:
    """`arvamuse_tahtaeg` → the current `Arvamuse tähtaeg`, never a second
    consultation (`app.matters.response_deadlines`). After the night:

    * Lovable has no day → no current request (the current one is withdrawn,
      CANCELLED, and stays in the history);
    * a current request → moved to Lovable's day (the same request, MOVED);
    * no current request, nothing sent or «ei saatnud», the day not passed →
      one is set (on a Chamber initiative too: its own deadline);
    * no current request otherwise → nothing is owed now (a passed day is never
      an obligation, an answered one is answered).

    Lovable unchanged but Juristid edited → the same rule puts it back (reason
    `RESTORED`). A closed Teema owes nothing current; a held lifecycle or
    outcome holds the request too."""
    name = "opinion_deadline"
    entry = _entry(applied, name)
    a_src = entry.get("source")
    c_src = src[name]
    b_day = b.get(name)
    unchanged = c_src == a_src and _settled(applied, name)
    # The request's whole input: its day and whether Lovable says anything went out.
    inputs_same = unchanged and all(
        _j(src.get(key)) == _j(_entry(applied, key).get("source")) for key in ("sent", "sent_date")
    )
    common = {"baseline": a_src, "current": b_day, "source": c_src}

    def same(target: Any) -> FieldDecision:
        if unchanged:
            return _decision(name, SyncOutcome.UNCHANGED, target=target, **common)
        return _decision(name, SyncOutcome.ALREADY_CONVERGED, target=target, **common)

    if closed_after:
        return same(b_day)
    if lifecycle_hold:
        if unchanged:
            return same(b_day)
        return _decision(name, SyncOutcome.REVIEW_REQUIRED, LIFECYCLE_HELD, **common)
    if c_src is not None and str(c_src).startswith("?"):
        if unchanged:
            return same(b_day)
        return _decision(name, SyncOutcome.REVIEW_REQUIRED, DATE_UNREADABLE, **common)
    if outcome_hold and b_day != c_src:
        # Whether anything was sent is held (contradictory or impossible):
        # neither «nothing sent» nor «sent» — no request is set, moved or ended.
        if unchanged:
            return same(b_day)
        return _decision(name, SyncOutcome.REVIEW_REQUIRED, OUTCOME_HELD, **common)
    if c_src is not None and outcome is not None:
        # Lovable says the opinion went out, or was declined: nothing is owed.
        # A current request (one a person set or moved since) ends as Lovable
        # says, through the deadline service — never moved onto an answered day.
        if b_day is None:
            return same(None)
        target_end = {"resolve": outcome["outcome"], "date": outcome.get("date"), "day": c_src}
        if REQUESTED_AT in entry:
            # Whose request is current: Lovable's (the one the sync set or saw,
            # moved or not) or one recorded since (a fresh or REPLACED request).
            target_end[REQUESTED_AT] = entry[REQUESTED_AT]
        restored = _restore_reason(inputs_same, entry, lambda before: before is None)
        return _decision(name, SyncOutcome.ELIGIBLE, restored, target=target_end, **common)
    if c_src is None:
        target = None
    elif b_day is not None:
        target = c_src
    elif dt.date.fromisoformat(c_src) >= today:
        last = b.get("request_last_ended") or {}
        juristid_end = bool(last) and not last.get("by_sync")
        identity = entry.get(REQUESTED_AT)
        if (
            # Lovable's own request, its day unchanged in Lovable since, answered
            # or declined in Juristid (a day Lovable changed is a new request).
            identity is not None
            and c_src == a_src
            and identity in (last.get("juristid_answered") or [])
        ) or (
            juristid_end
            and last.get("outcome") in ("ANSWERED", "NOT_ANSWERING")
            and c_src in (last.get("days") or [])
        ):
            # Juristid answered or declined this very request — any day of its
            # chain is Lovable's day — while Lovable's `valja` is still blank:
            # never a second consultation; held until Lovable records it. One
            # the sync ended (Lovable then blanked `valja`: Lovable wins), or
            # one for another day (a new request), is set on Lovable's day.
            # Lovable's own request (`REQUESTED_AT`) answered in Juristid holds
            # even when Juristid asked and answered another one since.
            return _decision(name, SyncOutcome.REVIEW_REQUIRED, ANSWERED_IN_JURISTID, **common)
        target = c_src
        if (
            juristid_end
            and last.get("outcome") in ("ANSWERED", "NOT_ANSWERING")
            and not inputs_same
        ):
            # After Juristid ended its request, Lovable stating another day is a
            # new request: a source change. Lovable unchanged falls through to
            # the restore rule (and its breaker).
            return _decision(name, SyncOutcome.ELIGIBLE, target=target, **common)
    else:
        return same(None)  # nothing is owed now
    if b_day == target:
        return same(target)
    restored = _restore_reason(inputs_same, entry, lambda before: before == target)
    return _decision(name, SyncOutcome.ELIGIBLE, restored, target=target, **common)


def _commencements_authoritative(
    applied: dict[str, Any], src: dict[str, Any], b: dict[str, Any], *, closed: bool
) -> FieldDecision:
    """`joustumised` → the Teema's commencements, as one set (owner decision
    11.11). Each Lovable row is read exactly (`lovable_reading.
    commencement_reading`: «Üldises korras», «Kindla kuupäevaga» — known once
    adopted —, «Eeldatava …» which stays an estimate) with its scope note
    (`tapsustus`). Juristid's commencements are compared by (kind, day):

    * one Lovable no longer states — an estimate that became fixed, general
      order that got a day, a moved or removed day — is cancelled through
      `cancel_effective_date` / `cancel_important_date`: its own audited event,
      the record kept as it was;
    * one Lovable states and Juristid lacks is added with its note.

    Unchanged in Lovable but different in Juristid → the same reconciliation
    (reason `RESTORED`). Held only where a row cannot be represented (an
    unreadable kind or day) or the Teema is closed (the date services refuse a
    closed file); unchanged, either stays as it is."""
    from app.legacy_import.lovable_reading import commencement_reading

    entry = _entry(applied, "commencements")
    a_src = entry.get("source") or []
    c_src = src["commencements"]
    have = b.get("lovable_commencements") or []
    unchanged = c_src == a_src
    common = {"baseline": a_src, "current": have, "source": c_src}
    readings = []
    seen: set[tuple[Any, ...]] = set()
    for liik, day, note in c_src:
        reading = commencement_reading({"liik": liik, "kuupaev": day}, status=src["stage"])
        key = (reading["target"], reading["kind"], reading["date"], text_value(note))
        if reading["target"] and key in seen:
            continue  # the same row stated twice is one commencement
        seen.add(key)
        readings.append({**reading, "note": text_value(note)})
    unreadable = any(not r["target"] for r in readings)
    want = Counter((r["target"], r["kind"], r["date"], r["note"]) for r in readings if r["target"])
    now = Counter(tuple(item) for item in have)
    if not unreadable and want == now:
        if unchanged:
            return _decision("commencements", SyncOutcome.UNCHANGED, **common)
        return _decision("commencements", SyncOutcome.ALREADY_CONVERGED, target=have, **common)
    reason = ""
    if unreadable:
        reason = COMMENCEMENT_UNREADABLE
    elif closed:
        reason = MATTER_CLOSED
    if reason:
        if unchanged:
            return _decision("commencements", SyncOutcome.UNCHANGED, **common)
        return _decision("commencements", SyncOutcome.REVIEW_REQUIRED, reason, **common)
    add = []
    remaining = Counter(now)
    for r in readings:
        if not r["target"]:
            continue
        key = (r["target"], r["kind"], r["date"], r["note"])
        if remaining[key] > 0:
            remaining[key] -= 1
            continue
        add.append({"target": r["target"], "kind": r["kind"], "date": r["date"], "note": r["note"]})
    cancel = [list(key) for key, n in (now - want).items() for _ in range(n)]
    target = {"add": add, "cancel": cancel, "source": [list(r) for r in c_src]}
    wanted = Counter(want)
    # A model switch because the stage changed is a source change, not a restore.
    restored = _restore_reason(
        unchanged and src["stage"] == _entry(applied, "stage").get("source"),
        entry,
        lambda before: Counter(tuple(item) for item in (before or [])) == wanted,
    )
    return _decision(
        "commencements",
        SyncOutcome.ELIGIBLE,
        restored,
        target=target,
        **common,
    )


def _legal_act_target(c_src: str) -> tuple[list[str] | None, str]:
    from app.taxonomy.legal_instruments import current_legal_instrument_keys

    if not c_src:
        return None, INSTRUMENT_CLEARED
    keys = sorted(current_legal_instrument_keys(c_src))
    if not keys:
        return None, INSTRUMENT_UNMAPPED
    if len(keys) > 1:
        return None, INSTRUMENT_SEVERAL
    return keys, ""


def _plan_authoritative(
    plan: MatterPlan,
    state: LovableSyncState,
    src: dict[str, Any],
    b: dict[str, Any],
    *,
    resolver: OwnerResolver,
    today: dt.date,
) -> None:
    """Every source-owned field of one operational Teema (docs/adr/0213 §10)."""
    applied = state.applied
    closed = not b["is_open"]

    # The lifecycle first: whether the Teema is (or tonight becomes) closed
    # decides what the step and the request may be.
    life, desired_life, life_hold = _lifecycle_decisions(state, src, b)
    if desired_life is not None and life[0].outcome in (
        SyncOutcome.ELIGIBLE,
        SyncOutcome.ALREADY_CONVERGED,
    ):
        closed_after = not desired_life["open"]
    else:
        closed_after = closed
    # Only a held closure holds the step and the request with it: an unmapped
    # stage label says nothing about either.
    if life_hold not in (SUCCESSOR_MISSING, SUCCESSOR_AMBIGUOUS):
        life_hold = ""

    plan.decisions.append(
        _scalar(
            "title",
            applied=applied,
            c_src=src["title"],
            b=b["title"],
            c_tgt=src["title"] or None,
            hold="" if src["title"] else TITLE_EMPTY,
        )
    )
    owner_target, owner_hold = resolver.resolve(src["owner"])
    if owner_hold and state.cohort == HISTORY_COHORT:
        # Former staff with no Juristid account: one grouped, not-representable
        # class of the historical register (`historical_hold`), never a flood.
        owner_hold = HISTORY_OWNER_UNRESOLVED
    plan.decisions.append(
        _scalar(
            "owner",
            applied=applied,
            c_src=src["owner"],
            b=b["owner"],
            c_tgt=owner_target,
            hold=owner_hold,
        )
    )
    link = src["onenote_link"]
    link_hold = "" if link.startswith("https://") else (LINK_NOT_HTTPS if link else LINK_REMOVED)
    plan.decisions.append(
        _scalar(
            "onenote_link",
            applied=applied,
            c_src=link,
            b=b["onenote_link"],
            c_tgt=link or None,
            hold=link_hold,
        )
    )

    outcome_decisions, outcome = _outcome_decisions(applied, src, b, today=today)
    outcome_hold = next(
        (d.reason for d in outcome_decisions if d.outcome == SyncOutcome.REVIEW_REQUIRED),
        "",
    )
    request: FieldDecision | None = None
    request_day = b.get("opinion_deadline")
    if "opinion_deadline" in applied:
        request = _request_decision_authoritative(
            applied,
            src,
            b,
            closed_after=closed_after,
            lifecycle_hold=life_hold,
            outcome=outcome,
            today=today,
            outcome_hold=outcome_hold if outcome_hold != ADDRESSEE_NO_SEND else "",
        )
        if request.outcome in (
            SyncOutcome.ELIGIBLE,
            SyncOutcome.ALREADY_CONVERGED,
            SyncOutcome.UNCHANGED,
        ) and not isinstance(request.target, dict):
            # The day the request has after tonight: a drafting step follows it.
            request_day = request.target
        elif isinstance(request.target, dict):
            request_day = None  # it ends tonight
    plan.decisions.extend(
        _step_decisions_authoritative(
            applied,
            src,
            b["next_action"],
            closed_after=closed_after,
            lifecycle_hold=life_hold,
            request_day=request_day,
            request_before=b.get("opinion_deadline"),
            outcome=outcome,
            outcome_hold=outcome_hold if outcome_hold != ADDRESSEE_NO_SEND else "",
            today=today,
        )
    )
    plan.decisions.extend(life)
    if request is not None:
        plan.decisions.append(request)
    if "sent" in applied:
        plan.decisions.extend(outcome_decisions)
    if "commencements" in applied:
        plan.decisions.append(_commencements_authoritative(applied, src, b, closed=closed))
    if "koda_initiative" in applied:
        koda_hold = ""
        if not src["koda_initiative"] and b["koda_initiative"]:
            # Back to the track it had before it became an initiative, when the
            # Teema's own history names it; otherwise the track is not stated.
            koda_hold = (
                "" if _track_before_initiative(state.matter_id) is not None else (TRACK_NOT_STATED)
            )
        plan.decisions.append(
            _scalar(
                "koda_initiative",
                applied=applied,
                c_src=src["koda_initiative"],
                b=b["koda_initiative"],
                c_tgt=src["koda_initiative"],
                hold=koda_hold,
            )
        )
    if "legal_act" in applied:
        keys, act_hold = _legal_act_target(src["legal_act"])
        if keys is None and not src["legal_act"] and not b["legal_act"]:
            keys, act_hold = [], ""  # blank in both: nothing to say
        plan.decisions.append(
            _scalar(
                "legal_act",
                applied=applied,
                c_src=src["legal_act"],
                b=b["legal_act"],
                c_tgt=keys,
                hold=act_hold,
            )
        )
    if "received" in applied:
        day = src["received"]
        received_hold = ""
        if day is not None and str(day).startswith("?"):
            received_hold = DATE_UNREADABLE
        elif day is not None and dt.date.fromisoformat(day) > today:
            received_hold = RECEIVED_IN_FUTURE
        elif day is not None and b["received"] is None and b["koda_initiative"]:
            received_hold = RECEIVED_ON_INITIATIVE
        plan.decisions.append(
            _scalar(
                "received",
                applied=applied,
                c_src=day,
                b=b["received"],
                c_tgt=day,
                hold=received_hold,
                blank_converges=True,
            )
        )
    if "operational_scope" in applied:
        entry = _entry(applied, "operational_scope")
        same = src["operational_scope"] == entry.get("source")
        plan.decisions.append(
            _decision(
                "operational_scope",
                SyncOutcome.UNCHANGED if same else SyncOutcome.REVIEW_REQUIRED,
                "" if same else SCOPE_NOT_REPRESENTABLE,
                baseline=entry.get("source"),
                source=src["operational_scope"],
            )
        )
    for decision in plan.decisions:
        if (
            decision.outcome == SyncOutcome.ALREADY_CONVERGED
            and decision.field not in (CONTINUATION,)
            and _j(decision.baseline) == _j(decision.source)
        ):
            # Unchanged in Lovable, and Juristid already holds it: nothing to
            # report, only the baseline to settle.
            decision.outcome, decision.settle = SyncOutcome.UNCHANGED, True


#: The order an authoritative Teema's writes go in: identity and classification
#: first; a reopening before the step it makes possible; the request and the
#: outcome before a closure; the step after a closure, which has already ended it.
AUTHORITATIVE_ORDER = (
    "title",
    "owner",
    "onenote_link",
    "koda_initiative",
    "legal_act",
    "received",
    "commencements",
    "stage:open",
    "continuation:open",
    "opinion_deadline",
    "sent",
    "sent_date",
    "addressee",
    "stage:close",
    "continuation:close",
    "next_action_text",
    "next_action_date",
)
#: Fields written by another field's write (the operation's other half).
RIDING_FIELDS = {
    "next_action_date": "next_action_text",
    "sent_date": "sent",
    "addressee": "sent",
    CONTINUATION: "stage",
}


def _write_order(decision: FieldDecision) -> int:
    name = decision.field
    if name in ("stage", CONTINUATION):
        target = decision.target or {}
        name = f"{name}:{'open' if target.get('open') else 'close'}"
    return AUTHORITATIVE_ORDER.index(name)


def _lifecycle_reason(desired: dict[str, Any], src: dict[str, Any]) -> str:
    from app.matters.models import Matter

    if desired.get("successor"):
        successor = Matter.all_objects.filter(pk=desired["successor"]).first()
        if successor is not None and successor.reference_year:
            return (
                f"Lovable: jätkub teemana {successor.reference_year}_{successor.reference_number}."
            )
        return "Lovable: jätkub teise teemana."
    return f"Lovable: «{text_value(src['stage'])}»."


def _apply_lifecycle(
    decision: FieldDecision, *, matter: Any, src: dict[str, Any], provenance: dict[str, Any]
) -> Any:
    from app.matters.enums import StageEpisodeOrigin
    from app.matters.models import Matter
    from app.matters.services import (
        change_stage,
        close_matter_from_source,
        reopen_matter_into_stage,
    )
    from app.workflow.models import StageVocabulary

    desired = decision.target
    fresh = Matter.all_objects.get(pk=matter.pk)
    b_life = conditional_values(fresh)["lifecycle"]
    stage = StageVocabulary.objects.get(key=desired["stage"]) if desired.get("stage") else None
    if desired["open"]:
        if not b_life["open"]:
            # The request the Teema closed with stays current when it is Lovable's.
            held_day = fresh.response_deadline
            keep = held_day is not None and held_day.isoformat() == src.get("opinion_deadline")
            reopen_matter_into_stage(
                matter=fresh, stage=stage, actor=None, keep_response_deadline=keep
            )
            if desired.get("move_to"):
                _move_to_register(fresh, desired["move_to"], provenance=provenance)
        else:
            change_stage(matter=fresh, stage=stage, actor=None, origin=StageEpisodeOrigin.IMPORTED)
    elif not b_life["open"]:
        from app.matters.services import change_closure_from_source

        change_closure_from_source(
            matter=fresh,
            disposition=desired["disposition"],
            successor=(
                Matter.objects.get(pk=desired["successor"]) if desired.get("successor") else None
            ),
            reason=_lifecycle_reason(desired, src),
            provenance=provenance,
        )
    else:
        successor = (
            Matter.objects.get(pk=desired["successor"]) if desired.get("successor") else None
        )
        close_matter_from_source(
            matter=fresh,
            disposition=desired["disposition"],
            stage=stage if stage is not None and b_life["stage"] != desired["stage"] else None,
            successor=successor,
            reason=_lifecycle_reason(desired, src),
            provenance=provenance,
        )
    return conditional_values(Matter.all_objects.get(pk=matter.pk))["lifecycle"]


def _track_before_initiative(matter_id: Any) -> str | None:
    """The track a Teema had before its latest change to a Chamber initiative,
    from its own `MATTER_TRACK_CHANGED` history, or ``None`` when no such change
    is recorded (an imported initiative): then the track is not stated."""
    from app.audit.models import ChangeEvent
    from app.workflow.enums import Track

    event = (
        ChangeEvent.objects.filter(
            matter_id=matter_id,
            event_type="MATTER_TRACK_CHANGED",
            payload__to=Track.KODA_INITIATIVE,
        )
        .order_by("-occurred_at")
        .first()
    )
    if event is None:
        return None
    return str((event.payload or {}).get("from") or "")


def _move_to_register(matter: Any, cohort: str, *, provenance: dict[str, Any]) -> None:
    """A historical Teema Lovable reopened inside its operational scope becomes
    current work (owner decision 11.11): the archive record is promoted to a
    full one (`promote_matter_to_full`, its identity and provenance unchanged)
    and its synchronisation state joins the register cohort, recorded as an
    append-only `MembershipBasis.MOVED` membership — the history cohort's
    required size drops by it and the register's grows (`cohorts`)."""
    from app.matters.enums import RecordMode
    from app.matters.models import Matter
    from app.matters.services import promote_matter_to_full

    fresh = Matter.all_objects.get(pk=matter.pk)
    if fresh.record_mode == RecordMode.ARCHIVE:
        promote_matter_to_full(
            matter=fresh,
            actor=None,
            reason="Lovable avas ajaloolise teema uuesti (operatiivne ulatus).",
            provenance=provenance,
        )
    state = LovableSyncState.objects.select_for_update().get(matter_id=matter.pk)
    LovableSyncState.objects.filter(pk=state.pk).update(cohort=cohort, updated_at=timezone.now())
    remember_membership(
        cohort=cohort,
        matter_id=matter.pk,
        source_id=state.source_id,
        basis=MembershipBasis.MOVED,
    )


#: `source_text` of a commencement the synchronisation writes: with the import's
#: own `EvidencePilotRecord` (record kind MatterEffectiveDate /
#: MatterImportantDate) it is how a commencement is known to be Lovable's, so a
#: person's own dates are never reconciled away (review P2-3).
LOVABLE_COMMENCEMENT_MARK = "Lovable"


@dataclass
class _CommencementRecord:
    pk: Any
    model: str
    kind: str
    date: str | None
    note: str
    used: bool = False


def _lovable_commencement_records(matter_id: Any) -> list[_CommencementRecord]:
    """The Teema's ACTIVE commencements that came from Lovable: those the
    Lovable imports recorded (`EvidencePilotRecord`, record kind
    MatterEffectiveDate / MatterImportantDate) and those the synchronisation
    wrote (`source_text` = `LOVABLE_COMMENCEMENT_MARK`). A person's own date —
    neither — is Juristid's only and never compared."""
    from app.intelligence.enums import FactStatus
    from app.intelligence.models import MatterEffectiveDate, MatterImportantDate
    from app.legacy_import.lovable_pilot import COHORTS, T_EFFECTIVE_DATE, T_IMPORTANT_DATE
    from app.legacy_import.models import EvidencePilotRecord

    # Only the Lovable importer's cohorts (`lovable_pilot.COHORTS`: the pilot, the
    # operational cohort and its rebuild, the register replacement, the history
    # backfill — new topics are created into one of these): the OneNote
    # evidence pilot (`evidence_pilot.PILOT`) and the Excel pilot write the same
    # record kinds, and their dates are not Lovable's.
    imported = {
        str(pk)
        for pk in EvidencePilotRecord.objects.filter(
            matter_id=matter_id,
            pilot__in=COHORTS,
            record_kind__in=(T_EFFECTIVE_DATE, T_IMPORTANT_DATE),
        ).values_list("target_id", flat=True)
    }
    out: list[_CommencementRecord] = []
    for pk, kind, day, note, mark in MatterEffectiveDate.objects.filter(
        matter_id=matter_id, status=FactStatus.ACTIVE, removed_at__isnull=True
    ).values_list("pk", "kind", "date_value", "description", "source_text"):
        if str(pk) in imported or mark == LOVABLE_COMMENCEMENT_MARK:
            out.append(
                _CommencementRecord(
                    pk,
                    T_EFFECTIVE_DATE,
                    str(kind),
                    day.isoformat() if day else None,
                    text_value(note),
                )
            )
    for pk, kind, day, note, mark in MatterImportantDate.objects.filter(
        matter_id=matter_id,
        status=FactStatus.ACTIVE,
        removed_at__isnull=True,
        kind__in=COMMENCEMENT_IMPORTANT_KINDS,
    ).values_list("pk", "kind", "date_value", "note", "source_text"):
        if str(pk) in imported or mark == LOVABLE_COMMENCEMENT_MARK:
            out.append(
                _CommencementRecord(
                    pk,
                    T_IMPORTANT_DATE,
                    str(kind),
                    day.isoformat() if day else None,
                    text_value(note),
                )
            )
    return out


def lovable_commencements(matter_id: Any) -> list[list[Any]]:
    """B for source authority's commencements: ``[target, kind, day, note]`` of
    each Lovable commencement (`_lovable_commencement_records`), sorted."""
    return sorted(
        ([r.model, r.kind, r.date, r.note] for r in _lovable_commencement_records(matter_id)),
        key=lambda item: json.dumps(item, default=str),
    )


def _apply_commencements(decision: FieldDecision, *, matter: Any) -> Any:
    """The commencement set Lovable states (`_commencements_authoritative`):
    each one it no longer states is cancelled through its own service, with the
    reason and its own audited event, and kept; each new one is added with its
    scope note. Nothing is deleted."""
    from app.intelligence.enums import FactStatus
    from app.intelligence.models import MatterEffectiveDate, MatterImportantDate
    from app.intelligence.services import (
        add_effective_date,
        add_important_date,
        cancel_effective_date,
        cancel_important_date,
    )
    from app.legacy_import.lovable_pilot import COMMENCEMENT_TITLES, T_EFFECTIVE_DATE

    target = decision.target
    stated = "; ".join(
        " ".join(str(part) for part in item if part) for item in target.get("source") or []
    )
    reason = f"Lovable'is muudeti jõustumist. Lovable'i järgi nüüd: {stated or 'puudub'}."[:500]
    ours = _lovable_commencement_records(matter.pk)
    for model_name, kind, day, note in target["cancel"]:
        record = next(
            (
                r
                for r in ours
                if r.model == model_name
                and (r.kind, r.date, r.note) == (kind, day, note)
                and not r.used
            ),
            None,
        )
        if record is None:
            raise SyncRefused("COMMENCEMENT_GONE", "A commencement to end is gone.")
        record.used = True
        if model_name == T_EFFECTIVE_DATE:
            effective = MatterEffectiveDate.objects.get(pk=record.pk, status=FactStatus.ACTIVE)
            cancel_effective_date(record=effective, actor=None, reason=reason)
        else:
            important = MatterImportantDate.objects.get(pk=record.pk, status=FactStatus.ACTIVE)
            cancel_important_date(record=important, actor=None, reason=reason)
    for item in target["add"]:
        day = dt.date.fromisoformat(item["date"]) if item["date"] else None
        if item["target"] == T_EFFECTIVE_DATE:
            add_effective_date(
                matter=matter,
                kind=item["kind"],
                date_value=day,
                period_end=day,
                date_precision="EXACT",
                description=item.get("note", ""),
                actor=None,
                source_text=LOVABLE_COMMENCEMENT_MARK,
            )
        else:
            if day is None:
                raise SyncRefused("NO_DAY", "An important date needs its day.")
            add_important_date(
                matter=matter,
                title=COMMENCEMENT_TITLES[item["kind"]],
                date_value=day,
                period_end=day,
                date_precision="EXACT",
                kind=item["kind"],
                note=item.get("note", ""),
                actor=None,
                source_text=LOVABLE_COMMENCEMENT_MARK,
            )
    return lovable_commencements(matter.pk)


def _apply_step(
    decision: FieldDecision, *, matter: Any, provenance: dict[str, Any]
) -> dict[str, Any] | None:
    from app.legacy_import.lovable_reading import STEP_RULE_VERSION
    from app.matters.models import Matter
    from app.workflow.enums import ActionStatus
    from app.workflow.models import NextAction
    from app.workflow.services import cancel_next_action, set_next_action

    target = decision.target
    fresh = Matter.all_objects.get(pk=matter.pk)
    open_step = NextAction.objects.filter(matter=fresh, status=ActionStatus.OPEN).first()
    if target.get("end"):
        if open_step is not None and fresh.is_open:
            cancel_next_action(
                action=open_step,
                actor=None,
                reason="Lovable'is pole enam järgmist tegevust.",
                provenance=provenance,
            )
        return None
    day = target.get("target_date")
    new = set_next_action(
        matter=fresh,
        text=target["text"],
        kind=target["kind"],
        date_semantics=target["date_semantics"],
        target_date=dt.date.fromisoformat(day) if day else None,
        date_precision=target["date_precision"],
        source_text=target["text"],
        actor=None,
        provenance={**provenance, "kind_basis": STEP_RULE_VERSION},
    )
    return _action_dict(new)


def _apply_request(decision: FieldDecision, *, matter: Any) -> Any:
    from app.matters.enums import ResponseDeadlineChange, ResponseDeadlineOutcome
    from app.matters.models import Matter
    from app.matters.response_deadlines import change_response_deadline

    fresh = Matter.all_objects.get(pk=matter.pk)
    if isinstance(decision.target, dict) and decision.target.get("resolve"):
        from app.core.dates import format_estonian_date
        from app.legacy_import.lovable_sync_models import DOCUMENT_NOT_YET_AVAILABLE
        from app.matters.response_deadlines import resolve_response_deadline

        day = decision.target.get("date")
        sent_on = dt.date.fromisoformat(day) if day else None
        # Identity, never dates: the request is Lovable's when its `requested_at`
        # is the one recorded with the baseline (a MOVED request keeps it); a
        # fresh or REPLACED request has a new one — Juristid's own, which the
        # earlier send never answered.
        requested = fresh.response_requested_at
        asked_after = (
            sent_on is not None
            and requested is not None
            and timezone.localtime(requested).date() > sent_on
        )
        current_day = fresh.response_deadline.isoformat() if fresh.response_deadline else None
        if REQUESTED_AT in decision.target:
            not_lovables = decision.target[REQUESTED_AT] != _iso_moment(requested)
        else:
            # No identity recorded yet (the first night after the deploy): the
            # dates decide — recorded after the send, for a day not Lovable's.
            not_lovables = asked_after and current_day != decision.target.get("day")
        if decision.target["resolve"] == "SENT" and not_lovables and sent_on is not None:
            # A request that is not Lovable's was never answered by its send:
            # withdrawn, with Lovable's word for what was sent — no answer claimed,
            # and an order of events only where the dates show one.
            when = format_estonian_date(sent_on)
            if asked_after:
                note = (
                    f"Lovable'i järgi saadeti arvamus {when}, enne seda küsimist; "
                    "küsimine lõpetati vastust märkimata."
                )
            else:
                note = (
                    f"Lovable'i järgi saadeti arvamus {when}. See ei ole Lovable'i "
                    "arvamuse küsimine; küsimine lõpetati vastust märkimata."
                )
            outcome = ResponseDeadlineOutcome.CANCELLED
        elif decision.target["resolve"] == "SENT":
            when = format_estonian_date(sent_on) if sent_on else ""
            note = f"Lovable'i järgi saadetud {when}. {DOCUMENT_NOT_YET_AVAILABLE}"
            outcome = ResponseDeadlineOutcome.ANSWERED
        else:
            note = "Lovable'i järgi arvamust ei saadetud («ei saatnud»)."
            outcome = ResponseDeadlineOutcome.NOT_ANSWERING
        if fresh.response_deadline is not None:
            resolve_response_deadline(matter=fresh, outcome=outcome, actor=None, note=note)
        return conditional_values(Matter.all_objects.get(pk=matter.pk))["opinion_deadline"]
    if decision.target is None:
        change_response_deadline(
            matter=fresh,
            deadline=None,
            actor=None,
            previous_outcome=ResponseDeadlineOutcome.CANCELLED,
            previous_note="Lovable'is arvamuse tähtaeg eemaldati.",
        )
    else:
        moving = fresh.response_deadline is not None
        change_response_deadline(
            matter=fresh,
            deadline=dt.date.fromisoformat(decision.target),
            actor=None,
            change=ResponseDeadlineChange.MOVED if moving else "",
            previous_note="Lovable'is muutus arvamuse tähtaeg." if moving else "",
        )
    return conditional_values(Matter.all_objects.get(pk=matter.pk))["opinion_deadline"]


def _apply_opinion_outcome(
    decision: FieldDecision,
    *,
    matter: Any,
    state: LovableSyncState,
    src: dict[str, Any],
    run: LovableSyncRun,
    snapshot: dict[str, Any],
    version: int | None,
) -> tuple[Any, bool]:
    """A new `LovableOpinionOutcome` (the newest is current), and — when the
    Teema is open and its current request is Lovable's — that request ends:
    answered with the source's words for SENT (no `Submission`, no document),
    «otsustati mitte vastata» for «ei saatnud». Returns the new B and whether
    the request ended."""
    from app.core.dates import format_estonian_date
    from app.legacy_import.lovable_sync_models import (
        DOCUMENT_NOT_YET_AVAILABLE,
        LovableOpinionOutcome,
        OpinionOutcome,
    )
    from app.matters.enums import ResponseDeadlineOutcome
    from app.matters.models import Matter
    from app.matters.response_deadlines import resolve_response_deadline

    target = decision.target
    latest = (
        LovableOpinionOutcome.objects.filter(matter_id=matter.pk)
        .order_by("-recorded_at", "-created_at")
        .first()
    )
    request = src.get("opinion_deadline")
    request_day = (
        dt.date.fromisoformat(request) if request and not str(request).startswith("?") else None
    )
    sent_on = dt.date.fromisoformat(target["date"]) if target.get("date") else None
    LovableOpinionOutcome.objects.create(
        matter_id=matter.pk,
        source_id=state.source_id,
        outcome=target["outcome"],
        sent_on=sent_on,
        request_deadline=request_day,
        koda_initiative=bool(src.get("koda_initiative")),
        source_valja=text_value(src.get("sent"))[:200],
        source_valja_kuupaev=str(src.get("sent_date") or "")[:40],
        addressee_raw=text_value(src.get("addressee"))[:500],
        previous=latest,
        run=run,
        source_version=version,
        source_snapshot_sha256=snapshot["sha256"],
        recorded_at=timezone.now(),
    )
    ended = False
    fresh = Matter.all_objects.get(pk=matter.pk)
    # Lovable's own request only: by identity when the baseline recorded one
    # (a fresh or REPLACED request is not it, whatever its day); `_apply_request`
    # withdraws one that is not.
    request_entry = _entry(state.applied, "opinion_deadline")
    lovables = (
        request_entry[REQUESTED_AT] == _iso_moment(fresh.response_requested_at)
        if REQUESTED_AT in request_entry
        else True
    )
    if (
        target["outcome"] in (OpinionOutcome.SENT, OpinionOutcome.NOT_SENT)
        and fresh.is_open
        and fresh.response_deadline is not None
        and fresh.response_deadline == request_day
        and lovables
    ):
        if target["outcome"] == OpinionOutcome.SENT:
            when = format_estonian_date(sent_on) if sent_on else ""
            note = f"Lovable'i järgi saadetud {when}. {DOCUMENT_NOT_YET_AVAILABLE}"
            outcome = ResponseDeadlineOutcome.ANSWERED
        else:
            note = "Lovable'i järgi arvamust ei saadetud («ei saatnud»)."
            outcome = ResponseDeadlineOutcome.NOT_ANSWERING
        resolve_response_deadline(matter=fresh, outcome=outcome, actor=None, note=note)
        ended = True
    return _outcome_view(matter.pk), ended


def _apply_authoritative(
    decision: FieldDecision,
    *,
    matter: Any,
    state: LovableSyncState,
    plan: MatterPlan,
    provenance: dict[str, Any],
    run: LovableSyncRun,
    snapshot: dict[str, Any],
    applied: dict[str, Any],
) -> Any:
    """One source-authoritative write through its domain service; returns the
    new B and records the field's new baseline in ``applied``."""
    from app.matters.models import Matter
    from app.matters.services import change_track, set_legal_instruments, set_matter_dates
    from app.workflow.enums import Track

    src = plan.source
    name = decision.field
    new_b: Any
    if name in RIDING_FIELDS:
        # Written by its operation's other half (`RIDING_FIELDS`): its B is that one's.
        new_b = _riding_b(name, matter)
    elif name in ("title", "owner", "onenote_link"):
        new_b = _apply_field(
            decision, matter=matter, provenance=provenance, run=run, snapshot=snapshot
        )
    elif name == "commencements":
        new_b = _apply_commencements(decision, matter=matter)
    elif name == "stage":
        new_b = _apply_lifecycle(decision, matter=matter, src=src, provenance=provenance)
    elif name == "next_action_text":
        new_b = _apply_step(decision, matter=matter, provenance=provenance)
    elif name == "opinion_deadline":
        new_b = _apply_request(decision, matter=matter)
    elif name == "sent":
        new_b, ended = _apply_opinion_outcome(
            decision,
            matter=matter,
            state=state,
            src=src,
            run=run,
            snapshot=snapshot,
            version=plan.version,
        )
        if ended:
            # The request Lovable's outcome answers is no longer current.
            applied["opinion_deadline"] = {
                "source": src["opinion_deadline"],
                "target": None,
                MIRRORED: True,
                REQUESTED_AT: None,
            }
    elif name == "koda_initiative":
        track = Track.KODA_INITIATIVE if decision.target else _track_before_initiative(matter.pk)
        if track is None:
            raise SyncRefused(TRACK_NOT_STATED, "The track before the initiative is not known.")
        change_track(matter=Matter.objects.get(pk=matter.pk), track=track)
        new_b = bool(decision.target)
    elif name == "legal_act":
        instruments = Matter._meta.get_field("legal_instruments").related_model
        fresh = Matter.objects.get(pk=matter.pk)
        set_legal_instruments(
            matter=fresh,
            legal_instruments=list(instruments._default_manager.filter(key__in=decision.target)),
        )
        new_b = conditional_values(fresh)["legal_act"]
    elif name == "received":
        fresh = Matter.objects.get(pk=matter.pk)
        set_matter_dates(
            matter=fresh,
            received_date=dt.date.fromisoformat(decision.target) if decision.target else None,
        )
        new_b = decision.target
    else:
        raise SyncRefused("NOT_ALLOWLISTED", f"{name} is not a source-authoritative field.")
    _mirror(applied, decision, new_b, matter_id=matter.pk)
    return new_b


def _riding_b(name: str, matter: Any) -> Any:
    from app.matters.models import Matter

    fresh = Matter.all_objects.get(pk=matter.pk)
    if name == "next_action_date":
        return current_values(fresh)["next_action"]
    if name == CONTINUATION:
        return conditional_values(fresh)[CONTINUATION]
    return _outcome_view(matter.pk)


#: `applied["opinion_deadline"]`: the current request's `response_requested_at`
#: when the baseline was last recorded — Lovable's request's identity.
REQUESTED_AT = "requested_at"


def _iso_moment(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _mirror(
    applied: dict[str, Any], decision: FieldDecision, new_b: Any, *, matter_id: Any = None
) -> None:
    """The baseline after a write or a convergence: A takes C, the target is
    what Juristid now holds, and the field is settled (`MIRRORED`). The
    request's baseline also records its `requested_at` (`REQUESTED_AT`)."""
    name = decision.field
    if name == "opinion_deadline" and matter_id is not None:
        from app.matters.models import Matter

        requested = (
            Matter.all_objects.filter(pk=matter_id)
            .values_list("response_requested_at", flat=True)
            .first()
        )
        applied[name] = {
            "source": decision.source,
            "target": new_b,
            MIRRORED: True,
            REQUESTED_AT: _iso_moment(requested),
        }
        return
    if name == "next_action_text":
        applied[name] = {"source": decision.source, MIRRORED: True}
        step = new_b
        if isinstance(step, dict) and step.get("end"):
            step = None
        applied["next_action"] = {"target": step, "treatment": MIRROR_TREATMENT}
        return
    if name in ("next_action_date", "sent_date", "addressee"):
        applied[name] = {"source": decision.source, MIRRORED: True}
        return
    applied[name] = {"source": decision.source, "target": new_b, MIRRORED: True}


def is_authoritative(state: LovableSyncState) -> bool:
    """Whether a Teema's fields follow Lovable (docs/adr/0213 §10): every live
    synchronised Teema, the closed historical register included (owner
    decision 11.11). Only a tombstone keeps the earlier rules."""
    matter = getattr(state, "matter", None)
    return getattr(matter, "deleted_at", None) is None


# ---------------------------------------------------------------------------
# Applying one Teema
# ---------------------------------------------------------------------------


def _provenance(
    run: LovableSyncRun, state: LovableSyncState, version: int | None, sha: str
) -> dict[str, Any]:
    return {
        "provenance_source": PROVENANCE_SOURCE,
        "sync_run": str(run.pk),
        "source_reference": state.reference,
        "source_version": version,
        "source_snapshot_sha256": sha,
        "mapping_version": MAPPING_VERSION,
    }


def idempotency_key(
    state: LovableSyncState,
    decision: FieldDecision,
    version: int | None,
    run: Any = None,
) -> str:
    """One applied write per source row version and field. A value may come back
    (12.11 → 15.11 → 12.11 → 15.11); a Lovable version never does, because its
    guard makes every save exactly one higher."""
    parts: list[Any] = [str(state.source_id), decision.field, version, decision.source]
    if run is not None and (
        decision.reason == RESTORED or _j(decision.source) == _j(decision.baseline)
    ):
        # A restore writes the same source value again over a local edit, and a
        # derived change (a drafting step whose day passed, a request ended
        # because the sent status changed) writes with this field's source
        # unchanged: one per run, never colliding with the write that first
        # brought the value.
        parts.append(str(run.pk))
    material = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(material.encode()).hexdigest()


def _apply_field(
    decision: FieldDecision,
    *,
    matter: Any,
    provenance: dict[str, Any],
    run: LovableSyncRun,
    snapshot: dict[str, Any],
) -> Any:
    """One allowlisted write, through its canonical service. Returns the new B."""
    from app.accounts.models import User
    from app.matters.services import assign_matter, set_matter_title
    from app.workflow.models import NextAction
    from app.workflow.services import set_next_action

    if decision.field == "title":
        set_matter_title(matter=matter, value=decision.target, actor=None, provenance=provenance)
        return text_value(matter.title)
    if decision.field == "owner":
        owner = User.objects.get(pk=decision.target, is_active=True)
        assign_matter(matter=matter, owner=owner, actor=None, provenance=provenance)
        return str(owner.pk)
    if decision.field == "next_action_date":
        open_step = NextAction.objects.select_for_update(no_key=True).get(pk=decision.target["id"])
        new = set_next_action(
            matter=matter,
            text=open_step.text,
            kind=open_step.kind,
            date_semantics=open_step.date_semantics,
            target_date=dt.date.fromisoformat(decision.target["target_date"]),
            date_precision=open_step.date_precision,
            source_text=open_step.source_text,
            responsible=open_step.responsible,
            actor=None,
            provenance={**provenance, "kind_basis": "UNCHANGED_SOURCE_WORDING"},
            carry_plan_step=True,
            carry_visibility_override=True,
        )
        return _action_dict(new)
    if decision.field == "onenote_link":
        return _append_link_observation(
            matter=matter, url=decision.target, run=run, snapshot=snapshot, provenance=provenance
        )
    if decision.field in CONDITIONAL_FIELDS:
        return _apply_conditional(decision, matter=matter)
    raise SyncRefused("NOT_ALLOWLISTED", f"{decision.field} is not an automatic field.")


def _apply_conditional(decision: FieldDecision, *, matter: Any) -> Any:
    """A conditional field (docs/adr/0213), through the service the rebuild used
    for the same change (docs/adr/0202 §3). These services take no provenance;
    the write's record is its `LovableSyncDecision` (APPLIED, idempotency key)."""
    from app.intelligence.services import add_effective_date, add_important_date
    from app.legacy_import.lovable_pilot import COMMENCEMENT_TITLES, T_EFFECTIVE_DATE
    from app.matters.enums import StageEpisodeOrigin
    from app.matters.models import Matter
    from app.matters.response_deadlines import request_response_deadline
    from app.matters.services import change_stage
    from app.workflow.models import StageVocabulary

    if decision.field == "stage":
        change_stage(
            matter=matter,
            stage=StageVocabulary.objects.get(key=decision.target),
            actor=None,
            origin=StageEpisodeOrigin.IMPORTED,
        )
    elif decision.field == "opinion_deadline":
        request_response_deadline(
            matter=matter, deadline=dt.date.fromisoformat(decision.target), actor=None
        )
    else:
        for item in decision.target:
            day = dt.date.fromisoformat(item["date"]) if item["date"] else None
            if item["target"] == T_EFFECTIVE_DATE:
                add_effective_date(
                    matter=matter,
                    kind=item["kind"],
                    date_value=day,
                    period_end=day,
                    date_precision="EXACT",
                    description=item.get("note", ""),
                    actor=None,
                    source_text="Lovable",
                )
            else:
                if day is None:
                    raise SyncRefused("NO_DAY", "An important date needs its day.")
                add_important_date(
                    matter=matter,
                    title=COMMENCEMENT_TITLES[item["kind"]],
                    date_value=day,
                    period_end=day,
                    date_precision="EXACT",
                    kind=item["kind"],
                    note=item.get("note", ""),
                    actor=None,
                    source_text="Lovable",
                )
    fresh = Matter.all_objects.get(pk=matter.pk)
    return conditional_values(fresh)[decision.field]


def _sync_batch(run: LovableSyncRun, snapshot: dict[str, Any]) -> Any:
    from app.legacy_import.models import ImportBatch, ReconciliationStatus

    batch, _ = ImportBatch.objects.get_or_create(
        source_system=SOURCE_SYSTEM,
        source_file_name=SOURCE_TABLE,
        source_snapshot_sha256=snapshot["sha256"],
        importer_version=MAPPING_VERSION,
        defaults={
            "contract_version": MAPPING_VERSION,
            "started_at": run.started_at,
            "reconciliation_status": ReconciliationStatus.COMPLETED,
            "finished_at": run.started_at,
            "notes": f"Lovable sync run {run.pk}",
        },
    )
    return batch


def _append_link_observation(
    *,
    matter: Any,
    url: str,
    run: LovableSyncRun,
    snapshot: dict[str, Any],
    provenance: dict[str, Any],
) -> str:
    """A new `MatterSourceReference` for the new link; the pilot's stays as it was."""
    from app.legacy_import.enums import OneNoteContentStatus
    from app.legacy_import.models import MatchMethod, MatterSourceReference

    year, number = matter.reference_year, matter.reference_number
    MatterSourceReference.objects.create(
        matter=matter,
        import_batch=_sync_batch(run, snapshot),
        source_system=SOURCE_SYSTEM,
        source_file_name=SOURCE_TABLE,
        source_snapshot_sha256=snapshot["sha256"],
        source_sheet=f"{SOURCE_TABLE}:{year}",
        source_row_number=number,
        source_row_raw={"lovable_sync": provenance, "onenote_link": url},
        source_title=matter.title,
        onenote_url=url,
        onenote_content_status=OneNoteContentStatus.NOT_IMPORTED,
        source_era=str(year),
        source_contract_version=MAPPING_VERSION,
        source_parser_version=MAPPING_VERSION,
        match_method=MatchMethod.REFERENCE_TOKEN,
        conflict_state="NONE",
    )
    return url


def apply_matter(
    plan: MatterPlan,
    *,
    run: LovableSyncRun,
    snapshot: dict[str, Any],
    resolver: OwnerResolver,
    today: dt.date,
) -> MatterPlan:
    """Every eligible field of one Teema, all or nothing, under its lock.

    The plan is made again under the lock: a lawyer's save that landed after the
    first reading turns ELIGIBLE into CONFLICT or LOCAL_TEST_CHANGE here, never
    into an overwrite."""
    from app.matters.locks import lock_matter_for_write

    with transaction.atomic():
        matter = lock_matter_for_write(plan.state.matter_id)
        state = LovableSyncState.objects.select_for_update().get(pk=plan.state.pk)
        state.matter = matter
        bundle = bundle_of(snapshot, state.source_id)
        fresh = plan_matter(state, bundle, resolver=resolver, today=today)
        provenance = _provenance(run, state, fresh.version, snapshot["sha256"])
        applied = dict(state.applied)
        authoritative = is_authoritative(state)
        # Title first, then owner, then the step: a hand-over moves the open step
        # to its new owner, and the date edit then carries that responsibility.
        # The authoritative order (`AUTHORITATIVE_ORDER`) keeps the same start.
        order = {name: i for i, name in enumerate(WRITABLE_FIELDS)}
        ordered = sorted(
            fresh.eligible,
            key=_write_order if authoritative else (lambda d: order[d.field]),
        )
        for decision in ordered:
            if authoritative:
                new_b = _apply_authoritative(
                    decision,
                    matter=matter,
                    state=state,
                    plan=fresh,
                    provenance=provenance,
                    run=run,
                    snapshot=snapshot,
                    applied=applied,
                )
            else:
                new_b = _apply_field(
                    decision, matter=matter, provenance=provenance, run=run, snapshot=snapshot
                )
            decision.outcome = SyncOutcome.APPLIED
            decision.current = new_b
            if authoritative:
                pass  # `_apply_authoritative` recorded the new baseline
            elif decision.field == "next_action_date":
                applied["next_action_date"] = {"source": decision.source}
                applied["next_action"] = {**applied.get("next_action", {}), "target": new_b}
            else:
                applied[decision.field] = {"source": decision.source, "target": new_b}
            LovableSyncDecision.objects.create(
                run=run,
                state=state,
                field=decision.field,
                outcome=SyncOutcome.APPLIED,
                baseline=decision.baseline,
                current=new_b,
                source=decision.source,
                reason=decision.reason[:60],
                idempotency_key=idempotency_key(state, decision, fresh.version, run),
            )
        for decision in fresh.decisions:
            if decision.outcome == SyncOutcome.ALREADY_CONVERGED or decision.settle:
                if authoritative:
                    _mirror(applied, decision, decision.target, matter_id=matter.pk)
                else:
                    _advance(applied, decision)
        if (
            fresh.eligible
            or any(d.outcome == SyncOutcome.APPLIED for d in fresh.decisions)
            or applied != state.applied
        ):
            state.applied = applied
            state.applied_version = fresh.version
            state.applied_snapshot_sha256 = snapshot["sha256"]
            state.save(
                update_fields=[
                    "applied",
                    "applied_version",
                    "applied_snapshot_sha256",
                    "updated_at",
                ]
            )
        fresh.state = state
        return fresh


def _advance(applied: dict[str, Any], decision: FieldDecision) -> None:
    """B already says what C says: the baseline catches up, nothing is written."""
    if decision.field == "next_action_text":
        applied["next_action_text"] = {"source": decision.source}
        if isinstance(decision.target, dict):
            # Juristid's open step now reads exactly as Lovable's sentence: a later
            # day-only change of that sentence may move it.
            applied["next_action"] = {"target": decision.target, "treatment": "SOURCE_NEXT_STEP"}
    elif decision.field == "next_action_date":
        applied["next_action_date"] = {"source": decision.source}
        if isinstance(decision.target, dict):
            applied["next_action"] = {**applied.get("next_action", {}), "target": decision.target}
    else:
        applied[decision.field] = {"source": decision.source, "target": decision.target}


def verify_matter(plan: MatterPlan) -> bool:
    """After commit: B is exactly what the baseline says Juristid was given."""
    from app.matters.models import Matter

    state = LovableSyncState.objects.get(pk=plan.state.pk)
    b = full_values(Matter.objects.get(pk=state.matter_id))
    for decision in plan.decisions:
        if decision.outcome != SyncOutcome.APPLIED:
            continue
        if decision.field in ("next_action_date", "next_action_text"):
            if b["next_action"] != state.applied["next_action"]["target"]:
                return False
        elif decision.field in ("sent", "sent_date", "addressee"):
            if b["opinion_outcome"] != state.applied["sent"]["target"]:
                return False
        elif (
            decision.field == "stage"
            and "lifecycle" in b
            and MIRRORED in (state.applied.get("stage") or {})
        ):
            if b["lifecycle"] != state.applied["stage"]["target"]:
                return False
        elif decision.field == CONTINUATION:
            if b[CONTINUATION] != state.applied[CONTINUATION]["target"]:
                return False
        elif decision.field == "commencements" and MIRRORED in (
            state.applied.get("commencements") or {}
        ):
            if b["lovable_commencements"] != state.applied["commencements"]["target"]:
                return False
        elif b[decision.field] != state.applied[decision.field]["target"]:
            return False
    return True


# ---------------------------------------------------------------------------
# New topics (docs/adr/0213, brief 2026-11-09 §6 B2)
# ---------------------------------------------------------------------------

#: The provenance version a created Teema's records carry
#: (`EvidencePilotRecord.pilot_version`): not a reviewed package's version.
NEW_TOPIC_RECORD_VERSION = "sync-2"


@dataclass
class NewTopicPlan:
    """One operational source topic with no Juristid Teema, as tonight read it."""

    source_id: str
    reference: str
    version: int | None = None
    #: CREATABLE: every check passed. Otherwise the hold's code (`NEW_*`).
    reason: str = ""
    #: The reader's topic and its one-topic envelope, when it read.
    topic: dict[str, Any] | None = None
    package: dict[str, Any] | None = None
    package_sha256: str = ""
    plan_sha256: str = ""
    #: Codes and counts only (held columns, stage-link partners).
    detail: dict[str, Any] = field(default_factory=dict)
    #: Set by an APPLY night.
    created_matter_id: Any = None
    failed: bool = False

    @property
    def creatable(self) -> bool:
        return not self.reason


def new_topic_candidates(index: Iterable[dict[str, Any]], cohort_ids: set[str]) -> list[str]:
    """Operational topics of the reading's index outside every synchronised
    cohort, by Lovable id — the population a new Teema could come from. Whether
    Juristid already holds one some other way is `_identity_hold`'s question."""
    return sorted(
        str(row["id"])
        for row in index
        if row.get("operatiivne_ulatus") and str(row["id"]) not in cohort_ids
    )


def _identity_holds(source_ids: list[str]) -> dict[str, str]:
    """Lovable id → why Juristid already holds it: a tombstone (never re-created:
    the topic was deleted in Juristid on purpose, or replaced; a person decides)
    or a live Teema outside the synchronised cohorts (an identity decision).
    Read from every place an identity is kept, tombstones included: the
    synchronisation states, the Lovable source references, the append-only
    cohort memberships and the retirements."""
    from app.legacy_import.models import MatterSourceReference
    from app.matters.models import Matter

    if not source_ids:
        return {}
    holders: dict[str, set[Any]] = {}
    for state_source, matter_id in LovableSyncState.objects.filter(
        source_id__in=source_ids
    ).values_list("source_id", "matter_id"):
        holders.setdefault(str(state_source), set()).add(matter_id)
    for raw, matter_id in MatterSourceReference._base_manager.filter(
        source_system=SOURCE_SYSTEM, source_row_raw__lovable__id__in=source_ids
    ).values_list("source_row_raw", "matter_id"):
        holders.setdefault(str((raw.get("lovable") or {}).get("id")), set()).add(matter_id)
    for model in (LovableSyncMembership, LovableSyncRetirement):
        for kept, matter_id in model.objects.filter(source_id__in=source_ids).values_list(
            "source_id", "matter_id"
        ):
            holders.setdefault(str(kept), set()).add(matter_id)
    deleted = set(
        Matter.all_objects.filter(
            pk__in={pk for found in holders.values() for pk in found},
            deleted_at__isnull=False,
        ).values_list("pk", flat=True)
    )
    out: dict[str, str] = {}
    for source_id, found in holders.items():
        if source_id not in source_ids or not found:
            continue
        out[source_id] = NEW_UUID_TOMBSTONE if found & deleted else NEW_UUID_LIVE
    return out


#: A new topic's reference was given back by the replacement (docs/adr/0203
#: §1.2): free for that replacement only; the importer's planner still counts it
#: as taken, so a person decides (an owner decision may waive it later).
NEW_REFERENCE_RELEASED = "REFERENCE_RELEASED_BY_REPLACEMENT"


def _reference_hold(reference: str) -> str:
    """Why the reference cannot be given to a new Teema, or ``""``: held by any
    Matter (a tombstone keeps its reference unless the replacement released
    it), or once held by a permanently deleted one (deleted means taken,
    docs/adr/0191 §5). A reference the replacement released is reported apart."""
    from app.audit.enums import SecurityEventType
    from app.audit.models import SecurityAuditEvent
    from app.legacy_import.lovable_pilot import parse_reference
    from app.matters.models import Matter

    parsed = parse_reference(reference)
    if parsed is None:
        return NEW_UNREADABLE
    year, number = parsed
    if Matter.all_objects.filter(reference_year=year, reference_number=number).exists():
        return NEW_REFERENCE_TAKEN
    events = SecurityAuditEvent.objects.filter(detail__reference=reference)
    if events.filter(event_type=SecurityEventType.REFERENCE_RELEASED).exists():
        return NEW_REFERENCE_RELEASED
    if events.filter(event_type=SecurityEventType.MATTER_DELETED).exists():
        return NEW_REFERENCE_DELETED
    return ""


def _new_topic_people(owner_raw: str, resolver: OwnerResolver) -> dict[str, str]:
    """The package's ``people``: the owner's name → the display name of the one
    account the synchronisation's own resolver finds (`OwnerResolver`), or
    nothing. The reader refuses an open topic without one."""
    from app.accounts.models import User

    target, _reason = resolver.resolve(owner_raw)
    if target is None:
        return {}
    name = User.objects.filter(pk=target).values_list("display_name", flat=True).first()
    if not name or User.objects.filter(display_name=name).count() != 1:
        return {}
    return {owner_raw: name}


def plan_new_topic(
    source_id: str,
    bundle: dict[str, Any] | None,
    *,
    index_row: dict[str, Any],
    hold: str,
    known: dict[str, str],
    resolver: OwnerResolver,
    today: dt.date,
    snapshot_sha256: str,
) -> NewTopicPlan:
    """Every check before a creation, reading only (docs/adr/0213):

    1. the Lovable id is held by no Teema, live or tombstone (`_identity_holds`);
    2. the topic was read in full tonight (`bundle`);
    3. its reference is free and was never a deleted Teema's (`_reference_hold`);
    4. the machine reader reads it (`lovable_reading.read_topic`), and the one-topic
       package validates and plans ready against the database
       (`lovable_pilot.validate_package`, `build_plan`: owner, stage, act,
       identity and reference collisions under the importer's own rules).

    The stage-link partners are counted (``detail``): a partner in Juristid is
    linked only by a person; a partner outside it stays source text."""
    from app.legacy_import import lovable_pilot
    from app.legacy_import.lovable_reading import (
        READER_VERSION,
        ReadingRefused,
        held_columns,
        operative_owner,
        package_for,
        read_topic,
    )

    reference = str(index_row.get("nr") or "")
    plan = NewTopicPlan(source_id=source_id, reference=reference, version=index_row.get("version"))
    if hold:
        plan.reason = hold
        return plan
    if bundle is None:
        plan.reason = NEW_NOT_READ
        return plan
    row = bundle.get("row") or {}
    plan.reference, plan.version = str(row.get("nr") or reference), row.get("version")
    partners = {
        str(side)
        for link in bundle.get("stage_links") or []
        for side in (link.get("prev"), link.get("next"))
        if str(side) != source_id
    }
    plan.detail["partners_in_juristid"] = sum(1 for p in partners if p in known)
    plan.detail["partners_outside"] = len(partners) - plan.detail["partners_in_juristid"]
    taken = _reference_hold(plan.reference)
    if taken:
        plan.reason = taken
        return plan
    people = _new_topic_people(operative_owner(row), resolver)
    try:
        topic = read_topic(
            {**bundle, "references": known}, as_of=today, people=people, historical=False
        )
    except ReadingRefused as error:
        plan.reason = f"{NEW_UNREADABLE}:{error.code}"[:60]
        return plan
    # A continuation is linked only inside one package: a new topic never links
    # itself to a Teema already in Juristid (that would close or move it).
    topic["successor"] = ""
    topic["source"].pop("successor_id", None)
    plan.topic = topic
    plan.detail["held_columns"] = held_columns(topic)
    plan.detail["lifecycle"] = topic["lifecycle"]
    plan.detail["reader_version"] = READER_VERSION
    package = package_for(
        [topic],
        as_of=today,
        snapshot_sha256=snapshot_sha256,
        people=people,
        cohort=lovable_pilot.OPERATIONAL_COHORT,  # the format-2 envelope; see `_writing_plan`
    )
    plan.package = package
    plan.package_sha256 = lovable_pilot.package_digest(package)
    if lovable_pilot.validate_package(package).problems:
        plan.reason = NEW_INVALID
        return plan
    built = lovable_pilot.build_plan(package)
    if built.problems or built.database_state != "ready":
        plan.reason = NEW_PLAN
        return plan
    plan.plan_sha256 = lovable_pilot.plan_digest(built)
    return plan


def _writing_plan(package: dict[str, Any], cohort: str) -> Any:
    """The importer's plan of a one-topic package, written under ``cohort``.

    The package is validated as a format-2 append (the importer's envelope for a
    non-replacement CREATE is the operational cohort's); the records it writes
    and their verification name the cohort the Teema joins, so the plan the
    writer and `verify_applied` see carries that cohort. The package digest —
    the identity the records bind — is the validated envelope's."""
    from dataclasses import replace

    from app.legacy_import import lovable_pilot

    built = lovable_pilot.build_plan(package)
    return replace(built, package={**built.package, "cohort": cohort})


def _new_topic_writer(plan: Any, *, run: LovableSyncRun, cohort: str, plan_sha256: str) -> Any:
    """The importer's CREATE writer (`lovable_pilot._Writer.write_topic`: the
    imported Teema, its open request and drafting step, its commencements, its
    canonical source reference and its provenance records), stamped as the
    synchronisation's: provenance ``LOVABLE_SYNC``, the run, the reader version,
    and a source reference that says a machine read it — never «reviewed»."""
    from app.legacy_import import lovable_pilot
    from app.legacy_import.lovable_reading import READER_VERSION

    class Writer(lovable_pilot._Writer):
        def __init__(self) -> None:
            super().__init__(plan, backup_set=run.backup_set, plan_sha256=plan_sha256)
            self.version = NEW_TOPIC_RECORD_VERSION
            self.decisions = "docs/adr/0213"
            self.base = {
                "source": PROVENANCE_SOURCE,
                "pilot": cohort,
                "pilot_version": self.version,
                "package_sha256": plan.digest,
                "cohort": cohort,
                "format_version": self.format_version,
                "sync_run": str(run.pk),
                "reader_version": READER_VERSION,
                "mapping_version": ENGINE_VERSION,
            }

        def _source_reference(
            self, matter: Any, topic: dict[str, Any], year: int, number: int
        ) -> Any:
            from app.legacy_import.enums import OneNoteContentStatus
            from app.legacy_import.models import MatchMethod, MatterSourceReference

            source = topic["source"]
            snapshot = self.package["snapshot"]
            url = topic.get("onenote_url") or ""
            return MatterSourceReference.objects.create(
                matter=matter,
                import_batch=self.batch,
                source_system=SOURCE_SYSTEM,
                source_file_name=SOURCE_TABLE,
                source_snapshot_sha256=snapshot["snapshot_sha256"],
                source_sheet=f"{SOURCE_TABLE}:{year}",
                source_row_number=number,
                source_row_raw={
                    "lovable": {
                        "id": source["id"],
                        "version": source.get("version"),
                        "updated_at": source.get("updated_at"),
                        "as_of": snapshot.get("as_of"),
                    },
                    "row": source["row"],
                    "excel_source_row": None,
                    "history": [],
                    "commencements": source.get("commencements") or [],
                    "responsible_changes": source.get("responsible_changes") or [],
                    "stage_links": source.get("stage_links") or [],
                    "field_decisions": topic.get("field_decisions") or {},
                    "review_notes": topic.get("review_notes") or [],
                    "reading": topic.get("reading") or {},
                    "lovable_sync": {"run": str(run.pk), "engine": ENGINE_VERSION},
                },
                source_title=topic["title"],
                source_date_raw=str(source["row"].get("sisse") or "")[:200],
                onenote_url=url,
                onenote_content_status=(
                    OneNoteContentStatus.NOT_IMPORTED
                    if url
                    else OneNoteContentStatus.NOT_APPLICABLE
                ),
                source_era=str(year),
                source_contract_version=f"{lovable_pilot.PACKAGE_FORMAT}/{self.format_version}",
                # A machine read it: never "+reviewed" (lovable_pilot's packages).
                source_parser_version=f"{READER_VERSION}+machine",
                match_method=MatchMethod.REFERENCE_TOKEN,
                conflict_state="NONE",
                reviewed_by="",
            )

    return Writer()


def new_topic_key(source_id: str) -> str:
    """The idempotency key of a created topic: one creation per Lovable id, ever."""
    return hashlib.sha256(json.dumps([NEW_TOPIC, str(source_id)]).encode()).hexdigest()


def _verify_new_topic(plan: Any) -> list[str]:
    """The importer's own read-back of what it wrote (`verify_applied`)."""
    from app.legacy_import import lovable_pilot

    return list(lovable_pilot.verify_applied(plan).discrepancies)


def create_new_topic(
    topic_plan: NewTopicPlan,
    *,
    run: LovableSyncRun,
    cohort: Cohort,
    snapshot: dict[str, Any],
) -> Any:
    """Create one new Teema, enrol it and seed its baseline — all or nothing.

    Under the year's reference-sequence row and the Matter table lock (the
    importer's lock order, `lovable_pilot._hold_and_replan`), the package is
    planned again and must hash to the plan the checks approved; then the
    importer's writer writes it, the importer's verification reads it back, the
    synchronisation state is seeded from the new canonical reference, an
    audited `LovableSyncMembership` (``NEW_TOPIC``) records that it joined the
    cohort, and a decision with the topic's idempotency key records the write.
    Any failure rolls back the whole Teema."""
    from app.audit.enums import SecurityEventType
    from app.audit.services import record_security_event
    from app.legacy_import import lovable_pilot
    from app.legacy_import.lovable_reading import READER_VERSION
    from app.legacy_import.models import ImportBatch, MatterSourceReference, ReconciliationStatus
    from app.matters.models import Matter, MatterReferenceSequence

    package = topic_plan.package or {}
    with transaction.atomic():
        year = lovable_pilot.parse_reference(topic_plan.reference)[0]  # type: ignore[index]
        with connection.cursor() as cursor:
            cursor.execute(f"SET LOCAL lock_timeout = '{lovable_pilot.LOCK_TIMEOUT}'")
        MatterReferenceSequence.objects.get_or_create(year=year)
        list(MatterReferenceSequence.objects.select_for_update().filter(year=year))
        with connection.cursor() as cursor:
            cursor.execute(f'LOCK TABLE "{Matter._meta.db_table}" IN SHARE ROW EXCLUSIVE MODE')
        again = lovable_pilot.build_plan(package)
        if again.problems or again.database_state != "ready":
            raise SyncRefused(NEW_PLAN, "The new topic's plan is not writable under the lock.")
        if lovable_pilot.plan_digest(again) != topic_plan.plan_sha256:
            raise SyncRefused(NEW_PLAN, "The new topic's plan changed under the lock.")
        if _identity_holds([topic_plan.source_id]) or _reference_hold(topic_plan.reference):
            raise SyncRefused(NEW_PLAN, "The new topic's identity or reference is taken.")
        plan = _writing_plan(package, cohort.key)
        writer = _new_topic_writer(
            plan, run=run, cohort=cohort.key, plan_sha256=topic_plan.plan_sha256
        )
        writer.batch = ImportBatch.objects.create(
            source_system=SOURCE_SYSTEM,
            source_file_name=SOURCE_TABLE,
            source_snapshot_sha256=snapshot["sha256"],
            importer_version=f"{PROVENANCE_SOURCE.lower()}/{NEW_TOPIC}"[:50],
            contract_version=f"{lovable_pilot.PACKAGE_FORMAT}/{lovable_pilot.ACTIVE_FORMAT_VERSION}",
            started_at=timezone.now(),
            source_row_count=1,
            reconciliation_status=ReconciliationStatus.RUNNING,
            notes=(
                f"{cohort.key}: uus Lovable'i teema {topic_plan.reference} öise sünkroonimise "
                f"käigus {run.pk} ({READER_VERSION}); pakett {topic_plan.package_sha256}; "
                f"plan-sha256 {topic_plan.plan_sha256}; varukoopia {run.backup_set} "
                "(docs/adr/0213)."
            ),
        )
        matter = writer.write_topic(plan.topics[0])
        problems = _verify_new_topic(plan)
        if problems:
            raise SyncRefused(VERIFY_MISMATCH, "; ".join(problems)[:200])
        writer.batch.finished_at = timezone.now()
        writer.batch.created_matter_count = 1
        writer.batch.reconciliation_status = ReconciliationStatus.COMPLETED
        writer.batch.save(
            update_fields=[
                "finished_at",
                "created_matter_count",
                "reconciliation_status",
                "updated_at",
            ]
        )
        reference = MatterSourceReference.objects.get(matter=matter, import_batch=writer.batch)
        seeding = Cohort(cohort.key, cohort.key, topic_plan.package_sha256, 0)
        state = LovableSyncState.objects.create(matter=matter, **seed_values(reference, seeding))
        remember_membership(
            cohort=state.cohort,
            matter_id=matter.pk,
            source_id=state.source_id,
            basis=MembershipBasis.NEW_TOPIC,
        )
        LovableSyncDecision.objects.create(
            run=run,
            state=state,
            field=NEW_TOPIC,
            outcome=SyncOutcome.APPLIED,
            source={"reference": topic_plan.reference, "version": topic_plan.version},
            current={"lifecycle": (topic_plan.topic or {}).get("lifecycle")},
            idempotency_key=new_topic_key(topic_plan.source_id),
        )
        record_security_event(
            event_type=SecurityEventType.IMPORT_RUN,
            actor=None,
            subject=writer.batch,
            detail={
                **writer.base,
                "kind": "NEW_TOPIC",
                "reference": topic_plan.reference,
                "lovable_id": topic_plan.source_id,
                "plan_sha256": topic_plan.plan_sha256,
                "snapshot_sha256": snapshot["sha256"],
                "backup_set": run.backup_set,
                "written": writer.report.written,
            },
        )
    return matter


def _record_new_topics(
    run: LovableSyncRun, plans: list[NewTopicPlan], *, seen_at: dt.datetime
) -> None:
    """The new-topic ledger, one row per Lovable id (docs/adr/0213). A held or
    planned topic stays listed every night until it is created, a person decides
    it (RESOLVED), or it is no longer new (GONE). A RESOLVED row is never planned
    again; a CREATED row never changes."""
    from app.legacy_import.lovable_reading import READER_VERSION

    seen = {p.source_id for p in plans}
    rows = {str(r.source_id): r for r in LovableSyncNewTopic.objects.select_for_update()}
    for p in plans:
        row = rows.get(p.source_id)
        if row is not None and row.status == NewTopicStatus.CREATED:
            if p.reason == NEW_UUID_TOMBSTONE:
                # Created, then permanently deleted in Juristid while Lovable kept
                # it: never re-created; held for a person, naming the tombstone.
                row.status, row.reason = NewTopicStatus.HELD, p.reason
                row.last_seen_at, row.last_seen_run = seen_at, run
                row.save(
                    update_fields=[
                        "status",
                        "reason",
                        "last_seen_at",
                        "last_seen_run",
                        "updated_at",
                    ]
                )
            continue
        if row is not None and row.status == NewTopicStatus.RESOLVED:
            row.last_seen_at, row.last_seen_run = seen_at, run
            row.save(update_fields=["last_seen_at", "last_seen_run", "updated_at"])
            continue
        if row is not None and row.created_matter_id is not None and p.created_matter_id is None:
            # A held row that names its deleted Teema keeps naming it.
            values_matter = row.created_matter_id
        else:
            values_matter = p.created_matter_id
        if p.created_matter_id is not None:
            status, reason = NewTopicStatus.CREATED, ""
        elif p.creatable:
            status, reason = NewTopicStatus.PLANNED, ""
        else:
            status, reason = NewTopicStatus.HELD, p.reason
        values = {
            "reference": p.reference[:20],
            "status": status,
            "reason": reason[:60],
            "last_seen_at": seen_at,
            "last_seen_run": run,
            "source_version": p.version,
            "reader_version": READER_VERSION,
            "package_sha256": p.package_sha256,
            "plan_sha256": p.plan_sha256,
            "detail": p.detail,
            "created_matter_id": values_matter,
        }
        if p.created_matter_id is not None:
            values["created_at_run"] = run
        if row is None:
            LovableSyncNewTopic.objects.create(
                source_id=p.source_id, first_seen_at=seen_at, first_seen_run=run, **values
            )
        else:
            for key, value in values.items():
                setattr(row, key, value)
            row.save()
    for source_id, row in rows.items():
        if source_id not in seen and row.status in (NewTopicStatus.PLANNED, NewTopicStatus.HELD):
            row.status, row.last_seen_run = NewTopicStatus.GONE, run
            row.save(update_fields=["status", "last_seen_run", "updated_at"])


def resolve_new_topic(*, source_id: str, note: str, actor: Any = None) -> LovableSyncNewTopic:
    """An administrator's decision on a held new topic (brief 2026-11-09 §7): it is
    not created by the machine and is not planned again. Nothing else changes."""
    note = text_value(note)
    if not note:
        raise SyncRefused("NO_NOTE", "A decision needs a note saying why.")
    with transaction.atomic():
        row = LovableSyncNewTopic.objects.select_for_update().filter(source_id=source_id).first()
        if row is None:
            raise SyncRefused("UNKNOWN_TOPIC", "No such new topic.")
        if row.status not in (NewTopicStatus.PLANNED, NewTopicStatus.HELD):
            raise SyncRefused("NOT_PENDING", "Only a pending new topic can be decided.")
        row.status, row.decided_by, row.decided_at = NewTopicStatus.RESOLVED, actor, timezone.now()
        row.decision_note = note[:300]
        row.save()
    return row


# ---------------------------------------------------------------------------
# Pending differences (interface 3, «Vaata erinevusi») and the read-only replay
# ---------------------------------------------------------------------------

#: The outcomes that wait for somebody: a held change, a conflict, a safe change
#: not applied (COMPARE or a breaker), a local change kept, a topic gone or
#: renumbered, a failed write.
#: A local value kept while the source is unchanged (LOCAL_TEST_CHANGE) waits for
#: nobody: under source authority the next source change wins (docs/adr/0213 §10).
PENDING_OUTCOMES = (
    SyncOutcome.REVIEW_REQUIRED,
    SyncOutcome.CONFLICT,
    SyncOutcome.ELIGIBLE,
    SyncOutcome.SOURCE_MISSING,
    SyncOutcome.IDENTIFIER_CHANGED,
    SyncOutcome.FAILED,
)
#: What ends a pending difference: the field was written, converged, or decided.
SETTLING_OUTCOMES = (SyncOutcome.APPLIED, SyncOutcome.ALREADY_CONVERGED)


@dataclass(frozen=True)
class PendingDifference:
    """One difference that waits for a person (or for the source to move), with
    the three values the reconciliation compares: A (``baseline``, last
    accepted), B (``current``, Juristid), C (``source``, Lovable tonight)."""

    kind: str  # "FIELD" or "NEW_TOPIC"
    field: str
    outcome: str
    reason: str
    reference: str
    since: dt.datetime | None
    policy: str
    matter: Any = None
    baseline: Any = None
    current: Any = None
    source: Any = None
    run_id: Any = None
    source_id: str = ""
    #: The Teema's `LovableSyncState.cohort` (empty for a new topic): the pages
    #: group by it (`historical_hold`), the engine never reads it here.
    cohort: str = ""


#: Holds a field keeps every night while Lovable's value stays as it is — the
#: value cannot be represented, so nothing ever settles it (`_scalar`'s
#: ``known_differs``, the step and outcome holds). On a closed history Teema
#: with Lovable unchanged (C = A) they are the archive's own state, grouped
#: with the import hold (`historical_hold`), never operational work.
STANDING_HOLDS = frozenset(
    {
        TITLE_EMPTY,
        DATE_UNREADABLE,
        TRACK_NOT_STATED,
        INSTRUMENT_UNMAPPED,
        INSTRUMENT_SEVERAL,
        INSTRUMENT_CLEARED,
        RECEIVED_IN_FUTURE,
        RECEIVED_ON_INITIATIVE,
        LINK_NOT_HTTPS,
        LINK_REMOVED,
        STEP_DATE_NO_TEXT,
        SENT_UNREADABLE,
        SENT_CONTRADICTORY,
        SENT_FUTURE,
        ADDRESSEE_NO_SEND,
        OUTCOME_HELD,
        COMMENCEMENT_UNREADABLE,
        UNRESOLVED_OWNER,
        NOT_ASSIGNABLE,
    }
)


def historical_hold(cohort: str, reason: str, baseline: Any, source: Any) -> bool:
    """A pending difference that is the historical register's own import hold
    — a closed ARCHIVE Teema of the history cohort whose field the backfill held,
    and Lovable has not changed it since the import (C = A) (docs/adr/0213 §9.9)
    — rather than operational work.

    `_hold_imported` gives every import-held field the reason IMPORT_HELD
    whatever Lovable says, so the reason alone would hide a real change (Lovable
    reopening a «lõpetatud» topic): that one, and any other difference on a
    historical Teema (MATTER_CLOSED, a gone topic), stays operational.

    Presentation only: the pages show these in their own group, after everything
    else, and leave them out of the operational headline numbers. Nothing about
    what is pending, applied or resolved depends on it. `historical_hold_q` is
    the same rule for a query of decisions."""
    if cohort == HISTORY_COHORT and reason == HISTORY_OWNER_UNRESOLVED:
        return True
    return (
        cohort == HISTORY_COHORT
        and (reason == IMPORT_HELD or reason in STANDING_HOLDS)
        and _j(baseline) == _j(source)
    )


#: What a held field's decision says: it waits for a person (or the source).
HELD_OUTCOMES = (SyncOutcome.REVIEW_REQUIRED, SyncOutcome.CONFLICT)
#: A run whose writes are committed but whose search rows are stale (LS-09).
SEARCH_REFRESH_FAILED = "SEARCH_REFRESH_FAILED"


def stale_search_matter_ids(*, exclude: Any = None) -> set[Any]:
    """The Teemad whose search rows a run left stale (LS-09): every Teema written
    (an APPLIED decision) by the unbroken line of newest runs that recorded
    ``search_refresh_pending``. The line ends at the first run that read Lovable
    and recorded none; a FAILED run recorded no counts and is passed over."""
    pending: set[Any] = set()
    runs = (
        LovableSyncRun.objects.exclude(trigger=SyncTrigger.CHECK)
        .filter(status__in=(*BASELINE_STATUSES, SyncRunStatus.FAILED))
        .order_by("-started_at")
    )
    if exclude is not None:
        runs = runs.exclude(pk=exclude.pk)
    for earlier in runs[:30]:
        counts = earlier.counts or {}
        if earlier.status == SyncRunStatus.FAILED and not counts:
            continue
        if not counts.get("search_refresh_pending"):
            break
        pending.update(
            earlier.decisions.filter(outcome=SyncOutcome.APPLIED)
            .exclude(state__matter_id__isnull=True)
            .values_list("state__matter_id", flat=True)
        )
    return pending


def held_fields(plans: Iterable[MatterPlan]) -> int:
    """Fields held tonight, whatever the reason (`HELD_OUTCOMES`): what the holds
    breaker (`MAX_NEW_HOLDS`) compares with the previous run's recorded decisions."""
    return sum(1 for plan in plans for d in plan.decisions if d.outcome in HELD_OUTCOMES)


def population_change(previous: dict[str, Any], current: dict[str, Any]) -> str:
    """Why the live source population moved too far since ``previous`` (a stored
    baseline reading) for an APPLY run to write, or ``""`` (`POPULATION_MAX_DELTA`,
    `POPULATION_MAX_SHARE`; audit 2026-11-11 LS-07). Counts only."""
    before = len(previous.get("index") or [])
    now = len(current.get("index") or [])
    delta = abs(now - before)
    if delta > POPULATION_MAX_DELTA and delta > POPULATION_MAX_SHARE * before:
        return (
            f"The live Lovable population moved from {before} to {now} topics since the "
            "previous reading; an APPLY run writes nothing until a person has looked "
            "(a COMPARE run accepts it as the new reading)."
        )
    return ""


def _waits_for_created_successor(plan: MatterPlan, created: set[str]) -> bool:
    """Whether ``plan`` was held for want of its one successor in Juristid
    (`SUCCESSOR_MISSING`), and that successor was created in this run (LS-01)."""
    if plan.status or plan.not_executed:
        return False
    if not any(d.reason == SUCCESSOR_MISSING for d in plan.decisions):
        return False
    successors = plan.source.get(CONTINUATION) or []
    return len(successors) == 1 and str(successors[0]) in created


def standing_holds(plans: Iterable[MatterPlan]) -> dict[str, int]:
    """``"cohort:reason"`` → how many fields are REVIEW_REQUIRED tonight while
    Lovable's value is what it was (C = A): the holds that recur every night."""
    found: Counter[str] = Counter()
    for plan in plans:
        for decision in plan.decisions:
            if decision.outcome != SyncOutcome.REVIEW_REQUIRED:
                continue
            if _j(decision.source) != _j(decision.baseline):
                continue
            found[f"{plan.state.cohort}:{decision.reason}"] += 1
    return dict(sorted(found.items()))


def historical_hold_q() -> Q:
    """`historical_hold` over `LovableSyncDecision` rows (jsonb equality; two
    absent values are equal, as in Python)."""
    return Q(state__cohort=HISTORY_COHORT, reason=HISTORY_OWNER_UNRESOLVED) | (
        Q(state__cohort=HISTORY_COHORT, reason__in=[IMPORT_HELD, *sorted(STANDING_HOLDS)])
        & (Q(source=F("baseline")) | Q(source__isnull=True, baseline__isnull=True))
    )


def latest_reconciled_run() -> LovableSyncRun | None:
    """The newest run that read Lovable and recorded its reconciliation."""
    for run in LovableSyncRun.objects.exclude(trigger=SyncTrigger.CHECK).order_by("-started_at")[
        :20
    ]:
        if run.status != SyncRunStatus.RUNNING and run_reconciled(run) and run.counts:
            return run
    return None


def pending_differences(*, visible_to: Any = None) -> list[PendingDifference]:
    """Every difference still pending after the newest reconciled run.

    A difference stays pending every night until it is applied, converges, the
    source returns to the baseline, or somebody decides it (`resolve_field`,
    `resolve_new_topic`) — so this is that run's pending decisions less the ones
    decided since, each with the moment it first appeared with this source
    value (``since``).

    ``visible_to``: ``None`` is the server's own view (commands, the engine).
    A user sees a Teema's difference only for a Teema `Matter.objects.visible_to`
    lets them open (a tombstone never); a new topic, which has no Teema, is
    counted but never named for a user — no reference, no value (docs/adr/0201
    §6) — until the owner decides that topics without a Teema may be shown."""
    from app.matters.models import Matter

    out: list[PendingDifference] = []
    run = latest_reconciled_run()
    if run is not None:
        rows = list(
            run.decisions.filter(outcome__in=PENDING_OUTCOMES)
            .exclude(field=NEW_TOPIC)
            .select_related("state")
        )
        matter_ids = {d.state.matter_id for d in rows}
        if visible_to is None:
            readable = set(Matter.objects.filter(pk__in=matter_ids).values_list("pk", flat=True))
        else:
            readable = set(
                Matter.objects.visible_to(visible_to)
                .filter(pk__in=matter_ids)
                .values_list("pk", flat=True)
            )
        matters = Matter.objects.in_bulk(list(readable))
        rows = [d for d in rows if d.state.matter_id in readable]
        settles = _settles(rows)
        sinces = _pending_sinces(rows, settles, run)
        for d in rows:
            key = (d.state_id, d.field)
            if any(at > d.created_at for at in settles.get(key, ())):
                continue
            out.append(
                PendingDifference(
                    kind="FIELD",
                    field=d.field,
                    outcome=d.outcome,
                    reason=d.reason,
                    reference=d.state.reference,
                    since=sinces.get(key),
                    policy=policy_of(d.field if d.field != "*" else "source_missing").policy,
                    matter=matters.get(d.state.matter_id),
                    baseline=d.baseline,
                    current=d.current,
                    source=d.source,
                    run_id=run.pk,
                    source_id=str(d.state.source_id),
                    cohort=d.state.cohort,
                )
            )
    for row in LovableSyncNewTopic.objects.filter(
        status__in=(NewTopicStatus.PLANNED, NewTopicStatus.HELD)
    ).order_by("reference"):
        named = visible_to is None
        out.append(
            PendingDifference(
                kind="NEW_TOPIC",
                field=NEW_TOPIC,
                outcome=(
                    SyncOutcome.ELIGIBLE
                    if row.status == NewTopicStatus.PLANNED
                    else SyncOutcome.REVIEW_REQUIRED
                ),
                reason=row.reason,
                reference=row.reference if named else "",
                since=row.first_seen_at,
                policy=FIELD_POLICY[NEW_TOPIC].policy,
                run_id=row.last_seen_run_id,
                source_id=str(row.source_id) if named else "",
            )
        )
    return out


def _settles(rows: list[LovableSyncDecision]) -> dict[tuple[Any, str], list[dt.datetime]]:
    """When each pending (state, field) settled — written, converged or decided —
    in one query whatever the number of differences (settles are rare)."""
    if not rows:
        return {}
    pairs = {(d.state_id, d.field) for d in rows}
    out: dict[tuple[Any, str], list[dt.datetime]] = {}
    for state_id, name, at in (
        LovableSyncDecision.objects.filter(
            state_id__in={d.state_id for d in rows}, field__in={d.field for d in rows}
        )
        .filter(Q(outcome__in=SETTLING_OUTCOMES) | Q(reason__startswith=RESOLVED_PREFIX))
        .values_list("state_id", "field", "created_at")
    ):
        if (state_id, name) in pairs:
            out.setdefault((state_id, name), []).append(at)
    return out


def _pending_sinces(
    rows: list[LovableSyncDecision],
    settles: dict[tuple[Any, str], list[dt.datetime]],
    run: LovableSyncRun,
) -> dict[tuple[Any, str], dt.datetime | None]:
    """When each difference — this field, this source value — first appeared,
    after the field last settled: the first scheduled or manual night that held
    it pending with the same source value, else tonight's run.

    One query for every row at once (the distinct source values each pair has
    had since it last settled, with the first night of each), so its rows grow
    neither with the nights nor with the number of differences."""
    if not rows:
        return {}
    after: dict[tuple[Any, str], dt.datetime | None] = {}
    for d in rows:
        key = (d.state_id, d.field)
        before = [at for at in settles.get(key, ()) if at < d.created_at]
        after[key] = max(before) if before else None
    by_moment: dict[tuple[str, dt.datetime | None], list[Any]] = {}
    for (state_id, name), moment in after.items():
        by_moment.setdefault((name, moment), []).append(state_id)
    condition = Q()
    for (name, moment), states in by_moment.items():
        part = Q(field=name, state_id__in=states)
        if moment is not None:
            part &= Q(created_at__gt=moment)
        condition |= part
    firsts: dict[tuple[Any, str], list[tuple[str, dt.datetime, dt.datetime]]] = {}
    for found in (
        LovableSyncDecision.objects.filter(condition)
        .filter(
            outcome__in=PENDING_OUTCOMES,
            run__trigger__in=(SyncTrigger.SCHEDULED, SyncTrigger.MANUAL),
        )
        .values("state_id", "field", "source")
        .annotate(first=Min("created_at"), started=Min("run__started_at"))
        .order_by()
    ):
        key = (found["state_id"], found["field"])
        if key in after:
            firsts.setdefault(key, []).append(
                (_j(found["source"]), found["first"], found["started"])
            )
    out: dict[tuple[Any, str], dt.datetime | None] = {}
    for d in rows:
        key = (d.state_id, d.field)
        wanted = _j(d.source)
        matching = sorted(
            (first, started) for value, first, started in firsts.get(key, ()) if value == wanted
        )
        out[key] = matching[0][1] if matching else run.started_at
    return out


def replay(
    *,
    run_key: str = "",
    today: dt.date | None = None,
    reading: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """What tonight's rules would decide over a **stored** reading, written to
    nothing: the planner runs inside a PostgreSQL ``READ ONLY`` transaction, so
    any write — a state, a decision, a ledger row, a business record — is refused
    by the database and the replay fails instead (brief 2026-11-09 §6 B5).

    Returns counts and, per Teema, the fields that would not be unchanged with
    their outcome and reason (references and codes; no value, title or wording).
    New topics are listed by reason; the stored readings before this engine carry
    no new-topic detail, so there they read NOT_IN_READING (a real run reads
    them fresh).

    With ``reading`` — a full-detail inventory reading (`lovable_observation.
    take_observation(detail="all")`, `lovable_inventory read`) — the same rules
    are planned over that reading instead: every cohort Teema and every new
    operational topic, read the moment the reading was taken."""
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
            raise _ReplayDone(_replay(run_key=run_key, today=today, reading=reading))
    except _ReplayDone as done:
        # Rolled back on purpose: the read-only setting goes with the transaction.
        return done.result


class _ReplayDone(Exception):
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result


def _reading_document(reading: dict[str, Any]) -> dict[str, Any]:
    """A full-detail inventory reading in the shape the planner reads: every
    topic's allowlisted bundle as the cohort and as the new topics' detail, and
    the index with the snapshot's operational key. Verified first."""
    from app.legacy_import.lovable_observation import DETAIL_ALL, verify_observation

    verify_observation(reading)
    if reading.get("detail_scope") != DETAIL_ALL or "bundles" not in reading:
        raise SyncRefused("NOT_A_FULL_READING", "The reading holds no detail for every topic.")
    bundles = reading["bundles"]
    return {
        "cohort": bundles,
        "new_topics": {"cohort": bundles},
        "index": [
            {"id": row["id"], "nr": row.get("nr"), "operatiivne_ulatus": row.get("operational")}
            for row in reading.get("index") or []
        ],
        "sha256": reading["sha256"],
        "taken_at": reading.get("taken_at"),
    }


def _replay(
    *, run_key: str, today: dt.date | None, reading: dict[str, Any] | None = None
) -> dict[str, Any]:
    day = today or timezone.localdate()
    states = population()
    resolver = OwnerResolver(states)
    if reading is not None:
        document = _reading_document(reading)
        unread = {s.pk for s in states if str(s.source_id) not in document["cohort"]}
        origin = {
            "run_key": "",
            "snapshot_sha256": document["sha256"],
            "taken_at": str(document["taken_at"] or ""),
        }
    else:
        snapshots = comparable_snapshots()
        stored = snapshots.filter(run__run_key=run_key).first() if run_key else snapshots.first()
        if stored is None:
            raise SyncRefused("NO_SNAPSHOT", "No stored Lovable snapshot to replay.")
        document = stored.document
        verify_snapshot(document)
        unread = not_yet_read(states, stored)
        origin = {
            "run_key": stored.run.run_key,
            "snapshot_sha256": stored.sha256,
            "taken_at": stored.taken_at.isoformat(),
        }
    plans = [
        plan_matter(s, bundle_of(document, s.source_id), resolver=resolver, today=day)
        for s in states
        if s.pk not in unread
    ]
    cohort_ids = {str(s.source_id) for s in states}
    candidates = new_topic_candidates(document.get("index") or [], cohort_ids)
    holds = _identity_holds(candidates)
    index_rows = {str(row["id"]): row for row in document.get("index") or []}
    known = {i: str(index_rows[i].get("nr") or "") for i in cohort_ids if i in index_rows}
    detail = (document.get("new_topics") or {}).get("cohort") or {}
    new_plans = [
        plan_new_topic(
            source_id,
            detail.get(source_id),
            index_row=index_rows[source_id],
            hold=holds.get(source_id, ""),
            known=known,
            resolver=resolver,
            today=day,
            snapshot_sha256=document["sha256"],
        )
        for source_id in candidates
    ]
    counts = RunCounts()
    for plan in plans:
        counts.add(plan)
    counts.add_new_topics(new_plans)
    teemad = {
        plan.state.reference: sorted(
            (d.field, d.outcome, d.reason)
            for d in plan.decisions
            if d.outcome != SyncOutcome.UNCHANGED
        )
        + ([("*", plan.status, "")] if plan.status else [])
        for plan in plans
    }
    return {
        **origin,
        "as_of": day.isoformat(),
        "engine": ENGINE_VERSION,
        "counts": {k: v for k, v in counts.as_dict().items() if k != "by_cohort"},
        "teemad": {ref: rows for ref, rows in sorted(teemad.items()) if rows},
        "new_topics": [
            {"reference": p.reference, "reason": p.reason or "CREATABLE"}
            for p in sorted(new_plans, key=lambda p: p.reference)
        ],
    }


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def control() -> LovableSyncControl:
    row, _ = LovableSyncControl.objects.get_or_create(pk=1)
    return row


def set_mode(mode: str, *, reason: str) -> LovableSyncControl:
    if mode not in SyncMode.values:
        raise SyncRefused("UNKNOWN_MODE", f"Unknown mode {mode!r}.")
    with transaction.atomic():
        row = LovableSyncControl.objects.select_for_update().get_or_create(pk=1)[0]
        if row.cut_over and mode != SyncMode.OFF:
            raise SyncRefused(
                "CUT_OVER", "Final cutover is recorded; the synchronisation never runs again."
            )
        row.mode, row.changed_at, row.changed_reason = mode, timezone.now(), reason[:300]
        row.save()
    return row


def cut_over(*, reason: str) -> LovableSyncControl:
    """Final cutover: Juristid becomes the source of truth. Irreversible."""
    with transaction.atomic():
        row = LovableSyncControl.objects.select_for_update().get_or_create(pk=1)[0]
        now = timezone.now()
        row.mode, row.cut_over, row.cut_over_at = SyncMode.OFF, True, row.cut_over_at or now
        row.changed_at, row.changed_reason = now, reason[:300]
        row.save()
    return row


@contextmanager
def run_lock() -> Any:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s, %s)", [LOCK_NAMESPACE, LOCK_KEY])
        if not cursor.fetchone()[0]:
            raise SyncRefused("LOCKED", "Another Lovable synchronisation is running.")
    try:
        yield
    finally:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [LOCK_NAMESPACE, LOCK_KEY])
        except DatabaseError:
            # The session lock dies with the connection; closing it releases it and
            # never masks the error the run itself raised.
            connection.close()


def run_key_for(trigger: str, now: dt.datetime) -> str:
    local = now.astimezone(TALLINN)
    if trigger == SyncTrigger.SCHEDULED:
        return local.date().isoformat()
    return f"{local:%Y-%m-%dT%H%M%S.%f}-{trigger.lower()}"


@dataclass
class RunCounts:
    checked: int = 0
    unchanged: int = 0
    updated: int = 0
    local_only: int = 0
    conflicts: int = 0
    review: int = 0
    eligible_unapplied: int = 0
    converged: int = 0
    missing: int = 0
    identifier_changed: int = 0
    failed: int = 0
    verify_failed: int = 0
    not_executed: int = 0
    fields: dict[str, int] = field(default_factory=dict)
    source_live: int = 0
    source_operational: int = 0
    outside_cohort: int = 0
    new_since_previous: int = 0
    operational_not_in_juristid: int = 0
    #: Teemad with a field still under the import's review hold.
    import_held: int = 0
    #: Open Teemad whose source is no longer ongoing work (`source_activity`).
    stopped_active: int = 0
    #: A check only: Teemad enrolled after the stored reading, so not in it (`not_yet_read`).
    not_yet_read: int = 0
    #: Members the synchronised cohorts lost to a permanent deletion (`LovableSyncRetirement`).
    retired: int = 0
    #: The same per cohort (`cohort_counts`), with each cohort's activity.
    by_cohort: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: New operational topics with no Juristid Teema (docs/adr/0213): seen in the
    #: reading, planned (would be created), held for a person, decided by one,
    #: created tonight, failed to create; and the holds by code.
    new_topics_seen: int = 0
    new_topics_planned: int = 0
    new_topics_held: int = 0
    new_topics_resolved: int = 0
    new_topics_created: int = 0
    new_topics_failed: int = 0
    new_topic_reasons: dict[str, int] = field(default_factory=dict)
    #: The Teemad-written breaker this run used (`run_sync(max_changed=…)`).
    breaker_limit: int = MAX_CHANGED_MATTERS
    #: Teemad with a source change / with only restores tonight, and the restore limit.
    changed_source: int = 0
    changed_restores: int = 0
    restore_limit: int = MAX_RESTORED_MATTERS
    #: Fields held while Lovable is unchanged (C = A): they come back every night
    #: until Lovable changes. ``"cohort:reason"`` → count.
    standing_holds: dict[str, int] = field(default_factory=dict)
    #: Fields held tonight (`held_fields`) and in the previous recorded run: the
    #: holds breaker (`MAX_NEW_HOLDS`) is on the rise between the two.
    held_fields: int = 0
    held_fields_before: int = 0
    #: Teemad written and verified whose search rows could not be refreshed
    #: (`SEARCH_REFRESH_FAILED`): `manage.py refresh_matter_search` recovers them.
    search_refresh_pending: int = 0
    #: Teemad an earlier run left stale in search, refreshed by this run.
    search_refreshed_carried: int = 0
    #: Codes of what went wrong without undoing the run (``SEARCH_REFRESH_FAILED``).
    warnings: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    def add_new_topics(self, plans: Iterable[NewTopicPlan]) -> None:
        for p in plans:
            self.new_topics_seen += 1
            if p.created_matter_id is not None:
                self.new_topics_created += 1
            elif p.failed:
                self.new_topics_failed += 1
            elif p.reason == "RESOLVED":
                self.new_topics_resolved += 1
            elif p.creatable:
                self.new_topics_planned += 1
            else:
                self.new_topics_held += 1
            if p.reason and p.reason != "RESOLVED":
                code = p.reason.split(":")[0]
                self.new_topic_reasons[code] = self.new_topic_reasons.get(code, 0) + 1

    def add(self, plan: MatterPlan) -> None:
        outcomes = plan.outcomes()
        held = sum(1 for d in plan.decisions if d.reason == IMPORT_HELD)
        stopped = plan.open and plan.activity != ACTIVITY_ONGOING
        self.import_held += bool(held)
        self.stopped_active += stopped
        part = self.by_cohort.setdefault(plan.state.cohort or PILOT_COHORT, cohort_counts())
        part["checked"] += 1
        part["unchanged"] += outcomes <= {SyncOutcome.UNCHANGED}
        part["review"] += SyncOutcome.REVIEW_REQUIRED in outcomes
        part["conflicts"] += SyncOutcome.CONFLICT in outcomes
        part["eligible_unapplied"] += SyncOutcome.ELIGIBLE in outcomes
        part["import_held"] += bool(held)
        part["import_held_fields"] += held
        part["stopped_active"] += stopped
        if plan.activity:
            part["activity"][plan.activity] = part["activity"].get(plan.activity, 0) + 1
        self.checked += 1
        if outcomes <= {SyncOutcome.UNCHANGED}:
            self.unchanged += 1
        self.updated += SyncOutcome.APPLIED in outcomes
        self.local_only += SyncOutcome.LOCAL_TEST_CHANGE in outcomes
        self.conflicts += SyncOutcome.CONFLICT in outcomes
        self.review += SyncOutcome.REVIEW_REQUIRED in outcomes
        self.eligible_unapplied += SyncOutcome.ELIGIBLE in outcomes
        self.converged += SyncOutcome.ALREADY_CONVERGED in outcomes
        self.missing += SyncOutcome.SOURCE_MISSING in outcomes
        self.identifier_changed += SyncOutcome.IDENTIFIER_CHANGED in outcomes
        self.failed += SyncOutcome.FAILED in outcomes
        self.verify_failed += plan.verify_failed
        self.not_executed += plan.not_executed
        tally = Counter(
            f"{d.field}:{d.outcome}" for d in plan.decisions if d.outcome != SyncOutcome.UNCHANGED
        )
        for key, n in tally.items():
            self.fields[key] = self.fields.get(key, 0) + n

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def cohort_counts(expected: int | None = None, retired: int = 0) -> dict[str, Any]:
    """One cohort's counts in `RunCounts.by_cohort`. Numbers only. ``expected``
    is the required size, after ``retired`` members left with their deletion;
    ``retired`` is present only for a cohort that lost one (audit 2026-11 C-L-04),
    so a cohort that never did reads exactly as before."""
    return {
        "expected": expected,
        **({"retired": retired} if retired else {}),
        "checked": 0,
        "unchanged": 0,
        "review": 0,
        "conflicts": 0,
        "eligible_unapplied": 0,
        "import_held": 0,
        "import_held_fields": 0,
        "stopped_active": 0,
        "not_yet_read": 0,
        "activity": dict.fromkeys(ACTIVITIES, 0),
    }


def _observe_index(
    counts: RunCounts,
    snapshot: dict[str, Any],
    cohort_ids: set[str],
    previous: dict[str, Any] | None,
) -> None:
    from app.matters.models import Matter

    index = snapshot.get("index") or []
    counts.source_live = len(index)
    counts.source_operational = sum(1 for row in index if row.get("operatiivne_ulatus"))
    counts.outside_cohort = sum(1 for row in index if str(row["id"]) not in cohort_ids)
    if previous is not None:
        before = {str(row["id"]) for row in previous.get("index") or []}
        counts.new_since_previous = sum(1 for row in index if str(row["id"]) not in before)
    known = set(
        Matter.all_objects.exclude(reference_year__isnull=True).values_list(
            "reference_year", "reference_number"
        )
    )
    operational = 0
    for row in index:
        if not row.get("operatiivne_ulatus") or str(row["id"]) in cohort_ids:
            continue
        match = re.fullmatch(r"(\d{4})_(\d+)", str(row.get("nr") or ""))
        if match is None or (int(match.group(1)), int(match.group(2))) not in known:
            operational += 1
    counts.operational_not_in_juristid = operational


def _record(run: LovableSyncRun, plan: MatterPlan) -> None:
    rows = [
        LovableSyncDecision(
            run=run,
            state=plan.state,
            field=d.field,
            outcome=d.outcome,
            reason=d.reason,
            baseline=d.baseline,
            current=d.current,
            source=d.source,
        )
        for d in plan.decisions
        if d.outcome not in (SyncOutcome.UNCHANGED, SyncOutcome.APPLIED)
    ]
    if plan.status:
        rows.append(LovableSyncDecision(run=run, state=plan.state, field="*", outcome=plan.status))
    elif plan.verify_failed:
        rows.append(
            LovableSyncDecision(
                run=run,
                state=plan.state,
                field="*",
                outcome=SyncOutcome.FAILED,
                reason=VERIFY_MISMATCH,
            )
        )
    LovableSyncDecision.objects.bulk_create(rows)


def _observe(state: LovableSyncState, plan: MatterPlan, run: LovableSyncRun) -> None:
    """Last observed, apart from last applied. Fields the pilot's snapshot did not
    carry start their baseline here, on first sight, as report-only fields."""
    updates = {"observed": plan.source, "observed_version": plan.version, "observed_run": run}
    applied = dict(state.applied)
    started = False
    for name in HELD_FIELDS:
        if name not in applied and name in plan.source:
            applied[name] = {"source": plan.source[name]}
            started = True
    for decision in plan.decisions:
        if (
            decision.field == CONTINUATION
            and CONTINUATION not in applied
            and decision.outcome == SyncOutcome.REVIEW_REQUIRED
        ):
            # A changed link is held from the link last seen: it stays pending
            # every night until somebody decides it or the source returns.
            applied[CONTINUATION] = {"source": decision.baseline}
            started = True
    if started:
        updates["applied"] = applied
    LovableSyncState.objects.filter(pk=state.pk).update(**updates, updated_at=timezone.now())


def run_reconciled(run: LovableSyncRun) -> bool:
    """Whether the run got as far as recording its reconciliation.

    A run that failed — or never finished — before `_execute` saved its counts
    compared nothing with Juristid: no field of it may be read as «sama»
    (audit 2026-10-28 B-01)."""
    return not (run.status in (SyncRunStatus.FAILED, SyncRunStatus.RUNNING) and not run.counts)


#: Only a run that finished without failing is a baseline (audit 2026-10-28
#: B-01, scenario A): a failed or unfinished run's snapshot is kept as the
#: evidence of what it read and did, but neither the next run's «new since the
#: previous reading», nor `Kontrolli muudatusi`, nor the next night on
#: Uuendused → Lovable is measured against it.
BASELINE_STATUSES = (SyncRunStatus.SUCCEEDED, SyncRunStatus.REVIEW_ONLY)


def is_baseline_run(run: LovableSyncRun) -> bool:
    return run.status in BASELINE_STATUSES


def comparable_snapshots() -> Any:
    """Stored snapshots of baseline runs only (`is_baseline_run`), newest first."""
    return (
        LovableSyncSnapshot.objects.filter(run__status__in=BASELINE_STATUSES)
        .select_related("run")
        .order_by("-taken_at")
    )


def fresh_backup_set(backup_set: str, started: dt.datetime) -> str:
    """The set's name if it is a set taken shortly before this run and proven
    verified (`backup_set_problem`), else ``""``."""
    return backup_set if not backup_set_problem(backup_set, started) else ""


def backup_set_problem(backup_set: str, started: dt.datetime) -> str:
    """Why ``backup_set`` does not open the mutating stage, or ``""``: no name
    (``NO_SET``), not a set's name (``SET_NAME``), not taken in the
    `BACKUP_MAX_AGE` before the run (``SET_STALE``), or no proof of its
    verification (`backup_proof_problem`). A name alone never passes."""
    if not backup_set:
        return "NO_SET"
    if not _BACKUP_SET.match(backup_set):
        return "SET_NAME"
    taken = dt.datetime.strptime(backup_set[5:], "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.UTC)
    if not dt.timedelta(0) <= started - taken <= BACKUP_MAX_AGE:
        return "SET_STALE"
    return backup_proof_problem(backup_set, started)


def backup_proof_problem(
    backup_set: str, started: dt.datetime, *, set_dir: str | None = None
) -> str:
    """``""`` when the set mounted at ``set_dir`` (default: `HOST_BACKUP_DIR_ENV`)
    proves it is ``backup_set``, verified to `BACKUP_PROOF_LEVEL` after it was
    taken and before this run, and that the dump it verified is the dump the set
    names; otherwise a code (audit 2026-11-11 DR-05). Fail-closed: no mount, no
    marker, a marker for another set, a lower level, a marker older than the set
    or than `BACKUP_MAX_AGE`, or a dump whose SHA-256 is not the one recorded —
    each is a refusal, and the run writes nothing (`NO_BACKUP`).

    The marker (``VERIFIED-L2``) is ``key=value`` lines written by the host's
    `juristid-verify-backup.sh --record` after every level-1 and level-2 check
    passed: ``set``, ``level``, ``verified_at`` (UTC, ``%Y-%m-%dT%H:%M:%SZ``),
    ``database_sha256``. The dump is hashed again here, so a set changed after
    its verification does not pass either."""
    import hashlib
    import os
    from pathlib import Path

    directory = set_dir if set_dir is not None else os.environ.get(HOST_BACKUP_DIR_ENV, "")
    if not directory or not Path(directory).is_dir():
        return "PROOF_NOT_MOUNTED"
    root = Path(directory)
    marker = root / f"VERIFIED-L{BACKUP_PROOF_LEVEL}"
    try:
        lines = marker.read_text(encoding="utf-8").splitlines()
        sums = (root / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return "PROOF_MISSING"
    fields = dict(line.split("=", 1) for line in lines if "=" in line)
    if fields.get("set") != backup_set[5:]:
        return "PROOF_OTHER_SET"
    if not (fields.get("level") or "").isdigit() or int(fields["level"]) < BACKUP_PROOF_LEVEL:
        return "PROOF_LEVEL"
    try:
        verified = dt.datetime.strptime(fields.get("verified_at", ""), "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return "PROOF_TIME"
    verified = verified.replace(tzinfo=dt.UTC)
    taken = dt.datetime.strptime(backup_set[5:], "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.UTC)
    if not taken <= verified <= started or started - verified > BACKUP_MAX_AGE:
        return "PROOF_TIME"
    recorded = {
        parts[1].lstrip("*"): parts[0]
        for parts in (line.split(None, 1) for line in sums)
        if len(parts) == 2
    }
    sha = fields.get("database_sha256", "")
    if not sha or recorded.get("database.dump") != sha:
        return "PROOF_DUMP"
    digest = hashlib.sha256()
    try:
        with (root / "database.dump").open("rb") as dump:
            for block in iter(lambda: dump.read(1 << 20), b""):
                digest.update(block)
    except OSError:
        return "PROOF_DUMP"
    return "" if digest.hexdigest() == sha else "PROOF_DUMP"


def run_sync(
    *,
    trigger: str,
    transport: Transport | None = None,
    backup_set: str = "",
    now: Callable[[], dt.datetime] | None = None,
    pause: Callable[[float], None] | None = None,
    max_changed: int | None = None,
    max_restored: int | None = None,
) -> LovableSyncRun:
    """One run, end to end. Returns the ledger row; never raises after it exists.

    ``max_changed`` raises the night's Teemad-written breaker (`MAX_CHANGED_MATTERS`)
    for this one supervised run only — the first source-authoritative
    convergence writes far more than a night's work (docs/adr/0213 §10). It is
    recorded in the run's counts (``breaker_limit``); no stored setting changes.
    ``max_restored`` does the same for the restore breaker (`MAX_RESTORED_MATTERS`,
    recorded as ``restore_limit``): the night stays all-or-nothing for restores,
    and one supervised run with a higher limit is how a held backlog drains.

    Both are a person's decision for one run: only on a MANUAL run, never
    negative, and ``None`` (not ``0``) means the approved limit — ``0`` is a
    limit of zero (audit 2026-11-11 LS-06). A scheduled run or a check with an
    override is refused before anything is read."""
    for name, value in (("max_changed", max_changed), ("max_restored", max_restored)):
        if value is None:
            continue
        if trigger != SyncTrigger.MANUAL:
            raise SyncRefused(
                "OVERRIDE_NOT_MANUAL",
                f"{name} is a person's decision for one supervised run; a {trigger} run "
                "keeps the approved limits.",
            )
        if value < 0:
            raise SyncRefused("OVERRIDE_NEGATIVE", f"{name} cannot be negative ({value}).")
    clock = now or timezone.now
    ctl = control()
    if ctl.cut_over:
        raise SyncRefused(
            "CUT_OVER", "Final cutover is recorded; the synchronisation is disabled for good."
        )
    if trigger != SyncTrigger.CHECK and ctl.mode == SyncMode.OFF:
        raise SyncRefused("PAUSED", "The synchronisation is paused (mode OFF).")
    if trigger != SyncTrigger.CHECK and transport is None:
        raise SyncRefused("NO_SOURCE", "A run that reads Lovable needs the source connection.")
    started = clock()
    key = run_key_for(trigger, started)
    if trigger == SyncTrigger.CHECK:
        last_check = (
            LovableSyncRun.objects.filter(trigger=SyncTrigger.CHECK).order_by("-started_at").first()
        )
        if last_check is not None and started - last_check.started_at < CHECK_MIN_INTERVAL:
            raise SyncRefused(
                "CHECK_TOO_SOON",
                "A check ran less than a minute ago; its findings are on the page.",
            )
    if LovableSyncRun.objects.filter(run_key=key).exists():
        raise SyncRefused(
            "DUPLICATE", f"Run {key} already exists; the nightly run happens once per date."
        )

    # A named set is accepted only with its verification's proof (DR-05).
    backup_problem = backup_set_problem(backup_set, started) if backup_set else ""
    with run_lock():
        mode = SyncMode.COMPARE if trigger == SyncTrigger.CHECK else ctl.mode
        if trigger == SyncTrigger.CHECK:
            same = _identical_check(clock)
            if same is not None:
                return same
        try:
            with transaction.atomic():
                run = LovableSyncRun.objects.create(
                    run_key=key,
                    trigger=trigger,
                    mode=mode,
                    started_at=started,
                    mapping_version=MAPPING_VERSION,
                    backup_set="" if backup_problem else backup_set,
                )
        except IntegrityError as error:
            raise SyncRefused("DUPLICATE", f"Run {key} already exists.") from error
        if backup_problem:
            # Named but not proven: the run goes on and writes nothing (NO_BACKUP);
            # the reason is the run's error once it finishes (DR-05).
            logger.warning("Lovable sync run %s: backup set refused (%s)", run.pk, backup_problem)
        try:
            _execute(
                run,
                transport=transport,
                clock=clock,
                pause=pause,
                max_changed=max_changed,
                max_restored=max_restored,
                backup_problem=backup_problem,
            )
        except (SourceError, SyncRefused) as error:
            _fail(run, error.code, str(error), clock)
        except Exception as error:
            logger.error("Lovable sync run %s failed (%s)", run.pk, type(error).__name__)
            _fail(run, "INTERNAL", type(error).__name__, clock)
        return run


def _fail(run: LovableSyncRun, code: str, message: str, clock: Callable[[], dt.datetime]) -> None:
    """A failed run: the reason, where it stopped and the Teemad it was writing
    (references only). Nothing recorded after the failure stays: the run's
    bookkeeping — converged baselines, decisions, observations, the new-topic
    ledger — is one transaction (`_execute`), so a failed run never advances a
    baseline it did not write, and the next run simply tries again."""
    progress = getattr(run, "progress", None) or {}
    where = ""
    if progress:
        affected = ",".join(progress.get("affected", [])[:8])
        where = f" [stage={progress.get('stage', '')}{' affected=' + affected if affected else ''}]"
    run.status = SyncRunStatus.FAILED
    run.error = f"{code}: {message}"[: 300 - len(where)] + where
    run.finished_at = clock()
    run.save(update_fields=["status", "error", "finished_at", "updated_at"])


def _with_new_topics(snapshot: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """The run's reading with the new topics' own consistent reading beside the
    cohort (docs/adr/0213): their rows and children, never another topic's, and
    a digest over the whole."""
    from app.legacy_import.integrity import canonical_sha256

    document = {key: value for key, value in snapshot.items() if key != "sha256"}
    document["new_topics"] = {
        "taken_at": extra["taken_at"],
        "attempts": extra.get("attempts"),
        "sha256": extra["sha256"],
        "cohort": extra["cohort"],
    }
    document["sha256"] = canonical_sha256(document)
    return document


#: A Teema created tonight is brought to the source's current step and sent
#: status in the same run (docs/adr/0213 §10): the reader writes a minimal Teema
#: (`lovable_reading.read_topic` holds a free-text step and a sent status), and
#: source authority then mirrors them through the same rules as every other
#: Teema. A topic created before this rule converges on its next run anyway.
CONVERGE_NEW_TOPICS = True


def _converge_created(
    matter: Any,
    *,
    run: LovableSyncRun,
    snapshot: dict[str, Any],
    resolver: OwnerResolver,
    today: dt.date,
) -> MatterPlan:
    """A Teema created tonight, reconciled at once: its own lock, plan and
    read-back, exactly as a cohort Teema's. A failure leaves the created Teema
    as the reader wrote it (its next run converges it) and is counted."""
    state = LovableSyncState.objects.select_related("matter").get(matter_id=matter.pk)
    plan = MatterPlan(state=state)
    try:
        done = apply_matter(plan, run=run, snapshot=snapshot, resolver=resolver, today=today)
    except Exception as error:
        logger.warning(
            "Lovable sync: created %s not converged (%s)", state.reference, type(error).__name__
        )
        plan.status = SyncOutcome.FAILED
        return plan
    if any(d.outcome == SyncOutcome.APPLIED for d in done.decisions) and not verify_matter(done):
        done.verify_failed = True
    return done


def _execute(
    run: LovableSyncRun,
    *,
    transport: Transport | None,
    clock: Callable[[], dt.datetime],
    pause: Callable[[float], None] | None,
    max_changed: int | None = None,
    max_restored: int | None = None,
    backup_problem: str = "",
) -> None:
    from app.matters.models import Matter
    from app.search.indexing import refresh_matters

    progress: dict[str, Any] = {"stage": "population", "affected": []}
    run.progress = progress  # type: ignore[attr-defined]
    check = run.trigger == SyncTrigger.CHECK
    # `Kontrolli muudatusi` reads only: it seeds nothing, advances no baseline,
    # observes nothing and prunes nothing; it records its own findings, no more.
    states = population() if check else ensure_states()
    active = cohorts()
    problem = cohort_size_problem(states)
    if problem is not None:
        key, held, wanted = problem
        raise SyncRefused(
            "COHORT_SIZE",
            f"The synchronised cohort {key} holds {held} Teemad, not {wanted}.",
        )
    cohort_ids = {str(s.source_id) for s in states}
    # Only a run that finished without failing is a baseline (`is_baseline_run`).
    previous_snapshot = comparable_snapshots().first()

    progress["stage"] = "read"
    if run.trigger == SyncTrigger.CHECK:
        if previous_snapshot is None:
            raise SyncRefused("NO_SNAPSHOT", "No stored Lovable snapshot to compare against yet.")
        snapshot = previous_snapshot.document
        verify_snapshot(snapshot)
    else:
        # Every run that reads Lovable reads it now (a manual run too): a stored
        # reading is never applied (brief 2026-11-09 §7, «Sünkrooni nüüd»).
        if transport is None:
            raise SyncRefused("NO_SOURCE", "A run that reads Lovable needs the source connection.")
        snapshot = take_snapshot(
            transport,
            sorted(cohort_ids),
            mapping_version=MAPPING_VERSION,
            pause=pause or time.sleep,
        )
        verify_snapshot(snapshot)
    candidates = new_topic_candidates(snapshot.get("index") or [], cohort_ids)
    holds = _identity_holds(candidates)
    if not check:
        readable = [i for i in candidates if i not in holds]
        if readable:
            # The new topics' rows and children, read as consistently as the
            # cohort's (GET only, the same allowlist), beside it.
            extra = take_snapshot(
                transport,  # type: ignore[arg-type]
                readable,
                mapping_version=MAPPING_VERSION,
                pause=pause or time.sleep,
            )
            verify_snapshot(extra)
            snapshot = _with_new_topics(snapshot, extra)
    verify_snapshot(snapshot)
    run.snapshot_sha256 = snapshot["sha256"]
    run.snapshot_taken_at = dt.datetime.fromisoformat(snapshot["taken_at"])
    run.save(update_fields=["snapshot_sha256", "snapshot_taken_at", "updated_at"])

    progress["stage"] = "plan"
    today = clock().astimezone(TALLINN).date()
    resolver = OwnerResolver(states)
    # A check compares the stored reading; a cohort enrolled after it was taken
    # was never read there — «not yet read», never «missing» (ADR 0202 §6).
    unread = not_yet_read(states, previous_snapshot) if check and previous_snapshot else set()
    plans = [
        plan_matter(s, bundle_of(snapshot, s.source_id), resolver=resolver, today=today)
        for s in states
        if s.pk not in unread
    ]
    index_rows = {str(row["id"]): row for row in snapshot.get("index") or []}
    known = {i: str(index_rows[i].get("nr") or "") for i in cohort_ids if i in index_rows}
    detail = (snapshot.get("new_topics") or {}).get("cohort") or {}
    decided = {
        str(i)
        for i in LovableSyncNewTopic.objects.filter(status=NewTopicStatus.RESOLVED).values_list(
            "source_id", flat=True
        )
    }
    new_plans = [
        plan_new_topic(
            source_id,
            detail.get(source_id),
            index_row=index_rows[source_id],
            hold="RESOLVED" if source_id in decided else holds.get(source_id, ""),
            known=known,
            resolver=resolver,
            today=today,
            snapshot_sha256=snapshot["sha256"],
        )
        for source_id in candidates
    ]

    missing = sum(1 for p in plans if p.status == SyncOutcome.SOURCE_MISSING)
    if missing > MAX_MISSING:
        # Before the snapshot is stored: a broken read is never kept as a reading.
        raise SyncRefused(
            "COHORT_MISSING", f"{missing} synchronised Teemad are missing from the source read."
        )
    if not check and run.mode == SyncMode.APPLY and previous_snapshot is not None:
        # Before the snapshot is stored, as above: a population that moved this
        # much is never a night's work and never tomorrow's baseline (LS-07).
        moved = population_change(previous_snapshot.document, snapshot)
        if moved:
            raise SyncRefused("POPULATION_CHANGE", moved)
    if run.trigger != SyncTrigger.CHECK:
        LovableSyncSnapshot.objects.create(
            run=run, taken_at=run.snapshot_taken_at, sha256=snapshot["sha256"], document=snapshot
        )

    changed = [p for p in plans if any(d.reason != RESTORED for d in p.eligible)]
    restoring = [p for p in plans if p.eligible and p not in changed]
    # The previous run that finished and recorded its decisions: a failed run,
    # or one killed while RUNNING, recorded none (or only some), and would make
    # tonight's every hold look new — or none of them (review of #617).
    previous_run = (
        LovableSyncRun.objects.exclude(pk=run.pk)
        .exclude(trigger=SyncTrigger.CHECK)
        .filter(status__in=BASELINE_STATUSES)
        .order_by("-started_at")
        .first()
    )
    held = held_fields(plans)
    held_before = (
        previous_run.decisions.filter(outcome__in=HELD_OUTCOMES).exclude(field="*").count()
        if previous_run
        else held
    )
    change_limit = MAX_CHANGED_MATTERS if max_changed is None else max_changed
    hold = ""
    if run.mode != SyncMode.APPLY or check:
        hold = COMPARE_ONLY
    elif len(changed) > change_limit or held - held_before > MAX_NEW_HOLDS:
        hold = BREAKER
    elif not run.backup_set:
        hold = NO_BACKUP
    # Ordinary lawyer edits never stop the night: over their own limit, only the
    # restores wait (`RESTORE_BREAKER`); the source changes are written.
    # A Teema with a source change applies all of it, restores included: the
    # source change can depend on them (a lawyer's closure undone before Lovable's
    # new step is written). Only restore-only Teemad wait.
    restore_limit = MAX_RESTORED_MATTERS if max_restored is None else max_restored
    hold_restores = not hold and len(restoring) > restore_limit

    progress["stage"] = "apply"
    applied_ids: list[Any] = []
    verified = True
    final: list[MatterPlan] = []
    for plan in plans:
        if hold or not plan.eligible or (hold_restores and plan in restoring):
            final.append(plan)
            continue
        if not verified:
            # A read-back failed: the writer or the baseline logic is wrong, and
            # writing more Teemad with it is the opposite of a breaker. Every
            # later eligible change is recorded as not executed; nothing earlier
            # is rolled back (ADR 0192 §7).
            plan.not_executed = True
            for decision in plan.eligible:
                decision.reason = VERIFY_STOPPED
            final.append(plan)
            continue
        progress["affected"] = [plan.state.reference]
        try:
            done = apply_matter(
                plan,
                run=run,
                snapshot=snapshot,
                resolver=resolver,
                today=today,
            )
        except Exception as error:
            logger.warning(
                "Lovable sync: %s not applied (%s)", plan.state.reference, type(error).__name__
            )
            plan.status = SyncOutcome.FAILED
            final.append(plan)
            continue
        final.append(done)
        if any(d.outcome == SyncOutcome.APPLIED for d in done.decisions):
            applied_ids.append(done.state.matter_id)
            if not verify_matter(done):
                done.verify_failed = True
                verified = False
    progress["affected"] = []

    # New topics (docs/adr/0213): created only on an APPLY night that writes,
    # with every field breaker clear and every read-back good, and under their
    # own breaker; otherwise planned and counted.
    progress["stage"] = "new_topics"
    creatable = [p for p in new_plans if p.creatable]
    new_hold = hold or ("" if verified else VERIFY_STOPPED)
    if not new_hold and len(creatable) > MAX_NEW_TOPICS:
        new_hold = NEW_BREAKER
    target = next((c for c in active if c.key == NEW_TOPIC_COHORT), None)
    if not new_hold and creatable and target is None:
        for p in creatable:
            p.reason = NEW_NO_COHORT
    elif not new_hold:
        for p in creatable:
            if not verified:
                # A created Teema did not read back as written: no further one is
                # created with the same writer, exactly as the cohort loop stops
                # (ADR 0192 §7; audit 2026-11-11 LS-08). Held, and planned again
                # on the next run.
                p.reason = VERIFY_STOPPED
                continue
            progress["affected"] = [p.reference]
            try:
                matter = create_new_topic(p, run=run, cohort=target, snapshot=snapshot)  # type: ignore[arg-type]
            except Exception as error:
                logger.warning(
                    "Lovable sync: new topic %s not created (%s)", p.reference, type(error).__name__
                )
                p.failed, p.reason = True, NEW_FAILED
                continue
            p.created_matter_id = matter.pk
            applied_ids.append(matter.pk)
            if CONVERGE_NEW_TOPICS:
                converged = _converge_created(
                    matter, run=run, snapshot=snapshot, resolver=resolver, today=today
                )
                final.append(converged)
                if converged.verify_failed:
                    verified = False
        progress["affected"] = []

    # A Teema whose successor was created tonight converges tonight (audit
    # 2026-11-11 LS-01 / ST-007): the cohort was planned before the creation, so
    # it was held SUCCESSOR_NOT_IN_JURISTID. Only those plans are planned again,
    # through `apply_matter` under the same gates — an APPLY night that writes,
    # every read-back good so far, the Teemad-written breaker counting them —
    # and read back; creation is never moved before the cohort.
    created = {p.source_id for p in new_plans if p.created_matter_id is not None}
    counted = {p.state.pk for p in changed}
    teemad_written = len(changed)
    if created and not hold:
        progress["stage"] = "successors"
        for index, plan in enumerate(final):
            if not verified:
                break
            if not _waits_for_created_successor(plan, created):
                continue
            more = 0 if plan.state.pk in counted else 1
            if teemad_written + more > change_limit:
                # Over the night's limit: it waits for the next run, as before.
                continue
            progress["affected"] = [plan.state.reference]
            state = LovableSyncState.objects.select_related("matter").get(pk=plan.state.pk)
            again = plan_matter(
                state, bundle_of(snapshot, state.source_id), resolver=resolver, today=today
            )
            if not again.eligible:
                continue
            try:
                done = apply_matter(
                    again, run=run, snapshot=snapshot, resolver=resolver, today=today
                )
            except Exception as error:
                logger.warning(
                    "Lovable sync: %s not converged with its new successor (%s)",
                    plan.state.reference,
                    type(error).__name__,
                )
                again.status = SyncOutcome.FAILED
                final[index] = again
                continue
            teemad_written += more
            counted.add(plan.state.pk)
            # What this Teema already wrote tonight stays its record.
            earlier = [d for d in plan.decisions if d.outcome == SyncOutcome.APPLIED]
            kept = {d.field for d in earlier}
            done.decisions = earlier + [d for d in done.decisions if d.field not in kept]
            final[index] = done
            if any(d.outcome == SyncOutcome.APPLIED for d in done.decisions):
                applied_ids.append(done.state.matter_id)
                if not verify_matter(done):
                    done.verify_failed = True
                    verified = False
        progress["affected"] = []

    search_pending = 0
    # Teemad an earlier run wrote but could not refresh in search (LS-09): this
    # run refreshes them with its own.
    carried = set() if check else stale_search_matter_ids(exclude=run)
    to_refresh = set(applied_ids) | carried
    if to_refresh:
        try:
            refresh_matters(Matter.objects.filter(pk__in=to_refresh))
        except Exception as error:  # the writes are committed; their record must be too
            # As `onenote_capture` does: a search-projection failure never throws
            # away the run's counts, decisions, baselines or new-topic ledger
            # (audit 2026-11-11 LS-09). The Teemad stay written and verified;
            # their search rows are stale until `manage.py refresh_matter_search`
            # or the next run that reads Lovable refreshes them
            # (`stale_search_matter_ids`); `sync_health` says so meanwhile.
            search_pending = len(to_refresh)
            logger.error(
                "Lovable sync run %s: search refresh failed for %s Teema(d) (%s)",
                run.pk,
                search_pending,
                type(error).__name__,
            )

    # The bookkeeping, all or nothing: a failure here leaves every baseline,
    # decision and observation as the previous run left them.
    progress["stage"] = "record"
    with transaction.atomic():
        # Converged fields advance their baseline even on a compare-only night: no
        # business write, only the record of what Lovable and Juristid agree on.
        for plan in final:
            if check or plan.status or plan.not_executed or plan.state.pk is None:
                continue
            state = LovableSyncState.objects.select_for_update().get(pk=plan.state.pk)
            applied = dict(state.applied)
            authoritative = is_authoritative(plan.state)
            for decision in plan.decisions:
                if decision.outcome == SyncOutcome.ALREADY_CONVERGED or decision.settle:
                    if authoritative:
                        _mirror(applied, decision, decision.target, matter_id=plan.state.matter_id)
                    else:
                        _advance(applied, decision)
            request_entry = applied.get("opinion_deadline")
            if (
                authoritative
                and isinstance(request_entry, dict)
                and REQUESTED_AT not in request_entry
            ):
                # The request's identity, recorded on the first authoritative run
                # whatever else happened tonight (only the key; A and the target
                # stay as they are).
                applied["opinion_deadline"] = {
                    **request_entry,
                    REQUESTED_AT: _iso_moment(
                        Matter.all_objects.filter(pk=plan.state.matter_id)
                        .values_list("response_requested_at", flat=True)
                        .first()
                    ),
                }
            if applied != state.applied:
                LovableSyncState.objects.filter(pk=state.pk).update(
                    applied=applied, updated_at=timezone.now()
                )
            plan.state.applied = applied

        counts = RunCounts(
            by_cohort={c.key: cohort_counts(c.required, c.retired) for c in active},
            retired=sum(c.retired for c in active),
        )
        for state in states:
            if state.pk in unread:
                counts.not_yet_read += 1
                counts.by_cohort[state.cohort]["not_yet_read"] += 1
        for plan in final:
            counts.add(plan)
            _record(run, plan)
            if not plan.status and not check:
                _observe(LovableSyncState.objects.get(pk=plan.state.pk), plan, run)
        _observe_index(
            counts, snapshot, cohort_ids, previous_snapshot.document if previous_snapshot else None
        )
        counts.add_new_topics(new_plans)
        counts.breaker_limit = change_limit
        counts.held_fields = held
        counts.held_fields_before = held_before
        if search_pending:
            counts.search_refresh_pending = search_pending
            counts.warnings.append(SEARCH_REFRESH_FAILED)
        elif carried:
            counts.search_refreshed_carried = len(carried)
        counts.changed_source = teemad_written
        counts.changed_restores = len(restoring)
        counts.restore_limit = restore_limit
        counts.standing_holds = standing_holds(final)
        if not check:
            _record_new_topics(run, new_plans, seen_at=run.started_at)
        finished = clock()
        counts.duration_seconds = round((finished - run.started_at).total_seconds(), 1)

        run.counts = counts.as_dict()
        run.verified = verified
        run.finished_at = finished
        if not verified:
            run.status = SyncRunStatus.FAILED
            run.error = (
                "VERIFY: an applied Teema does not read back as written; the run stopped writing"
                f" ({counts.not_executed} Teema(d) not written after it)."
            )
        elif counts.failed or counts.new_topics_failed:
            # The others were written and verified; the run still is not a success.
            run.status = SyncRunStatus.FAILED
            run.error = f"{PARTIAL}: {counts.failed} Teema(d) not applied; nothing half-written."
            if counts.new_topics_failed:
                run.error += f" New topics not created: {counts.new_topics_failed}."
        elif hold in (BREAKER, NO_BACKUP) or new_hold == NEW_BREAKER or hold_restores:
            run.status = SyncRunStatus.REVIEW_ONLY
            run.error = (
                hold
                if hold in (BREAKER, NO_BACKUP)
                else (NEW_BREAKER if new_hold == NEW_BREAKER else RESTORE_BREAKER)
            )
            if hold == NO_BACKUP and backup_problem:
                run.error = f"{NO_BACKUP}: {backup_problem}"
        else:
            run.status = SyncRunStatus.SUCCEEDED
            run.error = ""
        run.save(
            update_fields=["counts", "verified", "finished_at", "status", "error", "updated_at"]
        )
    if not check:
        prune_snapshots(now=finished)


def _findings(run_or_plans: Any) -> set[tuple[Any, ...]]:
    """What a check records, as comparable values."""
    if isinstance(run_or_plans, LovableSyncRun):
        return {
            (
                str(d.state_id),
                d.field,
                d.outcome,
                d.reason,
                _j(d.baseline),
                _j(d.current),
                _j(d.source),
            )
            for d in run_or_plans.decisions.all()
        }
    found: set[tuple[Any, ...]] = set()
    for plan in run_or_plans:
        for d in plan.decisions:
            if d.outcome not in (SyncOutcome.UNCHANGED, SyncOutcome.APPLIED):
                found.add(
                    (
                        str(plan.state.pk),
                        d.field,
                        d.outcome,
                        d.reason,
                        _j(d.baseline),
                        _j(d.current),
                        _j(d.source),
                    )
                )
        if plan.status:
            found.add((str(plan.state.pk), "*", plan.status, "", _j(None), _j(None), _j(None)))
    return found


def _j(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _identical_check(clock: Callable[[], dt.datetime]) -> LovableSyncRun | None:
    """The newest run, when it is a successful check of the same stored reading
    whose findings are exactly what a check now would record — so pressing
    `Kontrolli muudatusi` again adds no second, identical ledger entry. Reads
    only; never deletes or edits a recorded run."""
    latest = LovableSyncRun.objects.order_by("-started_at").first()
    snapshot = comparable_snapshots().first()
    if (
        latest is None
        or snapshot is None
        or latest.trigger != SyncTrigger.CHECK
        or latest.status != SyncRunStatus.SUCCEEDED
        or latest.snapshot_sha256 != snapshot.sha256
    ):
        return None
    try:
        verify_snapshot(snapshot.document)
    except SourceError:
        return None
    states = population()
    if cohort_size_problem(states) is not None:
        return None
    today = clock().astimezone(TALLINN).date()
    resolver = OwnerResolver(states)
    unread = not_yet_read(states, snapshot)
    plans = [
        plan_matter(s, bundle_of(snapshot.document, s.source_id), resolver=resolver, today=today)
        for s in states
        if s.pk not in unread
    ]
    return latest if _findings(plans) == _findings(latest) else None


def prune_snapshots(*, now: dt.datetime) -> int:
    """Snapshots older than the approved retention go; the run ledger stays."""
    cutoff = now - dt.timedelta(days=SNAPSHOT_RETENTION_DAYS)
    latest = LovableSyncSnapshot.objects.order_by("-taken_at").values_list("pk", flat=True).first()
    # The newest baseline too: after failed nights it is what a check compares with.
    baseline = comparable_snapshots().values_list("pk", flat=True).first()
    stale = LovableSyncSnapshot.objects.filter(taken_at__lt=cutoff)
    keep = [pk for pk in (latest, baseline) if pk is not None]
    if keep:
        stale = stale.exclude(pk__in=keep)
    deleted, _ = stale.delete()
    return deleted


def resolve_field(
    *, reference: str, field_name: str, note: str, actor: Any = None
) -> LovableSyncDecision:
    """An operator's resolution of a held change: the baseline takes the last
    observed source value, and the Teema keeps whatever Juristid holds. It is
    also the only thing that lifts the import's review hold (``import_held``,
    docs/adr/0202 §6) — for the step, from both its wording and its day.

    ``actor`` is the administrator who decided it from «Vaata erinevusi»
    (brief 2026-11-09 §7); the server command passes none. The decision row
    names the actor and the note, so who decided what, when, is on record.
    Nothing in the Teema is written: a decision moves the baseline only."""
    if field_name not in RESOLVABLE_FIELDS:
        raise SyncRefused("UNKNOWN_FIELD", f"Unknown field {field_name!r}.")
    note = text_value(note)
    if not note:
        raise SyncRefused("NO_NOTE", "A decision needs a note saying why.")
    with transaction.atomic():
        state = (
            LovableSyncState.objects.select_for_update()
            .select_related("matter")
            .get(reference=reference)
        )
        if field_name not in state.observed:
            raise SyncRefused("NOT_OBSERVED", "Nothing observed for that field yet.")
        run = state.observed_run
        if run is None:
            raise SyncRefused("NOT_OBSERVED", "Nothing observed yet.")
        applied = dict(state.applied)
        b = full_values(state.matter)
        value = state.observed[field_name]
        before = (applied.get(field_name) or {}).get("source")
        if field_name in STEP_FIELDS:
            applied[field_name] = {"source": value}
            # One operation: resolving either half lifts the import's hold on both.
            for half in STEP_FIELDS:
                if half != field_name and half in applied:
                    applied[half] = {k: v for k, v in applied[half].items() if k != "import_held"}
            step = b["next_action"]
            # The step Juristid keeps is the source's only if it reads as the
            # source's sentence; otherwise a later day change of that sentence
            # must never move the lawyer's own step.
            source_wording = bool(step) and text_value(step["text"]) == text_value(
                state.observed.get("next_action_text")
            )
            applied["next_action"] = {
                "target": step,
                "treatment": "SOURCE_NEXT_STEP" if source_wording else "RESOLVED_LOCAL",
            }
        elif field_name == "owner":
            # Who kept the file, not who the name is: never taught to the resolver.
            applied[field_name] = {"source": value, "target": b[field_name], "resolved": True}
        elif field_name in ("title", "onenote_link", "stage", "commencements", CONTINUATION):
            # What Juristid keeps is the target: unchanged afterwards, it is
            # unchanged, never a local change.
            applied[field_name] = {"source": value, "target": b[field_name]}
        else:
            applied[field_name] = {"source": value}
        state.applied = applied
        state.save(update_fields=["applied", "updated_at"])
        return LovableSyncDecision.objects.create(
            run=run,
            state=state,
            field=field_name,
            outcome=SyncOutcome.ALREADY_CONVERGED,
            reason=f"{RESOLVED_PREFIX} {note}"[:60],
            baseline=before,
            source=value,
            current=b.get(field_name),
            actor=actor,
        )


@dataclass(frozen=True)
class Enrolment:
    """What `enroll` did, in counts only."""

    cohort: str
    expected: int
    seeded: int
    import_held_fields: int
    already: bool = False
    #: Rebased Teemad whose state was archived and re-seeded in place.
    reseeded: int = 0
    #: The cohort the enrolment retired (`--supersede`), or "".
    superseded: str = ""


def enroll(
    *,
    cohort: str,
    package_sha256: str,
    expect: int,
    reason: str,
    supersede: str = "",
    replaces: Iterable[str] = (),
) -> Enrolment:
    """Enrol an imported cohort in the nightly comparison (docs/adr/0202 §6).

    The cohort's Teemad are exactly the Matters its approved provenance names
    (`EvidencePilotRecord(pilot=cohort, package_sha256=…, record_kind=MATTER or
    REBASE)`), each with exactly one canonical Lovable source reference written by
    the package. Everything happens in one transaction, under the run lock, and
    no Teema is written.

    * A created Teema gets a state seeded from its import (`seed_state`).
    * A rebased pilot Teema keeps its state row: with ``supersede`` naming the
      pilot, its baseline and last observation are archived
      (`LovableSyncBaselineArchive`) and the same row is re-seeded from the
      rebuild's reference (`reseed_state`). Every pilot state must be rebased,
      and the pilot is retired. Without ``supersede`` a Teema that already has a
      state refuses the enrolment.
    * The run and decision ledger is never touched.

    The same enrolment again is a no-op."""
    from app.legacy_import.models import EvidencePilotRecord

    reason = text_value(reason)
    if cohort == PILOT_COHORT:
        raise SyncRefused("PILOT_BUILT_IN", "The pilot is the built-in cohort; it is not enrolled.")
    if cohort not in ENROLLABLE_COHORTS:
        raise SyncRefused("UNKNOWN_COHORT", f"Unknown cohort {cohort!r}.")
    if supersede and supersede not in SUPERSEDABLE_COHORTS:
        raise SyncRefused("UNKNOWN_SUPERSEDE", f"Cohort {supersede!r} cannot be superseded.")
    if not re.fullmatch(r"[0-9a-f]{64}", package_sha256 or ""):
        raise SyncRefused("BAD_DIGEST", "The package digest must be 64 lower-case hex digits.")
    if expect <= 0:
        raise SyncRefused("BAD_EXPECT", "The expected number of Teemad must be positive.")
    if not reason:
        raise SyncRefused("NO_REASON", "An enrolment needs a reason.")
    ctl = LovableSyncControl.objects.filter(pk=1).first()
    if ctl is not None and ctl.cut_over:
        raise SyncRefused(
            "CUT_OVER", "Final cutover is recorded; the synchronisation never runs again."
        )
    replaced = sorted({key.strip() for key in replaces if key and key.strip()})
    if cohort == REGISTER_COHORT and not replaced:
        raise SyncRefused(
            "REPLACES_REQUIRED",
            "The register replaces every earlier cohort: name them with --replaces.",
        )
    if replaced and cohort != REGISTER_COHORT:
        raise SyncRefused(
            "REPLACES_ONLY_REGISTER",
            f"Only the register ({REGISTER_COHORT}) replaces earlier cohorts.",
        )
    if replaced:
        if supersede:
            raise SyncRefused(
                "SUPERSEDE_AND_REPLACE", "A replacement replaces cohorts; it supersedes none."
            )
        return _enroll_replacement(
            cohort=cohort,
            package_sha256=package_sha256,
            expect=expect,
            reason=reason,
            replaced=replaced,
        )
    with run_lock(), transaction.atomic():
        existing = LovableSyncCohort.objects.select_for_update().filter(key=cohort).first()
        if existing is not None:
            if existing.package_sha256 == package_sha256 and existing.expected == expect:
                return Enrolment(
                    cohort,
                    expect,
                    LovableSyncState.objects.filter(cohort=cohort).count(),
                    0,
                    already=True,
                    superseded=supersede,
                )
            raise SyncRefused(
                "COHORT_ENROLLED", f"Cohort {cohort} is already enrolled with another package."
            )
        if (
            supersede
            and LovableSyncCohort.objects.filter(key=supersede, retired_at__isnull=False).exists()
        ):
            raise SyncRefused("ALREADY_RETIRED", f"Cohort {supersede} is already retired.")
        target = Cohort(cohort, cohort, package_sha256, expect)
        members = list(
            EvidencePilotRecord.objects.filter(
                pilot=cohort, package_sha256=package_sha256, record_kind__in=MEMBER_KINDS
            ).values_list("matter_id", "record_kind")
        )
        matter_ids = [matter for matter, _ in members]
        rebased = {matter for matter, kind in members if kind == "REBASE"}
        if len(matter_ids) != expect or len(set(matter_ids)) != expect:
            raise SyncRefused(
                "COUNT_MISMATCH",
                f"The package's provenance names {len(set(matter_ids))} Teemad, not {expect}.",
            )
        references: dict[Any, list[Any]] = {}
        for reference in cohort_references(target):
            references.setdefault(reference.matter_id, []).append(reference)
        source_ids: dict[Any, str] = {}
        for matter_id in matter_ids:
            found = references.get(matter_id, [])
            source_id = (
                ((found[0].source_row_raw or {}).get("lovable") or {}).get("id")
                if len(found) == 1
                else None
            )
            if not source_id:
                raise SyncRefused(
                    "NO_CANONICAL_SOURCE",
                    f"{len(found)} canonical Lovable source references (with a Lovable id) "
                    "for one of the cohort's Teemad, not exactly one.",
                )
            source_ids[matter_id] = str(source_id)
        if len(set(source_ids.values())) != len(source_ids):
            raise SyncRefused("DUPLICATE_SOURCE", "Two of the cohort's Teemad name one source.")

        superseded: dict[Any, LovableSyncState] = {}
        if supersede:
            old = list(LovableSyncState.objects.select_for_update().filter(cohort=supersede))
            missing = [s for s in old if s.matter_id not in rebased]
            if missing:
                raise SyncRefused(
                    "SUPERSEDE_INCOMPLETE",
                    f"{len(missing)} Teemad of {supersede} are not rebased by this package.",
                )
            for state in old:
                if str(state.source_id) != source_ids[state.matter_id]:
                    raise SyncRefused(
                        "IDENTITY_MISMATCH",
                        "A rebased Teema's new reference names another Lovable topic.",
                    )
            superseded = {state.matter_id: state for state in old}
            _refuse_runs_since_rebuild(references, rebased)
        taken = (
            LovableSyncState.objects.filter(
                Q(source_id__in=list(source_ids.values())) | Q(matter_id__in=matter_ids)
            )
            .exclude(pk__in=[s.pk for s in superseded.values()])
            .count()
        )
        if taken:
            raise SyncRefused(
                "ALREADY_SYNCHRONISED",
                f"{taken} of the cohort's Teemad are already synchronised (in another cohort).",
            )

        now = timezone.now()
        LovableSyncCohort.objects.create(
            key=cohort,
            pilot=cohort,
            package_sha256=package_sha256,
            expected=expect,
            enrolled_at=now,
            reason=reason[:300],
        )
        held = reseeded = 0
        # Owner names resolve as the nightly run resolves them, through the
        # pairs the pilot's reviewed people map produced.
        resolver = OwnerResolver(superseded.values())
        archive_reason = f"Asendatud kohordiga {cohort} (pakett {package_sha256}): {reason}"
        for matter_id in matter_ids:
            reference = references[matter_id][0]
            if matter_id in superseded:
                reseed_state(
                    superseded[matter_id],
                    reference,
                    target,
                    reason=archive_reason,
                    resolver=resolver,
                )
                applied = LovableSyncState.objects.get(pk=superseded[matter_id].pk).applied
                reseeded += 1
            else:
                applied = seed_state(reference, target).applied
            held += len(held_by_import(applied))
        if supersede:
            first = (
                LovableSyncState.objects.filter(pk__in=[s.pk for s in superseded.values()])
                .order_by("created_at")
                .values_list("created_at", flat=True)
                .first()
            )
            LovableSyncCohort.objects.update_or_create(
                key=supersede,
                defaults={
                    "pilot": supersede,
                    "package_sha256": "",
                    "expected": EXPECTED_COHORT if supersede == PILOT_COHORT else len(superseded),
                    "enrolled_at": first or now,
                    "reason": "Sisseehitatud piloodikohort (docs/adr/0192).",
                    "retired_at": now,
                    "retired_reason": archive_reason[:300],
                },
            )
    return Enrolment(
        cohort,
        expect,
        len(matter_ids),
        held,
        reseeded=reseeded,
        superseded=supersede,
    )


def _enroll_replacement(
    *, cohort: str, package_sha256: str, expect: int, reason: str, replaced: list[str]
) -> Enrolment:
    """The source-authoritative replacement's enrolment (docs/adr/0203 §3).

    The replacement made every earlier Teema a tombstone (`delete_matter`, which
    removes their states, decisions and archives with them) and created the
    register as new Teemad. This enrols exactly those — the package's MATTER
    records — and retires every cohort it replaces; it deletes nothing (a
    state left on a tombstone with one of the register's ids refuses it). Refused
    unless every active cohort is replaced, no replaced state is still on a live
    Teema, each new Teema is live with exactly one canonical reference and a
    Lovable id no live state holds, and no night reconciled since the
    replacement. The same enrolment again is a no-op."""
    from app.legacy_import.models import EvidencePilotRecord, ImportBatch
    from app.matters.models import Matter

    with run_lock(), transaction.atomic():
        existing = LovableSyncCohort.objects.select_for_update().filter(key=cohort).first()
        if existing is not None:
            if existing.package_sha256 == package_sha256 and existing.expected == expect:
                return Enrolment(
                    cohort,
                    expect,
                    LovableSyncState.objects.filter(cohort=cohort).count(),
                    0,
                    already=True,
                )
            raise SyncRefused(
                "COHORT_ENROLLED", f"Cohort {cohort} is already enrolled with another package."
            )
        rows = {row.key: row for row in LovableSyncCohort.objects.select_for_update()}
        known = {PILOT_COHORT, *rows}
        unknown = [key for key in replaced if key not in known or key == cohort]
        if unknown:
            raise SyncRefused("UNKNOWN_REPLACES", f"Unknown cohort(s) to replace: {unknown}.")
        active = {c.key for c in cohorts()}
        left = sorted(active - set(replaced))
        if left:
            raise SyncRefused(
                "REPLACES_INCOMPLETE",
                f"Active cohort(s) {left} are not replaced; the register must be the only one.",
            )
        live = set(Matter.objects.values_list("pk", flat=True))
        remaining = LovableSyncState.objects.filter(cohort__in=replaced, matter_id__in=live)
        if remaining.exists():
            raise SyncRefused(
                "LIVE_STATE_REMAINS",
                f"{remaining.count()} state(s) of a replaced cohort are on a live Teema; "
                "the replacement did not retire them.",
            )

        target = Cohort(cohort, cohort, package_sha256, expect)
        matter_ids = list(
            EvidencePilotRecord.objects.filter(
                pilot=cohort, package_sha256=package_sha256, record_kind="MATTER"
            ).values_list("matter_id", flat=True)
        )
        if len(matter_ids) != expect or len(set(matter_ids)) != expect:
            raise SyncRefused(
                "COUNT_MISMATCH",
                f"The package's provenance names {len(set(matter_ids))} Teemad, not {expect}.",
            )
        if set(matter_ids) - live:
            raise SyncRefused("NOT_LIVE", "A Teema of the replacement is not live.")
        references: dict[Any, list[Any]] = {}
        for reference in cohort_references(target):
            references.setdefault(reference.matter_id, []).append(reference)
        source_ids: dict[Any, str] = {}
        for matter_id in matter_ids:
            found = references.get(matter_id, [])
            source_id = (
                ((found[0].source_row_raw or {}).get("lovable") or {}).get("id")
                if len(found) == 1
                else None
            )
            if not source_id:
                raise SyncRefused(
                    "NO_CANONICAL_SOURCE",
                    f"{len(found)} canonical Lovable source references (with a Lovable id) "
                    "for one of the cohort's Teemad, not exactly one.",
                )
            source_ids[matter_id] = str(source_id)
        if len(set(source_ids.values())) != len(source_ids):
            raise SyncRefused("DUPLICATE_SOURCE", "Two of the cohort's Teemad name one source.")
        held_live = LovableSyncState.objects.filter(
            Q(source_id__in=list(source_ids.values())) | Q(matter_id__in=matter_ids),
            matter_id__in=live,
        ).count()
        if held_live:
            raise SyncRefused(
                "ALREADY_SYNCHRONISED",
                f"{held_live} of the cohort's Teemad or Lovable ids are already synchronised.",
            )
        batches = {found[0].import_batch_id for found in references.values() if found}
        finished = [
            b.finished_at or b.started_at
            for b in ImportBatch.objects.filter(pk__in=batches)
            if b.finished_at or b.started_at
        ]
        if finished:
            runs = (
                LovableSyncRun.objects.exclude(trigger=SyncTrigger.CHECK)
                .filter(started_at__gt=min(finished))
                .count()
            )
            if runs:
                raise SyncRefused(
                    "SYNC_RAN_SINCE_REBUILD",
                    f"{runs} synchronisation run(s) started after the replacement was "
                    "applied. Restore the pre-replacement backup set and enrol before the next "
                    "night, or have the owner decide; nothing was enrolled.",
                )
        # A replaced state still on a tombstone (the deletion walk keeps none
        # today) would hold one of the register's Lovable ids. Enrolment deletes
        # nothing: it refuses, naming only the states' references.
        stale = sorted(
            LovableSyncState.objects.filter(source_id__in=list(source_ids.values()))
            .exclude(matter_id__in=live)
            .values_list("reference", flat=True)
        )
        if stale:
            raise SyncRefused(
                "STALE_STATE_ON_TOMBSTONE",
                f"Synchronisation state(s) {', '.join(stale)} of a retired Teema still hold a "
                "Lovable id of the register; nothing was enrolled.",
            )

        now = timezone.now()
        retired = f"Asendatud kohordiga {cohort} (pakett {package_sha256}): {reason}"[:300]
        for key in replaced:
            row = rows.get(key)
            if row is None:
                # The built-in pilot, never rebuilt here: its row records the retirement.
                LovableSyncCohort.objects.create(
                    key=key,
                    pilot=key,
                    package_sha256="",
                    expected=EXPECTED_COHORT,
                    enrolled_at=now,
                    reason="Sisseehitatud piloodikohort (docs/adr/0192).",
                    retired_at=now,
                    retired_reason=retired,
                )
            elif row.retired_at is None:
                row.retired_at, row.retired_reason = now, retired
                row.save(update_fields=["retired_at", "retired_reason", "updated_at"])
        LovableSyncCohort.objects.create(
            key=cohort,
            pilot=cohort,
            package_sha256=package_sha256,
            expected=expect,
            enrolled_at=now,
            reason=reason[:300],
        )
        held = 0
        for matter_id in matter_ids:
            held += len(held_by_import(seed_state(references[matter_id][0], target).applied))
    return Enrolment(cohort, expect, len(matter_ids), held, superseded=",".join(replaced))


def _refuse_runs_since_rebuild(references: dict[Any, list[Any]], rebased: set[Any]) -> None:
    """A night that reconciled after the rebuild was applied has already compared
    the rebuild's writes with the pilot's baselines, and converged some of them
    into those baselines: archiving them now would archive a mixture. Refuse;
    the operator restores the pre-rebuild state or the owner decides."""
    batches = {
        found[0].import_batch_id
        for matter, found in references.items()
        if matter in rebased and found
    }
    from app.legacy_import.models import ImportBatch

    applied = [
        batch.finished_at or batch.started_at
        for batch in ImportBatch.objects.filter(pk__in=batches)
        if batch.finished_at or batch.started_at
    ]
    if not applied:
        return
    since = min(applied)
    runs = [
        run
        for run in LovableSyncRun.objects.exclude(trigger=SyncTrigger.CHECK).filter(
            started_at__gt=since
        )
        if run_reconciled(run)
    ]
    if runs:
        raise SyncRefused(
            "SYNC_RAN_SINCE_REBUILD",
            f"{len(runs)} synchronisation run(s) reconciled after the rebuild was applied "
            "and may have moved the pilot baselines. Restore the pre-rebuild backup set and "
            "enrol before the next night, or have the owner decide; nothing was enrolled.",
        )


def summary_lines(run: LovableSyncRun) -> list[str]:
    """The night in a few Estonian lines (docs/adr/0192 §9). Counts only."""
    c = run.counts or {}
    day = run.started_at.astimezone(TALLINN)
    lines = [f"Lovable sünkroonimine — {day:%d.%m.%Y}"]
    if run.status == SyncRunStatus.FAILED:
        lines.append(f"EBAÕNNESTUS: {run.error.split(':')[0]}")
        return lines
    lines += [
        f"Kontrollitud: {c.get('checked', 0)} teemat",
        f"Muutusteta: {c.get('unchanged', 0)}",
        f"Uuendatud: {c.get('updated', 0)}",
        f"Kohalikke testmuudatusi: {c.get('local_only', 0)}",
        f"Vastuolusid: {c.get('conflicts', 0)}",
        f"Vajab ülevaatamist: {c.get('review', 0)}",
        f"Vigu: {c.get('failed', 0)}",
    ]
    if c.get("not_yet_read"):
        lines.append(f"Veel lugemata (lisati pärast viimast lugemist): {c['not_yet_read']}")
    if c.get("retired"):
        lines.append(f"Kohortidest välja arvatud (teema jäädavalt kustutatud): {c['retired']}")
    if run.mode != SyncMode.APPLY or run.status == SyncRunStatus.REVIEW_ONLY:
        lines.append(f"Ohutuid uuendusi rakendamata: {c.get('eligible_unapplied', 0)}")
    if c.get("changed_source") or c.get("changed_restores"):
        lines.append(
            f"Lovable'i muudatusega teemasid: {c.get('changed_source', 0)}"
            f"/{c.get('breaker_limit', MAX_CHANGED_MATTERS)}; Lovable'i väärtuse taastamisi: "
            f"{c.get('changed_restores', 0)}/{c.get('restore_limit', MAX_RESTORED_MATTERS)}"
        )
    if c.get("new_topics_seen"):
        lines.append(
            f"Uusi Lovable'i teemasid: {c['new_topics_seen']} (loodud "
            f"{c.get('new_topics_created', 0)}, loomisel {c.get('new_topics_planned', 0)}, "
            f"vajab ülevaatamist {c.get('new_topics_held', 0)})"
        )
    by_cohort = c.get("by_cohort") or {}
    if by_cohort and set(by_cohort) != {PILOT_COHORT}:
        # Only when the population is not the pilot alone: the pilot reads as before.
        for key, part in by_cohort.items():
            lines.append(
                f"{LOVABLE_COHORT_LABELS.get(key, key)}: kontrollitud {part.get('checked', 0)}"
                f"/{part.get('expected') or 0}, vajab ülevaatamist {part.get('review', 0)}"
                f" (impordil ootele jäetud {part.get('import_held', 0)}), Lovable'is lõppenud"
                f" kuid Juristides avatud {part.get('stopped_active', 0)}"
            )
    return lines


#: A nightly job that has not succeeded for longer than this is stale, and says so.
STALE_AFTER = dt.timedelta(hours=26)


@dataclass(frozen=True)
class SyncHealth:
    ok: bool
    detail: str
    last_run: LovableSyncRun | None
    last_success: LovableSyncRun | None


def sync_health(*, now: dt.datetime | None = None) -> SyncHealth:
    """The operator's verdict: never an earlier success shown as if it were tonight's."""
    moment = now or timezone.now()
    ctl = control()
    runs = LovableSyncRun.objects.exclude(trigger=SyncTrigger.CHECK).order_by("-started_at")
    last, good = runs.first(), runs.filter(status=SyncRunStatus.SUCCEEDED).first()
    if ctl.cut_over:
        return SyncHealth(True, "cut over: disabled for good", last, good)
    if ctl.mode == SyncMode.OFF:
        return SyncHealth(True, "paused", last, good)
    if last is None:
        return SyncHealth(False, "enabled but has never run", last, good)
    if last.status == SyncRunStatus.FAILED:
        return SyncHealth(False, f"last run failed ({last.error.split(':')[0]})", last, good)
    if last.status == SyncRunStatus.REVIEW_ONLY:
        return SyncHealth(False, f"last run held for review ({last.error})", last, good)
    if last.status == SyncRunStatus.RUNNING and moment - last.started_at > dt.timedelta(hours=1):
        return SyncHealth(False, "a run started over an hour ago and never finished", last, good)
    if good is None or moment - good.started_at > STALE_AFTER:
        return SyncHealth(False, "no successful run in the last 26 hours", last, good)
    stale = (last.counts or {}).get("search_refresh_pending") or 0
    if stale:
        # Written and verified, but not findable yet (LS-09): the next run, or
        # `manage.py refresh_matter_search`, refreshes them.
        return SyncHealth(False, f"search stale: {stale} Teema(d) not refreshed", last, good)
    return SyncHealth(True, f"{ctl.mode.lower()}, last success {good.run_key}", last, good)


def due(*, trigger: str, now: dt.datetime | None = None) -> str:
    """``DUE APPLY``, ``DUE COMPARE`` or ``NOT_DUE <reason>``.

    The host's cron fires at 21:15 and 22:15 UTC; exactly one of them is 00:15 in
    Tallinn, summer or winter, and only that one is due. The run key then makes a
    second attempt on the same date refuse."""
    moment = now or timezone.now()
    ctl = control()
    if ctl.cut_over:
        return "NOT_DUE CUT_OVER"
    if ctl.mode == SyncMode.OFF:
        return "NOT_DUE PAUSED"
    if trigger == SyncTrigger.SCHEDULED:
        if moment.astimezone(TALLINN).hour != 0:
            return "NOT_DUE NOT_MIDNIGHT_IN_TALLINN"
        if LovableSyncRun.objects.filter(run_key=run_key_for(trigger, moment)).exists():
            return "NOT_DUE ALREADY_RAN"
    return f"DUE {ctl.mode}"


#: The environment variable the host script sets on an on-demand run's
#: container: the verified backup set it took for an APPLY request.
HOST_BACKUP_SET_ENV = "LOVABLE_SYNC_BACKUP_SET"
#: Where the host script mounts that set, read-only, in the run's container: the
#: proof of its verification is read there (`backup_proof_problem`).
HOST_BACKUP_DIR_ENV = "LOVABLE_SYNC_BACKUP_DIR"


def pending_sync_request(lane: str = "lovable") -> bool:
    """Whether an administrator's «Sünkrooni nüüd» request waits (interface 4:
    `DataSyncRequest`, kind LOVABLE_SYNC, PENDING). Reads only; without the
    requests model in this build there is never one."""
    try:
        from app.legacy_import.sync_requests_models import (  # type: ignore[import-not-found,unused-ignore]
            DataSyncRequest,
        )
    except ImportError:
        return False
    return bool(DataSyncRequest.objects.filter(lane=lane, status="PENDING").exists())


def due_on_demand(lane: str = "lovable") -> str:
    """The host's on-demand firing (every few minutes, the same cron file):
    ``DUE <mode>`` only while a request waits, else ``NOT_DUE NO_REQUEST``.
    A paused synchronisation still answers ``DUE OFF`` for a waiting request, so
    the request runner records the refusal instead of leaving it pending. Reads
    only: it never creates the control row."""
    if lane == "onenote":
        # The OneNote lane has no Lovable control row: only whether one waits.
        return "DUE ONENOTE" if pending_sync_request("onenote") else "NOT_DUE NO_REQUEST"
    ctl = LovableSyncControl.objects.filter(pk=1).first()
    if ctl is not None and ctl.cut_over:
        return "NOT_DUE CUT_OVER"
    if not pending_sync_request("lovable"):
        return "NOT_DUE NO_REQUEST"
    return f"DUE {ctl.mode if ctl is not None else SyncMode.OFF}"


def host_backup_set() -> str:
    """The backup set the host script took for this container's run, or ``""``
    (`HOST_BACKUP_SET_ENV`). The request runner passes it to `run_sync`, which
    accepts it only if it is a fresh set's name (`fresh_backup_set`)."""
    import os

    return os.environ.get(HOST_BACKUP_SET_ENV, "").strip()
