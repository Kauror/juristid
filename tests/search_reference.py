"""The search query as it stood before ENG-010, kept for one purpose: proving
that the indexed rewrite answers every query exactly as this did.

Test-only. Nothing in `app/` imports it, and it is not a second search
implementation anybody may call: it is the *old* one, frozen at commit
ad1593a1 (Round 5 final main), so `tests/test_search_differential.py` can run
both over the same corpus and compare their ordered answers row for row.

Two things were rewritten here and nothing else. `visible_documents` and
`search_documents` became `reference_visible_documents` and
`reference_search_documents`, so a test cannot import this module's names by
accident in place of the real ones.
"""

from __future__ import annotations

from typing import Any

from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import Case, F, FloatField, Q, QuerySet, Value, When
from django.db.models.expressions import Combinable
from django.db.models.functions import Greatest

from app.core.authorization import apply as apply_scope
from app.core.authorization import projected_visibility_q, scope_for_user
from app.core.text import normalize_for_matching
from app.matters.models import Matter
from app.search.models import (
    INDEX_VERSION,
    SOURCE_OVERRIDE_FIELDS,
    SearchDocument,
    SearchSourceKind,
)
from app.search.services import WordSimilarity, clean_query

# Frozen copies, not imports: a later round that widens a tier list must not
# silently widen the reference it is being compared with.
TIER_REFERENCE = 100
TIER_TITLE_EXACT = 90
TIER_TITLE_PHRASE = 80
TIER_ALIAS = 70
TIER_CHILD_TITLE = 66
TIER_DOCUMENT_TITLE = 62
TIER_FULLTEXT = 60
TIER_SIMPLE = 50
TIER_TRIGRAM = 40
TRIGRAM_THRESHOLD = 0.6
CHILD_KINDS = (
    SearchSourceKind.ENTRY,
    SearchSourceKind.SUBMISSION,
    SearchSourceKind.LEGACY_SOURCE_PAGE,
)
ALIAS_KINDS = (
    SearchSourceKind.MATTER,
    SearchSourceKind.ENTRY,
    SearchSourceKind.SUBMISSION,
    SearchSourceKind.LEGACY_SOURCE_PAGE,
)


