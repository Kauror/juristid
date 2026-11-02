"""The Teemad register's columns — one catalogue for the table, its headings and its Excel.

Contract B of the 2026-11-02 package. «Vali veerud» lets a reader add columns
to Teemad (and hide the optional default ones); «Laadi Excel tabelis kuvatud
infoga» writes exactly those columns. Both read this catalogue, so a column
means the same thing on screen and in the file, and the workbook is never
scraped from rendered HTML.

**What a column is.** A stable key (the `?veerud=` word), an Estonian heading,
whether it is in the compact default set, an optional sort (only where the
register already sorts correctly — :mod:`app.matters.register_selection`), and
one or more typed spreadsheet cells. A date is a real date only when it is a
day; a month, a quarter or a year is its words (`format_at_precision`), so an
approximate date never becomes its first day. «Unknown» is an empty cell, not a
guess.

**Columns never change which rows are shown.** Choosing «Valdkond» does not
drop the Teemad that have none — the cell is empty. Filtering by a dimension
does not add or remove its column. The row population is the selection's
(Contract A); this module only describes rows already chosen.

**Teemad only.** The table partial is shared with Saabunud
(`templates/matters/inbox.html`), which passes no ``columns`` and renders its
six columns exactly as before. Teemad passes the chosen keys explicitly.

**Bounded queries.** :func:`load_facts` reads what the chosen extra columns need
for a whole page — or a whole export — in a fixed number of statements, each
scoped to the reader (`visible_to`) before it contributes a value, so a
restricted child record names nothing through a cell. Nothing is read per cell.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from django.db.models import Count, Max
from django.utils import timezone

from app.core.dates import format_estonian_date
from app.matters.enums import RecordMode
from app.matters.models import Matter
from app.workflow.dates import format_at_precision, is_approximate
from app.workflow.enums import Track

#: The `?veerud=` parameter: chosen keys, comma-separated or repeated.
COLUMNS_PARAM = "veerud"

#: The cell the title column always is. Not in the chooser: a row nobody can
#: identify is not a row.
TITLE = "pealkiri"


@dataclass(frozen=True)
class Cell:
    """One spreadsheet cell a column contributes: its heading, width and value."""

    heading: str
    value: Callable[[Matter, Facts], Any]
    width: int = 18
    wrap: bool = False


@dataclass(frozen=True)
class Column:
    key: str
    heading: str
    #: In the compact set a bare `/teemad/` shows.
    default: bool
    #: Drawn by `matter_row.html`'s own cell (the six that existed before);
    #: every other column is a plain text cell from :meth:`text`.
    base: bool
    cells: tuple[Cell, ...]
    #: The `?jarjestus=` value a heading click sets, if the register sorts it.
    sort: str | None = None
    #: The sorts that read this column, which a hidden column resets.
    sorts_needing: tuple[str, ...] = ()
    #: Shown under the heading in the chooser.
    help_text: str = ""

    def text(self, matter: Matter, facts: Facts) -> str:
        """The table cell for an extra column: every cell value, as words."""
        parts = [_as_text(cell.value(matter, facts)) for cell in self.cells]
        return " · ".join(part for part in parts if part)


def _as_text(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, date):
        return format_estonian_date(value)
    return str(value)


def _at_precision(value: date | None, precision: str) -> Any:
    """A day as a date, anything coarser as its words, unknown as nothing."""
    if value is None:
        return None
    return format_at_precision(value, precision) if is_approximate(precision) else value


# ---------------------------------------------------------------------------
# Facts — read once per page or export
# ---------------------------------------------------------------------------


@dataclass
class Facts:
    """What the chosen columns need beyond the row itself, keyed by Matter id."""

    user: Any
    today: date
    last_sent: dict[Any, date] = field(default_factory=dict)
    commencements: dict[Any, list[Any]] = field(default_factory=lambda: defaultdict(list))
    opinion: dict[Any, str] = field(default_factory=dict)
    attention: dict[Any, list[str]] = field(default_factory=lambda: defaultdict(list))
    source_pages: dict[Any, int] = field(default_factory=dict)
    register_rows: dict[Any, str] = field(default_factory=dict)
    source_instructions: dict[Any, str] = field(default_factory=dict)


def _needs(columns: Iterable[Column], *keys: str) -> bool:
    chosen = {column.key for column in columns}
    return any(key in chosen for key in keys)


def load_facts(
    user: Any, matters: Sequence[Matter], columns: Sequence[Column], *, today: date | None = None
) -> Facts:
    """Everything the chosen columns read, for these rows, in a bounded number of queries."""
    facts = Facts(user=user, today=today or timezone.localdate())
    ids = [matter.pk for matter in matters]
    if not ids:
        return facts
    if _needs(columns, "saadetud"):
        from app.submissions.models import Submission

        facts.last_sent = {
            row["matter_id"]: timezone.localtime(row["last"]).date()
            for row in Submission.objects.visible_to(user)
            .historically_sent()
            .filter(matter_id__in=ids)
            .values("matter_id")
            .annotate(last=Max("sent_at"))
        }
    if _needs(columns, "joustumine"):
        from app.intelligence.enums import FactStatus
        from app.intelligence.models import MatterEffectiveDate

        for record in (
            MatterEffectiveDate.objects.visible_to(user)
            .filter(matter_id__in=ids, status=FactStatus.ACTIVE)
            .order_by("date_value", "pk")
        ):
            facts.commencements[record.matter_id].append(record)
    if _needs(columns, "arvamus"):
        from app.matters.register_filters import (
            OPINION_DRAFTING,
            OPINION_SENT,
            opinion_state_q,
        )

        # The register filter's own definition, so the cell and `?arvamus=`
        # cannot disagree about a row. «Saadetud» wins over «koostamisel».
        for value, label in ((OPINION_DRAFTING, "Koostamisel"), (OPINION_SENT, "Saadetud")):
            for pk in (
                Matter.objects.filter(pk__in=ids)
                .filter(opinion_state_q(user, value))
                .values_list("pk", flat=True)
            ):
                facts.opinion[pk] = label
    if _needs(columns, "tahelepanu"):
        from app.matters import selectors

        # The register's own attention filters (`?tegevus=`), asked of these rows.
        base = Matter.objects.filter(pk__in=ids)
        for value, label in (
            ("hilinenud", "Samm hilinenud"),
            (selectors.REVIEW_DUE, "Ülevaatus käes"),
        ):
            for pk in selectors.filter_by_next_action(base, user, value, facts.today).values_list(
                "pk", flat=True
            ):
                facts.attention[pk].append(label)
        for pk in (
            selectors.filter_by_next_action(base, user, selectors.MISSING, facts.today)
            .filter(is_open=True)
            .values_list("pk", flat=True)
        ):
            facts.attention[pk].append("Järgmine samm puudub")
    if _needs(columns, "allikas"):
        from app.legacy_import.current_state import CurrentRegisterState
        from app.legacy_import.source_pages import MatterSourcePage

        facts.source_pages = dict(
            MatterSourcePage.objects.filter(matter_id__in=ids)
            .values("matter_id")
            .annotate(pages=Count("pk"))
            .values_list("matter_id", "pages")
        )
        facts.register_rows = {
            matter_id: f"Excel {sheet}, rida {row}"
            for matter_id, sheet, row in CurrentRegisterState.objects.visible_to(user)
            .filter(matter_id__in=ids)
            .exclude(source_sheet="")
            .values_list("matter_id", "source_sheet", "source_row_number")
        }
    if _needs(columns, "jargmiseks"):
        from app.legacy_import.register_display import source_instructions_for

        facts.source_instructions = source_instructions_for(matters)
    return facts


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------


def _reference(matter: Matter, facts: Facts) -> str:
    return matter.display_reference or ""


def _title(matter: Matter, facts: Facts) -> str:
    return matter.title


def _stage(matter: Matter, facts: Facts) -> str:
    return matter.stage.label_et if matter.stage is not None else ""


def _owner(matter: Matter, facts: Facts) -> str:
    return matter.owner.display_name if matter.owner is not None else ""


def _next(matter: Matter, facts: Facts) -> str:
    """The Järgmiseks cell's precedence, as words (templates/.../matter_row.html)."""
    from app.matters.next_step import prefetched_upcoming_milestone

    action = next(iter(getattr(matter, "open_actions", []) or []), None)
    if action is not None:
        return str(action.text)
    milestone = prefetched_upcoming_milestone(matter)
    if milestone is not None:
        return f"Oluline tähtaeg: {milestone.title}"
    instruction = facts.source_instructions.get(matter.pk)
    if instruction:
        return f"Excelist: {instruction}"
    return "Puudub — teema on suletud" if not matter.is_open else "Järgmine samm puudub"


