"""Derive the browser icons from the Chamber's round emblem (UI-02).

    python -m tools.brand_favicons            # writes static/brand/koda-round-*
    python -m tools.brand_favicons --check    # exit 1 if a written file differs

**One source, never redrawn.** Every icon is cut from
`static/brand/koda-logo-round.png` — the same file the top bar shows — by
cropping to the seal's own circle and resampling. No line of the emblem is
drawn here; there is no official vector, so there is no SVG icon either.

**Why the seal sits on its paper.** The artwork is one dark ink (#373736) over
an alpha mask, drawn for paper. A browser tab strip is light or dark at the
browser's whim, and on a dark one bare dark ink is invisible — which is how the
old icon (the white horizontal wordmark, squeezed into a square) failed in the
other direction. So the icons composite the ink over white *inside the seal's
own ring* and nowhere else: the corners stay transparent, the outline is the
emblem's, and nothing is added that the printed seal does not already have.
The 180px Apple touch icon is opaque white edge to edge, because iOS draws a
transparent touch icon on black and rounds the corners itself.

The written files are named for what they are (`koda-round-…`), and in
production `{% static %}` adds a content hash to each name
(`CompressedManifestStaticFilesStorage`), so a browser holding the previous
icon is never served it from cache under the new reference.
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

BRAND = Path(__file__).resolve().parent.parent / "static" / "brand"
SOURCE = BRAND / "koda-logo-round.png"

#: The PNG icons and their sizes; the .ico carries 16, 32 and 48 in one file.
PNG_SIZES = (16, 32, 48)
ICO_SIZES = (16, 32, 48)
TOUCH_SIZE = 180

#: Supersampling factor for the paper disc's edge, so it is anti-aliased like
#: the ink drawn over it.
_SUPERSAMPLE = 4

#: The largest size that is sharpened after resampling (see `_scaled`).
_SHARPEN_UP_TO = 48


def png_name(size: int) -> str:
    return f"koda-round-favicon-{size}.png"


ICO_NAME = "koda-round-favicon.ico"
TOUCH_NAME = "koda-round-apple-touch-icon.png"


def _seal() -> Image.Image:
    """The emblem cropped to a square around its circle, ink over a white disc."""
    art = Image.open(SOURCE).convert("RGBA")
    left, top, right, bottom = art.getchannel("A").getbbox() or (0, 0, *art.size)
    side = max(right - left, bottom - top)
    cx, cy = (left + right) / 2, (top + bottom) / 2
    box = (
        round(cx - side / 2),
        round(cy - side / 2),
        round(cx - side / 2) + side,
        round(cy - side / 2) + side,
    )
    art = art.crop(box)

    # The paper: a white disc just inside the ring's outer edge, so the ring's
    # own anti-aliased outside edge is what meets the transparent corners.
    big = side * _SUPERSAMPLE
    mask = Image.new("L", (big, big), 0)
    inset = 2 * _SUPERSAMPLE
    ImageDraw.Draw(mask).ellipse((inset, inset, big - 1 - inset, big - 1 - inset), fill=255)
    mask = mask.resize((side, side), Image.Resampling.LANCZOS)
    paper = Image.new("RGBA", (side, side), (255, 255, 255, 0))
    paper.putalpha(mask)
    paper.alpha_composite(art)
    return paper


def _scaled(seal: Image.Image, size: int) -> Image.Image:
    """Resampled to `size`; the tab sizes get a light unsharp mask.

    A hairline seal shrunk ~30x loses its lines into grey, and the mask gives
    some of that contrast back. It sharpens what resampling produced — it adds
    no line of its own.
    """
    image = seal.resize((size, size), Image.Resampling.LANCZOS)
    if size <= _SHARPEN_UP_TO:
        image = image.filter(ImageFilter.UnsharpMask(radius=0.6, percent=120, threshold=0))
    return image


def _touch(seal: Image.Image) -> Image.Image:
    """Opaque white square with the seal inset, for iOS home screens."""
    tile = Image.new("RGBA", (TOUCH_SIZE, TOUCH_SIZE), (255, 255, 255, 255))
    inner = round(TOUCH_SIZE * 0.86)
    offset = (TOUCH_SIZE - inner) // 2
    tile.alpha_composite(_scaled(seal, inner), (offset, offset))
    return tile.convert("RGB")


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def render() -> dict[str, bytes]:
    """Every icon file, by name, as the bytes that should be on disk."""
    seal = _seal()
    files = {png_name(size): _png_bytes(_scaled(seal, size)) for size in PNG_SIZES}
    largest = _scaled(seal, max(ICO_SIZES))
    buffer = io.BytesIO()
    largest.save(
        buffer,
        format="ICO",
        sizes=[(size, size) for size in ICO_SIZES],
        append_images=[_scaled(seal, size) for size in ICO_SIZES if size != max(ICO_SIZES)],
    )
    files[ICO_NAME] = buffer.getvalue()
    files[TOUCH_NAME] = _png_bytes(_touch(seal))
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare instead of writing")
    args = parser.parse_args(argv)
    stale = []
    for name, data in render().items():
        path = BRAND / name
        if args.check:
            if not path.exists() or path.read_bytes() != data:
                stale.append(name)
        else:
            path.write_bytes(data)
            print(f"wrote {path.relative_to(BRAND.parent.parent)} ({len(data)} bytes)")
    if stale:
        print("stale: " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
