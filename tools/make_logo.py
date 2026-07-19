#!/usr/bin/env python3
"""Generate the myMeal brand marks (icon + wordmark) as PNGs.

Committed as the SOURCE OF TRUTH for the artwork so the logo can be regenerated
or tweaked without a design tool — the same approach used for HomeHoard. Run:

    python3 tools/make_logo.py

Design notes
------------
The mark is a flat isometric **bowl** with a gold four-point sparkle rising out
of it: the bowl says "meals", the sparkle says "AI". The sparkle is deliberately
the same device as the HomeHoard mark's gold star, so the two apps read as a
family; the hue is what separates them — HomeHoard is indigo, myMeal is the
warm terracotta (#e2542a) that is already the app's accent in
frontend/src/style.css. Everything is drawn at 4x and downsampled, which is what
gives the edges their antialiasing (PIL's polygon fill is hard-edged).

Only one accent colour appears (the gold), and only on the signature element —
per docs/design-system.md, scarcity is what makes it read.
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

SS = 4  # supersampling factor

# --- palette (mirrors frontend/src/style.css) ------------------------------
TILE_TOP = (240, 112, 63)      # warm terracotta, light end of the gradient
TILE_BOT = (178, 53, 15)       # deep burnt orange, dark end
BOWL_LIGHT = (247, 248, 251)   # rim / lit face
BOWL_BODY = (233, 236, 244)    # bowl body
BOWL_SHADE = (206, 212, 226)   # shaded right side, gives the form depth
BOWL_INNER = (219, 224, 236)   # the inside of the bowl, seen through the rim
GOLD = (242, 177, 52)          # the one accent
GOLD_DEEP = (216, 146, 27)     # second facet of the sparkle
WHITE = (255, 255, 255)

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _vertical_gradient(size: tuple[int, int]) -> Image.Image:
    """Top-to-bottom gradient, with a slight diagonal lean like HomeHoard's."""
    w, h = size
    grad = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(h - 1, 1)
        grad.putpixel((0, y), tuple(
            round(TILE_TOP[i] + (TILE_BOT[i] - TILE_TOP[i]) * t) for i in range(3)
        ))
    return grad.resize((w, h), Image.BICUBIC)


def _rounded_tile(size: tuple[int, int], radius: int) -> Image.Image:
    """The gradient rounded-square the mark sits on."""
    w, h = size
    tile = _vertical_gradient((w, h)).convert("RGBA")
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(tile, (0, 0), mask)
    return out


def _sparkle(d: ImageDraw.ImageDraw, cx: float, cy: float, rx: float, ry: float,
             waist: float = 0.30) -> None:
    """A four-point star, drawn as two facets so it reads as dimensional.

    `waist` controls how pinched the star is: lower = spikier.
    """
    wx, wy = rx * waist, ry * waist
    # Left facet (lighter) and right facet (deeper) meeting on the vertical axis.
    d.polygon([(cx, cy - ry), (cx + wx, cy - wy), (cx + rx, cy),
               (cx + wx, cy + wy), (cx, cy + ry)], fill=GOLD)
    d.polygon([(cx, cy - ry), (cx - wx, cy - wy), (cx - rx, cy),
               (cx - wx, cy + wy), (cx, cy + ry)], fill=GOLD_DEEP)


