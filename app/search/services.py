"""Estonian search over the rebuildable projection.

The shape of this module is dictated by one rule: **authorization is applied to
the base queryset, before anything is filtered, ranked, counted or sliced.**
Not after. Not as a post-pass over results. A restricted Matter must not be able
to influence a result count, a page boundary or a ranking position, because each
of those leaks its existence just as surely as showing its title would
(master specification 5.2, Stage-2A brief 27, Stage-2B brief 43–44).

Stage 2B extends that rule downward. The projection now holds entries,
submissions and document fragments as well as matters, and each of those can be
restricted *below* its Matter. So the predicate joins the live child row —
`Entry`, `Submission` or `Document` — and reads its current
`visibility_override`. Nothing about visibility is stored in the index, at
either level. A document restricted a second ago is invisible on the next query
even though its fragments' search rows have not been touched
(docs/adr/0005, 0013, 0014).

It is still **one queryset and one statement**. Stage 2A worried that mixed
source kinds would force a union, and that a count taken across a union is a
count that can disagree with the rows beside it. The kind-to-override mapping in
`app.core.authorization.projected_visibility_q` is what avoids that.

Ranking is deterministic and tiered. Exact answers come first and fuzzy answers
last, so a lawyer typing a reference gets that file rather than a relevance
ordering's opinion about it. A phrase found on page 14 of an annex never
outranks an exact `2026_17`. Nothing is ranked by how often a record is opened
or by who owns it: a search that quietly favours popular matters is a search
that hides the neglected ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import Case, F, FloatField, Func, Q, QuerySet, TextField, Value, When
from django.db.models.expressions import Combinable
from django.db.models.functions import Greatest

from app.core.authorization import apply as apply_scope
from app.core.authorization import projected_visibility_q, scope_for_user
from app.core.text import normalize_for_matching
from app.matters.models import Matter
from app.search.models import SOURCE_OVERRIDE_FIELDS, SearchDocument, SearchSourceKind


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


class Headline(Func):
    """``ts_headline``: the matching words with their neighbours.

    PostgreSQL's own snippet function rather than a hand-rolled one, because it
    highlights the *lexemes the query actually matched* — Estonian inflection
    means the word on the page is frequently not the word that was typed, and a
    substring search would highlight nothing at all.
    """

    function = "ts_headline"
    output_field = TextField()


#: Markers around the matched words. Deliberately not HTML: this string is
#: escaped like any other text, split on these markers in Python, and rendered
#: as a sequence of highlighted and plain runs. There is no path by which
#: PostgreSQL output becomes markup in the page (Stage-2B brief 42, 70).
HIGHLIGHT_START = "⦑"
HIGHLIGHT_STOP = "⦒"

HEADLINE_OPTIONS = (
    f"StartSel={HIGHLIGHT_START}, StopSel={HIGHLIGHT_STOP}, "
    "MaxWords=32, MinWords=12, ShortWord=3, MaxFragments=2, "
    "FragmentDelimiter= … , HighlightAll=FALSE"
)


#: Why a row came back. Shown beside each result so a lawyer can see whether the
#: system understood the query the way they meant it.
MATCH_REFERENCE = "reference"
MATCH_TITLE = "title"
MATCH_PHRASE = "phrase"
MATCH_TAXONOMY = "taxonomy"
MATCH_CHILD_TITLE = "child_title"
MATCH_DOCUMENT_TITLE = "document_title"
MATCH_FULLTEXT = "fulltext"
MATCH_SIMPLE = "simple"
MATCH_FUZZY = "fuzzy"

MATCH_LABELS: dict[str, str] = {
    MATCH_REFERENCE: "Viide",
    MATCH_TITLE: "Pealkiri",
    MATCH_PHRASE: "Pealkirja fraas",
    MATCH_TAXONOMY: "Asutus, valdkond või silt",
    MATCH_CHILD_TITLE: "Sissekande või arvamuse pealkiri",
    MATCH_DOCUMENT_TITLE: "Dokumendi nimi",
    MATCH_FULLTEXT: "Tekstiotsing",
    MATCH_SIMPLE: "Sõnaotsing",
    MATCH_FUZZY: "Ligilähedane",
}

#: What kind of thing matched. Shown as a badge so a result set mixing a matter,
#: an entry and page 14 of an annex is readable at a glance.
SOURCE_LABELS: dict[str, str] = {
    SearchSourceKind.MATTER.value: "Teema",
    SearchSourceKind.ENTRY.value: "Sissekanne",
    SearchSourceKind.SUBMISSION.value: "Arvamus",
    SearchSourceKind.DOCUMENT_FRAGMENT.value: "Dokument",
    SearchSourceKind.LEGACY_SOURCE_PAGE.value: "Ajalooline OneNote",
}

#: Deterministic tiers. Higher wins, and the gaps are wide so that a strong
#: ``ts_rank`` inside one tier can never overtake the tier above it.
TIER_REFERENCE = 100
TIER_TITLE_EXACT = 90
TIER_TITLE_PHRASE = 80
TIER_ALIAS = 70
TIER_CHILD_TITLE = 66
TIER_DOCUMENT_TITLE = 62
TIER_FULLTEXT = 60
TIER_SIMPLE = 50
TIER_TRIGRAM = 40

TIER_MATCH_KIND: dict[int, str] = {
    TIER_REFERENCE: MATCH_REFERENCE,
    TIER_TITLE_EXACT: MATCH_TITLE,
    TIER_TITLE_PHRASE: MATCH_PHRASE,
    TIER_ALIAS: MATCH_TAXONOMY,
    TIER_CHILD_TITLE: MATCH_CHILD_TITLE,
    TIER_DOCUMENT_TITLE: MATCH_DOCUMENT_TITLE,
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

CHILD_KINDS = (
    SearchSourceKind.ENTRY,
    SearchSourceKind.SUBMISSION,
    SearchSourceKind.LEGACY_SOURCE_PAGE,
)


@dataclass(frozen=True)
class SnippetRun:
    text: str
    highlight: bool


@dataclass(frozen=True)
class SearchResult:
    matter: Matter
    match_kind: str
    rank: float
    source_kind: str = SearchSourceKind.MATTER.value
    source_locator: str = ""
    document_title: str = ""
    document_id: Any = None
    document_version_id: Any = None
    entry_id: Any = None
    submission_id: Any = None
    source_page_id: Any = None
    snippet: tuple[SnippetRun, ...] = ()

    @property
    def match_label(self) -> str:
        return MATCH_LABELS.get(self.match_kind, "")

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS.get(self.source_kind, "")

    @property
    def is_matter(self) -> bool:
        return self.source_kind == SearchSourceKind.MATTER


def visible_documents(user: Any) -> QuerySet[SearchDocument]:
    """The projection, scoped to one user, before any query term is applied.

    This is the chokepoint. Everything else in the module builds on the
    queryset this returns, so there is no path to a result that skipped it.
    """
    scope = scope_for_user(user)
    documents = SearchDocument.objects.select_related(
        "matter",
        "matter__owner",
        "matter__stage",
        "matter__source_organisation",
        "matter__addressee_organisation",
        "document",
        "document_version",
        "entry",
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
    alias = Q(alias_text__icontains=term) | Q(alias_text__icontains=normalized)
    child_title = Q(source_kind__in=CHILD_KINDS) & (
        Q(title__icontains=term) | Q(identifiers__iexact=term)
    )
    document_title = Q(source_kind=SearchSourceKind.DOCUMENT_FRAGMENT) & (
        Q(title__icontains=term) | Q(identifiers__icontains=term)
    )
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


def search_documents(*, query: str, user: Any) -> QuerySet[SearchDocument]:
    """The ranked, authorized queryset. Countable and sliceable as it stands."""
    term = (query or "").strip()
    if not term:
        return visible_documents(user).none()

    scoped = visible_documents(user).annotate(
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
        )
    )


def result_count(*, query: str, user: Any) -> int:
    """How many results this user has. Never includes anything they cannot see."""
    return search_documents(query=query, user=user).count()


def matching_matter_ids(*, query: str, user: Any) -> QuerySet[SearchDocument]:
    """The Matters this query reaches, as a subquery for another queryset.

    The register's live search narrows itself with this rather than growing a
    text search of its own. One projection, one set of tiers, one authorization
    predicate — a second full-text implementation over the same Matters would
    be a second opinion about what a word means, and the two would drift
    (Stage-2E.1 brief 8).

    Returns a values queryset rather than a list of ids on purpose: composed
    into ``filter(pk__in=...)`` it stays a single SQL statement, so a keystroke
    over a corpus-scale register never becomes a Python-side pass over
    thousands of rows.

    ``order_by()`` clears the ranking first. Ordering columns join a ``SELECT
    DISTINCT`` and a ``GROUP BY``, and the ranking annotations are not columns
    the caller wants — leaving them in produces a subquery PostgreSQL rejects.

    Ranking is deliberately discarded here. The register has its own sort
    (``?jarjestus=``), and silently reordering it by relevance the moment
    somebody types would move rows for reasons the column headers do not
    explain.
    """
    return search_documents(query=query, user=user).order_by().values("matter_id")


def search(*, query: str, user: Any, limit: int = MAX_RESULTS) -> list[SearchResult]:
    """Find things. Authorization first, deterministic tiers, then relevance."""
    term = (query or "").strip()
    documents = list(search_documents(query=term, user=user)[:limit])
    if not documents:
        return []

    snippets = _snippets_for(documents, term=term, user=user)
    return [
        SearchResult(
            matter=document.matter,
            # Annotations, not columns; django-stubs cannot see them on the
            # model, so they are read by name.
            match_kind=TIER_MATCH_KIND.get(
                int(getattr(document, "match_tier", TIER_FULLTEXT)), MATCH_FULLTEXT
            ),
            rank=float(getattr(document, "relevance", 0.0) or 0.0),
            source_kind=document.source_kind,
            source_locator=document.source_locator,
            document_title=document.document.title if document.document else "",
            document_id=document.document_id,
            document_version_id=document.document_version_id,
            entry_id=document.entry_id,
            submission_id=document.submission_id,
            source_page_id=document.matter_source_page_id,
            snippet=snippets.get(document.pk, ()),
        )
        for document in documents
    ]


def _snippets_for(
    documents: list[SearchDocument], *, term: str, user: Any
) -> dict[Any, tuple[SnippetRun, ...]]:
    """Highlighted excerpts for the rows about to be rendered, and no others.

    A second query rather than an annotation on the first. ``ts_headline`` is
    expensive — it re-parses the text it is summarising — and annotating it onto
    the ranked queryset would compute it for every row the filter matched, not
    the fifty being shown.

    The queryset is re-scoped through :func:`visible_documents` rather than
    fetched by primary key alone. The ids came from an authorized query a moment
    ago, so this is redundant; it is here because "the ids are already checked"
    is exactly the assumption that stops being true when somebody later reuses
    this function.
    """
    with_body = [document.pk for document in documents if document.body_text]
    if not with_body:
        return {}

    estonian = SearchQuery(term, config="estonian", search_type="websearch")
    rows = (
        visible_documents(user)
        .filter(pk__in=with_body)
        .annotate(
            headline=Headline(
                Value("estonian"),
                F("body_text"),
                estonian,
                Value(HEADLINE_OPTIONS),
            )
        )
        .values_list("pk", "headline")
    )
    return {pk: _split_highlights(headline or "") for pk, headline in rows}


def _split_highlights(headline: str) -> tuple[SnippetRun, ...]:
    """Marked text into runs, so the template can render without raw HTML.

    The markers are two private-use bracket characters that PostgreSQL inserted
    and that cannot occur in the source text. Splitting on them here means the
    page never receives a string that has to be trusted — every run is escaped
    normally and the highlight is a tag the template writes itself.
    """
    runs: list[SnippetRun] = []
    for chunk in headline.split(HIGHLIGHT_START):
        if HIGHLIGHT_STOP in chunk:
            highlighted, _, rest = chunk.partition(HIGHLIGHT_STOP)
            if highlighted:
                runs.append(SnippetRun(text=highlighted, highlight=True))
            if rest:
                runs.append(SnippetRun(text=rest, highlight=False))
        elif chunk:
            runs.append(SnippetRun(text=chunk, highlight=False))
    return tuple(runs)


def search_matters(*, query: str, user: Any, limit: int = MAX_RESULTS) -> list[SearchResult]:
    """Matter-level results only.

    Kept for callers that want the Stage-2A behaviour — a navigation shortcut,
    a picker — rather than the full mixed corpus.
    """
    documents = search_documents(query=query, user=user).filter(
        source_kind=SearchSourceKind.MATTER
    )[:limit]
    return [
        SearchResult(
            matter=document.matter,
            match_kind=TIER_MATCH_KIND.get(
                int(getattr(document, "match_tier", TIER_FULLTEXT)), MATCH_FULLTEXT
            ),
            rank=float(getattr(document, "relevance", 0.0) or 0.0),
        )
        for document in documents
    ]
