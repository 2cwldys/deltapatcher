"""The repository's social preview (the picture a GitHub link shows): docs/images/social-preview.png, 1280x640.

    python tools/make_preview.py

The logo and name on the left, the app's Update page (docs/images/app-update.png, from
tools/capture_screens.py) on the right. Upload it in GitHub: Settings > General > Social preview.
Needs Pillow; uses Segoe UI where it exists (Windows), else DejaVu Sans.
"""

import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGES = os.path.join(ROOT, "docs", "images")
W, H = 1280, 640
INK = (11, 15, 21)
INK2 = (19, 26, 36)
TEAL = (25, 179, 160)
TEAL_LIGHT = (63, 209, 189)
AMBER = (242, 163, 58)
WHITE = (240, 244, 248)
MUTED = (133, 147, 168)


def font(bold, size):
    names = (["segoeuib.ttf", "seguisb.ttf"] if bold else ["segoeui.ttf"]) + \
            (["DejaVuSans-Bold.ttf"] if bold else ["DejaVuSans.ttf"])
    dirs = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"), "/usr/share/fonts/truetype/dejavu",
            "/Library/Fonts", "/System/Library/Fonts/Supplemental"]
    for d in dirs:
        for n in names:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def background():
    bg = Image.new("RGB", (W, H), INK)
    px = bg.load()
    for y in range(H):
        for x in range(0, W):
            t = x / W
            px[x, y] = tuple(int(INK[i] + (INK2[i] - INK[i]) * t) for i in range(3))
    glow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(glow).ellipse([W - 620, -360, W + 360, 420], fill=70)
    glow = glow.filter(ImageFilter.GaussianBlur(140))
    teal = Image.new("RGB", (W, H), TEAL)
    bg = Image.composite(teal, bg, glow)
    # a faint grid of hash-like dots, the blobs
    d = ImageDraw.Draw(bg)
    for gx in range(24, 600, 36):
        for gy in range(24, H, 36):
            d.ellipse([gx - 1, gy - 1, gx + 1, gy + 1], fill=(30, 40, 54))
    return bg


def rounded(img, radius):
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.size[0] - 1, img.size[1] - 1], radius=radius, fill=255)
    out = Image.new("RGBA", img.size)
    out.paste(img, (0, 0), mask)
    return out


def chip(d, x, y, text, f, color):
    w = d.textlength(text, font=f)
    d.rounded_rectangle([x, y, x + w + 28, y + 34], radius=17, fill=(color[0] // 6 + 14, color[1] // 6 + 18, color[2] // 6 + 24),
                        outline=color, width=1)
    d.text((x + 14, y + 6), text, font=f, fill=color)
    return x + w + 28 + 10


def main():
    img = background().convert("RGBA")

    # the app, on the right, in a window frame with a shadow
    shot = Image.open(os.path.join(IMAGES, "app-update.png")).convert("RGB")
    sw = 700
    shot = shot.resize((sw, int(shot.size[1] * sw / shot.size[0])), Image.LANCZOS)
    frame_h = 30
    win = Image.new("RGB", (sw, shot.size[1] + frame_h), (15, 20, 28))
    wd = ImageDraw.Draw(win)
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        wd.ellipse([14 + i * 20, 10, 24 + i * 20, 20], fill=c)
    wd.text((sw // 2 - 50, 7), "DeltaPatcher", font=font(False, 14), fill=MUTED)
    win.paste(shot, (0, frame_h))
    win = rounded(win, 14)
    x0, y0 = W - sw + 70, 92
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([x0 + 10, y0 + 24, x0 + sw + 10, y0 + win.size[1] + 24], radius=18, fill=(0, 0, 0, 170))
    shadow = shadow.filter(ImageFilter.GaussianBlur(22))
    img = Image.alpha_composite(img, shadow)
    img.alpha_composite(win, (x0, y0))
    border = ImageDraw.Draw(img)
    border.rounded_rectangle([x0, y0, x0 + sw - 1, y0 + win.size[1] - 1], radius=14, outline=(45, 57, 75), width=1)

    # the name, on the left
    logo = Image.open(os.path.join(IMAGES, "logo.png")).convert("RGBA").resize((150, 150), Image.LANCZOS)
    img.alpha_composite(logo, (72, 96))
    d = ImageDraw.Draw(img)
    d.text((70, 268), "Delta Patcher", font=font(True, 66), fill=WHITE)
    d.text((74, 352), "Delta patching with SHA-256 blobs", font=font(False, 28), fill=TEAL_LIGHT)
    d.text((74, 394), "Any game. Any file. Only what changed.", font=font(False, 22), fill=MUTED)
    f = font(True, 16)
    x = 74
    x = chip(d, x, 462, "Signed releases", f, TEAL_LIGHT)
    x = chip(d, x, 462, "SFTP deploy", f, AMBER)
    chip(d, 74, 506, "Windows  /  Linux  /  macOS", f, (167, 139, 250))
    d.text((74, 580), "MIT licence  -  open source", font=font(False, 16), fill=(91, 106, 128))

    out = os.path.join(IMAGES, "social-preview.png")
    img.convert("RGB").save(out, optimize=True)
    print("wrote", out, img.size, os.path.getsize(out), "bytes")


if __name__ == "__main__":
    main()
