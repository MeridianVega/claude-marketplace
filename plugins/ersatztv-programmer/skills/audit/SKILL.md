---
name: ersatztv-audit
description: Audit an existing ErsatzTV Legacy install against a Next-based setup and report gaps. Loads when the user runs /ersatztv-audit, asks to "migrate from Legacy," asks "what channels do I already have?" or wants a one-time inventory before pivoting to Next. Read-only — never modifies either install.
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

1. **Open Legacy SQLite read-only.** Use `sqlite3 file:$LEGACY_DB?immutable=1` (or `?mode=ro`) so a half-running Legacy app doesn't get its WAL stomped. Bail with a friendly error if the file is locked.

2. **Introspect the schema before querying.** Real Legacy DBs use **singular** table names (`Channel`, not `Channels`), `"Group"` is a reserved word so it must be quoted, and `FFmpegProfile.ResolutionId` joins to a separate `Resolution` table. **Always run `.tables` first** and confirm column names with `.schema <table>` before issuing the production queries below — Legacy's schema has shifted across versions and a hardcoded query list will break.

   ```bash
   sqlite3 "file:$LEGACY_DB?immutable=1" -readonly <<'SQL'
   .tables
   .schema Channel
   .schema SmartCollection
   .schema FFmpegProfile
   .schema Resolution
   .schema ChannelWatermark
   SQL
   ```

   Skip any query whose table or column doesn't exist on this install. Surface what was skipped in the audit report so the user knows.

3. **Inventory Legacy.** These queries are correct for the schema observed in upstream `ErsatzTV/legacy` as of 2026-04. Quoted `"Group"` because it's a SQL reserved word; `Resolution` joined explicitly because `FFmpegProfile` only has `ResolutionId`.

   ```sql
   -- Channels
   SELECT Id, Number, Name, "Group", Categories,
          FFmpegProfileId, IsEnabled, ShowInEpg, SortNumber
     FROM Channel
    ORDER BY SortNumber;

   -- Smart collections (Lucene queries)
   SELECT Id, Name, Query
     FROM SmartCollection
    ORDER BY Name;

   -- FFmpeg profiles, resolution joined in
   SELECT p.Id, p.Name,
          r.Width AS Width, r.Height AS Height,
          p.VideoBitrate, p.AudioBitrate,
          p.AudioChannels,
          p.HardwareAcceleration,
          p.NormalizeFramerate, p.NormalizeLoudnessMode,
          p.VideoFormat, p.AudioFormat
     FROM FFmpegProfile p
     LEFT JOIN Resolution r ON p.ResolutionId = r.Id
    ORDER BY p.Id;

   -- Channels with watermarks
   SELECT c.Number, c.Name, w.Name AS WatermarkName,
          w.Image, w.Mode, w.Location, w.Opacity, w.WidthPercent
     FROM Channel c
     LEFT JOIN ChannelWatermark w ON c.WatermarkId = w.Id
    WHERE c.WatermarkId IS NOT NULL
    ORDER BY c.SortNumber;

   -- Channel → schedule mapping (block / sequential / template)
   SELECT c.Number, c.Name,
          ps.Id AS ScheduleId, ps.Name AS ScheduleName,
          ps.YamlFile,
          (SELECT COUNT(*) FROM ProgramScheduleItem WHERE ProgramScheduleId = ps.Id) AS Items
     FROM Channel c
     LEFT JOIN Playout pl ON pl.ChannelId = c.Id
     LEFT JOIN ProgramSchedule ps ON pl.ProgramScheduleId = ps.Id
    ORDER BY c.SortNumber;

   -- ProgramScheduleItem detail (which collection backs each schedule)
   SELECT psi.ProgramScheduleId AS PSId,
          ps.Name AS Schedule,
          psi.CollectionType AS CT,        -- 0=Coll 1=TVShow 2=TVSeason 3=Artist 4=Multi 5=Smart 6=Playlist
          psi.SmartCollectionId AS SCId,
          sc.Name AS SmartCollectionName,
          psi.PlaybackOrder AS Ordering,
          psi.GuideMode,
          psi.MarathonShuffleItems
     FROM ProgramScheduleItem psi
     LEFT JOIN SmartCollection sc ON psi.SmartCollectionId = sc.Id
     LEFT JOIN ProgramSchedule ps  ON psi.ProgramScheduleId = ps.Id
    ORDER BY psi.ProgramScheduleId;

   -- Connected media servers (URLs only, never tokens)
   SELECT Id, Address FROM JellyfinConnection;
   SELECT Id, Address FROM PlexConnection;
   SELECT Id, Address FROM EmbyConnection;

   -- Jellyfin libraries the Legacy install can see
   SELECT jl.Id, l.Name, jl.MediaSourceId, jl.ItemId, jl.LastSync, l.MediaKind
     FROM JellyfinLibrary jl
     JOIN Library l ON jl.Id = l.Id
    ORDER BY l.Name;
   ```

   For SeaDog-fork installs only (older work in `Chewable-Studios/legacy`):

   ```sql
   SELECT Id, Number, Name, ScheduleMode,
          ScheduleModeSmartCollectionId, LiveStreamUrl,
          PlanYamlOverride IS NOT NULL AS HasOverride,
          SeasonalStartMMDD, SeasonalEndMMDD
     FROM Channel;
   ```

   The `ScheduleMode` column is fork-specific; skip the query if `.schema Channel` doesn't list it.

4. **Detect II-twin pairs.** Default Legacy installs ship with timeshift-twin channels (e.g. `20 Action Movies` and `20.1 Action Movies II`) that share the same schedule + smart collection but have a different watermark. Most users want to drop the entire set during migration.

   Detection query:

   ```sql
   SELECT a.Number AS Primary_, b.Number AS Twin,
          a.Name AS PrimaryName, b.Name AS TwinName
     FROM Channel a
     JOIN Channel b ON CAST(a.SortNumber AS INTEGER) = CAST(b.SortNumber AS INTEGER)
                   AND b.Number LIKE a.Number || '.%'
                   AND a.Number != b.Number
    ORDER BY CAST(a.SortNumber AS INTEGER);
   ```

   In the audit report's Action items, surface a single line: *"N II-twin pairs detected. Drop them all? [Y/n]"* — defaulting to Y is reasonable based on the v0.1.0 field report.

5. **Inventory Next.**

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
