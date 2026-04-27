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
    """Adult Swim minimal: solid black background, one deadpan voice line in
    clean white Helvetica-ish bold, that's it. No logo, no channel mark, no
    gradient. The line is the bumper."""
    from PIL import Image, ImageDraw, ImageFont
    W, H = 1280, 720

    # Solid near-black background — Adult Swim signature
    img = Image.new("RGB", (W, H), (10, 10, 10))

    # Pick the boldest sans-serif we have for that Helvetica-bold feel
    # Order of preference for Adult Swim vibe: Archivo Black > Tomorrow Bold > BebasNeue > anything bold
    candidates = ["ArchivoBlack-Regular.ttf", "Tomorrow-Bold.ttf",
                  "BebasNeue-Regular.ttf", "Inter-Bold.ttf", "Oswald-Bold.ttf"]
    font_path = None
    for c in candidates:
        p = FONTS_DIR / c
        if p.is_file():
            font_path = p
            break
    if font_path is None:
        font_path = font_path_for(brand)

    img = img.convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    td = ImageDraw.Draw(overlay)

    # Pure white text — Adult Swim spec
    fg = (255, 255, 255, 255)

    # Find the PRIMARY text layer (the deadpan voice line) — the one with the
    # biggest font size in the input list. Drop everything else.
    if not text_layers:
        img.save(out_png, "PNG", compress_level=1)
        return True
    primary = max(text_layers, key=lambda L: L.size)

    # Render the primary line. Adult Swim does it lowercase, often.
    text = primary.text.lower()

    # Word-wrap the line to ~22 chars; max 4 rows.
    words = text.split()
    rows: list[str] = []
    current = ""
    for w in words:
        if len(current) + 1 + len(w) > 22 and current:
            rows.append(current)
            current = w
        else:
            current = (current + " " + w).strip()
    if current:
        rows.append(current)
    rows = rows[:4]

    # Big size scaled to row count + 720p canvas
    size = 110 if len(rows) <= 2 else 88 if len(rows) == 3 else 70
    try:
        f = ImageFont.truetype(str(font_path), size)
    except OSError:
        f = ImageFont.load_default()

    # Center the block vertically
    line_h = int(size * 1.15)
    block_h = line_h * len(rows)
    base_y = (H - block_h) // 2

    for i, row in enumerate(rows):
        bbox = td.textbbox((0, 0), row, font=f)
        tw = bbox[2] - bbox[0]
        x = (W - tw) // 2
        y = base_y + i * line_h
        td.text((x - bbox[0], y - bbox[1]), row, font=f, fill=fg)

    # Tiny brand color stripe at the bottom — that's the only "branding"
    # (Adult Swim has the [as] logo bottom-right; we use a 4px color line)
    stripe_color = _hex_to_rgb(brand.get("stroke") or "#FFFFFF")
    sd = ImageDraw.Draw(overlay)
    sd.rectangle([0, H - 4, W, H], fill=stripe_color + (255,))

    img = Image.alpha_composite(img, overlay).convert("RGB")
    img.save(out_png, "PNG", optimize=False, compress_level=1)
    return True


def _render_with_layers(
    a: RenderArgs,
    text_layers: list,
    logo_y: int = 140,
    logo_w: int = 280,
) -> bool:
    """Render card via PIL → static PNG → MP4 with audio. PNG persists alongside
    the MP4 so the deco system can use the same image as a long-form filler video
    via lavfi `movie=…,loop`. The MP4 is the spliced 15s pre-program bumper;
    the PNG is the static card displayed during the rest of the filler period."""
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
    # Keep PNG (no unlink) — deco system uses it as a static-image video source
    # for the full filler period via `movie=…,loop`. Both artifacts live side-by-side.
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


