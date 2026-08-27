"""Is the search projection structurally sound, and does it need rebuilding?

    python manage.py check_search_integrity

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
* does any row claim a Matter its own source does not belong to;
* is a rebuild currently owed, since when, and has paying it off failed.

Every check is a ``COUNT`` or a small ``GROUP BY``. None of them reads document
text, none walks the corpus row by row, and the whole command is a handful of
statements — so it is safe to run on a schedule and safe to run while people are
working. The deep, row-by-row comparison it deliberately is not: reconciling
every fragment against its source is what a rebuild does, and a rebuild is
cheaper than a report saying a rebuild is needed.

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
from django.db.models import Count, F, Q

from app.documents.enums import DerivativeStatus
from app.documents.models import DocumentTextFragment
from app.legacy_import.source_pages import MatterSourcePage
from app.matters.models import Entry, Matter, MatterEngagement
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
    """
    return [
        ("Teemad", SearchSourceKind.MATTER.value, Matter.objects.count()),
        ("Sissekanded", SearchSourceKind.ENTRY.value, Entry.objects.count()),
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
        ("Kaasamised", SearchSourceKind.ENGAGEMENT.value, MatterEngagement.objects.count()),
    ]


#: How many Matter rows the text-drift check recomputes. A rename invalidates
#: the whole corpus at once, so a sample answers "is a rebuild owed" as reliably
#: as a full pass and keeps a read-only report cheap enough to run often.
DRIFT_SAMPLE = 250


def build_report(*, sample: int = DRIFT_SAMPLE) -> IntegrityReport:
    report = IntegrityReport()
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
    report.findings.extend(_stale_matter_text(sample=sample))
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


def _stale_matter_text(*, sample: int) -> list[Finding]:
    """Rows whose indexed text is no longer what the canonical side would produce.

    Every other check here is structural: is the row there, does it carry a
    vector, does it name the right Matter. All of them pass on a row whose text
    is simply out of date, which is the one defect this projection can hold that
    a reader experiences directly — they search for a name and do not find the
    file.

    It is reachable by design, not by accident. `app/search/signals.py` refreshes
    what a write invalidates and deliberately stops where the fanout becomes
    unbounded: renaming an Organisation, editing its aliases, renaming a Tag or a
    PolicyArea changes the indexed text of every Matter pointing at it, and
    reindexing thousands of rows inside somebody's form submission is a worse
    failure than staleness. The documented answer is `rebuild_search_index` — but
    until now nothing told an operator the rebuild was owed, so the gap was not
    merely deferred, it was silent.

    Recomputing is what `refresh_matters` would write, compared against what is
    stored. Bounded by `sample` because the recomputation is the expensive half:
    a drift this reports is nearly always corpus-wide, so a few hundred rows
    answer the question that matters — *is a rebuild owed* — without reading
    every Matter to say so.
    """
    from app.search.indexing import indexable_matters, indexed_text_for

    rows = (
        SearchDocument.objects.filter(source_kind=SearchSourceKind.MATTER)
        .select_related("matter")
        .order_by("pk")[:sample]
    )
    indexed = {row.matter_id: row for row in rows}
    if not indexed:
        return []

    drifted = 0
    # `indexable_matters` is the indexer's own queryset, so the organisations,
    # aliases, areas and tags `indexed_text_for` reads arrive with the rows.
    # Spelling the prefetches out again here would be a second copy of the same
    # knowledge, free to drift, in the tool whose job is to notice drift.
    for matter in indexable_matters().filter(pk__in=indexed):
        row = indexed[matter.pk]
        expected = indexed_text_for(matter)
        if any(getattr(row, field) != value for field, value in expected.items()):
            drifted += 1

    if not drifted:
        return []
    return [
        Finding(
            label="Vananenud tekst",
            detail=(
                f"{drifted} kontrollitud {len(indexed)} reast kannab teksti, mida allikas "
                "enam ei ütle (nt asutuse või sildi ümbernimetamine). Vajalik on täisindeks."
            ),
        )
    ]


def _crossed_matters() -> list[Finding]:
    """Rows whose Matter is not the Matter their own source belongs to.

    Authorization is evaluated through ``SearchDocument.matter``. A fragment row
    that names Matter A while its document belongs to Matter B is therefore a
    row shown to the readers of A carrying the content of B — the one shape of
    projection defect that is a disclosure rather than an inconvenience.

    No database constraint can express this: it is a comparison across a join,
    not a property of the row. So it is checked here, in three joins over
    indexed columns, rather than approximated by a large polymorphic ``CHECK``
    that still could not see the other table.
    """
    findings: list[Finding] = []
    checks = [
        ("Sissekanne", Q(source_kind=SearchSourceKind.ENTRY), "entry__matter_id"),
        ("Arvamus", Q(source_kind=SearchSourceKind.SUBMISSION), "submission__matter_id"),
        (
            "Dokument",
            Q(source_kind=SearchSourceKind.DOCUMENT_FRAGMENT),
            "document__matter_id",
        ),
        (
            "Ajalooline leht",
            Q(source_kind=SearchSourceKind.LEGACY_SOURCE_PAGE),
            "matter_source_page__matter_id",
        ),
        ("Kaasamis", Q(source_kind=SearchSourceKind.ENGAGEMENT), "engagement__matter_id"),
    ]
    for label, kind, path in checks:
        crossed = (
            SearchDocument.objects.filter(kind)
            .filter(**{f"{path}__isnull": False})
            .exclude(matter_id=F(path))
            .count()
        )
        if crossed:
            findings.append(
                Finding(
                    label=f"{label}i teemaviide",
                    detail=f"{crossed} rida osutab teisele teemale kui nende allikas",
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
                "How many indexed Matters to recompute when looking for text that has "
                f"gone stale (default {DRIFT_SAMPLE}; 0 skips the check)."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        report = build_report(sample=max(0, options["drift_sample"]))

        if not options["quiet"]:
            self.stdout.write(f"Otsinguindeksis on {report.total_rows} rida.")
            width = max(len(label) for label, _, _ in report.counts)
            for label, expected, actual in report.counts:
                mark = "ok" if expected == actual else "ERINEVUS"
                self.stdout.write(
                    f"  {label:<{width}}  allikaid {expected:>7}  indeksis {actual:>7}  {mark}"
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
            self.stderr.write(
                self.style.WARNING(
                    "Paranda käsuga `rebuild_search_index` (kogu korpus) või "
                    "`refresh_matter_search <viide>` (üksik teema). Kui võlg on "
                    "vana, kontrolli kõigepealt, kas `run_search_refresh_worker` "
                    "töötab."
                )
            )
            # A non-zero exit rather than a raised CommandError: this is a
            # report, and a traceback would say the command failed when what it
            # did was work correctly and find something.
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS("Otsinguindeks on terve."))
