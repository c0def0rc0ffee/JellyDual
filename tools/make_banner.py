#!/usr/bin/env python3
"""
<summary>
Draws the README banner (docs/screenshots/banner.png, 1280 by 320) and the
GitHub social preview (docs/screenshots/social-preview.png, 1280 by 640) for
Jellyfin Dual Play.
</summary>
<remarks>
Both images share one look: a flat deep navy background, the add-on name in
bold near white on the left with a muted blue grey tagline under it, and
three rounded tiles on the right, each drawn in one accent colour from what
the add-on actually does. Teal: two TV outlines linked by an arrow. Amber:
the device name chips a Sync Group shows (Living Room, Bedroom). Soft blue:
play and pause glyphs over a progress bar.

Everything is drawn here from shapes and text; no outside artwork is used.
Each image is drawn at twice its size and scaled down, which smooths the
edges of the shapes.

Needs Pillow and a bold TrueType font (Noto Sans Bold, else DejaVu Sans
Bold). It is a development tool only: it lives outside the add-on folder,
so it never reaches the Dist zip, and Kodi never runs it.

Usage:
    python3 tools/make_banner.py              write both images if absent
    python3 tools/make_banner.py --overwrite  replace existing images

An existing image is never replaced without --overwrite, so a hand tuned
image cannot be lost by accident.
</remarks>
"""

import argparse
import os
import sys

from PIL import Image, ImageDraw, ImageFont

# Supersampling factor: draw this many times larger, then scale down.
SCALE = 2

BACKGROUND = (13, 22, 43)        # #0d162b, deep navy, flat
NAME_COLOUR = (240, 244, 250)    # #f0f4fa, near white
TAGLINE_COLOUR = (148, 163, 184) # #94a3b8, muted blue grey
PANEL = (8, 14, 30)              # darker than the background
PANEL_EDGE = (30, 42, 68)        # faint outline so the panel reads
TEAL = (45, 212, 191)            # #2dd4bf
AMBER = (245, 158, 11)           # #f59e0b
SOFT_BLUE = (96, 165, 250)       # #60a5fa

NAME = 'Jellyfin Dual Play'
TAGLINE = 'The same film on every Kodi box, in step.'

FONT_CANDIDATES = (
    '/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
)


def bold_font_path():
    """
    <summary>Finds the first bold TrueType font from <see cref="FONT_CANDIDATES"/>.</summary>
    <returns>The font file path.</returns>
    <exception cref="SystemExit">When none of the candidate fonts is installed.</exception>
    """
    for path in FONT_CANDIDATES:
        if os.path.isfile(path):
            return path
    sys.exit('No bold font found. Install fonts-noto-core or fonts-dejavu-core.')


def fit_font(path, text, max_width, start_size):
    """
    <summary>Picks the largest font size, at or below start_size, at which text fits max_width.</summary>
    <param name="path">TrueType font file.</param>
    <param name="text">The text to measure.</param>
    <param name="max_width">Available width in drawing pixels (already scaled).</param>
    <param name="start_size">The size to try first, in drawing pixels.</param>
    <returns>A loaded ImageFont at the chosen size.</returns>
    """
    size = start_size
    while size > 8:
        font = ImageFont.truetype(path, size)
        if font.getlength(text) <= max_width:
            return font
        size -= 2
    return ImageFont.truetype(path, size)


