---
name: ersatztv-setup
description: First-run wizard for the ersatztv-programmer plugin. Captures the user's media server connection, ErsatzTV Next paths, and channel preferences, then optionally registers a daily refresh routine. User-invocable only — run /ersatztv-setup explicitly when you want to configure or reconfigure.
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

The wizard always **asks first** before doing anything system-level (installing Docker, pulling images, opening browsers). Never run install commands yourself — give copy-pasteable shell commands the user runs, so the install is auditable and reversible.

### Step 0 — System setup (skip if you already have ErsatzTV Next running)

Ask the user one question: **"Do you already have ErsatzTV Next running and reachable, or do you need help getting it set up?"**

- **Already running** → skip to step 1.
- **Need help** → walk through 0a → 0d below.

#### 0a. Docker

Detect whether `docker` is on PATH (`command -v docker`). If yes, verify it's actually working with `docker run --rm hello-world` and confirm a successful exit. If no:

> **Install Docker.** Pick the right one for your OS:
>
> - **macOS (Apple Silicon or Intel)** — install [Docker Desktop for Mac](https://www.docker.com/products/docker-desktop/). Free for personal and small-business use. Open the downloaded `.dmg`, drag Docker to Applications, launch it, accept the system extension prompts. After it finishes initializing (whale icon in the menu bar), open a new terminal and run `docker run --rm hello-world` to verify.
> - **Windows** — install [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/) with the WSL 2 backend. Run the installer, reboot if prompted, launch Docker Desktop, verify with `docker run --rm hello-world` in a PowerShell or WSL terminal.
> - **Linux** — install Docker Engine following [the official docs](https://docs.docker.com/engine/install/) for your distribution. Then run the post-install steps so your user can run `docker` without `sudo`. Verify with `docker run --rm hello-world`.
>
> Once `docker run --rm hello-world` prints "Hello from Docker!" you're done with this step.

If the user's `hello-world` test fails, surface their error message and stop the wizard. Resume after they've fixed it.

#### 0b. Plan disk layout

Ask the user where on disk three things should live:

| Thing | Default | What goes here |
| :--- | :--- | :--- |
| Stack directory | `~/ersatztv-stack/` | The `docker-compose.yml` and the per-service `config/` subfolders. Easy to back up; safe to delete and recreate. |
| Media library | `/Volumes/Media` (macOS), `/srv/media` (Linux), `D:\Media` (Windows) | Their video files. Read-only mount into the containers. Must already exist. |
| HLS output | `/tmp/hls` (or under the stack dir) | Where ErsatzTV Next writes generated HLS segments. Ephemeral; can be wiped any time. |

Don't move files. Just record paths.

#### 0c. Drop in the example stack

Copy the bundled `examples/stack/docker-compose.yml` from this plugin (`${CLAUDE_PLUGIN_ROOT}/examples/stack/docker-compose.yml`) into the user's stack directory. Don't blindly overwrite — if a `docker-compose.yml` already exists there, ask first.

Then create a sibling `.env` so the ports stay overridable:

```bash
# ~/ersatztv-stack/.env
TZ=America/Los_Angeles
PUID=501
PGID=20
ERSATZTV_PORT=18409
JELLYFIN_PORT=18096
```

(Run `id -u` / `id -g` for PUID / PGID on Mac and Linux. The +10000 offset on the host ports lets the stack run alongside a native install without conflict.)

Show the user the resulting compose file content. Confirm before they bring it up.

#### 0d. Bring the stack up

```bash
cd ~/ersatztv-stack
docker compose up -d
```

This pulls `ghcr.io/ersatztv/next:latest` and `lscr.io/linuxserver/jellyfin:latest`. First pull on a typical home connection takes 2–5 minutes.

Verify both came up:

```bash
docker compose ps
curl -sf http://localhost:18409/channels.m3u && echo OK || echo "ErsatzTV Next not responding"
curl -sf http://localhost:18096/web/index.html && echo OK || echo "Jellyfin not responding"
```

Open Jellyfin in a browser at `http://localhost:18096`. Walk the user through Jellyfin's own first-run wizard: create the admin user, point at their media folder, let it scan. This is Jellyfin-owned territory; do not try to script it.

When Jellyfin finishes its initial scan, return to this wizard. Record:

```yaml
docker_stack:
  managed_by_plugin: true
  compose_file: ~/ersatztv-stack/docker-compose.yml
  ersatztv_port: 18409
  jellyfin_port: 18096
```

### Step 1 — Media server (recommended, not required)

Ask which media server the user runs (Jellyfin, Plex, or Emby) — or whether they want to skip this step.

A media-server MCP is **recommended but optional**. With it, Claude can discover content ("build me a horror channel") and resolve smart collections to file paths automatically. Without it, the user can still use `/program` by passing exact file paths or `http` URLs themselves; only the discovery loop breaks.

If the user wants to skip, record:

```yaml
media_server:
  type: none
```

…and continue to step 2.

If they want to use one, check whether the corresponding MCP is already loaded in this Claude Code session (run `/mcp list` or check tool inventory).

#### If the MCP is already loaded

Ask for the base URL the MCP is configured against, confirm the connection works by issuing a small probe query (e.g., list libraries). Skip ahead to "Record" below.

#### If the MCP is not loaded — guide the install

Walk the user through these three steps. Do not run them yourself; give the user copy-pasteable commands so the install is auditable.

**1. Install the MCP package.** Pick the user's media server:

| Server | Recommended package | Install |
| :--- | :--- | :--- |
| Jellyfin | [`jellyfin-mcp` on PyPI](https://pypi.org/project/jellyfin-mcp/) | `pip install jellyfin-mcp` (or `uv pip install jellyfin-mcp`) |
| Jellyfin (alternative) | [`Jellyfish-AI/jellyfin-mcp`](https://github.com/Jellyfish-AI/jellyfin-mcp) | Clone + `npm install`, see repo README |
| Plex | any current `plex-mcp` distribution on PyPI / GitHub | `pip install plex-mcp` |
| Emby | any current `emby-mcp`; Jellyfin MCPs typically work unchanged | `pip install jellyfin-mcp` and point it at your Emby base URL |

**2. Get the API key/token.** Tell the user how, depending on their server.

*Jellyfin:*

> 1. Open Jellyfin web UI and log in as an admin.
> 2. Open the **Dashboard** (avatar menu → Dashboard) and find **API Keys**. Newer Jellyfin builds list it directly; older builds put it under **Advanced → API Keys**.
> 3. Click **+** to create a new key. Name it `ersatztv-programmer` so you can revoke it later.
> 4. Copy the key. Treat it like a password — most Jellyfin builds don't display it again.

*Plex:*

> Plex doesn't have a dedicated API-key panel; you extract the token your existing session is using.
>
> 1. Sign in at https://app.plex.tv.
> 2. Browse to any item in your library. Click the **⋮** menu → **Get Info** → **View XML**.
> 3. In the URL of the XML page, find `X-Plex-Token=...`. The value after `=` is the token.
> 4. Alternatively: https://plex.tv/api/v2/users/account.json with your username/password (advanced).

*Emby:*

> 1. Open Emby web UI and sign in as an admin.
> 2. Click the gear icon → **Advanced** → **API Keys**.
> 3. **New** → name it `ersatztv-programmer` → **OK**.
> 4. Copy the key.

**3. Configure the MCP and tell Claude Code about it.** Two parts.

*Set environment variables* (so the MCP can authenticate when it starts):

```bash
# In ~/.zshrc (macOS default) or ~/.bashrc (Linux):
export JELLYFIN_BASE_URL="http://192.168.1.5:8096"
export JELLYFIN_TOKEN="your-token-from-step-2"
```

(Same shape for Plex/Emby — substitute `PLEX_BASE_URL` / `PLEX_TOKEN` etc. per the MCP's own README.)

Re-source the shell or open a new terminal so the variables are visible.

*Register the MCP with Claude Code.* The CLI requires an explicit transport and a `--` separator before the command. For a stdio MCP installed via pip:

```bash
claude mcp add --transport stdio \
  --env JELLYFIN_BASE_URL="$JELLYFIN_BASE_URL" \
  --env JELLYFIN_TOKEN="$JELLYFIN_TOKEN" \
  jellyfin -- jellyfin-mcp
```

(Substitute `plex` / `emby` and the relevant env vars for the other servers. Confirm the binary name with `which jellyfin-mcp` after install.) Reference: <https://code.claude.com/docs/en/mcp>.

Restart Claude Code, then verify with `/mcp list`. The new server should appear with its tools.

#### Record

Once the MCP is loaded and probed:

```yaml
media_server:
  type: jellyfin            # or plex, emby, or none
  base_url: http://192.168.1.5:8096
  # token lives in the env var the MCP itself reads (e.g. JELLYFIN_TOKEN);
  # this plugin never stores it.
```

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
For each channel listed in `channels:`, run the ersatztv-schedule skill's
procedure to (a) regenerate or refresh the playout JSON for the next 24
hours, (b) validate it with tools/playout-validate.py, and (c) write the
file into the channel's playout folder. If `refresh: never` is set on a
channel, skip it. Report a one-line summary per channel: channel number,
item count, total run time, status.
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