def _draw_mark(d: ImageDraw.ImageDraw, cx: float, cy: float, scale: float) -> None:
    """The bowl + sparkle, centred on (cx, cy). `scale` = 1.0 at a 512 tile."""
    s = scale
    rim_rx, rim_ry = 150 * s, 44 * s
    rim_y = cy + 42 * s          # rim sits below centre; sparkle occupies above
    depth = 150 * s              # how far the bowl body drops below the rim

    # Bowl body: the lower half of a circle, so the silhouette is a clean bowl.
    body_box = (cx - rim_rx, rim_y - depth, cx + rim_rx, rim_y + depth)
    # Fill with the SHADE, then lay a slightly smaller half-circle offset to the
    # left on top. What survives on the right is a crescent — which reads as a
    # curved surface turning away from the light. (A pieslice wedge here instead
    # reads as a crease folded into the bowl, not as shading.)
    d.pieslice(body_box, start=0, end=180, fill=BOWL_SHADE)
    lit_dx, lit_inset = -14 * s, 16 * s
    d.pieslice((cx - rim_rx + lit_dx + lit_inset, rim_y - depth + lit_inset,
                cx + rim_rx + lit_dx - lit_inset, rim_y + depth - lit_inset),
               start=0, end=180, fill=BOWL_BODY)

    # Rim: outer ellipse, then the visible interior inset within it.
    d.ellipse((cx - rim_rx, rim_y - rim_ry, cx + rim_rx, rim_y + rim_ry), fill=BOWL_LIGHT)
    inset_x, inset_y = 15 * s, 9 * s
    d.ellipse((cx - rim_rx + inset_x, rim_y - rim_ry + inset_y,
               cx + rim_rx - inset_x, rim_y + rim_ry - inset_y), fill=BOWL_INNER)

    # The sparkle hovering above the bowl — the signature element. It sits clear
    # of the rim rather than crossing it: overlapping made it read as a skewer
    # piercing the bowl instead of a sparkle floating over it.
    _sparkle(d, cx - 6 * s, rim_y - 132 * s, rx=56 * s, ry=88 * s)
    # A small companion sparkle, offset. This is the "cute" beat; keep it subtle
    # enough that it disappears gracefully at favicon sizes.
    _sparkle(d, cx + 96 * s, rim_y - 196 * s, rx=23 * s, ry=38 * s)


def make_icon(px: int) -> Image.Image:
    """Square app/add-on icon."""
    n = px * SS
    img = _rounded_tile((n, n), radius=round(n * 0.223))
    d = ImageDraw.Draw(img)
    _draw_mark(d, cx=n / 2, cy=n / 2, scale=(n / 512))
    return img.resize((px, px), Image.LANCZOS)


def make_logo(px_w: int, px_h: int) -> Image.Image:
    """Horizontal wordmark lockup: mark on the left, 'myMeal' on the right."""
    w, h = px_w * SS, px_h * SS
    img = _rounded_tile((w, h), radius=round(h * 0.10))
    d = ImageDraw.Draw(img)

    # 0.74 rather than filling the height: the mark's mass runs bottom (bowl) to
    # top (sparkle), so at larger scales it crowds both edges of the lockup.
    mark_scale = (h / 512) * 0.74
    mark_cx = w * 0.165
    _draw_mark(d, cx=mark_cx, cy=h / 2, scale=mark_scale)

    # Wordmark. Sized off the tile height so it scales with any output size.
    font = ImageFont.truetype(FONT_BOLD, size=round(h * 0.40))
    text = "myMeal"
    box = d.textbbox((0, 0), text, font=font)
    tx = mark_cx + 190 * mark_scale
    ty = h / 2 - (box[3] + box[1]) / 2
    d.text((tx, ty), text, font=font, fill=WHITE)

    return img.resize((px_w, px_h), Image.LANCZOS)


# path -> factory. Mirrors the HomeHoard asset set exactly.
TARGETS = {
    # Supervisor add-on (shown in the Add-on Store).
    "mymeal/icon.png": lambda: make_icon(512),
    "mymeal/logo.png": lambda: make_logo(1024, 340),
    # Staged for the home-assistant/brands PR (HA expects these exact sizes).
    "custom_components/mymeal/brand/icon.png": lambda: make_icon(256),
    "custom_components/mymeal/brand/icon@2x.png": lambda: make_icon(512),
    "custom_components/mymeal/brand/logo.png": lambda: make_logo(512, 170),
    "custom_components/mymeal/brand/logo@2x.png": lambda: make_logo(1024, 340),
    # Browser tab / PWA for the standalone SPA.
    "frontend/public/favicon.png": lambda: make_icon(64),
    "frontend/public/apple-touch-icon.png": lambda: make_icon(180),
}


def main() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel, factory in TARGETS.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        img = factory()
        img.save(path)
        print(f"  {rel:48} {img.size[0]}x{img.size[1]}")


if __name__ == "__main__":
    main()
