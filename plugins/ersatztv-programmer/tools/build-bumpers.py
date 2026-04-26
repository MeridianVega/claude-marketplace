#!/usr/bin/env python3
"""
build-bumpers.py — Voice-driven, Adult-Swim-style bumper cards.

For each channel listed in tools/bumper-voices.json, walks the day's playout,
finds top-of-hour transitions during primetime, and renders one of three
bumper types as a 15-20s branded MP4:

  - personality (~60%) — a single deadpan voice line. Channel-feel only.
  - up-next     (~30%) — functional "next at 9 / Show Title" using the
                          channel's templated phrasing.
  - block-summary (~10%) — Friday-Night-Lineup style. Renders once at the
                          first primetime hour of the channel; lists the
                          night's upcoming shows under the channel's
                          block_summary_intro line.

All cards have a music bed at 25% volume drawn from the channel's
brand-matched genre pool in Jellyfin.

Cards are written to bumpers/{YYYY-MM-DD}/{channel}/{HHMM}-{kind}.mp4 and
are intended to be spliced into the channel's playout JSON as a `local`
source replacing the trailing seconds of music filler before the on-the-
hour program. Re-running this overwrites prior renders for the day.

Usage:
    build-bumpers.py [--date YYYY-MM-DD] [--only-channel N] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import random
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

STACK_DIR = Path(os.environ.get("STACK_DIR", str(Path.home() / "ersatztv-stack")))
JF_DB = Path(
    os.environ.get(
        "JF_DB",
        str(Path.home() / "Library/Application Support/jellyfin/data/jellyfin.db"),
    )
)
FONTS_DIR = Path(
    os.environ.get(
        "FONTS_DIR",
        str(
            Path.home()
            / ".claude/plugins/marketplaces/meridianvega/plugins/ersatztv-programmer/tools/fonts"
        ),
    )
)

LINEUP = STACK_DIR / "config/ersatztv-next/lineup.json"
CHANNELS_DIR = STACK_DIR / "config/ersatztv-next/channels"
CHANNEL_FONTS = STACK_DIR / "tools/channel-fonts.json"
VOICES_FILE = STACK_DIR / "tools/bumper-voices.json"
BUMPERS_ROOT = STACK_DIR / "bumpers"
FFMPEG = "/usr/local/bin/ffmpeg"
FFPROBE = "/usr/local/bin/ffprobe"

# Per-channel music genre filter (case-insensitive substring against Genres pipe-list).
CHANNEL_MUSIC_GENRE = {
    "Background":          ["Easy Listening", "Soft Rock", "Soundtrack"],
    "Friends":             ["Alternative", "Pop", "Rock"],
    "Adult Animation":     ["Alternative", "Punk", "Hip Hop", "Electronic"],
    "Saturday Morning":    ["Pop", "Soundtrack"],
    "Action":              ["Rock", "Hip Hop", "Electronic"],
    "Adventure":           ["Soundtrack", "Rock"],
    "Comedy":              ["Pop", "Alternative"],
    "Drama":               ["Soundtrack", "Classical", "Ambient"],
    "Family":              ["Pop", "Easy Listening"],
    "Horror":              ["Metal", "Industrial", "Soundtrack"],
    "Romance":             ["R&B", "Soul", "Pop"],
    "Scifi":               ["Electronic", "Ambient"],
    "Western":             ["Country", "Folk"],
    "Documentary":         ["Classical", "Ambient", "Soundtrack"],
    "Anime":               ["J-Pop", "Anime", "Soundtrack"],
    "Animated":            ["Pop", "Soundtrack"],
    "Cooking TV":          ["Jazz", "Lounge", "Bossa Nova"],
    "Nature":              ["Ambient", "Classical"],
    "Disney & Pixar":      ["Soundtrack"],
    "Ghibli":              ["Soundtrack", "Classical"],
    "Nickelodeon":         ["Pop", "Alternative"],
    "Cartoon Network":     ["Electronic", "Alternative"],
    "HBO Style":           ["Soundtrack", "Classical"],
    "The Vault":           ["Classical", "Jazz"],
    "Classic Cinema":      ["Classical", "Jazz"],
    "80s Rewind":          ["Pop", "Rock", "New Wave"],
    "90s Throwback":       ["Alternative", "Hip Hop"],
    "2000s Replay":        ["Pop", "Punk", "Hip Hop"],
    "Now Showing":         ["Pop", "Hip Hop"],
    "24/7 Wrestling":      ["Rock", "Hip Hop", "Metal"],
}

PRIMETIME_HOURS = {19, 20, 21, 22}


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def load_channel_brand() -> dict[str, dict]:
    """Returns {channel_name: {"font_file": "Path.ttf", "fill": "#hex", "stroke": "#hex"}}."""
    if not CHANNEL_FONTS.is_file():
        return {}
    with open(CHANNEL_FONTS) as f:
        cfg = json.load(f)
    presets = cfg.get("presets", {})
    out = {}
    for name, key in cfg.get("exact_matches", {}).items():
        if key not in presets:
            continue
        font_file, fill, stroke, _size = presets[key]
        out[name] = {"font_file": font_file, "fill": fill, "stroke": stroke}
    return out


def load_voices() -> dict:
    if not VOICES_FILE.is_file():
        return {"_mix": {"deadpan_weight": 60, "up_next_weight": 30, "block_summary_weight": 10},
                "channels": {}}
    with open(VOICES_FILE) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Library helpers
# ---------------------------------------------------------------------------

def pick_music_track(conn: sqlite3.Connection, channel_name: str) -> tuple[str, float] | None:
    """Random song from the channel's branded genre pool."""
    genres = CHANNEL_MUSIC_GENRE.get(channel_name, [])
    if not genres:
        cur = conn.execute(
            """
            SELECT Path, RuntimeTicks/10000000.0
              FROM BaseItems
             WHERE Type='MediaBrowser.Controller.Entities.Audio.Audio'
               AND RuntimeTicks BETWEEN 900000000 AND 3000000000
             ORDER BY random() LIMIT 1
            """
        )
        row = cur.fetchone()
        return (row[0], row[1]) if row else None

    likes = " OR ".join("Genres LIKE ?" for _ in genres)
    params = [f"%{g}%" for g in genres]
    cur = conn.execute(
        f"""
        SELECT Path, RuntimeTicks/10000000.0
          FROM BaseItems
         WHERE Type='MediaBrowser.Controller.Entities.Audio.Audio'
           AND RuntimeTicks BETWEEN 900000000 AND 3000000000
           AND ({likes})
         ORDER BY random() LIMIT 1
        """,
        params,
    )
    row = cur.fetchone()
    if row:
        return (row[0], row[1])
    return pick_music_track(conn, "")


