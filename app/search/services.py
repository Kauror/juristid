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

It is still **one queryset**, and each question asked of it is one statement.
Stage 2A worried that mixed source kinds would force a union, and that a count
taken across a union is a count that can disagree with the rows beside it. The
kind-to-override mapping in `app.core.authorization.projected_visibility_q` is
what avoids that. The ranked page and the count are two statements over the same
filtered queryset, so they cannot describe different result sets.

**Every matching tier is a predicate an index can serve** (ENG-010). Until that,
the tiers were ORed into one WHERE clause in which one arm tested the *joined*
Matter title, one filtered on a `word_similarity` annotation and three used
`UPPER(x) LIKE UPPER('%…%')`, which no index on `x` can answer. A single
unindexable arm makes an OR unindexable, so every search read and ranked the
whole projection — 0 of 132 audited plans used a text index, and a READER's page
also paid for a `DISTINCT` over every selected column. Now each arm tests only
this table's own columns with an operator an index serves: `@@` against the
three GIN vectors, `ILIKE` and `%>` against the trigram indexes. PostgreSQL can
then answer a selective query with one bitmap per arm OR-ed together, and still
chooses a single sequential pass when a word occurs in half the corpus — which,
for a word like that, is the right plan.

**The ranking statement carries ids and sort keys, nothing else.** The body
text, the vectors and the eleven joined presentation tables used to travel
through the sort for every matching row so that fifty of them could be shown.
They are now fetched afterwards, for the page's rows only (`_present`).

Ranking is deterministic and tiered. Exact answers come first and fuzzy answers
last, so a lawyer typing a reference gets that file rather than a relevance
ordering's opinion about it. A phrase found on page 14 of an annex never
outranks an exact `2026_17`. Nothing is ranked by how often a record is opened
or by who owns it: a search that quietly favours popular matters is a search
that hides the neglected ones.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from django.contrib.postgres.search import SearchQuery, SearchRank
from django.core.paginator import Page, Paginator
from django.db import connection
from django.db.models import (
    Case,
    F,
    Field,
    FloatField,
    Func,
    Lookup,
    Q,
    QuerySet,
    TextField,
    Value,
    When,
)
from django.db.models.expressions import Combinable
from django.db.models.functions import Greatest

from app.core.authorization import projected_visibility_q, scope_for_user
from app.core.text import normalize_for_matching
from app.matters.models import Matter
from app.search.generations import active_generation_expression
from app.search.models import (
    INDEX_VERSION,
    SOURCE_OVERRIDE_FIELDS,
    SearchDocument,
    SearchSourceKind,
)


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


class ContainsAnyCase(Lookup):
    """``column ILIKE '%term%'`` — the substring test a trigram index can serve.

    Django's own ``icontains`` compiles to ``UPPER(column) LIKE UPPER(…)``. That
    is the same question, and no index on ``column`` can answer it: the
    expression being compared is ``UPPER(column)``, which nothing indexes. So
    the substring tiers read every row no matter which indexes existed, and one
    such tier in an OR was enough to make the whole search a sequential scan
    (ENG-010).

    ``ILIKE`` over the plain column is what ``gin_trgm_ops`` answers. The term
    is escaped exactly as ``icontains`` escapes it — ``%``, ``_`` and the
    backslash are literal characters to a lawyer — so ``50%`` finds «50%» and
    not every title with a 5 and a 0 in it.

    Registered on the three projection columns that carry trigram indexes, and
    nowhere else, so the lookup cannot be reached for a column where it would
    silently scan (`app.search.models.SearchDocument`).
    """

    lookup_name = "contains_any_case"
    prepare_rhs = False

    def get_db_prep_lookup(self, value: Any, connection: Any) -> tuple[str, tuple[str]]:
        return "%s", ("%" + connection.ops.prep_for_like_query(value) + "%",)

    def as_sql(self, compiler: Any, connection: Any) -> tuple[str, tuple[Any, ...]]:
        lhs, lhs_params = self.process_lhs(compiler, connection)
        rhs, rhs_params = self.process_rhs(compiler, connection)
        return f"{lhs} ILIKE {rhs}", (*lhs_params, *rhs_params)


for _column in ("title", "identifiers", "alias_text", "people_text"):
    _field = SearchDocument._meta.get_field(_column)
    if not isinstance(_field, Field):  # pragma: no cover - a model change, caught at import
        raise TypeError(f"SearchDocument.{_column} is not a column")
    _field.register_lookup(ContainsAnyCase)


