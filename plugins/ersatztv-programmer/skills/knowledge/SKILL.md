---
name: knowledge
description: Senior-engineer mental model for the ErsatzTV ecosystem. Project history, repository layout, where things live on disk, common debugging paths, upstream entry points, key concepts. Loads when the user asks "where does X live," "why is Y not working," "what's the difference between Legacy and Next," or otherwise needs deep ErsatzTV context the schedule/reference skills don't cover.
disable-model-invocation: false
user-invocable: false
---

# ErsatzTV operator's handbook

When this skill is in context, you are operating with a senior ErsatzTV engineer's mental model. Every fact below is sourced — cite the source when surfacing a fact to the user so they can verify it. If a claim isn't here and you can't trace it to one of the listed sources, say "I don't know; let me check" and fetch upstream rather than guess.

## The two-stack reality (April 2026)

ErsatzTV exists in two distinct projects right now:

- **ErsatzTV Legacy** — the original C# / Blazor / SQLite app. Originally archived, then **unarchived** by the maintainer (jasondove) on 2026-04-14 and explicitly designated the "legacy" reference. Feature-frozen; security fixes only. Will be updated to use Next as its transcoding engine.
    - Repo: <https://github.com/ErsatzTV/legacy>
    - Releases: <https://github.com/ErsatzTV/legacy/releases>
    - Docker images: `ersatztv/legacy:develop` and `ghcr.io/ersatztv/legacy:develop`
    - Maintainer announcement: <https://old.reddit.com/r/ErsatzTV/comments/1sngryj/what_is_next_for_ersatztv/>