def plan_channel_work(
    conn: sqlite3.Connection,
    channel_num: str,
    channel_name: str,
    brand: dict,
    voice: dict,
    mix: dict,
    out_root: Path,
    today: datetime,
) -> list[dict]:
    """Plan all bumper work units for one channel — returns list of work-item dicts.
    No rendering happens here; this just resolves library data for parallel dispatch."""
    folder = CHANNELS_DIR / channel_num / "playout"
    files = sorted(folder.glob("*.json")) if folder.is_dir() else []
    if not files: return []
    with open(files[-1]) as f:
        playout = json.load(f)
    items = playout.get("items", [])
    if not items: return []

    targets = find_top_of_hour_targets(items)
    primetime_targets = [(i, t) for i, t in targets if t.hour in PRIMETIME_HOURS]
    if not primetime_targets: return []

    rng = random.Random(f"{today.date()}-{channel_num}")
    deadpan_pool = list(voice.get("deadpan", []) or ["More to come."])
    rng.shuffle(deadpan_pool)
    deadpan_idx = 0

    # Pre-resolve titles for each primetime hour
    hour_to_title: dict[int, tuple[str, str, datetime]] = {}
    for idx, ts in primetime_targets:
        item = items[idx]
        if item.get("source", {}).get("source_type") != "local": continue
        l1, l2 = resolve_show_title(conn, item["source"]["path"])
        if l1:
            hour_to_title[ts.hour] = (l1, l2, ts)

    first_pt_hour = min(hour_to_title) if hour_to_title else None

    # Pick ONE music track for all this channel's bumpers — deterministic per day, fast.
    # Future bumpers reusing the same track is fine; cards are short and varied otherwise.
    music = pick_music_track(conn, channel_name)
    music_path = music[0] if music else None

    chan_dir = out_root / channel_num
    work_items = []
    for item_idx, target_start in primetime_targets:
        next_item = items[item_idx]
        if next_item.get("source", {}).get("source_type") != "local": continue
        line1, line2 = resolve_show_title(conn, next_item["source"]["path"])
        if not line1: continue
        if not music_path:
            music = pick_music_track(conn, channel_name)
            if not music: continue
            music_path = music[0]

        is_first = (first_pt_hour is not None and target_start.hour == first_pt_hour)
        kind = pick_kind(rng, mix, is_first)
        if kind == "block_summary" and not is_first:
            kind = "deadpan"

        dur = 18.0 if kind == "block_summary" else 15.0
        out_path = chan_dir / f"{target_start.strftime('%H%M')}-{kind}.mp4"

        wi = {
            "channel_num": channel_num,
            "channel_name": channel_name,
            "brand": brand,
            "music_path": str(music_path),
            "duration_s": dur,
            "out_path": str(out_path),
            "kind": kind,
            "time_text": fmt_time(target_start),
        }
        if kind == "block_summary":
            intro = voice.get("block_summary_intro") or f"TONIGHT ON {channel_name.upper()}"
            lineup = [(fmt_time(ts), t1) for h, (t1, _t2, ts) in sorted(hour_to_title.items())]
            wi.update({"intro": intro, "lineup": lineup})
        elif kind == "deadpan":
            wi["deadpan"] = deadpan_pool[deadpan_idx % len(deadpan_pool)]
            deadpan_idx += 1
        else:
            template = voice.get("up_next_template") or "AT {time}\n{title}"
            text = template.format(time=fmt_time(target_start), title=line1, subtitle=line2)
            rows = text.split("\n")
            wi["line1"] = rows[0] if rows else ""
            wi["line2"] = rows[1] if len(rows) > 1 else line2

        work_items.append(wi)
    return work_items


def render_work_item(wi: dict) -> tuple[str, bool]:
    """Top-level worker function for ProcessPool. Renders ONE bumper. Returns (kind, ok)."""
    args = RenderArgs(
        out_path=Path(wi["out_path"]),
        channel_name=wi["channel_name"],
        channel_num=wi["channel_num"],
        brand=wi["brand"],
        music_path=wi["music_path"],
        duration_s=wi["duration_s"],
    )
    kind = wi["kind"]
    args.out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if kind == "block_summary":
            ok = render_block_summary(args, wi["intro"], wi["lineup"])
        elif kind == "deadpan":
            ok = render_personality(args, wi["deadpan"])
        else:
            ok = render_up_next(args, wi["line1"], wi["line2"], wi["time_text"])
    except Exception as e:
        print(f"  ! {wi['out_path']}: {e}", file=sys.stderr)
        ok = False
    return (kind, ok)


