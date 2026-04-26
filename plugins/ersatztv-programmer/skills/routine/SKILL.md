---
name: routine
description: Run the nightly daily-refresh routine for an ErsatzTV Next stack — the full procedure that rebuilds every channel's playout, scores via the director, renders bumpers, regenerates M3U + XMLTV, and (after final-auditor PASS) refreshes Jellyfin's guide. Invoke as `/ersatztv-programmer:routine` from a CronCreate-scheduled job, a launchd-fired headless `claude --print` call, or manually for an ad-hoc rebuild. The skill is self-contained — it discovers the stack, batches channel work, handles Primetime + special-case channels inline, and never assumes prior context.
---

# Daily refresh routine

You are running the nightly daily-refresh routine for the user's ErsatzTV Next stack. This skill is the canonical procedure — the same one that fires from any scheduled trigger (CronCreate session-bound, launchd via `claude --print`, or `/ersatztv-programmer:routine` manually). Every fire follows this skill.

## What "running the routine" means in plain terms

By the time you finish, every channel in the lineup has a freshly written calendar-day-aligned playout for today, every primetime hour boundary has a rendered bumper card, the M3U and XMLTV files are up to date with the new programming, the director has scored every channel's work and flagged top/bottom performers, the final-auditor has verified the whole stack is internally consistent, and Jellyfin's guide has been refreshed so the user's TV clients see the new schedule when they next browse the guide.

If anything blocks (a channel-auditor REJECT after retries, the final-auditor BLOCK, a tool error), you stop short of the Jellyfin refresh and report the punch list — better to leave yesterday's guide live than push a corrupt one.

## Inputs you can rely on (the skill discovers everything else)

The stack root defaults to `~/ersatztv-stack/` (override via `STACK_DIR` env var). All other paths derive from the stack root:

| Path | Purpose |
| :--- | :--- |
| `${STACK_DIR}/config/ersatztv-next/lineup.json` | The 75-channel lineup (or whatever count the user has). Source of truth for which channels exist. |
| `${STACK_DIR}/config/ersatztv-next/channels/{N}/channel.json` | Per-channel config (playout folder, transcoding profile). |
| `${STACK_DIR}/config/ersatztv-next/channels/{N}/playout/*.json` | Per-channel playout files. Daily routine writes a new one per night. |
| `${STACK_DIR}/state/director-picks.json` | Director's daily leaderboard + reward queue. |
| `${STACK_DIR}/state/ratings-history.json` | Director's 30-day rolling history. |
| `${STACK_DIR}/state/{N}/state.json` | Per-channel state (weekly-progression cursors, slot anchors, etc.). |
| `${STACK_DIR}/tools/build-bumpers.py` | Voice-driven bumper renderer (reads `bumper-voices.json`). |
| `${STACK_DIR}/tools/build-m3u.py` | Sanitized M3U generator. |
| `${STACK_DIR}/tools/build-xmltv.py` | XMLTV generator (auto-loads `state/director-picks.json`). |
| `${STACK_DIR}/tools/playout-validate.py` | Per-channel schema validator (used by channel-auditor). |
| `${STACK_DIR}/logs/routine-{YYYYMMDD}.log` | Where headless-mode stdout is captured. Skill itself doesn't write logs; the launchd plist does the redirect. |

The Jellyfin SQLite DB defaults to `~/Library/Application Support/jellyfin/data/jellyfin.db` on macOS (override via `JF_DB`). Open it with `?immutable=1` so a half-running Jellyfin doesn't get its WAL stomped.

The Jellyfin token lives in the user's shell env as `JELLYFIN_TOKEN` (set by `/setup` Step 1). If you can't find it, surface that in the report and skip the guide refresh — never invent or guess a token.

## Hard rules — these are non-negotiable

These are repeated across the plugin's other docs (`schedule` skill, `programmer` agent, `final-auditor` agent) because every entry point must enforce them:

