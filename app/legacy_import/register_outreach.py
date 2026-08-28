"""Pointing a Matter at the outreach that was done for it.

Two things happened around most consultations in the maintained register and
neither is recorded on the file: a mailing went out through Sendsmaily, and a
public consultation page went up on koda.ee. Today the answer to "did we ask
anybody about this, and where" lives in a marketing tool's export and in
somebody's memory. ``MatterEngagement`` is the pointer that fixes that, and this
module is how a reviewed batch of pointers is proposed and — separately —
written.

A candidate is not a match
--------------------------
Nothing here writes on the strength of anything it computed. The matcher's whole
job is to hand an operator a short, ranked, explainable list; a **reviewed
mapping file**, prepared by a person and supplied at apply time, is the only
thing that produces an ``Engagement``. That split is not ceremony. The register
titles a Matter by its instrument — *Võlaõigusseaduse muutmise seaduse eelnõu
(direktiivi (EL) 2024/825 ülevõtmine)* — and the campaign titles it by its
subject — *toodete vastupidavuse ja parandatavuse kohta*. Those two strings
share no content word at all, and they are the same consultation. A matcher
confident enough to link them would be confident enough to link things that are
not.

So the signals are deliberately weak on their own and strong together:

**Owner.** The campaign template names the lawyer who ran it — *varjendid
05.03.26 Ireen*. That is the register's ``VASTUTAJA`` for the Matter, written by
the same department, and it is a hard filter rather than a score: a campaign
whose named owner is not this Matter's owner is not a candidate at any
confidence.

**Window.** The campaign has to fall between the day the material arrived and
the day the opinion was due. Asking members after the deadline is not how the
work runs, and asking before the file exists is impossible. Every one of the
seven consultations the operator identified in the 2026 pilot falls strictly
inside its Matter's window.

**Subject overlap.** Content words shared between the campaign's wording and the
Matter's title, after the register's own boilerplate is removed — *seaduse*,
*muutmise*, *eelnõu* and the rest appear in almost every title and separate
nothing. Overlap is what raises a candidate to ``HIGH_CONFIDENCE``; its absence
lowers the candidate and never rejects it, because of the 2024/825 case above.

On the pilot data those rules produce eleven high-confidence pairs and
sixty-four further candidates from eleven direct-feedback campaigns — and one of
the operator's seven, the one whose title names a directive, appears only as a
candidate. That is the honest result and the reason the reviewed file exists.

What is imported, and what is not
---------------------------------
Title, link, date, and — for a mailing — how many addresses it went to. Opens,
open rate, views, clicks, click rate, bounces, unsubscribes and complaints are
all in the export and none of them is imported. They are engagement analytics
about identifiable members, they answer no question the file asks, and a legal
file is not where they belong.

The recipient count is **not** the register's feedback count and the two are
never substituted for one another. Sendsmaily enqueued 234 addresses for the
mailing behind one Matter; the register records that 273 members were asked
directly. Both are true — the mailing is one channel of several — and each stays
on its own record (brief 24, 26).

Nothing here crawls anything. There is no HTTP client in this module, no
scheduled fetch and no provider credentials: a person prepares the mapping from
the export and the public pages, and this records what they approved.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction

from app.legacy_import.models import OutreachChannel, RegisterEngagementImport
from app.matters.enums import EngagementKind
from app.matters.models import Matter, MatterEngagement
from app.matters.services import add_engagement, update_engagement
from app.search.indexing import indexable_matters, refresh_matters

#: Bumped when the candidate rules change meaning. Recorded in the report, so a
#: shorter list next month is a rule change rather than a mystery.
OUTREACH_MATCHER_VERSION = "1.0"

#: The columns of the Sendsmaily campaign export this module will read. Named
#: as an allowlist rather than filtered as a denylist: a future export gaining
#: an ``Unique opens by device`` column must not become importable because
#: nobody remembered to exclude it (brief 23).
CAMPAIGN_COLUMNS: tuple[str, ...] = (
    "Section name",
    "Template name",
    "Template preview",
    "Due at",
    "Enqueues",
)

#: Everything else the export carries, listed so a test can assert that not one
#: of them reaches a Matter. Engagement analytics about identifiable members are
#: the vendor's business and not the legal file's.
REFUSED_CAMPAIGN_COLUMNS: tuple[str, ...] = (
    "Deliveries",
    "Bounces",
    "Opens",
    "Open rate",
    "Views",
    "Unique views",
    "Clicks",
    "Unique clicks",
    "Click rate",
    "Clickthrough rate",
    "Unsubscribes",
    "Forwards",
    "Complaints",
)


class Confidence:
    """How much the matcher can say about one pair. Never enough to write."""

    #: Owner, window and subject overlap all agree.
    HIGH = "HIGH_CONFIDENCE"
    #: Owner and window agree; the wordings share no content word. Real
    #: consultations look like this whenever the register titles the file by its
    #: instrument and the campaign titles it by its subject.
    CANDIDATE = "CANDIDATE"


CONFIDENCES: tuple[str, ...] = (Confidence.HIGH, Confidence.CANDIDATE)


# ---------------------------------------------------------------------------
# Reading the export
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Campaign:
    """One row of the campaign export, reduced to what may be recorded."""

    section_name: str
    template_name: str
    template_url: str
    sent_on: dt.date
    #: How many addresses the mailing was queued for. The only figure imported,
    #: and never the register's member-feedback count.
    enqueues: int | None

    @property
    def source_key(self) -> str:
        """The stable identity a re-run finds this campaign by.

        The template URL, which the vendor assigns and nobody here edits. Not
        the title: correcting a title is the one thing ``Kaasamine`` supports,
        and identity that moved when somebody fixed a typo would duplicate the
        record they were tidying (brief 27).
        """
        return self.template_url

    @property
    def words(self) -> frozenset[str]:
        return content_words(f"{self.section_name} {self.template_name}")


def _campaign_date(value: str) -> dt.date | None:
    """The date component of ``Due at``.

    The neutral campaign date the engagement records. The export's timestamp is
    the moment the vendor was told to send, which is the closest thing to "when
    Koda asked" that exists in it; the time of day is machinery and is dropped.
    """
    text = (value or "").strip()
    for shape in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(text, shape).date()
        except ValueError:
            continue
    return None


def _count(value: str) -> int | None:
    text = (value or "").strip().replace(" ", "")
    return int(text) if text.isdigit() else None


def read_campaigns(
    rows: Any, *, since: dt.date, until: dt.date
) -> tuple[list[Campaign], dict[str, int]]:
    """The campaigns in a window, from already-parsed export rows.

    Takes rows rather than a path on purpose: the export holds live campaign
    data, so the file is opened by the operator's command and never by anything
    that could be tempted to keep it. Only :data:`CAMPAIGN_COLUMNS` are read.
    """
    kept: list[Campaign] = []
    tally: Counter[str] = Counter()
    for row in rows:
        tally["rows"] += 1
        sent = _campaign_date(str(row.get("Due at") or ""))
        if sent is None:
            tally["undated"] += 1
            continue
        if not (since <= sent <= until):
            tally["outside_window"] += 1
            continue
        url = str(row.get("Template preview") or "").strip()
        if not url:
            # No stable identity means no idempotency, and a record that could
            # be written twice is worse than one that is not written.
            tally["no_template_url"] += 1
            continue
        kept.append(
            Campaign(
                section_name=str(row.get("Section name") or "").strip(),
                template_name=str(row.get("Template name") or "").strip(),
                template_url=url,
                sent_on=sent,
                enqueues=_count(str(row.get("Enqueues") or "")),
            )
        )
    tally["in_window"] = len(kept)
    return kept, dict(sorted(tally.items()))


def campaign_set_digest(campaigns: list[Campaign]) -> str:
    """A deterministic identity for the campaign set a plan was built from.

    **Not the file's digest, and the two are deliberately different things.**
    The export is re-taken whenever somebody opens the vendor's dashboard, and
    its bytes change when a campaign outside the pilot window is added, when a
    row's open count ticks up, or when the file is exported with a different
    column order. None of that changes what the matcher looked at.

    What must not change silently is the set of campaigns *this plan
    considered*, reduced to the five fields that may be recorded. That is what
    this hashes, and it is what the plan digest carries — so an apply run
    against a re-taken export with the same campaigns in it is allowed, and an
    apply run against an export whose campaigns have moved is refused.

    The file's own byte digest is reported beside it as
    ``campaign_file_sha256``: it identifies the operator's input, which is the
    thing they can point at, and it is evidence rather than a gate.
    """
    body = sorted(
        [
            campaign.template_url,
            campaign.sent_on.isoformat(),
            campaign.section_name,
            campaign.template_name,
            str(campaign.enqueues),
        ]
        for campaign in campaigns
    )
    encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Words that separate one file from another
# ---------------------------------------------------------------------------

#: Words that appear in so many register titles and campaign subjects that they
#: distinguish nothing. Almost every current row is a *… muutmise seaduse
#: eelnõu*, and counting that as agreement would make every campaign a
#: high-confidence match for every Matter in its window.
#:
#: Stems rather than words, because Estonian inflects: the list is compared
#: against the first seven characters of each word, which is what
#: :func:`content_words` produces.
BOILERPLATE_STEMS: frozenset[str] = frozenset(
    {
        "seaduse",
        "seadus",
        "seaduss",
        "muutmis",
        "eelnõu",
        "määruse",
        "vabarii",
        "valitsu",
        "ministr",
        "esitami",
        "kooskõl",
        "direkti",
        "ülevõtm",
        "muudatu",
        "teiste",
        "sellega",
        "seonduv",
        "avalik",
        "konsult",
        "arvamus",
        "tagasis",
        "plaanit",
        "kaubandu",
        "liikmet",
        "ettevõt",
        "kohustu",
        "teatud",
        "küsimus",
    }
)

#: Short words carry no subject in Estonian compounds — the distinguishing part
#: of *jäätmeseaduse* is the first half, and seven characters reaches it.
_MINIMUM_WORD = 7

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def content_words(text: str) -> frozenset[str]:
    """The stems of ``text`` that could distinguish one file from another.

    Prefix-truncated rather than stemmed by rule. Estonian's case endings attach
    to a stem this reaches — *jäätmeseaduse* and *jäätmeseadusesse* both begin
    *jäätme…* — and a real stemmer would broaden as it improved, which is
    exactly the property a matcher that must not over-match cannot afford.
    """
    found = set()
    for word in _WORD.findall((text or "").lower()):
        if len(word) < _MINIMUM_WORD:
            continue
        stem = word[:_MINIMUM_WORD]
        if stem in BOILERPLATE_STEMS:
            continue
        found.add(stem)
    return frozenset(found)


def named_owner(template_name: str, known: frozenset[str]) -> str:
    """The register owner this campaign template names, or "".

    Exactly one, or nothing. A template naming two of the department's lawyers
    says which of them ran it to a reader and not to this function, and picking
    one would be the guess the whole module exists to avoid.
    """
    folded = {name.casefold(): name for name in known}
    found = {
        folded[word.casefold()]
        for word in _WORD.findall(template_name or "")
        if word.casefold() in folded
    }
    return next(iter(found)) if len(found) == 1 else ""


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OutreachTarget:
    """One Matter as the newer workbook *would* leave it. Immutable, unsaved.

    The matcher's whole input, and the reason it is a value class rather than a
    queryset. Every field here is the **post-refresh** answer: the currency the
    reconciliation would decide, the owner the newer ``VASTUTAJA`` names, and
    the consultation window the newer ``SISSE`` and ``ARVAMUSE TÄHTAEG`` settle.

    Reading them from the database instead is the defect this replaces. Before
    an apply, ``CurrentRegisterState`` still describes the *previous* workbook
    and ``Matter`` still holds the previous dates — so a dry-run matched
    campaigns against facts the refresh was about to overwrite, and on a
    database that had never seen this snapshot at all it matched them against
    nothing and reported no candidates. An operator would have approved an empty
    outreach plan for a workbook full of consultations.

    Built by :func:`app.legacy_import.register_refresh.outreach_targets` through
    the same ``resolved_fields`` the apply spreads onto the Matter, so the plan
    and the write cannot disagree about a date.
    """

    matter_id: Any
    reference: str
    title: str
    #: ``VASTUTAJA`` as the newer snapshot writes it — a given name, which is
    #: what a campaign template names. Not the resolved account.
    owner_raw: str
    #: The consultation window after the refresh: the newer date where the
    #: source settles one, and the Matter's existing value where it does not —
    #: which is exactly what the apply would leave behind.
    received_date: dt.date | None
    response_deadline: dt.date | None

    @property
    def has_window(self) -> bool:
        return self.received_date is not None and self.response_deadline is not None

    def covers(self, day: dt.date) -> bool:
        """Whether a campaign sent on ``day`` falls inside this consultation.

        Both bounds are read into locals rather than tested through
        :attr:`has_window`, so the narrowing is one the type checker can follow
        and the closed interval is written once.
        """
        opened, closes = self.received_date, self.response_deadline
        if opened is None or closes is None:
            return False
        return opened <= day <= closes

    @property
    def words(self) -> frozenset[str]:
        return content_words(self.title)


@dataclass(frozen=True)
class Candidate:
    """One (Matter, campaign) pair the operator should look at."""

    matter_id: Any
    reference: str
    campaign: Campaign
    confidence: str
    owner_raw: str
    #: The stems that agreed, for a report that can be argued with.
    shared_terms: tuple[str, ...] = ()

    @property
    def is_high(self) -> bool:
        return self.confidence == Confidence.HIGH


@dataclass
class OutreachPlan:
    snapshot_sha256: str
    since: dt.date
    until: dt.date
    campaigns: list[Campaign] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    #: Campaigns that produced no candidate at all. Reported, because a
    #: consultation nobody can place is a finding rather than an absence.
    unmatched_campaigns: list[Campaign] = field(default_factory=list)
    read_tally: dict[str, int] = field(default_factory=dict)
    #: SHA-256 of the export file the operator supplied. Evidence, not a gate:
    #: it names the thing a person can point at, while the set digest below is
    #: what an apply is actually held to.
    campaign_file_sha256: str = ""

    @property
    def high_confidence(self) -> list[Candidate]:
        return [candidate for candidate in self.candidates if candidate.is_high]

    @property
    def campaign_set_sha256(self) -> str:
        return campaign_set_digest(self.campaigns)


def build_outreach_plan(
    *,
    snapshot_sha256: str,
    campaigns: list[Campaign],
    targets: list[OutreachTarget],
    since: dt.date,
    until: dt.date,
) -> OutreachPlan:
    """Rank the pairs worth an operator's attention. Writes nothing.

    **No queries at all.** ``targets`` is the projected post-refresh portfolio,
    derived once by the caller from the reconciliation it is reporting; this
    function is a pure computation over it and the campaign list. That is what
    makes the dry-run's answer the same answer the apply would produce, and it
    is the correction to a matcher that used to read the database and therefore
    described the previous workbook (see :class:`OutreachTarget`).
    """
    plan = OutreachPlan(
        snapshot_sha256=snapshot_sha256, since=since, until=until, campaigns=campaigns
    )

    # No window, no candidate. A campaign cannot be placed against a file whose
    # consultation period the register never recorded, and placing it on owner
    # and wording alone is the guess this refuses.
    placeable = [target for target in targets if target.owner_raw and target.has_window]
    owners = frozenset(target.owner_raw for target in placeable)

    by_owner: dict[str, list[OutreachTarget]] = {}
    for target in placeable:
        by_owner.setdefault(target.owner_raw, []).append(target)

    for campaign in campaigns:
        owner = named_owner(campaign.template_name, owners)
        if not owner:
            plan.unmatched_campaigns.append(campaign)
            continue

        words = campaign.words
        found = False
        for target in by_owner.get(owner, []):
            if not target.covers(campaign.sent_on):
                continue
            shared = tuple(sorted(words & target.words))
            plan.candidates.append(
                Candidate(
                    matter_id=target.matter_id,
                    reference=target.reference,
                    campaign=campaign,
                    confidence=Confidence.HIGH if shared else Confidence.CANDIDATE,
                    owner_raw=owner,
                    shared_terms=shared,
                )
            )
            found = True
        if not found:
            plan.unmatched_campaigns.append(campaign)

    plan.candidates.sort(key=lambda candidate: (candidate.campaign.sent_on, candidate.reference))
    return plan


def summary(plan: OutreachPlan) -> dict[str, Any]:
    """Aggregates and identities. No member data, ever."""
    confidences = Counter(candidate.confidence for candidate in plan.candidates)
    return {
        "matcher_version": OUTREACH_MATCHER_VERSION,
        "snapshot_sha256": plan.snapshot_sha256,
        "campaign_set_sha256": plan.campaign_set_sha256,
        "campaign_file_sha256": plan.campaign_file_sha256,
        "window": [plan.since.isoformat(), plan.until.isoformat()],
        "campaigns_read": plan.read_tally,
        "campaigns_in_window": len(plan.campaigns),
        "candidates": {name: confidences.get(name, 0) for name in CONFIDENCES},
        "candidate_pairs": len(plan.candidates),
        "matters_with_a_candidate": len({c.matter_id for c in plan.candidates}),
        "campaigns_unmatched": len(plan.unmatched_campaigns),
        # Stated in the report because it is the one thing a reader most needs
        # to know about these numbers: none of them writes anything.
        "writes_without_reviewed_mapping": 0,
    }


def candidate_rows(plan: OutreachPlan) -> list[dict[str, Any]]:
    """The operator's review file: one line per pair, ready to approve.

    Carries the campaign's title and link because the operator has to read them
    to decide, and both are Koda's own published material. It carries no
    recipient identity, no analytics and no register prose.
    """
    return [
        {
            "reference": candidate.reference,
            "matter_id": str(candidate.matter_id),
            "confidence": candidate.confidence,
            "channel": OutreachChannel.EMAIL_CAMPAIGN,
            "source_key": candidate.campaign.source_key,
            "title": candidate.campaign.section_name or candidate.campaign.template_name,
            "url": candidate.campaign.template_url,
            "occurred_on": candidate.campaign.sent_on.isoformat(),
            "enqueues": candidate.campaign.enqueues,
            "owner": candidate.owner_raw,
            "shared_terms": list(candidate.shared_terms),
        }
        for candidate in plan.candidates
    ]


# ---------------------------------------------------------------------------
# The reviewed mapping — the only thing that writes
# ---------------------------------------------------------------------------


class MappingError(Exception):
    """The reviewed mapping cannot be read, or names something that is not here."""


@dataclass(frozen=True)
class ReviewedLink:
    """One approved pointer: this Matter, this outreach, this date."""

    reference: str
    channel: str
    source_key: str
    title: str
    url: str = ""
    occurred_on: dt.date | None = None
    note: str = ""

    @property
    def kind(self) -> str:
        """The ``MatterEngagement`` channel this reviewed link becomes.

        A mailing and a published consultation are two channels, not two names
        for one thing, and a Matter legitimately carries both: members were
        e-mailed *and* anybody could respond through the public page. Nothing
        here deduplicates one against the other (brief 25).
        """
        return (
            EngagementKind.EMAIL_CAMPAIGN
            if self.channel == OutreachChannel.EMAIL_CAMPAIGN
            else EngagementKind.WEB_CALL
        )


def _date(value: Any) -> dt.date | None:
    if not value:
        return None
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value).strip())
    except ValueError as error:
        raise MappingError(f"Unreadable occurred_on {value!r}.") from error


def read_mapping(entries: Any) -> tuple[ReviewedLink, ...]:
    """The approved links, from an already-parsed mapping file.

    Every field is required to be present and meaningful; nothing is defaulted
    into existence. A mapping missing a ``source_key`` would produce a pointer
    with no import identity, which is a duplicate waiting for the next run.
    """
    links: list[ReviewedLink] = []
    seen: set[tuple[str, str, str]] = set()
    for entry in entries:
        reference = str(entry.get("reference") or "").strip()
        channel = str(entry.get("channel") or "").strip()
        source_key = str(entry.get("source_key") or "").strip()
        title = str(entry.get("title") or "").strip()
        if not reference or not source_key or not title:
            raise MappingError(f"Mapping entry needs reference, source_key and title: {entry!r}")
        if channel not in OutreachChannel.values:
            raise MappingError(f"Unknown channel {channel!r} for {reference}.")
        identity = (reference, channel, source_key)
        if identity in seen:
            raise MappingError(f"{reference}: {channel} {source_key} approved twice.")
        seen.add(identity)
        links.append(
            ReviewedLink(
                reference=reference,
                channel=channel,
                source_key=source_key,
                title=title[:500],
                url=str(entry.get("url") or "").strip(),
                occurred_on=_date(entry.get("occurred_on")),
                note=str(entry.get("note") or "").strip(),
            )
        )
    return tuple(links)


def mapping_digest(links: tuple[ReviewedLink, ...]) -> str:
    """The identity of one approval. Applying names it and it must match."""
    body = sorted(
        [
            link.reference,
            link.channel,
            link.source_key,
            link.title,
            link.url,
            link.occurred_on.isoformat() if link.occurred_on else "",
            link.note,
        ]
        for link in links
    )
    encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OutreachResult:
    created: int
    updated: int
    unchanged: int
    mapping_sha256: str

    @property
    def touched(self) -> int:
        return self.created + self.updated


def campaign_note(enqueues: int | None) -> str:
    """The one campaign figure that may be recorded, as a sentence.

    Recipients, and nothing else. It is here rather than in a column because it
    is context for a pointer, not a metric the application computes with:
    nothing sums it, nothing divides by it, and in particular nothing compares
    it with the register's member-feedback counts, which describe a different
    population (brief 23, 24).
    """
    return f"Sendsmaily adressaate: {enqueues}" if enqueues is not None else ""


@transaction.atomic
def apply_mapping(
    *, links: tuple[ReviewedLink, ...], expect_mapping_sha256: str, actor: Any = None
) -> OutreachResult:
    """Write the approved pointers, or none of them.

    Idempotent by construction rather than by comparison. Identity lives in
    ``RegisterEngagementImport``, whose unique constraint is
    ``(matter, channel, source_key)``, so a second run of the same approval
    finds the row it wrote last time and corrects the engagement it points at
    instead of adding a second one — even if somebody has since edited that
    engagement's title, which is the case title matching cannot survive.
    """
    expected = (expect_mapping_sha256 or "").strip().lower()
    digest = mapping_digest(links)
    if digest != expected:
        raise MappingError(
            f"Mapping digest {digest[:16]}… does not match the approved "
            f"{expected[:16] or '(none)'}…. Nothing was written."
        )

    references = sorted({link.reference for link in links})
    matters = {
        matter.display_reference: matter
        for matter in Matter.objects.filter(reference_number__isnull=False).select_for_update()
        if matter.display_reference in references
    }
    missing = [reference for reference in references if reference not in matters]
    if missing:
        raise MappingError(f"Mapping names Matters this database does not hold: {missing}.")

    existing = {
        (row.matter_id, row.channel, row.source_key): row
        for row in RegisterEngagementImport.objects.filter(
            matter_id__in=[matter.pk for matter in matters.values()]
        ).select_related("engagement")
    }

    created = updated = unchanged = 0
    touched: list[Any] = []
    for link in links:
        matter = matters[link.reference]
        identity = (matter.pk, link.channel, link.source_key)
        record = existing.get(identity)

        if record is None:
            engagement = add_engagement(
                matter=matter,
                kind=link.kind,
                title=link.title,
                url=link.url,
                note=link.note,
                occurred_on=link.occurred_on,
                # No actor. Nobody signed in decided this; what did is the
                # mapping digest recorded beside it.
                actor=actor,
            )
            RegisterEngagementImport.objects.create(
                engagement=engagement,
                matter=matter,
                channel=link.channel,
                source_key=link.source_key,
                mapping_sha256=digest,
                created_engagement=True,
            )
            created += 1
            touched.append(matter.pk)
            continue

        recorded: MatterEngagement = record.engagement
        before = (recorded.title, recorded.url, recorded.note, recorded.occurred_on)
        update_engagement(
            engagement=recorded,
            kind=link.kind,
            title=link.title,
            url=link.url,
            note=link.note,
            occurred_on=link.occurred_on,
            actor=actor,
        )
        recorded.refresh_from_db()
        if before == (recorded.title, recorded.url, recorded.note, recorded.occurred_on):
            unchanged += 1
            continue
        record.mapping_sha256 = digest
        record.save(update_fields=["mapping_sha256", "updated_at"])
        updated += 1
        touched.append(matter.pk)

    if touched:
        refresh_matters(indexable_matters().filter(pk__in=sorted(set(touched))))

    return OutreachResult(
        created=created, updated=updated, unchanged=unchanged, mapping_sha256=digest
    )


__all__ = [
    "BOILERPLATE_STEMS",
    "CAMPAIGN_COLUMNS",
    "CONFIDENCES",
    "OUTREACH_MATCHER_VERSION",
    "REFUSED_CAMPAIGN_COLUMNS",
    "Campaign",
    "Candidate",
    "Confidence",
    "MappingError",
    "OutreachPlan",
    "OutreachResult",
    "OutreachTarget",
    "ReviewedLink",
    "apply_mapping",
    "build_outreach_plan",
    "campaign_note",
    "campaign_set_digest",
    "candidate_rows",
    "content_words",
    "mapping_digest",
    "named_owner",
    "read_campaigns",
    "read_mapping",
    "summary",
]
