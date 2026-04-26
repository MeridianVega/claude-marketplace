#!/usr/bin/env python3
"""
render-logo.py — Channel logo + interstitial card renderer.

Ported from SeaDog Legacy's ChannelLogoGenerator + ChannelLogoStyles +
ChannelLogoClassifier (C# / SkiaSharp). Same preset table, same name-based
classification rules, Pillow-based rendering instead of Skia.

Usage:
  render-logo.py --name "Horror Movies" --bucket core --out logo.png
  render-logo.py --name "Horror Movies" --bucket core --card --out card.png
  render-logo.py --name "Action Movies" --genre action --out logo.png

The classifier picks a genre from the channel name first; if nothing
matches, it falls back to a bucket-flavored type (type_core, type_music,
type_live, type_rotating, type_experimental). --genre overrides the
classifier entirely.

Output:
  - Logo: ~1600x200 transparent PNG, tight-trimmed with stroke + fill.
  - Card: 1920x1080 stroke-color background + centered fill text (used as
          interstitial backdrop during music-only filler so the channel
          has branded visuals when the audio has no video track).

Fonts:
  Looks up font files in (in order):
    1. $ERSATZTV_LOGO_FONTS_DIR (if set)
    2. ./fonts/  (sibling of this script)
    3. /fonts                       (docker bind mount)
    4. /usr/share/fonts/ersatztv    (docker install)
    5. /usr/share/fonts/truetype/ersatztv
    6. /usr/share/fonts             (system fallback)
  Missing fonts fall back to Pillow's default sans — the logo still
  renders, just without the branded typography.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.stderr.write(
        "render-logo.py requires Pillow. Install with: pip install pillow\n"
    )
    sys.exit(2)

SCRIPT_DIR = Path(__file__).resolve().parent
PRESET_TABLE = SCRIPT_DIR / "fonts" / "preset-table.json"

FONT_SEARCH_PATHS = [
    os.environ.get("ERSATZTV_LOGO_FONTS_DIR"),
    str(SCRIPT_DIR / "fonts"),
    "/fonts",
    "/usr/share/fonts/ersatztv",
    "/usr/share/fonts/truetype/ersatztv",
    "/usr/share/fonts",
]


# ---------- classifier (port of ChannelLogoClassifier.cs) ----------

EXACT_MATCHES = {
    "friends": "friends",
    "ecw": "ecw",
    "wcw": "wcw",
    "on this day": "on_this_day",
    "late night": "late_night",
    "hbo": "hbo",
    "netflix": "netflix",
    "nickelodeon": "nickelodeon",
    "cartoon network": "cartoon_network",
    "a24": "a24",
}

# (substring needles, genre) — order matters; substrings can overlap.
PATTERN_RULES = [
    (("action movies", "action tv"), "action"),
    (("adventure movies", "adventure tv"), "adventure"),
    (("comedy movies", "comedy tv"), "comedy"),
    (("documentary movies", "documentary tv"), "documentary"),
    (("drama movies", "drama tv"), "drama"),
    (("family movies", "family tv"), "family"),
    (("horror movies", "horror tv"), "horror"),
    (("romance movies", "romance tv"), "romance"),
    (("scifi movies", "scifi tv"), "scifi"),
    (("western movies", "western tv"), "western"),
    (("animated movies", "animated tv"), "animated"),
    (("anime",), "anime"),
    (("adult animation",), "adult_animation"),
    (("weird science",), "scifi"),
    (("disney", "pixar"), "disney"),
    (("ghibli",), "ghibli"),
    (("nature",), "nature"),
    (("cooking",), "cooking"),
    (("classic movies",), "classic"),
    (("80s",), "retro_80s"),
    (("90s",), "retro_90s"),
    (("2000s",), "retro_2000s"),
    (("recent",), "contemporary"),
    (("christmas",), "christmas"),
    (("halloween",), "halloween"),
    (("thanksgiving",), "thanksgiving"),
    (("primetime",), "primetime"),
    (("background",), "ambient"),
]

BUCKET_FALLBACK = {
    "core": "type_core",
    "rotating": "type_rotating",
    "music": "type_music",
    "live": "type_live",
    "experimental": "type_experimental",
}


def classify(channel_name: str, bucket: str = "core") -> str:
    if not channel_name or not channel_name.strip():
        return "default"
    n = channel_name.lower()
    if n in EXACT_MATCHES:
        return EXACT_MATCHES[n]
    for needles, genre in PATTERN_RULES:
        if any(needle in n for needle in needles):
            return genre
    return BUCKET_FALLBACK.get(bucket, "type_core")


# ---------- preset resolution ----------

def load_presets() -> dict:
    with open(PRESET_TABLE, "r", encoding="utf-8") as f:
        return json.load(f)["presets"]


def resolve_style(genre: str, presets: dict) -> tuple:
    if genre in presets:
        return tuple(presets[genre])
    return tuple(presets["default"])


def resolve_font_path(font_file: str) -> Optional[str]:
    for base in FONT_SEARCH_PATHS:
        if not base:
            continue
        candidate = Path(base) / font_file
        if candidate.is_file():
            return str(candidate)
    return None


def load_typeface(font_file: str, point_size: float) -> ImageFont.FreeTypeFont:
    path = resolve_font_path(font_file)
    if path:
        try:
            return ImageFont.truetype(path, size=int(round(point_size)))
        except Exception:
            pass
    return ImageFont.load_default()


# ---------- rendering ----------

def hex_to_rgb(h: str) -> tuple:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def render_logo(channel_name: str, genre: str, presets: dict) -> Image.Image:
    """1600x200-ish transparent PNG, tight-trimmed."""
    font_file, fill_hex, stroke_hex, font_size = resolve_style(genre, presets)
    font = load_typeface(font_file, float(font_size))

    fill = hex_to_rgb(fill_hex) + (255,)
    stroke = hex_to_rgb(stroke_hex) + (255,)

    PADDING_X, PADDING_Y, STROKE_W = 12, 8, 3

    # Measure text on a throwaway draw context.
    sizing_img = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sizing_img)
    bbox = sd.textbbox((0, 0), channel_name, font=font, stroke_width=STROKE_W)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    width = max(1, text_w + PADDING_X * 2)
    height = max(1, text_h + PADDING_Y * 2)

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.text(
        (PADDING_X - bbox[0], PADDING_Y - bbox[1]),
        channel_name,
        font=font,
        fill=fill,
        stroke_width=STROKE_W,
        stroke_fill=stroke,
    )
    return img


def render_card(channel_name: str, genre: str, presets: dict) -> Image.Image:
    """1920x1080 backdrop card, stroke-color background + centered text."""
    font_file, fill_hex, stroke_hex, font_size = resolve_style(genre, presets)
    CARD_FONT_SCALE = 2.75
    font = load_typeface(font_file, float(font_size) * CARD_FONT_SCALE)

    fill = hex_to_rgb(fill_hex) + (255,)
    stroke = hex_to_rgb(stroke_hex) + (255,)

    W, H = 1920, 1080
    img = Image.new("RGB", (W, H), hex_to_rgb(stroke_hex))
    draw = ImageDraw.Draw(img)

    bbox = draw.textbbox((0, 0), channel_name, font=font, stroke_width=14)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (W - text_w) // 2 - bbox[0]
    y = (H - text_h) // 2 - bbox[1]

    draw.text(
        (x, y),
        channel_name,
        font=font,
        fill=fill,
        stroke_width=14,
        stroke_fill=(0, 0, 0, 180),
    )
    return img


# ---------- cli ----------

def load_config(path: str) -> dict:
    """Optional user-supplied config that augments classifier + presets.

    Schema:
        {
          "exact_matches": { "Channel Name": "preset_key", ... },
          "presets": {
            "preset_key": ["Font.ttf", "#FILL", "#STROKE", size],
            ...
          }
        }

    `exact_matches` keys match channel names case-insensitively.
    `presets` extends/overrides the built-in preset table.
    """
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    ap = argparse.ArgumentParser(description="Render an ErsatzTV channel logo or interstitial card.")
    ap.add_argument("--name", required=True, help="Channel name to render.")
    ap.add_argument("--bucket", default="core", choices=list(BUCKET_FALLBACK.keys()),
                    help="Channel bucket — used as fallback when name doesn't match a genre.")
    ap.add_argument("--genre", help="Override classifier; pick a preset key directly (e.g. 'horror', 'type_live').")
    ap.add_argument("--card", action="store_true", help="Render 1920x1080 interstitial card instead of a tight logo.")
    ap.add_argument("--out", required=True, help="Output PNG path.")
    ap.add_argument("--config", help="Optional JSON config with extra exact_matches + presets to override defaults.")
    ap.add_argument("--print-genre", action="store_true",
                    help="Print the resolved genre key and exit (no render).")
    args = ap.parse_args()

    presets = load_presets()

    # Merge user config if provided
    user_exact = {}
    if args.config:
        cfg = load_config(args.config)
        user_exact = {k.lower(): v for k, v in cfg.get("exact_matches", {}).items()}
        presets = {**presets, **cfg.get("presets", {})}

    if args.genre:
        genre = args.genre
    elif args.name and args.name.lower() in user_exact:
        genre = user_exact[args.name.lower()]
    else:
        genre = classify(args.name, args.bucket)

    if args.print_genre:
        print(genre)
        return 0

    img = render_card(args.name, genre, presets) if args.card else render_logo(args.name, genre, presets)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    print(f"{out_path}  genre={genre}  size={img.size[0]}x{img.size[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
