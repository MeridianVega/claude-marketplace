# HANDOFF — ersatztv-programmer

Onboarding for a Claude Code session that has just installed this plugin and is ready to start managing channels. Read top-to-bottom on first run; reference later as needed.

## What this plugin is

A scheduling brain for [ErsatzTV Next](https://github.com/ErsatzTV/next). Next handles transcoding and HLS streaming and explicitly leaves scheduling out of scope. This plugin gives Claude the procedures to:

- Build playout JSON from a Jellyfin / Plex / Emby library.
- Validate it against the upstream schema.
- Drop it in the channel's playout folder for Next to stream.

The plugin does **not** include a server, a database, or a UI. Everything runs through Claude Code.

## Stack history (skip if you already know)

- **ErsatzTV Legacy** — the original Blazor + SQLite app. Feature-frozen as of April 2026 by the upstream creator. Lives at `ErsatzTV/legacy`.
- **ErsatzTV Next** — Rust rewrite, transcoding only, consumes playout JSON. The path forward. Lives at `ErsatzTV/next`. Pre-1.0; expect occasional schema bumps.
- **A previous SeaDog fork** — extensions to Legacy that added channel modes, smart-collection synthesizers, and a multi-service Docker stack. Frozen at `Chewable-Studios/legacy:feat/blockitem-episode-state` as a reference artifact. Not under development.
- **This plugin** — the Next-aligned successor. Schedulers live in plugins, not in forks.

## Three tiers, all opt-in

| Tier | What you do | What runs |
| :--- | :--- | :--- |
| Manual | `/program` whenever you want | Nothing |
| Routine | `/setup` once, opt into a daily refresh | Desktop scheduled task or Cloud Routine |
| Hybrid | Both | Routine + ad-hoc |

Pick whichever matches your usage. No auto-installation of routines.

## First session — what to do

1. **Verify the plugin loaded**: run `/help`. Look for `program`, `setup`, `audit`. They appear under the `ersatztv-programmer` namespace if loaded from the marketplace, or as plain names if loaded via `--plugin-dir` for development.

2. **Verify a media-server MCP is loaded**: run `/mcp list`. You should see `jellyfin-mcp`, `plex-mcp`, or `emby-mcp`. If not, install one before going further:
    - Jellyfin: `Jellyfish-AI/jellyfin-mcp` or `pip install jellyfin-mcp`.
    - Plex: any current `plex-mcp` distribution.
    - Emby: any current `emby-mcp` (Jellyfin MCPs typically work unchanged).

   Configure the MCP with `JELLYFIN_BASE_URL` / `JELLYFIN_TOKEN` (or the Plex/Emby equivalents) per its own README.

3. **Verify ErsatzTV Next is reachable**: probe `http://localhost:18409/channels.m3u` (or wherever you've bound it). If it's not running, see `examples/stack/docker-compose.yml` for a one-shot stack.

4. **Decide your tier** (manual vs routine vs hybrid). If routine or hybrid, run `/setup`.

5. **Build your first channel** with `/program` or just say "build me a channel that …".

## Day-2 workflows

| Task | How |
| :--- | :--- |
| Build a one-off channel | `/program` |
| Refresh a channel's playout for the next 24h | `/program <number>` |
| Refresh every channel in your config | `/program --from-config` (typically only the daily routine runs this) |
| Inventory a Legacy install before migrating | `/audit` |
| Change setup answers | re-run `/setup` |
| Remove the routine | Claude Code Desktop → Schedule → delete the task; or web UI for cloud routines |
| Update the plugin | `/plugin marketplace update`, then `/reload-plugins` |

## What lives where

| Thing | Path |
| :--- | :--- |
| Plugin source | `plugins/ersatztv-programmer/` in this marketplace repo |
| Skills | `plugins/ersatztv-programmer/skills/{schedule,setup,reference,audit}/SKILL.md` |
| Agents | `plugins/ersatztv-programmer/agents/programmer.md` |
| Commands | `plugins/ersatztv-programmer/commands/program.md` (`/setup` and `/audit` are auto-created by their skills) |
| Hooks | `plugins/ersatztv-programmer/hooks/hooks.json` |
| Validator | `plugins/ersatztv-programmer/tools/playout-validate.py` |
| Examples | `plugins/ersatztv-programmer/examples/playouts/*.json` and `examples/stack/docker-compose.yml` |
| User config (after `/setup`) | OS-dependent: `~/Library/Application Support/ersatztv-programmer/config.yaml` (macOS), `~/.config/ersatztv-programmer/config.yaml` (Linux), `%APPDATA%\ersatztv-programmer\config.yaml` (Windows) |
| Update-check cache | OS-dependent under the cache dir; the SessionStart hook manages this. |

## Hard rules across every skill / agent

These come from upstream constraints and your own preferences. Honor them without re-asking each time.

1. **Never modify `lineup.json` or `channel.json` without explicit user permission.** Read freely; write only with consent.
2. **Never invent file paths.** Every `local` source must come from a real media-server query.
3. **Never store media-server tokens in `config.yaml`.** Tokens belong in MCP-server env vars.
4. **Always validate before reporting success.** `tools/playout-validate.py` exit 0 = pass.
5. **Items in a playout are contiguous in time.** Schema doesn't enforce this, but Next streams break with gaps. Use `lavfi` filler if you need an idle slot.
6. **Compact ISO 8601 in filenames** — no `:` or `-` separators except as the timezone-offset sign. Example: `20260413T000000.000000000-0500_20260414T000000.000000000-0500.json`.

## Updates

A `SessionStart` hook polls the public GitHub commits API for this marketplace and prints a one-line notice in your first turn if a newer commit exists. Run `/plugin marketplace update` then `/reload-plugins` to apply. The hook caches the last-seen SHA and won't nag for the same commit twice.

## When things go wrong

- **"channel N not in lineup"**: add the channel to `lineup.json` (and create its `channel.json`), then `/program` again. The plugin won't add lineup entries on its own.
- **Validator rejects the file you wrote**: read the error, fix, re-emit. Don't ship a broken playout.
- **Next isn't picking up changes**: confirm the playout filename is correct compact ISO 8601 and lives in the right `playout.folder`. Wait 5–10 seconds; Next watches the folder.
- **Cloud routine can't read local files**: that's by design. Either switch to a Desktop scheduled task, or push your `config.yaml` to a Git repo the routine clones.
- **Plugin commands don't appear**: `/reload-plugins`, then `/help`. If still missing, `/plugin marketplace update` and reload.

## Out of scope

Things this plugin deliberately does not do:

- Manage Sonarr / Radarr / Bazarr / Jellyseerr. Use those tools' own UIs.
- Curate library tags / smart collections inside the media server. Do that in Jellyfin / Plex / Emby itself.
- Author `lineup.json` or `channel.json`. The user (or their Compose stack) owns those.
- Run a UI. Claude Code is the UI.
