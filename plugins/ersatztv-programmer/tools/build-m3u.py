#!/usr/bin/env python3
"""
build-m3u.py — Generate a sanitized + filtered + reordered channels.m3u.

Walks lineup.json, applies the user-supplied seasonal-toggle config to
hide out-of-season channels, sorts the remaining channels alphabetically
within bucket order (Core → Rotating → Live → Experimental → Music last
per recommended UX), assigns tvg-chno based on alphabetical position,
and writes channels.m3u next to the xmltv.xml the nginx sidecar serves.

Run nightly as part of the daily routine — the date drives the seasonal
toggle, so today's M3U doesn't include channels that aren't in season.

Seasonal config:
  ${SEASONAL_RULES_FILE} (default: ${STACK_DIR}/tools/seasonal-rules.json)
  Format: { "Channel Name": [start_month, start_day, end_month, end_day] }
  Wrap-around supported (e.g. [11,25,1,5] for late-Nov-through-early-Jan).
  Channels not in the file are always considered in-season.

Group-title overrides (cosmetic — affects how Jellyfin groups channels):
  ${GROUP_OVERRIDES_FILE} (default: ${STACK_DIR}/tools/group-overrides.json)
  Format: { "Channel Name": "Display/Group" }
  Falls back to the channel's bucket name.
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

SEASONAL_RULES_FILE = Path(
    os.environ.get(
        "SEASONAL_RULES_FILE",
        str(STACK_DIR / "tools/seasonal-rules.json"),
    )
)
GROUP_OVERRIDES_FILE = Path(
    os.environ.get(
        "GROUP_OVERRIDES_FILE",
        str(STACK_DIR / "tools/group-overrides.json"),
    )
)


def load_seasonal_rules() -> dict[str, tuple[int, int, int, int]]:
    """Read user-supplied seasonal toggles. Channels not listed are always
    in-season. Format: {"Channel Name": [start_m, start_d, end_m, end_d]}."""
    if not SEASONAL_RULES_FILE.is_file():
        return {}
    try:
        with open(SEASONAL_RULES_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {k: tuple(v) for k, v in raw.items() if isinstance(v, list) and len(v) == 4}
    except (json.JSONDecodeError, OSError):
        return {}


def load_group_overrides() -> dict[str, str]:
    if not GROUP_OVERRIDES_FILE.is_file():
        return {}
    try:
        with open(GROUP_OVERRIDES_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {k: v for k, v in raw.items() if isinstance(v, str)}
    except (json.JSONDecodeError, OSError):
        return {}


def is_in_season(
    name: str,
    rules: dict[str, tuple[int, int, int, int]],
    today: date | None = None,
) -> bool:
    if name not in rules:
        return True
    today = today or date.today()
    sm, sd, em, ed = rules[name]
    today_mmdd = (today.month, today.day)
    start = (sm, sd)
    end = (em, ed)
    if start <= end:
        return start <= today_mmdd <= end
    # Wrap-around (e.g. late-Nov-through-early-Jan)
    return today_mmdd >= start or today_mmdd <= end


def main() -> int:
    if not LINEUP.is_file():
        print(f"lineup.json not found at {LINEUP}", file=sys.stderr)
        return 2

    today = date.today()
    seasonal_rules = load_seasonal_rules()
    group_overrides = load_group_overrides()

    with open(LINEUP, "r", encoding="utf-8") as f:
        channels = json.load(f)["channels"]

    # Filter + decorate
    rows = []
    dropped = []
    for ch in channels:
        num = ch["number"]
        name = ch["name"]
        bucket = bucket_for(num)
        if not is_in_season(name, seasonal_rules, today):
            dropped.append((num, name))
            continue
        group = group_overrides.get(name, bucket)
        rows.append({"num": num, "name": name, "bucket": bucket, "group": group})

    # Sort: bucket order, then alphabetical by name (case-insensitive)
    rows.sort(key=lambda r: (BUCKET_ORDER.get(r["bucket"], 99), r["name"].lower()))

    # Emit. Critical: tvg-id == tvg-chno (the alphabetical display position),
    # NOT the underlying lineup.json channel number. Jellyfin's M3U tuner uses
    # tvg-chno (or tvg-id when no chno) as the channel's "Number" field, and
    # XMLTV's <channel id="N"> must match that number for guide-data matching
    # to work. Stream URL still uses the lineup.json number (the iptv-prewarm
    # proxy + ETV Next still know the channel by its native number).
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for chno, r in enumerate(rows, start=1):
            num = r["num"]              # ETV/lineup native channel number
            name = r["name"]
            group = r["group"]
            logo = f"{SIDECAR_BASE}/logos/{num}.png"
            url = f"{PROXY_BASE}/iptv/{num}/live.m3u8"
            # XML-escape the name for inside the comma-suffixed display field
            disp_name = name.replace("&", "&amp;")
            f.write(
                f'#EXTINF:-1 tvg-chno="{chno}" tvg-id="{chno}" tvg-name="{disp_name}" '
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