1. **Calendar-day windowing.** Each playout file covers `[today 01:00:00 local, tomorrow 00:00:00 local)`. Never write rolling 24h windows from "now."
2. **Filler-hour rule.** `max(items[*].finish) ≤ today 23:59:59 local`. The 12:00 AM → 01:00 AM hour is filler-only (branded music, lavfi, voice bumpers — never scripted episodes or feature films). Programs must not bleed past midnight.
3. **Claude curates, scripts don't.** Playout content selection is done with Claude's judgment via the `subprogrammer` and `channel-auditor` agents. Mechanical work (M3U / XMLTV / bumper rendering / validation) is scripted; curation never is.
4. **Final-auditor ALWAYS runs.** No path skips it, even if every per-channel build APPROVED on the first try. It catches cross-cutting issues the per-channel auditor cannot.
5. **No Jellyfin refresh on BLOCK.** If the final-auditor returns BLOCK, do not call `/LiveTv/Guide/Refresh`. Surface the punch list and stop.

## Procedure

### Phase 0 — Bootstrap

1. Resolve `STACK_DIR` and verify the lineup exists. If `${STACK_DIR}/config/ersatztv-next/lineup.json` is missing, return `BLOCK: stack not initialized — run /ersatztv-programmer:setup first` and stop.
2. Compute today's date in the user's local timezone. Use the system's local zone (do not hardcode). Today's window: `[today 01:00:00 ±OFFSET, tomorrow 00:00:00 ±OFFSET)`.
3. Read the lineup and bucket each channel: `core | rotating | music | live | experimental | holiday`. The bucket comes from the channel's number range OR an explicit `bucket` field in the lineup if present. Default ranges: 1–34 = core, 35 = PPV, 100–109 = rotating, 200–209 = music, 300–309 = live, 900–909 = experimental, 31/32/33 = holiday.
4. Determine the active bucket list for today:
   - **Skip out-of-season holiday channels.** Halloween (31): only emit Sep 15 – Oct 31. Thanksgiving (32): only emit week before Thanksgiving through Thanksgiving Day. Christmas (33): only emit Dec 1 – Dec 26. Outside those windows, the channel is excluded entirely from this run and from M3U/XMLTV emission. (Persisted handling: keep `lineup.json` complete; the M3U/XMLTV generators read the same season rules and skip silently.)
5. Create `${STACK_DIR}/state/` if missing. Note the routine start time for the summary report.

### Phase 1 — Per-channel programming (single-agent inline)

**Architecture note (2026-04-26):** the plugin originally specified subagent fan-out (orchestrator → per-channel subprogrammers + auditors). Claude's current runtime doesn't honor `Agent(...)` permissions in non-root subagents, so fan-out is shelved until plist migration. For now, the routine runs as ONE agent — typically main Claude when CronCreate fires, or a single subagent when launchd fires `claude --print`. That single agent plays every role (subprogrammer, channel-auditor, director, final-auditor) inline. See `feedback_agent_team_pattern` memory for the full rationale.

Process channels in this order. Each channel: build playout → validate → self-audit → write to disk. Move on.

**Bucket order** (cheapest libraries first, most contextful last):

