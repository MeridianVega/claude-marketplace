#!/usr/bin/env python3
"""splice-bumpers.py — Insert rendered bumper MP4s into channel playouts.

For each channel that has bumpers under bumpers/{YYYY-MM-DD}/{N}/{HHMM}-{kind}.mp4,
walks the channel's playout JSON and splices each bumper as a `local` source
replacing the trailing N seconds of the item ending at that {HHMM} boundary.

Net effect: at each :00 / :30 boundary that has a bumper, viewers see a 15s
branded card before the next program starts.

Usage:
    splice-bumpers.py [--date YYYY-MM-DD] [--only-channel N] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

STACK_DIR = Path(os.environ.get("STACK_DIR", str(Path.home() / "ersatztv-stack")))
CHANNELS_DIR = STACK_DIR / "config/ersatztv-next/channels"
BUMPERS_ROOT = STACK_DIR / "bumpers"


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def find_playout(channel_num: str, today: datetime) -> Path | None:
    folder = CHANNELS_DIR / channel_num / "playout"
    if not folder.is_dir():
        return None
    files = sorted(folder.glob("*.json"))
    return files[-1] if files else None


def splice_channel(channel_num: str, today: datetime, dry_run: bool) -> tuple[int, int]:
    """Return (spliced_count, total_bumpers_for_channel)."""
    bumper_dir = BUMPERS_ROOT / today.strftime("%Y-%m-%d") / channel_num
    if not bumper_dir.is_dir():
        return (0, 0)
    bumpers = sorted(bumper_dir.glob("*.mp4"))
    if not bumpers:
        return (0, 0)

    pf = find_playout(channel_num, today)
    if not pf:
        return (0, len(bumpers))
    playout = json.loads(pf.read_text())
    items = playout.get("items", [])
    if not items:
        return (0, len(bumpers))

    # Build a {HHMM_string: mp4_path} map
    bumper_map: dict[str, Path] = {}
    for b in bumpers:
        # filename: HHMM-kind.mp4
        stem = b.stem  # e.g. "1900-deadpan"
        time_part = stem.split("-")[0]
        if len(time_part) != 4 or not time_part.isdigit():
            continue
        bumper_map[time_part] = b

    spliced = 0
    new_items: list[dict] = []
    i = 0
    while i < len(items):
        it = items[i]
        next_idx = i + 1
        # Look at the NEXT item: if it starts at a bumper boundary, splice the bumper
        # in *before* it by trimming the tail of THIS item.
        if next_idx < len(items):
            next_start = parse_iso(items[next_idx]["start"])
            target_hhmm = next_start.strftime("%H%M")
            if target_hhmm in bumper_map:
                # The bumper is 15s for personality/up-next, 18s for block-summary
                bumper_path = bumper_map[target_hhmm]
                # Inspect file basename to determine duration (block-summary is 18s)
                kind = bumper_path.stem.split("-", 1)[1] if "-" in bumper_path.stem else "up_next"
                bumper_dur_s = 18 if kind.startswith("block") else 15

                # Only splice into music-filler items (not real programs)
                src = it.get("source", {})
                src_type = src.get("source_type", "")
                if src_type == "local":
                    # Check if this is a music filler (id starts with "f-" or similar)
                    item_id = it.get("id", "")
                    is_filler = (
                        item_id.startswith("f-") or
                        item_id.startswith("pt-fill") or
                        item_id.startswith("pt-night-pad") or
                        item_id.startswith("pt-day-pad") or
                        "filler" in item_id or
                        "fill" in item_id
                    )
                    if not is_filler:
                        # Fall through — don't splice into a real program
                        new_items.append(it)
                        i += 1
                        continue

                    # Trim the music filler by bumper_dur_s, then add the bumper
                    item_start = parse_iso(it["start"])
                    item_finish = parse_iso(it["finish"])
                    item_dur = (item_finish - item_start).total_seconds()
                    if item_dur < bumper_dur_s + 5:
                        # Filler too short — replace it entirely with the bumper
                        new_items.append({
                            "id": f"bumper-{channel_num}-{target_hhmm}",
                            "start": it["start"],
                            "finish": it["finish"],
                            "source": {"source_type": "local", "path": str(bumper_path)}
                        })
                        spliced += 1
                        i += 1
                        continue
                    # Trim the filler
                    new_finish = item_finish - timedelta(seconds=bumper_dur_s)
                    new_filler = dict(it)
                    new_filler["finish"] = new_finish.isoformat(timespec='milliseconds')
                    # Also adjust out_point_ms if present, OR add it
                    new_src = dict(src)
                    if "out_point_ms" in new_src:
                        # Reduce out_point_ms
                        new_out = new_src["out_point_ms"] - int(bumper_dur_s * 1000)
                        if new_out > 0:
                            new_src["out_point_ms"] = new_out
                        else:
                            new_src.pop("out_point_ms", None)
                    else:
                        # Cap at the new shortened duration
                        new_dur = (new_finish - item_start).total_seconds()
                        new_src["out_point_ms"] = int(new_dur * 1000)
                    new_filler["source"] = new_src
                    new_items.append(new_filler)
                    # Insert bumper
                    new_items.append({
                        "id": f"bumper-{channel_num}-{target_hhmm}",
                        "start": new_finish.isoformat(timespec='milliseconds'),
                        "finish": item_finish.isoformat(timespec='milliseconds'),
                        "source": {"source_type": "local", "path": str(bumper_path)}
                    })
                    spliced += 1
                    i += 1
                    continue
                elif src_type == "lavfi":
                    # Lavfi filler: trim its duration via params, then add bumper
                    item_start = parse_iso(it["start"])
                    item_finish = parse_iso(it["finish"])
                    item_dur = (item_finish - item_start).total_seconds()
                    if item_dur < bumper_dur_s + 5:
                        # Replace entirely
                        new_items.append({
                            "id": f"bumper-{channel_num}-{target_hhmm}",
                            "start": it["start"],
                            "finish": it["finish"],
                            "source": {"source_type": "local", "path": str(bumper_path)}
                        })
                        spliced += 1
                        i += 1
                        continue
                    new_finish = item_finish - timedelta(seconds=bumper_dur_s)
                    new_dur = (new_finish - item_start).total_seconds()
                    # Update lavfi params duration
                    import re
                    params = src.get("params", "")
                    new_params = re.sub(r"d=\d+(?:\.\d+)?", f"d={int(new_dur)}", params)
                    new_filler = dict(it)
                    new_filler["finish"] = new_finish.isoformat(timespec='milliseconds')
                    new_filler["source"] = {"source_type": "lavfi", "params": new_params}
                    new_items.append(new_filler)
                    new_items.append({
                        "id": f"bumper-{channel_num}-{target_hhmm}",
                        "start": new_finish.isoformat(timespec='milliseconds'),
                        "finish": item_finish.isoformat(timespec='milliseconds'),
                        "source": {"source_type": "local", "path": str(bumper_path)}
                    })
                    spliced += 1
                    i += 1
                    continue
        new_items.append(it)
        i += 1

    if spliced > 0 and not dry_run:
        playout["items"] = new_items
        pf.write_text(json.dumps(playout, indent=2))
    return (spliced, len(bumpers))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (defaults to today, local).")
    ap.add_argument("--only-channel")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    today = datetime.now() if not args.date else datetime.strptime(args.date, "%Y-%m-%d")
    bumper_root = BUMPERS_ROOT / today.strftime("%Y-%m-%d")
    if not bumper_root.is_dir():
        print(f"No bumpers folder for {today.strftime('%Y-%m-%d')}", file=sys.stderr)
        return 1

    total_spliced = 0
    total_bumpers = 0
    for chan_dir in sorted(bumper_root.iterdir()):
        if not chan_dir.is_dir():
            continue
        n = chan_dir.name
        if args.only_channel and n != args.only_channel:
            continue
        spliced, count = splice_channel(n, today, args.dry_run)
        if count > 0:
            print(f"  ch{n}: {spliced}/{count} bumpers spliced{' (dry-run)' if args.dry_run else ''}")
        total_spliced += spliced
        total_bumpers += count
    print(f"\nTotal: {total_spliced}/{total_bumpers} bumpers spliced into playouts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
