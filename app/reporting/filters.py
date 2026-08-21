"""Turning the context back into something a person can see and remove.

The filter bar and the address bar are built from the same object, so they
cannot disagree. That is the whole reason the chips are generated here from
``ReportingContext.query_params`` rather than written into a template: a chip
that says one thing while the URL says another is worse than no chip, because
the reader believes the chip.

Which filters appear depends on the tab. Showing every dimension everywhere
would put an OneNote section picker above a submissions chart, and a filter that
cannot apply to what is on screen teaches people to distrust the ones that can
(brief 13).
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from app.accounts.models import User
from app.matters.enums import MatterOrigin, RecordMode
from app.reporting import context as ctx
from app.reporting.context import ReportingContext
from app.reporting.selectors.historical import visible_pages
from app.taxonomy.models import PolicyArea, Tag
from app.workflow.enums import Track
from app.workflow.models import StageVocabulary


@dataclass(frozen=True)
class Chip:
    """One active filter, and the query string that removes it."""

    label: str
    value: str
    remove_query: str


@dataclass(frozen=True)
class PeriodOption:
    key: str
    label: str
    query: str
    active: bool


#: Which contextual dimensions each tab offers, beyond the shared bar. The
#: shared bar is period, record mode, origin, owner and policy area everywhere.
TAB_FILTERS: dict[str, tuple[str, ...]] = {
    "ulevaade": (),
    "teemad": (ctx.PARAM_STAGE, ctx.PARAM_TRACK, ctx.PARAM_TAG),
    "tegevus": (ctx.PARAM_STAGE,),
    "ajalooline": (ctx.PARAM_SECTION,),
    "andmekvaliteet": (),
}

_LABELS: dict[str, str] = {
    ctx.PARAM_RECORD_MODE: "Kirje liik",
    ctx.PARAM_ORIGIN: "Päritolu",
    ctx.PARAM_OWNER: "Vastutaja",
    ctx.PARAM_POLICY_AREA: "Valdkond",
    ctx.PARAM_STAGE: "Hetkeseis",
    ctx.PARAM_TRACK: "Menetlusliik",
    ctx.PARAM_TAG: "Silt",
    ctx.PARAM_SECTION: "OneNote'i sektsioon",
    ctx.PARAM_FILE_TYPE: "Failitüüp",
}


def available_years(context: ReportingContext) -> list[PeriodOption]:
    """Every year a reader may actually select, newest first.

    Built from the *authorized* population, so a year that exists only inside
    records this viewer cannot see is not offered — an empty year in a list is
    itself a disclosure.

    Restricted to `REGISTER_YEAR_ORIGINS` for the reason that constant exists: a
    OneNote-only Matter's `reporting_year` comes from when somebody last edited
    the page, and offering 2021 because a page about a 2018 draft was touched
    then would invite a reader to filter on a year nobody filed anything under.
    Those Matters stay in *Teadmata aasta* here exactly as they do in the charts
    (app/matters/enums.py, Stage-2E.1 brief 10).

    Deliberately not the quick choices. Somebody who wants 2014 should be able
    to pick 2014, not click a chart bar to discover the URL.
    """
    from app.matters.enums import REGISTER_YEAR_ORIGINS
    from app.reporting.selectors.base import visible_matters

    years = (
        visible_matters(context)
        .filter(reporting_year__isnull=False, origin__in=REGISTER_YEAR_ORIGINS)
        .values_list("reporting_year", flat=True)
        .distinct()
        .order_by("-reporting_year")
    )
    return [
        PeriodOption(
            key=str(year),
            label=str(year),
            query=urlencode(context.query_params(**{ctx.PARAM_PERIOD: str(year)})),
            active=context.period.key == str(year),
        )
        for year in years
    ]


def period_options(context: ReportingContext) -> list[PeriodOption]:
    options = [
        PeriodOption(
            key=period.key,
            label=period.label,
            query=urlencode(context.query_params(**{ctx.PARAM_PERIOD: period.key})),
            active=period.key == context.period.key,
        )
        for period in ctx.period_options(context.today)
    ]
    if not any(option.active for option in options):
        # A drill-through link carrying an explicit year lands here. Showing it
        # as a fifth, selected option is more honest than highlighting nothing
        # and leaving the reader to wonder which period they are looking at.
        options.append(
            PeriodOption(
                key=context.period.key,
                label=context.period.label,
                query=urlencode(context.query_params()),
                active=True,
            )
        )
    return options


def _display_value(context: ReportingContext, param: str, raw: str) -> str:
    """A chip shows a name, not a key. A UUID in a chip is not a filter label."""
    if param == ctx.PARAM_RECORD_MODE:
        return dict(RecordMode.choices).get(raw, raw)
    if param == ctx.PARAM_ORIGIN:
        return dict(MatterOrigin.choices).get(raw, raw)
    if param == ctx.PARAM_TRACK:
        return dict(Track.choices).get(raw, raw)
    if param == ctx.PARAM_OWNER:
        owner = User.objects.filter(pk=raw).first() if raw else None
        return owner.display_name if owner else "tundmatu"
    if param == ctx.PARAM_POLICY_AREA:
        area = PolicyArea.objects.filter(key=raw).first()
        return area.name_et if area else raw
    if param == ctx.PARAM_STAGE:
        stage = StageVocabulary.objects.filter(key=raw).first()
        return stage.label_et if stage else raw
    if param == ctx.PARAM_TAG:
        tag = Tag.objects.filter(key=raw).first()
        return tag.name_et if tag else raw
    return raw


def chips(context: ReportingContext) -> list[Chip]:
    """Every active filter except the period, which has its own control."""
    params = context.query_params()
    rows: list[Chip] = []
    for param, raw in params.items():
        if param == ctx.PARAM_PERIOD or not raw:
            continue
        rows.append(
            Chip(
                label=_LABELS.get(param, param),
                value=_display_value(context, param, raw),
                remove_query=urlencode(context.query_params(**{param: ""})),
            )
        )
    return rows


def options(context: ReportingContext, tab: str) -> dict[str, object]:
    """The choices each select offers, limited to what this tab can use.

    The section list is built from the corpus rather than from a constant, and
    only from pages the viewer may reach: a filter offering a section that
    exists only inside restricted material would name it to somebody who may
    not see it.
    """
    contextual = TAB_FILTERS.get(tab, ())
    payload: dict[str, object] = {
        "record_modes": RecordMode.choices,
        "origins": MatterOrigin.choices,
        "owners": User.objects.filter(is_active=True).order_by("display_name"),
        "policy_areas": PolicyArea.objects.filter(is_active=True).order_by("name_et"),
        "show_stage": ctx.PARAM_STAGE in contextual,
        "show_track": ctx.PARAM_TRACK in contextual,
        "show_tag": ctx.PARAM_TAG in contextual,
        "show_section": ctx.PARAM_SECTION in contextual,
    }

    if ctx.PARAM_STAGE in contextual:
        payload["stages"] = StageVocabulary.objects.filter(is_active=True).order_by("sort_order")
    if ctx.PARAM_TRACK in contextual:
        payload["tracks"] = Track.choices
    if ctx.PARAM_TAG in contextual:
        payload["tags"] = Tag.objects.filter(is_active=True).order_by("name_et")
    if ctx.PARAM_SECTION in contextual:
        payload["sections"] = source_sections(context)

    return payload


def source_sections(context: ReportingContext) -> list[str]:
    """Sections that exist on pages this reader may reach, and no others.

    A picker offering a section that appears only inside restricted material
    would name that material to somebody who may not open it.
    """
    return sorted(
        {
            section
            for section in visible_pages(context).values_list("source_section", flat=True)
            if section
        }
    )


def hidden_inputs(
    context: ReportingContext, exclude: tuple[str, ...] = ()
) -> list[tuple[str, str]]:
    """Filter state a GET form must carry so submitting it does not drop it."""
    return [(key, value) for key, value in context.query_params().items() if key not in exclude]
