---
name: audit
description: Audit an existing ErsatzTV Legacy install against a Next-based setup and report gaps. Loads when the user runs /audit, asks to "migrate from Legacy," asks "what channels do I already have?" or wants a one-time inventory before pivoting to Next. Read-only — never modifies either install.
---

# Migration audit

Walks an existing ErsatzTV Legacy install (the SQLite-backed Blazor version) and an ErsatzTV Next setup side-by-side and reports what would need to move to bring Next to parity. Read-only on both sides.

Use cases:

- The user has been running native ErsatzTV Legacy on macOS for months; they want to switch to Next without losing channel definitions.
- The user runs both side-by-side during a transition; they need a periodic diff.

This skill **does not migrate anything automatically**. It produces a punch list. Migration steps are the user's call, executed via `/program` for each channel.

## Inputs

Ask the user, or detect:

- **Legacy install path.** Native macOS default: `~/.local/share/ersatztv/`. Docker default: bind-mount of the same. The SQLite database is `ErsatzTV.db` in that folder.
- **Next install path.** From the plugin config (`config.yaml` → `ersatztv_next.lineup_path`) if setup has run; otherwise ask.

If the user can't supply either, stop and explain what's needed.

## Procedure

1. **Open Legacy SQLite read-only.** Use `sqlite3 file:$LEGACY_DB?mode=ro -readonly` (or the equivalent `?immutable=1` URI) so a half-running Legacy app doesn't get its WAL stomped. Bail with a friendly error if the file is locked.

2. **Inventory Legacy.**

   Run these queries and capture results:

   ```sql
   SELECT Id, Number, Name, Group_, Categories,
          FFmpegProfileId, IsEnabled, ShowInEpg
     FROM Channels
    ORDER BY SortNumber;

   SELECT Id, Name, Query
     FROM SmartCollections
    ORDER BY Name;

   SELECT Id, Name, Resolution_Width, Resolution_Height,
          VideoFormat, AudioFormat, NormalizeVideo, NormalizeAudio
     FROM FFmpegProfiles
    ORDER BY Name;

   SELECT c.Number, c.Name, w.Name AS WatermarkName,
          w.Image, w.Mode, w.Location
     FROM Channels c
     LEFT JOIN ChannelWatermarks w ON c.WatermarkId = w.Id
    WHERE c.WatermarkId IS NOT NULL;
   ```

   For SeaDog-fork installs only (older work in `Chewable-Studios/legacy`):

   ```sql
   SELECT Id, Number, Name, ScheduleMode,
          ScheduleModeSmartCollectionId, LiveStreamUrl,
          PlanYamlOverride IS NOT NULL AS HasOverride,
          SeasonalStartMMDD, SeasonalEndMMDD
     FROM Channels;
   ```

   The `ScheduleMode` column is fork-specific; skip the query if it errors with "no such column."

3. **Inventory Next.**

   Read `lineup.json` → enumerate `channels[]`. For each channel, read the referenced `channel.json` and note the `playout.folder`. List the playout JSON files that already exist in each folder.

4. **Build the diff.**

   For each Legacy channel, decide:

   | Status | Meaning |
   | :--- | :--- |
   | **Already in Next** | A Next channel exists with the same number AND a playout folder with at least one current-window JSON file. |
   | **Defined in Next, no playout** | Next has the channel listed in `lineup.json` but the playout folder is empty or stale. The user needs to `/program` it. |
   | **Missing in Next** | No matching channel in `lineup.json`. The user needs to add it to `lineup.json`, write `channel.json`, and `/program` it. |
   | **Live URL** *(SeaDog fork only)* | `ScheduleMode == 2 (Live)` with a `LiveStreamUrl`. The Next equivalent is a single playout item with `source_type: http`. |
   | **YAML override** *(SeaDog fork only)* | `PlanYamlOverride` is set. The user wrote raw Sequential YAML; this won't migrate cleanly to Next playout JSON. Flag it for review. |

   For Smart Collections, FFmpeg Profiles, and Watermarks: Next does not own these concepts. They live in the user's media server (smart collections) or their channel.json (FFmpeg/normalization). Report them as informational, not as gaps to fill.

5. **Output.**

   Print a single Markdown report in the session:

   ```markdown
   # ErsatzTV migration audit
   Legacy DB: /Users/zach/.local/share/ersatztv/ErsatzTV.db
   Next lineup: /Users/zach/.config/ersatztv-next/lineup.json
   Run at: 2026-04-25 14:32 PDT

   ## Channels
   | # | Name | Status | Notes |
   | :--- | :--- | :--- | :--- |
   | 10 | Action Movies | Already in Next | playout 20260425T000000.000000000-0700_… |
   | 11 | Horror | Defined, no playout | run `/program 11` |
   | 60 | iptv-org news | Missing in Next | Live URL: http://...m3u8 — add to lineup.json then `/program 60` |

   ## Smart Collections (informational)
   - "TV: Action" — 247 Lucene-indexed items
   - "Movies: Horror" — 89 items

   ## FFmpeg Profiles (informational)
   - "Default" — 1080p HEVC 2000kbps, AAC 192kbps

   ## Action items
   1. `/program 11`
   2. Add channel 60 to `lineup.json` then `/program 60`
   3. Channel 5 has a `PlanYamlOverride` — manual review needed (raw Sequential YAML doesn't auto-migrate).
   ```

## What audit never does

- Never writes to the Legacy database. Only reads it (read-only mode on open).
- Never edits `lineup.json` or `channel.json`. Suggestions go in the report; the user runs them through `/program`.
- Never queries the user's media server. Smart-collection contents are reported by name + count from Legacy's SQLite; full content comes via the media server MCP at `/program` time.
- Never assumes a column exists. If `ScheduleMode` (or any SeaDog-fork-only field) is absent, skip that section without erroring.
