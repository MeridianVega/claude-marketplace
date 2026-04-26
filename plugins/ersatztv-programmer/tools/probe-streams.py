#!/usr/bin/env python3
"""probe-streams.py — Stream-validation probe for every channel in the lineup.

For each channel, hits the prewarm sidecar's `/iptv/{N}/live.m3u8` URL and
asserts:

  - HTTP 200 within PROBE_TIMEOUT_S (default 35s — enough for cold ETV
    session start + first 2 HLS segments).
  - Response body contains at least 2 segment refs (.ts).

Returns exit code 0 if every channel passes, 1 otherwise. Prints a one-line
status per channel, then a summary.

This is what the daily-routine final-auditor calls before triggering the
Jellyfin guide refresh — a JSON-valid playout doesn't mean the channel
actually streams.

Usage:
    probe-streams.py [--prewarm http://localhost:18407] [--timeout 35]
                     [--channel N] [--skip 31,32,33] [--parallel 4]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

STACK_DIR = Path(os.environ.get("STACK_DIR", str(Path.home() / "ersatztv-stack")))
LINEUP = STACK_DIR / "config/ersatztv-next/lineup.json"


def fetch(url: str, timeout: float) -> tuple[int, bytes]:
    try:
        with urllib_request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read()
    except HTTPError as e:
        return e.code, b""
    except (URLError, TimeoutError, ConnectionError, OSError):
        return 0, b""


def probe_channel(channel_num: str, name: str, prewarm: str, timeout: float) -> dict:
    """Probe one channel; return a status dict."""
    url = f"{prewarm}/iptv/{channel_num}/live.m3u8"
    t0 = time.monotonic()
    status, body = fetch(url, timeout)
    elapsed = time.monotonic() - t0
    seg_count = body.count(b".ts")
    ok = status == 200 and seg_count >= 2
    return {
        "channel": channel_num,
        "name": name,
        "status_code": status,
        "elapsed_s": round(elapsed, 1),
        "segments": seg_count,
        "ok": ok,
        "reason": (
            "ok" if ok else
            f"http {status}" if status != 200 else
            f"only {seg_count} segments after {elapsed:.1f}s"
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prewarm", default="http://localhost:18407",
                    help="Prewarm sidecar base URL.")
    ap.add_argument("--timeout", type=float, default=35.0)
    ap.add_argument("--channel", help="Probe only this channel number.")
    ap.add_argument("--skip", default="31,32,33",
                    help="Comma-separated channels to skip (default: out-of-season holiday).")
    ap.add_argument("--parallel", type=int, default=1,
                    help="Concurrent probes. Default 1 to avoid CPU contention with active viewers — each probe costs an ffmpeg session that lingers ~60s before idle-timeout.")
    ap.add_argument("--settle-seconds", type=float, default=3.0,
                    help="Sleep between sequential probes to let prior session start idling (default 3s; only applies when parallel=1).")
    ap.add_argument("--json", action="store_true", help="Output JSON-only.")
    args = ap.parse_args()

    if not LINEUP.is_file():
        print(f"lineup.json not found at {LINEUP}", file=sys.stderr)
        return 2

    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    with open(LINEUP) as f:
        channels = json.load(f)["channels"]
    if args.channel:
        channels = [c for c in channels if c["number"] == args.channel]
    else:
        channels = [c for c in channels if c["number"] not in skip]

    results = []
    if args.parallel == 1:
        # Sequential mode — gentle on CPU; safe to run while clients are watching.
        for i, c in enumerate(channels):
            r = probe_channel(c["number"], c["name"], args.prewarm, args.timeout)
            results.append(r)
            if not args.json:
                marker = "ok" if r["ok"] else "FAIL"
                print(
                    f"  ch{r['channel']:>4} {r['name']:<22} "
                    f"{marker:>4} ({r['elapsed_s']}s, {r['segments']} segs) {r['reason']}",
                    flush=True,
                )
            if i + 1 < len(channels) and args.settle_seconds > 0:
                time.sleep(args.settle_seconds)
    else:
        # Parallel mode — faster but may saturate CPU on small hosts.
        with ThreadPoolExecutor(max_workers=args.parallel) as pool:
            futures = {
                pool.submit(probe_channel, c["number"], c["name"], args.prewarm, args.timeout): c
                for c in channels
            }
            for fut in as_completed(futures):
                r = fut.result()
                results.append(r)
                if not args.json:
                    marker = "ok" if r["ok"] else "FAIL"
                    print(
                        f"  ch{r['channel']:>4} {r['name']:<22} "
                        f"{marker:>4} ({r['elapsed_s']}s, {r['segments']} segs) {r['reason']}",
                        flush=True,
                    )

    failed = [r for r in results if not r["ok"]]
    if args.json:
        print(json.dumps({
            "total": len(results),
            "passed": len(results) - len(failed),
            "failed": [r["channel"] for r in failed],
            "results": sorted(results, key=lambda r: int(r["channel"])),
        }, indent=2))
    else:
        print(f"\n{len(results) - len(failed)}/{len(results)} channels stream OK")
        if failed:
            print(f"FAILED: {[r['channel'] for r in failed]}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
