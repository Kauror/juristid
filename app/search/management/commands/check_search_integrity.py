"""Is the search projection structurally sound, and does it need rebuilding?

    python manage.py check_search_integrity          # structure + a text sample per kind
    python manage.py check_search_integrity --full   # structure + every row's text

**Read-only, always.** It never writes a row and never repairs anything, because
the repair already exists and is a different decision: ``rebuild_search_index``
for the whole corpus, ``refresh_matter_search`` for named Matters. A diagnostic
that quietly fixes what it finds is a diagnostic nobody can use to ask whether
something is wrong.

It answers the questions an operator actually has, in aggregate rather than row
by row:

* is every canonical source projected, and is anything projected that no longer
  has a source;
* was any of it built by an older indexer, so the corpus is a mix of index
  versions and the ranking differs by row;
* does any row have a null vector, which is a row that exists and can never
  match;
* does any row claim a Matter its own source does not belong to — for every
  kind with a source of its own;
* does any row hold text its source would no longer produce — every kind,
  recomputed through the indexer's own builders, author names included;
* is a rebuild currently owed, since when, and has paying it off failed.

**What it proves depends on the mode, and it says which it ran.** The
structural checks are ``COUNT``s and small ``GROUP BY``s. The text comparison
recomputes a deterministic, corpus-wide sample of each kind by default — enough
to answer "is a rebuild owed" cheaply and on a schedule — and every row with
``--full``, which is the only mode that can say the projection is current. A
deployment's proof runs ``--full`` (ENG-080). Which kinds exist, how each one's
Matter is reached and how its text is rebuilt live in one registry
(`kind_contracts`) that a test holds complete against `SearchSourceKind`.

The last of those is new in SEARCH-001 and is deliberately only a *report*.
`app/search/freshness.py` records the obligation and
`run_search_refresh_worker` discharges it; this command reads the same rows and
consumes nothing. A diagnostic that quietly drained the queue it was asked to
describe would be the same mistake as one that quietly repaired what it found —
and worse here, because the repair would then happen wherever somebody happened
to run a check.

An obligation that is merely *pending* is not a finding. A rename recorded four
seconds ago is the system working, and reporting it as a fault would train an
operator to ignore this command. It becomes a finding when it has outlived
`SEARCH_REBUILD_DEBT_STALE_SECONDS` — nothing is converging it — or when a
rebuild has already failed against it.

Exit status is 1 when anything is wrong, so it composes with a cron job.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.db.models import CharField, Count, F, Q
from django.db.models.functions import MD5, Cast

from app.documents.enums import DerivativeStatus
from app.documents.models import Document, DocumentTextFragment
from app.legacy_import.source_pages import MatterSourcePage
from app.matters.models import (
    Entry,
    Matter,
    MatterEngagement,
    MatterExternalPosition,
    MatterProceduralDevelopment,
)
from app.search.freshness import FreshnessStatus
from app.search.freshness import status as freshness_status
from app.search.models import INDEX_VERSION, SearchDocument, SearchSourceKind
from app.submissions.models import Submission


@dataclass
class Finding:
    label: str
    detail: str


@dataclass
class IntegrityReport:
    #: Whether every row's text was recomputed, or a sample of each kind.
    full: bool = False
    #: Rows whose text was recomputed, per source kind.
    drift_checked: dict[str, int] = field(default_factory=dict)
    counts: list[tuple[str, int, int]] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    index_versions: dict[str, int] = field(default_factory=dict)
    total_rows: int = 0
    freshness: FreshnessStatus | None = None

    @property
    def ok(self) -> bool:
        return not self.findings


def _expected_populations() -> list[tuple[str, str, int]]:
    """(label, source kind, how many rows the canonical side says there are).

    The fragment count is restricted to ACTIVE derivatives because that is what
    ``indexable_fragments`` projects: the pages of a superseded parse are kept
    as evidence and deliberately not searchable.

    Removed records are excluded for the same reason, one level up: a row a
    lawyer took off the file projects nothing, so counting it here would report
    a permanent shortfall on every Matter anybody has ever corrected — the
    check crying wolf about content that is absent on purpose
    (docs/adr/0102).
    """
    live = {"removed_at__isnull": True}
    return [
        ("Teemad", SearchSourceKind.MATTER.value, Matter.objects.count()),
        ("Sissekanded", SearchSourceKind.ENTRY.value, Entry.objects.filter(**live).count()),
        ("Arvamused", SearchSourceKind.SUBMISSION.value, Submission.objects.count()),
        (
            "Dokumendi tekstiosad",
            SearchSourceKind.DOCUMENT_FRAGMENT.value,
            DocumentTextFragment.objects.filter(derivative__status=DerivativeStatus.ACTIVE).count(),
        ),
        (
            "Ajaloolised lehed",
            SearchSourceKind.LEGACY_SOURCE_PAGE.value,
            MatterSourcePage.objects.count(),
        ),
        # AUTH-003 made `Kaasamine` a source kind and this list was not
        # extended, so for the whole of that release an engagement could be
        # recorded, never projected, and never reported missing — the check
        # said the index was healthy while content sat outside the corpus.
        # A kind that is projected and not counted here is a kind nothing
        # watches.
        (
            "Kaasamised",
            SearchSourceKind.ENGAGEMENT.value,
            MatterEngagement.objects.filter(**live).count(),
        ),
        # And the two the composer simplification left outside the corpus for
        # longer than AUTH-003 left `Kaasamine` there: `+ Märge` is the
        # product's main capture action and nothing it wrote was ever indexed,
        # and a recorded opinion was not either (QA-003).
        (
            "Märked",
            SearchSourceKind.PROCEDURAL_DEVELOPMENT.value,
            MatterProceduralDevelopment.objects.filter(**live).count(),
        ),
        (
            "Arvamused ja tagasiside",
            SearchSourceKind.EXTERNAL_POSITION.value,
            MatterExternalPosition.objects.filter(**live).count(),
        ),
        # One row per Document whatever its extraction state, which is what
        # `indexable_documents` projects (ENG-030).
        (
            "Dokumendid",
            SearchSourceKind.DOCUMENT.value,
            Document.objects.filter(matter__deleted_at__isnull=True).count(),
        ),
    ]


#: How many rows *of each source kind* the default text-drift check recomputes.
#: `--full` recomputes every row instead, and that is the mode a deployment's
#: proof uses: a sample says "a rebuild is probably owed"; only a full pass can
#: say "every row is current" (ENG-080).
DRIFT_SAMPLE = 250


@dataclass(frozen=True)
class KindContract:
    """What the integrity check knows about one source kind, in one place.

    Three questions per kind, and every kind must answer all three — a guard
    test fails if a `SearchSourceKind` exists without an entry here, which is
    how the next kind cannot be added with the check aware of only the old ones
    (ENG-080; the populations list already had such a guard, the crossing and
    drift lists did not, and each missed kinds).

    * ``source_matter`` — the path from a row to the Matter its *source*
      belongs to, compared with the row's own `matter`. ``None`` only for
      `MATTER`, whose source is the Matter.
    * ``rebuild`` — the indexer's own builder: given source ids, what each row
      should hold. Recomputing through the same functions the refresh uses is
      what makes this a check of the indexer rather than a second opinion.
    * ``repair`` — the command that repairs this kind's drift. Only `MATTER`
      rows can be refreshed one Matter at a time; every child kind is repaired
      by a full rebuild, and saying otherwise sent operators to a command that
      rewrote the wrong rows.
    """

    label: str
    source_matter: str | None
    rebuild: Any
    repair: str


#: The four columns a row's searchable text lives in.
TEXT_COLUMNS = ("title", "identifiers", "alias_text", "body_text")

REPAIR_ALL = "`rebuild_search_index`"
REPAIR_MATTERS = "`refresh_matter_search <viide>` (üksik teema) või `rebuild_search_index`"


def _matter_texts(ids: list[Any], now: Any) -> dict[Any, dict[str, Any]]:
    from app.search.indexing import indexable_matters, indexed_text_for

    return {
        matter.pk: indexed_text_for(matter) for matter in indexable_matters().filter(pk__in=ids)
    }


def _child_texts(indexable: Any, values: Any, *, removable: bool = False) -> Any:
    def build(ids: list[Any], now: Any) -> dict[Any, dict[str, Any] | None]:
        expected: dict[Any, dict[str, Any] | None] = {}
        for source in indexable().filter(pk__in=ids):
            # A removed record projects nothing (docs/adr/0102): a row for it
            # is stale, exactly like a row whose text has changed.
            if removable and source.is_removed:
                expected[source.pk] = None
                continue
            row = values(source, now)
            expected[source.pk] = {column: row[column] for column in TEXT_COLUMNS}
        return expected

    return build


def kind_contracts() -> dict[str, KindContract]:
    from app.search import child_indexing as child

    return {
        SearchSourceKind.MATTER.value: KindContract("Teemad", None, _matter_texts, REPAIR_MATTERS),
        SearchSourceKind.ENTRY.value: KindContract(
            "Sissekanded",
            "entry__matter_id",
            _child_texts(child.indexable_entries, child._entry_values, removable=True),
            REPAIR_ALL,
        ),
        SearchSourceKind.SUBMISSION.value: KindContract(
            "Arvamused",
            "submission__matter_id",
            _child_texts(child.indexable_submissions, child._submission_values),
            REPAIR_ALL,
        ),
        SearchSourceKind.DOCUMENT_FRAGMENT.value: KindContract(
            "Dokumendi tekstiosad",
            "document__matter_id",
            _child_texts(child.indexable_fragments, child.fragment_values),
            REPAIR_ALL,
        ),
        SearchSourceKind.LEGACY_SOURCE_PAGE.value: KindContract(
            "Ajaloolised lehed",
            "matter_source_page__matter_id",
            _child_texts(child.indexable_source_links, child.source_link_values),
            REPAIR_ALL,
        ),
        SearchSourceKind.ENGAGEMENT.value: KindContract(
            "Kaasamised",
            "engagement__matter_id",
            _child_texts(child.indexable_engagements, child._engagement_values, removable=True),
            REPAIR_ALL,
        ),
        SearchSourceKind.PROCEDURAL_DEVELOPMENT.value: KindContract(
            "Märked",
            "development__matter_id",
            _child_texts(child.indexable_developments, child._development_values, removable=True),
            REPAIR_ALL,
        ),
        SearchSourceKind.EXTERNAL_POSITION.value: KindContract(
            "Arvamused ja tagasiside",
            "external_position__matter_id",
            _child_texts(child.indexable_positions, child._position_values, removable=True),
            REPAIR_ALL,
        ),
        SearchSourceKind.DOCUMENT.value: KindContract(
            "Dokumendid",
            "document__matter_id",
            _child_texts(child.indexable_documents, child.document_values),
            REPAIR_ALL,
        ),
    }


def build_report(*, sample: int = DRIFT_SAMPLE, full: bool = False) -> IntegrityReport:
    """``sample`` rows per kind are recomputed, or every row with ``full``."""
    report = IntegrityReport()
    report.full = full
    report.total_rows = SearchDocument.objects.count()

    projected = dict(
        SearchDocument.objects.values_list("source_kind")
        .annotate(total=Count("id"))
        .values_list("source_kind", "total")
    )

    for label, kind, expected in _expected_populations():
        actual = projected.get(kind, 0)
        report.counts.append((label, expected, actual))
        if actual == expected:
            continue
        # Both directions are worth reporting and they mean opposite things. A
        # shortfall is content that exists and cannot be found — the silent
        # failure. A surplus is a result that points at something that is gone.
        missing = expected - actual
        report.findings.append(
            Finding(
                label=label,
                detail=(
                    f"{missing} allikat ei ole indekseeritud"
                    if missing > 0
                    else f"{-missing} indeksirida on üle"
                ),
            )
        )

    report.index_versions = dict(
        SearchDocument.objects.values_list("index_version")
        .annotate(total=Count("id"))
        .values_list("index_version", "total")
    )
    stale = sum(
        total for version, total in report.index_versions.items() if version != INDEX_VERSION
    )
    if stale:
        # Mixed versions are not merely old text. The vector configuration and
        # the weights are part of the version, so rows built by two indexers
        # rank against each other on different scales, and which of them a
        # search prefers is an accident of when each was last written.
        report.findings.append(
            Finding(
                label="Indeksi versioon",
                detail=(f"{stale} rida on ehitatud vanema indekseerijaga (ootus {INDEX_VERSION})"),
            )
        )

    unvectored = SearchDocument.objects.filter(
        Q(search_estonian__isnull=True) | Q(search_simple__isnull=True)
    ).count()
    if unvectored:
        # A row with no vector is a row that exists, counts as indexed and can
        # never match a full-text query. It is the one defect this projection
        # can hold that looks like success from every other angle.
        report.findings.append(
            Finding(label="Otsinguvektorid", detail=f"{unvectored} real puudub otsinguvektor")
        )

    unidentified = SearchDocument.objects.filter(source_object_id__isnull=True).count()
    if unidentified:
        # The uniqueness constraint is conditional on this column, so a row
        # without it is a row that can be duplicated without anything noticing.
        report.findings.append(
            Finding(
                label="Allika tunnus",
                detail=f"{unidentified} real puudub allika tunnus (source_object_id)",
            )
        )

    unknown_kinds = (
        SearchDocument.objects.exclude(source_kind__in=[k.value for k in SearchSourceKind])
        .values_list("source_kind", flat=True)
        .distinct()
    )
    for kind in unknown_kinds:
        # Authorization whitelists source kinds, so a row of an unrecognised
        # kind is invisible to every reader rather than dangerous. It is still
        # a row nothing will ever maintain.
        report.findings.append(
            Finding(label="Tundmatu allika liik", detail=f"{kind!r} ei ole teadaolev liik")
        )

    report.findings.extend(_crossed_matters())
    report.findings.extend(_stale_text(report, sample=sample, full=full))
    report.freshness = freshness_status()
    report.findings.extend(_unpaid_debt(report.freshness))
    return report


def _unpaid_debt(state: FreshnessStatus) -> list[Finding]:
    """Is anything owed that nothing is converging?

    Reads the obligations `app/search/freshness.py` records and never touches
    them. Pending work is reported in the body of the command as a fact, not
    here as a fault: this returns a finding only when the obligation has stopped
    looking like work in progress.
    """
    if state.is_clear:
        return []
    if state.failed_attempts:
        return [
            Finding(
                label="Indeksi taastamine",
                detail=(
                    f"täisindeksi ehitamine on ebaõnnestunud {state.failed_attempts} korda; "
                    f"viimane viga: {state.last_error or 'teadmata'}"
                ),
            )
        ]
    age = int(state.seconds_owed())
    limit = settings.SEARCH_REBUILD_DEBT_STALE_SECONDS
    if age < limit:
        return []
    return [
        Finding(
            label="Indeksi võlg",
            detail=(
                f"{state.owed} muudatust ootab täisindeksit juba {age}s (lubatud {limit}s). "
                "Kas `run_search_refresh_worker` töötab?"
            ),
        )
    ]


def _drift_rows(kind: str, *, sample: int, full: bool) -> Any:
    """The rows of one kind whose text is recomputed.

    The default is a sample *across* the kind, not its oldest rows. It used to
    be `order_by("pk")[:250]` over MATTER rows only, and primary keys are
    UUIDv7 while a refresh deletes and re-inserts — so the sample was always
    the rows written longest ago, the ones least likely to have drifted
    (ENG-080). Ordering by a hash of the source id spreads it over the whole
    kind, and the same corpus gives the same sample, so two runs agree.
    """
    rows = SearchDocument.objects.filter(source_kind=kind).only(
        "pk", "source_object_id", *TEXT_COLUMNS
    )
    if full:
        return rows.order_by("pk").iterator(chunk_size=500)
    return rows.annotate(spread=MD5(Cast("source_object_id", output_field=CharField()))).order_by(
        "spread"
    )[:sample]


def _stale_text(report: IntegrityReport, *, sample: int, full: bool) -> list[Finding]:
    """Rows whose indexed text is no longer what the canonical side would produce.

    Every kind, through the indexer's own builders (`kind_contracts`). The
    structural checks above all pass on a row whose text is simply out of date
    — a Märge edited by `QuerySet.update()`, a person's display name changed
    without a save, a data migration — and that is the defect a reader meets
    directly: they search for a word and do not find the file.

    Author names are covered because they are text the builders project (an
    Entry's `alias_text` carries its author). Bounded by ``sample`` rows per
    kind unless ``full``: the default answers "is a rebuild owed"; `--full`
    answers "is every row current", which is what a deployment has to prove.
    """
    if not full and sample <= 0:
        return []

    now = None
    findings: list[Finding] = []
    for kind, contract in kind_contracts().items():
        drifted = 0
        checked = 0
        batch: list[SearchDocument] = []

        def settle(rows: list[SearchDocument], contract: KindContract = contract) -> int:
            expected = contract.rebuild([row.source_object_id for row in rows], now)
            stale = 0
            for row in rows:
                wanted = expected.get(row.source_object_id)
                if wanted is None or any(
                    getattr(row, column) != wanted[column] for column in TEXT_COLUMNS
                ):
                    stale += 1
            return stale

        for row in _drift_rows(kind, sample=sample, full=full):
            batch.append(row)
            if len(batch) >= 500:
                drifted += settle(batch)
                checked += len(batch)
                batch = []
        if batch:
            drifted += settle(batch)
            checked += len(batch)
        report.drift_checked[kind] = checked

        if drifted:
            findings.append(
                Finding(
                    label=f"{contract.label}: vananenud tekst",
                    detail=(
                        f"{drifted} kontrollitud {checked} reast kannab teksti, mida allikas "
                        f"enam ei ütle. Parandus: {contract.repair}."
                    ),
                )
            )
    return findings


def _crossed_matters() -> list[Finding]:
    """Rows whose Matter is not the Matter their own source belongs to.

    Authorization is evaluated through ``SearchDocument.matter``. A fragment row
    that names Matter A while its document belongs to Matter B is therefore a
    row shown to the readers of A carrying the content of B — the one shape of
    projection defect that is a disclosure rather than an inconvenience.

    Every kind with a source of its own is compared, from `kind_contracts`. The
    hand-written list this replaced compared five of eight kinds, and never a
    `Märge` or a recorded opinion (ENG-080).
    """
    findings: list[Finding] = []
    for kind, contract in kind_contracts().items():
        if contract.source_matter is None:
            continue
        path = contract.source_matter
        crossed = (
            SearchDocument.objects.filter(source_kind=kind)
            .filter(**{f"{path}__isnull": False})
            .exclude(matter_id=F(path))
            .count()
        )
        if crossed:
            findings.append(
                Finding(
                    label=f"{contract.label}: teemaviide",
                    detail=(
                        f"{crossed} rida osutab teisele teemale kui nende allikas. "
                        f"Parandus: {contract.repair}."
                    ),
                )
            )
    return findings


class Command(BaseCommand):
    help = "Report whether the search projection is complete, current and consistent. Read-only."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Print only problems. The exit status is unchanged.",
        )
        parser.add_argument(
            "--drift-sample",
            type=int,
            default=DRIFT_SAMPLE,
            help=(
                "How many rows of each source kind to recompute when looking for text "
                f"that has gone stale (default {DRIFT_SAMPLE}; 0 skips the check)."
            ),
        )
        parser.add_argument(
            "--full",
            action="store_true",
            help=(
                "Recompute every row of every kind instead of a sample. This is the mode "
                "that proves the projection current, and the one a deployment runs."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        report = build_report(sample=max(0, options["drift_sample"]), full=options["full"])

        if not options["quiet"]:
            self.stdout.write(f"Otsinguindeksis on {report.total_rows} rida.")
            width = max(len(label) for label, _, _ in report.counts)
            for label, expected, actual in report.counts:
                mark = "ok" if expected == actual else "ERINEVUS"
                self.stdout.write(
                    f"  {label:<{width}}  allikaid {expected:>7}  indeksis {actual:>7}  {mark}"
                )
            checked = sum(report.drift_checked.values())
            if report.full:
                self.stdout.write(f"  teksti võrdlus: kõik {checked} rida (--full)")
            else:
                self.stdout.write(
                    f"  teksti võrdlus: valim, {checked} rida (kuni "
                    f"{options['drift_sample']} liigi kohta; --full kontrollib kõiki)"
                )
            for version, total in sorted(report.index_versions.items()):
                marker = "" if version == INDEX_VERSION else "  (vananenud)"
                self.stdout.write(f"  indeksi versioon {version}: {total}{marker}")
            state = report.freshness
            if state is None or state.is_clear:
                self.stdout.write("  täisindeksi võlg: puudub")
            else:
                reasons = ", ".join(
                    f"{reason} x {count}" for reason, count in sorted(state.reasons.items())
                )
                self.stdout.write(
                    f"  täisindeksi võlg: {state.owed} ({reasons}), "
                    f"vanim {int(state.seconds_owed())}s"
                )

        for finding in report.findings:
            self.stderr.write(self.style.WARNING(f"{finding.label}: {finding.detail}"))

        if not report.ok:
            # Each finding above names its own repair. This is the one that
            # repairs every class, said once: `refresh_matter_search` rewrites
            # MATTER rows only and cannot fix a child row (ENG-080).
            self.stderr.write(
                self.style.WARNING(
                    "Iga leiu parandus on selle real. `rebuild_search_index` parandab "
                    "kõik ülaltoodu ja tasub ka täisindeksi võla. Kui võlg on vana, "
                    "kontrolli kõigepealt, kas `run_search_refresh_worker` töötab."
                )
            )
            # A non-zero exit rather than a raised CommandError: this is a
            # report, and a traceback would say the command failed when what it
            # did was work correctly and find something.
            raise SystemExit(1)

        if report.full:
            self.stdout.write(
                self.style.SUCCESS("Otsinguindeks on terve (kõik read kontrollitud).")
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "Otsinguindeks on terve (tekst kontrollitud valimiga; --full kontrollib kõiki)."
                )
            )
