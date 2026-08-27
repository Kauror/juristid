"""The rebuildable search projection.

``SearchDocument`` is derived data and nothing else. It can be deleted in full
and rebuilt from canonical records, and the product is expected to survive that
with no loss beyond the time the rebuild takes. Nothing in the domain reads from
it, nothing writes business state through it, and no decision depends on it
(master specification 11.3).

**It stores no visibility.** The specification's sketch of this table mentions an
effective-visibility field, and Stage 0 already learned why that is the one field
it must not have: a stored authorization value goes stale the moment a Matter is
restricted, and a projection is refreshed *after* the fact by definition. Every
search query therefore joins the live Matter and applies the same
``app.core.authorization`` predicate as the rest of the system. The index makes
results fast; it never makes them visible (docs/adr/0005, docs/adr/0013).
"""

from __future__ import annotations

from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models

from app.core.models import BaseModel

#: Bumped when the indexed text or the vector configuration changes, so a
#: partially rebuilt index is visible rather than merely stale.
#: The contract a projection row was built under.
#:
#: Bumped by AUTH-003, and the bump is load-bearing rather than bookkeeping.
#: Rows written before it can contain a RESTRICTED `Kaasamine`'s title and note
#: inside a MATTER row's tsvector, and there is no way to neutralise part of a
#: stored vector at query time — the tokens are simply in it. So the query
#: chokepoint refuses to read any row that does not carry the current version
#: (`app.search.services._scoped_documents`), which makes every pre-fix row
#: ineligible the moment the new code is deployed, before any rebuild.
#:
#: That is deliberately fail-closed. Until the one-time rebuild runs, search
#: returns too little; it never returns something confidential.
INDEX_VERSION = "AUTH003.1"


class SearchSourceKind(models.TextChoices):
    """What a document projects.

    Stage 2B fills in the kinds Stage 2A only named. A fragment of an extracted
    document is its own row rather than text folded into the Matter's: a result
    has to be able to say *which* file matched and *where* in it, and content
    hidden inside the Matter row can say neither (docs/adr/0013, 0014).
    """

    MATTER = "MATTER", "Teema"
    ENTRY = "ENTRY", "Sissekanne"
    SUBMISSION = "SUBMISSION", "Arvamus"
    DOCUMENT_FRAGMENT = "DOCUMENT_FRAGMENT", "Dokumendi sisu"
    # One row per Matter↔page relationship rather than per page. A page shared
    # by three Matters is three rows, because a SearchDocument belongs to one
    # Matter and authorization is evaluated through that Matter — making one row
    # answer for three would mean choosing which of them decides who may see it
    # (Stage-2D brief 37).
    LEGACY_SOURCE_PAGE = "LEGACY_SOURCE_PAGE", "Ajalooline OneNote'i leht"
    # `Kaasamine`, as its own row rather than text folded into the Matter's.
    #
    # It was folded in until AUTH-003, and that was a confidentiality defect
    # rather than an untidiness: a MatterEngagement carries its own
    # `visibility_override`, so it can be stricter than the Matter it hangs off
    # — and a MATTER row is authorized by the Matter alone. A RESTRICTED
    # consultation's title and note were therefore searchable by anybody who
    # could open a NORMAL parent.
    #
    # The rule this restores is the one every other child kind here already
    # follows: text whose visibility may be narrower than its Matter's gets a
    # row whose visibility can express that.
    ENGAGEMENT = "ENGAGEMENT", "Kaasamine"


#: Which live column carries each kind's own restriction, for the authorization
#: join. ``MATTER`` maps to nothing because a Matter has no override above
#: itself — its visibility *is* the parent visibility.
#:
#: This mapping is the whole reason child content can be indexed at all. Every
#: entry names a real foreign key on this table, so a query can reach the
#: child's *current* override instead of a copy of it taken at index time
#: (docs/adr/0014, Stage-2B brief 43).
SOURCE_OVERRIDE_FIELDS: dict[str, str | None] = {
    SearchSourceKind.MATTER.value: None,
    SearchSourceKind.ENTRY.value: "entry__visibility_override",
    SearchSourceKind.SUBMISSION.value: "submission__visibility_override",
    SearchSourceKind.DOCUMENT_FRAGMENT.value: "document__visibility_override",
    SearchSourceKind.ENGAGEMENT.value: "engagement__visibility_override",
    # A source page has no restriction of its own. It is historical material
    # attached to a Matter, and the Matter's visibility is the whole answer —
    # so this maps to None, exactly like MATTER above.
    SearchSourceKind.LEGACY_SOURCE_PAGE.value: None,
}