def tsquery_of(text: str, *, config: str) -> SearchQuery:
    """``to_tsquery(config, text)``, over text this module assembled itself.

    Never a user's string: :func:`_tsquery_text` builds its argument from the
    ``\\w+`` runs of the query, joined with ``&`` and suffixed with ``:*``,
    so the only operators it can contain are the ones written here.

    A literal, always. ``to_tsquery(regconfig, text)`` is immutable, so over a
    literal PostgreSQL computes it once, at planning; over anything else it
    computes it for every row a scan tests. That is why diacritics are folded
    before this is called (:func:`_folded_words`) rather than inside it.

    A `SearchQuery` rather than a bare function call, because that is what the
    ``@@`` lookup recognises; anything else it wraps in ``plainto_tsquery``.
    """
    return SearchQuery(Value(text), config=config, search_type="raw")


def _folded_words(words: tuple[str, ...]) -> tuple[str, ...]:
    """The query's words through PostgreSQL's own ``unaccent``, in one round trip.

    The index side folds with that function (`app.search.indexing._folded`),
    so the query side must too — Python's Unicode decomposition disagrees with
    it on letters such as «ß» and «æ». But ``unaccent`` is only *stable*, so
    written into the query it would run for every row a scan tests, twice:
    on a term too common for the indexes, that was a third of the scan's time
    at the LARGE corpus (ADR 0117). Folded here, once, the tsquery is a
    literal. What comes back is re-split into ``\\w+`` runs, so a character
    that folds into punctuation («½» becomes « 1/2») cannot reach
    ``to_tsquery`` as syntax.
    """
    if not words:
        return ()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT array_agg(unaccent(word) ORDER BY position) "
            "FROM unnest(%s::text[]) WITH ORDINALITY AS words(word, position)",
            [list(words)],
        )
        (folded,) = cursor.fetchone()
    return tuple(run for word in folded for run in _WORD.findall(word))


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
MATCH_PERSON = "person"
MATCH_CHILD_TITLE = "child_title"
MATCH_DOCUMENT_TITLE = "document_title"
MATCH_FULLTEXT = "fulltext"
MATCH_SIMPLE = "simple"
MATCH_FOLDED = "folded"
MATCH_PREFIX = "prefix"
MATCH_FUZZY = "fuzzy"

MATCH_LABELS: dict[str, str] = {
    MATCH_REFERENCE: "Viide",
    MATCH_TITLE: "Pealkiri",
    MATCH_PHRASE: "Pealkirja fraas",
    MATCH_TAXONOMY: "Asutus, valdkond või silt",
    # A hit on the person who wrote the row. It used to be matched through the
    # taxonomy column and labelled as an organisation, area or tag (ENG-083).
    MATCH_PERSON: "Autor",
    # The kinds this tier covers grew beyond entries and opinions (ENG-031):
    # a Märge, a Kaasamine and a received opinion are matched by their own
    # title too, so the label names the idea rather than two of the kinds.
    MATCH_CHILD_TITLE: "Kirje pealkiri",
    MATCH_DOCUMENT_TITLE: "Dokumendi nimi",
    MATCH_FULLTEXT: "Tekstiotsing",
    MATCH_SIMPLE: "Sõnaotsing",
    MATCH_FOLDED: "Täpitähtedeta",
    MATCH_PREFIX: "Sõna algus",
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
    # Added when engagements started actually appearing in results. AUTH-003
    # created the source kind and only a full rebuild ever wrote one, so no
    # result carried this kind on any surface anybody looked at — and the
    # template prints `source_label` into a badge, which rendered empty. Found
    # by the visual regression the moment SEARCH-001 made a recorded
    # `Kaasamine` findable.
    SearchSourceKind.ENGAGEMENT.value: "Kaasamine",
    # Added with the kinds themselves, and for the reason the note above gives:
    # the template prints `source_label` into a badge, and a kind missing from
    # this map renders one that is empty (QA-003, QA-020).
    SearchSourceKind.PROCEDURAL_DEVELOPMENT.value: "Märge",
    SearchSourceKind.EXTERNAL_POSITION.value: "Arvamus või tagasiside",
    SearchSourceKind.DOCUMENT.value: "Dokument",
}

