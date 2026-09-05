"""«Võimalikud seosed»: what existing material resembles this Matter.

A deterministic, read-only recommendation. Given the same Matter, the same
search projection, the same archive projection and the same catalogues, the
same ranked candidates come back with the same reasons. No model, no
embedding, no external call, no click history — and nothing here writes a row
(docs/adr/0061 §2, §6).

The work is split the way the data is.

**PostgreSQL bounds the pool.** The existing search projection already holds,
for every Matter, a title vector and a trigram-indexed title; for every sent
opinion a row of its own; and for every archive letter a row in the archive
projection. Each pool query starts from the visibility chokepoint that product
surface already uses — `visible_documents`, `Matter.objects.visible_to`,
`visible_archive` — so nothing a reader may not see is ever *in* the pool, and
therefore cannot shape a count, a rank, a reason or a tie (§5). The queries
return a few dozen rows, not the corpus.

**Python explains.** Over that bounded pool the signals are computed in plain
code with named weights, so a test can hold the rule «same PolicyArea alone is
not enough» directly, and so the reason a lawyer reads is produced by the same
code that produced the score. The numeric score is internal. It is never shown
as a percentage, because the rules do not have that precision.

The threshold encodes the product rule rather than tuning for volume: a
candidate needs one strong subject-specific signal — the same named act, or a
title that repeats two of this Matter's subject words — or a credible
combination of structured ones, such as a shared tag plus the same ministry.
Same PolicyArea alone, same organisation alone, same Track alone, same year
alone: each is below the line by construction, and the tests hold it there
(§4). An empty result is a valid result; the threshold is not lowered to fill a
section.
"""

from __future__ import annotations

import functools
import operator
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from django.contrib.postgres.search import SearchQuery
from django.db.models import (
    Case,
    Count,
    Exists,
    ExpressionWrapper,
    F,
    IntegerField,
    OuterRef,
    Q,
    QuerySet,
    Value,
    When,
)
from django.urls import reverse

from app.core.text import normalize_for_matching
from app.legacy_import.opinion_access import may_read_archive
from app.legacy_import.opinion_archive import OpinionMatchCandidate, OpinionSubmissionImport
from app.legacy_import.opinion_binary import OpinionArchiveMatterLink
from app.legacy_import.opinion_enums import OpinionCandidateState
from app.legacy_import.opinion_search import visible_archive
from app.legacy_import.opinion_search_models import OpinionArchiveSearchDocument
from app.matters.models import Matter
from app.related_materials import text
from app.related_materials.models import (
    MatterBackgroundMaterial,
    MatterRelation,
    RelatedSuggestionDismissal,
)
from app.search.models import SearchSourceKind
from app.search.services import WordSimilarity, visible_documents
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission

# -- the contract -----------------------------------------------------------

#: How many suggestions the section shows at first, and the most «Näita veel»
#: may extend it to. Five, because a lawyer reads five; fifteen, because past
#: that the list is the register with a different heading.
DEFAULT_LIMIT = 5
MAX_LIMIT = 15

#: How many rows each pool query may hand to Python. A few dozen plausible
#: rows, not the corpus.
TEXT_POOL = 40
STRUCTURED_POOL = 40
MATERIAL_POOL = 40
ARCHIVE_POOL = 30

#: Below this many subject words the title says nothing usable, so no text
#: query runs at all.
TRIGRAM_POOL_THRESHOLD = 0.45

#: How many reasons a card shows. The strongest first; the rest are not lies,
#: they are noise.
MAX_REASONS = 3

# -- the weights ------------------------------------------------------------
#
# Named, documented, and deliberately few. The exact numbers are an
# implementation detail; the *relations* between them are the product rule and
# are pinned by tests: one strong signal clears the threshold on its own, no
# single structured signal does, and a credible combination of structured
# signals does.

#: The same named act. The strongest thing two titles can share.
W_ACT = 6.0
ACT_CAP = 2
#: Two or more of this Matter's subject words in the candidate's title.
W_TITLE_STRONG = 5.0
#: One subject word in the title: supporting, not sufficient.
W_TITLE_TERM = 1.5
#: A shared tag. Tags are the department's own specific vocabulary.
W_TAG = 2.0
TAG_CAP = 2
#: A shared policy area — the broadest classification there is, so the least.
W_AREA_FIRST = 1.0
W_AREA_SECOND = 0.5
#: The same sender or addressee body, counted once however many roles match.
W_ORGANISATION = 1.5
#: The same track. Weak support only, never a reason on the card, and never
#: counted on its own.
W_TRACK = 0.5
#: A subject word found in the candidate's text but not its title.
W_BODY_TERM = 0.75
BODY_TERM_CAP = 2
#: An opinion or letter on a Matter the person has already confirmed as related.
W_RELATED_MATTER_MATERIAL = 5.0
#: An archive letter the reconciliation has tied to some Matter or applied as
#: a Submission: evidence the file really is Koda correspondence. A ranking
#: preference between otherwise equal letters, never a reason and never enough.
W_ARCHIVE_REVIEWED = 1.0