def _date(matter: Matter, facts: Facts) -> Any:
    from app.matters.register_dates import register_date

    shown = register_date(matter)
    if shown is None:
        return None
    return shown.display if shown.is_approximate else shown.value


def _date_meaning(matter: Matter, facts: Facts) -> str:
    from app.matters.register_dates import register_date

    shown = register_date(matter)
    if shown is None:
        return ""
    return f"{shown.meaning} · üle tähtaja" if shown.is_overdue else shown.meaning


def _activity(matter: Matter, facts: Facts) -> Any:
    from app.matters.activity import activity_of

    fact = activity_of(matter)
    if fact is None:
        return None
    return fact.display_date if fact.is_approximate else fact.occurred_on


def _activity_kind(matter: Matter, facts: Facts) -> str:
    from app.matters.activity import activity_of

    fact = activity_of(matter)
    return fact.label if fact is not None else ""


def _names(items: Iterable[Any], attribute: str) -> str:
    return "; ".join(sorted(str(getattr(item, attribute)) for item in items))


def _areas(matter: Matter, facts: Facts) -> str:
    names = _names(matter.policy_areas.all(), "name_et")
    other = getattr(matter, "policy_area_other", "") or ""
    return "; ".join(part for part in (names, other) if part)


def _instruments(matter: Matter, facts: Facts) -> str:
    names = _names(matter.legal_instruments.all(), "label_et")
    other = getattr(matter, "legal_instrument_other", "") or ""
    return "; ".join(part for part in (names, other) if part)