#: Deterministic tiers. Higher wins, and the gaps are wide so that a strong
#: ``ts_rank`` inside one tier can never overtake the tier above it.
TIER_REFERENCE = 100
TIER_TITLE_EXACT = 90
TIER_TITLE_PHRASE = 80
TIER_ALIAS = 70
TIER_PERSON = 68
TIER_CHILD_TITLE = 66
TIER_DOCUMENT_TITLE = 62
TIER_FULLTEXT = 60
TIER_SIMPLE = 50
TIER_FOLDED = 48
TIER_PREFIX = 45
TIER_TRIGRAM = 40

TIER_MATCH_KIND: dict[int, str] = {
    TIER_REFERENCE: MATCH_REFERENCE,
    TIER_TITLE_EXACT: MATCH_TITLE,
    TIER_TITLE_PHRASE: MATCH_PHRASE,
    TIER_ALIAS: MATCH_TAXONOMY,
    TIER_PERSON: MATCH_PERSON,
    TIER_CHILD_TITLE: MATCH_CHILD_TITLE,
    TIER_DOCUMENT_TITLE: MATCH_DOCUMENT_TITLE,
    TIER_FULLTEXT: MATCH_FULLTEXT,
    TIER_SIMPLE: MATCH_SIMPLE,
    TIER_FOLDED: MATCH_FOLDED,
    TIER_PREFIX: MATCH_PREFIX,
    TIER_TRIGRAM: MATCH_FUZZY,
}

#: Results per page on `/otsing/`.
#:
#: This used to be `MAX_RESULTS`, and it was a ceiling rather than a page: the
#: fifty-first result could not be reached from global search at all (ENG-048).
#: It is now the size of one page of a numbered, server-side pagination that
#: reaches every authorized result.
RESULTS_PER_PAGE = 50

#: Below this, trigram matching is noise.
#:
#: Higher than pg_trgm's 0.3 default, deliberately. `word_similarity` scores far
#: more generously than `similarity` — it ignores the length of everything it
#: did *not* match — so keeping the old threshold would turn the last-resort
#: fuzzy tier into a source of near-random results. 0.6 still catches an
#: ordinary one-letter typo (the case above scores 0.824) while rejecting words
#: that merely share a stem.
TRIGRAM_THRESHOLD = 0.6

#: The kinds whose own title is matched as a substring.
#:
#: `Kaasamine`, `Märge` and a received opinion joined in Round 6 (ENG-031).
#: They were added to the projection after this list was written and never to
#: it, so a word inside a Märge title reached the row only if the Estonian
#: stemmer happened to reduce it to the typed form — «Tarbijakaitseseadus»
#: missed «…Tarbijakaitseseaduse…».
CHILD_KINDS = (
    SearchSourceKind.ENTRY,
    SearchSourceKind.SUBMISSION,
    SearchSourceKind.LEGACY_SOURCE_PAGE,
    SearchSourceKind.ENGAGEMENT,
    SearchSourceKind.PROCEDURAL_DEVELOPMENT,
    SearchSourceKind.EXTERNAL_POSITION,
)

#: The kinds that actually populate ``alias_text``. A document fragment does not
#: — `fragment_values` writes ``""`` — and neither does a Märge.
#:
#: A `Kaasamine` (its link's host) and a received opinion (who gave it) joined
#: in Round 6 (ENG-031); an organisation known to a Teema only through the
#: feedback it sent was otherwise reachable only through a stemmed body match.
ALIAS_KINDS = (
    SearchSourceKind.MATTER,
    SearchSourceKind.ENTRY,
    SearchSourceKind.SUBMISSION,
    SearchSourceKind.LEGACY_SOURCE_PAGE,
    SearchSourceKind.ENGAGEMENT,
    SearchSourceKind.EXTERNAL_POSITION,
)

#: The shortest word matched by its beginning. «seadu» reaching «seaduse» is
#: what the prefix tier is for; «ma» reaching every word that starts with «ma»
#: is noise, so shorter words must match whole.
PREFIX_MINIMUM = 4

#: Words the query means literally — a phrase in quotes, an excluded word, an
#: `or` — are websearch syntax, and the prefix and diacritic-free tiers do not
#: second-guess them.
_WEBSEARCH_SYNTAX = re.compile(r'"|(?:^|\s)-\w|(?:^|\s)or(?:\s|$)', re.IGNORECASE)
_WORD = re.compile(r"\w+")

