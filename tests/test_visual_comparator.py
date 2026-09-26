"""The visual comparator's contract, on synthetic images (ENG-037).

`e2e/visual_compare.py` judges every screenshot the visual job takes. Its old
rule — luma of the difference over 24, on more than 0.2% of the image — passed a
removed control on 29 of 36 baselines, a recoloured badge on 23, and a pure-blue
change of up to 214 at any size. These tests hold the replacement to what a
visual gate is for, and each defect case also asserts that the *old* rule missed
it, so none of them passes vacuously.

The noise cases are shaped from what CI actually produced across eleven runs of
the same code: a spot of up to 12 pixels moving by up to 61 in a channel at a
glyph edge, and faint anti-aliasing drift of up to 540 pixels moving by at most 7.
"""

from __future__ import annotations

import pytest
from PIL import Image, ImageChops, ImageDraw

from e2e.visual_compare import CHANNEL_THRESHOLD, PIXEL_BUDGET, judge

SURFACE = (16, 20, 24)
TEXT = (230, 233, 236)
ACCENT = (28, 150, 214)


def _page(width: int, height: int) -> Image.Image:
    """A dark page with some text-like rows and a control, like the real ones."""
    image = Image.new("RGB", (width, height), SURFACE)
    draw = ImageDraw.Draw(image)
    for y in range(40, height - 40, 48):
        draw.rectangle((32, y, min(width - 32, 32 + 600), y + 10), fill=TEXT)
    draw.rectangle((200, 120, 280, 152), fill=ACCENT)  # an 80×32 control
    draw.line((32, 100, width - 32, 100), fill=(60, 66, 72))  # a 1px rule
    return image


def _old_rule_passes(expected: Image.Image, actual: Image.Image) -> bool:
    """The comparator this replaced, verbatim in effect."""
    difference = ImageChops.difference(actual, expected)
    over = difference.convert("L").point(lambda value: 255 if value > 24 else 0)
    differing = over.histogram()[255]
    return differing / (expected.width * expected.height) <= 0.002


SMALL = (400, 240)
HUGE = (1440, 4000)


def test_identical_images_pass():
    page = _page(*SMALL)
    verdict, _ = judge(page, page.copy())
    assert verdict.passed and verdict.differing == 0 and verdict.peak == 0


def test_the_measured_renderer_noise_passes():
    """A 12-pixel glyph-edge spot up to 61, plus 540 pixels of faint drift up to 7."""
    expected = _page(*HUGE)
    actual = expected.copy()
    pixels = actual.load()
    for index in range(12):
        x, y = 247 + index % 3, 1042 + index * 10
        r, g, b = pixels[x, y]
        pixels[x, y] = (min(255, r + 61 - index * 5), g, b)
    for index in range(540):
        x, y = 40 + (index * 7) % 1300, 300 + (index * 13) % 3000
        r, g, b = pixels[x, y]
        pixels[x, y] = (r + 7, g + 7, b + 7)

    verdict, _ = judge(expected, actual)

    assert verdict.passed, verdict
    assert verdict.differing <= 12


@pytest.mark.parametrize("size", [SMALL, HUGE], ids=["small", "huge"])
def test_a_removed_control_fails_at_any_size(size):
    expected = _page(*size)
    actual = expected.copy()
    ImageDraw.Draw(actual).rectangle((200, 120, 280, 152), fill=SURFACE)

    verdict, _ = judge(expected, actual)

    assert not verdict.passed
    if size == HUGE:
        assert _old_rule_passes(expected, actual), "the old rule caught it; test proves nothing"


@pytest.mark.parametrize("size", [SMALL, HUGE], ids=["small", "huge"])
def test_the_same_small_defect_fails_on_a_small_and_a_huge_page(size):
    """A 10×4 block of text lost — about one short word."""
    expected = _page(*size)
    actual = expected.copy()
    ImageDraw.Draw(actual).rectangle((40, 40, 49, 43), fill=SURFACE)

    verdict, _ = judge(expected, actual)

    assert verdict.differing == 40
    assert not verdict.passed


def test_a_moved_control_fails():
    expected = _page(*HUGE)
    actual = expected.copy()
    draw = ImageDraw.Draw(actual)
    draw.rectangle((200, 120, 280, 152), fill=SURFACE)
    draw.rectangle((204, 120, 284, 152), fill=ACCENT)  # four pixels to the right

    verdict, _ = judge(expected, actual)

    assert not verdict.passed
    assert _old_rule_passes(expected, actual)


def test_a_meaningful_recolour_of_a_thin_rule_fails():
    expected = _page(*HUGE)
    actual = expected.copy()
    ImageDraw.Draw(actual).line((32, 100, 1408, 100), fill=(110, 116, 122))

    verdict, _ = judge(expected, actual)

    assert not verdict.passed
    assert _old_rule_passes(expected, actual)


def test_a_change_in_one_channel_only_fails():
    """Luma counts blue at 0.11: this moved blue by 150 and the old rule saw 16."""
    expected = _page(*HUGE)
    actual = expected.copy()
    r, g, b = ACCENT
    ImageDraw.Draw(actual).rectangle((200, 120, 280, 152), fill=(r, g, max(0, b - 150)))

    verdict, _ = judge(expected, actual)

    assert not verdict.passed
    assert verdict.peak == 150
    assert _old_rule_passes(expected, actual)


def test_a_faint_change_under_the_threshold_is_noise_whatever_its_size():
    """The other side of the threshold: an anti-aliasing shift is not a defect."""
    expected = _page(*SMALL)
    actual = Image.eval(expected, lambda value: min(255, value + CHANNEL_THRESHOLD))

    verdict, _ = judge(expected, actual)

    assert verdict.passed and verdict.differing == 0


def test_the_budget_is_absolute_and_small():
    """The two numbers the ADR and the module docstring describe."""
    assert CHANNEL_THRESHOLD == 16
    assert PIXEL_BUDGET == 24


def test_a_size_change_is_refused_before_any_counting():
    with pytest.raises(ValueError, match="sizes differ"):
        judge(_page(400, 240), _page(400, 241))
