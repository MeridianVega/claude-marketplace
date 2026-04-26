---
name: ersatztv-schedule
description: Procedures for programming an ErsatzTV Next channel — building, validating, and writing playout JSON files from a media library. Loads when the user asks to plan, schedule, build, modify, or rebuild a channel; or when "playout JSON," "marathon," "channel programming," or similar phrases appear.
---

# ErsatzTV Next channel programming

You are programming channels for [ErsatzTV Next](https://github.com/ErsatzTV/next), a transcoding/streaming engine that consumes **playout JSON files**. This skill carries the schema, file layout, and write procedure.

The user's request defines the *shape* of the channel; you choose the right structure for the playout JSON. There are no fixed channel types — common patterns (marathons, daily blocks, external live URLs, date-range gates) are covered in `examples/playouts/` for reference.

## Three-tier configuration

ErsatzTV Next reads three levels of config:

1. **`lineup.json`** — top-level. Server bind address, port, output folder, and a list of channels (each pointing at a `channel.json`).
2. **`channel.json`** — per channel. FFmpeg paths, normalization (codec/resolution/bitrate), and a `playout.folder` pointing at where playout JSON files live.
3. **Playout JSON files** — under each channel's playout folder. Files are named `{start}_{finish}.json` using **compact ISO 8601 with no separators**, e.g. `20260413T000000.000000000-0500_20260414T002131.620000000-0500.json`. The channel worker locates the right file by matching the current time to the file name window.

When you write a playout, you write one file per time window. Long windows (multi-week marathons) can be a single file; daily plans are typically one file per day.

## Playout JSON shape

Top-level:

```json
{
  "version": "https://ersatztv.org/playout/version/0.0.1",
  "items": [ /* PlayoutItem array */ ]
}
```

Each `PlayoutItem`:

```json
{
  "id": "stable-id-unique-within-playout",
  "start": "2026-04-13T20:00:00.000-05:00",
  "finish": "2026-04-13T22:00:00.000-05:00",
  "source": { /* one of LocalSource, LavfiSource, HttpSource */ },
  "tracks": { /* optional per-track overrides */ }
}
```

Source variants (discriminated by `source_type`):

```json
{ "source_type": "local", "path": "/abs/path/to/file.mkv",
  "in_point_ms": 0, "out_point_ms": null }
```

```json
{ "source_type": "lavfi", "params": "anullsrc=channel_layout=stereo:sample_rate=48000:d=10" }
```

```json
{ "source_type": "http", "uri": "https://example.com/stream.m3u8" }
```

`tracks` is optional. If omitted, the server picks the first video and audio stream from the source. Use `tracks` to select alternate audio (e.g. dub track) or to override the source per-track:

```json
"tracks": {
  "video": { "stream_index": 0 },
  "audio": { "stream_index": 1 },
  "subtitle": { "stream_index": 2 }
}
```

The full schema is at `https://github.com/ErsatzTV/next/blob/main/schema/playout.json`. The bundled `reference` skill in this plugin pins a verbatim copy.

## Schema rules

These are enforced by the schema and must hold:

- `version` is required at the top level. Use `"https://ersatztv.org/playout/version/0.0.1"` until a newer version is published.
- Each `PlayoutItem` requires `id`, `start`, and `finish`.
- Each `id` is unique within the playout file.
- An item must supply media for its tracks. Either set `source` at the item level (used by every track that doesn't override it), or set `tracks` with per-track `source`s, or both.
- `start` and `finish` are **RFC 3339** date-times. Use an explicit numeric timezone offset (e.g. `-05:00`); avoid naive timestamps.

## Conventions to follow

The schema does not enforce these, but ErsatzTV Next streams break if you ignore them:

- **Items should be contiguous in time.** `finish` of item *N* equals `start` of item *N+1*. Gaps cause the channel to go dark; overlaps drop the earlier item. If the user wants intentional idle filler, emit a `lavfi` item to fill the gap.
- **Source paths must be absolute and reachable from inside the ErsatzTV Next process.** Matters in Docker — paths must be valid in the container's filesystem, not the host's. Cross-check the channel's `channel.json` mount mapping.
- **Item duration should match the source's duration.** To trim, set `in_point_ms` / `out_point_ms` on the local source rather than lying about the finish time.

## File naming

Files use **compact ISO 8601 in both date and time portions** — no `:` and no `-` anywhere except as the timezone-offset sign character. The literal example in the schema:

```
20260413T000000.000000000-0500_20260414T002131.620000000-0500.json
```

Breakdown:

- `20260413` — date YYYYMMDD, no separators.
- `T` — date/time separator.
- `000000.000000000` — time HHMMSS.fffffffff, no `:` separators.
- `-0500` — timezone offset, no `:`.
- `_` — separator between start and finish.
- `.json` — extension.

The channel worker locates the right file by matching the current wall-clock time to the `{start}_{finish}` window in the filename. If multiple files cover overlapping windows, behavior is undefined; emit non-overlapping windows.

## Channel-level `virtual_start`

`channel.json` → `playout.virtual_start` (RFC 3339 string, optional) lets the channel pretend the playout window started at a different wall-clock time. Use it for:

- **Time-shifting** a marathon to start later than the file timestamps suggest.
- **Looping** by re-anchoring the same playout window to "now" each cycle.

Set this only when the user asks for time-shift behavior; default `null` is fine for normal channels.

## Procedure for building a channel

When the user asks for a channel, follow this order. Skip steps that are already known (e.g. on `/reschedule` you already have channel metadata).

1. **Resolve the request.** Ask only what you can't infer. For each channel you need:
    - Channel number (string, e.g. `"42"`).
    - Channel name (display name).
    - The "shape" — what plays when. This is freeform; common shapes:
        - *Marathon*: one collection played in order, looping.
        - *Random*: shuffled / shuffle-in-order across a collection.
        - *Daily schedule*: per-day blocks at specific times.
        - *Live mirror*: external HLS/MPEG-TS/RTMP URL.
        - *Date-gated*: only on-air during a date range.
2. **Resolve content via the configured media server MCP.** Query Jellyfin/Plex/Emby for the items that satisfy the request. Fetch enough metadata to make ordering decisions: file path, duration, release date, season/episode numbers (for shows). Cache the response — multiple items often need the same query.
3. **Plan the time window.** Default: 24 hours from local midnight in the user's timezone. For marathons that exceed a day, span multiple days in one file. For daily schedules, one file per day starting at local midnight.
4. **Build the items array.** For each scheduled item:
    - Compute `start` and `finish` from the file's duration. If the request demands a fixed end time and the durations don't fit cleanly, decide: trim with `out_point_ms`, drop the overage item, or pad with a `lavfi` filler. Surface the choice to the user only if it's ambiguous.
    - Use `local` sources for filesystem media. Convert host paths to container paths if Next runs in Docker (use `channel.json` mount mapping).
    - Use `http` sources for live mirrors.
    - Use `lavfi` sources for synthetic fill (silence + black, "be right back" cards, etc.).
5. **Write the file.** Path: `{channel_playout_folder}/{compact_start}_{compact_finish}.json`. Compact ISO 8601: strip `:` and `-` from time portion only (date portion keeps no separators by being `YYYYMMDD`).
6. **Validate.** Run `tools/playout-validate.py {path}` from this plugin. Reject and rebuild if it fails.
7. **Confirm.** Report back: channel number, item count, total run time, file path. If you trimmed, padded, or skipped anything, say so explicitly.

## Validation

`tools/playout-validate.py` parses a playout JSON and checks:

- Top-level `version` matches a known schema version.
- `items[*].start` < `items[*].finish` for every item.
- Item *N+1* `start` equals item *N* `finish` (no gaps, no overlaps).
- Each `id` is unique.
- Each `source` is a valid variant with required fields.

Always run validation before reporting success. If it fails, fix and re-emit; do not hand back a broken playout.

## Reload signal

Next watches the playout folder. New or updated files are picked up on the next channel-worker tick; you do not need to restart the server. After a write, give Next 5–10 seconds and confirm via the channel's `/channel/{N}.m3u8` endpoint if you need to verify.

## Examples

`examples/playouts/` contains canonical files for common patterns:

- `marathon.json` — chronological playthrough of many items, looping is configured at the channel level (file names define the wrap point).
- `daily-schedule.json` — fixed time slots per day with a smart-collection-driven block.
- `live-mirror.json` — single `http` source covering the channel window.
- `seasonal.json` — Calendar-Based content gated by date range; items only present in-window, empty `items: []` outside.
- `random-shuffle.json` — shuffled random items filling a window.

Read one before writing if you're unsure about the exact shape.

## When to delegate

If the request involves more than two channels, or the user asks for a "package" of channels (e.g. "build me 5 channels for my horror collection"), delegate to the `programmer` agent. It runs the procedure above per channel without filling the main session's context with library queries.

## Bucket-aware programming (the 75/5 model)

The `ersatztv-setup` wizard organizes channels into **five buckets** that sum to a recommended **75 channels** (configurable). Each bucket gets a different daily-refresh strategy. The setup skill captures the bucket assignments in `config.yaml` under `channels.buckets`; this skill consumes them.

| Bucket | Default count | Daily refresh action |
| :--- | ---: | :--- |
| `core` | 35 | Re-emit the next 24 h of programming against the channel's stable theme. Library has changed since yesterday → today's lineup picks up new items. |
| `rotating` | 10 | Check whether the month rolled over since last refresh. If yes, AI picks a new theme (record it in `config.yaml`). Then emit the next 24 h. |
| `music` | 10 | Same as core but biased to music libraries. Hour-of-day tags can shift mood (morning easy listening, evening bangers). |
| `live` | 10 | Re-emit the long window if it's expired. No content discovery — these are just `http` sources pointed at external streams. |
| `experimental` | 10 | AI picks a fresh format experiment within user-set guardrails. Different from `rotating` because the *format* changes (not just the theme): a marathon today, a daily 8 PM movie tomorrow, a "every show in alphabetical order" stunt the day after. |

When the daily refresh routine fires (default cadence: midnight local, see [`setup` skill](../setup/SKILL.md) Step 4), iterate `config.yaml`'s buckets in this order: `live` (cheapest — no MCP query), `music`, `core`, `rotating`, `experimental`. This puts the most stable buckets first so a partial run still produces useful results if the routine is interrupted.

## The midnight–1 AM slot — bucket-aware late-night filler

Every channel reserves the **12 AM–1 AM local-time block** for filler so the daily refresh routine has a low-stakes window to write new playouts. The slot is also the **last hour viewers see** before fresh programming kicks in — bad filler ruins the channel feel for an hour every night.

Default approach is **theme-matched late-night content from the user's existing library**, NOT acquired infomercials. Acquisition (yt-dlp from Internet Archive — see [`setup/infomercial-filler.md`](../setup/infomercial-filler.md)) is a fallback for users who genuinely want that 1990s late-night-cable nostalgia and have the disk space.

The picker the subprogrammer uses, by bucket:

### `core` channels — three sub-rules

The core bucket is the densest part of the lineup; the picker varies by channel character:

- **Genre channels** (Action, Drama, Horror, Scifi, etc.) — pick **one documentary episode** whose subject matches the channel theme. NOVA, Cosmos, Anthony Bourdain Parts Unknown, Ken Burns specials, Planet Earth-style. Match by genre/tag overlap.
  - Example: Channel 19 *Nature* → NOVA episode about wildlife.
  - Example: Channel 13 *Scifi* → a Cosmos episode (Carl Sagan/Tyson — both fit the channel feel).
  - Example: Channel 18 *Cooking TV* → Anthony Bourdain Parts Unknown.
  - Why: Documentaries are calm, ~55 min (perfect 12am–1am fit), and the theme overlap means the channel still feels like itself at 12:30am.
- **Show-block channels** (Friends, Adult Animation, Saturday Morning, etc.) — continue the channel's signature show into a "sleeper episode." Friends 24/7 just plays the next Friends. Adult Animation plays a King of the Hill late-night episode. Saturday Morning runs a calm late-night cartoon.
- **Wrestling channels** — Channel 34 (24/7 Wrestling) keeps mixing wrestling continuously, no break. Channel 35 (PPV Time Machine) goes to its standard countdown slate.

### `rotating` channels

Theme-matched documentary or short film. Use the channel's `current_theme` to pick — Director Spotlight runs an extra short film by the chosen director, Decade Deep Dive runs an era-matched documentary, etc.

### `music` channels

**No break.** Music keeps playing — just continue the queue. Songs already fit the late-night vibe; switching to anything else would break flow. The "filler" here is just more of the same channel.

### `live` channels

**Static slate** — ETV does not transcode an `http` source into a 1-hour filler item. Either:
- Emit a `lavfi` slate (`color=c=0x101010 ... + sine`) as a single item, OR
- Skip the slot entirely (channel goes dark for an hour, valid since live URLs may already be intermittent)

The point: don't burn CPU on a live-channel "filler" — there's nothing to transcode that wouldn't work better as a static placeholder.

### `experimental` channels

**Continue the current format.** If the week's format is "Pilot Pile," the 12am–1am hour just plays another pilot. If it's "Cold Open," another 10-minute show start. The format is the channel; don't break it for a filler block.

### `holiday` core channels (31 Halloween, 32 Thanksgiving, 33 Christmas)

When out-of-season, the channel is disabled entirely from the lineup (no playout emitted). When in-season, follow the genre-channel rule: pick a theme-matched documentary or short.

### Implementation note for the subprogrammer

The subprogrammer queries Jellyfin SQLite for documentary episodes matching the channel's theme by (1) `Genres` overlap and (2) `Type='MediaBrowser.Controller.Entities.TV.Episode'` AND `SeriesName` matches a known doc-show list (NOVA, Cosmos, Anthony Bourdain - Parts Unknown, etc. — extend as the user's library grows).

Recommended SQL (schedule skill recipe):

```sql
SELECT Path, RuntimeTicks/10000000.0 AS dur_s, SeriesName, Name, Genres
  FROM BaseItems
 WHERE Type='MediaBrowser.Controller.Entities.TV.Episode'
   AND SeriesName IN ('NOVA','Cosmos','Cosmos: A Spacetime Odyssey',
                      'Anthony Bourdain - Parts Unknown',
                      'Anthony Bourdain - No Reservations',
                      'Planet Earth','Planet Earth II','Blue Planet',
                      'A Cook''s Tour','Chef''s Table',
                      'Jim Henson''s The Storyteller',
                      'Bill Nye - The Science Guy','Horizon')
   AND RuntimeTicks BETWEEN 30000000000 AND 65000000000  -- 50–108 minutes
 ORDER BY random()
 LIMIT 1;
```

Filter by genre/tag overlap with the channel's theme to pick the most-coherent doc.

Fall-through: if the user's library has no documentary that matches the channel theme, fall back to a **lavfi color slate** with the channel's logo as a still image — quiet, branded, harmless. Don't pull random non-thematic content into the slot.

## Claude curates, scripts don't (load-bearing)

This is the single most important rule in this skill. The user has stated
it as non-negotiable.

A real network programmer thinks about *which* item goes *where in the
day* — pacing, format mix, daypart character, what comes before vs.
after, what last week looked like. Bring that judgment, every time.

The temptation to grab a smart collection, `random.shuffle()` it, and
call the channel done is real and wrong. So is "alphabetical by title,"
"chronological by release," "longest first." Those are scripts. They
produce 24 hours of identical character — no rhythm, no peak, no
distinction between Tuesday afternoon and Saturday primetime.

Practical rules that follow from this:

- Build the items array item-by-item with intent. For each slot, ask:
  *given the daypart, the day of week, what aired in the previous slot,
  and what kind of channel this is — what should go here?*
- If a query returns 200 candidates, you pick which 14 land today and
  in what order. Don't pad with whatever's left.
- If you don't have enough items to fill 24 h with judgment intact (a
  smart collection of 6 obscure films on a one-show channel), surface
  that to the parent (`short` status from the subprogrammer agent).
  Don't pad with garbage and call it done — the user would rather see a
  short report than 24 h of noise.