def resolve_show_title(conn: sqlite3.Connection, source_path: str) -> tuple[str, str]:
    """Return (line1, line2). Episodes: ("Series", "S01E04 — Title"). Movies: ("Title", "Year")."""
    cur = conn.execute(
        """
        SELECT Name, Type, SeriesName, SeasonName, IndexNumber, ProductionYear
          FROM BaseItems WHERE Path = ? LIMIT 1
        """,
        (source_path,),
    )
    row = cur.fetchone()
    if not row:
        return Path(source_path).stem, ""
    name, type_, series, season, idx, year = row
    if type_ == "MediaBrowser.Controller.Entities.TV.Episode":
        line1 = series or name
        line2 = name or ""
        if season and idx:
            try:
                s = int(str(season).split()[-1])
                e = int(idx)
                line2 = f"S{s:02d}E{e:02d} — {line2}" if line2 else f"S{s:02d}E{e:02d}"
            except (ValueError, IndexError):
                pass
        return line1, line2
    if type_ == "MediaBrowser.Controller.Entities.Movies.Movie":
        return name, str(year) if year else ""
    return name, ""


# ---------------------------------------------------------------------------
# ffmpeg drawtext
# ---------------------------------------------------------------------------

def hex_to_ffmpeg(hex_color: str) -> str:
    return hex_color.replace("#", "0x")


def esc(s: str) -> str:
    """Escape text for ffmpeg drawtext."""
    return (
        s.replace("\\", "\\\\")
         .replace(":", "\\:")
         .replace("'", "\\'")
         .replace("%", "\\%")
         .replace(",", "\\,")
    )


