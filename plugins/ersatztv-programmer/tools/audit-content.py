#!/usr/bin/env python3
"""audit-content.py — Scan every playout for off-genre items.

For each channel, walks its playout JSON and checks each `local`-source
item against the channel's rules in `channel-genres.json` (plus the
`_global_exclusions` block that all non-holiday channels inherit).
Reports violations: holiday content on non-holiday channels, anime on
non-anime channels, items whose Jellyfin metadata clearly contradicts the
channel's identity.

Returns exit code 0 if every channel is clean, 1 if any violations.

Usage:
    audit-content.py [--strict] [--channel N] [--json]

--strict treats warning-class issues (e.g. "Drama playing a Comedy") as
hard fails. Without it, only clear-cut violations (holiday on non-holiday,
non-anime on anime channel) fail.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

STACK_DIR = Path(os.environ.get("STACK_DIR", str(Path.home() / "ersatztv-stack")))
JF_DB = Path(
    os.environ.get(
        "JF_DB",
        str(Path.home() / "Library/Application Support/jellyfin/data/jellyfin.db"),
    )
)
LINEUP = STACK_DIR / "config/ersatztv-next/lineup.json"
CHANNELS_DIR = STACK_DIR / "config/ersatztv-next/channels"
GENRES = STACK_DIR / "tools/channel-genres.json"

HOLIDAY_CHANNELS = {"31", "32", "33"}


def load_metadata(conn: sqlite3.Connection, paths: list[str]) -> dict:
    """Return {path: {name, series, genres}} for the given paths."""
    out = {}
    for p in paths:
        row = conn.execute(
            "SELECT Name, SeriesName, Genres FROM BaseItems WHERE Path = ? LIMIT 1",
            (p,),
        ).fetchone()
        if row:
            out[p] = {
                "name": row[0] or "",
                "series": row[1] or "",
                "genres": (row[2] or "").lower(),
            }
    return out


def matches_substr(haystack: str, needles: list[str]) -> str | None:
    """Return the first matching needle, or None."""
    h = (haystack or "").lower()
    for n in needles:
        if n.lower() in h:
            return n
    return None


def audit_channel(
    channel_num: str,
    channel_name: str,
    playout_items: list[dict],
    rules: dict,
    global_excl: dict,
    meta: dict,
) -> list[dict]:
    """Return a list of violation dicts for this channel."""
    violations = []
    is_holiday = channel_num in HOLIDAY_CHANNELS
    glob_paths = global_excl.get("path_excludes", [])
    glob_series = global_excl.get("series_excludes", [])
    glob_titles = global_excl.get("title_substrings_excludes", [])

    for it in playout_items:
        src = it.get("source") or {}
        if src.get("source_type") != "local":
            continue
        path = src.get("path", "")
        m = meta.get(path, {})
        title = m.get("name", "") or Path(path).stem
        series = m.get("series", "")
        genres = m.get("genres", "")

        # Skip music channels (200-209) — songs with "Christmas/Halloween" in title
        # are usually NOT holiday programming (they're year-round catalog songs).
        is_music_channel = 200 <= int(channel_num) <= 209 if channel_num.isdigit() else False
        is_audio_file = any(path.lower().endswith(ext) for ext in
                           (".mp3", ".flac", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".alac"))

        # Check global holiday exclusion (non-holiday channels)
        if not is_holiday:
            # Path check: applies to ALL non-holiday channels (including music)
            hit = matches_substr(path, glob_paths)
            if hit and not is_audio_file:
                # For audio files, only path-match if the path contains a holiday-album folder
                violations.append({
                    "channel": channel_num,
                    "name": channel_name,
                    "severity": "ERROR",
                    "kind": "holiday content on non-holiday channel",
                    "path": path,
                    "matched": hit,
                })
                continue
            elif hit and is_audio_file:
                # Audio file with holiday in path — flag only if it looks like an album, not a song with the word
                # Path patterns like /Christmas/ or /Holiday Album/ are real; /Album with christmas in song title/ is not
                if "/Christmas/" in path or "/Holiday/" in path or "/Halloween/" in path or "/Xmas/" in path:
                    violations.append({
                        "channel": channel_num,
                        "name": channel_name,
                        "severity": "ERROR",
                        "kind": "holiday album on non-holiday music channel",
                        "path": path,
                        "matched": hit,
                    })
                    continue
                # Otherwise — false positive (song title contains the word but isn't holiday programming)

            # Series check: skip for music channels (songs don't have "series")
            if not is_music_channel:
                hit = matches_substr(series, glob_series)
                if hit:
                    violations.append({
                        "channel": channel_num,
                        "name": channel_name,
                        "severity": "ERROR",
                        "kind": "holiday series on non-holiday channel",
                        "series": series,
                        "matched": hit,
                    })
                    continue

            # Title check: skip for music channels AND audio files
            if not is_music_channel and not is_audio_file:
                hit = matches_substr(title, glob_titles)
                if hit:
                    violations.append({
                        "channel": channel_num,
                        "name": channel_name,
                        "severity": "ERROR",
                        "kind": "holiday title on non-holiday channel",
                        "title": title,
                        "matched": hit,
                    })
                    continue

        # Check per-channel excluded series
        excl_series = rules.get("tv_excluded_series", []) + rules.get("movie_excluded_series", [])
        if excl_series and series:
            hit = matches_substr(series, excl_series)
            if hit:
                violations.append({
                    "channel": channel_num,
                    "name": channel_name,
                    "severity": "ERROR",
                    "kind": "blacklisted series",
                    "series": series,
                    "matched": hit,
                })
                continue

        # Check per-channel excluded genres
        excl_genres = rules.get("tv_excluded_genres", []) + rules.get("movie_excluded_genres", [])
        if excl_genres and genres:
            hit = matches_substr(genres, excl_genres)
            if hit:
                violations.append({
                    "channel": channel_num,
                    "name": channel_name,
                    "severity": "WARN",
                    "kind": "off-genre",
                    "genres": genres[:80],
                    "matched": hit,
                })

        # Whitelist-based "not in series whitelist" checks removed — channels no
        # longer have hardcoded series whitelists. The rebuild agent picks from
        # live Jellyfin queries against metadata filters in channel-genres.json.

    return violations


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true",
                    help="WARN-class issues become hard fails.")
    ap.add_argument("--channel", help="Audit only this channel.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not LINEUP.is_file():
        print(f"lineup.json not found at {LINEUP}", file=sys.stderr)
        return 2
    if not GENRES.is_file():
        print(f"channel-genres.json not found at {GENRES}", file=sys.stderr)
        return 2

    with open(LINEUP) as f:
        channels = json.load(f)["channels"]
    with open(GENRES) as f:
        genre_rules = json.load(f)
    global_excl = genre_rules.get("_global_exclusions", {})

    if args.channel:
        channels = [c for c in channels if c["number"] == args.channel]

    conn = sqlite3.connect(f"file:{JF_DB}?immutable=1", uri=True) if JF_DB.is_file() else None

    all_violations = []
    audited = 0
    for ch in channels:
        n = ch["number"]
        rules = genre_rules.get(n, {})
        pf = CHANNELS_DIR / n / "playout"
        files = sorted(pf.glob("*.json")) if pf.is_dir() else []
        if not files:
            continue
        d = json.loads(files[-1].read_text())
        items = d.get("items", [])
        if not items:
            continue
        # Pre-fetch metadata for all paths in this channel
        paths = [
            it["source"]["path"] for it in items
            if (it.get("source") or {}).get("source_type") == "local"
        ]
        meta = load_metadata(conn, paths) if conn else {}
        violations = audit_channel(n, ch["name"], items, rules, global_excl, meta)
        all_violations.extend(violations)
        audited += 1

    errors = [v for v in all_violations if v["severity"] == "ERROR"]
    warnings = [v for v in all_violations if v["severity"] == "WARN"]

    if args.json:
        print(json.dumps({
            "audited": audited,
            "errors": errors,
            "warnings": warnings,
        }, indent=2))
    else:
        print(f"\nAudited {audited} channels: {len(errors)} ERRORs, {len(warnings)} WARNs")
        if errors:
            print("\nERRORS (must fix — holiday content on non-holiday channel, blacklisted series):")
            for v in errors[:30]:
                detail = v.get("series") or v.get("title") or v.get("path", "")
                print(f"  ch{v['channel']:>3} {v['name']:<22} [{v['kind']}] {detail[:80]} (matched: {v.get('matched','-')})")
        if warnings and (args.strict or len(errors) == 0):
            print("\nWARNINGS (advisory):")
            for v in warnings[:30]:
                detail = v.get("series") or v.get("genres") or "?"
                print(f"  ch{v['channel']:>3} {v['name']:<22} [{v['kind']}] {detail[:80]}")

    if errors:
        return 1
    if warnings and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
