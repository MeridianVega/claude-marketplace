#!/usr/bin/env python3
"""quarter-planner.py — Per-channel 12-week strategic planner.

For each channel in lineup.json (minus holiday/PPV/live), surveys the
Jellyfin library, picks 3-5 anchor TV series that own primetime
weeknight slots for their full run, builds a weeknight grid, and saves
state/{N}/quarter-plan.json. The daily-rebuild routine reads these plans
and slots the next-in-queue episode of each anchor on its scheduled
weeknight.

Anchors hold their slot for the full run of the series — no rotation
mid-season just to chase variety. The planner only swaps an anchor
when its episodes_remaining hits zero OR when a hot new arrival
ranks high enough to displace it.

Usage:
    quarter-planner.py [--channel N] [--force]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

STACK_DIR = Path(os.environ.get("STACK_DIR", str(Path.home() / "ersatztv-stack")))
JF_DB = Path(os.environ.get("JF_DB",
    str(Path.home() / "Library/Application Support/jellyfin/data/jellyfin.db")))
LINEUP = STACK_DIR / "config/ersatztv-next/lineup.json"
GENRES = STACK_DIR / "tools/channel-genres.json"
STATE = STACK_DIR / "state"
MOUNTED = ("/Volumes/Jupiter/", "/Volumes/Pluto/", "/Volumes/Saturn/", "/Volumes/Uranus/")

# Channels that don't get planners: holiday (handled seasonally), PPV (anniversary-driven),
# live (single http source), music (continuous shuffle, no anchors).
SKIP = {"31","32","33","35"} | set(str(n) for n in range(200,210)) | set(str(n) for n in range(300,310))

WEEKNIGHTS = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]


def matches_genre_filter(rules: dict, item_type: str, genres: str, path: str, year: int | None) -> bool:
    """Lightweight in-Python filter — mirrors what the rebuild SQL does."""
    g_lower = (genres or "").lower()
    p_lower = (path or "").lower()

    if item_type == "tv":
        any_g = rules.get("tv_genres_any", [])
        all_g = rules.get("tv_genres_all", [])
        excl_g = rules.get("tv_excluded_genres", [])
        path_c = rules.get("tv_path_contains", [])
    elif item_type == "audio":
        any_g = rules.get("audio_genres_any", [])
        all_g = rules.get("audio_genres_all", [])
        excl_g = []
        path_c = []
    else:
        any_g = rules.get("movie_genres_any", [])
        all_g = rules.get("movie_genres_all", [])
        excl_g = rules.get("movie_excluded_genres", [])
        path_c = rules.get("movie_path_contains", [])

    if path_c and not any(p.lower() in p_lower for p in path_c):
        return False
    if any_g and not any(g.lower() in g_lower for g in any_g):
        return False
    if all_g and not all(g.lower() in g_lower for g in all_g):
        return False
    if excl_g and any(g.lower() in g_lower for g in excl_g):
        return False

    yr = rules.get("movie_year_range") if item_type == "movie" else None
    if yr and year is not None:
        if not (yr[0] <= year <= yr[1]):
            return False

    return True


def survey_channel(conn: sqlite3.Connection, rules: dict) -> dict:
    """Return {anchor_candidates, block_filler_series, movies, newly_added}."""
    if rules.get("_general_purpose_") or rules.get("_holiday_") or rules.get("_experimental_"):
        return {"anchor_candidates": [], "block_filler_series": [], "movies": [], "newly_added": []}

    is_audio_channel = "audio_genres_any" in rules

    # TV series with episode counts
    series_rows = conn.execute("""
        SELECT SeriesName,
               COUNT(*) as ep_count,
               GROUP_CONCAT(DISTINCT Genres),
               MIN(Path),
               MIN(ProductionYear)
          FROM BaseItems
         WHERE Type='MediaBrowser.Controller.Entities.TV.Episode'
           AND SeriesName IS NOT NULL
           AND Path IS NOT NULL
         GROUP BY SeriesName
    """).fetchall()

    anchor_candidates = []
    block_filler = []
    for series, ep_count, genres_concat, sample_path, sample_year in series_rows:
        # Quick reachability sanity
        if not any(sample_path and sample_path.startswith(p) for p in MOUNTED):
            continue
        if not matches_genre_filter(rules, "tv", genres_concat or "", sample_path or "", sample_year):
            continue
        # Skip excluded series
        excl = rules.get("tv_excluded_series", [])
        if excl and any(e.lower() in series.lower() for e in excl):
            continue
        entry = {"series": series, "episodes": ep_count, "genres": genres_concat}
        if ep_count >= 13:
            anchor_candidates.append(entry)
        else:
            block_filler.append(entry)

    anchor_candidates.sort(key=lambda x: -x["episodes"])
    block_filler.sort(key=lambda x: -x["episodes"])

    # Movies (sample)
    movies = []
    if not is_audio_channel:
        movie_rows = conn.execute("""
            SELECT Name, Genres, Path, ProductionYear, RuntimeTicks/600000000.0 as runtime_min
              FROM BaseItems
             WHERE Type='MediaBrowser.Controller.Entities.Movies.Movie'
               AND Name IS NOT NULL AND Path IS NOT NULL
               AND RuntimeTicks BETWEEN 4500000000 AND 96000000000
             ORDER BY ProductionYear DESC LIMIT 200
        """).fetchall()
        for name, genres_, path, year, rt in movie_rows:
            if not any(path and path.startswith(p) for p in MOUNTED): continue
            if not matches_genre_filter(rules, "movie", genres_ or "", path or "", year): continue
            movies.append({"name": name, "year": year, "runtime_min": int(rt) if rt else 0})
    movies.sort(key=lambda x: -(x.get("year") or 0))

    # Newly added (last 7 days)
    seven_ago_ticks = int((datetime.now() - timedelta(days=7)).timestamp() * 10000000) + 621355968000000000
    newly_added = []
    new_rows = conn.execute("""
        SELECT Name, SeriesName, Path, Genres, ProductionYear
          FROM BaseItems
         WHERE DateCreated > ?
           AND Type IN ('MediaBrowser.Controller.Entities.Movies.Movie',
                        'MediaBrowser.Controller.Entities.TV.Episode')
         LIMIT 50
    """, (seven_ago_ticks,)).fetchall()
    for name, series, path, genres_, year in new_rows:
        if not any(path and path.startswith(p) for p in MOUNTED): continue
        item_type = "tv" if series else "movie"
        if not matches_genre_filter(rules, item_type, genres_ or "", path or "", year): continue
        newly_added.append({"name": name, "series": series, "year": year})

    return {
        "anchor_candidates": anchor_candidates[:20],
        "block_filler_series": block_filler[:20],
        "movies": movies[:30],
        "newly_added": newly_added[:10],
    }


def plan_channel(channel_num: str, channel_name: str, rules: dict, survey: dict) -> dict:
    """Build the quarterly plan for one channel."""
    anchors = survey["anchor_candidates"]
    plan = {
        "channel": channel_num,
        "name": channel_name,
        "planned_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "valid_through": (datetime.now() + timedelta(days=84)).strftime("%Y-%m-%d"),
        "library_summary": {
            "anchor_candidates": len(survey["anchor_candidates"]),
            "block_filler_series": len(survey["block_filler_series"]),
            "movies_in_pool": len(survey["movies"]),
            "newly_added_matching": len(survey["newly_added"]),
        },
        "anchors": {},
        "blocks": {
            "morning":   {"hours": [5, 9],   "character": "lighter / sitcom-adjacent / kid-friendly"},
            "daytime":   {"hours": [9, 16],  "character": "block-filler series, episodic"},
            "afternoon": {"hours": [16, 19], "character": "lead-in to primetime"},
            "primetime": {"hours": [19, 23], "character": "anchored tentpole weeknights"},
            "latenight": {"hours": [23, 25], "character": "edgier or comedy"},
        },
        "weeknight_grid": {},
        "priority_promotions": [],
    }

    # Pick top 3-5 anchors and assign them to weeknights
    top_anchors = anchors[:5]
    weeknight_slots = [("Tue", 21), ("Wed", 21), ("Thu", 21), ("Fri", 21), ("Sun", 21)]
    for i, anchor in enumerate(top_anchors[:len(weeknight_slots)]):
        day, hour = weeknight_slots[i]
        slot_key = f"{day}-{hour:02d}"
        plan["anchors"][slot_key] = {
            "primary": anchor["series"],
            "next_episode": {"season": 1, "episode": 1},
            "episodes_remaining": anchor["episodes"],
            "expected_end_date": (datetime.now() + timedelta(weeks=anchor["episodes"])).strftime("%Y-%m-%d"),
            "_": f"Anchored for {anchor['episodes']} weekly slots",
        }

    # Weeknight grid: 19 + 20 = lead-in (filler series), 21 = anchor, 22 = post-tentpole
    block_pool = [s["series"] for s in survey["block_filler_series"]][:8]
    for day in WEEKNIGHTS:
        plan["weeknight_grid"][day] = {}
        for hour, char in [(19, "lead-in"), (20, "8pm anchor"), (22, "post-tentpole")]:
            if block_pool:
                plan["weeknight_grid"][day][str(hour)] = block_pool[(hash(day + str(hour)) % len(block_pool))]
        # 21 from anchors
        slot_key = f"{day}-21"
        if slot_key in plan["anchors"]:
            plan["weeknight_grid"][day]["21"] = plan["anchors"][slot_key]["primary"]
        elif top_anchors:
            plan["weeknight_grid"][day]["21"] = top_anchors[hash(day) % len(top_anchors)]["series"]

    # Block-pool series for non-primetime
    plan["blocks"]["morning"]["series_pool"]   = block_pool[:5]
    plan["blocks"]["daytime"]["series_pool"]   = block_pool[2:8]
    plan["blocks"]["afternoon"]["series_pool"] = block_pool[1:6]
    plan["blocks"]["latenight"]["series_pool"] = block_pool[3:8]

    # Priority promotions for newly-added items
    for item in survey["newly_added"][:3]:
        plan["priority_promotions"].append({
            "item": item.get("series") or item.get("name"),
            "slot_hint": "Thu-21" if item.get("series") else "Sat-20",
            "promote_for_weeks": 4,
        })

    return plan


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", help="Plan only this channel.")
    ap.add_argument("--force", action="store_true", help="Re-plan even if existing plan is current.")
    args = ap.parse_args()

    if not LINEUP.is_file() or not GENRES.is_file():
        print("lineup or channel-genres not found", file=sys.stderr)
        return 2
    conn = sqlite3.connect(f"file:{JF_DB}?immutable=1", uri=True)
    lineup = json.loads(LINEUP.read_text())["channels"]
    genre_rules = json.loads(GENRES.read_text())

    STATE.mkdir(parents=True, exist_ok=True)
    planned = 0
    for ch in lineup:
        n = ch["number"]
        if args.channel and n != args.channel: continue
        if n in SKIP: continue
        rules = genre_rules.get(n, {})
        if not rules: continue

        plan_path = STATE / n / "quarter-plan.json"
        plan_path.parent.mkdir(parents=True, exist_ok=True)

        # Skip if recent and not forced
        if plan_path.is_file() and not args.force:
            try:
                existing = json.loads(plan_path.read_text())
                planned_at = datetime.fromisoformat(existing["planned_at"])
                if (datetime.now().astimezone() - planned_at).days < 7:
                    continue
            except (KeyError, ValueError):
                pass

        survey = survey_channel(conn, rules)
        plan = plan_channel(n, ch["name"], rules, survey)
        plan_path.write_text(json.dumps(plan, indent=2))
        planned += 1
        anchor_count = len(plan["anchors"])
        print(f"  ch{n} {ch['name']}: {anchor_count} anchors, "
              f"{len(survey['block_filler_series'])} fillers, "
              f"{len(survey['newly_added'])} newly-added")

    print(f"\nPlanned {planned} channels — quarterly plans valid 12 weeks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