def font_path_for(brand: dict) -> Path:
    p = FONTS_DIR / brand["font_file"]
    if not p.is_file():
        return FONTS_DIR / "Tomorrow-Bold.ttf"
    return p


@dataclass
class RenderArgs:
    out_path: Path
    channel_name: str
    channel_num: str
    brand: dict
    music_path: str
    duration_s: float


LOGOS_DIR = STACK_DIR / "config/ersatztv-next/logos"


def gradient_input(stroke_hex: str, duration_s: float) -> list[str]:
    """Build a two-tone vertical gradient input. Top = stroke, bottom = near-black."""
    bg_top = hex_to_ffmpeg(stroke_hex)
    return [
        "-f", "lavfi", "-i",
        f"gradients=size=1920x1080:c0={bg_top}:c1=0x0A0A0A:n=2:type=linear:duration={duration_s}:rate=30:speed=0",
    ]


def vignette_chain(prefix: str, suffix: str) -> str:
    """Apply a soft vignette to make the card feel cinematic."""
    return f"[{prefix}]vignette=PI/4:x0=w/2:y0=h/2[{suffix}]"


def _run_ffmpeg(cmd: list[str], label: str) -> bool:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ffmpeg failed for {label}: {result.stderr[:300]}", file=sys.stderr)
        return False
    return True


def _build_text_chain(
    fp: Path,
    fg: str,
    *,
    channel_mark: str,
    primary_text: str,      # the big focal text
    primary_size: int,
    primary_y: int,
    secondary_text: str = "",
    secondary_size: int = 0,
    secondary_y: int = 0,
    tertiary_text: str = "",
    tertiary_size: int = 0,
    tertiary_y: int = 0,
    fade_start: float = 0.0,
    fade_dur: float = 0.6,
) -> str:
    """Build a drawtext chain with channel mark + 1-3 stacked text layers, all fading in."""
    e_chan = esc(channel_mark)
    fade_alpha = f"if(lt(t,{fade_start}),0,if(lt(t,{fade_start+fade_dur}),(t-{fade_start})/{fade_dur},1))"
    parts = [
        # Channel mark — small, top, with subtle backing
        f"drawtext=fontfile='{fp}':text='{e_chan}':"
        f"fontsize=28:fontcolor={fg}@0.85:x=(w-text_w)/2:y=64:"
        f"box=1:boxcolor=black@0.35:boxborderw=14"
    ]
    if primary_text:
        e_p = esc(primary_text)
        parts.append(
            f"drawtext=fontfile='{fp}':text='{e_p}':"
            f"fontsize={primary_size}:fontcolor={fg}:x=(w-text_w)/2:y={primary_y}:"
            f"alpha='{fade_alpha}'"
        )
    if secondary_text:
        e_s = esc(secondary_text)
        parts.append(
            f"drawtext=fontfile='{fp}':text='{e_s}':"
            f"fontsize={secondary_size}:fontcolor={fg}@0.78:x=(w-text_w)/2:y={secondary_y}:"
            f"alpha='{fade_alpha}'"
        )
    if tertiary_text:
        e_t = esc(tertiary_text)
        parts.append(
            f"drawtext=fontfile='{fp}':text='{e_t}':"
            f"fontsize={tertiary_size}:fontcolor={fg}@0.65:x=(w-text_w)/2:y={tertiary_y}:"
            f"alpha='{fade_alpha}'"
        )
    return ",".join(parts)


def _has_logo(channel_num: str) -> bool:
    p = LOGOS_DIR / f"{channel_num}.png"
    return p.is_file() and p.stat().st_size > 1000


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _vertical_gradient(w: int, h: int, top_rgb, bot_rgb):
    """Create a vertical RGB gradient image."""
    from PIL import Image
    img = Image.new("RGB", (w, h), top_rgb)
    px = img.load()
    for y in range(h):
        t = y / (h - 1)
        r = int(top_rgb[0] * (1 - t) + bot_rgb[0] * t)
        g = int(top_rgb[1] * (1 - t) + bot_rgb[1] * t)
        b = int(top_rgb[2] * (1 - t) + bot_rgb[2] * t)
        for x in range(w):
            px[x, y] = (r, g, b)
    return img