#: The longest query this module will act on.
#:
#: Generous — no real legal phrase approaches it — and a *refusal* rather than a
#: truncation. Cutting a query silently changes what was asked and then answers
#: the shortened question confidently, which is the one failure mode a search
#: over a legal record must not have. Above this the caller gets an empty
#: result and the page says why.
MAX_QUERY_CHARACTERS = 500

#: Control characters, which reach here from a hand-built URL rather than from a
#: keyboard. NUL is the one that matters: psycopg refuses to send it and the
#: request becomes a 500 — an unhandled exception where "no results" was the
#: honest answer. The rest are stripped with it because none of them can
#: contribute a lexeme.
#:
#: Tab, newline and carriage return are deliberately absent: they are whitespace,
#: and `clean_query` collapses whitespace a line below.
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_query(query: str | None) -> str:
    """The term this module will act on, or ``""`` for one it refuses.

    Every public entry point goes through here, so there is no path by which a
    hand-built query string reaches PostgreSQL unexamined. It does three things
    and nothing else: strips characters that cannot be part of a word, collapses
    whitespace so ``2026   184`` and ``2026 184`` are the same lookup, and
    refuses anything longer than :data:`MAX_QUERY_CHARACTERS`.
    """
    term = _CONTROL_CHARACTERS.sub(" ", query or "")
    term = " ".join(term.split())
    if len(term) > MAX_QUERY_CHARACTERS:
        return ""
    return term


@dataclass(frozen=True)
class SnippetRun:
    text: str
    highlight: bool


#: Source kinds whose stored `source_locator` is a primary key, not a place.
#:
#: `source_locator` is documented as *where the result opens from, when the
#: source is not the Teema itself*, and two of its five producers honour that: a
#: document fragment writes «lk 14» and a historical page writes its OneNote
#: location. The other three wrote `kaasamine-<uuid>`, `sissekanne-<uuid>` and
#: `arvamus-<uuid>` — a prefixed database identifier, rendered under the result
#: title on `/otsing/`.
#:
#: It told a lawyer nothing. The prefix repeats the badge already beside it, the
#: identifier is not a reference used anywhere in the register or in any
#: document, it is not what the row links to (`_target_url` builds every anchor
#: from the typed `entry_id` / `submission_id` / `document_id` columns), and the
#: provenance it carries is already structural — `source_kind` plus
#: `source_object_id` say the same thing, in columns, to code that can use them.
#:
#: Filtered *here*, in the read model, rather than only at the producers. The
#: producers are fixed too, so nothing new is written; but a projection row
#: keeps whatever it was written with until something reindexes it, and
#: `INDEX_VERSION` is not the instrument for this. That version is a fail-closed
#: authorization gate: bumping it makes every existing row ineligible until a
#: rebuild has run, which is correct when a stored vector may hold text a reader
#: may not see, and much too heavy for a value that is in no vector and decides
#: no access. Suppressing on read fixes every row, old and new, on deploy
#: (docs/adr/0057).
OPAQUE_LOCATOR_KINDS: frozenset[str] = frozenset(
    {
        SearchSourceKind.ENGAGEMENT.value,
        SearchSourceKind.ENTRY.value,
        SearchSourceKind.EXTERNAL_POSITION.value,
        SearchSourceKind.PROCEDURAL_DEVELOPMENT.value,
        SearchSourceKind.SUBMISSION.value,
    }
)


def readable_locator(source_kind: str, stored: str) -> str:
    """The locator a reader may be shown, or "" when there is nothing to show.

    By source kind rather than by inspecting the string. A rule that hid
    anything UUID-shaped would be guessing at content, and would start hiding a
    legitimate locator the day one happened to contain a hyphenated hex run.
    """
    return "" if source_kind in OPAQUE_LOCATOR_KINDS else stored


