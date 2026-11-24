"""Draw the DeltaPatcher icon: deltapatcher/assets/deltapatcher.ico (16-256 px) and .png (256 px).

    python tools/make_icon.py

A delta (the change) on a dark rounded tile, with a small square inside it (the blob). Drawn once
at 1024 px and scaled down, so every size is anti-aliased. Needs Pillow.
"""

import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "deltapatcher", "assets")
S = 1024
TILE = (29, 35, 48, 255)        # dark slate
DELTA = (25, 179, 160, 255)     # teal
BLOB = (242, 163, 58, 255)      # amber
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def draw():
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    pad = 40
    d.rounded_rectangle([pad, pad, S - pad, S - pad], radius=210, fill=TILE)
    # the delta: an outer triangle in teal, the inside cut back to the tile
    top, base, left, right = 205, 805, 175, S - 175
    d.polygon([(S // 2, top), (right, base), (left, base)], fill=DELTA)
    t = 88                                    # stroke width at 1024 px
    half = (right - left) / 2.0
    slope = half / float(base - top)          # half-width gained per pixel down
    # an inward offset of t moves the apex down by t / sin(half-angle) = t * sqrt(1 + 1/slope^2)
    inner_top = top + int(t * (1 + 1 / slope ** 2) ** 0.5)
    inner_base = base - t
    ih = (inner_base - inner_top) * slope
    d.polygon([(S // 2, inner_top), (S // 2 + ih, inner_base), (S // 2 - ih, inner_base)], fill=TILE)
    # the blob: a square standing on the inner base, clear of the sides
    b, gap = 120, 24
    y1 = inner_base - gap
    y0 = y1 - b
    assert (y0 - inner_top) * slope > b / 2 + gap, "the blob would touch the delta's sides"
    d.rounded_rectangle([S // 2 - b // 2, y0, S // 2 + b // 2, y1], radius=22, fill=BLOB)
    return im


def main():
    os.makedirs(OUT, exist_ok=True)
    big = draw()
    frames = [big.resize((n, n), Image.LANCZOS) for n in SIZES]
    frames[-1].save(os.path.join(OUT, "deltapatcher.ico"), format="ICO", sizes=[(n, n) for n in SIZES],
                    append_images=frames[:-1])
    big.resize((256, 256), Image.LANCZOS).save(os.path.join(OUT, "deltapatcher.png"), optimize=True)
    # the README's logo: large enough to stay sharp on high-DPI screens at the size it is shown
    docs_images = os.path.join(os.path.dirname(HERE), "docs", "images")
    os.makedirs(docs_images, exist_ok=True)
    big.resize((512, 512), Image.LANCZOS).save(os.path.join(docs_images, "logo.png"), optimize=True)
    print("wrote", os.path.join(OUT, "deltapatcher.ico"), ", deltapatcher.png and docs/images/logo.png")


if __name__ == "__main__":
    main()