#: What a candidate must reach. One act (6.0) or one strong title (5.0) clears
#: it alone; tag + organisation (3.5) clears it; area + organisation (2.5) and
#: tag + area (3.0) do not.
THRESHOLD = 3.5

KIND_SUBMISSION = "SUBMISSION"
KIND_ARCHIVE = "ARCHIVE"

LABEL_SUBMISSION = "Varasem arvamus"
LABEL_ARCHIVE = "Arhiivimaterjal"

EMPTY_MESSAGE = "Praegu ei leitud piisavalt tugevaid võimalikke seoseid."


# ---------------------------------------------------------------------------
# Read models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RelatedMatterSuggestion:
    """One candidate Matter, with why it is here."""

    matter: Matter
    score: float
    reasons: tuple[str, ...]
    is_dismissed: bool = False

    @property
    def state_label(self) -> str:
        return "avatud" if self.matter.is_open else "suletud"


@dataclass(frozen=True)
class MaterialSuggestion:
    """One candidate opinion or archive letter, with why it is here."""

    kind: str
    key: uuid.UUID
    title: str
    date: date | None
    source_reference: str
    source_title: str
    recipient: str
    score: float
    reasons: tuple[str, ...]
    open_url: str
    is_dismissed: bool = False

    @property
    def label(self) -> str:
        return LABEL_SUBMISSION if self.kind == KIND_SUBMISSION else LABEL_ARCHIVE

    @property
    def is_submission(self) -> bool:
        return self.kind == KIND_SUBMISSION

    @property
    def form_kind(self) -> str:
        """The POST vocabulary the routes speak."""
        return "arvamus" if self.kind == KIND_SUBMISSION else "arhiiv"


@dataclass(frozen=True)
class Suggestions:
    """Everything «Võimalikud seosed» renders for one opening."""

    matters: tuple[RelatedMatterSuggestion, ...]
    materials: tuple[MaterialSuggestion, ...]
    limit: int
    more_matters: int
    more_materials: int
    hidden_count: int
    hidden_matters: tuple[RelatedMatterSuggestion, ...] = ()
    hidden_materials: tuple[MaterialSuggestion, ...] = ()
    hidden_shown: bool = False

    @property
    def count(self) -> int:
        return len(self.matters) + len(self.materials)

    @property
    def is_empty(self) -> bool:
        return self.count == 0

    @property
    def has_more(self) -> bool:
        return self.more_matters > 0 or self.more_materials > 0


@dataclass(frozen=True)
class _TextSignals:
    """Which of our subject words a projection row carried, and where."""

    title_terms: tuple[text.Term, ...]
    body_terms: tuple[text.Term, ...]


@dataclass(frozen=True)
class SubjectProfile:
    """What the current Matter is about, read once."""

    matter: Matter
    instruments: tuple[text.LegalInstrument, ...]
    title_terms: tuple[text.Term, ...]
    summary_terms: tuple[text.Term, ...]
    tag_names: dict[Any, str]
    area_names: dict[Any, str]
    organisation_names: dict[Any, str]
    track: str
    related_matter_ids: frozenset[Any]
    excluded_matter_ids: frozenset[Any]
    dismissed_matter_ids: frozenset[Any]
    dismissed_submission_ids: frozenset[Any]
    dismissed_binary_ids: frozenset[Any]
    background_submission_ids: frozenset[Any]
    background_binary_ids: frozenset[Any]

    @property
    def terms(self) -> tuple[text.Term, ...]:
        return (*self.title_terms, *self.summary_terms)

    @property
    def has_dismissals(self) -> bool:
        return bool(
            self.dismissed_matter_ids or self.dismissed_submission_ids or self.dismissed_binary_ids
        )


# ---------------------------------------------------------------------------
# The profile
# ---------------------------------------------------------------------------


