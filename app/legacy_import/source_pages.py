"""The OneNote historical corpus, as product data rather than importer scaffolding.

These models are not a staging area that gets truncated after a successful run.
They are the historical record itself: a lawyer opening a 2022 Matter three years
from now should still be able to read the OneNote page the work actually
happened on, in the order it was written, with the files still sitting where the
narrative introduces them.

Four things and the reason each is separate.

``LegacySourcePage`` is one archived OneNote page, imported **once** per original
page id no matter how many Matters point at it. Duplicating it per Matter would
mean 138 pages existing several times over with no way to tell which copy was
the page.

``MatterSourcePage`` is the join, and it is deliberately many-to-many. The audit
settled that empirically: 402 Matters have more than one plausible page and 159
pages are plausible for more than one Matter. A one-to-one column would have to
throw one of those away silently.

``LegacySourceResource`` is the catalogue of what was attached to a page — one
row per file per page, carrying the block ordinal that keeps
*"Ettepaneku eestikeelne variant"* next to the PDF it introduces.

``LegacySourceResourceImport`` records that a resource became a Document *for a
particular Matter*. A shared page materialises its files into each Matter that
accepted it, and every copy points back at the same archive resource key and the
same source SHA-256, so nothing pretends they are different files
(Stage-2D brief 10, 12, 27, 28, 29).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from app.core.models import BaseModel


class SourceSystem(models.TextChoices):
    """Where an archived page was read from.

    ``ONENOTE_DESKTOP`` is the only value that may be imported. The earlier
    Microsoft Graph export is named so that a row can never be mistaken for one
    — its page-to-file associations were proven wrong, and a value that exists
    but is never written is cheaper than a comment nobody reads
    (Stage-2D brief 4).
    """

    ONENOTE_DESKTOP = "ONENOTE_DESKTOP", "OneNote (töölauarakendus)"
    ONENOTE_GRAPH_INVALID = "ONENOTE_GRAPH_INVALID", "OneNote (Graph, kehtetu eksport)"


class SourcePageRole(models.TextChoices):
    """What the audit judged a page to be.

    Only ``MATTER_LIKE`` may become a Matter on its own. The others are the
    reason that restriction exists: `ARHIIV → Alkohol, tubakas` is a drawer, not
    a legislative matter, and turning every page into a Matter would bury 731
    real ones under 18 filing cabinets (Stage-2D brief 7, 8).
    """

    MATTER_LIKE = "MATTER_LIKE", "Võib olla teema"
    CATEGORY_OR_CONTAINER = "CATEGORY_OR_CONTAINER", "Kategooria või koondleht"
    BACKGROUND = "BACKGROUND", "Taustainfo"
    INDEX_OR_LIST = "INDEX_OR_LIST", "Sisukord või loend"
    EMPTY_OR_PLACEHOLDER = "EMPTY_OR_PLACEHOLDER", "Tühi või kohatäide"
    UNCLEAR = "UNCLEAR", "Ebaselge"


class SourceRelationshipKind(models.TextChoices):
    PRIMARY = "PRIMARY", "Peamine allikas"
    RELATED = "RELATED", "Seotud allikas"
    BACKGROUND = "BACKGROUND", "Taustamaterjal"


class SourceMatchMethod(models.TextChoices):
    """How a page came to be attached to a Matter.

    The first three are deterministic and were applied automatically. The last
    three record a person's decision, and the difference matters: a future
    reader must be able to tell a hyperlink's GUID from somebody's judgement
    (Stage-2D brief 12, 13, 14).
    """

    EXCEL_EXACT_PAGE_ID = "EXCEL_EXACT_PAGE_ID", "Exceli link — täpne lehe ID"
    EXCEL_EXACT_LINK = "EXCEL_EXACT_LINK", "Exceli link — täpne URL"
    EXACT_REFERENCE_TOKEN = "EXACT_REFERENCE_TOKEN", "Täpne viitenumber"
    REVIEWED_MATCH = "REVIEWED_MATCH", "Ülevaatusel kinnitatud"
    ONENOTE_ONLY_MATTER = "ONENOTE_ONLY_MATTER", "OneNote'i-põhine teema"
    MANUAL = "MANUAL", "Käsitsi lisatud"


class SourceMatchClass(models.TextChoices):
    EXACT = "EXACT", "Täpne"
    REVIEWED = "REVIEWED", "Ülevaadatud"


class LegacySourcePage(BaseModel):
    """One archived OneNote page. Imported once, however many Matters use it."""

    source_system = models.CharField(
        max_length=32,
        choices=SourceSystem.choices,
        default=SourceSystem.ONENOTE_DESKTOP,
        verbose_name="lähtesüsteem",
    )

    # -- identity ----------------------------------------------------------
    #
    # `source_page_id` is OneNote's own page id and is what the Excel
    # hyperlinks resolve to; `page_key` is the archive's stable local key.
    # Both are kept: the first is the join to the register, the second is the
    # join to the files on disk.
    source_page_id = models.CharField(max_length=300, verbose_name="OneNote'i lehe ID")
    page_key = models.CharField(max_length=100, verbose_name="arhiivi lehe võti")

    # -- where it lived ----------------------------------------------------
    #
    # Kept verbatim rather than mapped onto PolicyArea. "Maksud ja toll" is
    # where a lawyer filed something in 2019; the modern taxonomy is a separate
    # reviewed decision, and overwriting one with the other loses the only
    # record of how the department actually organised itself
    # (Stage-2D brief 9).
    source_notebook = models.CharField(max_length=200, verbose_name="märkmik")
    source_section = models.CharField(max_length=300, verbose_name="sektsioon")
    source_section_group = models.CharField(
        max_length=400, blank=True, verbose_name="sektsioonirühm"
    )
    source_parent_page = models.CharField(max_length=400, blank=True, verbose_name="ülemleht")

    title = models.TextField(blank=True, verbose_name="pealkiri")
    page_level = models.PositiveSmallIntegerField(default=1, verbose_name="tase")
    page_order = models.PositiveIntegerField(default=0, verbose_name="järjekord sektsioonis")
    child_count = models.PositiveIntegerField(default=0, verbose_name="alamlehti")

    page_role = models.CharField(
        max_length=32,
        choices=SourcePageRole.choices,
        default=SourcePageRole.UNCLEAR,
        db_index=True,
        verbose_name="lehe roll",
    )
    role_reason = models.TextField(blank=True, verbose_name="rolli põhjendus")

    source_created_at = models.DateTimeField(null=True, blank=True, verbose_name="loodud allikas")
    source_modified_at = models.DateTimeField(
        null=True, blank=True, verbose_name="muudetud allikas"
    )

    # -- the capture -------------------------------------------------------
    capture_id = models.CharField(max_length=100, verbose_name="hõive ID")
    source_xml_storage_key = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="lähte-XML hoidla võti",
        help_text="Muutumatu lähtetõend. Ei renderdata kunagi otse kasutajale.",
    )
    source_xml_sha256 = models.CharField(
        max_length=64, blank=True, verbose_name="lähte-XML SHA-256"
    )

    derived_text = models.TextField(blank=True, verbose_name="tuletatud tekst")

    # The narrative and its files, in one ordered list. A JSON column rather
    # than a table of block rows because a block is only ever read as part of
    # its page — nothing queries across blocks — and because the ordering is
    # the payload, which a row per block makes easy to lose.
    blocks = models.JSONField(default=list, blank=True, verbose_name="plokid")
    links = models.JSONField(default=list, blank=True, verbose_name="lingid")

    reading_order_ambiguous = models.BooleanField(
        default=False,
        verbose_name="lugemisjärjekord ebaselge",
        help_text=(
            "OneNote on vaba paigutusega; siin ei langenud XML-i ja paigutuse järjekord kokku."
        ),
    )
    reading_order_strategy = models.CharField(
        max_length=64, blank=True, verbose_name="järjestamise strateegia"
    )

    onenote_hyperlink = models.TextField(
        blank=True,
        verbose_name="OneNote'i link",
        help_text="Genereeritud link, mis avab lehe töölauarakenduses.",
    )
    reference_tokens = models.CharField(
        max_length=300, blank=True, verbose_name="viitenumbrid lehel"
    )

    text_characters = models.PositiveIntegerField(default=0, verbose_name="märke")
    block_count = models.PositiveIntegerField(default=0, verbose_name="plokke")
    file_count = models.PositiveIntegerField(default=0, verbose_name="faile")
    file_bytes = models.BigIntegerField(default=0, verbose_name="failide maht")

    first_imported_at = models.DateTimeField(verbose_name="esimest korda imporditud")
    latest_imported_at = models.DateTimeField(verbose_name="viimati imporditud")
    import_batch = models.ForeignKey(
        "legacy_import.ImportBatch",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="source_pages",
        verbose_name="impordipartii",
    )

    class Meta:
        verbose_name = "ajalooline lähteleht"
        verbose_name_plural = "ajaloolised lähtelehed"
        ordering = ["source_section", "page_order", "title"]
        constraints = [
            # One row per original page. This is the constraint that makes
            # "imported once, linked many times" true rather than aspirational.
            models.UniqueConstraint(
                fields=["source_system", "source_page_id"],
                name="legacy_one_row_per_source_page",
            ),
            models.UniqueConstraint(
                fields=["source_system", "page_key"],
                name="legacy_one_row_per_archive_page_key",
            ),
        ]
        indexes = [
            models.Index(fields=["source_section", "page_order"], name="legacy_page_section_order"),
            models.Index(fields=["page_role"], name="legacy_page_role"),
        ]

    def __str__(self) -> str:
        return f"{self.source_section} · {self.title[:60]}"

    @property
    def display_location(self) -> str:
        parts = [self.source_section_group, self.source_section, self.source_parent_page]
        return " → ".join(part for part in parts if part)


class MatterSourcePage(BaseModel):
    """One Matter's claim on one source page.

    Neither side is unique on its own, and both facts came from the corpus
    rather than from a preference: a Matter can have several pages and a page
    can serve several Matters (Stage-2D brief 12).
    """

    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.CASCADE,
        related_name="source_pages",
        verbose_name="teema",
    )
    source_page = models.ForeignKey(
        LegacySourcePage,
        on_delete=models.PROTECT,
        related_name="matter_links",
        verbose_name="lähteleht",
    )

    relationship_kind = models.CharField(
        max_length=16,
        choices=SourceRelationshipKind.choices,
        default=SourceRelationshipKind.PRIMARY,
        verbose_name="seose liik",
    )
    match_method = models.CharField(
        max_length=32,
        choices=SourceMatchMethod.choices,
        verbose_name="sidumise viis",
    )
    match_class = models.CharField(
        max_length=16,
        choices=SourceMatchClass.choices,
        default=SourceMatchClass.EXACT,
        verbose_name="sidumise klass",
    )
    source_audit_reference = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="auditi viide",
        help_text="Millisest auditi reast see seos pärineb.",
    )

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reviewed_source_pages",
        verbose_name="ülevaataja",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name="üle vaadatud")

    class Meta:
        verbose_name = "teema lähteleht"
        verbose_name_plural = "teema lähtelehed"
        ordering = ["matter", "relationship_kind", "source_page"]
        constraints = [
            # The pair is unique; neither column is. That is the whole design.
            models.UniqueConstraint(
                fields=["matter", "source_page"],
                name="legacy_one_link_per_matter_and_page",
            ),
        ]
        indexes = [
            models.Index(fields=["source_page", "matter"], name="legacy_link_by_page"),
        ]

    def __str__(self) -> str:
        return f"{self.matter} ← {self.source_page}"


class ResourceKind(models.TextChoices):
    FILE_ATTACHMENT = "FILE_ATTACHMENT", "Manustatud fail"
    IMAGE = "IMAGE", "Pilt"
    OTHER = "OTHER", "Muu"


class LegacySourceResource(BaseModel):
    """One file as it sits on one archived page.

    The catalogue exists separately from the Documents made out of it because
    the file belongs to the *page* — it was attached there, in that position,
    between those two sentences — and that fact is true regardless of how many
    Matters later claim the page or whether any of them do.
    """

    source_page = models.ForeignKey(
        LegacySourcePage,
        on_delete=models.CASCADE,
        related_name="resources",
        verbose_name="lähteleht",
    )
    resource_key = models.CharField(max_length=100, verbose_name="ressursi võti")
    original_filename = models.CharField(max_length=500, verbose_name="algne failinimi")
    resource_kind = models.CharField(
        max_length=32,
        choices=ResourceKind.choices,
        default=ResourceKind.FILE_ATTACHMENT,
        verbose_name="liik",
    )

    # Where the file sits in the narrative. Not optional and not cosmetic: it
    # is what keeps a caption attached to the document it captions
    # (Stage-2D brief 22).
    source_block_ordinal = models.PositiveIntegerField(default=0, verbose_name="ploki järjekord")

    sha256 = models.CharField(max_length=64, db_index=True, verbose_name="SHA-256")
    size_bytes = models.BigIntegerField(default=0, verbose_name="suurus baitides")
    archive_relative_path = models.TextField(verbose_name="tee arhiivis")
    is_inline = models.BooleanField(default=False, verbose_name="lehesisene")

    class Meta:
        verbose_name = "ajalooline lähtefail"
        verbose_name_plural = "ajaloolised lähtefailid"
        ordering = ["source_page", "source_block_ordinal", "original_filename"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_page", "resource_key"],
                name="legacy_one_resource_per_page_and_key",
            ),
        ]
        indexes = [
            models.Index(fields=["source_page", "source_block_ordinal"], name="legacy_res_order"),
        ]

    def __str__(self) -> str:
        return self.original_filename


class ResourceImportState(models.TextChoices):
    IMPORTED = "IMPORTED", "Imporditud"
    SKIPPED = "SKIPPED", "Vahele jäetud"
    FAILED = "FAILED", "Ebaõnnestus"


class LegacySourceResourceImport(BaseModel):
    """This archive file became that Document, for this Matter.

    A structured row rather than a sentence in ``provenance_note``, for the
    reason the email-attachment link already exists: "which OneNote page did
    this PDF come from, and where on it" has to stay answerable after the
    importer that knew has been rewritten (Stage-2D brief 27).

    A page shared between Matters produces one of these per Matter. Each names
    the same ``resource_key`` and the same source SHA-256, so the duplication is
    visible as duplication rather than passing for different files.
    """

    matter_source_page = models.ForeignKey(
        MatterSourcePage,
        on_delete=models.CASCADE,
        related_name="resource_imports",
        verbose_name="teema lähteleht",
    )
    resource = models.ForeignKey(
        LegacySourceResource,
        on_delete=models.PROTECT,
        related_name="imports",
        verbose_name="lähtefail",
    )
    document = models.ForeignKey(
        "documents.Document",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="legacy_imports",
        verbose_name="dokument",
    )
    document_version = models.ForeignKey(
        "documents.DocumentVersion",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="legacy_imports",
        verbose_name="tõendiversioon",
    )
    import_batch = models.ForeignKey(
        "legacy_import.ImportBatch",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="resource_imports",
        verbose_name="impordipartii",
    )

    state = models.CharField(
        max_length=16,
        choices=ResourceImportState.choices,
        default=ResourceImportState.IMPORTED,
        db_index=True,
        verbose_name="olek",
    )
    error_code = models.CharField(max_length=64, blank=True, verbose_name="vea kood")
    error_detail = models.TextField(blank=True, verbose_name="vea kirjeldus")

    class Meta:
        verbose_name = "lähtefaili import"
        verbose_name_plural = "lähtefailide import"
        ordering = ["matter_source_page", "resource"]
        constraints = [
            # The idempotency rule for materialisation: re-running an apply
            # finds this row and does nothing rather than storing the bytes a
            # second time (Stage-2D brief 47).
            models.UniqueConstraint(
                fields=["matter_source_page", "resource"],
                name="legacy_one_import_per_link_and_resource",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.resource} → {self.state}"


class CandidateClass(models.TextChoices):
    STRONG = "STRONG", "Tugev"
    REVIEW_REQUIRED = "REVIEW_REQUIRED", "Vajab ülevaatust"
    CONFLICT = "CONFLICT", "Vastuolu"
    UNLINKED_PAGE = "UNLINKED_PAGE", "Sidumata leht"
    BROKEN_EXCEL_LINK = "BROKEN_EXCEL_LINK", "Katkine Exceli link"


class CandidateState(models.TextChoices):
    PENDING = "PENDING", "Ootel"
    LINKED = "LINKED", "Seotud teemaga"
    MATTER_CREATED = "MATTER_CREATED", "Loodi uus teema"
    BACKGROUND = "BACKGROUND", "Taustamaterjal"
    CONTAINER = "CONTAINER", "Kategooria või koondleht"
    REJECTED = "REJECTED", "Tagasi lükatud"


class HistoricalMatchCandidate(BaseModel):
    """A proposed link that a person has to decide about.

    Everything the audit could not settle deterministically lands here rather
    than being applied or discarded. The corpus supplies 76 strong candidates,
    456 needing review and 3 genuine conflicts; guessing on any of them would
    file one ministry's correspondence into another matter, where nobody looks
    for it again (Stage-2D brief 14, 39–42).
    """

    source_page = models.ForeignKey(
        LegacySourcePage,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="candidates",
        verbose_name="lähteleht",
    )
    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="source_page_candidates",
        verbose_name="pakutud teema",
    )

    # Kept as text as well as a foreign key: the reference is what the audit
    # recorded, and it stays readable even if the Matter row is not found.
    excel_reference = models.CharField(max_length=40, blank=True, verbose_name="Exceli viide")
    excel_title = models.TextField(blank=True, verbose_name="Exceli pealkiri")
    excel_onenote_url = models.TextField(blank=True, verbose_name="Exceli OneNote'i link")

    candidate_class = models.CharField(
        max_length=32,
        choices=CandidateClass.choices,
        db_index=True,
        verbose_name="kandidaadi klass",
    )
    score = models.FloatField(default=0.0, verbose_name="hinne")
    match_signals = models.TextField(blank=True, verbose_name="signaalid")
    conflicts = models.TextField(blank=True, verbose_name="vastuolud")
    explanation = models.TextField(blank=True, verbose_name="selgitus")

    state = models.CharField(
        max_length=16,
        choices=CandidateState.choices,
        default=CandidateState.PENDING,
        db_index=True,
        verbose_name="olek",
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="historical_decisions",
        verbose_name="otsustaja",
    )
    decided_at = models.DateTimeField(null=True, blank=True, verbose_name="otsustatud")
    decision_note = models.TextField(blank=True, verbose_name="otsuse märkus")
    resulting_matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_from_candidates",
        verbose_name="tulemuseks olev teema",
    )

    import_batch = models.ForeignKey(
        "legacy_import.ImportBatch",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="candidates",
        verbose_name="impordipartii",
    )

    class Meta:
        verbose_name = "ajaloo sidumiskandidaat"
        verbose_name_plural = "ajaloo sidumiskandidaadid"
        ordering = ["candidate_class", "-score", "excel_reference"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_page", "excel_reference", "candidate_class"],
                name="legacy_one_candidate_per_page_ref_class",
            ),
        ]
        indexes = [
            models.Index(fields=["state", "candidate_class"], name="legacy_candidate_queue"),
        ]

    def __str__(self) -> str:
        return f"{self.candidate_class} {self.excel_reference or '—'}"