def tile_tvs(draw, x, y, s):
    """
    <summary>Teal tile: two TV outlines side by side, linked by a two way arrow.</summary>
    <param name="draw">The ImageDraw to paint on.</param>
    <param name="x">Left edge of the tile, drawing pixels.</param>
    <param name="y">Top edge of the tile, drawing pixels.</param>
    <param name="s">Tile side length, drawing pixels.</param>
    <remarks>Every measurement is a fraction of s so the tile scales cleanly.</remarks>
    """
    line = max(2, int(s * 0.03))
    tv_w, tv_h = s * 0.30, s * 0.22
    top = y + s * 0.30
    for left in (x + s * 0.10, x + s * 0.60):
        draw.rounded_rectangle([left, top, left + tv_w, top + tv_h],
                               radius=s * 0.03, outline=TEAL, width=line)
        stand_x = left + tv_w / 2
        draw.line([stand_x, top + tv_h, stand_x, top + tv_h + s * 0.06], fill=TEAL, width=line)
        draw.line([stand_x - s * 0.07, top + tv_h + s * 0.06, stand_x + s * 0.07, top + tv_h + s * 0.06],
                  fill=TEAL, width=line)
    # The link: a shaft between the screens with a head at each end.
    mid_y = top + tv_h / 2
    a, b = x + s * 0.43, x + s * 0.57
    draw.line([a, mid_y, b, mid_y], fill=TEAL, width=line)
    head = s * 0.035
    draw.polygon([(a - head * 0.4, mid_y), (a + head, mid_y - head), (a + head, mid_y + head)], fill=TEAL)
    draw.polygon([(b + head * 0.4, mid_y), (b - head, mid_y - head), (b - head, mid_y + head)], fill=TEAL)
    # A matching play mark on each screen says both show the same thing.
    for left in (x + s * 0.10, x + s * 0.60):
        cx, cy, r = left + tv_w / 2, top + tv_h / 2, s * 0.045
        draw.polygon([(cx - r * 0.7, cy - r), (cx - r * 0.7, cy + r), (cx + r, cy)], fill=TEAL)


def tile_chips(draw, x, y, s, font_path):
    """
    <summary>Amber tile: two device name chips, as a Sync Group names its boxes.</summary>
    <param name="draw">The ImageDraw to paint on.</param>
    <param name="x">Left edge of the tile, drawing pixels.</param>
    <param name="y">Top edge of the tile, drawing pixels.</param>
    <param name="s">Tile side length, drawing pixels.</param>
    <param name="font_path">Bold font used for the chip labels.</param>
    <remarks>The names are made up room names, never a real device.</remarks>
    """
    labels = ('Living Room', 'Bedroom')
    chip_w, chip_h = s * 0.76, s * 0.20
    font = fit_font(font_path, labels[0], chip_w - s * 0.26, int(s * 0.10))
    for i, label in enumerate(labels):
        left = x + (s - chip_w) / 2
        top = y + s * 0.25 + i * (chip_h + s * 0.10)
        draw.rounded_rectangle([left, top, left + chip_w, top + chip_h],
                               radius=chip_h / 2, outline=AMBER, width=max(2, int(s * 0.025)))
        dot = s * 0.035
        cy = top + chip_h / 2
        draw.ellipse([left + s * 0.07, cy - dot, left + s * 0.07 + dot * 2, cy + dot], fill=AMBER)
        draw.text((left + s * 0.17, cy), label, font=font, fill=AMBER, anchor='lm')


def tile_playback(draw, x, y, s):
    """
    <summary>Soft blue tile: a play glyph and a pause glyph over a progress bar.</summary>
    <param name="draw">The ImageDraw to paint on.</param>
    <param name="x">Left edge of the tile, drawing pixels.</param>
    <param name="y">Top edge of the tile, drawing pixels.</param>
    <param name="s">Tile side length, drawing pixels.</param>
    """
    cy = y + s * 0.40
    r = s * 0.12
    px = x + s * 0.30
    draw.polygon([(px - r * 0.8, cy - r), (px - r * 0.8, cy + r), (px + r, cy)], fill=SOFT_BLUE)
    bar_w = s * 0.07
    qx = x + s * 0.60
    for offset in (0, bar_w * 1.8):
        draw.rounded_rectangle([qx + offset, cy - r, qx + offset + bar_w, cy + r],
                               radius=bar_w * 0.25, fill=SOFT_BLUE)
    # Progress bar: a dim track, a filled part and a round playhead.
    track_y = y + s * 0.72
    left, right = x + s * 0.14, x + s * 0.86
    thick = s * 0.035
    dim = tuple(int(c * 0.35 + b * 0.65) for c, b in zip(SOFT_BLUE, PANEL))
    draw.rounded_rectangle([left, track_y - thick / 2, right, track_y + thick / 2], radius=thick / 2, fill=dim)
    head = left + (right - left) * 0.62
    draw.rounded_rectangle([left, track_y - thick / 2, head, track_y + thick / 2], radius=thick / 2, fill=SOFT_BLUE)
    knob = s * 0.045
    draw.ellipse([head - knob, track_y - knob, head + knob, track_y + knob], fill=SOFT_BLUE)