def reference_visible_documents(user: Any) -> QuerySet[SearchDocument]:
    """The projection, scoped to one user, before any query term is applied.

    This is the chokepoint. Everything else in the module builds on the
    queryset this returns, so there is no path to a result that skipped it.
    """
    scope = scope_for_user(user)
    # Rows built under an older projection contract are not read at all.
    #
    # Visibility filtering is row-granular: it decides whether a row is returned,
    # never what is inside one. A MATTER row indexed before AUTH-003 can hold a
    # RESTRICTED Kaasamine's words in its tsvector, and no predicate can take
    # them back out — so the only safe answer is to stop trusting the row.
    #
    # The cost is that search is empty between deploying this and running
    # `rebuild_search_index`. That is the correct direction to fail: a reader
    # sees too little and can tell, rather than reading something they should
    # not and cannot (docs/adr/0038).
    documents = SearchDocument.objects.filter(index_version=INDEX_VERSION).select_related(
        "matter",
        "matter__owner",
        "matter__stage",
        "matter__addressee_organisation",
        "document",
        "document_version",
        "entry",
        "engagement",
        "submission",
        "matter_source_page",
        "matter_source_page__source_page",
    )
    # The predicate is evaluated against the joined live rows — the Matter, and
    # for a child row its own current override — never against anything the
    # projection stores. Restricting either takes effect on the next query with
    # no reindex.
    return apply_scope(
        documents,
        projected_visibility_q(
            scope,
            kind_field="source_kind",
            kind_overrides=SOURCE_OVERRIDE_FIELDS,
            parent_prefix="matter__",
        ),
    )


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
        #
        # Restricted to the Matter row: a reference lookup means "open that
        # file", and returning its twelve indexed pages alongside it would turn
        # a navigation into a result list.
        return (
            reference & Q(source_kind=SearchSourceKind.MATTER),
            Value(float(TIER_REFERENCE), output_field=FloatField()),
            Value(1.0, output_field=FloatField()),
        )

    is_matter = Q(source_kind=SearchSourceKind.MATTER)
    title_exact = is_matter & (Q(matter__title__iexact=term) | Q(title__iexact=term))
    # Against the title-only vector. The combined vector also carries
    # identifiers and aliases, so a phrase query against it would report an
    # organisation-name hit as a title match.
    title_phrase = is_matter & Q(search_title=phrase)
    alias = Q(source_kind__in=ALIAS_KINDS) & (
        Q(alias_text__icontains=term) | Q(alias_text__icontains=normalized)
    )
    child_title = Q(source_kind__in=CHILD_KINDS) & (
        Q(title__icontains=term) | Q(identifiers__iexact=term)
    )
    # A document's own row as well as its pages: an upload nobody extracted
    # has only the former, and before it existed was unfindable by name
    # (ENG-030). Its title and filenames are the whole of the row.
    document_title = Q(
        source_kind__in=(SearchSourceKind.DOCUMENT_FRAGMENT, SearchSourceKind.DOCUMENT)
    ) & (Q(title__icontains=term) | Q(identifiers__icontains=term))
    fulltext = Q(search_estonian=estonian)
    simple_match = Q(search_simple=simple)
    # Fuzzy matching is a short-string feature: titles, references, names. It is
    # confined to Matter rows so the partial trigram indexes can serve it, and
    # because "which page of this annex is nearly spelled like your typo" is not
    # a question anybody has (Stage-2B brief 40, 41).
    fuzzy = is_matter & Q(title_similarity__gte=TRIGRAM_THRESHOLD)

    matched = (
        title_exact
        | title_phrase
        | alias
        | child_title
        | document_title
        | fulltext
        | simple_match
        | fuzzy
    )

    tier = Case(
        When(title_exact, then=Value(TIER_TITLE_EXACT)),
        When(title_phrase, then=Value(TIER_TITLE_PHRASE)),
        When(alias, then=Value(TIER_ALIAS)),
        When(child_title, then=Value(TIER_CHILD_TITLE)),
        When(document_title, then=Value(TIER_DOCUMENT_TITLE)),
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


def reference_search_documents(*, query: str, user: Any) -> QuerySet[SearchDocument]:
    """The ranked, authorized queryset. Countable and sliceable as it stands."""
    term = clean_query(query)
    if not term:
        return reference_visible_documents(user).none()

    scoped = reference_visible_documents(user).annotate(
        # Computed only for Matter rows. PostgreSQL's CASE short-circuits, so
        # `word_similarity` is never called on a document fragment — which
        # matters when the design headroom for that kind is millions of rows.
        title_similarity=Case(
            When(
                source_kind=SearchSourceKind.MATTER,
                then=Greatest(
                    WordSimilarity(Value(term), F("title")),
                    WordSimilarity(Value(term), F("identifiers")),
                    output_field=FloatField(),
                ),
            ),
            default=Value(0.0),
            output_field=FloatField(),
        )
    )
    matched, tier, relevance = _build(term)
    return (
        scoped.filter(matched)
        .annotate(match_tier=tier, relevance=relevance)
        .order_by(
            "-match_tier",
            "-relevance",
            "-matter__reference_year",
            "-matter__reference_number",
            "source_kind",
            "source_locator",
            # The last tie-break, and the only one guaranteed to break the tie.
            # Everything above it can repeat: an archive Matter has no
            # reference at all, and two fragments of two different files
            # attached to the same Matter are both "lk 3". Without a unique
            # final key PostgreSQL is free to return those rows in a different
            # order on every request, which moves results between the fifty
            # shown and the ones cut off — a row that vanishes on reload for no
            # reason a reader can see.
            "pk",
        )
    )