def prune_old_bumpers(keep_days: int) -> int:
    """Delete bumper-day folders older than `keep_days` days. Returns count removed."""
    if not BUMPERS_ROOT.is_dir() or keep_days < 1:
        return 0
    today = datetime.now().date()
    removed = 0
    for d in BUMPERS_ROOT.iterdir():
        if not d.is_dir() or len(d.name) != 10: continue
        try:
            folder_date = datetime.strptime(d.name, "%Y-%m-%d").date()
        except ValueError:
            continue
        age_days = (today - folder_date).days
        if age_days > keep_days:
            import shutil
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    return removed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    import os as _os
    from concurrent.futures import ProcessPoolExecutor, as_completed
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (defaults to today, local).")
    ap.add_argument("--only-channel", help="Render only this channel number.")
    ap.add_argument("--dry-run", action="store_true", help="Just count what would render.")
    ap.add_argument("--workers", type=int,
                    default=max(1, (_os.cpu_count() or 4) - 2),
                    help="Parallel render workers (default: cpu_count - 2).")
    ap.add_argument("--keep-days", type=int, default=3,
                    help="Auto-prune bumper folders older than N days (default: 3). 0 = no prune.")
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

    # Phase A: plan all work serially (SQL queries done once on main thread)
    all_work: list[dict] = []
    chan_count: dict[str, int] = {}
    for ch in channels:
        num, name = ch["number"], ch["name"]
        if args.only_channel and num != args.only_channel:
            continue
        b = brand.get(name)
        v = voiced.get(name)
        if not b or not v: continue
        items = plan_channel_work(conn, num, name, b, v, mix, out_root, today)
        chan_count[num] = len(items)
        all_work.extend(items)

    if args.dry_run:
        print(f"DRY RUN: {len(all_work)} bumpers across {sum(1 for v in chan_count.values() if v>0)} channels")
        for num, n in sorted(chan_count.items(), key=lambda x: int(x[0])):
            if n > 0: print(f"  ch{num}: {n} bumpers")
        return 0

    # Phase B: parallel render
    totals = {"deadpan": 0, "up_next": 0, "block_summary": 0}
    if not all_work:
        print("nothing to render")
        return 0

    print(f"rendering {len(all_work)} bumpers across {sum(1 for v in chan_count.values() if v>0)} channels with {args.workers} workers...")
    import time as _time
    t0 = _time.time()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(render_work_item, wi) for wi in all_work]
        done_n = 0
        for fut in as_completed(futures):
            kind, ok = fut.result()
            if ok:
                totals[kind] = totals.get(kind, 0) + 1
            done_n += 1
            if done_n % 25 == 0 or done_n == len(futures):
                elapsed = _time.time() - t0
                rate = done_n / max(elapsed, 0.01)
                eta = (len(futures) - done_n) / max(rate, 0.01)
                print(f"  [{done_n}/{len(futures)}] {rate:.1f} cards/s, ETA {int(eta)}s")
    rendered_channels = sum(1 for v in chan_count.values() if v > 0)

    grand = sum(totals.values())
    elapsed = _time.time() - t0
    print(
        f"\nTotals across {rendered_channels} channels: "
        f"{totals['deadpan']} deadpan, {totals['up_next']} up-next, "
        f"{totals['block_summary']} block-summary "
        f"({grand} bumpers in {elapsed:.1f}s, written to {out_root})"
    )

    # Auto-prune old day folders
    if args.keep_days > 0:
        removed = prune_old_bumpers(args.keep_days)
        if removed > 0:
            print(f"Pruned {removed} bumper folder(s) older than {args.keep_days} days.")

    # Disk accounting
    try:
        import subprocess as _sp
        du_today = _sp.check_output(["du", "-sh", str(out_root)], text=True).split()[0]
        du_all   = _sp.check_output(["du", "-sh", str(BUMPERS_ROOT)], text=True).split()[0]
        print(f"Disk: today={du_today}, all bumpers={du_all}")
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