def _vertical_gradient_fast(w: int, h: int, top_rgb, bot_rgb):
    """Faster gradient via numpy if available, else fall back to per-row."""
    try:
        import numpy as np
        from PIL import Image
        t = np.linspace(0, 1, h)
        r = (top_rgb[0] * (1 - t) + bot_rgb[0] * t).astype(np.uint8)
        g = (top_rgb[1] * (1 - t) + bot_rgb[1] * t).astype(np.uint8)
        b = (top_rgb[2] * (1 - t) + bot_rgb[2] * t).astype(np.uint8)
        column = np.stack([r, g, b], axis=-1)  # (h, 3)
        arr = np.broadcast_to(column[:, None, :], (h, w, 3)).copy()
        return Image.fromarray(arr, "RGB")
    except ImportError:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (w, h), top_rgb)
        d = ImageDraw.Draw(img)
        for y in range(h):
            t = y / (h - 1)
            r = int(top_rgb[0] * (1 - t) + bot_rgb[0] * t)
            g = int(top_rgb[1] * (1 - t) + bot_rgb[1] * t)
            b = int(top_rgb[2] * (1 - t) + bot_rgb[2] * t)
            d.line([(0, y), (w, y)], fill=(r, g, b))
        return img


def _apply_vignette(img):
    """Apply a soft radial vignette by darkening edges."""
    try:
        import numpy as np
        from PIL import Image
        w, h = img.size
        cx, cy = w / 2, h / 2
        max_r = (cx ** 2 + cy ** 2) ** 0.5
        y, x = np.ogrid[:h, :w]
        r = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
        # Vignette factor: 1.0 at center, 0.55 at corners
        v = 1.0 - 0.45 * (r / max_r) ** 2
        v = np.clip(v, 0.55, 1.0)
        arr = np.array(img).astype(np.float32)
        arr *= v[:, :, None]
        return Image.fromarray(arr.clip(0, 255).astype("uint8"), "RGB")
    except ImportError:
        return img  # skip vignette if numpy missing


def _draw_text_centered(draw, text, font, fill, y, w):
    """Draw centered text at y with given font."""
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (w - tw) // 2
    draw.text((x - bbox[0], y - bbox[1]), text, font=font, fill=fill)
    return th


def _draw_text_with_box(draw, text, font, fill, y, w, pad=14):
    """Draw centered text with a semi-transparent black box behind it."""
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (w - tw) // 2
    # Box
    draw.rectangle(
        [x - pad - bbox[0], y - pad - bbox[1], x + tw + pad - bbox[0], y + th + pad - bbox[1]],
        fill=(0, 0, 0, 90),
    )
    draw.text((x - bbox[0], y - bbox[1]), text, font=font, fill=fill)


@dataclass
class TextLayer:
    text: str
    size: int
    y: int
    opacity: float = 1.0
    boxed: bool = False


