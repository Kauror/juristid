"""A Statistika panel as a self-contained SVG, drawn from its chart spec (UX-005).

The image a lawyer puts on a slide or in a Koda publication: neutral print
colours on white (or on nothing — ``transparent``), one system sans-serif, and
everything the figure needs to be read away from the page — the title, the
period, the filters it was taken under, the legend, the values, and the notes
on what it does not count. It holds no script, no stylesheet link, no font or
image reference and no CSS variable: the file is whole on its own.

Every number printed here is a value or a text of the spec
(`app.reporting.chart_spec`), which names the `opinion_stats` results the page
shows. Geometry — heights, widths, the axis — is the only thing worked out
here. An unknown value (``None``) is drawn as no column and the label «—»,
never as a zero-height column with «0».
"""

from __future__ import annotations

import math
from datetime import date
from xml.sax.saxutils import escape, quoteattr

from app.reporting import chart_spec as cs

WIDTH = 1200
MARGIN = 40
FONT = "Arial, Helvetica, sans-serif"
INK = "#1a1a1a"
SECONDARY = "#4a4a4a"
MUTED = "#6b6b6b"
GRID = "#d9d9d9"
AXIS = "#8c8c8c"

#: The page's series slots in print colours (`statseries--<slot>`).
SERIES = {
    "0": "#1f5fa8",
    "1": "#e07b39",
    "2": "#3a9d5d",
    "3": "#b8434e",
    "4": "#7a5aa6",
    "5": "#2a9fb0",
    "6": "#a0782c",
    "7": "#c45a9a",
    "muted": "#9aa3ad",
}
#: The bands of a share bar, darkest first (`statband--<n>`).
BANDS = ("#0b3d6e", "#1f5fa8", "#5a8fd0", "#a9c6ea")

PLOT_HEIGHT = 340


def _text(
    x: float,
    y: float,
    content: str,
    *,
    size: int = 13,
    fill: str = INK,
    weight: str = "normal",
    anchor: str = "start",
) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}"'
        f' font-weight="{weight}" text-anchor="{anchor}">{escape(content)}</text>'
    )


def _rect(x: float, y: float, w: float, h: float, fill: str, opacity: float = 1.0) -> str:
    extra = f' fill-opacity="{opacity:.2f}"' if opacity < 1 else ""
    return (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w, 0):.1f}"'
        f' height="{max(h, 0):.1f}" fill="{fill}"{extra}/>'
    )


def _wrap(text: str, width: int) -> list[str]:
    """``text`` in lines of at most ``width`` characters, broken at spaces."""
    lines: list[str] = []
    line = ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        lines.append(line)
    return lines


def _ticks(top: float) -> list[float]:
    """Round axis values from 0 to just above ``top``, about five of them."""
    if top <= 0:
        return [0.0, 1.0]
    raw = top / 5
    power = 10 ** math.floor(math.log10(raw))
    step = next(m * power for m in (1, 2, 2.5, 5, 10) if m * power >= raw)
    if step < 1 and float(top).is_integer():
        step = 1
    count = math.ceil(top / step)
    return [round(i * step, 6) for i in range(count + 1)]


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}".replace(".", ",")


def _legend(spec: cs.ChartSpec, y: float) -> tuple[list[str], float]:
    """Swatches and names in rows; the y below them."""
    if spec.kind == cs.YEARS or not spec.series or spec.kind == cs.SHARE:
        return [], y
    out = []
    x = float(MARGIN)
    for series in spec.series:
        width = 22 + 7.5 * len(series.name) + 18
        if x + width > WIDTH - MARGIN and x > MARGIN:
            x = MARGIN
            y += 22
        out.append(_rect(x, y - 11, 12, 12, SERIES.get(series.slot, SERIES["0"])))
        out.append(_text(x + 18, y, series.name, size=13, fill=SECONDARY))
        x += width
    return out, y + 18