- The only acceptable "shuffle" is `shuffle-in-order` for music
  channels, where the playback engine itself rotates a curated list
  you've already shaped by hour-of-day mood.

This rule applies to setup, daily refresh, ad-hoc `/ersatztv-program`,
and the agent-team subprogrammers. None of them get to skip it.

## Network-style daily programming patterns

When programming `core`, `rotating`, `music`, or `experimental` channels, think like a real network programmer, not like a shuffle algorithm. Real channels have **dayparts** — the time-of-day windows that anchor what kind of content goes when.

### Dayparts (US-style network model)

| Daypart | Hours (local) | Typical content character |
| :--- | :--- | :--- |
| Early morning | 5–9 AM | News, kid shows, soft openers |
| Daytime | 9 AM–4 PM | Soaps, talk, game shows, sitcom reruns |
| Late afternoon | 4–7 PM | Newsmagazines, family-friendly reruns, talk |
| Primetime | 7–11 PM | The big shows — drama, blockbuster movies, premieres |
| Late night | 11 PM–2 AM | Comedy, talk shows, edgier content |
| Overnight | 2–6 AM | Reruns, movies, infomercials |

Even on a single-genre channel, lean into these rhythms. A horror channel's "primetime" might be a feature-length classic; its "overnight" might be schlocky B-movies; its "early morning" might be PG-13 atmospheric stuff for the few people awake.