def render(width, height, tile, name_lines, name_size, tagline_size, out_path):
    """
    <summary>Draws one image in the house style and saves it as an optimised PNG.</summary>
    <param name="width">Final width in pixels.</param>
    <param name="height">Final height in pixels.</param>
    <param name="tile">Final tile side length in pixels.</param>
    <param name="name_lines">The name split into the lines to draw, one string per line.</param>
    <param name="name_size">Largest name font size to try, final pixels.</param>
    <param name="tagline_size">Tagline font size, final pixels.</param>
    <param name="out_path">Where to write the PNG.</param>
    <remarks>
    The tiles sit right aligned, 64 pixels in from the right edge, and the text
    block is vertically centred 80 pixels in from the left. The name shrinks
    to fit the gap between the left margin and the tiles.
    </remarks>
    """
    k = SCALE
    w, h = width * k, height * k
    image = Image.new('RGB', (w, h), BACKGROUND)
    draw = ImageDraw.Draw(image)
    font_path = bold_font_path()

    gap, right_margin, left_margin = 22 * k, 64 * k, 80 * k
    s = tile * k
    tiles_left = w - right_margin - 3 * s - 2 * gap
    tiles_top = (h - s) / 2
    for i, painter in enumerate((tile_tvs, tile_chips, tile_playback)):
        tx = tiles_left + i * (s + gap)
        draw.rounded_rectangle([tx, tiles_top, tx + s, tiles_top + s], radius=s * 0.12,
                               fill=PANEL, outline=PANEL_EDGE, width=max(1, k))
        if painter is tile_chips:
            painter(draw, tx, tiles_top, s, font_path)
        else:
            painter(draw, tx, tiles_top, s)

    text_width = tiles_left - left_margin - 36 * k
    longest = max(name_lines, key=len)
    name_font = fit_font(font_path, longest, text_width, name_size * k)
    tag_font = fit_font(font_path.replace('Bold', 'Regular'), TAGLINE, text_width, tagline_size * k) \
        if os.path.isfile(font_path.replace('Bold', 'Regular')) \
        else fit_font(font_path, TAGLINE, text_width, tagline_size * k)

    line_h = name_font.size * 1.12
    tag_gap = name_font.size * 0.30
    block_h = line_h * len(name_lines) + tag_gap + tag_font.size
    top = (h - block_h) / 2
    for i, text in enumerate(name_lines):
        draw.text((left_margin, top + i * line_h), text, font=name_font, fill=NAME_COLOUR, anchor='lt')
    draw.text((left_margin, top + line_h * len(name_lines) + tag_gap), TAGLINE,
              font=tag_font, fill=TAGLINE_COLOUR, anchor='lt')

    final = image.resize((width, height), Image.LANCZOS)
    # A flat palette keeps the file small; 64 colours is ample for flat shapes and smooth edges.
    final = final.quantize(colors=64, method=Image.MEDIANCUT, dither=Image.NONE)
    final.save(out_path, optimize=True)
    print('wrote {} ({} KB)'.format(out_path, os.path.getsize(out_path) // 1024))


def main():
    """
    <summary>Parses the arguments and writes the banner and the social preview.</summary>
    <returns>0 when both images were written or already present, 1 when one was skipped.</returns>
    """
    parser = argparse.ArgumentParser(description='Draw the README banner and social preview.')
    parser.add_argument('--overwrite', action='store_true', help='replace images that already exist')
    args = parser.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(root, 'docs', 'screenshots')
    os.makedirs(out_dir, exist_ok=True)

    jobs = (
        ('banner.png', dict(width=1280, height=320, tile=176, name_lines=[NAME],
                            name_size=64, tagline_size=24)),
        ('social-preview.png', dict(width=1280, height=640, tile=184,
                                    name_lines=['Jellyfin', 'Dual Play'],
                                    name_size=104, tagline_size=26)),
    )
    skipped = 0
    for name, spec in jobs:
        path = os.path.join(out_dir, name)
        if os.path.exists(path) and not args.overwrite:
            print('kept {} (exists; pass --overwrite to replace it)'.format(path))
            skipped += 1
            continue
        render(out_path=path, **spec)
    return 1 if skipped else 0


if __name__ == '__main__':
    sys.exit(main())
