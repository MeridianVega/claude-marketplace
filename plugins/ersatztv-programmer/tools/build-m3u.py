#!/usr/bin/env python3
"""
build-m3u.py — Generate a sanitized + filtered + reordered channels.m3u.

Walks lineup.json, drops out-of-season holiday channels, sorts the
remaining channels alphabetically within bucket order (Core → Rotating →
Live → Experimental → Music last per user preference), assigns tvg-chno
based on alphabetical position, and writes channels.m3u next to the
xmltv.xml the nginx sidecar serves.

Run nightly as part of the daily routine — the date drives the seasonal
toggle, so today's M3U doesn't include channels that aren't in season.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

STACK_DIR = Path(os.environ.get("STACK_DIR", str(Path.home() / "ersatztv-stack")))
LINEUP = STACK_DIR / "config/ersatztv-next/lineup.json"
OUT = STACK_DIR / "config/ersatztv-next/channels.m3u"

# URLs that go into the M3U for clients
PROXY_BASE = os.environ.get("PROXY_BASE", "http://localhost:18407")  # iptv-prewarm
SIDECAR_BASE = os.environ.get("SIDECAR_BASE", "http://localhost:18408")  # nginx (logos+xmltv)

# Channel name → bucket (used for sort order). Inferred from channel-number
# range: 1-99=core, 100-199=rotating, 200-299=music, 300-399=live, 900-999=experimental.
def bucket_for(num: str) -> str:
    try:
        n = int(num)
    except ValueError:
        return "Core"
    if n < 100:
        return "Core"
    if n < 200:
        return "Rotating"
    if n < 300:
        return "Music"
    if n < 400:
        return "Live"
    return "Experimental"


# Sort order: music last, otherwise alphabetical within bucket.
BUCKET_ORDER = {
    "Core": 0,
    "Rotating": 1,
    "Live": 2,
    "Experimental": 3,
    "Music": 4,
}

# Holiday seasonal toggle. (start_month, start_day, end_month, end_day) inclusive.
SEASONAL_RULES = {
    "Halloween":     (10, 1, 10, 31),
    "Thanksgiving":  (11, 1, 11, 30),
    "Christmas":     (11, 25, 12, 31),
}


def is_in_season(name: str, today: date | None = None) -> bool:
    if name not in SEASONAL_RULES:
        return True
    today = today or date.today()
    sm, sd, em, ed = SEASONAL_RULES[name]
    today_mmdd = (today.month, today.day)
    start = (sm, sd)
    end = (em, ed)
    if start <= end:
        return start <= today_mmdd <= end
    # Wrap-around (Dec→Jan etc.)
    return today_mmdd >= start or today_mmdd <= end


def main() -> int:
    if not LINEUP.is_file():
        print(f"lineup.json not found at {LINEUP}", file=sys.stderr)
        return 2

    today = date.today()
    with open(LINEUP, "r", encoding="utf-8") as f:
        channels = json.load(f)["channels"]

    # Filter + decorate
    rows = []
    dropped = []
    for ch in channels:
        num = ch["number"]
        name = ch["name"]
        bucket = bucket_for(num)
        if not is_in_season(name, today):
            dropped.append((num, name))
            continue
        group = bucket
        if name in ("Halloween", "Thanksgiving", "Christmas"):
            group = "Core/Holiday"
        if name in ("24/7 Wrestling", "Time Machine PPV"):
            group = "Core/Wrestling"
        rows.append({"num": num, "name": name, "bucket": bucket, "group": group})

    # Sort: bucket order, then alphabetical by name (case-insensitive)
    rows.sort(key=lambda r: (BUCKET_ORDER.get(r["bucket"], 99), r["name"].lower()))

    # Emit
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for chno, r in enumerate(rows, start=1):
            num = r["num"]
            name = r["name"]
            group = r["group"]
            logo = f"{SIDECAR_BASE}/logos/{num}.png"
            url = f"{PROXY_BASE}/iptv/{num}/live.m3u8"
            # XML-escape the name for inside the comma-suffixed display field
            disp_name = name.replace("&", "&amp;")
            f.write(
                f'#EXTINF:-1 tvg-chno="{chno}" tvg-id="{num}" tvg-name="{disp_name}" '
                f'tvg-logo="{logo}" group-title="{group}",{disp_name}\n'
            )
            f.write(url + "\n")

    print(f"Wrote {OUT}")
    print(f"  channels emitted: {len(rows)}")
    if dropped:
        print(f"  out-of-season (dropped): {[f'{n} {nm}' for n, nm in dropped]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
