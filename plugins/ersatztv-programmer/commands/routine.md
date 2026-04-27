---
name: ersatztv-routine
description: Run the daily refresh routine for every channel — rebuild today's playouts, score via director, render bumpers, regenerate M3U + XMLTV, refresh Jellyfin's guide. The same procedure the cron fires nightly at 1:07 AM. Invoke manually for an ad-hoc full-stack rebuild.
disable-model-invocation: true
---

Invoke the `ersatztv-programmer:routine` skill to run the full daily refresh end-to-end.

The skill is the canonical procedure — Phase 0 through Phase 6:

| Phase | What it does |
| :--- | :--- |
| 0 | Bootstrap: discover lineup, set today's calendar-day window, classify channels by bucket. |
| 1 | Per-channel block-aware rebuild. Reads `state/{N}/quarter-plan.json` for anchored slots; reads `state/{N}/state.json` for episode cursors. Slots next-in-queue episode of each anchor on its scheduled weeknight. Fills non-anchored slots from the channel's filter pool with no back-to-back same-series. Persists cursor advancement. |
| 2 | Director scores each channel 0-100 against seven signals (tentpole-hit, daypart adherence, novelty, newly-added, voice coverage, source-paths, filler-hour). Writes `state/director-picks.json` + appends to `ratings-history.json`. |
| 3 | Render bumper PNGs (per-channel personality cards) + splice into music filler before clean-clock primetime boundaries. Music plays under the deco card for the full filler period. |
| 4 | Regenerate `channels.m3u` (`tvg-id == tvg-chno`) + `xmltv.xml`. Filler items merge into single "Station Break" programme entries. |
| 4.5 | `audit-content.py` — hard-fails on holiday content on non-holiday channels, blacklisted series, off-genre. Surgically replaces violators with non-violating alternatives. |
| 5 | Final-auditor — verifies the whole stack is consistent. Returns PASS or BLOCK with punch list. |
| 6a | Restart ETV Next (forces fresh playout reload), then stream-probe every channel via `tools/probe-streams.py`. |
| 6b | On PASS: refresh Jellyfin's guide via the ScheduledTasks API. On BLOCK: skip — better stale-but-correct than fresh-but-broken. |

Output: one summary message with per-channel status, director's note, audit result, Jellyfin refresh outcome.

Runtime: 5-10 minutes for a 75-channel lineup. Invoke during the user's dedicated 12am-1am filler hour (the routine inserts black/lavfi-color filler at the start of the day's playout exactly so this hour is low-stakes).

For ad-hoc one-channel rebuilds (mid-day), use `/ersatztv-program {channel}` instead — it's faster and doesn't churn ETV.

For strategic re-planning (anchor rotation, hot-new-arrival promotion), run `/ersatztv-programmer:plan` first, then `/ersatztv-programmer:routine`.