### Common channel patterns that work

- **Movie nights** — themed feature films at a fixed primetime slot, with appropriate runners-up. Saturday family movie at 7 PM, Friday horror at 9 PM, Sunday classics at 8 PM.
- **Show blocks** — 3–4 episodes of a sitcom in a row, then switch to a different show. The 30-minute commitment is real, the 4-hour one isn't.
- **Marathons** — a single show or director or year, end-to-end. Best for weekends or off-peak. Don't run a marathon during primetime mid-week.
- **Themed weekdays** — Monday classic westerns, Tuesday spy films, etc. The pattern itself becomes the brand.
- **Holiday programming** — Christmas in December, horror in October, fireworks-themed action on July 4. Tag-aware. The user's `rotating` bucket is the natural home for this.
- **Pre/post-show** — short bumpers (5–15 s) between programs, longer "be right back" cards during transitions. See [`setup/infomercial-filler.md`](../setup/infomercial-filler.md).

### What NOT to do

- Don't shuffle a single smart-collection forever. Variety within a daypart is fine; same-show reruns 24/7 is dead air.
- Don't blast feature films back-to-back without pacing. Two 2-hour movies in a row is fine; eight is fatiguing.
- Don't over-program weekends. Marathons earn their slots on Saturday; primetime slots on Friday/Saturday are still primetime.
- Don't forget the dead-air slot. The midnight–1 AM window is where the daily refresh routine writes new playouts. Schedule infomercials or reruns there so it never matters if a refresh runs slightly late.