def build_profile(matter: Matter) -> SubjectProfile:
    """Read what the Matter is about and what has already been decided about it.

    Reads the Matter's own relations, and the reader's own catalogues, and
    nothing about anybody else's visibility — the profile is *ours*.
    """
    titles = [matter.title, *(matter.alternate_titles or [])]
    instruments = tuple(text.legal_instruments(" . ".join(titles)))
    title_terms = tuple(text.subject_terms(*titles, limit=8))
    summary_terms = tuple(
        term
        for term in text.subject_terms(matter.brief_summary or "", limit=8)
        if not any(text.same_word(term.key, ours.key) for ours in title_terms)
    )[:4]

    tag_names = {tag.pk: tag.name_et for tag in matter.tags.all()}
    area_names = {area.pk: area.name_et for area in matter.policy_areas.all()}
    organisation_names = {
        organisation.pk: organisation.name for organisation in matter.source_organisations.all()
    }
    if matter.addressee_organisation_id is not None and matter.addressee_organisation is not None:
        organisation_names.setdefault(
            matter.addressee_organisation_id, matter.addressee_organisation.name
        )

    related = set(
        MatterRelation.objects.filter(Q(matter_a=matter) | Q(matter_b=matter)).values_list(
            "matter_a_id", "matter_b_id"
        )
    )
    related_ids = {other for pair in related for other in pair if other != matter.pk}

    dismissed_matters: set[Any] = set()
    dismissed_submissions: set[Any] = set()
    dismissed_binaries: set[Any] = set()
    for matter_id, submission_id, binary_id in RelatedSuggestionDismissal.objects.filter(
        matter=matter
    ).values_list("candidate_matter_id", "candidate_submission_id", "candidate_archive_binary_id"):
        if matter_id is not None:
            dismissed_matters.add(matter_id)
        if submission_id is not None:
            dismissed_submissions.add(submission_id)
        if binary_id is not None:
            dismissed_binaries.add(binary_id)

    background_submissions: set[Any] = set()
    background_binaries: set[Any] = set()
    for submission_id, binary_id in MatterBackgroundMaterial.objects.filter(
        matter=matter
    ).values_list("submission_id", "archive_binary_id"):
        if submission_id is not None:
            background_submissions.add(submission_id)
        if binary_id is not None:
            background_binaries.add(binary_id)

    # The continuation chain is the existing, authoritative relationship and is
    # shown where it always was; it is not offered again as a possibility.
    successors = set(Matter.objects.filter(superseded_by=matter).values_list("pk", flat=True))
    excluded = {matter.pk, *related_ids, *dismissed_matters, *successors}
    if matter.superseded_by_id is not None:
        excluded.add(matter.superseded_by_id)

    return SubjectProfile(
        matter=matter,
        instruments=instruments,
        title_terms=title_terms,
        summary_terms=summary_terms,
        tag_names=tag_names,
        area_names=area_names,
        organisation_names=organisation_names,
        track=matter.track or "",
        related_matter_ids=frozenset(related_ids),
        excluded_matter_ids=frozenset(excluded),
        dismissed_matter_ids=frozenset(dismissed_matters),
        dismissed_submission_ids=frozenset(dismissed_submissions),
        dismissed_binary_ids=frozenset(dismissed_binaries),
        background_submission_ids=frozenset(background_submissions),
        background_binary_ids=frozenset(background_binaries),
    )


# ---------------------------------------------------------------------------
# Text signals in SQL
# ---------------------------------------------------------------------------


def _term_queries(term: text.Term, *, title_field: str, title_vector: str | None) -> tuple[Q, Q]:
    """``(in the title, anywhere)`` for one subject word on one projection.

    The Estonian configuration stems both sides; the simple vector is asked for
    the word's prefix so an inflection the stemmer does not know still counts.
    The prefix is letters only — `text.tokens` admits nothing else — so the raw
    query cannot be given operators.
    """
    estonian = SearchQuery(term.display, config="estonian")
    prefix = term.prefix.split("-", 1)[0]
    in_title = Q(**{f"{title_field}__icontains": prefix}) if len(prefix) >= 4 else Q(pk__in=[])
    if title_vector is not None:
        in_title |= Q(**{title_vector: estonian})
    anywhere = Q(search_estonian=estonian) | in_title
    if len(prefix) >= 4:
        anywhere |= Q(search_simple=SearchQuery(f"{prefix}:*", search_type="raw", config="simple"))
    return in_title, anywhere


def _annotate_terms(
    rows: QuerySet[Any],
    terms: Sequence[text.Term],
    *,
    title_field: str,
    title_vector: str | None,
) -> tuple[QuerySet[Any], Q]:
    """One integer flag per term for the title and one for anywhere.

    Returns the annotated rows and the «any term matched» condition.
    """
    annotations: dict[str, Any] = {}
    any_match = Q(pk__in=[])
    for index, term in enumerate(terms):
        in_title, anywhere = _term_queries(term, title_field=title_field, title_vector=title_vector)
        annotations[f"in_title_{index}"] = Case(
            When(in_title, then=Value(1)), default=Value(0), output_field=IntegerField()
        )
        annotations[f"anywhere_{index}"] = Case(
            When(anywhere, then=Value(1)), default=Value(0), output_field=IntegerField()
        )
        any_match |= anywhere
    if not terms:
        return rows, any_match
    rows = rows.annotate(**annotations)
    rows = rows.annotate(
        title_hits=_sum(f"in_title_{index}" for index in range(len(terms))),
        any_hits=_sum(f"anywhere_{index}" for index in range(len(terms))),
    )
    return rows, any_match


def _sum(names: Iterable[str]) -> Any:
    return ExpressionWrapper(
        functools.reduce(operator.add, (F(name) for name in names)), output_field=IntegerField()
    )


def _signals_from_row(row: dict[str, Any], terms: Sequence[text.Term]) -> _TextSignals:
    title_terms = tuple(term for index, term in enumerate(terms) if row.get(f"in_title_{index}"))
    body_terms = tuple(
        term
        for index, term in enumerate(terms)
        if row.get(f"anywhere_{index}") and not row.get(f"in_title_{index}")
    )
    return _TextSignals(title_terms=title_terms, body_terms=body_terms)