class SearchDocument(BaseModel):
    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.CASCADE,
        related_name="search_documents",
        verbose_name="teema",
    )
    source_kind = models.CharField(
        max_length=32,
        choices=SearchSourceKind.choices,
        default=SearchSourceKind.MATTER,
        verbose_name="allika liik",
    )
    source_object_id = models.UUIDField(
        null=True,
        blank=True,
        verbose_name="allika objekt",
        help_text="Sama mis teema, kui dokument projitseerib teemat ennast.",
    )

    # -- live joins for authorization and display --------------------------
    #
    # ``source_object_id`` identifies the source; these *reach* it. A UUID
    # column cannot be joined, and joining is the point: the child's current
    # restriction has to participate in the query rather than being copied into
    # this table, where it would go stale the moment somebody restricts a
    # document (docs/adr/0013's central argument, one level down).
    #
    # All nullable, all CASCADE: a projection row for a deleted source is not
    # stale data to be cleaned up later, it is a search result pointing at
    # nothing.
    entry = models.ForeignKey(
        "matters.Entry",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="search_documents",
        verbose_name="sissekanne",
    )
    submission = models.ForeignKey(
        "submissions.Submission",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="search_documents",
        verbose_name="arvamus",
    )
    document = models.ForeignKey(
        "documents.Document",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="search_documents",
        verbose_name="dokument",
    )
    document_version = models.ForeignKey(
        "documents.DocumentVersion",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="search_documents",
        verbose_name="tõendiversioon",
        help_text="Täpne versioon, mille sisu see rida esindab.",
    )
    fragment = models.ForeignKey(
        "documents.DocumentTextFragment",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="search_documents",
        verbose_name="tekstiosa",
    )
    engagement = models.ForeignKey(
        "matters.MatterEngagement",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="search_documents",
        verbose_name="kaasamine",
    )
    matter_source_page = models.ForeignKey(
        "legacy_import.MatterSourcePage",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="search_documents",
        verbose_name="ajalooline lähteleht",
    )

    title = models.TextField(blank=True, verbose_name="pealkiri")
    identifiers = models.TextField(
        blank=True,
        verbose_name="identifikaatorid",
        help_text="Normaliseeritud viited, mille järgi saab täpselt otsida.",
    )
    alias_text = models.TextField(
        blank=True,
        verbose_name="nimekujud",
        help_text="Asutuste, valdkondade ja siltide nimed ning nimekujud.",
    )
    body_text = models.TextField(
        blank=True,
        verbose_name="sisutekst",
        help_text="Sissekande, arvamuse või dokumendi tekstiosa sisu.",
    )
    source_locator = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="allika asukoht",
        help_text="Kust tulemus avaneb, kui allikas ei ole teema ise.",
    )

    search_estonian = SearchVectorField(null=True, editable=False)
    search_simple = SearchVectorField(null=True, editable=False)
    # Title and alternate titles only. The combined vector above also carries
    # identifiers and aliases, so a phrase query against it matches an
    # organisation name and calls it a title hit. Ranking tiers are only
    # meaningful if each one tests what it claims to test.
    search_title = SearchVectorField(null=True, editable=False)

    index_version = models.CharField(max_length=16, default=INDEX_VERSION, editable=False)
    indexed_at = models.DateTimeField(db_index=True, verbose_name="indekseeritud")

    class Meta:
        verbose_name = "otsingudokument"
        verbose_name_plural = "otsingudokumendid"
        ordering = ["matter", "source_kind"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_kind", "source_object_id"],
                condition=models.Q(source_object_id__isnull=False),
                name="search_one_document_per_source_object",
            ),
        ]
        indexes = [
            GinIndex(fields=["search_estonian"], name="search_estonian_gin"),
            GinIndex(fields=["search_simple"], name="search_simple_gin"),
            GinIndex(fields=["search_title"], name="search_title_gin"),
            # Trigram indexes are for short strings only. The body text column
            # is deliberately absent: trigram-indexing extracted document text
            # is how a PostgreSQL search installation becomes unmaintainable
            # (master specification 14.1).
            #
            # And they are now *partial*. Stage 2A had one row per Matter, so
            # indexing every row's title cost nothing. Stage 2B adds a row per
            # document fragment, where the design headroom is millions — and
            # fuzzy-matching a typo against a fragment's title is not a feature
            # anybody asked for. Restricting both indexes to MATTER rows keeps
            # them the size they were, and the fuzzy tier in the query is
            # restricted to the same rows so the planner can use them.
            GinIndex(
                fields=["title"],
                opclasses=["gin_trgm_ops"],
                name="search_title_trigram",
                condition=models.Q(source_kind="MATTER"),
            ),
            GinIndex(
                fields=["identifiers"],
                opclasses=["gin_trgm_ops"],
                name="search_identifiers_trigram",
                condition=models.Q(source_kind="MATTER"),
            ),
            models.Index(fields=["matter", "source_kind"], name="search_matter_kind"),
            # Refreshing one document's projection deletes its rows first, and
            # at fragment scale that lookup must not be a scan.
            models.Index(fields=["document_version"], name="search_by_version"),
        ]

    def __str__(self) -> str:
        return f"{self.source_kind}:{self.title[:60]}"