### Four scheduling primitives beyond the daily refresh

The default refresh strategy ("re-emit the next 24 h") covers most
channels, but a real lineup also wants channels that drift on longer
arcs. Use these primitives in `config.yaml` under each channel; the
daily refresh routine consumes them.

#### 1. `year-offset` — "On This Day" / time-shifted history

Shift the playout window's wall-clock target by a fixed year delta so
the channel airs what was on N years ago today. Reads air-date metadata
from the media server.

```yaml
- number: "10"
  name: "On This Day"
  bucket: core
  primitive: year-offset
  year_offset: -25         # 25 years ago, today
  source_collection: "TV: Sitcoms"
```

At refresh time: query the collection, filter by `air_date.month/day ==
today.month/day` and `air_date.year == today.year + year_offset`, build
24 h around what's left. If nothing aired exactly N years ago today,
widen to ±3 days and surface that softly.

#### 2. `weekly-progression` — fixed-slot show advancement

A specific show plays at a specific weekday + time, advancing one
episode per occurrence. The classic "Tuesday 9 PM is *The Leftovers*."
State persists across runs in `state.json` next to the playout folder.

```yaml
- number: "11"
  name: "Tuesday Drama"
  bucket: core
  primitive: weekly-progression
  slots:
    - weekday: 2          # 0=Mon..6=Sun
      time: "21:00"
      duration_min: 60
      show: "The Leftovers"
      mode: episode-advance      # next-in-queue
    - weekday: 5          # also Friday
      time: "20:00"
      duration_min: 90
      show: "True Detective"
      mode: episode-advance
```