1. **Live (300–309)** — single-item `http` source per channel, URL from `state/{N}/state.json`. Window covers 7 days. Reuse last week's file if URL unchanged and window still covers today (just rename the file with the new window). ~10 seconds per channel.
2. **PPV (35)** — dark slate today (`source_type: lavfi`, `params: color=c=0x101010:s=1280x720:d=86400`) unless today is the exact day a real ECW PPV aired in the channel's pinned year. On a hit, slot the PPV in primetime. ~5 seconds.
3. **Music (200–209)** — continuous shuffle-in-order from the channel's brand-matched genre pool (Jellyfin SQL query: `Type='Audio' AND Genres LIKE ?`). Single 24h "loop" file with item starts at clean :00/:30 boundaries; runtime aggregates to fill 23 hours of programmed music + 1 hour of branded music filler in the 12am–1am slot. ~30 seconds per channel.
4. **Rotating (100–109)** — themed by `current_theme` in `state/{N}/state.json` (rotates monthly). Fill 23 hours with theme-matched content; 12am–1am with theme-matched docs/shorts. ~60 seconds per channel.
5. **Core ex-Primetime ex-PPV (2–30, 34)** — bucket-genre channels with optional `ratings-chasing` primitive (named tentpoles + off-season pools in `state/{N}/state.json.slot_anchors`). For each channel: detect today's weeknight slot anchors → in-season → tentpole owns 9 PM, off-season → rotate among `off_season_pool` based on longest-ago `last_aired_in_slot`. Fill other dayparts with theme-matched content. ~60-90 seconds per channel.
6. **Experimental (900–909)** — `weekly-reinvention` primitive: each Monday pick a new format (one-week-only theme, marathon, alphabetical stunt, etc.) recorded in `state/{N}/state.json.current_format`; rest of the week, carry on with that format. ~60 seconds per channel.
7. **Primetime (Channel 1)** — last, because it's the densest. Recreate verbatim from `project_ersatztv_primetime_canonical` memory: Monday/Tuesday/Wednesday/Thursday/Friday/Saturday/Sunday blocks, `weekly-progression` on *The Leftovers* Tuesday-21:00 + similar slots, deco "Nighttime" filling between blocks. Maintain `state/1/state.json` for per-show episode cursors. ~3-5 minutes.

**Holiday channels (31, 32, 33)** — skip if out of season today (Halloween: Sep 15 – Oct 31; Thanksgiving: week before through Thanksgiving Day; Christmas: Dec 1 – Dec 26). Don't emit playout, don't include in M3U/XMLTV.

**Per-channel inline procedure:**

For each channel in scope:

```
a. Read channel.json + state/{N}/state.json (if exists).
b. Determine today's slot anchors / current_theme / current_format / weekly-progression cursors.
c. Query Jellyfin SQLite (read-only, ?immutable=1) for candidate items. Use LIMIT clauses; don't dump full results into context.
d. Build the 24-h items array with curatorial judgment:
   - Calendar-day window [today 01:00:00, tomorrow 00:00:00).
   - Filler-hour rule: max(items[*].finish) ≤ today 23:59:59. The 12am–1am hour is filler.
   - Daypart adherence: short items daytime, hour-longs primetime, features in primetime block.
   - Tentpoles in their season window own 9 PM; off-season rotation otherwise.
e. Write the playout JSON to channels/{N}/playout/{compact_start}_{compact_finish}.json via Write tool.
f. Validate via Bash: tools/playout-validate.py <path>. If fail: re-build (max 1 retry).
g. Update state/{N}/state.json with advanced cursors / last_aired_in_slot / etc.
h. Record one-line outcome: "ok N items" / "short M items" / "failed: reason".
```

**Context-budget discipline:** don't dump full library queries into context; project just `Path, RuntimeTicks, Name, SeriesName, IndexNumber` columns. Don't re-read your own JSON writes — trust validation. Per-channel target: ~5-10k context tokens. 75 channels × 8k = ~600k; plus Phase 2 overhead ~700-800k total. Tight on Opus 4.7 1M.

### Phase 2 — Director scoring (inline)

Switch into the **director role** (read `agents/director.md` in the plugin for the persona spec). Don't spawn a separate agent — do the scoring inline with the same Claude conversation.

For each channel that completed Phase 1 successfully:

- Read its playout JSON.
- Score 0–100 against seven signals (tentpole-in-primetime, daypart adherence, novelty vs. last 14 days, newly-added surfacing, voice-bumper coverage, source-path resolution, filler-hour compliance). Apply history weights from `state/ratings-history.json` (anti-streak –3 if top-3 yesterday; comeback floor +3 if bottom-3 yesterday).
- Special-case scoring: live channels = `null` (out of competition); music channels score against three signals only (daypart, novelty, voice coverage); PPV dark-slate = `null`; out-of-season holiday = excluded.

Compute the leaderboard. Pick top-3 (rewards: `[Editor's Pick]` prefix in XMLTV primetime, newly-added priority claim recorded in `newly_added_queue`) and bottom-3 (warnings: "needs love" flag, revoke prior newly-added claim).

