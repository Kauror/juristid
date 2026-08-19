"""Estonian search over the rebuildable projection.

The shape of this module is dictated by one rule: **authorization is applied to
the base queryset, before anything is filtered, ranked, counted or sliced.**
Not after. Not as a post-pass over results. A restricted Matter must not be able
to influence a result count, a page boundary or a ranking position, because each
of those leaks its existence just as surely as showing its title would
(master specification 5.2, Stage-2A brief 27).

The projection is joined to the live Matter on every query and the visibility
predicate is evaluated against *that*, never against anything stored in the
index. A Matter restricted a second ago is invisible on the next query even
though its search document has not been touched.

Ranking is deterministic and tiered. Exact answers come first and fuzzy answers
last, so a lawyer typing a reference gets that file rather than a relevance
ordering's opinion about it. Nothing is ranked by how often a record is opened
or by who owns it: a search that quietly favours popular matters is a search
that hides the neglected ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import Case, F, FloatField, Func, Q, QuerySet, Value, When
from django.db.models.expressions import Combinable
from django.db.models.functions import Greatest

from app.core.authorization import apply as apply_scope
from app.core.authorization import matter_visibility_q, scope_for_user
from app.core.text import normalize_for_matching
from app.matters.models import Matter
from app.search.models import SearchDocument, SearchSourceKind


class WordSimilarity(Func):
    """``pg_trgm`` word similarity: the query against the *best-matching run of
    words* inside the target, rather than against the whole string.

    The deployment found why this matters. ``similarity()`` divides shared
    trigrams by the trigrams of the entire string, so it decays as titles get
    longer — and real Estonian legal titles are long. Searching
    ``pakendiseeaduse`` against
    "Rehearsal: sünteetiline pakendiseaduse muutmise eelnõu" scored **0.259**,
    under the 0.3 threshold, while the intended word was sitting in plain sight.
    ``word_similarity()`` scores the same pair **0.824**.

    The earlier unit test passed only because its synthetic title was short
    enough to hide the effect, which is why the regression test for this uses a
    realistically long one.

    Argument order is not symmetric: ``word_similarity(query, target)`` asks
    "how well does the query match some part of the target", which is the
    question being asked. Reversed, it asks the opposite and scores badly.
    """

    function = "word_similarity"
    output_field = FloatField()


#: Why a row came back. Shown beside each result so a lawyer can see whether the
#: system understood the query the way they meant it.
MATCH_REFERENCE = "reference"
MATCH_TITLE = "title"
MATCH_PHRASE = "phrase"
MATCH_TAXONOMY = "taxonomy"
MATCH_FULLTEXT = "fulltext"
MATCH_SIMPLE = "simple"
MATCH_FUZZY = "fuzzy"

MATCH_LABELS: dict[str, str] = {
    MATCH_REFERENCE: "Viide",
    MATCH_TITLE: "Pealkiri",
    MATCH_PHRASE: "Pealkirja fraas",
    MATCH_TAXONOMY: "Asutus, valdkond või silt",
    MATCH_FULLTEXT: "Tekstiotsing",
    MATCH_SIMPLE: "Sõnaotsing",
    MATCH_FUZZY: "Ligilähedane",
}

#: Deterministic tiers. Higher wins, and the gaps are wide so that a strong
#: ``ts_rank`` inside one tier can never overtake the tier above it.
TIER_REFERENCE = 100
TIER_TITLE_EXACT = 90
TIER_TITLE_PHRASE = 80
TIER_ALIAS = 70
TIER_FULLTEXT = 60
TIER_SIMPLE = 50
TIER_TRIGRAM = 40

TIER_MATCH_KIND: dict[int, str] = {
    TIER_REFERENCE: MATCH_REFERENCE,
    TIER_TITLE_EXACT: MATCH_TITLE,
    TIER_TITLE_PHRASE: MATCH_PHRASE,
    TIER_ALIAS: MATCH_TAXONOMY,
    TIER_FULLTEXT: MATCH_FULLTEXT,
    TIER_SIMPLE: MATCH_SIMPLE,
    TIER_TRIGRAM: MATCH_FUZZY,
}

MAX_RESULTS = 50

#: Below this, trigram matching is noise.
#:
#: Higher than pg_trgm's 0.3 default, deliberately. `word_similarity` scores far
#: more generously than `similarity` — it ignores the length of everything it
#: did *not* match — so keeping the old threshold would turn the last-resort
#: fuzzy tier into a source of near-random results. 0.6 still catches an
#: ordinary one-letter typo (the case above scores 0.824) while rejecting words
#: that merely share a stem.
TRIGRAM_THRESHOLD = 0.6


@dataclass(frozen=True)
class SearchResult:
    matter: Matter
    match_kind: str
    rank: float

    @property
    def match_label(self) -> str:
        return MATCH_LABELS.get(self.match_kind, "")


def visible_documents(user: Any) -> QuerySet[SearchDocument]:
    """The projection, scoped to one user, before any query term is applied.

    This is the chokepoint. Everything else in the module builds on the
    queryset this returns, so there is no path to a result that skipped it.
    """
    scope = scope_for_user(user)
    documents = SearchDocument.objects.filter(source_kind=SearchSourceKind.MATTER).select_related(
        "matter",
        "matter__owner",
        "matter__stage",
        "matter__source_organisation",
        "matter__addressee_organisation",
    )
    # The predicate is evaluated against the joined live Matter, never against
    # anything the projection stores. Restricting a Matter takes effect on the
    # next query with no reindex.
    return apply_scope(documents, matter_visibility_q(scope, prefix="matter__"))


def _reference_condition(term: str) -> Q | None:
    """``2026_184``, ``2026-184`` and ``2026 184`` all mean the same file.

    Returns ``None`` when the term is not a reference at all, which is what
    tells the caller to fall through to the text tiers.
    """
    candidate = " ".join(term.split())
    parsed = Matter.parse_reference(candidate.replace("-", "_").replace(" ", "_"))
    if parsed is None:
        return None
    year, number = parsed
    return Q(matter__reference_year=year, matter__reference_number=number)


def _build(term: str) -> tuple[Q, Combinable, Combinable]:
    """One query with every tier expressed as SQL.

    Deliberately a single statement. Running the tiers as separate queries and
    stitching the results in Python would make the result count depend on
    Python-side deduplication, and a count that is computed anywhere other than
    the database is a count that can disagree with the rows.
    """
    normalized = normalize_for_matching(term)
    estonian = SearchQuery(term, config="estonian", search_type="websearch")
    simple = SearchQuery(term, config="simple", search_type="websearch")
    phrase = SearchQuery(term, config="estonian", search_type="phrase")

    reference = _reference_condition(term)
    if reference is not None:
        # A well-formed reference is answered exactly or not at all. Fuzziness
        # here is actively harmful: `2026_1` and `2026_2` are two different
        # files, trigram similarity rates them as nearly identical, and a lawyer
        # asking for one would be handed the other. It also breaks the
        # navigation shortcut, which fires only when a query resolves to
        # exactly one result.
        return (
            reference,
            Value(float(TIER_REFERENCE), output_field=FloatField()),
            Value(1.0, output_field=FloatField()),
        )

    title_exact = Q(matter__title__iexact=term) | Q(title__iexact=term)
    # Against the title-only vector. The combined vector also carries
    # identifiers and aliases, so a phrase query against it would report an
    # organisation-name hit as a title match.
    title_phrase = Q(search_title=phrase)
    alias = Q(alias_text__icontains=term) | Q(alias_text__icontains=normalized)
    fulltext = Q(search_estonian=estonian)
    simple_match = Q(search_simple=simple)
    fuzzy = Q(title_similarity__gte=TRIGRAM_THRESHOLD)

    matched = title_exact | title_phrase | alias | fulltext | simple_match | fuzzy

    tier = Case(
        When(title_exact, then=Value(TIER_TITLE_EXACT)),
        When(title_phrase, then=Value(TIER_TITLE_PHRASE)),
        When(alias, then=Value(TIER_ALIAS)),
        When(fulltext, then=Value(TIER_FULLTEXT)),
        When(simple_match, then=Value(TIER_SIMPLE)),
        default=Value(TIER_TRIGRAM),
        output_field=FloatField(),
    )
    # Within a tier, relevance decides. The weights set at index time (title A,
    # identifiers B, aliases C, body D) are what ts_rank reads.
    relevance = Case(
        When(fulltext, then=SearchRank(F("search_estonian"), estonian)),
        When(simple_match, then=SearchRank(F("search_simple"), simple)),
        default=F("title_similarity"),
        output_field=FloatField(),
    )
    return matched, tier, relevance


def search_documents(*, query: str, user: Any) -> QuerySet[SearchDocument]:
    """The ranked, authorized queryset. Countable and sliceable as it stands."""
    term = (query or "").strip()
    if not term:
        return visible_documents(user).none()

    scoped = visible_documents(user).annotate(
        title_similarity=Greatest(
            WordSimilarity(Value(term), F("title")),
            WordSimilarity(Value(term), F("identifiers")),
            output_field=FloatField(),
        )
    )
    matched, tier, relevance = _build(term)
    return (
        scoped.filter(matched)
        .annotate(match_tier=tier, relevance=relevance)
        .order_by(
            "-match_tier", "-relevance", "-matter__reference_year", "-matter__reference_number"
        )
    )


def result_count(*, query: str, user: Any) -> int:
    """How many results this user has. Never includes anything they cannot see."""
    return search_documents(query=query, user=user).count()


def search_matters(*, query: str, user: Any, limit: int = MAX_RESULTS) -> list[SearchResult]:
    """Find Matters. Authorization first, deterministic tiers, then relevance."""
    documents = search_documents(query=query, user=user)[:limit]
    return [
        SearchResult(
            matter=document.matter,
            # Annotations, not columns; django-stubs cannot see them on the
            # model, so they are read by name.
            match_kind=TIER_MATCH_KIND.get(
                int(getattr(document, "match_tier", TIER_FULLTEXT)), MATCH_FULLTEXT
            ),
            rank=float(getattr(document, "relevance", 0.0) or 0.0),
        )
        for document in documents
    ]