def _columns(spec: cs.ChartSpec, top_y: float) -> tuple[list[str], float]:
    """A column chart: stacked months, paired months, or one column per year."""
    out: list[str] = []
    n = max(len(spec.categories), 1)
    left, right = MARGIN + 44, WIDTH - MARGIN
    plot_w = right - left
    base = top_y + PLOT_HEIGHT
    if spec.kind == cs.STACKED:
        peaks = [float(total or 0) for total in spec.totals]
    else:
        peaks = [float(v) for s in spec.series for v in s.values if v is not None]
    ticks = _ticks(max(peaks, default=0))
    scale = PLOT_HEIGHT / ticks[-1]
    unit = next((s.unit for s in spec.series if s.unit), "")
    if spec.kind == cs.PAIRED:
        unit = " / ".join(s.unit for s in spec.series)
    for tick in ticks:
        y = base - tick * scale
        out.append(
            f'<line x1="{left:.1f}" y1="{y:.1f}" x2="{right:.1f}" y2="{y:.1f}"'
            f' stroke="{GRID if tick else AXIS}" stroke-width="1"/>'
        )
        out.append(_text(left - 8, y + 4, _number(tick), size=12, fill=MUTED, anchor="end"))
    out.append(_text(left - 8, top_y - 12, unit, size=12, fill=MUTED, anchor="end"))
    band = plot_w / n
    labelled = band >= 26
    every = 1 if band >= 44 else math.ceil(44 / band)
    for index, label in enumerate(spec.categories):
        centre = left + band * (index + 0.5)
        if index % every == 0:
            out.append(_text(centre, base + 18, label, size=12, fill=SECONDARY, anchor="middle"))
        if spec.kind == cs.STACKED:
            width = band * 0.64
            y = base
            for series in spec.series:
                value = series.values[index] or 0
                height = value * scale
                y -= height
                out.append(_rect(centre - width / 2, y, width, height, SERIES[series.slot]))
            total = spec.totals[index] if index < len(spec.totals) else None
            if labelled and total:
                out.append(_text(centre, y - 6, str(total), size=12, anchor="middle"))
        elif spec.kind == cs.PAIRED:
            width = band * 0.34
            for offset, pair in zip((-width, 0.0), spec.series, strict=False):
                count = pair.values[index]
                height = (count or 0) * scale
                x = centre + offset
                out.append(_rect(x, base - height, width, height, SERIES[pair.slot]))
                if labelled and count:
                    out.append(
                        _text(
                            x + width / 2, base - height - 5, str(count), size=11, anchor="middle"
                        )
                    )
        else:
            only = spec.series[0]
            known = only.values[index]
            text = only.texts[index] if index < len(only.texts) else _number(known or 0)
            partial = index < len(spec.partial) and spec.partial[index]
            width = band * 0.62
            if known is None:
                out.append(_text(centre, base - 6, "—", size=12, fill=MUTED, anchor="middle"))
                continue
            height = known * scale
            out.append(
                _rect(
                    centre - width / 2,
                    base - height,
                    width,
                    height,
                    SERIES["0"],
                    0.5 if partial else 1,
                )
            )
            if labelled:
                out.append(_text(centre, base - height - 6, text, size=12, anchor="middle"))
    return out, base + 34


def _share(spec: cs.ChartSpec, top_y: float) -> tuple[list[str], float]:
    """One bar split into parts, and a key with each part's count and share."""
    out: list[str] = []
    counts, shares = spec.series[0], spec.series[1] if len(spec.series) > 1 else None
    total = sum(v or 0 for v in counts.values)
    colours = [SERIES["0"], SERIES["muted"]] if spec.key == "otsus" else list(BANDS)
    x, width = float(MARGIN), float(WIDTH - 2 * MARGIN)
    for index, value in enumerate(counts.values):
        if not value or not total:
            continue
        part = width * value / total
        out.append(_rect(x, top_y, part, 36, colours[index % len(colours)]))
        x += part
    y = top_y + 70
    for index, label in enumerate(spec.categories):
        out.append(_rect(MARGIN, y - 12, 14, 14, colours[index % len(colours)]))
        out.append(_text(MARGIN + 24, y, label, size=15))
        out.append(
            _text(MARGIN + 520, y, counts.texts[index], size=15, weight="bold", anchor="end")
        )
        if shares is not None:
            out.append(
                _text(MARGIN + 600, y, shares.texts[index], size=15, fill=SECONDARY, anchor="end")
            )
        y += 30
    return out, y + 6


def render(spec: cs.ChartSpec, *, transparent: bool = False, generated: date | None = None) -> str:
    """``spec`` as an SVG document; a table panel has no picture (`ValueError`)."""
    if spec.is_table:
        raise ValueError(f"{spec.key} is a table: it is downloaded as Excel only")
    body: list[str] = []
    y = 46.0
    body.append(_text(MARGIN, y, spec.title, size=24, weight="bold"))
    y += 28
    body.append(_text(MARGIN, y, spec.period, size=15, fill=SECONDARY))
    y += 22
    # The period is the line above; the rest of the filters go here.
    others = " · ".join(
        f"{label}: {value}" for label, value in spec.filters if label not in ("Periood", "Aastad")
    )
    body.append(_text(MARGIN, y, others, size=13, fill=MUTED))
    y += 34
    legend, y = _legend(spec, y)
    body.extend(legend)
    y += 18
    if spec.empty:
        body.append(_text(MARGIN, y + 20, "Andmed puuduvad.", size=15, fill=SECONDARY))
        y += 50
    elif spec.kind == cs.SHARE:
        chart, y = _share(spec, y)
        body.extend(chart)
    else:
        chart, y = _columns(spec, y + 14)
        body.extend(chart)
    notes = list(spec.notes)
    if any(spec.partial):
        notes = [
            "Jooksev aasta on pooleli (heledam tulp)."
            if note == "Jooksev aasta on pooleli."
            else note
            for note in notes
        ]
    for note in notes:
        for line in _wrap(note, 150):
            y += 18
            body.append(_text(MARGIN, y, line, size=12, fill=SECONDARY))
    y += 28
    made = generated or date.today()
    body.append(
        _text(
            MARGIN,
            y,
            f"Allikas: Juristid, Eesti Kaubandus-Tööstuskoda · koostatud {made:%d.%m.%Y}",
            size=11,
            fill=MUTED,
        )
    )
    height = math.ceil(y + 24)
    background = "" if transparent else f'<rect width="{WIDTH}" height="{height}" fill="#ffffff"/>'
    title = escape(spec.title)
    description = escape(" · ".join(part for part in (spec.period, spec.filter_line()) if part))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}"'
        f' viewBox="0 0 {WIDTH} {height}" role="img" aria-label={quoteattr(spec.title)}'
        f' font-family="{FONT}">'
        f"<title>{title}</title><desc>{description}</desc>"
        f"{background}{''.join(body)}</svg>\n"
    )