def _render_card_png(
    out_png: Path,
    brand: dict,
    channel_num: str,
    channel_mark: str,
    text_layers: list,
    logo_y: int = 140,
    logo_w: int = 280,
) -> bool:
    """Compose the card image entirely in PIL — gradient + vignette + logo + text. ~50-100x faster than ffmpeg drawtext."""
    from PIL import Image, ImageDraw, ImageFont
    W, H = 1920, 1080
    fp = font_path_for(brand)

    # Background gradient
    bg_top = _hex_to_rgb(brand["stroke"])
    bg_bot = (10, 10, 10)
    img = _vertical_gradient_fast(W, H, bg_top, bg_bot)
    img = _apply_vignette(img)

    # Logo
    if _has_logo(channel_num):
        try:
            logo = Image.open(LOGOS_DIR / f"{channel_num}.png").convert("RGBA")
            ratio = logo_w / logo.size[0]
            logo = logo.resize((logo_w, int(logo.size[1] * ratio)), Image.LANCZOS)
            lx = (W - logo_w) // 2
            img.paste(logo, (lx, logo_y), logo)
        except (OSError, ValueError):
            pass

    # Channel mark (small with semi-transparent black box behind)
    img = img.convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    try:
        small_font = ImageFont.truetype(str(fp), 28)
    except OSError:
        small_font = ImageFont.load_default()
    bbox = od.textbbox((0, 0), channel_mark, font=small_font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    cm_x = (W - tw) // 2
    cm_y = 64
    od.rounded_rectangle(
        [cm_x - 14 - bbox[0], cm_y - 8 - bbox[1], cm_x + tw + 14 - bbox[0], cm_y + th + 8 - bbox[1]],
        radius=6, fill=(0, 0, 0, 90),
    )
    od.text((cm_x - bbox[0], cm_y - bbox[1]), channel_mark, font=small_font, fill=brand["fill"])
    img = Image.alpha_composite(img, overlay)

    # Text layers
    img = img.convert("RGBA")
    text_overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    td = ImageDraw.Draw(text_overlay)
    fg_rgb = _hex_to_rgb(brand["fill"])
    for layer in text_layers:
        try:
            f = ImageFont.truetype(str(fp), layer.size)
        except OSError:
            f = ImageFont.load_default()
        alpha = int(255 * max(0.0, min(1.0, layer.opacity)))
        fill = fg_rgb + (alpha,)
        bbox = td.textbbox((0, 0), layer.text, font=f)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x = (W - tw) // 2
        td.text((x - bbox[0], layer.y - bbox[1]), layer.text, font=f, fill=fill)
    img = Image.alpha_composite(img, text_overlay).convert("RGB")
    img.save(out_png, "PNG", optimize=False, compress_level=1)
    return True


def _render(a: RenderArgs, text_chain: str, logo_y: int = 140, logo_w: int = 280) -> bool:
    """Legacy fallback (ffmpeg drawtext) — only used if PIL composition fails. Kept for safety."""
    raise NotImplementedError("Use _render_with_layers instead")


def _render_with_layers(
    a: RenderArgs,
    text_layers: list,
    logo_y: int = 140,
    logo_w: int = 280,
) -> bool:
    """Render card via PIL → static PNG → MP4 with audio. ~10-30x faster than the ffmpeg drawtext approach."""
    a.out_path.parent.mkdir(parents=True, exist_ok=True)
    png = a.out_path.with_suffix(".png")
    if not _render_card_png(
        png, a.brand, a.channel_num, a.channel_name.upper(), text_layers, logo_y, logo_w,
    ):
        return False
    mp4_cmd = [
        FFMPEG, "-y", "-loglevel", "error",
        "-loop", "1", "-framerate", "1", "-i", str(png),
        "-i", str(a.music_path),
        "-filter_complex",
        f"[1:a]volume=0.22,afade=t=in:st=0:d=1.0,afade=t=out:st={a.duration_s-1.5}:d=1.5[a]",
        "-map", "0:v:0", "-map", "[a]",
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
        "-crf", "26", "-pix_fmt", "yuv420p",
        "-r", "1",
        "-c:a", "aac", "-b:a", "96k",
        "-t", f"{a.duration_s}",
        "-shortest",
        "-movflags", "+faststart",
        str(a.out_path),
    ]
    ok = _run_ffmpeg(mp4_cmd, f"{a.out_path.name}")
    try:
        png.unlink()
    except OSError:
        pass
    return ok


def render_up_next(
    a: RenderArgs,
    show_line1: str,
    show_line2: str,
    when_text: str,
) -> bool:
    """UP NEXT card via PIL: gradient + logo + show title + secondary + time."""
    layers = [
        TextLayer(show_line1[:36], 82, 520, opacity=1.0),
    ]
    if show_line2:
        layers.append(TextLayer(show_line2[:48], 38, 625, opacity=0.78))
    if when_text:
        layers.append(TextLayer(when_text, 46, 720, opacity=1.0))
    return _render_with_layers(a, layers, logo_y=140, logo_w=280)


def render_personality(a: RenderArgs, deadpan_line: str) -> bool:
    """Voice card via PIL: gradient + logo + 1-3 stacked rows of deadpan text."""
    # Wrap at ~26 chars max 3 rows
    words = deadpan_line.split()
    rows: list[str] = []
    current = ""
    for w in words:
        if len(current) + 1 + len(w) > 26 and current:
            rows.append(current); current = w
        else:
            current = (current + " " + w).strip()
    if current: rows.append(current)
    rows = rows[:3]
    n = len(rows)
    base_y = 580 - (n * 54)
    layers = [TextLayer(row, 78, base_y + i * 100, opacity=1.0) for i, row in enumerate(rows)]
    return _render_with_layers(a, layers, logo_y=180, logo_w=240)


def render_block_summary(
    a: RenderArgs,
    intro: str,
    lineup: list[tuple[str, str]],  # [(time_label, title)]
) -> bool:
    """Friday-Night-Lineup card via PIL: gradient + logo + intro + lineup rows."""
    intro_rows = intro.split("\n")[:2]
    layers = []
    for i, row in enumerate(intro_rows):
        layers.append(TextLayer(row, 62, 210 + i * 72, opacity=1.0))
    block_top = 210 + len(intro_rows) * 72 + 50
    for i, (when, title) in enumerate(lineup[:4]):
        text = f"{when}   {title[:32]}"
        layers.append(TextLayer(text, 46, block_top + i * 96, opacity=0.95))
    return _render_with_layers(a, layers, logo_y=128, logo_w=200)


# ---------------------------------------------------------------------------
# Per-channel build
# ---------------------------------------------------------------------------

def find_clean_clock_targets(items: list[dict]) -> list[tuple[int, datetime]]:
    """Return [(item_index, target_start)] — items whose start lands on :00 or :30."""
    hits = []
    for i, it in enumerate(items):
        start = parse_iso(it["start"])
        if start.minute in (0, 30) and start.second == 0:
            hits.append((i, start))
    return hits


# Backward compat
find_top_of_hour_targets = find_clean_clock_targets


def fmt_time(dt: datetime) -> str:
    """'8 PM', '9:30 PM' (no leading zero)."""
    s = dt.strftime("%-I:%M %p")
    if s.startswith("0"):
        s = s[1:]
    return s.replace(":00 ", " ")


def pick_kind(rng: random.Random, mix: dict, is_first_primetime: bool) -> str:
    """'block_summary' only for the first primetime hour; else weighted between deadpan/up_next."""
    if is_first_primetime and rng.random() < (mix.get("block_summary_weight", 10) / 100):
        return "block_summary"
    pool_total = mix.get("deadpan_weight", 60) + mix.get("up_next_weight", 30)
    if pool_total <= 0:
        return "up_next"
    if rng.random() < (mix.get("deadpan_weight", 60) / pool_total):
        return "deadpan"
    return "up_next"


def build_for_channel(
    conn: sqlite3.Connection,
    channel_num: str,
    channel_name: str,
    brand: dict,
    voice: dict,
    mix: dict,
    out_root: Path,
    today: datetime,
    dry_run: bool,
) -> dict:
    """Render bumpers for one channel. Returns counts by kind."""
    counts = {"deadpan": 0, "up_next": 0, "block_summary": 0}
    folder = CHANNELS_DIR / channel_num / "playout"
    files = sorted(folder.glob("*.json")) if folder.is_dir() else []
    if not files:
        return counts
    playout_path = files[-1]
    with open(playout_path) as f:
        playout = json.load(f)
    items = playout.get("items", [])
    if not items:
        return counts

    targets = find_top_of_hour_targets(items)
    primetime_targets = [(i, t) for i, t in targets if t.hour in PRIMETIME_HOURS]
    if not primetime_targets:
        return counts

    chan_dir = out_root / channel_num
    if dry_run:
        print(f"  ch{channel_num} {channel_name}: {len(primetime_targets)} primetime targets")
        return {k: len(primetime_targets) // 3 for k in counts}

    chan_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(f"{today.date()}-{channel_num}")
    deadpan_pool = list(voice.get("deadpan", []) or ["More to come."])
    rng.shuffle(deadpan_pool)
    deadpan_idx = 0

    # Pre-resolve titles for each primetime hour for block-summary use
    hour_to_title: dict[int, tuple[str, str, datetime]] = {}
    for idx, ts in primetime_targets:
        item = items[idx]
        if item.get("source", {}).get("source_type") != "local":
            continue
        l1, l2 = resolve_show_title(conn, item["source"]["path"])
        if l1:
            hour_to_title[ts.hour] = (l1, l2, ts)

    first_pt_hour = min(hour_to_title) if hour_to_title else None

    for item_idx, target_start in primetime_targets:
        next_item = items[item_idx]
        if next_item.get("source", {}).get("source_type") != "local":
            continue
        line1, line2 = resolve_show_title(conn, next_item["source"]["path"])
        if not line1:
            continue

        music = pick_music_track(conn, channel_name)
        if not music:
            continue
        music_path, _ = music

        is_first = (first_pt_hour is not None and target_start.hour == first_pt_hour)
        kind = pick_kind(rng, mix, is_first)
        # Hard rule: only the first primetime hour gets block-summary
        if kind == "block_summary" and not is_first:
            kind = "deadpan"

        dur = 18.0 if kind == "block_summary" else 15.0
        out_path = chan_dir / f"{target_start.strftime('%H%M')}-{kind}.mp4"
        args = RenderArgs(
            out_path=out_path,
            channel_name=channel_name,
            channel_num=channel_num,
            brand=brand,
            music_path=music_path,
            duration_s=dur,
        )

        ok = False
        if kind == "block_summary":
            intro = voice.get("block_summary_intro") or f"TONIGHT ON {channel_name.upper()}"
            lineup = []
            for h in sorted(hour_to_title):
                t1, _t2, ts = hour_to_title[h]
                lineup.append((fmt_time(ts), t1))
            ok = render_block_summary(args, intro, lineup)
        elif kind == "deadpan":
            line = deadpan_pool[deadpan_idx % len(deadpan_pool)]
            deadpan_idx += 1
            ok = render_personality(args, line)
        else:
            template = voice.get("up_next_template") or "AT {time}\n{title}"
            text = template.format(
                time=fmt_time(target_start),
                title=line1,
                subtitle=line2,
            )
            rows = text.split("\n")
            l1 = rows[0] if rows else ""
            l2 = rows[1] if len(rows) > 1 else line2
            ok = render_up_next(args, l1, l2, fmt_time(target_start))

        if ok:
            counts[kind] += 1

    return counts


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (defaults to today, local).")
    ap.add_argument("--only-channel", help="Render only this channel number.")
    ap.add_argument("--dry-run", action="store_true", help="Just count what would render.")
    args = ap.parse_args()

    today = datetime.now() if not args.date else datetime.strptime(args.date, "%Y-%m-%d")
    out_root = BUMPERS_ROOT / today.strftime("%Y-%m-%d")

    if not LINEUP.is_file():
        print("lineup.json not found", file=sys.stderr)
        return 2

    with open(LINEUP) as f:
        channels = json.load(f)["channels"]
    brand = load_channel_brand()
    voices_doc = load_voices()
    voiced = voices_doc.get("channels", {})
    mix = voices_doc.get("_mix", {"deadpan_weight": 60, "up_next_weight": 30, "block_summary_weight": 10})
    conn = sqlite3.connect(f"file:{JF_DB}?immutable=1", uri=True)

    totals = {"deadpan": 0, "up_next": 0, "block_summary": 0}
    rendered_channels = 0
    for ch in channels:
        num, name = ch["number"], ch["name"]
        if args.only_channel and num != args.only_channel:
            continue
        b = brand.get(name)
        v = voiced.get(name)
        if not b or not v:
            continue
        counts = build_for_channel(conn, num, name, b, v, mix, out_root, today, args.dry_run)
        n = sum(counts.values())
        if n > 0:
            rendered_channels += 1
            for k in totals:
                totals[k] += counts[k]
            print(
                f"  ch{num} {name}: "
                f"{counts['deadpan']} deadpan / "
                f"{counts['up_next']} up-next / "
                f"{counts['block_summary']} block-summary "
                f"{'(dry-run)' if args.dry_run else 'rendered'}"
            )

    grand = sum(totals.values())
    print(
        f"\nTotals across {rendered_channels} channels: "
        f"{totals['deadpan']} deadpan, {totals['up_next']} up-next, "
        f"{totals['block_summary']} block-summary "
        f"({grand} bumpers {'planned' if args.dry_run else 'written to ' + str(out_root)})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