At refresh time: read `state.json`, for any slot whose target weekday +
time falls inside today's window, place that show's next-in-queue
episode and advance the cursor. Other hours of the day fall back to the
channel's regular daypart programming.

#### 3. `seasonal-toggle` — date-window content gating

The channel runs entirely different content during specific date
windows. Halloween in October, Christmas in December, fireworks-action
on July 4. Use for `rotating` channels that have predictable seasonal
peaks.

```yaml
- number: "100"
  name: "The Mood Channel"
  bucket: rotating
  primitive: seasonal-toggle
  seasons:
    - name: "Halloween"
      start: "10-01"        # MM-DD
      end:   "10-31"
      theme: "horror, slashers, atmospheric"
    - name: "Christmas"
      start: "11-25"
      end:   "12-31"
      theme: "holiday classics, romantic comedies, cozy"
    - name: "Summer"
      start: "06-21"
      end:   "08-31"
      theme: "blockbusters, beach movies, action"
  default_theme: "general crowd-pleasers"
```

At refresh time: pick the season matching today's date (windows can
overlap — the first match wins); program against that theme. When no
window matches, fall back to `default_theme`. Recorded:
`current_season: Halloween` so daily reports show what's active.

#### 4. `weekly-reinvention` — experimental format rotation

Each week the channel reinvents itself: a marathon Mondays, daily 8 PM
movie Tuesdays, alphabetical-by-title stunt the next week. Lives in the
`experimental` bucket; the AI picks the format under the channel's
`freeform_guardrails`.

