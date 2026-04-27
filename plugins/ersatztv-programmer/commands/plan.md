---
name: ersatztv-plan
description: Run the per-channel quarterly planner — surveys the Jellyfin library, picks anchor TV series for primetime weeknight slots, builds 12-week strategic plans per channel. Outputs state/{N}/quarter-plan.json files that the daily routine consumes. Run once a week (Sunday) or whenever a major new show arrives in the library.
disable-model-invocation: true
---

Run the studio's strategic-planning step. For every channel in the user's lineup (minus holiday/PPV/live/music — those don't need anchors), invoke the `channel-planner` agent (or fall back to the `quarter-planner.py` bootstrap script) to:

1. Survey the entire Jellyfin library for content matching the channel's metadata filter (`tools/channel-genres.json`).
2. Identify anchor TV series — multi-season, ≥ 13 episodes — that can hold a fixed primetime weeknight slot for their full run.
3. Map a 12-week plan: which anchor owns Tue 9 PM, Wed 9 PM, Thu 9 PM, etc.
4. Identify hot newly-added items (last 7 days) and tag them for promotion.
5. Persist `state/{N}/quarter-plan.json` per channel.

Hard rules the planner must honor:
- **Don't trust genre tags blindly.** Many shows are mistagged (King of the Hill is comedy regardless of how Jellyfin classified it). The agent reasons about each item's actual identity — title, era, network of origin, runtime, cast — not just metadata flags.
- **Anchors hold their slot for the FULL RUN.** Don't rotate mid-season for variety. Only swap when episodes_remaining hits 0 or a hot new arrival displaces via director scoring.
- **No back-to-back same-series across slots.** Different weeknights get different anchors.
- **Holiday content stays on holiday channels.** Even if a Christmas episode arrives in newly-added queue, never promote it to a non-holiday channel.

After running, surface a one-line summary per channel: anchor count, top anchor name + episodes remaining, newly-added flagged for promotion.

Procedure:

1. Run `${STACK_DIR}/tools/quarter-planner.py --force` for the bootstrap pass.
2. For higher-quality placements (Claude judgment instead of genre-tag matching), spawn the `channel-planner` agent per channel — passing the channel number, channel-genres.json rules, the survey output, and the director's last-30-day score history. Let the agent re-write the per-channel plan with its judgment.
3. Surface the summary; note any channels where the planner couldn't find anchors (library doesn't have enough content matching the channel's filter).

The output (per-channel `quarter-plan.json`) is consumed by `/ersatztv-programmer:routine` daily. The planner does NOT run the daily rebuild — it just sets the strategy.
