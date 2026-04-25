---
name: schedule
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