```yaml
- number: "900"
  name: "Format Lab"
  bucket: experimental
  primitive: weekly-reinvention
  reinvent_on: monday          # rollover trigger
  freeform_guardrails: |
    Family-safe by default. Each week pick a format that hasn't run in
    the last 8 weeks. Surprise me — but every week must still result in
    24 h of continuous content with daypart-aware pacing.
  history_window_weeks: 8
```

At refresh time: if today is the reinvent day and `last_reinvented_week`
is older than this week, pick a new format (record it in
`current_format`, append previous to `format_history`). Otherwise carry
on with the existing format.

### Drop the genre-bucket model

Earlier versions of this plugin assumed channels would split TV vs. Movies and bucket by genre (`Movies: Action`, `TV: Action`, etc.). Don't do that anymore. A real channel mixes formats:

- Channel 20 is "Action Network" — afternoon action movies, evening action TV, late-night classic war films, weekend marathons of one show. **All under one channel.**
- Channel 200 is "Pop Hits" — morning easy-listening playlist, afternoon decade-themed blocks, evening top 40, late-night chillout.

The user's `core` and `rotating` channels should look like real broadcast properties. The `music` bucket is the only one where a single-axis (artist, decade, mood) channel makes sense.

## Querying the media library (Jellyfin SQLite, no MCP)

**Default content-discovery path:** read Jellyfin's SQLite DB directly,
read-only via `?immutable=1`. **Do not require an MCP install.** Jellyfin's
DB has every fact a subprogrammer needs (file paths, durations, genres,
season/episode numbers, premiere dates, studios, tags) and is local — no
network, no token, no install.

The DB lives at:

| OS | Path |
| :--- | :--- |
| macOS native install | `~/Library/Application Support/jellyfin/data/jellyfin.db` |
| linuxserver/jellyfin Docker | `/config/data/jellyfin.db` (inside the container) |
| Bare Linux install | `/var/lib/jellyfin/data/jellyfin.db` |

The `setup` skill captures the path in `config.yaml` under
`media_server.sqlite_path`. The subprogrammer + agent prompts reference
that key.

### Schema essentials (Jellyfin 10.10+)

The single useful table is `BaseItems`. Discriminate by `Type`:

| Content kind | `Type` value |
| :--- | :--- |
| Movie | `MediaBrowser.Controller.Entities.Movies.Movie` |
| TV episode | `MediaBrowser.Controller.Entities.TV.Episode` |
| TV series | `MediaBrowser.Controller.Entities.TV.Series` |
| TV season | `MediaBrowser.Controller.Entities.TV.Season` |
| Audio (song) | `MediaBrowser.Controller.Entities.Audio.Audio` |
| Music album | `MediaBrowser.Controller.Entities.Audio.MusicAlbum` |
| Music artist | `MediaBrowser.Controller.Entities.Audio.MusicArtist` |

Useful columns for programming:

- **`Path`** — absolute filesystem path. Already what ETV Next + Jellyfin both see (when bind-mounts use identical host paths).
- **`Name`** — display title.
- **`RuntimeTicks`** — duration in 100-nanosecond ticks. Convert: `seconds = ticks / 10_000_000`.
- **`ProductionYear`** — release year (movies); first-aired year (shows).
- **`PremiereDate`** — RFC 3339 timestamp; ISO date for episodes' original air date.
- **`Genres`** — pipe-separated string, e.g. `"Adventure|Comedy|Fantasy|Family"`.
- **`Studios`** — pipe-separated.
- **`Tags`** — pipe-separated user/auto tags.
- **`SeriesName`**, **`SeasonName`**, **`IndexNumber`** (= episode number), **`ParentIndexNumber`** (= season number) — for episodes.
- **`OfficialRating`** — content rating (G / PG / PG-13 / R / TV-14 / TV-MA / etc.).

