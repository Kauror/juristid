"""How a candidate screenshot is judged against its baseline (ENG-037).

Two numbers, both measured rather than chosen:

* **`CHANNEL_THRESHOLD`** — a pixel differs when its *largest* channel moved by
  more than this. Per channel, not the luma of the difference: luma weights
  blue at 0.11, so a badge that changed only its blue could move 214 of 255 and
  never be counted.
* **`PIXEL_BUDGET`** — how many differing pixels a scenario may have, as an
  **absolute count**. The old limit was 0.2% of the image, which is 98 pixels on
  a 1024×48 strip and 8,115 on a 1440×2818 page: the taller the capture, the
  bigger the change it swallowed. A removed button is the same number of pixels
  on either.

**The measurement.** Eleven CI runs of `main` and its PRs on 2026-09-26, 36
scenarios each, compared candidate against candidate — code that renders the
same thing, run on different runners. 29 scenarios were byte-identical in every
pair. The rest differed in one spot each: `minu-too` by at most 10 in any
channel on at most 8 pixels, and the four open-Matter captures by up to 61 on at
most 12 pixels (6 of them above 16, 3 above 32) — anti-aliasing at one glyph
edge. The baselines themselves carried a second, wider but fainter drift on
`minu-too` and `teemad-*`: 199 and 540 pixels, none of them more than 7 in any
channel. So a threshold of 16 ignores all of the faint drift, and a budget of
24 is four times the worst noise that clears it — while a 1px rule 30 pixels
long, a moved 20×12 label or a single recoloured glyph line exceeds it.

The contract is `tests/test_visual_comparator.py`, which judges synthetic
images through this module and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageChops

#: A pixel differs when any one channel moved by more than this (0–255).
CHANNEL_THRESHOLD = 16

#: Differing pixels a scenario may have, whatever its size.
PIXEL_BUDGET = 24


@dataclass(frozen=True)
class Verdict:
    differing: int
    peak: int

    @property
    def passed(self) -> bool:
        return self.differing <= PIXEL_BUDGET


def judge(expected: Image.Image, actual: Image.Image) -> tuple[Verdict, Image.Image]:
    """The verdict, and the difference image to save beside a failure.

    Both images must be the same size; a size change is its own failure, reported
    before anything is compared (a page that grew is not a pixel budget question).
    """
    if expected.size != actual.size:
        raise ValueError(f"sizes differ: {actual.size} against {expected.size}")
    difference = ImageChops.difference(actual.convert("RGB"), expected.convert("RGB"))
    red, green, blue = difference.split()
    peak_channel = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    histogram = peak_channel.histogram()
    differing = sum(histogram[CHANNEL_THRESHOLD + 1 :])
    peak = max((value for value, count in enumerate(histogram) if count), default=0)
    return Verdict(differing=differing, peak=peak), difference