# ---------------------------------------------------------------------------
# Candidate Matters
# ---------------------------------------------------------------------------


def _structured_pool(profile: SubjectProfile, viewer: Any) -> list[Any]:
    """Matters sharing enough catalogue facts with ours to be worth scoring.

    One query, inside the visibility boundary, bounded. A shared tag alone is
    admitted to the *pool* (it may combine with text later); a shared area
    alone is not, because half the register shares an area with something.
    """
    tag_ids = list(profile.tag_names)
    area_ids = list(profile.area_names)
    organisation_ids = list(profile.organisation_names)
    if not (tag_ids or area_ids or organisation_ids):
        return []

    rows = (
        Matter.objects.visible_to(viewer)
        .filter(data_class=profile.matter.data_class)
        .exclude(pk__in=list(profile.excluded_matter_ids))
    )
    # Only the facts this Matter actually carries are asked about: an empty
    # `IN ()` inside an aggregate filter is not a zero, it is a query that
    # cannot be compiled.
    annotations: dict[str, Any] = {}
    parts: list[Any] = []
    if tag_ids:
        annotations["shared_tags"] = Count("tags", filter=Q(tags__in=tag_ids), distinct=True)
        parts.append(F("shared_tags") * 2)
    if area_ids:
        annotations["shared_areas"] = Count(
            "policy_areas", filter=Q(policy_areas__in=area_ids), distinct=True
        )
        parts.append(F("shared_areas"))
    if organisation_ids:
        annotations["shared_senders"] = Count(
            "source_organisations",
            filter=Q(source_organisations__in=organisation_ids),
            distinct=True,
        )
        annotations["same_addressee"] = Case(
            When(addressee_organisation_id__in=organisation_ids, then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        )
        parts.append(F("shared_senders"))
        parts.append(F("same_addressee"))
    rows = rows.annotate(**annotations).annotate(
        structured=ExpressionWrapper(
            functools.reduce(operator.add, parts), output_field=IntegerField()
        )
    )
    return list(
        rows.filter(structured__gte=2)
        .order_by("-structured", "pk")
        .values_list("pk", flat=True)[:STRUCTURED_POOL]
    )


def _matter_text_pool(
    profile: SubjectProfile, viewer: Any, structured_ids: Sequence[Any]
) -> dict[Any, _TextSignals]:
    """Matter projection rows that carry our subject words, plus the structured pool.

    Starts from the search chokepoint, restricted to MATTER rows of the same
    data class. Returns the text signals per Matter; a Matter without a current
    projection row simply has none, and the page still works.
    """
    terms = profile.terms
    if not terms and not structured_ids:
        return {}
    rows = (
        visible_documents(viewer)
        .filter(source_kind=SearchSourceKind.MATTER, matter__data_class=profile.matter.data_class)
        .exclude(matter_id__in=list(profile.excluded_matter_ids))
    )
    rows, any_match = _annotate_terms(rows, terms, title_field="title", title_vector="search_title")
    if terms:
        distinctive = " ".join(term.display for term in profile.title_terms) or terms[0].display
        rows = rows.annotate(title_similarity=WordSimilarity(Value(distinctive), F("title")))
        rows = rows.filter(
            any_match
            | Q(title_similarity__gte=TRIGRAM_POOL_THRESHOLD)
            | Q(matter_id__in=list(structured_ids))
        ).order_by(
            F("title_hits").desc(), F("any_hits").desc(), F("title_similarity").desc(), "matter_id"
        )
    else:
        rows = rows.filter(matter_id__in=list(structured_ids)).order_by("matter_id")

    columns = ["matter_id", *(f"in_title_{i}" for i in range(len(terms)))]
    columns += [f"anywhere_{i}" for i in range(len(terms))]
    found: dict[Any, _TextSignals] = {}
    for row in rows.values(*columns)[: TEXT_POOL + len(structured_ids)]:
        found[row["matter_id"]] = _signals_from_row(row, terms)
    return found


def _fetch_matters(viewer: Any, ids: Iterable[Any]) -> dict[Any, Matter]:
    """The pool's Matters with everything scoring reads, through the boundary."""
    wanted = list(ids)
    if not wanted:
        return {}
    rows = (
        Matter.objects.visible_to(viewer)
        .filter(pk__in=wanted)
        .select_related("addressee_organisation", "stage")
        .prefetch_related("tags", "policy_areas", "source_organisations")
    )
    return {matter.pk: matter for matter in rows}


def _matter_signals(
    profile: SubjectProfile, candidate: Matter, signals: _TextSignals | None
) -> tuple[float, list[str]]:
    """Score one Matter against ours and say why, strongest reason first."""
    score = 0.0
    reasons: list[str] = []

    titles = [candidate.title, *(candidate.alternate_titles or [])]
    their_acts = text.legal_instruments(" . ".join(titles))
    shared_acts = text.shared_instruments(list(profile.instruments), their_acts)[:ACT_CAP]
    for act in shared_acts:
        score += W_ACT
        reasons.append(f"Sama õigusakt: {act.display}")

    if signals is not None:
        title_terms = list(signals.title_terms)
        body_terms = list(signals.body_terms)
    else:
        title_terms = text.matching_terms(list(profile.title_terms), " . ".join(titles))
        body_terms = []
    title_terms = _without_act_words(title_terms, shared_acts)
    body_terms = _without_act_words(body_terms, shared_acts)
    score, reasons = _add_text_signals(score, reasons, title_terms, body_terms)

    shared_tags = sorted(
        profile.tag_names[tag.pk] for tag in candidate.tags.all() if tag.pk in profile.tag_names
    )[:TAG_CAP]
    for name in shared_tags:
        score += W_TAG
        reasons.append(f"Sama silt: {name}")

    candidate_organisations = {
        organisation.pk for organisation in candidate.source_organisations.all()
    }
    if candidate.addressee_organisation_id is not None:
        candidate_organisations.add(candidate.addressee_organisation_id)
    shared_organisations = sorted(
        profile.organisation_names[pk]
        for pk in candidate_organisations
        if pk in profile.organisation_names
    )
    if shared_organisations:
        score += W_ORGANISATION
        reasons.append(f"Sama asutus: {shared_organisations[0]}")

    shared_areas = sorted(
        profile.area_names[area.pk]
        for area in candidate.policy_areas.all()
        if area.pk in profile.area_names
    )
    if len(shared_areas) == 1:
        score += W_AREA_FIRST
        reasons.append(f"Sama valdkond: {shared_areas[0]}")
    elif shared_areas:
        score += W_AREA_FIRST + W_AREA_SECOND
        reasons.append(f"Samad valdkonnad: {text.format_list(shared_areas, 2)}")

    # Track supports a candidate that already has a reason; it never starts one.
    if score > 0 and profile.track and candidate.track == profile.track:
        score += W_TRACK

    return score, reasons


def _without_act_words(
    terms: Sequence[text.Term], acts: Sequence[text.LegalInstrument]
) -> list[text.Term]:
    """Drop the words that *are* a shared act, so it is not counted twice."""
    if not acts:
        return list(terms)
    act_keys = [text.stem(part) for act in acts for part in act.display.split()]
    return [term for term in terms if not any(text.same_word(term.key, key) for key in act_keys)]


def _add_text_signals(
    score: float,
    reasons: list[str],
    title_terms: Sequence[text.Term],
    body_terms: Sequence[text.Term],
) -> tuple[float, list[str]]:
    title_words = [term.display for term in title_terms]
    if len(title_words) >= 2:
        score += W_TITLE_STRONG
        reasons.append(f"Sarnane pealkiri: {text.format_list(title_words)}")
    elif title_words:
        score += W_TITLE_TERM
        reasons.append(f"Pealkirjas kordub: {title_words[0]}")
    body_words = [term.display for term in body_terms][:BODY_TERM_CAP]
    if body_words:
        score += W_BODY_TERM * len(body_words)
        if len(body_words) == 1:
            reasons.append(f"Tekstis kattub: {body_words[0]}")
        else:
            reasons.append(f"Tekstis kattuvad: {text.format_list(body_words)}")
    return score, reasons


def _matter_order(item: RelatedMatterSuggestion) -> tuple[Any, ...]:
    """Score first; then the newer file, the title, and the key. Never the planner."""
    matter = item.matter
    return (
        -item.score,
        -(matter.reference_year or 0),
        -(matter.reference_number or 0),
        matter.title.casefold(),
        str(matter.pk),
    )


def related_matter_candidates(
    profile: SubjectProfile, viewer: Any
) -> tuple[list[RelatedMatterSuggestion], dict[Any, tuple[float, list[str]]]]:
    """Every Matter over the threshold, best first, and every pool score.

    The second value is what the material scorer reads: an opinion inherits
    the case for its own Matter, whether or not that Matter made the list.
    """
    structured = _structured_pool(profile, viewer)
    signals = _matter_text_pool(profile, viewer, structured)
    matters = _fetch_matters(viewer, set(signals) | set(structured))

    scored: dict[Any, tuple[float, list[str]]] = {}
    found: list[RelatedMatterSuggestion] = []
    for matter_id, candidate in matters.items():
        score, reasons = _matter_signals(profile, candidate, signals.get(matter_id))
        scored[matter_id] = (score, reasons)
        if score >= THRESHOLD:
            found.append(
                RelatedMatterSuggestion(
                    matter=candidate, score=score, reasons=tuple(reasons[:MAX_REASONS])
                )
            )
    found.sort(key=_matter_order)
    return found, scored


# ---------------------------------------------------------------------------
# Candidate material: opinions
# ---------------------------------------------------------------------------


def _submission_date(submission: Submission) -> date | None:
    return submission.sent_at.date() if submission.sent_at else None


def _submission_suggestion(
    submission: Submission, *, score: float, reasons: Sequence[str], dismissed: bool = False
) -> MaterialSuggestion:
    matter = submission.matter
    return MaterialSuggestion(
        kind=KIND_SUBMISSION,
        key=submission.pk,
        title=submission.title,
        date=_submission_date(submission),
        source_reference=matter.display_reference,
        source_title=matter.title,
        recipient=", ".join(
            sorted(organisation.name for organisation in submission.recipients.all())
        ),
        score=score,
        reasons=tuple(reasons[:MAX_REASONS]),
        open_url=reverse("matters:matter_position", kwargs={"pk": matter.pk}),
        is_dismissed=dismissed,
    )


def opinion_candidates(
    profile: SubjectProfile,
    viewer: Any,
    matter_scores: dict[Any, tuple[float, list[str]]],
) -> list[MaterialSuggestion]:
    """Sent opinions on other visible Matters that may be useful background.

    Through the search chokepoint's SUBMISSION rows, so a restricted opinion —
    or an opinion on a restricted Matter — is not in the pool for a reader who
    may not open it. An opinion on this Matter is not background for it.
    """
    terms = profile.terms
    anchored = set(matter_scores) | set(profile.related_matter_ids)
    if not terms and not anchored:
        return []
    excluded = set(profile.dismissed_submission_ids) | set(profile.background_submission_ids)
    rows = (
        visible_documents(viewer)
        .filter(
            source_kind=SearchSourceKind.SUBMISSION,
            submission__status=SubmissionStatus.SENT,
            matter__data_class=profile.matter.data_class,
        )
        .exclude(matter_id=profile.matter.pk)
        .exclude(submission_id__in=list(excluded))
    )
    rows, any_match = _annotate_terms(rows, terms, title_field="title", title_vector="search_title")
    condition = any_match | Q(matter_id__in=list(anchored))
    rows = rows.filter(condition)
    if terms:
        rows = rows.order_by(
            F("title_hits").desc(), F("any_hits").desc(), "-submission__sent_at", "submission_id"
        )
    else:
        rows = rows.order_by("-submission__sent_at", "submission_id")

    columns = ["submission_id", "matter_id"]
    columns += [f"in_title_{i}" for i in range(len(terms))]
    columns += [f"anywhere_{i}" for i in range(len(terms))]
    pool = list(rows.values(*columns)[:MATERIAL_POOL])
    if not pool:
        return []

    submissions = {
        submission.pk: submission
        for submission in Submission.objects.visible_to(viewer)
        .filter(pk__in=[row["submission_id"] for row in pool])
        .select_related("matter", "matter__addressee_organisation")
        .prefetch_related("recipients")
    }
    # An opinion inherits the case for its Matter. Matters the pool query did
    # not already score are fetched and scored on their catalogue facts alone.
    missing = {row["matter_id"] for row in pool if row["matter_id"] not in matter_scores} - set(
        profile.related_matter_ids
    )
    parents = _fetch_matters(viewer, missing)
    parent_scores = dict(matter_scores)
    for matter_id, parent in parents.items():
        parent_scores[matter_id] = _matter_signals(profile, parent, None)

    found: list[MaterialSuggestion] = []
    for row in pool:
        submission = submissions.get(row["submission_id"])
        if submission is None:
            continue
        score, reasons = _material_signals(
            profile,
            title=submission.title,
            signals=_signals_from_row(row, terms),
            parent=parent_scores.get(submission.matter_id),
            related=submission.matter_id in profile.related_matter_ids,
            related_reason="Seotud teema arvamus",
            recipient_names=[organisation.name for organisation in submission.recipients.all()],
        )
        if score >= THRESHOLD:
            found.append(_submission_suggestion(submission, score=score, reasons=reasons))
    found.sort(key=_material_order)
    return found


def _material_signals(
    profile: SubjectProfile,
    *,
    title: str,
    signals: _TextSignals,
    parent: tuple[float, list[str]] | None,
    related: bool,
    related_reason: str,
    recipient_names: Sequence[str],
) -> tuple[float, list[str]]:
    """Score one opinion or letter: its own words, plus its Matter's case."""
    score = 0.0
    reasons: list[str] = []
    if related:
        score += W_RELATED_MATTER_MATERIAL
        reasons.append(related_reason)
    if parent is not None:
        parent_score, parent_reasons = parent
        score += parent_score
        reasons.extend(parent_reasons)

    their_acts = text.legal_instruments(title)
    shared_acts = text.shared_instruments(list(profile.instruments), their_acts)[:ACT_CAP]
    for act in shared_acts:
        reason = f"Sama õigusakt: {act.display}"
        if reason not in reasons:
            score += W_ACT
            reasons.insert(0, reason)

    title_terms = _without_act_words(list(signals.title_terms), shared_acts)
    body_terms = _without_act_words(list(signals.body_terms), shared_acts)
    own_score, own_reasons = _add_text_signals(0.0, [], title_terms, body_terms)
    score += own_score
    reasons.extend(reason for reason in own_reasons if reason not in reasons)

    if not any(reason.startswith("Sama asutus") for reason in reasons):
        ours = {normalize_for_matching(name) for name in profile.organisation_names.values()}
        shared = sorted(name for name in recipient_names if normalize_for_matching(name) in ours)
        if shared:
            score += W_ORGANISATION
            reasons.append(f"Sama adressaat: {shared[0]}")
    return score, _distinct(reasons)


def _distinct(reasons: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    kept: list[str] = []
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            kept.append(reason)
    return kept


def _material_order(item: MaterialSuggestion) -> tuple[Any, ...]:
    """Score, then the more recent letter, then the title, then the key."""
    return (
        -item.score,
        -(item.date.toordinal() if item.date else 0),
        item.title.casefold(),
        str(item.key),
    )


# ---------------------------------------------------------------------------
# Candidate material: the archive
# ---------------------------------------------------------------------------


def _archive_rows(profile: SubjectProfile, viewer: Any) -> QuerySet[OpinionArchiveSearchDocument]:
    """The archive letters this reader may be offered for this Matter.

    Empty unless the reader may read the archive at all. Then four exclusions,
    each a claim already made about the letter that outranks a suggestion:

    * a letter already filed onto *this* Matter (`OpinionArchiveMatterLink`) —
      that relationship is stronger than background and is shown where it is;
    * a letter a reviewer has called *not an opinion* on any of its
      occurrences — never offered as previous correspondence;
    * a letter already chosen as background here, or dismissed here;
    * a letter whose canonical Submission **this reader can see** — the
      Submission is offered instead, through its own channel. A Submission the
      reader may *not* see does not suppress the letter: the archive is
      legitimately theirs to read, and a hidden row must not become an oracle
      by silencing a visible one (docs/adr/0061 §5).

    `REJECTED`, `DUPLICATE` and `DEFERRED` are decisions about one proposed
    Matter match, not about the letter's worth as background, and are not
    excluded. `LINKED` letters may still be useful on another Matter.
    """
    if not may_read_archive(viewer):
        return OpinionArchiveSearchDocument.objects.none()
    excluded = set(profile.dismissed_binary_ids) | set(profile.background_binary_ids)
    rows = visible_archive(viewer).exclude(binary_id__in=list(excluded))
    rows = rows.exclude(
        Exists(
            OpinionArchiveMatterLink.objects.filter(
                binary_id=OuterRef("binary_id"), matter_id=profile.matter.pk
            )
        )
    )
    rows = rows.exclude(
        Exists(
            OpinionMatchCandidate.objects.filter(
                item__binary_id=OuterRef("binary_id"),
                state=OpinionCandidateState.NOT_AN_OPINION,
            )
        )
    )
    return rows.exclude(
        Exists(
            OpinionSubmissionImport.objects.filter(
                item__binary_id=OuterRef("binary_id"),
                submission__in=Submission.objects.visible_to(viewer).values("pk"),
            )
        )
    )


def _archive_suggestion(
    row: OpinionArchiveSearchDocument,
    *,
    score: float,
    reasons: Sequence[str],
    dismissed: bool = False,
) -> MaterialSuggestion:
    return MaterialSuggestion(
        kind=KIND_ARCHIVE,
        key=row.binary_id,
        title=row.title or "Pealkirjata kiri",
        date=row.document_date,
        source_reference=str(row.source_year) if row.source_year else "",
        source_title="",
        recipient=row.recipient,
        score=score,
        reasons=tuple(reasons[:MAX_REASONS]),
        open_url=reverse("legacy_import:opinion_archive_detail", kwargs={"pk": row.binary_id}),
        is_dismissed=dismissed,
    )


def archive_candidates(profile: SubjectProfile, viewer: Any) -> list[MaterialSuggestion]:
    """Held archive letters that carry this Matter's subject."""
    terms = profile.terms
    related_ids = list(profile.related_matter_ids)
    if not terms and not related_ids:
        return []
    rows = _archive_rows(profile, viewer)
    rows, any_match = _annotate_terms(rows, terms, title_field="title", title_vector=None)
    if related_ids:
        rows = rows.annotate(
            on_related_matter=Exists(
                OpinionArchiveMatterLink.objects.filter(
                    binary_id=OuterRef("binary_id"), matter_id__in=related_ids
                )
            )
        )
        condition = any_match | Q(on_related_matter=True)
    else:
        rows = rows.annotate(on_related_matter=Value(False))
        condition = any_match
    rows = rows.filter(condition)
    if terms:
        rows = rows.order_by(F("title_hits").desc(), F("any_hits").desc(), "-document_date", "pk")
    else:
        rows = rows.order_by("-document_date", "pk")

    columns = [f"in_title_{i}" for i in range(len(terms))] + [
        f"anywhere_{i}" for i in range(len(terms))
    ]
    found: list[MaterialSuggestion] = []
    for row in rows.only(
        "binary_id",
        "title",
        "recipient",
        "document_date",
        "source_year",
        "review_state",
        "is_linked",
    )[:ARCHIVE_POOL]:
        flags = {column: getattr(row, column, 0) for column in columns}
        score, reasons = _material_signals(
            profile,
            title=row.title,
            signals=_signals_from_row(flags, terms),
            parent=None,
            related=bool(getattr(row, "on_related_matter", False)),
            related_reason="Seotud teema arhiivikiri",
            recipient_names=_recipient_names(row.recipient, profile),
        )
        if row.is_linked or row.review_state in {
            OpinionCandidateState.APPLIED,
            OpinionCandidateState.LINKED,
        }:
            score += W_ARCHIVE_REVIEWED
        if score >= THRESHOLD:
            found.append(_archive_suggestion(row, score=score, reasons=reasons))
    found.sort(key=_material_order)
    return found


def _recipient_names(recipient: str, profile: SubjectProfile) -> list[str]:
    """Our organisations that the letter's recipient text names."""
    folded = normalize_for_matching(recipient)
    if not folded:
        return []
    return [
        name
        for name in profile.organisation_names.values()
        if normalize_for_matching(name) and normalize_for_matching(name) in folded
    ]


# ---------------------------------------------------------------------------
# Hidden (dismissed) candidates
# ---------------------------------------------------------------------------


def hidden_candidates(
    profile: SubjectProfile, viewer: Any
) -> tuple[list[RelatedMatterSuggestion], list[MaterialSuggestion]]:
    """What «Näita peidetud» shows: dismissed candidates this reader may see."""
    matters: list[RelatedMatterSuggestion] = []
    if profile.dismissed_matter_ids:
        matters = [
            RelatedMatterSuggestion(matter=matter, score=0.0, reasons=(), is_dismissed=True)
            for matter in Matter.objects.visible_to(viewer)
            .filter(pk__in=list(profile.dismissed_matter_ids))
            .select_related("addressee_organisation", "stage")
        ]
        matters.sort(key=_matter_order)

    materials: list[MaterialSuggestion] = []
    if profile.dismissed_submission_ids:
        materials.extend(
            _submission_suggestion(submission, score=0.0, reasons=(), dismissed=True)
            for submission in Submission.objects.visible_to(viewer)
            .filter(pk__in=list(profile.dismissed_submission_ids))
            .select_related("matter")
            .prefetch_related("recipients")
        )
    if profile.dismissed_binary_ids and may_read_archive(viewer):
        materials.extend(
            _archive_suggestion(row, score=0.0, reasons=(), dismissed=True)
            for row in visible_archive(viewer).filter(
                binary_id__in=list(profile.dismissed_binary_ids)
            )
        )
    materials.sort(key=_material_order)
    return matters, materials


def hidden_count(profile: SubjectProfile, viewer: Any) -> int:
    """How many dismissed candidates this reader may see. Never more than that."""
    if not profile.has_dismissals:
        return 0
    total = 0
    if profile.dismissed_matter_ids:
        total += (
            Matter.objects.visible_to(viewer)
            .filter(pk__in=list(profile.dismissed_matter_ids))
            .count()
        )
    if profile.dismissed_submission_ids:
        total += (
            Submission.objects.visible_to(viewer)
            .filter(pk__in=list(profile.dismissed_submission_ids))
            .count()
        )
    if profile.dismissed_binary_ids and may_read_archive(viewer):
        total += (
            visible_archive(viewer).filter(binary_id__in=list(profile.dismissed_binary_ids)).count()
        )
    return total


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------


def suggestions_for(
    matter: Matter,
    viewer: Any,
    *,
    limit: int = DEFAULT_LIMIT,
    include_hidden: bool = False,
) -> Suggestions:
    """Everything «Võimalikud seosed» shows for this Matter and this reader.

    Read-only: computes, returns, and leaves every table exactly as it was.
    """
    limit = max(1, min(int(limit), MAX_LIMIT))
    profile = build_profile(matter)

    matters, matter_scores = related_matter_candidates(profile, viewer)
    materials = opinion_candidates(profile, viewer, matter_scores)
    materials.extend(archive_candidates(profile, viewer))
    materials.sort(key=_material_order)

    hidden_matters: list[RelatedMatterSuggestion] = []
    hidden_materials: list[MaterialSuggestion] = []
    if include_hidden:
        hidden_matters, hidden_materials = hidden_candidates(profile, viewer)
        count = len(hidden_matters) + len(hidden_materials)
    else:
        count = hidden_count(profile, viewer)

    return Suggestions(
        matters=tuple(matters[:limit]),
        materials=tuple(materials[:limit]),
        limit=limit,
        more_matters=max(0, min(len(matters), MAX_LIMIT) - limit),
        more_materials=max(0, min(len(materials), MAX_LIMIT) - limit),
        hidden_count=count,
        hidden_matters=tuple(hidden_matters),
        hidden_materials=tuple(hidden_materials),
        hidden_shown=include_hidden,
    )