Write `state/director-picks.json` with the leaderboard + one-line directors_note in your voice. Append today's leaderboard to `state/ratings-history.json` (auto-prune to last 30 days).

Capture the directors_note for the summary report.

### Phase 3 — Render bumpers

Run `${STACK_DIR}/tools/build-bumpers.py` (no flags — it auto-resolves today's date and reads `bumper-voices.json`). The renderer emits 15–18s MP4s under `${STACK_DIR}/bumpers/{YYYY-MM-DD}/{N}/{HHMM}-{kind}.mp4` mixing personality / up-next / block-summary types per primetime hour.

After rendering completes, splice each rendered bumper into the channel's playout as a `local` source replacing the trailing 15s of music filler before the on-the-hour program. Re-run `playout-validate.py` after the splice for each modified channel.

### Phase 4 — Generate M3U + XMLTV

In order:

1. `${STACK_DIR}/tools/build-m3u.py` — sanitized M3U with `tvg-id == tvg-chno`. Writes `serve/channels.m3u`.
2. `${STACK_DIR}/tools/build-xmltv.py` — auto-loads `state/director-picks.json` if present. Top-3 channels' primetime `<title>` entries get `[Editor's Pick] ` prefix. Writes `serve/xmltv.xml`.

Both must succeed before continuing. If either fails, treat the whole routine as BLOCK.

### Phase 5 — Final-auditor (ALWAYS, inline)

Switch into the **final-auditor role** (read `agents/final-auditor.md` for the full check list). Don't spawn a separate agent — do the audit inline with the same Claude conversation.

Run the 10-section audit list from `agents/final-auditor.md`:

1. Lineup integrity (configs resolve, no dup numbers/names).
2. Per-channel playout exists for today.
3. Filler-hour rule (`max(items[*].finish) ≤ today 23:59:59` per channel).
4. Time / contiguity (RFC 3339, `start < finish`, contiguous, non-overlapping).
5. Source-path existence (every `local` path exists; every `http` URI well-formed; every `lavfi` params non-empty).
6. M3U + XMLTV consistency (channel counts match; `tvg-id == tvg-chno`; `<channel id>` present per M3U entry).
7. Bumper coverage (warnings only — non-blocking).
8. Server reachability (advisory).
9. State drift (parses; `last_refresh_date` is today/yesterday).
10. Plugin sole-maintainer hygiene (tools parse).

Determine **PASS** vs **BLOCK**:

- Critical failures (sections 1–6) → BLOCK.
- Warnings only (sections 7–10) → PASS, but list warnings.

If **PASS** → continue to Phase 6.
If **BLOCK** → skip Phase 6. Include the full punch list in the summary report. Stop.

### Phase 6 — Refresh Jellyfin guide (only if Phase 5 PASS)

Jellyfin's `/LiveTv/Guide/Refresh` endpoint does NOT exist on current builds (returns 404). Use the **ScheduledTasks** API with the actual task IDs:

| Task | ID | What it does |
| :--- | :--- | :--- |
| Refresh Guide | `bea9b218c97bbf98c5dc1303bdb9a0ca` | Pulls XMLTV from configured tuner sources, repopulates the EPG. |
| Refresh Channels | `0c9ee3a88fc15547c6852205480da1fd` | Re-reads the M3U tuner, picks up new/removed channels. |

```bash
JF_BASE="${JF_BASE:-http://localhost:8096}"   # native Jellyfin (Docker users: 18096)
TOK="${JELLYFIN_TOKEN:?set in shell rc}"
curl -sf -m 30 -X POST "$JF_BASE/ScheduledTasks/Running/bea9b218c97bbf98c5dc1303bdb9a0ca" -H "X-Emby-Token: $TOK"
curl -sf -m 30 -X POST "$JF_BASE/ScheduledTasks/Running/0c9ee3a88fc15547c6852205480da1fd" -H "X-Emby-Token: $TOK"
```

Both should return HTTP **204 No Content**. The actual scan runs asynchronously inside Jellyfin (~10–30 s); the curl just queues the task.

**Fire BOTH every routine fire** — the channels refresh picks up M3U changes (e.g., a holiday channel coming back in season), and the guide refresh ingests the regenerated XMLTV. Skipping one leaves the EPG stale.

If the user's Jellyfin runs on a non-default port, override `JF_BASE` in their shell. The plugin's `setup` skill captures this at install time.

If a curl returns non-zero or non-2xx, the guide refresh failed but the underlying schedule is fine — log the failure and surface it; don't treat the routine as BLOCK.

### Phase 7 — Report

Return one summary message. Format:

```text
ErsatzTV daily refresh — 2026-04-26 — completed in 23m
Stack: /Users/zach/ersatztv-stack/

