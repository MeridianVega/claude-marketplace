# ersatztv-programmer

A Claude Code plugin that programs [ErsatzTV Next](https://github.com/ErsatzTV/next) channels from a media library.

## What it does

ErsatzTV Next is a transcoding and streaming engine. It consumes **playout JSON** files that describe what to play and when, and it deliberately leaves scheduling out of scope. This plugin gives Claude Code the procedures to build those playout JSON files from a Jellyfin, Plex, or Emby library, on demand, in whatever shape a channel needs.

There are no fixed channel types. A request like *"build me a horror marathon for October"* is enough; Claude resolves the library queries, orders the items, emits valid JSON, and writes it where ErsatzTV Next picks it up.

## How you use it — three opt-in tiers

| Tier | What you do | What runs in the background | When to choose it |
| :--- | :--- | :--- | :--- |
| **Manual** *(default)* | Run `/program` whenever you want to build or rebuild a channel. | Nothing. ErsatzTV Next streams whatever JSON exists; channels keep playing without further action. | You program rarely, want full control, or are still figuring out what kinds of channels you want. |
| **Routine** *(opt-in)* | Run `/setup`, choose a refresh cadence, lock in your channel preferences. | A scheduled task (Desktop or Cloud Routine) re-runs the programming once per day so each channel rotates fresh content. | You have steady patterns (daily marathons, "this week in X" channels) and want them refreshed without you remembering. |
| **Hybrid** | Use `/program` for one-offs *and* keep a routine running for recurring channels. | Routine runs as in tier 2; ad-hoc requests run on demand. | The common case once you've used the plugin for a while. |

Nothing is auto-installed beyond the plugin itself. No routine is created until you explicitly run `/setup` and pick the routine tier.

## Install

```text
/plugin marketplace add MeridianVega/claude-marketplace
/plugin install ersatztv-programmer@meridianvega
```

That's it. After install you can immediately run `/program` to build a channel.

## Required environment

| Component | Why |
| :--- | :--- |
| **ErsatzTV Next** | Runs locally or in Docker. Watches a per-channel playout folder; the plugin writes JSON files into it. |
| **A media server MCP** | Lets Claude query your library. The plugin is library-agnostic — install whichever MCP matches your server: Jellyfin ([`Jellyfish-AI/jellyfin-mcp`](https://github.com/Jellyfish-AI/jellyfin-mcp), [`jellyfin-mcp` on PyPI](https://pypi.org/project/jellyfin-mcp/)), Plex (any current `plex-mcp` distribution), or Emby (any current `emby-mcp`; Emby's API mirrors Jellyfin's, so most Jellyfin MCPs work unchanged). Configure per the MCP's own README. ErsatzTV Next reads media directly from the filesystem, so the MCP only needs to resolve selections to file paths. |
| **Python 3.11+** | The bundled `tools/playout-validate.py` validates emitted JSON against the Next schema. Standard library only. |

A minimal `examples/docker-compose.yml` is included for a one-shot Next + Jellyfin stack.

## What the plugin contributes

| Surface | Name | Purpose |
| :--- | :--- | :--- |
| Skill | `schedule` | Loaded when scheduling work begins. Carries the playout JSON schema, file/folder layout, validation procedure, and reload signal. |
| Skill | `setup` | First-run procedure. Captures your media-server connection, ErsatzTV paths, and channel preferences. Optionally registers the daily routine. |
| Skill | `reference` | Pinned schema references for `playout.json`, `channel.json`, and `lineup.json`. |
| Skill | `audit` | Migration helper. Compares an existing ErsatzTV Legacy install against the Next-based setup and reports gaps. |
| Agent | `programmer` | Specialized scheduling subagent for multi-channel work. |
| Command | `/program` | Build or rebuild a channel manually. |
| Command | `/setup` | Run the first-run wizard. Opt into a routine here if you want one. |
| Command | `/audit` | Run the migration audit. |
| Tool | `playout-validate.py` | JSON-schema validator, runs offline. |
| Hook | `SessionStart` update check | On session start, polls the marketplace's GitHub repo for newer commits and surfaces a one-line notice if the local plugin is behind. Suggests `/plugin marketplace update`. |

## Usage

After install, scheduling work flows through normal conversation or slash commands.

Manual examples:

> Build me a horror channel that plays my Halloween smart collection chronologically, only during October.

> /program — add a marathon channel cycling through every WCW PPV from 1995 to 2001.

> Mirror this iptv-org URL as channel 60.

The `programmer` agent picks the right shape, queries the library, emits JSON, validates it, drops it in the playout folder, and reports back.

Opt into a routine:

> /setup
>
> *(walks you through media-server config, lock in your default channels, ask whether to register a daily Desktop scheduled task or Claude Code cloud routine)*

After `/setup`, the daily run rebuilds tomorrow's playout for each registered channel. You can still `/program` ad-hoc channels alongside it.

## Documentation

- [`HANDOFF.md`](./HANDOFF.md) — onboarding for a Claude Code session new to this stack.
- [`skills/schedule/SKILL.md`](./skills/schedule/SKILL.md) — schema, file layout, write procedure.
- [`skills/setup/SKILL.md`](./skills/setup/SKILL.md) — first-run wizard procedure.
- [`examples/playouts/`](./examples/playouts) — example playouts covering common patterns.

## License

[MIT](../../LICENSE)
