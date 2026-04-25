---
name: setup
description: First-run wizard for the ersatztv-programmer plugin. Captures the user's media server connection, ErsatzTV Next paths, and channel preferences, then optionally registers a daily refresh routine. User-invocable only — run /setup explicitly when you want to configure or reconfigure.
disable-model-invocation: true
---

# First-run setup

This wizard runs the **first time** a user wants to configure the ersatztv-programmer plugin and any time they want to change plugin-level settings. The plugin works without it — manual `/program` calls do not require setup. Setup is required only when the user wants:

- Stored channel preferences (so they don't re-describe channels every time).
- A scheduled daily refresh (Desktop scheduled task or Cloud Routine).

Treat setup as **opt-in and reversible**. The user can skip any step, run setup again later, or remove the routine without breaking manual programming.

## Where the config lives

Setup writes a single YAML file:

| OS | Path |
| :--- | :--- |
| macOS | `~/Library/Application Support/ersatztv-programmer/config.yaml` |
| Linux | `${XDG_CONFIG_HOME:-$HOME/.config}/ersatztv-programmer/config.yaml` |
| Windows | `%APPDATA%\ersatztv-programmer\config.yaml` |

Detect the OS at the start of setup and use the right path. Do not hard-code one. The config is plain YAML — no secrets stored inline (tokens go in env vars; see below).

## Wizard structure

Each step is a question, a confirmation, and a write. Save partial progress after each step so a Ctrl-C in the middle doesn't lose earlier answers.

### Step 1 — Media server

Ask which media server the user runs (Jellyfin, Plex, or Emby). Confirm the corresponding MCP server is loaded in this Claude Code session (run `/mcp list` or check tool inventory). If not, stop and tell the user to install the MCP first; link them at:

- Jellyfin: `Jellyfish-AI/jellyfin-mcp` or `jellyfin-mcp` on PyPI.
- Plex: any current `plex-mcp` distribution.
- Emby: any current `emby-mcp` distribution.

If the MCP is loaded, ask for the base URL the MCP is configured against and confirm the connection works by issuing a small probe query (e.g., list libraries). Record:

```yaml
media_server:
  type: jellyfin            # or plex, emby
  base_url: http://192.168.1.5:8096
  # token lives in the env var the MCP itself reads (e.g. JELLYFIN_TOKEN);
  # this plugin never stores it.
```

### Step 2 — ErsatzTV Next paths

Ask the user for the absolute path to their ErsatzTV Next config. Probe these in order:

1. The `lineup.json` path the user gave.
2. Default Linux/Mac install: `~/.config/ersatztv-next/lineup.json`.
3. Default Docker mount: `./config/lineup.json` relative to a compose file the user identifies.

Once `lineup.json` is found, parse it to enumerate existing channels and the playout folder per channel (channel.json → `playout.folder`). Record:

```yaml
ersatztv_next:
  lineup_path: /Users/zach/.config/ersatztv-next/lineup.json
  channels_dir: /Users/zach/.config/ersatztv-next/channels
  output_folder: /tmp/hls       # from lineup.json output.folder
```

### Step 3 — Channel preferences

Ask: "Do you want to lock in a default set of channels now, or program them ad-hoc later?"

- If **ad-hoc later**, skip to step 4.
- If **lock in now**, walk through:
    - How many channels (rough number).
    - Which themes (let the user describe — horror, classic TV, news mirror, etc.).
    - For each channel, capture: name, channel number, request shape (free text — the prompt the routine will use to rebuild it), preferred refresh cadence (default daily).

Record as:

```yaml
channels:
  - number: "42"
    name: Halloween Marathon
    request: "Cycle through my Halloween smart collection chronologically; pull from the user's Jellyfin smart collection 'Halloween'."
    refresh: daily
  - number: "60"
    name: News Mirror
    request: "Mirror http://example.com/news.m3u8 as a Live channel."
    refresh: never           # Live mirrors don't need to refresh content
```

### Step 4 — Routine (opt-in)

Ask: "Do you want a scheduled task that re-runs the programming once per day?"

- **No**: stop here. Setup is complete. The user will use `/program` manually.
- **Yes**: ask which kind:
    - **Desktop scheduled task** — runs locally, requires the Claude Code Desktop app to be open. Best for users whose laptop is usually awake during the refresh window. Configure via the Desktop app's Schedule page or by asking Claude to create one. The task's prompt is `/program --from-config` (or equivalent — see step 5).
    - **Cloud routine** — runs on Anthropic's infrastructure even when the laptop is closed, but **cannot read local files**, so the user's `config.yaml` and channel preferences must live in a Git repo the routine clones. This is more involved; offer it only if the user explicitly wants laptop-off operation.

For Desktop tasks, the user creates the task themselves through the Desktop app or by running `/schedule` in a session — this skill does not create it directly because plugin code cannot inject scheduled tasks into Claude Code Desktop. Walk them through the steps and confirm.

For Cloud routines, the user creates the routine at https://claude.ai/code/routines or via `/schedule` in the CLI. They will need to push their `config.yaml` to a GitHub repo first; explain this trade-off.

Record the user's choice:

```yaml
routine:
  enabled: true
  kind: desktop                # or cloud, or none
  cadence: "0 4 * * *"         # 4 AM daily, local time
  notes: "Created via Claude Code Desktop > Schedule > New local task"
```

### Step 5 — The routine prompt

If the user opted into a routine, give them this exact prompt to paste into the routine's prompt field:

```text
Read the ersatztv-programmer config at the path appropriate for this OS
(see ersatztv-programmer skills/setup/SKILL.md for the full path table).
For each channel listed in `channels:`, run the schedule skill's procedure
to (a) regenerate or refresh the playout JSON for the next 24 hours, (b)
validate it with tools/playout-validate.py, and (c) write the file into
the channel's playout folder. If `refresh: never` is set on a channel,
skip it. Report a one-line summary per channel: channel number, item
count, total run time, status.
```

This prompt is self-contained and re-uses the `schedule` skill at run time.

### Step 6 — Verify

Run an end-to-end dry run of the first channel as a final check:

1. Pull a small slice of the user's media server (5–10 items).
2. Build a 1-hour playout for the channel.
3. Validate it.
4. **Do not** write it to the playout folder unless the user confirms.

Report what was tested, then ask if the user wants to keep the dry-run output or delete it.

## Idempotency and re-running

Running `/setup` again loads the existing `config.yaml`, shows current values, and offers to update each section individually. Do not blow away the file silently. If the user wants a clean reset, the wizard renames the existing file to `config.yaml.bak.{timestamp}` first.

## What setup never does

- Never asks for or stores a media-server token in `config.yaml`. Tokens live in environment variables read by the MCP server itself.
- Never auto-creates the routine. The user must take an explicit action in Claude Code Desktop or the web UI.
- Never modifies ErsatzTV Next's `lineup.json` or `channel.json` without user confirmation. Reading is fine; writing those is a separate, explicit step.
- Never assumes a media server type. Always ask.