Phase 1 — programming (75 channels):
  inline:
    ok   1 Primetime          24h, 18 items, weekly-progression: Tue 21:00 → Leftovers S01E04
    ok  35 Time Machine PPV   dark slate (no historic PPV today)
    ok 300 CBS Philadelphia   live http, 7d window
    ... (other inline)
  agent-team:
    ok   2 Background         24h, 31 items
    ok   3 Friends            24h, 19 items
    short 53 Western          12h, 6 items — Jellyfin returned only 6 westerns
    failed 91 Format Lab      auditor: 2 retries; gap at 13:45-14:00 in attempt 2

Phase 2 — director:
  Top 3:    ch1 Primetime (94), ch12 Drama (89), ch24 HBO Style (87)
  Bottom 3: ch53 Western (54), ch201 90s MTV (52), ch102 Studio Spotlight (49)
  Note: "Channel 1 is locked in. Channel 53, three days of the same five
         westerns — pick it up."

Phase 3 — bumpers: 64 rendered (38 deadpan / 19 up-next / 7 block-summary)
Phase 4 — XMLTV: 75 channels, 1,847 programmes, [Editor's Pick] on ch1, ch12, ch24
Phase 5 — final-auditor: PASS
Phase 6 — Jellyfin refresh: 200 OK

Next routine fires: 2026-04-27 01:07 local (cron)
```

If BLOCK at Phase 5, replace Phase 5's `PASS` with the punch list and replace Phase 6 with `SKIPPED — auditor BLOCK`.

## Idempotency notes

The routine should be safe to run mid-day (e.g. user manually re-fires after fixing a config). Specifically:

- Re-running overwrites today's playout files, today's bumpers, today's M3U/XMLTV, today's director-picks.json. State files (per-channel `state.json`) are merged: existing slot anchors and weekly-progression cursors are preserved unless they point at exhausted shows.
- The director's `ratings-history.json` is append-only by date; a same-day re-run REPLACES today's entry (not appends).
- The Jellyfin guide refresh is idempotent on Jellyfin's side.

## Failure surface area + fallback behavior

| What fails | Routine behavior |
| :--- | :--- |
| `lineup.json` missing | BLOCK at Phase 0; tell user to run `/ersatztv-programmer:setup`. |
| Jellyfin DB locked or missing | BLOCK at Phase 1; the subagents need it to query the library. Surface and stop. |
| 1–5 agent-team channels return `failed` after retries | Continue (partial success is fine). The director scores only the channels that succeeded. Final-auditor flags missing playouts as warnings, not BLOCK. |
| Bumper render errors on some channels | Continue. Final-auditor flags missing bumpers as warnings, not BLOCK. |
| build-xmltv.py errors | BLOCK at Phase 4. The XMLTV is the user-facing guide; partial XMLTV is worse than yesterday's. |
| Final-auditor BLOCK | Skip Phase 6. Return punch list. |
| Jellyfin refresh curl fails | Log + continue; the schedule is still correct, the user can refresh manually. |

## When NOT to use this skill

- Single-channel ad-hoc programming — use the `schedule` skill directly via `/ersatztv-programmer:program {N}`.
- First-time setup — use `/ersatztv-programmer:setup`. The routine assumes the stack is already initialized.
- Library auditing — use `/ersatztv-programmer:audit`.
- Library-thin gap analysis — use `/ersatztv-programmer:librarian` (the routine spawns it automatically when channels return `short`, but the user-driven entry point is the librarian skill itself).
