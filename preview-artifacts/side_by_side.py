"""Build the product-owner comparison artifact. PREVIEW ONLY.

Two 1560x991 captures of the *same* synthetic Matter, side by side at 1:1 —
current on the left, refinement preview on the right — with nothing scaled.
The originals stay beside it so details can be read at full resolution.

    uv run python preview-artifacts/side_by_side.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent / "matter-refinement"
GAP = 24
BAR = 34
GROUND = (11, 14, 18)
RULE = (42, 50, 59)
INK = (232, 237, 242)


def build(
    left_name: str, right_name: str, out_name: str, left_label: str, right_label: str
) -> None:
    left = Image.open(OUT / f"{left_name}.png").convert("RGB")
    right = Image.open(OUT / f"{right_name}.png").convert("RGB")
    height = max(left.height, right.height)
    canvas = Image.new("RGB", (left.width + GAP + right.width, height + BAR), GROUND)
    canvas.paste(left, (0, BAR))
    canvas.paste(right, (left.width + GAP, BAR))

    draw = ImageDraw.Draw(canvas)
    draw.text((12, 11), left_label, fill=INK)
    draw.text((left.width + GAP + 12, 11), right_label, fill=INK)
    draw.line(
        [(left.width + GAP // 2, 0), (left.width + GAP // 2, height + BAR)], fill=RULE, width=2
    )

    canvas.save(OUT / f"{out_name}.png")
    print(f"  {out_name}.png  {canvas.width}x{canvas.height}")


def main() -> int:
    print("side-by-side")
    build(
        "A-current-1560x991-viewport",
        "A-preview-1560x991-viewport",
        "A-side-by-side-1560x991",
        "PRAEGUNE  /teemad/<pk>/                                    1560 x 991",
        "ETTEPANEK  /disainisusteem/teema-refinement/<pk>/          1560 x 991",
    )
    build(
        "A-current-1560x991",
        "A-preview-1560x991",
        "A-side-by-side-full-page",
        "PRAEGUNE  /teemad/<pk>/  (kogu leht)",
        "ETTEPANEK  /disainisusteem/teema-refinement/<pk>/  (kogu leht)",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
