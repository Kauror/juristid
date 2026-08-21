"""Chart geometry, computed on the server.

No charting library, no client-side rendering, and no JavaScript beyond what the
rest of the product already loads. A composition chart here is a list of rows
with a width percentage, drawn with CSS; a trend is an inline SVG polyline whose
coordinates were worked out in Python.

That is not asceticism. Three things follow from it that a chart library would
have made harder:

* the numbers on screen are the numbers the selectors computed, with no second
  aggregation step in the browser to disagree with them;
* every segment is a real ``<a>``, so drill-through works with a keyboard and
  with a screen reader, and a middle click opens it in a tab;
* the page is legible with JavaScript disabled, which is what the accessibility
  target actually requires (master specification 17.8, 18.10).

Both shapes carry a text alternative and a data table. Nothing on these pages is
readable *only* by measuring a bar (brief 46, 60).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.reporting.metric_types import MetricResult, Segment

#: Trend viewBox. Fixed units, scaled by CSS: the SVG is drawn once in abstract
#: coordinates and stretches to whatever column it lands in.
TREND_WIDTH = 720
TREND_HEIGHT = 200
TREND_PAD_LEFT = 44
TREND_PAD_RIGHT = 12
TREND_PAD_TOP = 12
TREND_PAD_BOTTOM = 28


@dataclass(frozen=True)
class Bar:
    """One row of a composition chart."""

    label: str
    value: int
    #: Width as a percentage of the largest bar, so the longest row fills the
    #: track. Share of the total is a different number and is shown separately.
    width: float
    share: float
    url: str
    note: str
    is_unknown: bool

    @property
    def share_text(self) -> str:
        """For a reader: Estonian uses a decimal comma."""
        return f"{self.share * 100:.1f}%".replace(".", ",")

    @property
    def width_css(self) -> str:
        """For CSS: a decimal *point*, whatever the reader's language.

        Django localizes every number a template renders, and Estonian uses a
        comma — so `width:25,0%` reaches the browser, which is not a CSS length,
        so the fill loses its width and fills its track. Every bar on every
        chart then renders full length.

        A screenshot from the first green CI round is what caught it: four bars
        reading 1, 0, 0 and 0, all exactly the same size. No test asserting
        counts could have — the numbers beside the bars were right all along
        (docs/adr/0010: some defects only a browser shows).
        """
        return f"{self.width:.1f}"


@dataclass(frozen=True)
class Point:
    x: float
    y: float
    label: str
    value: int
    url: str

    #: SVG coordinates, formatted here for the same reason as ``Bar.width_css``:
    #: `cx="123,4"` is not a coordinate, and the browser drops the attribute.
    @property
    def cx(self) -> str:
        return f"{self.x:.1f}"

    @property
    def cy(self) -> str:
        return f"{self.y:.1f}"


@dataclass(frozen=True)
class Trend:
    """An inline SVG line chart, fully described by these fields."""

    points: tuple[Point, ...]
    polyline: str
    maximum: int
    width: int = TREND_WIDTH
    height: int = TREND_HEIGHT

    @property
    def is_drawable(self) -> bool:
        """Two points make a line. One is a single value and is shown as text.

        A "trend" through one observation is a chart that invents a direction.
        """
        return len(self.points) >= 2

    @property
    def baseline_y(self) -> float:
        return TREND_HEIGHT - TREND_PAD_BOTTOM

    @property
    def baseline_css(self) -> str:
        return f"{self.baseline_y:.1f}"

    @property
    def axis_labels(self) -> tuple[Point, ...]:
        """Every point, or a thinned selection when the axis would collide.

        Sixteen years of register history at 720 units wide is roughly 42 units
        per label; four-digit years need about 30. Thinning starts above that.
        """
        if len(self.points) <= 16:
            return self.points
        step = (len(self.points) // 12) + 1
        return tuple(self.points[::step])


def bars(result: MetricResult, *, limit: int | None = None) -> list[Bar]:
    """Composition rows for one metric's segments, largest bar full width."""
    segments: tuple[Segment, ...] = result.segments
    if limit is not None:
        segments = segments[:limit]

    largest = max((segment.value for segment in segments), default=0)
    total = sum(segment.value for segment in segments)

    return [
        Bar(
            label=segment.label,
            value=segment.value,
            width=(100.0 * segment.value / largest) if largest else 0.0,
            share=(segment.value / total) if total else 0.0,
            url=segment.url,
            note=segment.note,
            is_unknown=segment.is_unknown,
        )
        for segment in segments
    ]


def trend(result: MetricResult) -> Trend:
    """A line through one metric's ordered segments.

    The vertical scale always starts at zero. A trend drawn from its own minimum
    turns a 3 % change into a cliff, and this product's charts are read by people
    deciding where to put a lawyer's week.
    """
    segments = result.segments
    if not segments:
        return Trend(points=(), polyline="", maximum=0)

    maximum = max(segment.value for segment in segments) or 1
    span = TREND_WIDTH - TREND_PAD_LEFT - TREND_PAD_RIGHT
    plot_height = TREND_HEIGHT - TREND_PAD_TOP - TREND_PAD_BOTTOM
    divisor = max(len(segments) - 1, 1)

    points = tuple(
        Point(
            x=TREND_PAD_LEFT + (span * index / divisor),
            y=TREND_PAD_TOP + plot_height * (1 - segment.value / maximum),
            label=segment.label,
            value=segment.value,
            url=segment.url,
        )
        for index, segment in enumerate(segments)
    )
    polyline = " ".join(f"{point.x:.1f},{point.y:.1f}" for point in points)
    return Trend(points=points, polyline=polyline, maximum=maximum)


def summarise(result: MetricResult, *, top: int = 3) -> str:
    """The text alternative a chart's ``<desc>`` and caption both use.

    Written from the same data the chart draws, so it cannot describe a
    different picture. A reader who cannot see the bars gets the shape of the
    answer in one sentence and the exact figures in the table below it.
    """
    if not result.segments:
        return "Andmeid ei ole."

    total = result.segment_total
    ordered = sorted(result.segments, key=lambda segment: segment.value, reverse=True)
    leading = ordered[:top]
    parts = [
        f"{segment.label} {segment.value}"
        + (f" ({100.0 * segment.value / total:.0f}%)" if total else "")
        for segment in leading
    ]
    tail = len(ordered) - len(leading)
    sentence = f"{len(result.segments)} rühma, kokku {total}. Suurimad: " + "; ".join(parts)
    if tail > 0:
        sentence += f"; ja veel {tail} rühma"
    return sentence + "."