class SearchRebuildReason(models.TextChoices):
    """Why a full rebuild is owed.

    Every member is a *vocabulary* change: text that lives on a reference row
    and is copied into the projection of every record pointing at it. That is
    the whole population of high-fanout invalidations, and naming them
    individually rather than storing free text keeps two properties. An
    operator reading `check_search_freshness` learns what kind of edit made the
    corpus stale without the debt table holding any business content; and a new
    member cannot be added without somebody deciding which projection column it
    invalidates (docs/adr/0041).

    `POLICY_AREA_REMOVED` is the one member that is not an edit. A PolicyArea is
    the only reference row in this vocabulary that can be *deleted* while its
    name is in the corpus — every other one is held by a `PROTECT` foreign key
    the moment anything indexes it — so a disappearance is a distinct way for
    the same column to go stale, and it says so rather than borrowing
    `POLICY_AREA_RENAMED`. An operator who reads "renamed" goes looking for a
    rename.
    """

    ORGANISATION_RENAMED = "ORGANISATION_RENAMED", "Asutus nimetati ümber"
    ORGANISATION_ALIAS_CHANGED = "ORGANISATION_ALIAS_CHANGED", "Asutuse nimekuju muutus"
    TAG_RENAMED = "TAG_RENAMED", "Silt nimetati ümber"
    TAG_ALIAS_CHANGED = "TAG_ALIAS_CHANGED", "Sildi nimekuju muutus"
    POLICY_AREA_RENAMED = "POLICY_AREA_RENAMED", "Valdkond nimetati ümber"
    POLICY_AREA_REMOVED = "POLICY_AREA_REMOVED", "Valdkond kustutati"
    PERSON_RENAMED = "PERSON_RENAMED", "Inimese nimi muutus"


class SearchRebuildDebt(BaseModel):
    """One durable record that the projection owes a full rebuild.

    **The point of this table is that it is a row and not a callback.** A
    high-fanout mutation — renaming an Organisation invalidates the indexed text
    of every Matter, Submission and Entry that names it — must not fan out
    inside the request that caused it, and until now it did not fan out at all:
    the corpus went stale and stayed stale until a human noticed and ran
    `rebuild_search_index`. Deferring the work is right; deferring it into
    nothing is what made SEARCH-001 a defect.

    So the mutation writes this row **in its own transaction**. That is the
    whole guarantee, and it is why `transaction.on_commit` is not what this is:
    an on-commit callback lives in the process's memory, so a deploy, an OOM
    kill or a `docker compose down` between the commit and the callback loses
    the only record that anything was owed — and loses it silently, which is the
    failure mode this projection cannot afford. A row survives all three, and a
    business transaction that rolls back takes its debt with it for free.

    **Append-only in use, deliberately without deduplication.** Renaming three
    organisations writes three rows and they coalesce at the other end: the
    consumer claims every outstanding row, performs *one* rebuild, and clears
    exactly the rows it claimed. Collapsing them at write time would need an
    upsert whose lost-update window is precisely a mark arriving while a rebuild
    is running — the one moment a mark matters most. Rows are small, they live
    for one poll interval, and the arithmetic is the same either way
    (`app/search/freshness.py`).

    The columns record attempts rather than successes. A cleared debt is a
    deleted row, so this table is empty in the ordinary case, and anything in it
    is either work not yet done or work that has failed — which is what makes
    `SELECT count(*)` a usable health probe.

    **It is operational state, not canonical state**, and `recovery_fingerprint`
    knows that (`app.core.deployment.OPERATIONAL_MODELS`). A row here means "a
    vocabulary edit landed seconds ago and the worker has not caught up", which
    is a healthy condition and not something a restore has to reproduce
    row-for-row. It is still dumped and still restored — it is an ordinary table
    in an ordinary `pg_dump` — it is simply never *compared*, because a probe
    that reports canonical drift whenever a queue is non-empty is a probe an
    operator learns to ignore.
    """

    reason = models.CharField(
        max_length=40,
        choices=SearchRebuildReason.choices,
        db_index=True,
        verbose_name="põhjus",
    )
    attempts = models.PositiveIntegerField(
        default=0,
        verbose_name="katseid",
        help_text="Mitu korda on täisindeksi ehitamine seda rida katsetades ebaõnnestunud.",
    )
    last_attempt_at = models.DateTimeField(null=True, blank=True, verbose_name="viimane katse")
    #: Sanitised metadata about the last failure — never the exception's own
    #: message. `check_search_freshness` prints this column to a terminal and a
    #: container log, and a PostgreSQL error message is composed out of the row
    #: that failed: a not-null violation's DETAIL is `Failing row contains (…)`
    #: with the projected `title` and `body_text` in it. Everything written here
    #: goes through `app.search.freshness.describe_failure`, which is the only
    #: writer, and which reads schema identifiers and SQLSTATE and nothing else.
    last_error = models.TextField(blank=True, verbose_name="viimane viga")

    class Meta:
        verbose_name = "otsinguindeksi võlg"
        verbose_name_plural = "otsinguindeksi võlad"
        # Oldest first: the age of the first row is "how long has search been
        # stale", which is the number both the probe and the operator want.
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.reason}@{self.created_at:%Y-%m-%d %H:%M:%S}"