### Recipe queries

```sql
-- Duration in seconds, generic
SELECT Path, RuntimeTicks / 10000000.0 AS dur_s FROM BaseItems WHERE Path = ?;
```

```sql
-- All Friends S1 episodes in order
SELECT Path, RuntimeTicks/10000000.0 AS dur_s, IndexNumber AS ep
  FROM BaseItems
 WHERE Type='MediaBrowser.Controller.Entities.TV.Episode'
   AND SeriesName='Friends' AND ParentIndexNumber=1
 ORDER BY IndexNumber;
```

```sql
-- All horror movies
SELECT Path, RuntimeTicks/10000000.0 AS dur_s, ProductionYear AS yr
  FROM BaseItems
 WHERE Type='MediaBrowser.Controller.Entities.Movies.Movie'
   AND Genres LIKE '%Horror%'
 ORDER BY ProductionYear DESC;
```

```sql
-- "On this day" — premiere date matches today's MM-DD across any year
SELECT Path, RuntimeTicks/10000000.0 AS dur_s, PremiereDate, Name
  FROM BaseItems
 WHERE PremiereDate IS NOT NULL
   AND substr(PremiereDate, 6, 5) = strftime('%m-%d', 'now', 'localtime')
 ORDER BY substr(PremiereDate, 1, 4);  -- year ascending
```

```sql
-- Newest acquisitions across the whole library (last N days)
SELECT Path, RuntimeTicks/10000000.0 AS dur_s, DateCreated, Name, Type
  FROM BaseItems
 WHERE DateCreated >= datetime('now', '-7 days')
   AND Type IN (
     'MediaBrowser.Controller.Entities.Movies.Movie',
     'MediaBrowser.Controller.Entities.TV.Episode'
   )
 ORDER BY DateCreated DESC;
```

```sql
-- All content from a specific Jellyfin library by ParentId (the library's
-- TopParentId in BaseItems). Use TopParentId not a Path LIKE — paths are
-- user-specific; the library's UUID is stable.
SELECT Path, RuntimeTicks/10000000.0 AS dur_s, SeriesName, Name
  FROM BaseItems
 WHERE TopParentId = ?  -- pass the library's BaseItems.Id (look up by Name)
   AND Type='MediaBrowser.Controller.Entities.TV.Episode'
 ORDER BY Path;

-- To find a library's TopParentId by name first:
SELECT Id FROM BaseItems
 WHERE Type='MediaBrowser.Controller.Entities.CollectionFolder'
   AND Name = ?;
```

### Read-only safety

Always open with `?immutable=1` so a half-running Jellyfin doesn't get its
WAL stomped:

```bash
sqlite3 "file:${JF_DB}?immutable=1" -readonly <<'SQL'
SELECT ...
SQL
```

### When MCP-style structured access is preferable

Reading the DB directly trades typed access for portability. Use direct
SQL when:
- You're inside the daily routine and need bulk queries fast.
- The user hasn't installed an MCP.
- You want one less moving part.

Use a Jellyfin / Plex / Emby MCP when:
- The user has it set up and is already authenticated against a remote
  server (the MCP knows the right URL/token; the SQLite path approach
  only works for a *local* Jellyfin install).
- You want playback / queue / user-state operations the read-only DB
  doesn't expose.

Both paths are valid — pick what's available, prefer SQLite when the
choice is open.

## Refreshing Jellyfin's EPG after a write

Jellyfin doesn't auto-refetch the XMLTV file ErsatzTV Next serves. After writing playout JSON for any channel that's part of the Live TV tuner, hit:

```bash
curl -X POST "http://localhost:18096/LiveTv/Guide/Refresh" \
  -H "X-Emby-Token: $JELLYFIN_TOKEN"
```

This is what the daily refresh routine does at the end of its run. For ad-hoc `/ersatztv-program` calls, only do this if the user is actively watching via Jellyfin's Live TV section.
