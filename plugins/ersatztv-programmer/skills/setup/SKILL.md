---
name: ersatztv-setup
description: First-run wizard for the ersatztv-programmer plugin. Detects existing ErsatzTV installs, brings up a Docker-based Next + Jellyfin stack on +10000 ports, replicates an existing Jellyfin install's libraries (paths and folder images), and captures channel preferences as a 5-bucket architecture (core, rotating, music, live, experimental) with optional daily refresh routine. User-invocable only — run /ersatztv-setup explicitly when configuring or reconfiguring.
disable-model-invocation: true
allowed-tools: Bash(command -v *) Bash(docker --version) Bash(docker info) Bash(docker compose ps) Bash(docker compose ls) Bash(docker compose pull) Bash(docker compose up -d) Bash(docker compose logs *) Bash(docker run --rm hello-world) Bash(docker run --rm -it *) Bash(id -u) Bash(id -g) Bash(uname -*) Bash(ls *) Bash(ls -la *) Bash(ls -d *) Bash(pwd) Bash(stat *) Bash(du -sh *) Bash(df -h *) Bash(file *) Bash(pgrep *) Bash(lsof -nP -iTCP:*) Bash(curl -s -m * http://localhost:*) Bash(curl -sf -m * http://localhost:*) Bash(curl -sI -m * http://localhost:*) Bash(curl -s -m * https://api.github.com/*) Bash(sqlite3 file:*?immutable=1*) Bash(sqlite3 -header -column file:*?immutable=1*) Bash(grep *) Bash(head *) Bash(tail *) Bash(awk *) Bash(sed *) Bash(jq *) Bash(python3 -m json.tool) Bash(plutil -p *)
---

# First-run setup

Walks the user from "no plugin configured" to "Docker-based ErsatzTV Next + Jellyfin stack running on +10000 ports, channel inventory captured, daily refresh routine optionally registered." Skip any step that doesn't apply.

The plugin works without setup — manual `/ersatztv-program` calls do not require it. Setup is for users who want stored channel preferences plus a routine that programs the next 24 hours every night.

## Where the config lives

Setup writes a single YAML file:

| OS | Path |
| :--- | :--- |
| macOS | `~/Library/Application Support/ersatztv-programmer/config.yaml` |
| Linux | `${XDG_CONFIG_HOME:-$HOME/.config}/ersatztv-programmer/config.yaml` |
| Windows | `%APPDATA%\ersatztv-programmer\config.yaml` |

Detect the OS at the start. Save partial progress after each step so a Ctrl-C in the middle doesn't lose earlier answers. **Never** store media-server tokens in `config.yaml` — those live in env vars the MCP reads.

## Pre-flight — detect what's already running

Before Step 0, scan the host for existing installs and surface them so the user doesn't accidentally double-install or wipe state. Run these probes in parallel:

```bash
pgrep -afl -i 'ersatz|jellyfin|plex|emby' 2>/dev/null
lsof -nP -iTCP:8409 -iTCP:8096 -iTCP:32400 -iTCP:18409 -iTCP:18096 -sTCP:LISTEN 2>/dev/null
ls -d /Applications/{ErsatzTV,Jellyfin,Plex*,Emby*}.app 2>/dev/null
ls -d ~/Library/Application\ Support/{ersatztv,ersatz-tv,jellyfin,Plex\ Media\ Server} 2>/dev/null
```

Surface findings to the user as a short table:

```
Found running:
  ErsatzTV Legacy on :8409 (PID 698)  → DB: ~/Library/Application Support/ersatztv/ersatztv.sqlite3
  Jellyfin native on :8096            → data: ~/Library/Application Support/jellyfin
```

If any ErsatzTV install is found, **stop and recommend `/ersatztv-audit` first** before proceeding. The audit produces a snapshot at `${CONFIG_DIR}/last-audit.md` that the rest of this wizard reads to drive smarter defaults (which Jellyfin libraries to replicate, which channel themes to pre-suggest, etc.). After the user confirms the audit ran, resume here.

## Step 0 — Bring up the Docker stack on +10000 ports

The plugin's default install runs alongside any existing native ErsatzTV / Jellyfin without conflict by offsetting all host ports by +10000. The native install keeps running, undisturbed, on its original ports until the user is ready to switch over (uninstall the native apps, repoint clients to the new ports).

This stays opt-in: the user can answer "I already have Next running, skip stack setup" and we go to Step 1.

### 0a. Docker

`command -v docker` and `docker info` to confirm a working daemon. If missing, link the user to <https://www.docker.com/products/docker-desktop/> for macOS/Windows, or <https://docs.docker.com/engine/install/> for Linux. Don't run installers yourself.

### 0b. Stack location

Default: `~/ersatztv-stack/` — initialized as a Git repo so the user can track config changes (heavy data subdirs are gitignored). Confirm with the user, then:

```bash
mkdir -p ~/ersatztv-stack && cd ~/ersatztv-stack && git init -b main
cp ${CLAUDE_PLUGIN_ROOT}/examples/stack/docker-compose.yml ./
```

Generate the `.env`:

```bash
cat > .env <<EOF
TZ=$(readlink /etc/localtime 2>/dev/null | sed 's|.*/zoneinfo/||' || echo America/Los_Angeles)
PUID=$(id -u)
PGID=$(id -g)
ERSATZTV_PORT=18409
JELLYFIN_PORT=18096
EOF
```

Add a `.gitignore` that excludes `config/jellyfin/data/`, `config/ersatztv-next/cache/`, `hls/`, and any large transcode / segment dirs. Commit the initial state.

### 0c. Bring it up

```bash
cd ~/ersatztv-stack && docker compose up -d
docker compose ps
curl -sf http://localhost:18409/channels.m3u  # ETV Next ready when this returns 200
curl -sf http://localhost:18096/web/index.html  # Jellyfin ready when this returns 200
```

First pull is 2–5 minutes on a typical home connection.

### 0d. Replicate Jellyfin libraries

If the user has an existing native Jellyfin install, mirror its libraries into the new container so they don't have to re-add paths. Detailed REST API recipe: see [`jellyfin-replicate.md`](./jellyfin-replicate.md) in this skill folder.

The summary:

1. Read the native install's `~/Library/Application Support/jellyfin/root/default/*/options.xml` to discover library names, kinds, and paths.
2. Bind-mount each unique host volume into the new container at the same host path (so library `PathInfos` stay valid verbatim).
3. After Jellyfin's first-run wizard creates the admin user, hit `POST /Library/VirtualFolders` for each library and `POST /Items/{LibraryId}/Images/Primary` for the folder.png image.

Out-of-scope for replication: users, watch history, smart-collection-equivalent features. Start clean — those rebuild over time.

### 0e. Wire ETV Next as a Live TV tuner inside Jellyfin

Jellyfin treats ErsatzTV Next as an M3U tuner with XMLTV guide. Inside-container URLs:

- M3U: `http://ersatztv-next:8409/iptv/channels.m3u`
- XMLTV: `http://ersatztv-next:8409/iptv/xmltv.xml`

From the host: substitute `localhost:18409`. See [`jellyfin-replicate.md`](./jellyfin-replicate.md) for the exact `POST /LiveTv/TunerHosts` and `POST /LiveTv/ListingProviders` calls.

### 0f. Record stack state

```yaml
docker_stack:
  managed_by_plugin: true
  compose_file: ~/ersatztv-stack/docker-compose.yml
  ersatztv_port: 18409
  jellyfin_port: 18096
  jellyfin_libraries_replicated: [Movies, Shows, Wrestling, Music, "Lost Media"]
  live_tv_tuner_configured: true
```

## Step 1 — Media-server MCP

A media-server MCP lets Claude discover content during programming. Recommended but not required.

If `/mcp list` already shows a Jellyfin/Plex/Emby MCP, confirm the base URL and skip the install. Otherwise walk through:

1. **Install** — Jellyfin: `pip install jellyfin-mcp`. Plex: `pip install plex-mcp`. Emby: same Jellyfin MCP works.
2. **API key** — Jellyfin: Dashboard → API Keys, create new, copy. Plex: extract `X-Plex-Token` from a Get Info → View XML URL. Emby: gear → Advanced → API Keys.
3. **Env vars** — `export JELLYFIN_BASE_URL=http://localhost:18096` and `export JELLYFIN_TOKEN=...` in shell rc.
4. **Register with Claude Code:**

   ```bash
   claude mcp add --transport stdio \
     --env JELLYFIN_BASE_URL="$JELLYFIN_BASE_URL" \
     --env JELLYFIN_TOKEN="$JELLYFIN_TOKEN" \
     jellyfin -- jellyfin-mcp
   ```

   Reference: <https://code.claude.com/docs/en/mcp>.

Record:

```yaml
media_server:
  type: jellyfin
  base_url: http://localhost:18096
  # token lives in the env var the MCP reads; this plugin never stores it.
```

## Step 2 — ErsatzTV Next paths

Find `lineup.json` and the channels directory. Default in the bundled stack: `~/ersatztv-stack/config/ersatztv-next/lineup.json`. Parse it to enumerate existing channels.

```yaml
ersatztv_next:
  lineup_path: ~/ersatztv-stack/config/ersatztv-next/lineup.json
  channels_dir: ~/ersatztv-stack/config/ersatztv-next/channels
  output_folder: ~/ersatztv-stack/hls
```

## Step 3 — Channel architecture (the 75/5 model)

The plugin organizes channels into **five buckets** that sum to a recommended **75 total**. Each bucket has its own daily-refresh strategy.

| Bucket | Default count | Behavior |
| :--- | ---: | :--- |
| `core` | 35 | Stable network-style channels. User defines theme; the daily routine refreshes the next 24 h of programming against the user's library each night. |
| `rotating` | 10 | AI picks a fresh theme on the first of each month and programs against it for the rest of the month. User can pin or skip a rotation. |
| `music` | 10 | Music-only channels — artist focus, decade focus, mood, etc. Refreshed daily for variety. |
| `live` | 10 | External HLS/IPTV URLs (iptv-org, news, hobby streams). Static `http`-source playout that just keeps streaming. |
| `experimental` | 10 | AI has free rein within user-set guardrails. Tries novel programming patterns: format experiments, themed nights, oddball blocks. |

Counts are user-resizable (e.g. drop experimental to 5 if you want more core). Cap is a recommendation, not a hard limit — but past 75 the daily routine starts to take real time.

### Suggesting themes from the user's library

For `core` and `music` buckets, scan the user's actual Jellyfin library before suggesting themes — recommendations land better when they match content the user owns.

If `last-audit.md` exists, read library names + counts from there. Otherwise, query the configured Jellyfin MCP for `Genres`, `Studios`, `Tags`, and per-library item counts. Build a suggestion list that names specific available content:

> Based on your library (1,525 movies, 19,783 episodes, 244 shows, 5,848 songs across Movies / Shows / Wrestling / Music / Lost Media), I'd suggest these core channels:
>
> - **Primetime Network** — mixed drama / comedy / late-night classics in network-style blocks
> - **Saturday Morning** — animated movies + cartoon shows, 6 AM–noon Saturdays
> - **Movie Night Cinema** — themed movie nights (Friday horror, Saturday family, Sunday classics)
> - **Wrestling Marathons** — your Wrestling library, chronological blocks
> - **Lost Media Hour** — items from your Lost Media library on rotation
> - …

If Jellyfin is empty or the MCP isn't configured yet, fall back to a generic genre starter list (Action / Comedy / Drama / Family / Horror / Documentary / Music / News / Holiday / Late Night) and let the user refine.

### Capture format

```yaml
channels:
  cap: 75
  buckets:
    core:
      count: 35
      channels:
        - number: "1"
          name: "Primetime Network"
          theme: "Mixed primetime drama/comedy/late-night classics in network-style blocks"
        - number: "2"
          name: "Saturday Morning"
          theme: "Animated movies + cartoon shows, 6am–noon Saturdays; reruns rest of day"
        # ... 33 more
    rotating:
      count: 10
      monthly_intent: "Seasonal vibes, holiday tie-ins, deep cuts; rotate first of each month"
      channels:
        - number: "100"
          name: "Theme of the Month #1"
          # AI fills `current_theme` and `current_month` each rollover
    music:
      count: 10
      channels:
        - number: "200"
          name: "Pop Hits Through the Decades"
          theme: "Top 40 across the 70s, 80s, 90s, 00s; shuffled in order, tag changes by hour of day"
    live:
      count: 10
      channels:
        - number: "60"
          name: "BBC News (iptv-org)"
          url: "https://i.mjh.nz/.../bbcnews.m3u8"
          # url is the only required field; theme/refresh ignored for live
    experimental:
      count: 10
      freeform_guardrails: "Family-safe by default; surprise me with formats; never the same theme two months in a row"
      channels:
        - number: "900"
          # AI sets name + theme each refresh
```

Channel-number space: leave gaps (1–9 for primary core, 10–99 for the rest of core, 100–199 for rotating, 200–299 for music, 300–399 for live, 900–999 for experimental — adjust per personal preference).

## Step 4 — Daily refresh routine (opt-in)

Default cadence: **midnight to 1 AM local time**. This is the dead-air slot where channels typically run infomercials anyway, so refresh activity is invisible to viewers.

The routine performs, in order:

1. Read `config.yaml`.
2. For each bucket, run its refresh strategy (see the table in [`schedule` skill](../schedule/SKILL.md)).
3. Validate every emitted playout JSON with `${CLAUDE_PLUGIN_ROOT}/tools/playout-validate.py`.
4. Reload Jellyfin's Live TV guide via `POST /LiveTv/Guide/Refresh` so EPG matches the new programming.
5. Optionally prune playout files older than 7 days from each channel's playout folder.

### Where to host the routine

- **Desktop scheduled task** — runs locally, requires Claude Code Desktop to be open. Best for users with the Mac on overnight. Configure via the Desktop app's Schedule page or by asking Claude to create one. Source: <https://code.claude.com/docs/en/desktop-scheduled-tasks>.
- **Cloud routine** — runs on Anthropic infrastructure even when the laptop is closed. Cannot read local files; needs the user's `config.yaml` pushed to a Git repo the routine clones. Source: <https://code.claude.com/docs/en/routines>.

For most home users, the Desktop scheduled task is simpler and sufficient.

### Routine prompt

Paste this into the routine's prompt field:

```text
Read the ersatztv-programmer config at the OS-appropriate path
(macOS: ~/Library/Application Support/ersatztv-programmer/config.yaml).
For each bucket in channels.buckets, run its refresh strategy per the
ersatztv-schedule skill: core/music = next 24 h against stable theme;
rotating = check month rollover then next 24 h; live = re-emit long
window if expired; experimental = pick fresh AI theme then next 24 h.
Validate every emitted JSON with tools/playout-validate.py. Hit
POST http://localhost:18096/LiveTv/Guide/Refresh to refresh Jellyfin's
EPG. Prune playout files older than 7 days. Report a one-line summary
per channel.
```

Record:

```yaml
routine:
  enabled: true
  kind: desktop                       # or cloud, or none
  cadence: "0 0 * * *"                # midnight daily, local TZ
  notes: "Created via Claude Code Desktop > Schedule > New local task"
```

## Step 5 — Filler library (optional)

For "infomercial" filler between programming blocks, see [`infomercial-filler.md`](./infomercial-filler.md) for the yt-dlp acquisition recipe and how to reference filler items in playout JSON.

Recommended location: somewhere on an external drive, e.g. `/Volumes/Pluto/_FILLER_LIBRARY/infomercials/`. Record the path:

```yaml
filler:
  infomercials_dir: /Volumes/Pluto/_FILLER_LIBRARY/infomercials
  bumpers_dir: ~                      # optional — channel idents, station bumpers
```

## Step 6 — Verify

End-to-end smoke test of one channel before marking setup complete:

1. Pick the first `core` channel (or fall back to a `live` channel if no core was defined).
2. Run the schedule procedure for the next 1 hour only.
3. Validate.
4. Write to disk.
5. Curl `http://localhost:18409/iptv/channel/{N}.m3u8` to confirm ErsatzTV Next picked it up.

Print a one-line summary and finish.

## Re-running setup

`/ersatztv-setup` is idempotent. On re-run, it loads the existing `config.yaml`, shows current values, and lets the user update individual sections. If a clean reset is requested, the wizard renames the existing file to `config.yaml.bak.{timestamp}` first.

## What setup never does

- Run install commands silently — every system-affecting command is shown to the user first.
- Store tokens in `config.yaml` — env vars only.
- Modify `lineup.json` or `channel.json` outside Step 0d/0e — read freely; write only when explicit.
- Touch the native install's data — read-only via `?immutable=1` if a Legacy SQLite is read for context.