@dataclass(frozen=True)
class SearchResult:
    matter: Matter
    match_kind: str
    rank: float
    source_kind: str = SearchSourceKind.MATTER.value
    source_locator: str = ""
    #: The row's own title when the source is not the Teema — the Märge, the
    #: Kaasamine, the opinion, who gave a received one — so several hits on
    #: one Teema say which of its records each is (ENG-083). Empty for a Teema
    #: and for a document, whose name `document_title` already carries.
    source_title: str = ""
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

    It joins nothing it does not decide with: the Matter, for its visibility and
    owner, and a child's own row, for that child's current override. Presentation
    joins are added by whoever presents (`_present`, `search_matters`), because a
    ranked search over the whole projection must not carry eleven tables' worth
    of columns through its sort (ENG-010).
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
    #
    # And only the active generation (`app.search.generations`): while a full
    # rebuild fills the next one, its rows sit beside these, complete for no
    # one yet (ENG-011, docs/adr/0118).
    documents = SearchDocument.objects.filter(
        index_version=INDEX_VERSION, generation=active_generation_expression()
    )
    # The predicate is evaluated against the joined live rows — the Matter, and
    # for a child row its own current override — never against anything the
    # projection stores. Restricting either takes effect on the next query with
    # no reindex.
    #
    # Filtered without `.distinct()`. Participation reaches the collaborators
    # through an uncorrelated subquery rather than a join, so no row can appear
    # twice, and the `DISTINCT` over every selected column — the READER's and
    # the administrator's four-second planning cost — has nothing to do
    # (`restricted_participation_subquery_q`, ENG-010).
    return documents.filter(
        projected_visibility_q(
            scope,
            kind_field="source_kind",
            kind_overrides=SOURCE_OVERRIDE_FIELDS,
            parent_prefix="matter__",
            participation_by_subquery=True,
        )
    )