def _track(matter: Matter, facts: Facts) -> str:
    return dict(Track.choices).get(matter.track, "") if matter.track else ""


def _year(matter: Matter, facts: Facts) -> Any:
    from app.matters.enums import REGISTER_YEAR_ORIGINS

    # A reporting year is only a register year for these origins; anything
    # else is unknown, never the creation year (app/matters/selectors.py).
    if matter.reporting_year and matter.origin in REGISTER_YEAR_ORIGINS:
        return matter.reporting_year
    return "Teadmata"


def _received(matter: Matter, facts: Facts) -> Any:
    return matter.received_date


def _deadline(matter: Matter, facts: Facts) -> Any:
    return matter.response_deadline


def _opinion(matter: Matter, facts: Facts) -> str:
    return facts.opinion.get(matter.pk, "")


def _sent(matter: Matter, facts: Facts) -> Any:
    return facts.last_sent.get(matter.pk)


def _addressee(matter: Matter, facts: Facts) -> str:
    organisation = matter.addressee_organisation
    return organisation.name if organisation is not None else ""


def _senders(matter: Matter, facts: Facts) -> str:
    return _names(matter.source_organisations.all(), "name")


def _commencement(matter: Matter, facts: Facts) -> Any:
    records = facts.commencements.get(matter.pk, [])
    if len(records) == 1 and records[0].date_value is not None:
        return _at_precision(records[0].date_value, records[0].date_precision)
    words = [
        format_at_precision(record.date_value, record.date_precision)
        if record.date_value is not None
        else (record.get_kind_display() or "Kuupäevata")
        for record in records
    ]
    return "; ".join(words)


def _step_date(matter: Matter, facts: Facts) -> Any:
    action = next(iter(getattr(matter, "open_actions", []) or []), None)
    if action is None or action.target_date is None:
        return None
    return _at_precision(action.target_date, action.date_precision)