- **ErsatzTV Next** — the Rust rewrite. Transcoding and streaming engine only — library management and scheduling are explicitly out of scope. Pre-1.0; expect schema/API churn.
    - Repo: <https://github.com/ErsatzTV/next>
    - Docker image: `ghcr.io/ersatztv/next:latest`
    - README states the scope boundary verbatim: "Library and metadata management, scheduling and playout creation **are not in scope for this project**." (<https://github.com/ErsatzTV/next/blob/main/README.md>)

This plugin targets **Next**. References to Legacy in the `audit` skill exist solely to help users migrate.

## Next architecture in one diagram

```
lineup.json  ──►  channels[N].config = ./channels/N/channel.json
                                              │
                                              ├─► playout.folder ──► {start}_{finish}.json (one or many)
                                              ├─► ffmpeg.{paths}
                                              └─► normalization.{audio,video}
```

| Layer | File | What it owns |
| :--- | :--- | :--- |
| Server | `lineup.json` | Bind address, output folder, the full channel list |
| Channel | `channel.json` (per-channel) | FFmpeg paths, normalization, where playout files live |
| Time slice | `{start}_{finish}.json` (per-window) | The actual `items[]` to play in that window |

This separation is the whole point of Next: a channel is a long-lived config, a playout is a short-lived schedule, and the streaming engine is dumb about everything except how to follow the playout.

Source of truth: <https://github.com/ErsatzTV/next/blob/main/README.md> + the three schemas under <https://github.com/ErsatzTV/next/tree/main/schema>.

## Crate layout (Next)

If you have to read source, this is the order to read it in:

| Crate | What it does | Path |
| :--- | :--- | :--- |
| `ersatztv` | Axum HTTP server. Serves `/channels.m3u`, `/channel/{N}.m3u8`, `/session/{channel}/{file}`. Spawns one `ersatztv-channel` subprocess per active channel. | `crates/ersatztv` |
| `ersatztv-channel` | Per-channel worker. Reads playout JSON, builds FFmpeg pipelines, writes HLS segments. Has a 4-state buffering machine (`SeekAndWorkAhead` → `ZeroAndWorkAhead` → `SeekAndRealtime` → `ZeroAndRealtime`). | `crates/ersatztv-channel` |
| `ffpipeline` | FFmpeg pipeline builder. Probes source media, picks hardware acceleration via the `HwAccel` trait (CUDA, QSV, VAAPI, VideoToolbox), constructs filter chains. | `crates/ffpipeline` |
| `ersatztv-playout` | Playout JSON data models — serde + schemars. Schema generated here. | `crates/ersatztv-playout` |
| `ersatztv-core` | Shared utilities: heartbeat/ready-file management, timing constants. | `crates/ersatztv-core` |
| `ersatztv-playout-generator` | Dev tool: generates playout JSON from a video folder, or runs `sync-channel` against a Legacy SQLite DB. | `crates/ersatztv-playout-generator` |

Source: <https://github.com/ErsatzTV/next/blob/main/CLAUDE.md> (project's own AGENTS.md/CLAUDE.md, kept current by the upstream team).

## Where things live on disk

**Legacy (native macOS install)**, per `ErsatzTV.Core/FileSystemLayout.cs` in the upstream codebase:

| Thing | Path |
| :--- | :--- |
| App data | `~/.local/share/ersatztv/` |
| SQLite DB | `~/.local/share/ersatztv/ErsatzTV.db` (with `-wal` and `-shm` siblings) |
| Channel guide cache | `~/.local/share/ersatztv/cache/channel-guide/{N}.xml` |
| Logo cache | `~/.local/share/ersatztv/cache/artwork/logos/` |
| Watermark cache | `~/.local/share/ersatztv/cache/artwork/watermarks/` |
| FanArt cache | `~/.local/share/ersatztv/cache/artwork/fanart/` |
| Lucene search index | `~/.local/share/ersatztv/search-index/` |
| Logs | `~/.local/share/ersatztv/logs/` |

Folder declarations come from `FileSystemLayout.cs`. File-naming inside each cache folder (flat vs hash-bucketed) is a write-side convention — confirm against the specific writer when it matters. Source: <https://github.com/ErsatzTV/legacy/blob/main/ErsatzTV.Core/FileSystemLayout.cs>.

**Next (typical Docker layout)**:

| Thing | Path inside container | Path on host (typical) |
| :--- | :--- | :--- |
| Lineup | `/config/lineup.json` | `~/ersatztv-stack/config/ersatztv-next/lineup.json` |
| Per-channel config | `/config/channels/{N}/channel.json` | `~/ersatztv-stack/config/ersatztv-next/channels/{N}/channel.json` |
| Per-channel playouts | `{folder from channel.json playout.folder}` | wherever the user pointed it (commonly `~/ersatztv-stack/config/ersatztv-next/channels/{N}/playout/`) |
| HLS output | `output.folder` from `lineup.json` | `~/ersatztv-stack/hls/` or `/tmp/hls` |

Verbatim Dockerfile + path conventions: <https://github.com/ErsatzTV/next/blob/main/docker/Dockerfile>.

## Common debugging paths

When a user says "my channel won't play," walk this list in order. Each step has a one-line check.

1. **Is Next reachable at all?** `curl http://localhost:18409/channels.m3u` — should return the M3U list. If 404, the server isn't running. If empty, no channels are in `lineup.json`.
2. **Is the channel in the lineup?** `jq '.channels[] | select(.number=="42")' lineup.json` — if no output, the channel is missing from the lineup.
3. **Is `channel.json` valid?** `jq . channel.json` — JSON parse failure here surfaces as a silent omission in the lineup.
4. **Is there a playout file for the current time?** `ls channels/42/playout/`. The compact-ISO-8601 filename's window must contain *now*, in the user's local timezone.
5. **Does the playout file validate?** `python tools/playout-validate.py channels/42/playout/*.json` (this plugin's bundled validator, hooked in via `${CLAUDE_PLUGIN_ROOT}/tools/playout-validate.py`).
6. **Does the first item's source actually exist?** For `local`, `stat "$path"` from inside the container (`docker exec ersatztv-next stat /media/...`). For `http`, `curl -I` the URL.
7. **Is FFmpeg crashing?** Container logs: `docker logs ersatztv-next --tail 200`. A repeating "ffmpeg exited 1" usually means a probe failure on the source — bad codec, broken file, unreachable URL.
8. **Are there HLS segments being written?** `ls /tmp/hls/`. If empty more than ~30 s after channel hit, the worker isn't progressing.

These are the steps the upstream Discord and forum recommend; collated from <https://discuss.ersatztv.org/> and the project's CLAUDE.md.

## Key concepts (vocabulary)

When the user uses one of these terms, this is what they mean. Mismatch on these is the #1 cause of confusion in support threads.

| Term | Meaning |
| :--- | :--- |
| **Lineup** | The full channel roster. Lives in `lineup.json`. Next-only term. |
| **Channel** | A logical 24/7 stream. In Legacy: a row in the `Channels` SQLite table. In Next: a `lineup.json` entry pointing at a `channel.json`. |
| **Playout** | The time-coded list of items a channel plays. In Legacy: rows in the `PlayoutItem` table generated by a builder from a Schedule/Block/Sequential YAML. In Next: a JSON file under the channel's playout folder. |
| **Block** *(Legacy)* | A reusable group of scheduled items, e.g. "Saturday Morning Cartoons block." Block + Template + PlayoutTemplate is the Legacy scheduling stack. |
| **Smart Collection** *(media-server side)* | A saved query that resolves to a set of media items. Lives in Jellyfin/Plex/Emby, not in ErsatzTV. The plugin queries the user's server via MCP to resolve it. |
| **Source** *(Next)* | A `local` file, a `lavfi` synthetic, or an `http` URL. Each playout item has at least one. |
| **Lavfi** | FFmpeg's `-f lavfi -i` synthetic input — generates audio/video from a filter graph (silence, color bars, "be right back" cards). Use for fillers and gaps. |
| **HLS** | HTTP Live Streaming. Next outputs `.m3u8` playlists + `.ts` (or `.fmp4`) segments under `output.folder`. The default segment length is 4 s; keyframe interval is 2 s — both per CLAUDE.md. |

## Upstream entry points

When you don't know something and it's not here, go to one of these in order:

1. **Schemas** — `https://github.com/ErsatzTV/next/tree/main/schema` (live truth for Next config).
2. **README + CLAUDE.md** — `https://github.com/ErsatzTV/next/blob/main/README.md`, `https://github.com/ErsatzTV/next/blob/main/CLAUDE.md` (project's own agent-oriented docs).
3. **Project home** — `https://ersatztv.org/` (entry point; community links and docs index).
4. **Discord** — invite from the project home page (real-time, but ephemeral; don't cite link rot).
5. **Issues** — `https://github.com/ErsatzTV/next/issues` (known bugs, design discussions).
6. **Reddit** — `https://old.reddit.com/r/ErsatzTV/` (announcements, occasional debugging threads).

## Schema freshness

The bundled `reference` skill in this plugin pins the playout schema at version `https://ersatztv.org/playout/version/0.0.1`. The `tools/check-updates.sh` SessionStart hook polls `https://raw.githubusercontent.com/ErsatzTV/next/main/schema/playout.json` and warns if upstream's `$id` differs. If you see the warning surface in `additionalContext`:

1. Confirm by visiting the schema URL above.
2. If drift is real, the bundled `reference` skill is out of date — file an issue at <https://github.com/MeridianVega/claude-marketplace/issues> so the plugin maintainer can refresh it.
3. In the interim, prefer the live schema over the pinned reference for any field you have doubts about.

## Project history (one paragraph)

ErsatzTV was created by [jasondove](https://github.com/jasondove) as a C#/Blazor IPTV server with smart-collection scheduling. It accumulated a complex scheduling stack (Classic schedules → Block schedules → Sequential YAML → Scripted schedules) and a Lucene-backed search index. In April 2026 the project bifurcated: the original was archived briefly, then **unarchived as "Legacy"** and feature-frozen; a Rust rewrite called **Next** took over the streaming/transcoding role with a much smaller scope. The maintainer's stated direction: Next is the transcoding engine, third-party schedulers (this plugin among them) emit playout JSON for it, Legacy continues to exist as a reference scheduler and will eventually use Next as its transcoding backend. Source: <https://old.reddit.com/r/ErsatzTV/comments/1sngryj/what_is_next_for_ersatztv/> and the unarchive notice on the Legacy repo.