#: What a result row needs to render, joined for the page's rows only.
PRESENTATION_RELATIONS = (
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


def _plain_words(term: str) -> tuple[str, ...]:
    """The query's words, when it is plain words, for the recall tiers.

    Empty for a query that uses websearch syntax (a quoted phrase, ``-word``,
    ``or``): that query says exactly what it means, and the exact tiers answer
    it as written.
    """
    if _WEBSEARCH_SYNTAX.search(term):
        return ()
    return tuple(_WORD.findall(term))


def _tsquery_text(words: tuple[str, ...], *, prefix: bool = False) -> str:
    """``word & word``, or ``word:* & word:*`` — the only tsquery text built here.

    Every word is a ``\\w+`` run, so it contains no quote, operator or
    parenthesis; the syntax is all ours. A word shorter than
    :data:`PREFIX_MINIMUM` is matched whole even in the prefix tier.
    """
    return " & ".join(
        f"{word}:*" if prefix and len(word) >= PREFIX_MINIMUM else word for word in words
    )


def _any_case(term: str, normalized: str, column: str) -> Q:
    """``column ILIKE %term%``, and its diacritic-free form when that differs."""
    found = Q(**{f"{column}__contains_any_case": term})
    if normalized != term:
        found |= Q(**{f"{column}__contains_any_case": normalized})
    return found


def _build(term: str) -> tuple[Q, Combinable, Combinable]:
    """Every tier as SQL: the eligibility predicate, the tier, the relevance.

    One predicate, and every arm of it is served by an index on this table's
    own columns (ENG-010). That is what the three rules below are for, and each
    one replaced an arm that forced a full scan:

    * **Substrings are `ILIKE`** (:class:`ContainsAnyCase`), which the trigram
      indexes answer, instead of ``UPPER(x) LIKE``, which nothing does.
    * **An exact title is found through the projection's own title.** The arm
      used to test ``matters_matter.title`` directly — a column of another
      table, which turned the whole OR into a filter applied *after* joining
      every row. A MATTER row's title begins with its Matter's title, so the
      trigram index narrows first, and the exact comparison against the Matter
      is an uncorrelated ``IN`` that PostgreSQL evaluates once.
    * **Fuzzy matching uses the operator** ``%>``, which the trigram index
      serves, and only then the threshold on ``word_similarity`` — the same
      number as before, now computed for the rows the index returned rather
      than for all of them.

    What each arm *means* is unchanged, and `tests/test_search_differential.py`
    holds that against the query as it stood before, row for row and in order.
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
    # The Matter's own title, exactly, or the row's whole projected title. The
    # substring test is implied by either and is what the trigram index serves;
    # the subquery is a single hashed lookup, not a join.
    exactly_titled = Matter.all_objects.filter(title__iexact=term).values("pk")
    title_exact = (
        is_matter
        & Q(title__contains_any_case=term)
        & (Q(matter__in=exactly_titled) | Q(title__iexact=term))
    )
    # Against the title-only vector. The combined vector also carries
    # identifiers and aliases, so a phrase query against it would report an
    # organisation-name hit as a title match.
    title_phrase = is_matter & Q(search_title=phrase)
    alias = Q(source_kind__in=ALIAS_KINDS) & _any_case(term, normalized, "alias_text")
    child_title = Q(source_kind__in=CHILD_KINDS) & (
        Q(title__contains_any_case=term)
        | (Q(identifiers__contains_any_case=term) & Q(identifiers__iexact=term))
    )
    # A document's own row as well as its pages: an upload nobody extracted
    # has only the former, and before it existed was unfindable by name
    # (ENG-030). Its title and filenames are the whole of the row.
    document_title = Q(
        source_kind__in=(SearchSourceKind.DOCUMENT_FRAGMENT, SearchSourceKind.DOCUMENT)
    ) & (Q(title__contains_any_case=term) | Q(identifiers__contains_any_case=term))
    person = _any_case(term, normalized, "people_text")
    fulltext = Q(search_estonian=estonian)
    simple_match = Q(search_simple=simple)
    # Two recall tiers below the exact ones (ENG-031), both answered by GIN:
    #
    # * **Diacritic-free.** The query's words, folded, against a vector of the
    #   row's words folded the same way — «tahtaja» reaches «tähtaja».
    # * **The start of a word.** Each word of four letters or more as a prefix,
    #   against the Estonian vector (its stem: «seadus» becomes «seadu», which
    #   begins «seaduse») and against the folded one (the word as typed, which
    #   begins its inflected forms). This is where a nominative query finds the
    #   genitive the stemmer does not reduce. It is not a stemmer: a stem that
    #   *alternates* — «tähtaeg»/«tähtaja», «riigihange»/«riigihanke» — is not a
    #   prefix of its other forms, and stays a documented miss
    #   (tests/estonian_recall_corpus.py).
    words = _plain_words(term)
    if words:
        # Never empty for non-empty words in practice; if every word folded
        # into punctuation, the words as typed are the next best thing.
        folded_words = _folded_words(words) or words
        folded_query = tsquery_of(_tsquery_text(folded_words), config="simple")
        stem_prefix = tsquery_of(_tsquery_text(words, prefix=True), config="estonian")
        word_prefix = tsquery_of(_tsquery_text(folded_words, prefix=True), config="simple")
        folded = Q(search_folded=folded_query)
        prefix = Q(search_estonian=stem_prefix) | Q(search_folded=word_prefix)
        # For *eligibility*, one `@@` per vector with the queries OR-ed inside
        # it, instead of one per tier. Same rows — `v @@ (a || b)` is
        # `v @@ a OR v @@ b` — but PostgreSQL detoasts a vector afresh for every
        # `@@` that reads it, and on a term too common for the indexes to help,
        # the scan paid that twice per vector per row: the broad queries on the
        # IMPORT corpus took twice as long as before these tiers (ADR 0117).
        # The tier below still tells the arms apart, on the matching rows only.
        estonian_any = Q(search_estonian=estonian | stem_prefix)
        folded_any = Q(search_folded=folded_query | word_prefix)
    else:
        folded = prefix = Q(pk__in=[])
        estonian_any = fulltext
        folded_any = Q(pk__in=[])
    # Fuzzy matching is a short-string feature: titles, references, names. It is
    # confined to Matter rows because "which page of this annex is nearly spelled
    # like your typo" is not a question anybody has (Stage-2B brief 40, 41).
    #
    # `%>` is `word_similarity(term, column) >= pg_trgm.word_similarity_threshold`,
    # and it is here because the index can answer it; the explicit comparison
    # beside it is the rule, so a server configured with a lower threshold
    # returns exactly what this one does. One configured *higher* would narrow
    # the tier, which `check_search_capabilities` refuses.
    fuzzy = (
        is_matter
        & (Q(title__trigram_word_similar=term) | Q(identifiers__trigram_word_similar=term))
        & Q(title_similarity__gte=TRIGRAM_THRESHOLD)
    )

    matched = (
        title_exact
        | title_phrase
        | alias
        | person
        | child_title
        | document_title
        | estonian_any
        | simple_match
        | folded_any
        | fuzzy
    )

    tier = Case(
        When(title_exact, then=Value(TIER_TITLE_EXACT)),
        When(title_phrase, then=Value(TIER_TITLE_PHRASE)),
        When(alias, then=Value(TIER_ALIAS)),
        When(person, then=Value(TIER_PERSON)),
        When(child_title, then=Value(TIER_CHILD_TITLE)),
        When(document_title, then=Value(TIER_DOCUMENT_TITLE)),
        When(fulltext, then=Value(TIER_FULLTEXT)),
        When(simple_match, then=Value(TIER_SIMPLE)),
        When(folded, then=Value(TIER_FOLDED)),
        When(prefix, then=Value(TIER_PREFIX)),
        default=Value(TIER_TRIGRAM),
        output_field=FloatField(),
    )
    # Within a tier, relevance decides. The weights set at index time (title A,
    # identifiers B, aliases C, body D) are what ts_rank reads.
    relevance_by_folding: list[When] = []
    if words:
        relevance_by_folding = [
            When(
                folded,
                then=SearchRank(F("search_folded"), folded_query),
            ),
            When(
                prefix,
                then=SearchRank(F("search_folded"), word_prefix),
            ),
        ]
    relevance = Case(
        When(fulltext, then=SearchRank(F("search_estonian"), estonian)),
        When(simple_match, then=SearchRank(F("search_simple"), simple)),
        *relevance_by_folding,
        default=F("title_similarity"),
        output_field=FloatField(),
    )
    return matched, tier, relevance


def search_documents(*, query: str, user: Any) -> QuerySet[SearchDocument]:
    """The ranked, authorized queryset. Countable and sliceable as it stands.

    Model instances without presentation joins: a caller that renders fetches
    those for the rows it renders (`_present`). Iterating this over a broad
    query is the whole matching set, so the page and the count use
    ``values_list`` and ``count`` on it rather than this.
    """
    term = clean_query(query)
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
        .order_by(*RESULT_ORDER)
    )


#: The ranking, deterministic to the last row.
RESULT_ORDER = (
    "-match_tier",
    "-relevance",
    "-matter__reference_year",
    "-matter__reference_number",
    "source_kind",
    "source_locator",
    # The last tie-break, and the only one guaranteed to break the tie.
    # Everything above it can repeat: an archive Matter has no reference at
    # all, and two fragments of two different files attached to the same
    # Matter are both "lk 3". Without a unique final key PostgreSQL is free to
    # return those rows in a different order on every request, which moves
    # results between pages — a row that vanishes on the next page, or appears
    # on two, for no reason a reader can see (ENG-048).
    "pk",
)


def result_count(*, query: str, user: Any) -> int:
    """How many results this user has. Never includes anything they cannot see.

    A ``COUNT(*)`` over the same filtered queryset the page is sliced from, and
    nothing else: no ranking, no sort, no presentation joins. It used to be a
    second copy of the whole ranked search wrapped in a count (ENG-010).
    """
    return search_documents(query=query, user=user).count()


def matching_matter_ids(*, query: str, user: Any) -> QuerySet[SearchDocument, Any]:
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


@dataclass(frozen=True)
class SearchPage:
    """One page of results, and the paginator that says where it sits."""

    results: list[SearchResult]
    page: Page

    @property
    def total(self) -> int:
        return self.page.paginator.count


def search_page(
    *,
    query: str,
    user: Any,
    page_number: Any = None,
    per_page: int = RESULTS_PER_PAGE,
) -> SearchPage:
    """A numbered page of the ranked results, every one of them reachable.

    Offset pagination over an order that ends in the primary key, so a page is
    the same rows on every request and consecutive pages neither repeat nor
    skip a row (ENG-048). Authorization is in the queryset being sliced, so a
    row the reader may not see is not a gap on their page: it was never one of
    their results.

    `Paginator.get_page` is the malformed-input policy, and it is the
    register's too: anything that is not a page number reads as the first page,
    and a number past the end reads as the last one.

    Three statements, each bounded by what it answers: a ``COUNT(*)``, the
    page's ids in rank order, and the page's rows with what they need to render
    (plus excerpts, for the rows that have a body).
    """
    term = clean_query(query)
    page = Paginator(_ranked(term, user), per_page).get_page(page_number)
    results = _present(list(page.object_list), term=term, user=user)
    page.object_list = results
    return SearchPage(results=results, page=page)


def search(*, query: str, user: Any, limit: int = RESULTS_PER_PAGE) -> list[SearchResult]:
    """Find things. Authorization first, deterministic tiers, then relevance.

    The first ``limit`` results. The page uses :func:`search_page`, which can
    reach the rest.
    """
    term = clean_query(query)
    return _present(list(_ranked(term, user)[:limit]), term=term, user=user)


def _ranked(term: str, user: Any) -> Any:
    """``(pk, tier, relevance)`` in rank order — the slim ranking statement.

    An empty term, including a refused one, is no results, and asks the
    database nothing.
    """
    if not term:
        return []
    # The two annotations are invisible to django-stubs, not to Django.
    return search_documents(query=term, user=user).values_list(  # type: ignore[misc]
        "pk", "match_tier", "relevance"
    )


def _present(ranked: list[tuple[Any, Any, Any]], *, term: str, user: Any) -> list[SearchResult]:
    """The page's rows, in rank order, with what they need to render.

    Fetched through :func:`visible_documents` rather than by primary key alone.
    The ids came from an authorized query a moment ago, so this is redundant;
    it is here because "the ids are already checked" is exactly the assumption
    that stops being true when somebody later reuses this function.
    """
    if not ranked:
        return []
    documents = {
        document.pk: document
        for document in visible_documents(user)
        .filter(pk__in=[pk for pk, _, _ in ranked])
        .select_related(*PRESENTATION_RELATIONS)
    }
    ordered = [
        (documents[pk], tier, relevance) for pk, tier, relevance in ranked if pk in documents
    ]
    snippets = _snippets_for([document for document, _, _ in ordered], term=term, user=user)
    return [
        SearchResult(
            matter=document.matter,
            match_kind=TIER_MATCH_KIND.get(int(tier or TIER_FULLTEXT), MATCH_FULLTEXT),
            rank=float(relevance or 0.0),
            source_kind=document.source_kind,
            source_locator=readable_locator(document.source_kind, document.source_locator),
            source_title=_source_title(document),
            document_title=document.document.title if document.document else "",
            document_id=document.document_id,
            document_version_id=document.document_version_id,
            entry_id=document.entry_id,
            submission_id=document.submission_id,
            source_page_id=document.matter_source_page_id,
            snippet=snippets.get(document.pk, ()),
        )
        for document, tier, relevance in ordered
    ]


#: Kinds whose row title is not already on the result under another name.
_OWN_TITLE_KINDS = frozenset(
    {
        SearchSourceKind.ENTRY.value,
        SearchSourceKind.SUBMISSION.value,
        SearchSourceKind.ENGAGEMENT.value,
        SearchSourceKind.PROCEDURAL_DEVELOPMENT.value,
        SearchSourceKind.EXTERNAL_POSITION.value,
        SearchSourceKind.LEGACY_SOURCE_PAGE.value,
    }
)


def _source_title(document: SearchDocument) -> str:
    """The row's own title, where it is not the Teema's or a document's name.

    Read from the projection row, which the reader was just authorized to see —
    the same row, and the same visibility, as the excerpt beside it.
    """
    return document.title if document.source_kind in _OWN_TITLE_KINDS else ""


class TsQueryOr(Func):
    """``(a || b)`` over two tsqueries: either one's words are worth marking."""

    template = "(%(expressions)s)"
    arg_joiner = " || "
    output_field = TextField()


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

    highlight: Any = SearchQuery(term, config="estonian", search_type="websearch")
    words = _plain_words(term)
    if words:
        # A word matched by its beginning is marked as well: the stem-prefix
        # tier's own query, so «seadus» marks «seaduse» in the excerpt.
        highlight = TsQueryOr(
            highlight, tsquery_of(_tsquery_text(words, prefix=True), config="estonian")
        )
    rows = (
        visible_documents(user)
        .filter(pk__in=with_body)
        .annotate(
            headline=Headline(
                Value("estonian"),
                F("body_text"),
                highlight,
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


def search_matters(*, query: str, user: Any, limit: int = RESULTS_PER_PAGE) -> list[SearchResult]:
    """Matter-level results only.

    Kept for callers that want the Stage-2A behaviour — a navigation shortcut,
    a picker — rather than the full mixed corpus. The Matter's owner, addressee
    and stage are joined because the header's suggestions print them.
    """
    documents = (
        search_documents(query=query, user=user)
        .filter(source_kind=SearchSourceKind.MATTER)
        .select_related(
            "matter", "matter__owner", "matter__stage", "matter__addressee_organisation"
        )[:limit]
    )
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