def _attention(matter: Matter, facts: Facts) -> str:
    reasons = list(facts.attention.get(matter.pk, []))
    if matter.is_open and not matter.owner_id:
        reasons.append("Vastutajata")
    return "; ".join(reasons)


def _modified(matter: Matter, facts: Facts) -> Any:
    return timezone.localtime(matter.updated_at).date()


def _source(matter: Matter, facts: Facts) -> str:
    parts = []
    if matter.pk in facts.register_rows:
        parts.append(facts.register_rows[matter.pk])
    pages = facts.source_pages.get(matter.pk, 0)
    if pages:
        parts.append(f"OneNote: {pages} {'leht' if pages == 1 else 'lehte'}")
    return "; ".join(parts)


def _state(matter: Matter, facts: Facts) -> str:
    if matter.record_mode == RecordMode.ARCHIVE:
        return "Arhiiv"
    if matter.is_open:
        return "Avatud"
    closed = timezone.localtime(matter.closed_at).date() if matter.closed_at else None
    return f"Suletud {format_estonian_date(closed)}" if closed else "Suletud"


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------

#: In display order. The six `base` columns are matter_row.html's own cells;
#: the default set is exactly the register as it was before «Vali veerud».
CATALOGUE: tuple[Column, ...] = (
    Column(TITLE, "Pealkiri", True, True, (Cell("Pealkiri", _title, 60, True),), sort="title"),
    Column(
        "viide",
        "Viide",
        False,
        False,
        (Cell("Viide", _reference, 12),),
        sort="reference",
        help_text="Registri viitenumber, nt 2026_14.",
    ),
    Column("hetkeseis", "Hetkeseis", True, True, (Cell("Hetkeseis", _stage, 24),)),
    Column("vastutaja", "Vastutaja", True, True, (Cell("Vastutaja", _owner, 22),)),
    Column("jargmiseks", "Järgmiseks", True, True, (Cell("Järgmiseks", _next, 50, True),)),
    Column(
        "kuupaev",
        "Kuupäev",
        True,
        True,
        (Cell("Kuupäev", _date, 14), Cell("Kuupäeva tähendus", _date_meaning, 22)),
        sorts_needing=("kuupaev_asc", "kuupaev_desc"),
    ),
    Column(
        "viimane",
        "Viimane tegevus",
        True,
        True,
        (Cell("Viimane tegevus", _activity, 14), Cell("Viimase tegevuse liik", _activity_kind, 24)),
        sorts_needing=("viimane_uusim", "viimane_vanim"),
    ),
    Column("valdkond", "Valdkond", False, False, (Cell("Valdkond", _areas, 28, True),)),
    Column("oigusakt", "Õigusakt", False, False, (Cell("Õigusakt", _instruments, 28, True),)),
    Column("menetlusliik", "Menetlusliik", False, False, (Cell("Menetlusliik", _track, 18),)),
    Column(
        "aasta",
        "Aruandlusaasta",
        False,
        False,
        (Cell("Aruandlusaasta", _year, 12),),
        help_text="«Teadmata», kui aasta ei ole registri aasta.",
    ),
    Column("saabus", "Saabus", False, False, (Cell("Saabus", _received, 12),)),
    Column(
        "tahtaeg",
        "Arvamuse tähtaeg",
        False,
        False,
        (Cell("Arvamuse tähtaeg", _deadline, 14),),
        sort="deadline",
    ),
    Column("arvamus", "Arvamus", False, False, (Cell("Arvamus", _opinion, 14),)),
    Column(
        "saadetud",
        "Viimati saadetud",
        False,
        False,
        (Cell("Viimati saadetud", _sent, 14),),
        help_text="Viimane tegelikult saadetud arvamus või vastus.",
    ),
    Column("adressaat", "Adressaat", False, False, (Cell("Adressaat", _addressee, 28, True),)),
    Column(
        "saatja",
        "Algataja või saatja",
        False,
        False,
        (Cell("Algataja või saatja", _senders, 28, True),),
    ),
    Column(
        "joustumine", "Jõustumine", False, False, (Cell("Jõustumine", _commencement, 18, True),)
    ),
    Column(
        "samm",
        "Järgmise sammu kuupäev",
        False,
        False,
        (Cell("Järgmise sammu kuupäev", _step_date, 14),),
    ),
    Column("tahelepanu", "Tähelepanu", False, False, (Cell("Tähelepanu", _attention, 28, True),)),
    Column(
        "muudetud",
        "Kirjet muudetud",
        False,
        False,
        (Cell("Kirjet muudetud", _modified, 14),),
        sort="updated",
        help_text="Millal kirjet viimati salvestati — mitte viimane tegevus.",
    ),
    Column("allikas", "Allikas", False, False, (Cell("Allikas", _source, 26, True),)),
    Column("olek", "Olek", False, False, (Cell("Olek", _state, 16),)),
)

BY_KEY: dict[str, Column] = {column.key: column for column in CATALOGUE}
DEFAULT_KEYS: tuple[str, ...] = tuple(column.key for column in CATALOGUE if column.default)


def chosen_keys(params: Any) -> tuple[str, ...]:
    """The columns the address asks for, in catalogue order; the default when it asks none.

    Accepts ``?veerud=a,b`` and ``?veerud=a&veerud=b``. Unknown words are
    ignored (a column retired later must not break a bookmark). The title is
    always first.
    """
    raw: list[str] = []
    getter = getattr(params, "getlist", None)
    values = getter(COLUMNS_PARAM) if getter else [params.get(COLUMNS_PARAM, "")]
    for value in values:
        raw += [part.strip() for part in str(value or "").split(",") if part.strip()]
    known = {key for key in raw if key in BY_KEY}
    if not known:
        return DEFAULT_KEYS
    known.add(TITLE)
    return tuple(column.key for column in CATALOGUE if column.key in known)


def columns_for(keys: Iterable[str]) -> list[Column]:
    return [BY_KEY[key] for key in keys]


def is_default(keys: Sequence[str]) -> bool:
    return tuple(keys) == DEFAULT_KEYS


def columns_value(keys: Sequence[str]) -> str:
    """The canonical `?veerud=` value for a choice — empty for the default."""
    return "" if is_default(keys) else ",".join(keys)


#: Said above the table when a sort read a column the reader has hidden.
SORT_RESET_NOTICE = "Järjestus taastati vaikimisi, sest selle aluseks olev veerg on peidetud."


def effective_sort(sort: str, keys: Sequence[str]) -> tuple[str, str]:
    """The sort to apply with these columns, and a notice when it had to change.

    A heading sort (`kuupaev_*`, `viimane_*`) orders by a column. Hiding that
    column would leave the table sorted by something nobody can see, so the
    register falls back to its default order and says so — the same rule for
    the table and for its export.
    """
    from app.matters.register_selection import DEFAULT_SORT

    for column in CATALOGUE:
        if sort in column.sorts_needing and column.key not in keys:
            return DEFAULT_SORT, SORT_RESET_NOTICE
    return sort, ""


def xlsx_cells(keys: Sequence[str]) -> list[Cell]:
    """The spreadsheet columns for a choice, in display order."""
    return [cell for column in columns_for(keys) for cell in column.cells]


def chooser_options(keys: Sequence[str]) -> list[dict[str, Any]]:
    """What «Vali veerud» lists: every column except the title, ticked as chosen."""
    return [
        {
            "key": column.key,
            "heading": column.heading,
            "checked": column.key in keys,
            "default": column.default,
            "help": column.help_text,
        }
        for column in CATALOGUE
        if column.key != TITLE
    ]


def row_cells(
    matters: Iterable[Matter], keys: Sequence[str], facts: Facts
) -> dict[Any, list[dict[str, str]]]:
    """The extra (non-base) cells per row for the table, as text with a title."""
    extra = [column for column in columns_for(keys) if not column.base]
    cells: dict[Any, list[dict[str, str]]] = {}
    for matter in matters:
        cells[matter.pk] = [
            {"key": column.key, "text": column.text(matter, facts)} for column in extra
        ]
    return cells
